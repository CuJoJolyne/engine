"""
LOD参数计算器 v2 - 数据驱动的经验模型
"""
import numpy as np
from typing import Dict
from lod_density_loader import SOGLODLoader
from lod_density_grid import DensityGrid
from lod_auto_calculator import LODParameters


class LODCalculatorV2:
    """基于经验校准的LOD参数计算器"""

    def __init__(self,
                 loader: SOGLODLoader,
                 density_grid: DensityGrid,
                 target_splat_count: int = 1_000_000):
        """
        Args:
            loader: SOG数据加载器
            density_grid: 密度网格
            target_splat_count: 目标渲染splat数
        """
        self.loader = loader
        self.grid = density_grid
        self.target_count = target_splat_count

        self.scene_diameter = loader.get_scene_diameter()
        self.lod_levels = loader.get_lod_levels()
        self.total_splat_count = loader.get_statistics()['total_lod0_splats']

        print(f"[LODCalculatorV2] Initialized")
        print(f"  Scene diameter: {self.scene_diameter:.1f} m")
        print(f"  LOD levels: {self.lod_levels}")
        print(f"  Total LOD0 splats: {self.total_splat_count:,}")

    def compute_lod_parameters(self,
                              camera_pos: np.ndarray,
                              camera_forward: np.ndarray,
                              view_mode: str = "auto") -> LODParameters:
        """计算LOD参数（经验模型）

        Args:
            camera_pos: 相机位置
            camera_forward: 相机前向
            view_mode: "ground" / "aerial" / "auto"（自动检测）

        Returns:
            LODParameters
        """
        print(f"\n[V2 Computing LOD params for camera at {camera_pos}]")

        # 1. 采样密度（用于视角模式检测）
        scene_bounds_min, scene_bounds_max = self.loader.get_scene_bounds()
        scene_center = (scene_bounds_min + scene_bounds_max) / 2
        dist_to_center = np.linalg.norm(camera_pos - scene_center)
        far_dist = min(dist_to_center + self.scene_diameter * 0.6, self.scene_diameter * 0.8)
        far_dist = max(far_dist, 100.0)

        profile = self.grid.sample_frustum_density(
            camera_pos, camera_forward,
            fov_deg=60, near_dist=1.0, far_dist=far_dist, num_samples=100
        )

        print(f"  Density profile: near={profile.near:.1f}, eff={profile.effective:.1f} splat/m^3")

        # 2. 自动检测视角模式（如果未指定）
        if view_mode == "auto":
            # 判断依据：
            # - 相机高度（Y坐标）相对内容范围
            # - forward向量的Y分量
            camera_y = camera_pos[1]
            forward_y = camera_forward[1]

            content_y_min = scene_bounds_min[1]
            content_y_max = scene_bounds_max[1]
            content_y_range = content_y_max - content_y_min

            # 相机在内容上方 + 向下看 → 航拍
            if camera_y > content_y_max + content_y_range * 0.2 and forward_y < -0.3:
                view_mode = "aerial"
            else:
                view_mode = "ground"

            print(f"  Auto-detected view mode: {view_mode}")

        # 3. 经验公式计算base
        base = self._compute_base_empirical(view_mode)

        # 4. 从手调参数规律计算M
        M = self._compute_multiplier_empirical(view_mode, base)

        # 5. 生成距离列表
        distances = [base * (M ** k) for k in range(self.lod_levels)]

        # 6. 简单预测（假设N_visible ∝ base^3）
        ratio = base / self.scene_diameter
        predicted_count = int(self.total_splat_count * (ratio ** 3) * 10)  # 粗略估计

        print(f"  Result: base={base:.1f}m, M={M:.2f}")
        print(f"  Distances: {[f'{d:.1f}m' for d in distances]}")
        print(f"  View mode: {view_mode}")

        return LODParameters(
            base_distance=base,
            multiplier=M,
            distances=distances,
            mlod_factor=0.0,  # v2不计算mlod_factor
            effective_density=profile.effective,
            predicted_splat_count=predicted_count
        )

    def _compute_base_empirical(self, view_mode: str) -> float:
        """经验公式计算base（基于拟合的校准系数）

        公式：base = k × (target/N)^alpha × L^beta

        从手调数据拟合的系数：
        - 航拍（11M, 1670m, 4层）：base=50m
        - 地面（82M, 6646m, 5层）：base=10m
        """
        N = self.total_splat_count
        target = self.target_count
        L = self.lod_levels

        # 拟合的校准系数（从fit_calibration.py）
        if view_mode == "aerial":
            k = 179.92
            alpha = 0.3366
            beta = -0.3331
        else:  # ground
            k = 149.84
            alpha = 0.4697
            beta = -0.3943

        # 计算base
        ratio = target / N
        base = k * (ratio ** alpha) * (L ** beta)

        # 限制base范围
        base = np.clip(base, 5.0, 200.0)

        return base

    def _compute_multiplier_empirical(self, view_mode: str, base: float) -> float:
        """经验公式计算M

        手调参数规律：
        - 航拍 [50, 100, 500, 1000]：M ≈ 2~10（非均匀）
        - 地面 [10, 20, 60, 200, 800]：M ≈ 2~3（非均匀）

        实际M不是固定的几何级数，但为了简化，用平均倍率
        """
        D = self.scene_diameter
        L = self.lod_levels

        if view_mode == "aerial":
            # 航拍：M稍大（2.0~2.5）
            M_target = 2.2
        else:
            # 地面：M中等（2.0~3.0）
            M_target = 2.5

        # 确保最后一层能覆盖场景
        # 约束：base × M^(L-1) >= D
        M_min = (D / base) ** (1.0 / (L - 1))
        M_min = max(M_min, 1.5)

        # 取目标M和最小M的加权平均
        M = 0.7 * M_target + 0.3 * M_min

        # 限制范围
        M = np.clip(M, 1.5, 3.5)

        return M


def test_v2():
    """测试v2计算器"""
    from analyze_scene_content import analyze_scene_content_distribution

    test_cases = [
        {
            'path': r'C:\Doc\AGP\LoD\nys_lod_0618',
            'name': 'nys_aerial',
            'view_mode': 'aerial',
            'manual': [50, 100, 500, 1000]
        },
        {
            'path': r'C:\Doc\AGP\LoD\syp_lod_0529',
            'name': 'syp_ground',
            'view_mode': 'ground',
            'manual': [10, 30, 100, 300, 10000]  # 正确的手调参数
        }
    ]

    print("=" * 80)
    print("LOD Calculator V2 Test")
    print("=" * 80)

    for case in test_cases:
        print(f"\n{'='*80}")
        print(f"Scene: {case['name']} ({case['view_mode']})")
        print(f"{'='*80}")

        # 加载
        loader = SOGLODLoader(case['path'])
        nodes = loader.get_octree_nodes_for_density()
        bounds_min, bounds_max = loader.get_scene_bounds()

        grid = DensityGrid(bounds_min, bounds_max, grid_resolution=32)
        grid.build_from_nodes(nodes)

        content_info = analyze_scene_content_distribution(loader)
        weighted_center = content_info['weighted_center']
        content_min = content_info['content_min']
        content_max = content_info['content_max']

        # 设置相机
        if case['view_mode'] == "aerial":
            camera_pos = weighted_center.copy()
            camera_pos[1] = content_max[1] + 200
            camera_forward = np.array([0, -1, 0])
        else:
            camera_pos = weighted_center.copy()
            camera_pos[1] = content_min[1] + 10
            camera_forward = np.array([1, 0, 0])

        # 计算
        calc = LODCalculatorV2(loader, grid, target_splat_count=1_000_000)
        result = calc.compute_lod_parameters(camera_pos, camera_forward, view_mode=case['view_mode'])

        # 对比
        manual_base = case['manual'][0]
        manual_M = case['manual'][1] / case['manual'][0]

        print(f"\nManual:  base={manual_base}m, M={manual_M:.2f}")
        print(f"         distances={case['manual']}")
        print(f"V2 Auto: base={result.base_distance:.1f}m, M={result.multiplier:.2f}")
        print(f"         distances={[round(d, 1) for d in result.distances]}")
        print(f"Ratio:   {result.base_distance / manual_base:.2f}x")


if __name__ == '__main__':
    test_v2()
