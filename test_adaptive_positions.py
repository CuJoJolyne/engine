"""
测试自适应LOD计算器在不同相机位置的表现
"""
import numpy as np
from lod_density_loader import SOGLODLoader
from lod_density_grid import DensityGrid
from lod_adaptive_calculator import AdaptiveLODCalculator
from analyze_scene_content import analyze_scene_content_distribution


def test_multiple_positions():
    """测试多个相机位置"""

    scene_path = r'C:\Doc\AGP\LoD\syp_lod_0529\grid1_y'

    # 加载数据
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

    # 测试位置
    test_cases = [
        {
            'name': '密度热点（加权中心）',
            'pos': content_info['weighted_center'].copy(),
            'forward': np.array([1, 0, 0])
        },
        {
            'name': '场景中心（可能低密度）',
            'pos': (bounds_min + bounds_max) / 2,
            'forward': np.array([1, 0, 0])
        },
        {
            'name': '场景边缘',
            'pos': np.array([
                bounds_min[0] + 50,
                (bounds_min[1] + bounds_max[1]) / 2,
                (bounds_min[2] + bounds_max[2]) / 2
            ]),
            'forward': np.array([1, 0, 0])
        }
    ]

    print("=" * 80)
    print("Adaptive LOD Calculator - Multi-Position Test")
    print("=" * 80)

    results = []

    for case in test_cases:
        print(f"\n{'='*80}")
        print(f"Test: {case['name']}")
        print(f"{'='*80}")

        # 确保相机在内容区域内
        camera_pos = case['pos'].copy()
        camera_pos[1] = max(camera_pos[1], content_info['content_min'][1] + 10)

        result = calculator.compute_adaptive_params(
            camera_pos, case['forward'],
            view_mode="ground"
        )

        results.append({
            'name': case['name'],
            'local_density': result.local_density,
            'base': result.base_distance,
            'M': result.multiplier,
            'distances': result.distances,
            'predicted': result.predicted_splat_count,
            'converged': result.converged,
            'iterations': result.iterations
        })

    # 汇总表格
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)

    print(f"\n{'Location':<30} {'Density':>12} {'base(m)':>10} {'M':>6} {'Predicted':>12} {'Conv':>6}")
    print("-" * 80)

    for r in results:
        converged_mark = "Y" if r['converged'] else "N"
        print(f"{r['name']:<30} {r['local_density']:>12.1f} {r['base']:>10.1f} "
              f"{r['M']:>6.2f} {r['predicted']:>12,} {converged_mark:>6}")

    # 距离列表详情
    print(f"\n{'='*80}")
    print("Distance Lists")
    print(f"{'='*80}")

    for r in results:
        distances_str = [f"{d:.1f}m" for d in r['distances']]
        print(f"\n{r['name']}:")
        print(f"  {distances_str}")
        print(f"  Iterations: {r['iterations']}")


if __name__ == '__main__':
    test_multiple_positions()
