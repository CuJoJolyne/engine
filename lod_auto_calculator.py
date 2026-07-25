"""
LOD参数自动计算器 - 基于密度感知 + 视角自适应
"""
import numpy as np
from typing import Dict, Tuple, Optional
from dataclasses import dataclass

from lod_density_loader import SOGLODLoader
from lod_density_grid import DensityGrid, DensityProfile


@dataclass
class LODParameters:
    """LOD参数计算结果"""
    base_distance: float       # LOD0切换距离 (m)
    multiplier: float          # 几何级数倍率
    distances: list           # 各级切换距离 [base, base*M, base*M^2, ...]
    mlod_factor: float        # 多层叠加系数
    effective_density: float  # 有效密度 (splat/m^3)
    predicted_splat_count: int  # 预测渲染splat数


class LODAutoCalculator:
    """LOD参数自动计算器"""

    def __init__(self,
                 loader: SOGLODLoader,
                 density_grid: DensityGrid,
                 target_splat_count: int = 1_000_000,
                 lod_decay: float = 0.125):
        """
        Args:
            loader: SOG数据加载器
            density_grid: 密度网格
            target_splat_count: 目标渲染splat数
            lod_decay: LOD层级衰减因子（典型0.125 = 1/8）
        """
        self.loader = loader
        self.grid = density_grid
        self.target_count = target_splat_count
        self.decay = lod_decay

        self.scene_diameter = loader.get_scene_diameter()
        self.lod_levels = loader.get_lod_levels()
        self.total_splat_count = loader.get_statistics()['total_lod0_splats']

        print(f"[LODAutoCalculator] Initialized")
        print(f"  Scene diameter: {self.scene_diameter:.1f} m")
        print(f"  LOD levels: {self.lod_levels}")
        print(f"  Total LOD0 splats: {self.total_splat_count:,}")
        print(f"  Target count: {self.target_count:,}")

    def compute_lod_parameters(self,
                              camera_pos: np.ndarray,
                              camera_forward: np.ndarray,
                              dimension: int = 3,
                              fov_deg: float = 60.0) -> LODParameters:
        """计算LOD参数（视角自适应）

        Args:
            camera_pos: 相机位置 [x, y, z]
            camera_forward: 相机前向 [x, y, z]
            dimension: 场景维度 (1=线性, 2=面积, 3=体积)
            fov_deg: 视场角

        Returns:
            LODParameters
        """
        print(f"\n[Computing LOD params for camera at {camera_pos}]")

        # 1. 自适应设置视锥采样距离
        # 根据场景直径和相机位置智能选择far_dist
        scene_bounds_min, scene_bounds_max = self.loader.get_scene_bounds()
        scene_center = (scene_bounds_min + scene_bounds_max) / 2

        # 相机到场景中心的距离
        dist_to_center = np.linalg.norm(camera_pos - scene_center)

        # far_dist应该能覆盖到场景边界
        far_dist = min(dist_to_center + self.scene_diameter * 0.6, self.scene_diameter * 0.8)
        far_dist = max(far_dist, 100.0)  # 最小100m

        print(f"  Adaptive sampling: dist_to_center={dist_to_center:.1f}m, far_dist={far_dist:.1f}m")

        # 2. 采样视锥内密度分布
        profile = self.grid.sample_frustum_density(
            camera_pos, camera_forward,
            fov_deg=fov_deg,
            near_dist=1.0,
            far_dist=far_dist,
            num_samples=100
        )

        print(f"  Density profile: near={profile.near:.1f}, mid={profile.mid:.1f}, "
              f"far={profile.far:.1f}, eff={profile.effective:.1f} splat/m^3")

        # 如果密度太低，回退到全局平均密度估计
        if profile.effective < 1.0:
            avg_density = self.total_splat_count / (self.scene_diameter ** 3)
            print(f"  Warning: low sampled density, fallback to avg density={avg_density:.3f}")
            profile.effective = avg_density

        # 2. 迭代求解base和M
        result = self._solve_base_iterative(
            effective_density=profile.effective,
            dimension=dimension
        )

        base, M, mlod_factor = result['base'], result['M'], result['mlod_factor']

        # 3. 生成距离列表
        distances = [base * (M ** k) for k in range(self.lod_levels)]

        # 4. 预测实际渲染量
        predicted_count = self._predict_render_count(base, M, profile.effective, dimension)

        print(f"  Result: base={base:.1f}m, M={M:.2f}, mlod={mlod_factor:.2f}")
        print(f"  Distances: {[f'{d:.1f}m' for d in distances]}")
        print(f"  Predicted splat count: {predicted_count:,} (target: {self.target_count:,})")

        return LODParameters(
            base_distance=base,
            multiplier=M,
            distances=distances,
            mlod_factor=mlod_factor,
            effective_density=profile.effective,
            predicted_splat_count=predicted_count
        )

    def _solve_base_iterative(self,
                              effective_density: float,
                              dimension: int,
                              max_iter: int = 10) -> Dict:
        """迭代求解base（简化经验模型）

        经验模型：
        base ∝ (target / N)^(1/E) × (scene_scale)
        使用密度调整基准值，避免复杂的体积积分

        Args:
            effective_density: 有效密度 (splat/m^3)
            dimension: E (1/2/3)
            max_iter: 最大迭代次数

        Returns:
            {'base': float, 'M': float, 'mlod_factor': float}
        """
        E = dimension
        L = self.lod_levels
        D = self.scene_diameter
        N = self.total_splat_count
        target = self.target_count
        decay = self.decay

        # 简化模型：直接从目标比例推算
        # base/D ≈ (target/N)^(1/E) × density_factor
        ratio = target / N  # 目标splat占比

        # 密度因子：有效密度越高，base可以越小
        # 归一化密度（相对于平均密度）
        avg_density = N / (D ** 3)  # 场景平均密度
        density_factor = (avg_density / max(effective_density, 1.0)) ** (1 / E)

        # 初始base估计
        base = D * (ratio ** (1 / E)) * density_factor * 1.5  # 1.5是经验系数

        # 限制base范围
        base = np.clip(base, 5.0, D * 0.5)

        for iteration in range(max_iter):
            # 从base计算M（确保覆盖场景）
            M = (D / base) ** (1 / (L - 1))

            # 限制M范围（放宽上限到3.0，避免过度限制）
            M = np.clip(M, 1.5, 3.0)

            # 计算mlod_factor
            alpha = decay * (M ** E)
            if abs(alpha - 1.0) < 1e-6:
                mlod_factor = (M ** E - 1) * L
            else:
                sum_term = (1 - alpha ** L) / (1 - alpha)
                mlod_factor = (M ** E - 1) * sum_term

            # 反算base：从目标splat数和mlod_factor
            # N_visible ≈ N × (base/D)^E × mlod_factor
            if mlod_factor > 0:
                base_new = D * ((target / N) / mlod_factor) ** (1 / E)
            else:
                base_new = base

            # 限制base范围
            base_new = np.clip(base_new, 5.0, D * 0.5)

            # 收敛检查
            if abs(base_new - base) < 1.0:  # 收敛到1m以内
                print(f"    Converged at iteration {iteration}: base={base_new:.2f}m, M={M:.2f}")
                return {'base': base_new, 'M': M, 'mlod_factor': mlod_factor}

            # 阻尼更新（避免震荡）
            base = 0.5 * base + 0.5 * base_new

        # 返回最后值
        M = np.clip((D / base) ** (1 / (L - 1)), 1.5, 3.0)
        alpha = decay * (M ** E)
        mlod_factor = (M ** E - 1) * ((1 - alpha ** L) / (1 - alpha)) if abs(alpha - 1) > 1e-6 else (M ** E - 1) * L
        print(f"    Converged with damping after {max_iter} iterations: base={base:.2f}m, M={M:.2f}")
        return {'base': base, 'M': M, 'mlod_factor': mlod_factor}

    def _predict_render_count(self,
                              base: float,
                              M: float,
                              effective_density: float,
                              dimension: int) -> int:
        """预测实际渲染splat数

        Args:
            base: LOD0切换距离
            M: 倍率
            effective_density: 有效密度
            dimension: E

        Returns:
            预测的渲染splat数
        """
        E = dimension
        L = self.lod_levels
        D = self.scene_diameter
        decay = self.decay

        # 计算mlod_factor
        alpha = decay * (M ** E)
        if abs(alpha - 1.0) < 1e-6:
            mlod_factor = (M ** E - 1) * L
        else:
            sum_term = (1 - alpha ** L) / (1 - alpha)
            mlod_factor = (M ** E - 1) * sum_term

        # N_visible approx C × rho × base^E × mlod_factor
        C_density = 1.2
        volume_factor = (base / D) ** E
        predicted = int(C_density * effective_density * (D ** E) * volume_factor * mlod_factor)

        return predicted


def main():
    """主测试流程"""
    import sys

    # 加载数据
    lod_dir = r'C:\Doc\AGP\LoD\syp_lod_0529'
    if len(sys.argv) > 1:
        lod_dir = sys.argv[1]

    print("=" * 60)
    print("LOD Auto-Calculator Test")
    print("=" * 60)

    loader = SOGLODLoader(lod_dir)
    nodes = loader.get_octree_nodes_for_density()
    bounds_min, bounds_max = loader.get_scene_bounds()

    # 构建密度网格
    print("\n[Building density grid...]")
    grid = DensityGrid(bounds_min, bounds_max, grid_resolution=32)
    grid.build_from_nodes(nodes)

    # 创建计算器
    calculator = LODAutoCalculator(
        loader=loader,
        density_grid=grid,
        target_splat_count=1_000_000,
        lod_decay=0.125
    )

    # 分析场景内容分布，找到密度热点
    from analyze_scene_content import analyze_scene_content_distribution
    content_info = analyze_scene_content_distribution(loader)

    weighted_center = content_info['weighted_center']
    content_min = content_info['content_min']
    content_max = content_info['content_max']

    # 测试场景1：地面视角（在密度热点附近）
    print("\n" + "=" * 60)
    print("Test 1: Ground View (at density hotspot)")
    print("=" * 60)
    camera_pos_ground = weighted_center.copy()
    camera_pos_ground[1] = content_min[1] + 10  # 内容最低点+10m
    camera_forward = np.array([1, 0, 0])

    print(f"Camera position: {camera_pos_ground}")
    print(f"Camera forward: {camera_forward}")

    params_ground = calculator.compute_lod_parameters(
        camera_pos_ground, camera_forward,
        dimension=3, fov_deg=60
    )

    print("\n[Ground View Result]")
    print(f"  base: {params_ground.base_distance:.1f} m")
    print(f"  M: {params_ground.multiplier:.2f}")
    print(f"  Distances: {[f'{d:.1f}m' for d in params_ground.distances]}")

    # 测试场景2：空中俯视（俯瞰密度热点）
    print("\n" + "=" * 60)
    print("Test 2: Aerial View (looking down at hotspot)")
    print("=" * 60)
    camera_pos_aerial = weighted_center.copy()
    camera_pos_aerial[1] = content_max[1] + 100  # 内容最高点+100m
    camera_forward_down = np.array([0, -0.7, 0.7])

    print(f"Camera position: {camera_pos_aerial}")
    print(f"Camera forward: {camera_forward_down}")

    params_aerial = calculator.compute_lod_parameters(
        camera_pos_aerial, camera_forward_down,
        dimension=2,  # 航拍用2D模式
        fov_deg=60
    )

    print("\n[Aerial View Result]")
    print(f"  base: {params_aerial.base_distance:.1f} m")
    print(f"  M: {params_aerial.multiplier:.2f}")
    print(f"  Distances: {[f'{d:.1f}m' for d in params_aerial.distances]}")

    # 对比
    print("\n" + "=" * 60)
    print("Comparison")
    print("=" * 60)
    print(f"Ground base: {params_ground.base_distance:.1f}m")
    print(f"Aerial base: {params_aerial.base_distance:.1f}m")
    print(f"Ratio: {params_aerial.base_distance / params_ground.base_distance:.2f}x")


if __name__ == '__main__':
    main()
