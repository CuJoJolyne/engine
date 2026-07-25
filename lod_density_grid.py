"""
密度网格模块 - 空间密度查询和视锥采样
"""
import numpy as np
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass


@dataclass
class DensityProfile:
    """视锥内的密度分布剖面"""
    near: float      # 近处平均密度 (splat/m^3)
    mid: float       # 中距平均密度
    far: float       # 远处平均密度
    effective: float # 加权有效密度
    samples: int     # 采样点数


class DensityGrid:
    """三维密度网格 - O(1)空间密度查询"""

    def __init__(self,
                 bounds_min: np.ndarray,
                 bounds_max: np.ndarray,
                 grid_resolution: int = 32):
        """
        Args:
            bounds_min: 场景最小边界 [x, y, z]
            bounds_max: 场景最大边界 [x, y, z]
            grid_resolution: 每维网格数（默认32³=32768个cell）
        """
        self.bounds_min = bounds_min
        self.bounds_max = bounds_max
        self.size = bounds_max - bounds_min
        self.resolution = grid_resolution

        # 网格cell尺寸
        self.cell_size = self.size / grid_resolution

        # 密度网格：[res_x, res_y, res_z]
        self.grid = np.zeros((grid_resolution, grid_resolution, grid_resolution),
                            dtype=np.float32)

        print(f"[DensityGrid] Created {grid_resolution}^3 grid")
        print(f"  Bounds: {bounds_min} ~ {bounds_max}")
        print(f"  Cell size: {self.cell_size}")

    def build_from_nodes(self, nodes: List[Dict]):
        """从octree节点构建密度网格

        Args:
            nodes: [{'center': [x,y,z], 'splat_count': int, 'bounds': [[min],[max]]}, ...]
        """
        print(f"[DensityGrid] Building from {len(nodes)} nodes...")

        for node in nodes:
            center = np.array(node['center'])
            count = node['splat_count']
            bounds = node['bounds']

            # 计算节点体积
            node_min = np.array(bounds[0])
            node_max = np.array(bounds[1])
            node_volume = np.prod(node_max - node_min)

            if node_volume <= 0:
                continue

            # 节点密度
            density = count / node_volume

            # 将节点密度分配到覆盖的所有网格cell（而不是只分配中心点）
            # 计算节点覆盖的cell范围
            cell_min = self._world_to_grid(node_min)
            cell_max = self._world_to_grid(node_max)

            # 遍历覆盖范围内的所有cell
            for ix in range(cell_min[0], cell_max[0] + 1):
                for iy in range(cell_min[1], cell_max[1] + 1):
                    for iz in range(cell_min[2], cell_max[2] + 1):
                        if self._is_valid_index((ix, iy, iz)):
                            # 均匀分配密度到覆盖的cell
                            num_cells = (cell_max[0] - cell_min[0] + 1) * \
                                       (cell_max[1] - cell_min[1] + 1) * \
                                       (cell_max[2] - cell_min[2] + 1)
                            self.grid[ix, iy, iz] += density / max(num_cells, 1)

        total_density = np.sum(self.grid)
        non_zero_cells = np.count_nonzero(self.grid)
        print(f"  Total density: {total_density:.2e}")
        print(f"  Non-zero cells: {non_zero_cells} / {self.resolution**3}")
        print(f"  Avg density (non-zero): {total_density / max(non_zero_cells, 1):.1f} splat/m^3")

    def _world_to_grid(self, pos: np.ndarray) -> Tuple[int, int, int]:
        """世界坐标 → 网格索引"""
        normalized = (pos - self.bounds_min) / self.size
        idx = (normalized * self.resolution).astype(int)
        # clamp到有效范围
        idx = np.clip(idx, 0, self.resolution - 1)
        return tuple(idx)

    def _is_valid_index(self, idx: Tuple[int, int, int]) -> bool:
        """检查网格索引是否有效"""
        return all(0 <= i < self.resolution for i in idx)

    def query_density(self, pos: np.ndarray) -> float:
        """查询点密度

        Args:
            pos: 世界坐标 [x, y, z]

        Returns:
            密度 (splat/m^3)
        """
        idx = self._world_to_grid(pos)
        return float(self.grid[idx[0], idx[1], idx[2]])

    def sample_frustum_density(self,
                              camera_pos: np.ndarray,
                              camera_forward: np.ndarray,
                              fov_deg: float = 60.0,
                              near_dist: float = 1.0,
                              far_dist: float = 1000.0,
                              num_samples: int = 100) -> DensityProfile:
        """采样视锥内的密度分布

        Args:
            camera_pos: 相机位置 [x, y, z]
            camera_forward: 相机前向（归一化）[x, y, z]
            fov_deg: 视场角（度）
            near_dist: 近裁剪面距离
            far_dist: 远裁剪面距离
            num_samples: 采样点数

        Returns:
            DensityProfile with near/mid/far/effective densities
        """
        forward = camera_forward / np.linalg.norm(camera_forward)

        # 生成视锥内的采样点
        samples_near = []
        samples_mid = []
        samples_far = []

        fov_rad = np.deg2rad(fov_deg)
        tan_half_fov = np.tan(fov_rad / 2)

        for _ in range(num_samples):
            # 随机距离（对数分布，远处更多采样）
            t = np.random.random()
            dist = near_dist * (far_dist / near_dist) ** t

            # 随机偏移（圆锥内）
            radius = dist * tan_half_fov
            angle = np.random.random() * 2 * np.pi
            offset_x = radius * np.cos(angle) * np.random.random()
            offset_y = radius * np.sin(angle) * np.random.random()

            # 构造正交基（简化：假设forward不平行于Y轴）
            up = np.array([0, 1, 0])
            if abs(np.dot(forward, up)) > 0.99:
                up = np.array([1, 0, 0])
            right = np.cross(forward, up)
            right = right / np.linalg.norm(right)
            up = np.cross(right, forward)

            # 采样点世界坐标
            sample_pos = camera_pos + forward * dist + right * offset_x + up * offset_y

            # 查询密度
            density = self.query_density(sample_pos)

            # 分段统计
            third = (far_dist - near_dist) / 3
            if dist < near_dist + third:
                samples_near.append(density)
            elif dist < near_dist + 2 * third:
                samples_mid.append(density)
            else:
                samples_far.append(density)

        # 计算各段平均密度
        near_density = np.mean(samples_near) if samples_near else 0.0
        mid_density = np.mean(samples_mid) if samples_mid else 0.0
        far_density = np.mean(samples_far) if samples_far else 0.0

        # 加权有效密度（近处权重高）
        weights = np.array([0.5, 0.3, 0.2])  # near, mid, far
        effective_density = (
            weights[0] * near_density +
            weights[1] * mid_density +
            weights[2] * far_density
        )

        return DensityProfile(
            near=near_density,
            mid=mid_density,
            far=far_density,
            effective=effective_density,
            samples=num_samples
        )


if __name__ == '__main__':
    # 测试
    from lod_density_loader import SOGLODLoader

    loader = SOGLODLoader(r'C:\Doc\AGP\LoD\syp_lod_0529')
    nodes = loader.get_octree_nodes_for_density()

    bounds_min, bounds_max = loader.get_scene_bounds()

    print("\n[Building density grid...]")
    grid = DensityGrid(bounds_min, bounds_max, grid_resolution=32)
    grid.build_from_nodes(nodes)

    print("\n[Testing frustum sampling...]")
    # 地面视角（场景中心附近）
    scene_center = (bounds_min + bounds_max) / 2
    camera_pos_ground = scene_center.copy()
    camera_pos_ground[1] = bounds_min[1] + 10  # 地面高度+10m
    camera_forward = np.array([1, 0, 0])

    profile_ground = grid.sample_frustum_density(
        camera_pos_ground, camera_forward,
        fov_deg=60, near_dist=1, far_dist=500, num_samples=100
    )

    print(f"\nGround view density profile:")
    print(f"  Near:      {profile_ground.near:.1f} splat/m^3")
    print(f"  Mid:       {profile_ground.mid:.1f} splat/m^3")
    print(f"  Far:       {profile_ground.far:.1f} splat/m^3")
    print(f"  Effective: {profile_ground.effective:.1f} splat/m^3")

    # 空中视角
    camera_pos_aerial = scene_center.copy()
    camera_pos_aerial[1] = bounds_max[1] - 100  # 高空
    camera_forward_down = np.array([0, -0.7, 0.7])  # 向下俯视

    profile_aerial = grid.sample_frustum_density(
        camera_pos_aerial, camera_forward_down,
        fov_deg=60, near_dist=1, far_dist=1000, num_samples=100
    )

    print(f"\nAerial view density profile:")
    print(f"  Near:      {profile_aerial.near:.1f} splat/m^3")
    print(f"  Mid:       {profile_aerial.mid:.1f} splat/m^3")
    print(f"  Far:       {profile_aerial.far:.1f} splat/m^3")
    print(f"  Effective: {profile_aerial.effective:.1f} splat/m^3")
