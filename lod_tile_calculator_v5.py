"""
LOD参数计算器 V5 - Tile Simulation + Visibility Factor
基于真实 tile 遍历预测 + 经验 visibility factor

修正了 V4 的问题：
- V4 用面积近似公式，与实际 tile 分布差 6-9x
- V5 直接遍历 tile 分配 LOD（ground truth），再乘 visibility factor
"""
import json
import numpy as np
from typing import Dict, Tuple, List, Optional
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TileLODResult:
    """LOD计算结果"""
    base_distance: float
    multiplier: float
    distances: list
    predicted_splat_count: int
    iterations: int
    converged: bool
    # 诊断信息
    tile_count: int = 0
    view_mode: str = "ground"


class TileLODCalculatorV5:
    """基于 Tile Simulation 的 LOD 参数计算器

    核心预测公式：
        predicted = tile_simulation(base, M) × visibility_factor

    tile_simulation: 遍历每个 tile，根据距离分配 LOD，统计 splat 数
    visibility_factor: 经验值，模拟 frustum culling + occlusion 的综合效果
        - Ground: 0.15 (FOV=60°, 单方向)
        - Aerial: 0.25 (FOV=120°, 俯视覆盖更广)
    """

    def __init__(self, lod_dir: str, target_splat_count: int = 1_000_000):
        """
        Args:
            lod_dir: LOD数据目录（包含 lod-meta.json）
            target_splat_count: 目标渲染 splat 数
        """
        self.lod_dir = Path(lod_dir)
        self.target_count = target_splat_count
        self.target_min = int(target_splat_count * 0.8)
        self.target_max = int(target_splat_count * 1.2)

        # 加载 tile 数据
        self._load_tiles()

        self.max_iterations = 15

    def _load_tiles(self):
        """从 lod-meta.json 加载所有叶节点 tile"""
        meta_path = self.lod_dir / 'lod-meta.json'
        with open(meta_path, 'r') as f:
            meta = json.load(f)

        self.lod_levels = meta['lodLevels']
        self.tiles = []  # List[(center, lod_counts)]

        def traverse(node):
            has_children = 'children' in node and node['children']
            lods = node.get('lods', {})
            if not has_children and lods:
                bound = node['bound']
                bmin = np.array(bound['min'])
                bmax = np.array(bound['max'])
                center = (bmin + bmax) / 2
                lod_counts = {}
                for k, v in lods.items():
                    if isinstance(v, dict) and 'count' in v:
                        lod_counts[int(k)] = v['count']
                    elif isinstance(v, (int, float)):
                        lod_counts[int(k)] = int(v)
                self.tiles.append((center, lod_counts))
            if has_children:
                for child in node['children']:
                    traverse(child)

        traverse(meta['tree'])

        # 计算场景统计
        centers = np.array([t[0] for t in self.tiles])
        self.tile_count = len(self.tiles)
        self.scene_center = np.median(centers, axis=0)
        dists = np.linalg.norm(centers - self.scene_center, axis=1)
        self.max_tile_distance = float(dists.max())
        self.total_splats = sum(t[1].get(0, 0) for t in self.tiles)

        # 计算 tile 空间密度（用于自适应 visibility factor）
        x_range = centers[:, 0].max() - centers[:, 0].min()
        z_range = centers[:, 2].max() - centers[:, 2].min()
        footprint_area = max(x_range * z_range, 1.0)
        self.tile_density = self.tile_count / footprint_area  # tiles/m^2

        print(f"[TileLODCalculatorV5] {self.lod_dir.name}")
        print(f"  Tiles: {self.tile_count}, LOD levels: {self.lod_levels}")
        print(f"  Total splats: {self.total_splats:,}")
        print(f"  Max tile distance: {self.max_tile_distance:.1f}m")
        print(f"  Tile density: {self.tile_density:.6f} tiles/m^2")

    def compute(self, view_mode: str = "ground") -> TileLODResult:
        """计算自适应 LOD 参数

        Args:
            view_mode: "ground" / "aerial"

        Returns:
            TileLODResult
        """
        # 迭代求解 base
        base = self._solve_base(view_mode)

        # 计算 M
        M = (self.max_tile_distance / base) ** (1 / (self.lod_levels - 1))
        M = np.clip(M, 1.5, 4.0)

        distances = [float(base * M**k) for k in range(self.lod_levels)]

        # 最终预测
        predicted = self._predict(base, M, view_mode)
        converged = self.target_min <= predicted <= self.target_max

        return TileLODResult(
            base_distance=float(base),
            multiplier=float(M),
            distances=distances,
            predicted_splat_count=predicted,
            iterations=self._last_iterations,
            converged=converged,
            tile_count=self.tile_count,
            view_mode=view_mode
        )

    def _tile_simulation(self, base: float, M: float) -> int:
        """Tile simulation: 遍历每个 tile，根据距离分配 LOD

        这是 ground truth — 精确模拟距离-LOD 分配逻辑
        """
        distances = [base * M**k for k in range(self.lod_levels)]

        total = 0
        for center, lod_counts in self.tiles:
            dist = np.linalg.norm(center - self.scene_center)

            # 分配 LOD（默认最粗层）
            lod = self.lod_levels - 1
            for k in range(self.lod_levels):
                if dist <= distances[k]:
                    lod = k
                    break

            total += lod_counts.get(lod, 0)

        return total

    def _predict(self, base: float, M: float, view_mode: str) -> int:
        """预测渲染 splat 数

        predicted = tile_simulation × visibility_factor

        visibility_factor 自适应规则：
        - Ground: 固定 0.11（FOV=60°×45°，单方向）
        - Aerial: 根据 tile 密度自适应
          - 稀疏场景（<0.001 tiles/m²）：0.8-1.0（几乎无遮挡）
          - 密集场景（>0.002 tiles/m²）：0.2-0.25（遮挡严重）
        """
        sim_total = self._tile_simulation(base, M)

        if view_mode == "ground":
            # 地面模式：区分室内/室外
            if self.max_tile_distance < 100.0:
                # 室内/小场景（<100m）：封闭空间，全部可见
                # 实测：xiaohuizhou (max_dist=36m, total=0.99M)
                #       base=18m → sim=821k, 需要 factor≈1.0
                # 实测：adongge (max_dist=42m, total=1.00M)
                #       base=18m → sim=893k, 需要 factor≈1.0
                visibility = 1.0
            else:
                # 大型室外：FOV=60°×45°，单方向
                # 实测：syp_0610 (tile_density=0.00474)
                #       base=10m → sim=10.5M, 实际需要≈1M → factor≈0.11
                visibility = 0.11
        else:
            # 航拍：根据 tile 密度自适应
            # 实测数据点：
            #   nys (0.00056 tiles/m²): base=50m → sim=0.93M, 需要 factor≈1.08
            #   syp_uav (0.00251 tiles/m²): base=50m → sim=4.17M, 需要 factor≈0.24

            if self.tile_density < 0.0008:
                # 极稀疏（nys 级别）
                visibility = 1.0
            elif self.tile_density < 0.0015:
                # 稀疏
                # 线性插值：0.0008→1.0, 0.0015→0.4
                t = (self.tile_density - 0.0008) / (0.0015 - 0.0008)
                visibility = 1.0 - t * 0.6
            elif self.tile_density < 0.003:
                # 中等密度（syp_uav 级别）
                # 线性插值：0.0015→0.4, 0.003→0.25
                t = (self.tile_density - 0.0015) / (0.003 - 0.0015)
                visibility = 0.4 - t * 0.15
            else:
                # 高密度
                visibility = 0.25

        return int(sim_total * visibility)

    def _solve_base(self, view_mode: str) -> float:
        """二分法求解使预测 splat 数接近目标的 base

        关键：base 越小 → LOD0 覆盖范围越小 → 预测越小
              base 越大 → LOD0 覆盖范围越大 → 预测越大
        """
        # 先测试边界，确保解在区间内
        base_lo = 1.0
        # 上界用 max_tile_distance（不是 0.5×），确保室内/小场景可以把所有 tile 压到 LOD0
        # 室外大场景 max_dist > 1000m，上界被 500m 自然截断
        base_hi = min(self.max_tile_distance, 500.0)

        # 测试上界
        M_hi = (self.max_tile_distance / base_hi) ** (1 / (self.lod_levels - 1))
        M_hi = np.clip(M_hi, 1.5, 4.0)
        pred_hi = self._predict(base_hi, M_hi, view_mode)

        # 测试下界
        M_lo = (self.max_tile_distance / base_lo) ** (1 / (self.lod_levels - 1))
        M_lo = np.clip(M_lo, 1.5, 4.0)
        pred_lo = self._predict(base_lo, M_lo, view_mode)

        # 如果上界预测还不够，说明场景太大或密度太低，返回上界
        if pred_hi < self.target_min:
            self._last_iterations = 1
            return base_hi

        # 如果下界预测已经过高，说明场景很密集，返回下界
        if pred_lo > self.target_max:
            self._last_iterations = 1
            return base_lo

        self._last_iterations = 0

        for i in range(self.max_iterations):
            self._last_iterations = i + 1
            base_mid = (base_lo + base_hi) / 2

            M = (self.max_tile_distance / base_mid) ** (1 / (self.lod_levels - 1))
            M = np.clip(M, 1.5, 4.0)

            predicted = self._predict(base_mid, M, view_mode)

            if self.target_min <= predicted <= self.target_max:
                return base_mid

            if predicted < self.target_count:
                # 预测不足 → base 太小 → 增大 base
                base_lo = base_mid
            else:
                # 预测过高 → base 太大 → 减小 base
                base_hi = base_mid

        return (base_lo + base_hi) / 2


if __name__ == '__main__':
    print("=" * 90)
    print("LOD Tile Simulation Calculator V5")
    print("=" * 90)

    scenes = [
        (r'C:\Doc\AGP\LoD\syp_lod_0610', 'syp_0610_full', 'ground'),
        (r'C:\Doc\AGP\LoD\syp_lod_0610\grid_0_y', 'grid_0', 'ground'),
        (r'C:\Doc\AGP\LoD\syp_lod_0610\grid_2_y', 'grid_2', 'ground'),
        (r'C:\Doc\AGP\LoD\nys_lod_0618', 'nys_aerial', 'aerial'),
        (r'C:\Doc\AGP\LoD\syp_lod_uav_0610', 'syp_uav_full', 'aerial'),
    ]

    print(f"\n{'Scene':<18} {'Mode':<8} {'base(m)':<8} {'M':<6}"
          f" {'Distances':<40} {'Predicted':<12} {'Conv'}")
    print("-" * 100)

    for path, name, mode in scenes:
        if not (Path(path) / 'lod-meta.json').exists():
            print(f"{name:<18} SKIP (no data)")
            continue

        calc = TileLODCalculatorV5(path)
        result = calc.compute(view_mode=mode)

        dists_str = ', '.join([f'{d:.0f}' for d in result.distances])
        conv = "Y" if result.converged else "N"
        print(f"{name:<18} {mode:<8} {result.base_distance:<8.1f} "
              f"{result.multiplier:<6.2f} [{dists_str}]{'':<5}"
              f" {result.predicted_splat_count:<12,} {conv}")
