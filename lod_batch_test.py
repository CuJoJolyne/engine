"""
批量测试LOD参数计算 - 对比手调参数进行校准
"""
import numpy as np
from pathlib import Path
from typing import Dict, List, Tuple
from lod_density_loader import SOGLODLoader
from lod_density_grid import DensityGrid
from lod_auto_calculator import LODAutoCalculator
from analyze_scene_content import analyze_scene_content_distribution


def extract_distances_from_manual(manual_distances: List[float]) -> Tuple[float, float]:
    """从手动距离列表反推base和M

    Args:
        manual_distances: [d0, d1, d2, ...] 例如 [50, 100, 500, 1000]

    Returns:
        (base, M) 其中 M ≈ d1/d0
    """
    base = manual_distances[0]
    if len(manual_distances) > 1:
        # M ≈ d1/d0 (几何级数倍率)
        M = manual_distances[1] / manual_distances[0]
    else:
        M = 2.0  # 默认
    return base, M


def test_scene(scene_path: str,
              scene_name: str,
              manual_params: Dict,
              view_type: str = "ground") -> Dict:
    """测试单个场景

    Args:
        scene_path: LOD数据目录
        scene_name: 场景名称
        manual_params: {'distances': [...], 'target': int}
        view_type: "ground" 或 "aerial"

    Returns:
        测试结果字典
    """
    print("\n" + "=" * 80)
    print(f"Testing: {scene_name} ({view_type} view)")
    print("=" * 80)

    # 加载数据
    loader = SOGLODLoader(scene_path)
    nodes = loader.get_octree_nodes_for_density()
    bounds_min, bounds_max = loader.get_scene_bounds()

    # 构建密度网格
    grid = DensityGrid(bounds_min, bounds_max, grid_resolution=32)
    grid.build_from_nodes(nodes)

    # 分析内容分布
    content_info = analyze_scene_content_distribution(loader)
    weighted_center = content_info['weighted_center']
    content_min = content_info['content_min']
    content_max = content_info['content_max']

    # 创建计算器
    target_count = manual_params.get('target', 1_000_000)
    calculator = LODAutoCalculator(
        loader=loader,
        density_grid=grid,
        target_splat_count=target_count,
        lod_decay=0.125
    )

    # 设置相机位置
    if view_type == "ground":
        camera_pos = weighted_center.copy()
        camera_pos[1] = content_min[1] + 10  # 地面+10m
        camera_forward = np.array([1, 0, 0])
        dimension = 3
    else:  # aerial
        camera_pos = weighted_center.copy()
        camera_pos[1] = content_max[1] + 200  # 高空+200m
        camera_forward = np.array([0, -1, 0])  # 垂直向下
        dimension = 2

    print(f"\nCamera: pos={camera_pos}, forward={camera_forward}")

    # 自动计算
    auto_params = calculator.compute_lod_parameters(
        camera_pos, camera_forward,
        dimension=dimension, fov_deg=60
    )

    # 手调参数
    manual_distances = manual_params['distances']
    manual_base, manual_M = extract_distances_from_manual(manual_distances)

    # 结果对比
    result = {
        'scene_name': scene_name,
        'view_type': view_type,
        'manual_base': manual_base,
        'manual_M': manual_M,
        'manual_distances': manual_distances,
        'auto_base': auto_params.base_distance,
        'auto_M': auto_params.multiplier,
        'auto_distances': [round(d, 1) for d in auto_params.distances],
        'base_ratio': auto_params.base_distance / manual_base,
        'effective_density': auto_params.effective_density,
        'predicted_count': auto_params.predicted_splat_count,
        'target_count': target_count,
        'scene_diameter': loader.get_scene_diameter(),
        'content_diagonal': content_info['content_diagonal'],
        'total_splats': loader.get_statistics()['total_lod0_splats']
    }

    print(f"\n{'='*40}")
    print("COMPARISON")
    print(f"{'='*40}")
    print(f"Manual:  base={manual_base:.1f}m, M={manual_M:.2f}")
    print(f"         distances={manual_distances}")
    print(f"Auto:    base={auto_params.base_distance:.1f}m, M={auto_params.multiplier:.2f}")
    print(f"         distances={result['auto_distances']}")
    print(f"Ratio:   base auto/manual = {result['base_ratio']:.2f}x")
    print(f"Density: effective={auto_params.effective_density:.1f} splat/m^3")

    return result


def main():
    """批量测试"""

    # 测试场景配置
    test_cases = [
        # 1. 无人机航拍场景（空中视角）
        {
            'path': r'C:\Doc\AGP\LoD\nys_lod_0618',
            'name': 'nys_aerial',
            'view_type': 'aerial',
            'manual': {
                'distances': [50, 100, 500, 1000],
                'target': 1_000_000
            }
        },

        # 2. 地面大场景（syp整体）
        {
            'path': r'C:\Doc\AGP\LoD\syp_lod_0529',
            'name': 'syp_full',
            'view_type': 'ground',
            'manual': {
                'distances': [10, 20, 60, 200, 800],  # 估计的5层参数
                'target': 1_000_000
            }
        }
    ]

    # 执行测试
    results = []

    for i, case in enumerate(test_cases):
        try:
            result = test_scene(
                scene_path=case['path'],
                scene_name=case['name'],
                manual_params=case['manual'],
                view_type=case['view_type']
            )
            results.append(result)
        except Exception as e:
            print(f"\n[ERROR] Failed to test {case['name']}: {e}")
            import traceback
            traceback.print_exc()
            continue

    # 汇总报告
    print("\n" + "=" * 80)
    print("SUMMARY REPORT")
    print("=" * 80)

    print(f"\nTested {len(results)} scenes:")
    print(f"\n{'Scene':<20} {'View':<10} {'Manual base':<12} {'Auto base':<12} {'Ratio':<10}")
    print("-" * 80)

    for r in results:
        print(f"{r['scene_name']:<20} {r['view_type']:<10} "
              f"{r['manual_base']:<12.1f} {r['auto_base']:<12.1f} "
              f"{r['base_ratio']:<10.2f}x")

    # 计算统计
    if results:
        ratios = [r['base_ratio'] for r in results]
        avg_ratio = np.mean(ratios)
        std_ratio = np.std(ratios)

        print(f"\nBase ratio statistics:")
        print(f"  Mean: {avg_ratio:.2f}x")
        print(f"  Std:  {std_ratio:.2f}x")
        print(f"  Range: [{min(ratios):.2f}x, {max(ratios):.2f}x]")

        # 建议校准系数
        calibration_factor = 1.0 / avg_ratio
        print(f"\nRecommended calibration factor: {calibration_factor:.3f}")
        print(f"  (multiply auto_base by this factor to match manual params)")


if __name__ == '__main__':
    main()
