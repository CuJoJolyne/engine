"""
批量测试grid子目录 - 验证方案泛化能力
"""
import numpy as np
from pathlib import Path
from lod_density_loader import SOGLODLoader
from lod_density_grid import DensityGrid
from lod_calculator_v2 import LODCalculatorV2
from analyze_scene_content import analyze_scene_content_distribution


def test_grid_scenes():
    """测试syp_lod_0529下的所有grid子目录"""

    base_dir = Path(r'C:\Doc\AGP\LoD\syp_lod_0529')
    grid_dirs = sorted(base_dir.glob('grid*'))

    print("=" * 80)
    print(f"Testing {len(grid_dirs)} grid scenes")
    print("=" * 80)

    results = []

    for grid_dir in grid_dirs:
        grid_name = grid_dir.name
        meta_path = grid_dir / 'lod-meta.json'

        if not meta_path.exists():
            print(f"\n[SKIP] {grid_name}: no lod-meta.json")
            continue

        print(f"\n{'='*80}")
        print(f"Scene: {grid_name}")
        print(f"{'='*80}")

        try:
            # 加载数据
            loader = SOGLODLoader(str(grid_dir))
            stats = loader.get_statistics()
            nodes = loader.get_octree_nodes_for_density()
            bounds_min, bounds_max = loader.get_scene_bounds()

            # 构建密度网格
            grid = DensityGrid(bounds_min, bounds_max, grid_resolution=32)
            grid.build_from_nodes(nodes)

            # 分析内容分布
            content_info = analyze_scene_content_distribution(loader)
            weighted_center = content_info['weighted_center']
            content_min = content_info['content_min']

            # 创建计算器（地面模式）
            calculator = LODCalculatorV2(
                loader=loader,
                density_grid=grid,
                target_splat_count=1_000_000
            )

            # 设置地面视角相机
            camera_pos = weighted_center.copy()
            camera_pos[1] = content_min[1] + 10  # 地面+10m
            camera_forward = np.array([1, 0, 0])

            # 计算参数
            params = calculator.compute_lod_parameters(
                camera_pos, camera_forward,
                view_mode="ground"
            )

            # 记录结果
            result = {
                'name': grid_name,
                'total_splats': stats['total_lod0_splats'],
                'scene_diameter': loader.get_scene_diameter(),
                'content_diagonal': content_info['content_diagonal'],
                'lod_levels': loader.get_lod_levels(),
                'auto_base': params.base_distance,
                'auto_M': params.multiplier,
                'auto_distances': [round(d, 1) for d in params.distances],
                'effective_density': params.effective_density
            }

            results.append(result)

            print(f"\n[Result]")
            print(f"  Splats: {result['total_splats']:,}")
            print(f"  Scene diameter: {result['scene_diameter']:.1f} m")
            print(f"  Content diagonal: {result['content_diagonal']:.1f} m")
            print(f"  LOD levels: {result['lod_levels']}")
            print(f"  Auto base: {result['auto_base']:.1f} m")
            print(f"  Auto M: {result['auto_M']:.2f}")
            print(f"  Distances: {result['auto_distances']}")
            print(f"  Density: {result['effective_density']:.1f} splat/m^3")

        except Exception as e:
            print(f"\n[ERROR] Failed to process {grid_name}: {e}")
            import traceback
            traceback.print_exc()
            continue

    # 汇总分析
    print("\n" + "=" * 80)
    print("SUMMARY TABLE")
    print("=" * 80)

    print(f"\n{'Scene':<20} {'Splats':<15} {'Diameter(m)':<12} {'LOD':<5} {'Base(m)':<10} {'M':<6}")
    print("-" * 80)

    for r in results:
        print(f"{r['name']:<20} {r['total_splats']:<15,} "
              f"{r['scene_diameter']:<12.1f} {r['lod_levels']:<5} "
              f"{r['auto_base']:<10.1f} {r['auto_M']:<6.2f}")

    # 统计分析
    if results:
        splat_counts = [r['total_splats'] for r in results]
        bases = [r['auto_base'] for r in results]

        print(f"\n{'='*80}")
        print("Statistics")
        print(f"{'='*80}")
        print(f"Splat count range: {min(splat_counts):,} ~ {max(splat_counts):,}")
        print(f"Base range: {min(bases):.1f}m ~ {max(bases):.1f}m")
        print(f"Mean base: {np.mean(bases):.1f}m")
        print(f"Std base: {np.std(bases):.1f}m")

        # 分析base与splat数的关系
        print(f"\n{'='*80}")
        print("Base vs Splat Count Correlation")
        print(f"{'='*80}")

        # 按splat数排序
        sorted_results = sorted(results, key=lambda x: x['total_splats'])

        for r in sorted_results:
            ratio = r['total_splats'] / 1_000_000  # 相对1M的倍数
            expected_base = 10.0 * (ratio ** -0.4697)  # 基于拟合公式的预期
            deviation = (r['auto_base'] - expected_base) / expected_base * 100

            print(f"{r['name']:<20} N={r['total_splats']/1e6:>6.1f}M  "
                  f"base={r['auto_base']:>6.1f}m  "
                  f"expected={expected_base:>6.1f}m  "
                  f"dev={deviation:>+6.1f}%")

        # 检查是否需要重新拟合系数
        deviations = [abs((r['auto_base'] - 10.0 * (r['total_splats']/1e6) ** -0.4697) /
                         (10.0 * (r['total_splats']/1e6) ** -0.4697)) for r in results]
        avg_deviation = np.mean(deviations) * 100

        print(f"\nAverage deviation from fitted formula: {avg_deviation:.1f}%")

        if avg_deviation > 20:
            print("\n⚠️  High deviation detected! Consider refitting coefficients with expanded dataset.")
        else:
            print("\n✅  Formula generalization is good across different scene sizes.")


if __name__ == '__main__':
    test_grid_scenes()
