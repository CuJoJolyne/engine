# PlayCanvas 3DGS LOD 切换距离参数方案实现分析

## 概述

PlayCanvas 3DGS（3D Gaussian Splatting）采用了一套 **基于几何级数距离阈值的 LOD 切换方案**，通过几何级数保证远距离区域 splat 数量指数增长（符合透视投影的面积比），结合 FOV 感知缩放、背后相机惩罚、全局预算平衡等机制实现大场景高效渲染。

---

## 核心距离公式

`src/scene/gsplat-unified/gsplat-placement.js:317` 定义了几何级数 LOD 距离：

```javascript
getLodDistance(level) {
    return this.lodBaseDistance * Math.pow(this.lodMultiplier, level);
}
```

### 四级 LOD 距离阈值（默认 `lodBaseDistance=5`, `lodMultiplier=3`）

| LOD | 距离范围 |
|-----|----------|
| 0 (精细) | 0 ~ 5 |
| 1 | 5 ~ 15 |
| 2 | 15 ~ 45 |
| 3 (粗糙) | 45 ~ 135 |

等效数学表达：`optimalLodIndex = floor(1 + log(distance / lodBaseDistance) / log(lodMultiplier))`

---

## 参数体系

### 每实例参数（`GSplatComponent` / `GSplatPlacement`）

| 参数 | 默认值 | 约束 | 作用 |
|------|--------|------|------|
| `lodBaseDistance` | 5 | >= 0.1 | 首次 LOD 切换距离（相机到节点的局部空间距离） |
| `lodMultiplier` | 3 | >= 1.2 | 相邻 LOD 切换距离的几何比率 |
| `lodRangeMin` | 0 | [0, maxLod] | 允许的最小 LOD 索引（含），阻止加载精细层 |
| `lodRangeMax` | 99 | [rangeMin, maxLod] | 允许的最大 LOD 索引（含），实际受 `octree.lodLevels - 1` 限制 |
| `lodIndex` | 0 | - | 当前 placement 的 LOD 索引 |

文件：`src/framework/components/gsplat/component.js`、`src/scene/gsplat-unified/gsplat-placement.js`

### 全局场景参数（`GSplatParams`，挂载在 `app.scene.gsplat`）

| 参数 | 默认值 | 作用 |
|------|--------|------|
| `lodUpdateDistance` | 1 | 相机或 octree 实例位移超过多少世界单位触发 LOD 重评估 |
| `lodUpdateAngle` | 0 | 相机旋转角度阈值（度），0=禁用旋转触发 |
| `lodBehindPenalty` | 1 | 背后相机的距离惩罚因子（1=无惩罚，越高背后越粗糙） |
| `lodUnderfillLimit` | 0 | 最优 LOD 未加载时允许使用的更粗糙层数（0=必须等待最优） |
| `splatBudget` | 0 | 全场景 splat 数量预算（0=仅距离策略，>0 启用预算平衡） |
| `GSPLAT_DEBUG_LOD` | - | 调试：按 LOD 等级着色（红=0，绿=1，蓝=2，黄=3，品红=4+） |

文件：`src/scene/gsplat-unified/gsplat-params.js`

---

## 关键实现逻辑

### 1. 带 FOV 补偿的距离计算

文件：`src/scene/gsplat-unified/gsplat-octree-instance.js:528-536`

```javascript
const REF_TAN_HALF_FOV = Math.tan(22.5 * math.DEG_TO_RAD); // 参考 45° 垂直 FOV

let tanHalfVFov = Math.tan(camera.fov * 0.5 * math.DEG_TO_RAD);
if (camera.horizontalFov) {
    tanHalfVFov /= camera.aspectRatio;
}
const tanHalfHFov = tanHalfVFov * camera.aspectRatio;
const fovScale = Math.min(tanHalfVFov, tanHalfHFov) / REF_TAN_HALF_FOV;

// 有效距离 = penalizedDistance * fovScale
```

**作用**：不同焦距/宽高比相机下保持视觉一致性。更宽 FOV → 物体在屏幕上更小 → 更早切换到粗糙 LOD。

### 2. 相机背后惩罚

文件：`src/scene/gsplat-unified/gsplat-octree-instance.js:600-609`

```javascript
if (lodBehindPenalty > 1 && actualDistance > 0.01) {
    const dotOverDistance = (fwx * dx + fwy * dy + fwz * dz) / actualDistance;
    if (dotOverDistance < 0) {
        const t = -dotOverDistance; // 0=正前方, 1=正后方
        const factor = 1 + t * (lodBehindPenalty - 1);
        penalizedDistance = actualDistance * factor;
    }
}
```

**作用**：线性插值惩罚。正 behind 时有效距离 = 实际距离 × `lodBehindPenalty`，正前方 = 实际距离 × 1。

### 3. 距离计算（相机到节点 AABB 最近点）

文件：`src/scene/gsplat-unified/gsplat-octree-instance.js:571-595`

```javascript
// 计算相机位置到节点 AABB 最近点
let qx = clamp(px, minX, maxX);
let qy = clamp(py, minY, maxY);
let qz = clamp(pz, minZ, maxZ);

const dx = qx - px, dy = qy - py, dz = qz - pz;
const actualDistance = Math.sqrt(dx*dx + dy*dy + dz*dz);
```

### 4. 最优 LOD 索引选择

文件：`src/scene/gsplat-unified/gsplat-octree-instance.js:612-622`

```javascript
if (maxLod === 0 || fovAdjustedDistance < lodBaseDistance) {
    optimalLodIndex = 0;
} else {
    optimalLodIndex = maxLod;
    while (optimalLodIndex > 1 && fovAdjustedDistance < minDistBuf[optimalLodIndex]) {
        optimalLodIndex--;
    }
}
```

其中 `minDistBuf[k] = lodBaseDistance * lodMultiplier^(k-1)`，从粗糙到精细逐级比较（整数比较，避免 log 运算）。

### 5. LOD 更新触发机制

文件：`src/scene/gsplat-unified/gsplat-world.js:1014-1046`

LOD 在以下任一条件满足时触发重评估：

- 相机位移 > `lodUpdateDistance`
- 相机旋转 > `lodUpdateAngle`（度）
- 相机 FOV 变化 > 2%
- 任一 octree 实例位移 > `lodUpdateDistance`
- 任一实例有 `lodDirty` 标志或待加载资源
- 新增 octree 实例

节奏控制（metronome + back-pressure gate）：

```javascript
// 每 10 帧产生一次 LOD 更新请求，受排序 CPU 压力门控
if (--this._framesTillFullUpdate <= 0) {
    this._framesTillFullUpdate = 10;
    this._lodUpdateRequested = true;
}
if (this._lodUpdateRequested && allowLodUpdate) {
    fullUpdate = true;
    this._lodUpdateRequested = false;
}
```

### 6. Underfill 策略（LOD Fallback）

文件：`src/scene/gsplat-unified/gsplat-octree-instance.js:393-415`

```javascript
selectDesiredLodIndex(node, optimalLodIndex, maxLod, lodUnderfillLimit) {
    if (lodUnderfillLimit > 0) {
        const allowedMaxCoarseLod = Math.min(maxLod, optimalLodIndex + lodUnderfillLimit);
        // 优先选择 [optimal..optimal+underfill] 范围内已加载的最精细 LOD
        for (let lod = optimalLodIndex; lod <= allowedMaxCoarseLod; lod++) {
            if (resource is loaded) return lod;
        }
        // 兜底：选范围内最粗糙的可用 LOD
        for (let lod = allowedMaxCoarseLod; lod >= optimalLodIndex; lod--) {
            if (lod has valid fileIndex) return lod;
        }
    }
    return optimalLodIndex; // 默认：始终等待最优 LOD
}
```

预取加载（`prefetchNextLod()`）每帧向更精细方向递进一级，避免粗糙数据就位前混入精细请求。

### 7. 预算平衡器（`GSplatBudgetBalancer`）

文件：`src/scene/gsplat-unified/gsplat-budget-balancer.js`、`src/scene/gsplat-unified/gsplat-world.js:1134-1191`

启用 `splatBudget > 0` 时的三阶段流程：

**阶段 1：预算感知参数调整**

```javascript
const effectiveBase = lodBaseDistance * budgetScale;
const effectiveMult = Math.max(1.2, lodMultiplier * Math.pow(budgetScale, -0.2));
```

**阶段 2：自适应 budgetScale**

```javascript
if (ratio > 1 + deadZone || ratio < 1 - deadZone) {
    const invCorrection = 1 / Math.sqrt(ratio);
    this._budgetScale *= 1 + (invCorrection - 1) * blendRate;
    this._budgetScale = clamp(this._budgetScale, 0.01, 100.0);
}
```

**阶段 3：64 桶距离平衡**

```javascript
// 超预算：从最远桶（N-1）到最近桶（0）逐级降级 LOD
for (let b = NUM_BUCKETS - 1; b >= 0 && !done; b--) {
    nodeInfo.optimalLod = optimalLod + 1; // 降一级
}

// 欠预算：从最近桶（0）到最远桶（N-1）逐级升级 LOD
for (let b = 0; b < NUM_BUCKETS && !done; b++) {
    nodeInfo.optimalLod = optimalLod - 1; // 升一级
}
```

桶边界使用 `sqrt` 映射，保证近处桶分辨率更高。

---

## 预设配置示例

参考脚本 `scripts/esm/gsplat/streamed-gsplat.mjs` 中的预设：

| 画质 | `lodBaseDistance` | `lodMultiplier` | `splatBudget` | 说明 |
|------|-------------------|-----------------|----------------|------|
| Ultra | 8 | 2.5 | 0 | 仅距离策略，最近切换点 |
| High | 5 | 3 | 3000000 | 300 万 splat 预算 |
| Medium | 3 | 3 | 1500000 | 150 万 splat 预算 |
| Low | 2 | 3 | 800000 | 80 万 splat 预算 |

---

## 架构流程图

```
GSplatComponent (per-entity 参数)
    │ lodBaseDistance / lodMultiplier / lodRangeMin / lodRangeMax
    ▼
GSplatPlacement → getLodDistance(level)  ← 几何级数公式
    │
    ▼
GSplatOctreeInstance.evaluateNodeLods()
    │ ├─ 计算相机到节点 AABB 最近点距离
    │ ├─ FOV 补偿（tan-half-fov 比例缩放）
    │ ├─ 相机背后惩罚（线性插值）
    │ └─ 扫描距离阈值缓冲区选择 optimalLodIndex
    │
    ▼
selectDesiredLodIndex()  ← underfill 策略
    │ 选择已加载的最佳可用 LOD，或等待最优
    │
    ▼
GSplatBudgetBalancer（可选）
    │ ├─ 64 个 sqrt 映射距离桶
    │ ├─ 超预算：远 → 近降级
    │ └─ 欠预算：近 → 远升级
    │
    ▼
Streaming / Rendering
    ├─ 加载/卸载 LOD 文件（带 cooldown）
    └─ Placement 创建/销毁
```

---

## 设计特点总结

1. **几何级数距离阈值**：保证远距离区域 splat 数量指数增长，符合透视投影的面积比例自然法则。
2. **FOV 感知缩放**：不同焦距/宽高比相机下保持视觉一致性，避免镜头缩放导致的 LOD 跳动。
3. **背后相机惩罚**：优化视锥外渲染开销，线性插值避免突变。
4. **10 帧节奏 + CPU 背压门控**：避免每帧全场景 LOD 重评估，与排序阶段解耦。
5. **Underfill 策略**：平衡加载延迟与画面质量，支持渐进式细化。
6. **预算平衡系统**：64 距离桶全局优化，在固定硬件预算下保证近处优先质量。
7. **AABB 最近点距离**：比中心距离更准确，大节点跨距离带时避免突然切换。

---

## 相关文件索引

| 分类 | 文件 | 作用 |
|------|------|------|
| 核心公式 | `src/scene/gsplat-unified/gsplat-placement.js` | `getLodDistance()`、参数定义 |
| LOD 评估 | `src/scene/gsplat-unified/gsplat-octree-instance.js` | `evaluateNodeLods()`、`selectDesiredLodIndex()` |
| 全局参数 | `src/scene/gsplat-unified/gsplat-params.js` | `lodUpdateDistance`、`lodBehindPenalty` 等 |
| Octree 数据 | `src/scene/gsplat-unified/gsplat-octree.js` | `lodLevels`、LOD 解析 |
| 节点数据 | `src/scene/gsplat-unified/gsplat-octree-node.js` | 每节点 LOD 数组（file/offset/count） |
| 预算平衡 | `src/scene/gsplat-unified/gsplat-budget-balancer.js` | 64 桶平衡算法 |
| 更新触发 | `src/scene/gsplat-unified/gsplat-world.js` | `testCameraMovedForLod()`、`_enforceBudget()` |
| 编排 | `src/scene/gsplat-unified/gsplat-manager.js` | streaming tick、LOD 事件 |
| 多相机 | `src/scene/gsplat-unified/gsplat-director.js` | 按相机/层的 manager 分发 |
| 常量 | `src/scene/gsplat-unified/constants.js` | `NUM_BUCKETS = 64` |
| 组件 API | `src/framework/components/gsplat/component.js` | 公开 LOD 参数 |
| 解析器 | `src/framework/parsers/gsplat-octree.js` | `lod-meta.json` 解析 |
| Shader | `src/scene/gsplat/gsplat-resource-base.js` | `GSPLAT_LOD` shader define |
| 示例脚本 | `scripts/esm/gsplat/streamed-gsplat.mjs` | 预设参考 |
| 示例 | `examples/src/examples/gaussian-splatting/lod-instances.example.mjs` | LOD 实例示例 |
| 示例 | `examples/src/examples/gaussian-splatting/lod-streaming.example.mjs` | LOD 流式示例 |
