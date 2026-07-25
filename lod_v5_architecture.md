# LOD 参数计算器 V5 架构设计

> **作者**: 布偶猫/宪宪 claude-opus-4-6 🐾  
> **日期**: 2026-07-24

---

## 1. 系统架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                     LOD Parameter Calculator V5                  │
│                                                                   │
│  输入:                                                            │
│  • lod-meta.json (场景 tile 数据)                                │
│  • view_mode ("ground" / "aerial")                               │
│  • target_splat_count (默认 1M)                                  │
│                                                                   │
│  输出:                                                            │
│  • base_distance (LOD0 切换半径)                                 │
│  • multiplier (M, 几何级数倍率)                                  │
│  • distances [base, base×M, base×M², ...]                       │
│  • predicted_splat_count (预测渲染 splat 数)                    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    TileLODCalculatorV5 类                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  核心组件:                                                        │
│                                                                   │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ 1. Tile Loader (_load_tiles)                             │  │
│  │    • 解析 lod-meta.json                                  │  │
│  │    • 遍历 octree，提取叶节点 tile                        │  │
│  │    • 每个 tile: (center, {lod_level: splat_count})      │  │
│  └───────────────────────────────────────────────────────────┘  │
│                        │                                          │
│                        ▼                                          │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ 2. Scene Statistics                                       │  │
│  │    • 计算场景中心、最远距离                              │  │
│  │    • 计算 tile 空间密度 (tiles/m² in XZ plane)          │  │
│  │    • tile_density = tile_count / (x_range × z_range)    │  │
│  └───────────────────────────────────────────────────────────┘  │
│                        │                                          │
│                        ▼                                          │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ 3. Binary Search Solver (_solve_base)                    │  │
│  │    • 初始区间: [1m, 500m]                                │  │
│  │    • 目标: predicted ∈ [0.8M, 1.2M]                      │  │
│  │    • 迭代: base_mid = (base_lo + base_hi) / 2           │  │
│  │    • 收敛判断: |predicted - target| < 20%               │  │
│  └───────────────────────────────────────────────────────────┘  │
│                        │                                          │
│                        ▼                                          │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ 4. Prediction Engine (_predict)                          │  │
│  │                                                            │  │
│  │    predicted = tile_simulation(base, M) × visibility     │  │
│  │                                                            │  │
│  │    ┌──────────────────────────────────────────────────┐  │  │
│  │    │ 4a. Tile Simulation (_tile_simulation)          │  │  │
│  │    │     • 遍历每个 tile                              │  │  │
│  │    │     • dist = ||tile_center - scene_center||     │  │  │
│  │    │     • 分配 LOD: lod = argmin(dist <= dists[k]) │  │  │
│  │    │     • 累加: total += tile.lod_counts[lod]       │  │  │
│  │    └──────────────────────────────────────────────────┘  │  │
│  │                        │                                   │  │
│  │                        ▼                                   │  │
│  │    ┌──────────────────────────────────────────────────┐  │  │
│  │    │ 4b. Visibility Factor (自适应)                  │  │  │
│  │    │                                                  │  │  │
│  │    │  Ground:                                         │  │  │
│  │    │    if max_tile_distance < 100m:                 │  │  │
│  │    │        visibility = 1.0  (室内/小场景)          │  │  │
│  │    │    else:                                         │  │  │
│  │    │        visibility = 0.11 (大型室外)             │  │  │
│  │    │                                                  │  │  │
│  │    │  Aerial: 基于 tile_density                      │  │  │
│  │    │  • <0.0008: 1.0 (极稀疏，无遮挡)               │  │  │
│  │    │  • 0.0008-0.0015: 1.0 → 0.4 (线性插值)         │  │  │
│  │    │  • 0.0015-0.003: 0.4 → 0.25                     │  │  │
│  │    │  • >0.003: 0.25 (高密度，遮挡严重)             │  │  │
│  │    └──────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 核心流程图

### 2.1 主流程（compute 方法）

```
开始
 │
 ├─→ 加载 tile 数据 (_load_tiles)
 │    • 解析 lod-meta.json
 │    • 提取所有叶节点 tile
 │    • tiles = [(center, lod_counts), ...]
 │
 ├─→ 计算场景统计
 │    • scene_center = median(tile_centers)
 │    • max_tile_distance = max(||center - scene_center||)
 │    • tile_density = tile_count / (x_range × z_range)
 │
 ├─→ 求解最优 base (_solve_base)
 │    │
 │    └─→ [二分法循环] ──────────────┐
 │         │                           │
 │         ├─→ base_mid = (lo + hi)/2 │
 │         │                           │
 │         ├─→ 计算 M                 │
 │         │   M = (max_dist/base)^(1/(levels-1))
 │         │   M = clip(M, 1.5, 4.0)  │
 │         │                           │
 │         ├─→ 预测 splat 数          │
 │         │   pred = _predict(base, M, mode)
 │         │                           │
 │         ├─→ 收敛判断               │
 │         │   if 0.8M ≤ pred ≤ 1.2M: │
 │         │       return base_mid    │
 │         │                           │
 │         ├─→ 调整区间               │
 │         │   if pred < target:      │
 │         │       base_lo = base_mid │
 │         │   else:                  │
 │         │       base_hi = base_mid │
 │         │                           │
 │         └─→ [重复 15 次] ──────────┘
 │
 ├─→ 计算最终参数
 │    • M = (max_dist / base) ^ (1/(levels-1))
 │    • distances = [base × M^k for k in 0..levels-1]
 │    • predicted = _predict(base, M, view_mode)
 │
 └─→ 返回结果
      TileLODResult(
          base_distance, multiplier, distances,
          predicted_splat_count, iterations, converged
      )
```

### 2.2 预测子流程（_predict 方法）

```
_predict(base, M, view_mode)
 │
 ├─→ Tile Simulation
 │    │
 │    └─→ distances = [base × M^k for k in 0..levels-1]
 │         │
 │         ├─→ [遍历所有 tile] ───────────────┐
 │         │    │                              │
 │         │    ├─→ dist = ||center - scene_center||
 │         │    │                              │
 │         │    ├─→ 分配 LOD                  │
 │         │    │   lod = levels - 1  (默认最粗)
 │         │    │   for k in 0..levels-1:     │
 │         │    │       if dist ≤ distances[k]:│
 │         │    │           lod = k            │
 │         │    │           break              │
 │         │    │                              │
 │         │    ├─→ 累加 splat 数             │
 │         │    │   total += lod_counts[lod]  │
 │         │    │                              │
 │         │    └─→ [下一个 tile] ────────────┘
 │         │
 │         └─→ sim_total = total
 │
 ├─→ 自适应 Visibility Factor
 │    │
 │    ├─→ if view_mode == "ground":
 │    │       visibility = 0.11
 │    │
 │    └─→ if view_mode == "aerial":
 │             if tile_density < 0.0008:
 │                 visibility = 1.0
 │             elif tile_density < 0.0015:
 │                 t = (density - 0.0008) / 0.0007
 │                 visibility = 1.0 - t × 0.6
 │             elif tile_density < 0.003:
 │                 t = (density - 0.0015) / 0.0015
 │                 visibility = 0.4 - t × 0.15
 │             else:
 │                 visibility = 0.25
 │
 └─→ return sim_total × visibility
```

---

## 3. 数据流图

```
lod-meta.json
     │
     │ parse
     ▼
┌─────────────────┐
│  Tile List      │
│  [(center,      │
│    {lod: count})]│
└─────────────────┘
     │
     │ compute statistics
     ▼
┌─────────────────┐
│ Scene Stats     │
│ • scene_center  │
│ • max_distance  │
│ • tile_density  │
└─────────────────┘
     │
     │ + view_mode + target
     ▼
┌─────────────────────────────────┐
│   Binary Search Loop (15 iter)  │
│                                  │
│   ┌─────────────────────────┐   │
│   │  Trial: base_mid        │   │
│   │         ↓               │   │
│   │  Calculate M            │   │
│   │         ↓               │   │
│   │  Tile Simulation        │◄──┼── tiles + distances
│   │         ↓               │   │
│   │  × Visibility Factor    │◄──┼── view_mode + tile_density
│   │         ↓               │   │
│   │  predicted_count        │   │
│   │         ↓               │   │
│   │  Converge Check         │   │
│   │         ↓               │   │
│   │  Adjust [lo, hi]        │   │
│   └─────────────────────────┘   │
│              │                   │
│              │ converged         │
└──────────────┼───────────────────┘
               │
               ▼
┌─────────────────────────────────┐
│       TileLODResult             │
│ • base_distance                 │
│ • multiplier (M)                │
│ • distances [base, base×M, ...] │
│ • predicted_splat_count         │
│ • converged (bool)              │
└─────────────────────────────────┘
```

---

## 4. 类图（UML）

```
┌──────────────────────────────────────────────┐
│         TileLODCalculatorV5                  │
├──────────────────────────────────────────────┤
│ - lod_dir: Path                              │
│ - target_count: int                          │
│ - target_min: int                            │
│ - target_max: int                            │
│ - lod_levels: int                            │
│ - tiles: List[Tuple[ndarray, Dict]]         │
│ - scene_center: ndarray                      │
│ - max_tile_distance: float                   │
│ - tile_density: float                        │
│ - total_splats: int                          │
│ - tile_count: int                            │
│ - max_iterations: int = 15                   │
├──────────────────────────────────────────────┤
│ + __init__(lod_dir, target_splat_count)     │
│ + compute(view_mode) → TileLODResult        │
│ - _load_tiles() → void                       │
│ - _tile_simulation(base, M) → int           │
│ - _predict(base, M, view_mode) → int        │
│ - _solve_base(view_mode) → float            │
└──────────────────────────────────────────────┘
                    │
                    │ returns
                    ▼
┌──────────────────────────────────────────────┐
│          TileLODResult                       │
├──────────────────────────────────────────────┤
│ + base_distance: float                       │
│ + multiplier: float                          │
│ + distances: List[float]                     │
│ + predicted_splat_count: int                 │
│ + iterations: int                            │
│ + converged: bool                            │
│ + tile_count: int                            │
│ + view_mode: str                             │
└──────────────────────────────────────────────┘
```

---

## 5. 核心算法伪代码

### 5.1 Tile Simulation（核心预测算法）

```python
function tile_simulation(base, M, tiles, levels, scene_center):
    # 计算各 LOD 层的距离阈值
    distances = [base × M^k for k in range(levels)]
    
    total_splats = 0
    
    # 遍历每个 tile
    for (tile_center, lod_counts) in tiles:
        # 计算 tile 到场景中心的距离
        dist = euclidean_distance(tile_center, scene_center)
        
        # 根据距离分配 LOD 层级
        lod = levels - 1  # 默认最粗层
        for k in range(levels):
            if dist <= distances[k]:
                lod = k
                break
        
        # 累加该 tile 在对应 LOD 层的 splat 数
        total_splats += lod_counts[lod]
    
    return total_splats
```

### 5.2 自适应 Visibility Factor

```python
function adaptive_visibility(view_mode, max_tile_distance, tile_density):
    if view_mode == "ground":
        # 地面：区分室内/室外
        if max_tile_distance < 100.0:
            return 1.0  # 室内/小场景（封闭空间，全部可见）
        else:
            return 0.11  # 大型室外（FOV=60°×45°，单方向）
    
    # 航拍：根据 tile 密度自适应
    if tile_density < 0.0008:
        # 极稀疏：几乎无遮挡
        return 1.0
    
    elif tile_density < 0.0015:
        # 稀疏 → 中等：线性插值
        t = (tile_density - 0.0008) / (0.0015 - 0.0008)
        return 1.0 - t × 0.6  # [1.0 → 0.4]
    
    elif tile_density < 0.003:
        # 中等 → 密集：线性插值
        t = (tile_density - 0.0015) / (0.003 - 0.0015)
        return 0.4 - t × 0.15  # [0.4 → 0.25]
    
    else:
        # 高密度：遮挡严重
        return 0.25
```

### 5.3 二分法求解 Base

```python
function solve_base(view_mode, target, tiles, max_dist, levels):
    base_lo = 1.0
    base_hi = min(max_dist, 500.0)  # 上界用 max_dist（室内场景需要 base≈max_dist）
    
    # 边界检查
    M_hi = compute_M(base_hi, max_dist, levels)
    pred_hi = predict(base_hi, M_hi, view_mode)
    if pred_hi < target × 0.8:
        return base_hi  # 上界预测还不够，返回上界
    
    M_lo = compute_M(base_lo, max_dist, levels)
    pred_lo = predict(base_lo, M_lo, view_mode)
    if pred_lo > target × 1.2:
        return base_lo  # 下界预测已过高，返回下界
    
    # 二分搜索
    for iter in range(15):
        base_mid = (base_lo + base_hi) / 2
        M = compute_M(base_mid, max_dist, levels)
        predicted = predict(base_mid, M, view_mode)
        
        # 收敛判断
        if target × 0.8 <= predicted <= target × 1.2:
            return base_mid
        
        # 调整区间
        if predicted < target:
            base_lo = base_mid  # 预测不足 → 增大 base
        else:
            base_hi = base_mid  # 预测过高 → 减小 base
    
    return (base_lo + base_hi) / 2

function compute_M(base, max_dist, levels):
    M = (max_dist / base) ^ (1 / (levels - 1))
    return clip(M, 1.5, 4.0)

function predict(base, M, view_mode):
    sim_total = tile_simulation(base, M, ...)
    visibility = adaptive_visibility(view_mode, tile_density)
    return sim_total × visibility
```

---

## 6. 决策树：Visibility Factor 选择

```
                      view_mode?
                     /          \
                  ground       aerial
                    │             │
                visibility=0.11   │
                                  │
                          tile_density?
                         /     |    |     \
                   <0.0008  0.0008 0.0015  >0.003
                      │      -0.0015 -0.003  │
                      │        │      │      │
                   vis=1.0  线性插值 线性插值 vis=0.25
                            1.0→0.4  0.4→0.25
                            
说明：
- 稀疏场景（tile 间距大）→ 遮挡少 → visibility 高
- 密集场景（tile 重叠多）→ 遮挡严重 → visibility 低
```

---

## 7. 性能特征

| 操作 | 时间复杂度 | 说明 |
|------|-----------|------|
| _load_tiles | O(n) | n = octree 节点数，遍历一次 |
| _tile_simulation | O(t × l) | t = tile 数，l = LOD 层数（通常 4-5） |
| _predict | O(t × l) | 调用 tile_simulation + O(1) |
| _solve_base | O(iter × t × l) | iter = 15（二分法迭代次数） |
| **总体** | **O(15 × 5000 × 5)** | ≈ **375,000** 次距离计算（~15ms） |

**优化空间**：
- 缓存 tile_simulation 中间结果（固定 tiles、scene_center）
- 并行化 tile 遍历（CPU 多核）
- GPU 加速（CUDA/WebGPU）

---

## 8. 与 V3 架构对比

| 维度 | V3 (面积近似) | V5 (Tile Simulation) |
|------|--------------|---------------------|
| **核心算法** | 解析公式（球壳体积 × 密度 × FOV） | 遍历 tile 模拟 LOD 分配 |
| **复杂度** | O(1) | O(t × l × iter) |
| **精度** | ±10-50x | ±20% |
| **可调试性** | 低（公式黑盒） | 高（逐 tile 可追踪） |
| **可扩展性** | 低（公式耦合严重） | 高（模块化清晰） |
| **维护成本** | 高（参数相互影响） | 低（visibility 独立调整） |

---

**[布偶猫/宪宪 claude-opus-4-6 🐾]**  
*2026-07-24*
