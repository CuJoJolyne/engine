"""
LOD I/O 安全模块测试

验证人工检视意见对应的防御确实生效（红/绿证据）：
1. 路径：realpath 解析 + allowed_base_dir 约束 + 文件大小限制
2. LOD key 安全转换：畸形 key 被跳过
4. LOD count 安全提取：畸形 count 被跳过
"""
import os
import json
import tempfile
import shutil

from lod_io_safety import (
    safe_load_meta, safe_int_key, safe_count, DEFAULT_MAX_META_BYTES
)

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, cond):
    results.append((name, cond))
    print(f"  [{PASS if cond else FAIL}] {name}")


def _write_meta(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def test_path_and_size():
    print("\n[1/3] Path validation + size limit")
    tmp = tempfile.mkdtemp(prefix="lodsafe_")
    try:
        allowed = os.path.join(tmp, "allowed")
        outside = os.path.join(tmp, "outside")
        os.makedirs(allowed)
        os.makedirs(outside)

        good = os.path.join(allowed, "lod-meta.json")
        _write_meta(good, {"lodLevels": 3, "tree": {}})

        # 正常加载
        meta = safe_load_meta(good, allowed_base_dir=allowed)
        check("load valid meta inside allowed dir", meta.get("lodLevels") == 3)

        # allowed_base_dir=None 不限制
        meta2 = safe_load_meta(good, allowed_base_dir=None)
        check("load with no dir restriction", meta2.get("lodLevels") == 3)

        # 路径穿越：文件在 outside，但约束 allowed → 应拒绝
        bad = os.path.join(outside, "lod-meta.json")
        _write_meta(bad, {"lodLevels": 5, "tree": {}})
        try:
            safe_load_meta(bad, allowed_base_dir=allowed)
            check("block path outside allowed dir", False)
        except PermissionError:
            check("block path outside allowed dir", True)

        # `..` 穿越尝试：用 allowed 拼一个指回 outside 的路径
        traversal = os.path.join(allowed, "..", "outside", "lod-meta.json")
        try:
            safe_load_meta(traversal, allowed_base_dir=allowed)
            check("block '..' traversal via realpath", False)
        except PermissionError:
            check("block '..' traversal via realpath", True)

        # 文件大小限制
        big = os.path.join(allowed, "big.json")
        _write_meta(big, {"lodLevels": 3, "tree": {}, "pad": "x" * 5000})
        try:
            safe_load_meta(big, max_bytes=1000)
            check("reject oversized file", False)
        except ValueError:
            check("reject oversized file", True)

        # 畸形 JSON
        broken = os.path.join(allowed, "broken.json")
        with open(broken, "w", encoding="utf-8") as f:
            f.write("{not valid json,,,}")
        try:
            safe_load_meta(broken)
            check("reject malformed JSON", False)
        except ValueError:
            check("reject malformed JSON", True)

        # 不存在
        try:
            safe_load_meta(os.path.join(allowed, "nope.json"))
            check("reject missing file", False)
        except FileNotFoundError:
            check("reject missing file", True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_int_key():
    print("\n[2/3] safe_int_key")
    check("valid '0'", safe_int_key("0") == 0)
    check("valid '3'", safe_int_key("3") == 3)
    check("valid int 2", safe_int_key(2) == 2)
    check("reject 'abc'", safe_int_key("abc") is None)
    check("reject '-1'", safe_int_key("-1") is None)
    check("reject '1.5'", safe_int_key("1.5") is None)
    check("reject '' empty", safe_int_key("") is None)
    check("reject None", safe_int_key(None) is None)
    check("reject bool True", safe_int_key(True) is None)
    check("reject '1e3' sci", safe_int_key("1e3") is None)


def test_count():
    print("\n[3/3] safe_count")
    check("dict count", safe_count({"count": 100, "file": 0}) == 100)
    check("int count", safe_count(500) == 500)
    check("integer-valued float 500.0", safe_count(500.0) == 500)
    check("reject non-integer float 1.9 (no silent trunc)", safe_count(1.9) is None)
    check("reject non-integer float in dict", safe_count({"count": 2.7}) is None)
    check("reject negative float -1.0", safe_count(-1.0) is None)
    check("reject str count", safe_count({"count": "abc"}) is None)
    check("reject missing count key", safe_count({"file": 0}) is None)
    check("reject bare str", safe_count("100") is None)
    check("reject bool", safe_count(True) is None)
    check("reject negative", safe_count({"count": -5}) is None)
    check("reject None", safe_count(None) is None)


if __name__ == "__main__":
    print("=" * 60)
    print("LOD I/O Safety Tests")
    print("=" * 60)
    test_path_and_size()
    test_int_key()
    test_count()

    total = len(results)
    passed = sum(1 for _, c in results if c)
    print("\n" + "=" * 60)
    print(f"Result: {passed}/{total} passed")
    print("=" * 60)
    if passed != total:
        failed = [n for n, c in results if not c]
        print("FAILED:")
        for n in failed:
            print(f"  - {n}")
        raise SystemExit(1)
    print("ALL PASS")
