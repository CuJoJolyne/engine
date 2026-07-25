"""
批量测试自适应LOD计算器 - 验证泛化能力
测试所有grid子场景 + 航拍场景
"""
import numpy as np
from pathlib import Path
from lod_density_loader import SOGLODLoader
from lod_density_grid import DensityGrid
from lod_adaptive_calculator import AdaptiveLODCalculator
from analyze_scene_content import analyze_scene_content_distribution


def test_scene(scene_path, scene_name, view_mode="ground"):
    """测试单个场景"""
    print(f"\n{'='*80}")
    print(f"Scene: {scene_name} ({view_mode})")
    print(f"{'='*80}")

    try:
        # 加载数据
        loader = SOGLODLoader(str(scene_path))
        stats = loader.get_statistics()
        nodes = loader.get_octree_nodes_for_density()
        bounds_min, bounds_max = loader.get_scene_bounds()

        # 构建密度网格
        grid = DensityGrid(bounds_min, bounds_max, grid_resolution=32)
        grid.build_from_nodes(nodes)

        # 分析内容分布
        content_info = analyze_scene_content_distribution(loader)

        # 创建计算器
        calculator = AdaptiveLODCalculator(
            density_grid=grid,
            scene_bounds=(bounds_min, bounds_max),
            lod_levels=loader.get_lod_levels(),
            total_splat_count=stats['total_lod0_splats'],
            target_splat_count=1_000_000
        )

        # 设置相机（根据视角模式）
        if view_mode == "aerial":
            camera_pos = content_info['weighted_center'].copy()
            camera_pos[1] = content_info['content_max'][1] + 200
            camera_forward = np.array([0, -1, 0])
        else:  # ground
            camera_pos = content_info['weighted_center'].copy()
            camera_pos[1] = content_info['content_min'][1] + 50
            camera_forward = np.array([1, 0, 0])

        # 计算参数
        result = calculator.compute_adaptive_params(
            camera_pos, camera_forward,
            view_mode=view_mode
        )

        return {
            'name': scene_name,
            'view_mode': view_mode,
            'total_splats': stats['total_lod0_splats'],
            'scene_diameter': loader.get_scene_diameter(),
            'lod_levels': loader.get_lod_levels(),
            'local_density': result.local_density,
            'base': result.base_distance,
            'M': result.multiplier,
            'distances': [round(d, 1) for d in result.distances],
            'predicted': result.predicted_splat_count,
            'converged': result.converged,
            'iterations': result.iterations,
            'error_pct': (result.predicted_splat_count - 1_000_000) / 1_000_000 * 100
        }

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    print("=" * 80)
    print("Adaptive LOD Calculator - Multi-Scene Validation")
    print("=" * 80)

    # 测试场景列表
    test_cases = []

    # Grid子场景（地面模式）
    base_dir = Path(r'C:\Doc\AGP\LoD\syp_lod_0529')
    for grid_dir in sorted(base_dir.glob('grid*')):
        if (grid_dir / 'lod-meta.json').exists():
            test_cases.append((grid_dir, grid_dir.name, "ground"))

    # 航拍场景
    aerial_path = Path(r'C:\Doc\AGP\LoD\nys_lod_0618')
    if aerial_path.exists():
        test_cases.append((aerial_path, "nys_aerial", "aerial"))

    # 运行测试
    results = []
    for scene_path, scene_name, view_mode in test_cases:
        result = test_scene(scene_path, scene_name, view_mode)
        if result:
            results.append(result)

    # 汇总报告
    print("\n" + "=" * 80)
    print("SUMMARY TABLE")
    print("=" * 80)

    print(f"\n{'Scene':<20} {'Splats':>10} {'Density':>10} {'base(m)':>8} {'M':>6} "
          f"{'Predicted':>12} {'Error%':>8} {'Conv':>5}")
    print("-" * 100)

    for r in results:
        conv_mark = "Y" if r['converged'] else "N"
        splats_m = r['total_splats'] / 1_000_000
        print(f"{r['name']:<20} {splats_m:>10.1f}M {r['local_density']:>10.1f} "
              f"{r['base']:>8.1f} {r['M']:>6.2f} {r['predicted']:>12,} "
              f"{r['error_pct']:>+7.1f}% {conv_mark:>5}")

    # 统计分析
    if results:
        converged_count = sum(1 for r in results if r['converged'])
        avg_error = np.mean([abs(r['error_pct']) for r in results])
        max_error = max([abs(r['error_pct']) for r in results])
        avg_iterations = np.mean([r['iterations'] for r in results])

        print(f"\n{'='*80}")
        print("Statistics")
        print(f"{'='*80}")
        print(f"Total scenes: {len(results)}")
        print(f"Converged: {converged_count}/{len(results)} ({converged_count/len(results)*100:.0f}%)")
        print(f"Avg error: {avg_error:.1f}%")
        print(f"Max error: {max_error:.1f}%")
        print(f"Avg iterations: {avg_iterations:.1f}")

        # 分析base与splat数关系
        print(f"\n{'='*80}")
        print("Base vs Scene Size")
        print(f"{'='*80}")

        sorted_results = sorted(results, key=lambda x: x['total_splats'])
        for r in sorted_results:
            ratio = r['total_splats'] / 1_000_000
            print(f"{r['name']:<20} N={ratio:>6.1f}M  base={r['base']:>6.1f}m  "
                  f"density={r['local_density']:>8.1f}  iter={r['iterations']}")


if __name__ == '__main__':
    main()
