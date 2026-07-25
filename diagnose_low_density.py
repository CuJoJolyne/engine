"""
诊断低密度场景问题
"""
import numpy as np
from lod_density_loader import SOGLODLoader
from lod_density_grid import DensityGrid
from analyze_scene_content import analyze_scene_content_distribution


def diagnose_scene(scene_path, scene_name):
    """诊断场景密度分布"""
    print(f"\n{'='*80}")
    print(f"Diagnosing: {scene_name}")
    print(f"{'='*80}")

    loader = SOGLODLoader(str(scene_path))
    stats = loader.get_statistics()
    nodes = loader.get_octree_nodes_for_density()
    bounds_min, bounds_max = loader.get_scene_bounds()

    grid = DensityGrid(bounds_min, bounds_max, grid_resolution=32)
    grid.build_from_nodes(nodes)

    content_info = analyze_scene_content_distribution(loader)

    # 测试多个位置的密度
    test_positions = [
        ("Weighted center", content_info['weighted_center']),
        ("Content min corner", content_info['content_min'] + np.array([10, 10, 10])),
        ("Content max corner", content_info['content_max'] - np.array([10, 10, 10])),
        ("Scene center", (bounds_min + bounds_max) / 2),
    ]

    print(f"\nScene stats:")
    print(f"  Total splats: {stats['total_lod0_splats']:,}")
    print(f"  Scene volume: {np.prod(bounds_max - bounds_min):.1f} m^3")
    print(f"  Global avg density: {stats['total_lod0_splats'] / np.prod(bounds_max - bounds_min):.4f} splat/m^3")

    content_volume = np.prod(content_info['content_max'] - content_info['content_min'])
    scene_volume = np.prod(bounds_max - bounds_min)
    fill_ratio = content_volume / scene_volume
    print(f"  Content fill ratio: {fill_ratio*100:.1f}%")

    print(f"\nDensity sampling at different positions:")
    for name, pos in test_positions:
        # 确保在边界内
        pos = np.clip(pos, bounds_min + 1, bounds_max - 1)

        # 单点查询
        point_density = grid.query_density(pos)

        # 局部采样（100m球体）
        local_samples = []
        radius = 100.0
        for dx in [-50, 0, 50]:
            for dy in [-50, 0, 50]:
                for dz in [-50, 0, 50]:
                    sample_pos = pos + np.array([dx, dy, dz])
                    sample_pos = np.clip(sample_pos, bounds_min + 1, bounds_max - 1)
                    d = grid.query_density(sample_pos)
                    if d > 0:
                        local_samples.append(d)

        local_avg = np.mean(local_samples) if local_samples else 0.0

        print(f"  {name}:")
        print(f"    Position: [{pos[0]:.1f}, {pos[1]:.1f}, {pos[2]:.1f}]")
        print(f"    Point density: {point_density:.4f} splat/m^3")
        print(f"    Local avg (100m): {local_avg:.4f} splat/m^3")
        print(f"    Non-zero samples: {len(local_samples)}/27")


if __name__ == '__main__':
    # 诊断未收敛的场景
    scenes = [
        (r'C:\Doc\AGP\LoD\syp_lod_0529\grid0_0_0_y', 'grid0_0_0_y'),
        (r'C:\Doc\AGP\LoD\syp_lod_0529\grid0_0_1_y', 'grid0_0_1_y'),
        (r'C:\Doc\AGP\LoD\syp_lod_0529\grid1_y', 'grid1_y (good case)'),
    ]

    for path, name in scenes:
        diagnose_scene(path, name)
