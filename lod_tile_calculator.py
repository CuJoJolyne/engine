"""
LOD参数计算器 V4 - Tile-Based 预测模型
基于 tile 空间密度 + FOV 扇形面积 + 实际 LOD splat 统计
自然收敛到 base ≈ 10m（地面）/ 50m（航拍）

修正了 V3 的三个核心问题：
1. 用 tile spatial density (tiles/m^2) 替代连续体积密度
2. 用 FOV 扇形面积替代完整球体体积
3. 用实际 LOD 层级 avg splat/tile 替代 decay 指数衰减
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
    tile_density: float = 0.0  # tiles/m^2
    avg_lod_splats: list = field(default_factory=list)
    fov_deg: float = 60.0


@dataclass
class TileInfo:
    """单个 tile 的信息"""
    center: np.ndarray
    lod_counts: Dict[int, int]  # {lod_level: splat_count}


class TileLODCalculator:
    """基于 Tile 统计的 LOD 参数计算器

    核心预测公式：
        predicted = sum(
            FOV/360 * pi * (r_k^2 - r_{k-1}^2)  # 可见扇形面积
            × tile_density                          # tiles/m^2 (XZ平面)
            × avg_lod_splats[k]                     # 该层平均 splat/tile
        )
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

        # 计算 tile 空间统计
        self._compute_tile_stats()

        self.max_iterations = 10

    def _load_tiles(self):
        """从 lod-meta.json 加载所有叶节点 tile"""
        meta_path = self.lod_dir / 'lod-meta.json'
        with open(meta_path, 'r') as f:
            meta = json.load(f)

        self.lod_levels = meta['lodLevels']
        self.tiles: List[TileInfo] = []

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
                self.tiles.append(TileInfo(center=center, lod_counts=lod_counts))
            if has_children:
                for child in node['children']:
                    traverse(child)

        traverse(meta['tree'])
        root_bound = meta['tree']['bound']
        self.bounds_min = np.array(root_bound['min'])
        self.bounds_max = np.array(root_bound['max'])

    # PLACEHOLDER_STATS

    def _compute_tile_stats(self):
        """计算 tile 空间统计量"""
        centers = np.array([t.center for t in self.tiles])
        self.tile_count = len(self.tiles)

        # XZ 平面足迹面积
        x_range = centers[:, 0].max() - centers[:, 0].min()
        z_range = centers[:, 2].max() - centers[:, 2].min()
        self.scene_footprint_area = max(x_range * z_range, 1.0)

        # tile 空间密度 (tiles/m^2 in XZ plane)
        self.tile_spatial_density = self.tile_count / self.scene_footprint_area

        # 每个 LOD 层级的平均 splat 数
        self.avg_lod_splats = []
        for k in range(self.lod_levels):
            counts = [t.lod_counts.get(k, 0) for t in self.tiles if k in t.lod_counts]
            avg = np.mean(counts) if counts else 0
            self.avg_lod_splats.append(float(avg))

        # 场景中心和最远距离
        self.scene_center = np.median(centers, axis=0)
        dists = np.linalg.norm(centers - self.scene_center, axis=1)
        self.max_tile_distance = float(dists.max())
        self.total_splats = sum(t.lod_counts.get(0, 0) for t in self.tiles)

        print(f"[TileLODCalculator] {self.lod_dir.name}")
        print(f"  Tiles: {self.tile_count}, LOD levels: {self.lod_levels}")
        print(f"  Total splats: {self.total_splats:,}")
        print(f"  Tile density: {self.tile_spatial_density:.5f} tiles/m^2"
              f" (1 per {np.sqrt(1/self.tile_spatial_density):.1f}m)")
        print(f"  Avg splats/tile: {[f'{s:.0f}' for s in self.avg_lod_splats]}")

    # PLACEHOLDER_COMPUTE

    def compute(self, view_mode: str = "ground", fov_deg: float = 60.0) -> TileLODResult:
        """计算自适应 LOD 参数

        Args:
            view_mode: "ground" / "aerial"
            fov_deg: 水平视场角（度）

        Returns:
            TileLODResult
        """
        # 视距（最远 tile 距离）
        view_distance = self.max_tile_distance

        # 迭代求解 base
        base = self._solve_base(view_mode, fov_deg, view_distance)

        # 计算 M
        M = (view_distance / base) ** (1 / (self.lod_levels - 1))
        M = np.clip(M, 1.5, 4.0)

        distances = [float(base * M**k) for k in range(self.lod_levels)]

        # 最终预测
        predicted = self._predict(base, M, view_mode, fov_deg)
        converged = self.target_min <= predicted <= self.target_max

        return TileLODResult(
            base_distance=float(base),
            multiplier=float(M),
            distances=distances,
            predicted_splat_count=predicted,
            iterations=self._last_iterations,
            converged=converged,
            tile_count=self.tile_count,
            tile_density=self.tile_spatial_density,
            avg_lod_splats=self.avg_lod_splats,
            fov_deg=fov_deg
        )

    def _predict(self, base: float, M: float,
                 view_mode: str, fov_deg: float) -> int:
        """tile-based 预测渲染 splat 数

        Ground: FOV 扇形面积 × tile密度 × avg_lod[k]
        Aerial: FOV 圆锥投影面积 × tile密度 × avg_lod[k]
        """
        total = 0.0
        distances = [base * M**k for k in range(self.lod_levels)]

        if view_mode == "ground":
            # 地面：水平方向 FOV 扇形（XZ 平面）
            fov_fraction = fov_deg / 360.0
            for k in range(self.lod_levels):
                r_inner = distances[k - 1] if k > 0 else 0
                r_outer = distances[k]
                # 可见扇形面积（XZ 平面内）
                ring_area = fov_fraction * np.pi * (r_outer**2 - r_inner**2)
                tiles_in_ring = ring_area * self.tile_spatial_density
                total += tiles_in_ring * self.avg_lod_splats[k]
        else:
            # 航拍：俯视，看到完整圆形区域（相机在上方）
            # 航拍 FOV 形成的地面投影圆较大
            for k in range(self.lod_levels):
                r_inner = distances[k - 1] if k > 0 else 0
                r_outer = distances[k]
                ring_area = np.pi * (r_outer**2 - r_inner**2)
                tiles_in_ring = ring_area * self.tile_spatial_density
                total += tiles_in_ring * self.avg_lod_splats[k]

        return int(total)

    def _solve_base(self, view_mode: str, fov_deg: float,
                    view_distance: float) -> float:
        """二分法求解使预测 splat 数接近目标的 base"""
        base_lo = 1.0
        base_hi = min(view_distance * 0.5, 500.0)

        self._last_iterations = 0

        for i in range(self.max_iterations):
            self._last_iterations = i + 1
            base_mid = (base_lo + base_hi) / 2

            M = (view_distance / base_mid) ** (1 / (self.lod_levels - 1))
            M = np.clip(M, 1.5, 4.0)

            predicted = self._predict(base_mid, M, view_mode, fov_deg)

            if self.target_min <= predicted <= self.target_max:
                return base_mid

            if predicted > self.target_count:
                # 预测过高，减小 base
                base_hi = base_mid
            else:
                # 预测不足，增大 base
                base_lo = base_mid

        return (base_lo + base_hi) / 2


if __name__ == '__main__':
    import sys

    print("=" * 90)
    print("LOD Tile-Based Calculator V4")
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

        calc = TileLODCalculator(path)
        result = calc.compute(view_mode=mode, fov_deg=60.0)

        dists_str = ', '.join([f'{d:.0f}' for d in result.distances])
        conv = "Y" if result.converged else "N"
        print(f"{name:<18} {mode:<8} {result.base_distance:<8.1f} "
              f"{result.multiplier:<6.2f} [{dists_str}]{'':<5}"
              f" {result.predicted_splat_count:<12,} {conv}")
