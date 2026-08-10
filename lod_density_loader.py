"""
LOD数据加载模块 - 支持gsbox生成的.sog格式
"""
import json
import logging
import numpy as np
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass

from lod_io_safety import safe_load_meta, safe_int_key, safe_count

logger = logging.getLogger("lod_density_loader")


@dataclass
class OctreeNode:
    """Octree节点"""
    bound_min: np.ndarray
    bound_max: np.ndarray
    center: np.ndarray
    size: float
    lods: Dict[int, Dict]  # {lod_level: {'file': int, 'offset': int, 'count': int}}
    is_leaf: bool

    @property
    def total_splat_count(self) -> int:
        """所有LOD层的splat总数"""
        return sum(lod['count'] for lod in self.lods.values()) if self.lods else 0

    @property
    def lod0_splat_count(self) -> int:
        """LOD0层的splat数"""
        return self.lods.get(0, {}).get('count', 0) if self.lods else 0


class SOGLODLoader:
    """gsbox .sog格式LOD数据加载器"""

    def __init__(self, lod_dir: str, allowed_base_dir: Optional[str] = None):
        """
        Args:
            lod_dir: LOD数据目录路径（包含lod-meta.json）
            allowed_base_dir: 可选，约束 lod-meta.json 真实路径必须在此目录内
                              （防路径穿越；None = 不限制，本地 CLI 默认）
        """
        self.lod_dir = Path(lod_dir)
        self.meta_path = self.lod_dir / 'lod-meta.json'

        # 安全加载：realpath 解析 + 可选目录约束 + 大小限制
        self.meta = safe_load_meta(self.meta_path, allowed_base_dir=allowed_base_dir)

        self.lod_levels = self.meta['lodLevels']
        self.filenames = self.meta['filenames']
        self.tree_root = self.meta['tree']

        # 解析场景边界
        self.scene_bound_min = np.array(self.tree_root['bound']['min'])
        self.scene_bound_max = np.array(self.tree_root['bound']['max'])

        # 计算场景对角线
        scene_size = self.scene_bound_max - self.scene_bound_min
        self.scene_diameter = float(np.linalg.norm(scene_size))

        print(f"[SOGLODLoader] Loaded {lod_dir}")
        print(f"  LOD levels: {self.lod_levels}")
        print(f"  Tiles: {len(self.filenames)}")
        print(f"  Scene bounds: {self.scene_bound_min} ~ {self.scene_bound_max}")
        print(f"  Scene diameter: {self.scene_diameter:.1f} m")

    def get_scene_diameter(self) -> float:
        """获取场景对角线长度"""
        return self.scene_diameter

    def get_lod_levels(self) -> int:
        """获取LOD层级数"""
        return self.lod_levels

    def get_scene_bounds(self) -> Tuple[np.ndarray, np.ndarray]:
        """获取场景边界"""
        return self.scene_bound_min, self.scene_bound_max

    def traverse_leaf_nodes(self) -> List[OctreeNode]:
        """遍历所有叶节点

        Returns:
            List of OctreeNode (leaf nodes only)
        """
        leaves = []

        def traverse(node_data):
            bound_min = np.array(node_data['bound']['min'])
            bound_max = np.array(node_data['bound']['max'])
            center = (bound_min + bound_max) / 2
            size = np.linalg.norm(bound_max - bound_min)

            has_children = 'children' in node_data and node_data['children']
            lods = node_data.get('lods', {})

            # 防 lods 容器本身畸形（非 dict，如 list/str）导致 .items() AttributeError
            # （砚砚 Finding：原第 4 条"格式异常+类型检查"的容器级近邻）
            if lods and not isinstance(lods, dict):
                logger.warning("Malformed 'lods' (not a dict, got %s) in %s, treating as empty",
                               type(lods).__name__, self.lod_dir)
                lods = {}

            if not has_children and lods:
                # 叶节点
                # 转换lods字典的key为int + 规范化 count（安全：跳过畸形项，
                # 不因单个坏 key/count 崩溃；防 count 非数字导致下游求和 TypeError）
                lods_int = {}
                for k, v in lods.items():
                    ik = safe_int_key(k)
                    if ik is None:
                        logger.warning("Skipping malformed LOD key %r in %s", k, self.lod_dir)
                        continue
                    c = safe_count(v)
                    if c is None:
                        logger.warning("Skipping malformed LOD count %r (key=%r) in %s",
                                       v, k, self.lod_dir)
                        continue
                    # 保留原 entry 结构（file/offset 等），但用规范化后的 int count 覆盖
                    entry = dict(v) if isinstance(v, dict) else {}
                    entry['count'] = c
                    lods_int[ik] = entry

                # 只保留有有效 LOD 数据的叶节点（空 dict 会破坏统计）
                if not lods_int:
                    logger.warning("Skipping leaf with no valid LOD entries in %s", self.lod_dir)
                    return

                node = OctreeNode(
                    bound_min=bound_min,
                    bound_max=bound_max,
                    center=center,
                    size=size,
                    lods=lods_int,
                    is_leaf=True
                )
                leaves.append(node)

            # 递归处理子节点
            if has_children:
                for child in node_data['children']:
                    traverse(child)

        traverse(self.tree_root)
        return leaves

    def get_octree_nodes_for_density(self) -> List[Dict]:
        """获取节点数据用于构建密度网格

        Returns:
            [{'center': [x,y,z], 'splat_count': int}, ...]
        """
        leaves = self.traverse_leaf_nodes()

        nodes = []
        for leaf in leaves:
            # 使用LOD0层的splat数作为密度计算基准
            splat_count = leaf.lod0_splat_count
            if splat_count > 0:
                nodes.append({
                    'center': leaf.center.tolist(),
                    'splat_count': splat_count,
                    'bounds': [leaf.bound_min.tolist(), leaf.bound_max.tolist()]
                })

        return nodes

    def get_statistics(self) -> Dict:
        """获取场景统计信息"""
        leaves = self.traverse_leaf_nodes()

        total_lod0 = sum(node.lod0_splat_count for node in leaves)
        total_all = sum(node.total_splat_count for node in leaves)

        # 按LOD层级统计
        lod_counts = {}
        for lod_level in range(self.lod_levels):
            count = sum(
                node.lods.get(lod_level, {}).get('count', 0)
                for node in leaves
            )
            lod_counts[lod_level] = count

        return {
            'leaf_nodes': len(leaves),
            'total_lod0_splats': total_lod0,
            'total_all_splats': total_all,
            'lod_distribution': lod_counts,
            'scene_diameter': self.scene_diameter,
            'scene_bounds': {
                'min': self.scene_bound_min.tolist(),
                'max': self.scene_bound_max.tolist()
            }
        }


if __name__ == '__main__':
    # 测试加载
    import sys

    if len(sys.argv) > 1:
        lod_dir = sys.argv[1]
    else:
        lod_dir = r'C:\Doc\AGP\LoD\syp_lod_0529'

    print(f"Loading LOD data from: {lod_dir}\n")

    loader = SOGLODLoader(lod_dir)

    print("\n[Statistics]")
    stats = loader.get_statistics()
    print(f"  Leaf nodes: {stats['leaf_nodes']}")
    print(f"  LOD0 splats: {stats['total_lod0_splats']:,}")
    print(f"  Total splats (all LODs): {stats['total_all_splats']:,}")
    print(f"\n  LOD distribution:")
    for lod, count in stats['lod_distribution'].items():
        print(f"    LOD{lod}: {count:,}")

    print("\n[Density Nodes Sample]")
    nodes = loader.get_octree_nodes_for_density()
    print(f"  Total nodes with splats: {len(nodes)}")
    print(f"  First 3 nodes:")
    for i, node in enumerate(nodes[:3]):
        print(f"    {i+1}. center={node['center']}, count={node['splat_count']}")
