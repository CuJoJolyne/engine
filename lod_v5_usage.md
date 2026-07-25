# LOD 参数计算器 V5 使用手册

> **版本**: V5 (Tile Simulation + Adaptive Visibility)  
> **作者**: 布偶猫/宪宪 claude-opus-4-6 🐾  
> **日期**: 2026-07-25

---

## 快速开始

### 基础用法

```python
from lod_tile_calculator_v5 import TileLODCalculatorV5

# 初始化（指向包含 lod-meta.json 的目录）
calc = TileLODCalculatorV5(
    lod_dir=r'C:\Doc\AGP\LoD\syp_lod_0610',
    target_splat_count=1_000_000  # 目标 GPU 预算（默认 1M）
)

# 计算参数
result = calc.compute(view_mode="ground")

# 查看结果
print(f"Base distance: {result.base_distance:.1f}m")
print(f"Multiplier (M): {result.multiplier:.2f}")
print(f"LOD distances: {result.distances}")
print(f"Predicted splat count: {result.predicted_splat_count:,}")
print(f"Converged: {result.converged}")
```

---

## 批量测试

运行 `test_v5_final.py` 测试所有场景：

```bash
cd C:\Source\3DGS\playcanvas\engine
python test_v5_final.py
```

**输出示例**：

```
Scene              Mode     base(m)      M  Distances                   Predicted Conv
---------------------------------------------------------------------------------------
xiaohuizhou        ground       18.8   1.50 [19,28,42]                    831,053 Y
adongge            ground       21.4   1.50 [21,32,48]                    907,519 Y
syp_0610_full      ground        8.8   3.32 [9,29,97,322,1069]         1,061,743 Y
nys_aerial         aerial       47.8   2.26 [48,108,245,553]              888,611 Y
...
Overall Convergence: 12/12 (100%)
```

---

## API 参考

### TileLODCalculatorV5

#### 构造函数

```python
TileLODCalculatorV5(lod_dir: str, target_splat_count: int = 1_000_000)
```

**参数**：
- `lod_dir` (str): LOD 数据目录路径，必须包含 `lod-meta.json`
- `target_splat_count` (int): 目标渲染 splat 数，默认 1M

**示例**：
```python
calc = TileLODCalculatorV5(
    lod_dir=r'C:\Doc\AGP\LoD\nys_lod_0618',
    target_splat_count=1_200_000  # 自定义目标
)
```

#### compute 方法

```python
compute(view_mode: str = "ground") -> TileLODResult
```

**参数**：
- `view_mode` (str): 
  - `"ground"` — 地面漫游模式（FOV=60°×45°，单方向）
  - `"aerial"` — 航拍俯瞰模式（自适应 visibility）

**返回值**: `TileLODResult` 对象，包含：

| 字段 | 类型 | 说明 |
|------|------|------|
| `base_distance` | float | LOD0 切换半径（米） |
| `multiplier` | float | 几何级数倍率 M |
| `distances` | list[float] | 各 LOD 层距离 [base, base×M, base×M², ...] |
| `predicted_splat_count` | int | 预测渲染 splat 数 |
| `iterations` | int | 二分法迭代次数 |
| `converged` | bool | 是否收敛到目标区间 [0.8×target, 1.2×target] |
| `tile_count` | int | 场景 tile 总数 |
| `view_mode` | str | 使用的视角模式 |

---

## 使用场景

### 1. 地面漫游场景

```python
calc = TileLODCalculatorV5(r'C:\Doc\AGP\LoD\syp_lod_0610')
result = calc.compute(view_mode="ground")

# 应用到渲染器
renderer.set_lod_params(
    base=result.base_distance,
    multiplier=result.multiplier
)
```

**适用**：第一人称/第三人称地面漫游、室内场景

**特性**：
- 室内/小场景（max_dist < 100m）：visibility = 1.0（封闭空间全部可见）
- 大型室外：visibility = 0.11（FOV 限制 + 遮挡）

### 2. 航拍场景

```python
calc = TileLODCalculatorV5(r'C:\Doc\AGP\LoD\nys_lod_0618')
result = calc.compute(view_mode="aerial")

print(f"Aerial base: {result.base_distance:.1f}m")
# 预期输出: Aerial base: 47.8m（目标 ~50m）
```

**适用**：无人机航拍、俯视地图、上帝视角

**特性**：
- 自适应 visibility（基于 tile 密度）
- 稀疏场景（< 0.0008 tiles/m²）：visibility = 1.0
- 密集场景（> 0.003 tiles/m²）：visibility = 0.25

---

## 收敛判断

计算器使用二分法求解 base，目标区间为 **[0.8M, 1.2M]**（±20%）。

**收敛标准**：
```python
result.converged == True  # 预测在目标区间内
result.predicted_splat_count  # 在 [800k, 1.2M] 范围内
```

**未收敛处理**：
- 如果 `result.converged == False`，检查：
  - 场景是否过小（total_splats < 1M）
  - 场景是否过大（max_tile_distance > 1000m）
- 可以调整 `target_splat_count` 再次计算

---

## 高级用法

### 自定义目标预算

```python
# 针对不同 GPU 调整目标
calc_low = TileLODCalculatorV5(lod_dir, target_splat_count=500_000)   # 低端 GPU
calc_high = TileLODCalculatorV5(lod_dir, target_splat_count=2_000_000) # 高端 GPU

result_low = calc_low.compute("ground")
result_high = calc_high.compute("ground")

print(f"Low-end base: {result_low.base_distance:.1f}m")
print(f"High-end base: {result_high.base_distance:.1f}m")
```

### 批量场景处理

```python
scenes = [
    (r'C:\Doc\AGP\LoD\scene1', 'Scene 1', 'ground'),
    (r'C:\Doc\AGP\LoD\scene2', 'Scene 2', 'aerial'),
]

for path, name, mode in scenes:
    calc = TileLODCalculatorV5(path)
    result = calc.compute(view_mode=mode)
    
    print(f"{name}: base={result.base_distance:.1f}m, "
          f"pred={result.predicted_splat_count:,}, "
          f"converged={result.converged}")
```

---

## 数据要求

### lod-meta.json 格式

计算器需要标准的 SOG LOD 元数据文件：

```json
{
  "lodLevels": 5,
  "tree": {
    "bound": {"min": [...], "max": [...]},
    "lods": {
      "0": {"count": 100000},
      "1": {"count": 43000},
      ...
    },
    "children": [...]
  }
}
```

**必须字段**：
- `lodLevels`: LOD 层数（通常 3-5）
- `tree.lods`: 每个 LOD 层的 splat 数
- `tree.bound`: Tile 包围盒（用于计算距离）

---

## 性能特征

| 操作 | 时间复杂度 | 典型耗时 |
|------|-----------|---------|
| 加载 tile 数据 | O(n) | ~5ms |
| 单次 tile simulation | O(tiles × levels) | ~1ms |
| 完整计算（15 次迭代） | O(15 × tiles × levels) | ~15ms |

**示例**（5120 tiles, 5 LOD levels）：
- 总计算次数：15 × 5120 × 5 = 384,000 次距离计算
- 实测耗时：~15ms（Python）

---

## 故障排查

### 问题：计算器加载失败

```
FileNotFoundError: lod-meta.json not found
```

**解决**：确认 `lod_dir` 路径正确且包含 `lod-meta.json` 文件。

### 问题：预测偏差过大

```
result.predicted_splat_count = 5,000,000  # 远超 1M
result.converged = False
```

**可能原因**：
- 场景过于密集（total_splats > 100M）
- tile_density 过高（> 0.01 tiles/m²）

**解决**：增大 `target_splat_count` 或检查场景数据。

### 问题：base 值异常

```
result.base_distance = 500.0  # 达到上界
```

**原因**：场景过小或过稀疏，solver 无法收敛。

**解决**：
- 检查 `max_tile_distance`（过小 < 50m？）
- 检查 `total_splats`（过少 < 500k？）

---

## 技术参考

完整技术文档：
- `lod_v5_final_report.md` — 最终报告（包含测试结果）
- `lod_v5_architecture.md` — 架构设计（含 UML 类图、伪代码）
- `lod_v5_flowcharts.md` — 流程图（Mermaid 格式）

---

**[布偶猫/宪宪 claude-opus-4-6 🐾]**  
*2026-07-25*
