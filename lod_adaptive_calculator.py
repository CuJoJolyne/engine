"""
LOD参数计算器 V3 - 运行时自适应方案
基于局部密度 + 视角 + GPU预算动态计算LOD参数
"""
import numpy as np
from typing import Dict, Tuple, Optional
from dataclasses import dataclass

from lod_density_loader import SOGLODLoader
from lod_density_grid import DensityGrid


@dataclass
class AdaptiveLODResult:
    """自适应LOD计算结果"""
    base_distance: float
    multiplier: float
    distances: list
    local_density: float
    predicted_splat_count: int
    iterations: int
    converged: bool


class AdaptiveLODCalculator:
    """运行时自适应LOD参数计算器"""

    def __init__(self,
                 density_grid: DensityGrid,
                 scene_bounds: Tuple[np.ndarray, np.ndarray],
                 lod_levels: int,
                 total_splat_count: int,  # 新增：场景总splat数
                 target_splat_count: int = 1_000_000):
        """
        Args:
            density_grid: 预计算的密度网格
            scene_bounds: (bounds_min, bounds_max)
            lod_levels: LOD层级数
            total_splat_count: 场景总splat数（用于计算全局平均密度）
            target_splat_count: 目标渲染splat数
        """
        self.grid = density_grid
        self.scene_bounds = scene_bounds
        self.lod_levels = lod_levels
        self.total_splat_count = total_splat_count
        self.target_count = target_splat_count

        # 升降级策略软区间（运行时动态调整）
        self.target_min = int(target_splat_count * 0.8)  # 80万
        self.target_max = int(target_splat_count * 1.2)  # 120万

        # 计算场景对角线
        self.scene_diameter = float(np.linalg.norm(
            scene_bounds[1] - scene_bounds[0]
        ))

        # 计算全局平均密度
        scene_volume = np.prod(scene_bounds[1] - scene_bounds[0])
        self.global_avg_density = total_splat_count / scene_volume

        # 超参数
        self.lod0_budget_ratio = 0.2  # LOD0层占总预算的20%（降低以适应高密度）
        self.base_visibility_factor = 0.05  # 基础visibility（高密度城市）
        self.local_sample_radius = 100.0  # 局部密度采样半径(m)
        self.convergence_threshold = 0.20  # 收敛阈值（±20%）
        self.max_iterations = 8  # 增加迭代次数

        # base下限约束（手调经验最小值，留给升降级策略调整空间）
        # 只对低密度场景生效，高密度场景允许更小base
        self.base_min_high_density = 5.0     # 高密度场景（>100 splat/m³）
        self.base_min_medium_density = 5.0   # 中密度场景（10-100）降到5m
        self.base_min_low_density = 5.0      # 低密度场景（<10）降到5m，统一最小值

        print(f"[AdaptiveLODCalculator] Initialized")
        print(f"  Scene diameter: {self.scene_diameter:.1f} m")
        print(f"  Total splats: {self.total_splat_count:,}")
        print(f"  Global avg density: {self.global_avg_density:.2f} splat/m^3")
        print(f"  LOD levels: {self.lod_levels}")
        print(f"  Target: {self.target_count:,} splats")

    def compute_adaptive_params(self,
                               camera_pos: np.ndarray,
                               camera_forward: np.ndarray,
                               view_mode: str = "auto") -> AdaptiveLODResult:
        """计算自适应LOD参数

        Args:
            camera_pos: 相机位置 [x, y, z]
            camera_forward: 相机前向（归一化）[x, y, z]
            view_mode: "ground" / "aerial" / "auto"

        Returns:
            AdaptiveLODResult
        """
        print(f"\n[Adaptive LOD] Camera at {camera_pos}")

        # 1. 采样局部密度
        local_density = self._sample_local_density(camera_pos)
        print(f"  Local density: {local_density:.1f} splat/m^3")

        # 航拍模式特殊处理：忽略局部密度，使用全局平均
        # 因为航拍俯瞰全局，不应该被相机正下方的密度热点影响
        if view_mode == "aerial":
            print(f"  Aerial mode: using global average density instead of local")
            local_density = self.global_avg_density * 10  # 降低倍数（原50→10），避免预测过高
            local_density = max(local_density, 0.5)  # 最小0.5保护

        if local_density < 0.01:
            # 回退到全局平均密度（真实的低密度场景）
            print(f"  Warning: very low density, using global average")
            local_density = max(self.global_avg_density, 0.1)  # 至少0.1保护

        # 2. 自动检测视角模式
        if view_mode == "auto":
            view_mode = self._detect_view_mode(camera_pos, camera_forward)
            print(f"  View mode: {view_mode}")

        # 3. 估算视距
        view_distance = self._estimate_view_distance(camera_pos)
        print(f"  View distance: {view_distance:.1f} m")

        # 4. 迭代计算base
        result = self._solve_adaptive_base(
            local_density=local_density,
            view_distance=view_distance,
            view_mode=view_mode
        )

        print(f"  Result: base={result.base_distance:.1f}m, M={result.multiplier:.2f}")
        print(f"  Distances: {[f'{d:.1f}m' for d in result.distances]}")
        print(f"  Predicted: {result.predicted_splat_count:,} splats (converged={result.converged})")

        return result

    def _sample_local_density(self, camera_pos: np.ndarray) -> float:
        """采样相机周围的局部密度

        采样策略：球形区域内的多点采样
        """
        radius = self.local_sample_radius
        samples = []

        # 在球形区域内采样更多点（5x5x5网格）
        steps = 5
        step_size = radius * 2 / (steps - 1)

        for i in range(steps):
            for j in range(steps):
                for k in range(steps):
                    offset = np.array([
                        -radius + i * step_size,
                        -radius + j * step_size,
                        -radius + k * step_size
                    ])

                    # 只采样球内的点
                    if np.linalg.norm(offset) <= radius:
                        sample_pos = camera_pos + offset
                        density = self.grid.query_density(sample_pos)
                        if density > 0:
                            # 距离权重（中心权重高）
                            dist = np.linalg.norm(offset)
                            weight = 1.0 - (dist / radius) * 0.5
                            samples.append((density, weight))

        if not samples:
            return 0.0

        # 加权平均
        densities, weights = zip(*samples)
        weighted_avg = np.average(densities, weights=weights)

        return float(weighted_avg)

    def _detect_view_mode(self,
                         camera_pos: np.ndarray,
                         camera_forward: np.ndarray) -> str:
        """自动检测视角模式"""
        bounds_min, bounds_max = self.scene_bounds
        camera_y = camera_pos[1]
        forward_y = camera_forward[1]

        content_y_max = bounds_max[1]
        content_y_min = bounds_min[1]
        content_y_range = content_y_max - content_y_min

        # 航拍判断：相机在内容上方 + 向下看
        if camera_y > content_y_max + content_y_range * 0.2 and forward_y < -0.3:
            return "aerial"
        else:
            return "ground"

    def _estimate_view_distance(self, camera_pos: np.ndarray) -> float:
        """估算相机到场景边界的距离"""
        bounds_min, bounds_max = self.scene_bounds

        # 计算到6个边界面的距离
        distances = [
            abs(camera_pos[0] - bounds_min[0]),
            abs(camera_pos[0] - bounds_max[0]),
            abs(camera_pos[1] - bounds_min[1]),
            abs(camera_pos[1] - bounds_max[1]),
            abs(camera_pos[2] - bounds_min[2]),
            abs(camera_pos[2] - bounds_max[2])
        ]

        # 取最大距离（最远可见边界）
        max_dist = max(distances)

        # 限制范围
        return np.clip(max_dist, 100.0, self.scene_diameter)

    def _solve_adaptive_base(self,
                            local_density: float,
                            view_distance: float,
                            view_mode: str) -> AdaptiveLODResult:
        """计算自适应base

        方案B逻辑：
        - 地面场景：强制base在[5, 10]范围，不追求100万splat收敛
          预测不足的交给升级策略（LOD1-4升级到更精细层级）
        - 航拍场景：base在[30, 50]范围
        - 只调整M来覆盖视距
        """

        # ========== 确定base范围 ==========
        if view_mode == "aerial":
            # 航拍：base 30-50m
            base_min = 30.0
            base_max = 50.0
        else:
            # 地面：base 5-10m（优先保证整体场景满足）
            base_min = 5.0
            base_max = 10.0

        # ========== 根据密度在范围内选择base ==========
        # 密度越高，base越小（LOD0区域内splat已经够多）
        # 密度越低，base越大（需要更大范围才能获取足够内容）
        if view_mode == "aerial":
            # 航拍：密度对base影响小，偏向50m
            base = 50.0
        else:
            # 地面：根据局部密度在[5, 10]内线性插值
            # 高密度(>100)→5m, 低密度(<1)→10m
            if local_density > 100:
                base = 5.0
            elif local_density > 10:
                # 10-100: 线性从10m到5m
                t = (local_density - 10) / 90.0
                base = 10.0 - t * 5.0
            elif local_density > 1.0:
                # 1-10: 固定10m
                base = 10.0
            else:
                # <1: 固定10m（极低密度也用10m上限，依赖升级策略）
                base = 10.0

        # 应用范围约束
        base = np.clip(base, base_min, base_max)

        # ========== 计算M（覆盖视距） ==========
        M = (view_distance / base) ** (1 / (self.lod_levels - 1))
        M = np.clip(M, 1.5, 4.0)

        # 生成距离列表
        distances = [base * (M ** k) for k in range(self.lod_levels)]

        # ========== 预测splat数（仅用于参考，不再驱动迭代） ==========
        # 使用适当的visibility_factor
        if view_mode == "aerial":
            adaptive_visibility = 0.20
        elif local_density > 1000:
            adaptive_visibility = 0.05
        elif local_density > 100:
            adaptive_visibility = 0.08
        elif local_density > 10:
            adaptive_visibility = 0.12
        elif local_density > 1.0:
            adaptive_visibility = 0.20
        else:
            adaptive_visibility = 0.30

        predicted = self._predict_total_splat_count(
            base, M, local_density, adaptive_visibility
        )

        # 收敛判断：预测在[80万, 120万]范围内算收敛
        converged = self.target_min <= predicted <= self.target_max

        density_ratio = local_density / max(self.global_avg_density, 0.001)
        print(f"  Density ratio: {density_ratio:.1f}x, visibility: {adaptive_visibility:.2f}")
        print(f"  [Plan B] base forced to [{base_min}, {base_max}] range")

        return AdaptiveLODResult(
            base_distance=base,
            multiplier=M,
            distances=distances,
            local_density=local_density,
            predicted_splat_count=predicted,
            iterations=1,
            converged=converged
        )

    def _predict_total_splat_count(self,
                                   base: float,
                                   M: float,
                                   local_density: float,
                                   visibility_factor: float) -> int:
        """预测总渲染splat数

        混合策略：
        - LOD0用局部密度（相机周围100m内，准确）
        - LOD1+用全局平均密度（远处区域，避免过度估计）
        """
        total = 0.0

        # LOD衰减因子（每层密度衰减）
        decay = 0.125  # 1/8

        for k in range(self.lod_levels):
            # 第k层的距离范围
            r_inner = base * (M ** k) if k > 0 else 0
            r_outer = base * (M ** (k + 1))

            # 壳层体积
            volume = (4/3) * np.pi * (r_outer**3 - r_inner**3)

            # 选择密度：LOD0用局部，其他用全局平均
            if k == 0:
                base_density = local_density
            else:
                base_density = self.global_avg_density

            # 该层密度（LOD衰减）
            layer_density = base_density * (decay ** k)

            # 该层贡献
            layer_splats = volume * layer_density * visibility_factor

            total += layer_splats

        return int(total)


if __name__ == '__main__':
    # 简单测试
    from lod_density_loader import SOGLODLoader
    from analyze_scene_content import analyze_scene_content_distribution

    print("=" * 80)
    print("Adaptive LOD Calculator V3 Test")
    print("=" * 80)

    # 测试场景：grid1_y（高密度）
    scene_path = r'C:\Doc\AGP\LoD\syp_lod_0529\grid1_y'

    loader = SOGLODLoader(scene_path)
    nodes = loader.get_octree_nodes_for_density()
    bounds_min, bounds_max = loader.get_scene_bounds()

    grid = DensityGrid(bounds_min, bounds_max, grid_resolution=32)
    grid.build_from_nodes(nodes)

    content_info = analyze_scene_content_distribution(loader)

    calculator = AdaptiveLODCalculator(
        density_grid=grid,
        scene_bounds=(bounds_min, bounds_max),
        lod_levels=5,
        total_splat_count=loader.get_statistics()['total_lod0_splats'],
        target_splat_count=1_000_000
    )

    # 测试：地面视角（修正相机位置到内容区域内）
    camera_pos = content_info['weighted_center'].copy()
    camera_pos[1] = content_info['content_min'][1] + 50  # 内容最低点+50m（确保在内容区域内）
    camera_forward = np.array([1, 0, 0])

    result = calculator.compute_adaptive_params(
        camera_pos, camera_forward,
        view_mode="ground"
    )

    print(f"\n[Final Result]")
    print(f"  Base: {result.base_distance:.1f} m")
    print(f"  M: {result.multiplier:.2f}")
    print(f"  Distances: {[round(d, 1) for d in result.distances]}")
    print(f"  Local density: {result.local_density:.1f} splat/m^3")
    print(f"  Predicted count: {result.predicted_splat_count:,}")
    print(f"  Converged: {result.converged} (iterations: {result.iterations})")
