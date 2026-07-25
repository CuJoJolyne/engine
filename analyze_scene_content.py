"""
场景内容分布分析工具
"""
import numpy as np
from lod_density_loader import SOGLODLoader

def analyze_scene_content_distribution(loader: SOGLODLoader):
    """分析场景实际内容分布"""
    nodes = loader.traverse_leaf_nodes()

    if not nodes:
        print("No leaf nodes found!")
        return

    # 收集所有有splat的节点的中心
    centers = []
    counts = []

    for node in nodes:
        if node.lod0_splat_count > 0:
            centers.append(node.center)
            counts.append(node.lod0_splat_count)

    centers = np.array(centers)
    counts = np.array(counts)

    print(f"\n[Content Distribution Analysis]")
    print(f"  Nodes with content: {len(centers)}")
    print(f"  Total splats: {np.sum(counts):,}")

    # 计算加权中心（splat密度中心）
    weighted_center = np.average(centers, axis=0, weights=counts)
    print(f"\n  Weighted center (density hotspot): {weighted_center}")

    # 计算内容边界（实际有splat的区域）
    content_min = np.min(centers, axis=0)
    content_max = np.max(centers, axis=0)
    content_size = content_max - content_min
    content_diagonal = np.linalg.norm(content_size)

    print(f"  Content bounds: {content_min} ~ {content_max}")
    print(f"  Content size: {content_size}")
    print(f"  Content diagonal: {content_diagonal:.1f} m")

    # 对比场景边界
    scene_min, scene_max = loader.get_scene_bounds()
    scene_size = scene_max - scene_min

    print(f"\n  Scene bounds: {scene_min} ~ {scene_max}")
    print(f"  Scene size: {scene_size}")
    print(f"  Scene diagonal: {loader.get_scene_diameter():.1f} m")

    print(f"\n  Content fill ratio: {content_diagonal / loader.get_scene_diameter():.1%}")

    # 找到密度最高的区域
    top_k = 10
    top_indices = np.argsort(counts)[-top_k:][::-1]

    print(f"\n  Top {top_k} densest nodes:")
    for i, idx in enumerate(top_indices):
        print(f"    {i+1}. center={centers[idx]}, count={counts[idx]:,}")

    return {
        'weighted_center': weighted_center,
        'content_min': content_min,
        'content_max': content_max,
        'content_diagonal': content_diagonal,
        'densest_centers': centers[top_indices]
    }


if __name__ == '__main__':
    import sys

    lod_dir = r'C:\Doc\AGP\LoD\syp_lod_0529'
    if len(sys.argv) > 1:
        lod_dir = sys.argv[1]

    loader = SOGLODLoader(lod_dir)
    result = analyze_scene_content_distribution(loader)

    print("\n" + "=" * 60)
    print("Recommendation for camera test positions:")
    print("=" * 60)

    wc = result['weighted_center']
    c_min = result['content_min']
    c_max = result['content_max']

    print(f"\nGround view (density hotspot):")
    ground_pos = wc.copy()
    ground_pos[1] = c_min[1] + 10  # 内容最低点+10m
    print(f"  Position: {ground_pos}")
    print(f"  Forward: [1, 0, 0]")

    print(f"\nAerial view (looking down at hotspot):")
    aerial_pos = wc.copy()
    aerial_pos[1] = c_max[1] + 100  # 内容最高点+100m
    print(f"  Position: {aerial_pos}")
    print(f"  Forward: [0, -0.7, 0.7]")
