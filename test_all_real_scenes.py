"""
全量测试：所有真实场景的自适应LOD参数计算
包括室内（3个）+ 室外地面（grid子目录）+ 室外航拍（1个）
"""
import numpy as np
from pathlib import Path
from lod_density_loader import SOGLODLoader
from lod_density_grid import DensityGrid
from lod_adaptive_calculator import AdaptiveLODCalculator
from analyze_scene_content import analyze_scene_content_distribution


def test_scene(scene_path, scene_name, view_mode="ground", scene_type="outdoor"):
    """测试单个场景"""
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
            camera_pos[1] = content_info['content_max'][1] + 200
            camera_forward = np.array([0, -1, 0])
        else:
            camera_pos = content_info['weighted_center'].copy()
            camera_pos[1] = content_info['content_min'][1] + 50
            camera_forward = np.array([1, 0, 0])

        result = calculator.compute_adaptive_params(
            camera_pos, camera_forward, view_mode=view_mode
        )

        # 计算场景尺寸
        scene_size = bounds_max - bounds_min
        content_size = content_info['content_max'] - content_info['content_min']

        return {
            'name': scene_name,
            'type': scene_type,
            'view_mode': view_mode,
            'total_splats': stats['total_lod0_splats'],
            'lod_levels': loader.get_lod_levels(),
            'scene_diameter': loader.get_scene_diameter(),
            'content_diagonal': float(np.linalg.norm(content_size)),
            'local_density': result.local_density,
            'base': result.base_distance,
            'M': result.multiplier,
            'distances': result.distances,
            'predicted': result.predicted_splat_count,
            'converged': result.converged,
            'iterations': result.iterations
        }

    except Exception as e:
        print(f"  ERROR processing {scene_name}: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    print("=" * 100)
    print("LOD Adaptive Calculator - All Real Scenes Test")
    print("Target: 1,000,000 splats")
    print("=" * 100)

    results = []

    # === 室内场景 ===
    indoor_scenes = [
        (r'C:\Doc\AGP\LoD\10_xiaohuizhou_mask', '10_xiaohuizhou (indoor)'),
        (r'C:\Doc\AGP\LoD\11_adongge_mask', '11_adongge (indoor)'),
        (r'C:\Doc\AGP\LoD\14_huixingshengchouguiyu', '14_huixing (indoor)'),
    ]

    print("\n--- Indoor Scenes ---")
    for path, name in indoor_scenes:
        print(f"\nProcessing: {name}")
        r = test_scene(path, name, view_mode="ground", scene_type="indoor")
        if r:
            results.append(r)

    # === 室外航拍 ===
    print("\n--- Outdoor Aerial ---")
    aerial_path = r'C:\Doc\AGP\LoD\nys_lod_0618'
    print(f"\nProcessing: nys_aerial")
    r = test_scene(aerial_path, 'nys_aerial', view_mode="aerial", scene_type="outdoor_aerial")
    if r:
        results.append(r)

    # === 室外地面（整体场景） ===
    print("\n--- Outdoor Ground (Full Scene) ---")
    syp_path = r'C:\Doc\AGP\LoD\syp_lod_0529'
    if Path(syp_path).joinpath('lod-meta.json').exists():
        print(f"\nProcessing: syp_full_ground")
        r = test_scene(syp_path, 'syp_full (82M)', view_mode="ground", scene_type="outdoor_ground")
        if r:
            results.append(r)

    # === 室外地面（grid子场景） ===
    print("\n--- Outdoor Ground (Grid Sub-scenes) ---")
    base_dir = Path(r'C:\Doc\AGP\LoD\syp_lod_0529')
    for grid_dir in sorted(base_dir.glob('grid*')):
        if (grid_dir / 'lod-meta.json').exists():
            name = grid_dir.name
            print(f"\nProcessing: {name}")
            r = test_scene(grid_dir, name, view_mode="ground", scene_type="outdoor_ground")
            if r:
                results.append(r)

    # === 输出结果表格 ===
    print("\n\n")
    print("=" * 120)
    print("FINAL RESULTS TABLE")
    print("=" * 120)

    # 按类型分组
    indoor_results = [r for r in results if r['type'] == 'indoor']
    aerial_results = [r for r in results if r['type'] == 'outdoor_aerial']
    ground_results = [r for r in results if r['type'] == 'outdoor_ground']

    header = f"{'Scene':<25} {'Type':<10} {'Splats':<10} {'LOD':<4} {'Diameter':<10} {'Density':<10} {'base(m)':<8} {'M':<6} {'Distances':<40} {'Predicted':<12} {'Conv':<5}"
    sep = "-" * 140

    print(f"\n{header}")
    print(sep)

    # 室内
    if indoor_results:
        for r in indoor_results:
            dist_str = ', '.join([f"{d:.1f}" for d in r['distances']])
            splats_str = f"{r['total_splats']/1e6:.1f}M"
            conv = "Y" if r['converged'] else "N"
            print(f"{r['name']:<25} {'indoor':<10} {splats_str:<10} {r['lod_levels']:<4} "
                  f"{r['scene_diameter']:<10.1f} {r['local_density']:<10.1f} "
                  f"{r['base']:<8.1f} {r['M']:<6.2f} [{dist_str}]{'':<5} {r['predicted']:<12,} {conv:<5}")
        print(sep)

    # 航拍
    if aerial_results:
        for r in aerial_results:
            dist_str = ', '.join([f"{d:.1f}" for d in r['distances']])
            splats_str = f"{r['total_splats']/1e6:.1f}M"
            conv = "Y" if r['converged'] else "N"
            print(f"{r['name']:<25} {'aerial':<10} {splats_str:<10} {r['lod_levels']:<4} "
                  f"{r['scene_diameter']:<10.1f} {r['local_density']:<10.1f} "
                  f"{r['base']:<8.1f} {r['M']:<6.2f} [{dist_str}]{'':<5} {r['predicted']:<12,} {conv:<5}")
        print(sep)

    # 地面
    if ground_results:
        for r in sorted(ground_results, key=lambda x: -x['total_splats']):
            dist_str = ', '.join([f"{d:.1f}" for d in r['distances']])
            splats_str = f"{r['total_splats']/1e6:.1f}M"
            conv = "Y" if r['converged'] else "N"
            print(f"{r['name']:<25} {'ground':<10} {splats_str:<10} {r['lod_levels']:<4} "
                  f"{r['scene_diameter']:<10.1f} {r['local_density']:<10.1f} "
                  f"{r['base']:<8.1f} {r['M']:<6.2f} [{dist_str}]{'':<5} {r['predicted']:<12,} {conv:<5}")
        print(sep)

    # 统计
    print(f"\nTotal scenes tested: {len(results)}")
    converged = sum(1 for r in results if r['converged'])
    print(f"Converged: {converged}/{len(results)} ({converged/len(results)*100:.0f}%)")

    # 手调参数对比
    print(f"\n{'='*120}")
    print("Manual vs Adaptive Comparison (known scenes)")
    print(f"{'='*120}")
    print(f"\n{'Scene':<25} {'Manual base':<12} {'Auto base':<12} {'Manual distances':<35} {'Auto distances':<45}")
    print("-" * 130)

    # 已知手调参数
    manual_params = {
        'nys_aerial': {'base': 50, 'distances': [50, 100, 500, 1000]},
        'syp_full (82M)': {'base': 10, 'distances': [10, 30, 100, 300, 10000]},
    }

    for r in results:
        if r['name'] in manual_params:
            mp = manual_params[r['name']]
            auto_dist_str = ', '.join([f"{d:.0f}" for d in r['distances']])
            manual_dist_str = ', '.join([str(d) for d in mp['distances']])
            print(f"{r['name']:<25} {mp['base']:<12} {r['base']:<12.1f} "
                  f"[{manual_dist_str}]{'':<10} [{auto_dist_str}]")


if __name__ == '__main__':
    main()
