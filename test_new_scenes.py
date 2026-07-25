"""
测试新增两组场景数据：
1. syp_lod_uav_0610 - 无人机空中漫游（整体 + 4个grid）
2. syp_lod_0610 - 地面漫游（整体 + 6个grid）
"""
import numpy as np
from pathlib import Path
from lod_density_loader import SOGLODLoader
from lod_density_grid import DensityGrid
from lod_adaptive_calculator import AdaptiveLODCalculator
from analyze_scene_content import analyze_scene_content_distribution


def test_scene(scene_path, scene_name, view_mode="ground"):
    """测试单个场景，返回结果dict"""
    try:
        loader = SOGLODLoader(str(scene_path))
        stats = loader.get_statistics()
        nodes = loader.get_octree_nodes_for_density()
        bounds_min, bounds_max = loader.get_scene_bounds()

        grid = DensityGrid(bounds_min, bounds_max, grid_resolution=32)
        grid.build_from_nodes(nodes)

        content_info = analyze_scene_content_distribution(loader)

        calculator = AdaptiveLODCalculator(
            density_grid=grid,
            scene_bounds=(bounds_min, bounds_max),
            lod_levels=loader.get_lod_levels(),
            total_splat_count=stats['total_lod0_splats'],
            target_splat_count=1_000_000
        )

        # 相机位置
        if view_mode == "aerial":
            camera_pos = content_info['weighted_center'].copy()
            camera_pos[1] = content_info['content_max'][1] + 50  # 内容上方50m
            camera_forward = np.array([0.3, -0.9, 0.0])
        else:
            camera_pos = content_info['weighted_center'].copy()
            camera_pos[1] = content_info['content_min'][1] + 50
            camera_forward = np.array([1, 0, 0])

        result = calculator.compute_adaptive_params(
            camera_pos, camera_forward, view_mode=view_mode
        )

        return {
            'name': scene_name,
            'view_mode': view_mode,
            'total_splats': stats['total_lod0_splats'],
            'lod_levels': loader.get_lod_levels(),
            'scene_diameter': loader.get_scene_diameter(),
            'local_density': result.local_density,
            'base': result.base_distance,
            'M': result.multiplier,
            'distances': result.distances,
            'predicted': result.predicted_splat_count,
            'converged': result.converged,
            'iterations': result.iterations
        }

    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback
        traceback.print_exc()
        return None


def print_results_table(title, results):
    """打印结果表格"""
    print(f"\n{'='*120}")
    print(f"  {title}")
    print(f"{'='*120}")
    print(f"{'Scene':<22} {'Splats':<10} {'LOD':<4} {'Diam(m)':<9} {'Density':<10} "
          f"{'base(m)':<8} {'M':<6} {'Distances':<45} {'Predicted':<12} {'Conv'}")
    print("-" * 140)

    for r in results:
        dist_str = ', '.join([f"{d:.0f}" for d in r['distances']])
        splats_str = f"{r['total_splats']/1e6:.1f}M"
        conv = "Y" if r['converged'] else "N*"
        print(f"{r['name']:<22} {splats_str:<10} {r['lod_levels']:<4} "
              f"{r['scene_diameter']:<9.1f} {r['local_density']:<10.1f} "
              f"{r['base']:<8.1f} {r['M']:<6.2f} [{dist_str}]{'':<5} "
              f"{r['predicted']:<12,} {conv}")


def main():
    print("=" * 120)
    print("LOD Adaptive Calculator - New Scene Data Test")
    print("Target: 1,000,000 splats")
    print("=" * 120)

    # ========================================
    # 1. syp_lod_uav_0610 - 无人机空中漫游
    # ========================================
    uav_base = Path(r'C:\Doc\AGP\LoD\syp_lod_uav_0610')
    uav_results = []

    # 整体场景
    print("\n[1] syp_lod_uav_0610 (aerial)")
    r = test_scene(uav_base, 'syp_uav_full', view_mode="aerial")
    if r:
        uav_results.append(r)

    # 子grid
    for grid_dir in sorted(uav_base.glob('grid*')):
        if (grid_dir / 'lod-meta.json').exists():
            name = f"uav_{grid_dir.name}"
            print(f"\n  Processing: {name}")
            r = test_scene(grid_dir, name, view_mode="aerial")
            if r:
                uav_results.append(r)

    print_results_table("syp_lod_uav_0610 - UAV Aerial", uav_results)

    # ========================================
    # 2. syp_lod_0610 - 地面漫游
    # ========================================
    ground_base = Path(r'C:\Doc\AGP\LoD\syp_lod_0610')
    ground_results = []

    # 整体场景
    print("\n\n[2] syp_lod_0610 (ground)")
    r = test_scene(ground_base, 'syp_0610_full', view_mode="ground")
    if r:
        ground_results.append(r)

    # 子grid
    for grid_dir in sorted(ground_base.glob('grid*')):
        if (grid_dir / 'lod-meta.json').exists():
            name = f"gnd_{grid_dir.name}"
            print(f"\n  Processing: {name}")
            r = test_scene(grid_dir, name, view_mode="ground")
            if r:
                ground_results.append(r)

    print_results_table("syp_lod_0610 - Ground", ground_results)

    # ========================================
    # 汇总
    # ========================================
    all_results = uav_results + ground_results
    print(f"\n\n{'='*120}")
    print("SUMMARY")
    print(f"{'='*120}")
    print(f"Total scenes: {len(all_results)}")
    converged = sum(1 for r in all_results if r['converged'])
    print(f"Converged: {converged}/{len(all_results)} ({converged/len(all_results)*100:.0f}%)")

    # 按类型统计
    print(f"\nUAV aerial scenes: {len(uav_results)}")
    uav_conv = sum(1 for r in uav_results if r['converged'])
    print(f"  Converged: {uav_conv}/{len(uav_results)}")
    if uav_results:
        bases = [r['base'] for r in uav_results]
        print(f"  Base range: {min(bases):.1f}m ~ {max(bases):.1f}m")

    print(f"\nGround scenes: {len(ground_results)}")
    gnd_conv = sum(1 for r in ground_results if r['converged'])
    print(f"  Converged: {gnd_conv}/{len(ground_results)}")
    if ground_results:
        bases = [r['base'] for r in ground_results]
        print(f"  Base range: {min(bases):.1f}m ~ {max(bases):.1f}m")


if __name__ == '__main__':
    main()
