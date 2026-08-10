"""
畸形 lod-meta.json 集成测试

验证 reviewer（砚砚）Finding 1 & 2 的失败模式已受控：
- Finding 1: loader 遇到非数字 count 不再在 get_statistics() 求和时 TypeError，
             而是跳过并 warning，正常返回。
- Finding 2: calculator 全部 tile 无效时 fail-fast 抛 ValueError（不是 AxisError）。
"""
import os
import json
import tempfile
import shutil

from lod_density_loader import SOGLODLoader
from lod_tile_calculator_v5 import TileLODCalculatorV5

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, cond):
    results.append((name, cond))
    print(f"  [{PASS if cond else FAIL}] {name}")


def _make_meta(tmp, tree, lod_levels=3, filenames=None):
    d = tempfile.mkdtemp(prefix="lodbad_", dir=tmp)
    meta = {
        "lodLevels": lod_levels,
        "filenames": filenames if filenames is not None else ["a.webp"],
        "tree": tree,
    }
    with open(os.path.join(d, "lod-meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f)
    return d


def _leaf(cx, cz, lods):
    """构造一个叶节点（无 children）"""
    return {
        "bound": {"min": [cx - 1, -1, cz - 1], "max": [cx + 1, 1, cz + 1]},
        "lods": lods,
    }


def _root_with(leaves):
    return {
        "bound": {"min": [-100, -10, -100], "max": [100, 10, 100]},
        "children": leaves,
    }


def test_finding1_loader_bad_count():
    print("\n[Finding 1] loader: non-numeric count must not crash get_statistics()")
    tmp = tempfile.mkdtemp(prefix="lodbad_root_")
    try:
        # 一个正常 tile + 一个 count 为字符串的畸形 tile
        tree = _root_with([
            _leaf(10, 10, {"0": {"count": 1000, "file": 0}, "1": {"count": 500, "file": 0}}),
            _leaf(20, 20, {"0": {"count": "bad", "file": 0}}),  # 畸形
        ])
        d = _make_meta(tmp, tree)

        try:
            loader = SOGLODLoader(d)
            stats = loader.get_statistics()  # 旧代码在这里 TypeError
            # 畸形 tile 被跳过，只剩 1 个有效叶节点，LOD0=1000
            ok = (stats["leaf_nodes"] == 1 and stats["total_lod0_splats"] == 1000)
            check("loader skips bad count, stats computes without TypeError", ok)
        except TypeError as e:
            check(f"loader skips bad count (got TypeError: {e})", False)

        # 非整数 float count 也应被跳过（不静默截断）
        tree2 = _root_with([
            _leaf(10, 10, {"0": {"count": 1000, "file": 0}}),
            _leaf(20, 20, {"0": {"count": 3.7, "file": 0}}),  # 非整数 float
        ])
        d2 = _make_meta(tmp, tree2)
        loader2 = SOGLODLoader(d2)
        stats2 = loader2.get_statistics()
        check("loader skips non-integer float count",
              stats2["leaf_nodes"] == 1 and stats2["total_lod0_splats"] == 1000)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_finding2_calculator_all_invalid():
    print("\n[Finding 2] calculator: all-invalid tiles must raise ValueError (not AxisError)")
    tmp = tempfile.mkdtemp(prefix="lodbad_calc_")
    try:
        # 所有 tile 的 count 都畸形 → 全部跳过 → self.tiles=[]
        tree = _root_with([
            _leaf(10, 10, {"0": {"count": "bad", "file": 0}}),
            _leaf(20, 20, {"0": {"count": None, "file": 0}}),
        ])
        d = _make_meta(tmp, tree)

        try:
            TileLODCalculatorV5(d)
            check("calculator raises ValueError on all-invalid tiles", False)
        except ValueError as e:
            check(f"calculator raises ValueError (msg: {str(e)[:40]}...)", True)
        except Exception as e:
            # AxisError / 其它 = 未受控失败模式
            check(f"calculator raises ValueError (got {type(e).__name__}: {e})", False)

        # 对照：至少一个有效 tile 时正常工作
        tree_ok = _root_with([
            _leaf(10, 10, {"0": {"count": 500000, "file": 0}, "1": {"count": 200000, "file": 0}}),
            _leaf(20, 20, {"0": {"count": "bad", "file": 0}}),  # 畸形，被跳过
        ])
        d_ok = _make_meta(tmp, tree_ok)
        calc = TileLODCalculatorV5(d_ok)
        check("calculator works with 1 valid + 1 invalid tile", calc.tile_count == 1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_finding_lods_not_dict():
    """砚砚 复核 Finding: lods 容器本身非 dict（list/str）→ 不能 AttributeError"""
    print("\n[Finding: non-dict lods] loader + calculator must not AttributeError on '.items()'")
    tmp = tempfile.mkdtemp(prefix="lodbad_container_")
    try:
        # --- loader: 一个正常 tile + 一个 lods 为 list 的畸形 tile ---
        tree = _root_with([
            _leaf(10, 10, {"0": {"count": 1000, "file": 0}}),
            _leaf(20, 20, ["not", "a", "dict"]),  # lods 是 list
        ])
        d = _make_meta(tmp, tree)
        try:
            loader = SOGLODLoader(d)
            stats = loader.get_statistics()
            # 畸形 tile 被当空跳过，只剩 1 个有效叶节点
            check("loader treats non-dict lods as empty (no AttributeError)",
                  stats["leaf_nodes"] == 1 and stats["total_lod0_splats"] == 1000)
        except AttributeError as e:
            check(f"loader treats non-dict lods as empty (got AttributeError: {e})", False)

        # --- calculator: lods 为 str 的畸形 tile + 一个正常 tile ---
        tree2 = _root_with([
            _leaf(10, 10, {"0": {"count": 600000, "file": 0}, "1": {"count": 200000, "file": 0}}),
            _leaf(20, 20, "garbage_string"),  # lods 是 str
        ])
        d2 = _make_meta(tmp, tree2)
        try:
            calc = TileLODCalculatorV5(d2)
            check("calculator treats non-dict lods as empty (no AttributeError)",
                  calc.tile_count == 1)
        except AttributeError as e:
            check(f"calculator treats non-dict lods as empty (got AttributeError: {e})", False)

        # --- calculator: 全部 tile 的 lods 都非 dict → fail-fast ValueError（不是 AttributeError）---
        tree3 = _root_with([
            _leaf(10, 10, ["bad"]),
            _leaf(20, 20, "also_bad"),
        ])
        d3 = _make_meta(tmp, tree3)
        try:
            TileLODCalculatorV5(d3)
            check("calculator fail-fast when all lods non-dict", False)
        except ValueError:
            check("calculator fail-fast when all lods non-dict (ValueError)", True)
        except Exception as e:
            check(f"calculator fail-fast when all lods non-dict (got {type(e).__name__}: {e})", False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    print("=" * 64)
    print("Malformed lod-meta.json Integration Tests (Reviewer Findings)")
    print("=" * 64)
    test_finding1_loader_bad_count()
    test_finding2_calculator_all_invalid()
    test_finding_lods_not_dict()

    total = len(results)
    passed = sum(1 for _, c in results if c)
    print("\n" + "=" * 64)
    print(f"Result: {passed}/{total} passed")
    print("=" * 64)
    if passed != total:
        print("FAILED:")
        for n, c in results:
            if not c:
                print(f"  - {n}")
        raise SystemExit(1)
    print("ALL PASS")
