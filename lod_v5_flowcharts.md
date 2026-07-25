# LOD V5 可视化流程图

> 使用 Mermaid 语法，可在 GitHub/支持 Mermaid 的 Markdown 渲染器中查看

---

## 1. 主流程图

```mermaid
flowchart TD
    Start([开始: compute]) --> LoadTiles[加载 Tile 数据<br/>_load_tiles]
    LoadTiles --> ParseJSON[解析 lod-meta.json<br/>遍历 octree 叶节点]
    ParseJSON --> ExtractTiles[提取 tiles<br/>center + lod_counts]
    
    ExtractTiles --> CalcStats[计算场景统计<br/>scene_center<br/>max_tile_distance<br/>tile_density]
    
    CalcStats --> SolvBase[求解最优 base<br/>_solve_base]
    
    SolvBase --> BinaryLoop{二分法循环<br/>iter < 15?}
    
    BinaryLoop -->|是| CalcMid[base_mid = base_lo + base_hi / 2]
    CalcMid --> CalcM[计算 M<br/>M = max_dist / base ^ 1/levels-1<br/>clip M to 1.5-4.0]
    CalcM --> Predict[预测 splat 数<br/>_predict]
    
    Predict --> CheckConv{收敛判断<br/>0.8M ≤ pred ≤ 1.2M?}
    CheckConv -->|是| ReturnBase[返回 base_mid]
    CheckConv -->|否| AdjustRange{pred < target?}
    
    AdjustRange -->|是| IncLo[base_lo = base_mid<br/>增大 base]
    AdjustRange -->|否| DecHi[base_hi = base_mid<br/>减小 base]
    
    IncLo --> BinaryLoop
    DecHi --> BinaryLoop
    
    BinaryLoop -->|否| FinalCalc[最终计算<br/>M, distances, predicted]
    ReturnBase --> FinalCalc
    
    FinalCalc --> ReturnResult[返回 TileLODResult]
    ReturnResult --> End([结束])
    
    style Start fill:#e1f5e1
    style End fill:#e1f5e1
    style Predict fill:#fff4e6
    style CheckConv fill:#ffe6e6
    style ReturnBase fill:#e6f3ff
```

---

## 2. 预测引擎详细流程

```mermaid
flowchart TD
    PredictStart([_predict<br/>base, M, view_mode]) --> TileSim[Tile Simulation<br/>_tile_simulation]
    
    TileSim --> CalcDist[计算 LOD 距离阈值<br/>distances = base × M^k]
    CalcDist --> InitTotal[total = 0]
    
    InitTotal --> LoopTiles{遍历所有 tile}
    
    LoopTiles -->|每个 tile| CalcTileDist[计算 tile 距离<br/>dist = ||center - scene_center||]
    CalcTileDist --> AssignLOD[分配 LOD 层级<br/>lod = argmin dist ≤ distances]
    AssignLOD --> AddSplats[累加 splat 数<br/>total += lod_counts lod]
    AddSplats --> LoopTiles
    
    LoopTiles -->|完成| SimTotal[sim_total = total]
    
    SimTotal --> CheckMode{view_mode?}
    
    CheckMode -->|ground| CheckIndoor{max_tile_distance?}
    
    CheckIndoor -->|< 100m| GroundIndoor[visibility = 1.0<br/>室内/小场景]
    CheckIndoor -->|≥ 100m| GroundOutdoor[visibility = 0.11<br/>大型室外]
    
    CheckMode -->|aerial| CheckDensity{tile_density?}
    
    CheckDensity -->|< 0.0008| VisSparse[visibility = 1.0<br/>极稀疏]
    CheckDensity -->|0.0008-0.0015| VisInterp1[线性插值<br/>1.0 → 0.4]
    CheckDensity -->|0.0015-0.003| VisInterp2[线性插值<br/>0.4 → 0.25]
    CheckDensity -->|> 0.003| VisDense[visibility = 0.25<br/>高密度]
    
    GroundIndoor --> Multiply[predicted = sim_total × visibility]
    GroundOutdoor --> Multiply
    VisSparse --> Multiply
    VisInterp1 --> Multiply
    VisInterp2 --> Multiply
    VisDense --> Multiply
    
    Multiply --> PredictEnd([返回 predicted])
    
    style PredictStart fill:#e1f5e1
    style PredictEnd fill:#e1f5e1
    style TileSim fill:#fff4e6
    style CheckMode fill:#ffe6e6
    style CheckDensity fill:#ffe6e6
    style Multiply fill:#e6f3ff
```

---

## 3. 数据流图

```mermaid
graph LR
    A[lod-meta.json] -->|parse| B[Tile List<br/>center + lod_counts]
    B -->|compute stats| C[Scene Stats<br/>center, max_dist, tile_density]
    C --> D[Binary Search]
    E[view_mode<br/>target_count] --> D
    
    D --> F[Trial: base_mid]
    F --> G[Calculate M]
    G --> H[Tile Simulation]
    B --> H
    H --> I[× Visibility Factor]
    C --> I
    E --> I
    I --> J[predicted_count]
    J --> K{Converged?}
    K -->|No| D
    K -->|Yes| L[TileLODResult<br/>base, M, distances, predicted]
    
    style A fill:#e1f5e1
    style L fill:#e6f3ff
    style H fill:#fff4e6
    style I fill:#fff4e6
```

---

## 4. 类关系图

```mermaid
classDiagram
    class TileLODCalculatorV5 {
        -Path lod_dir
        -int target_count
        -int lod_levels
        -List~Tuple~ tiles
        -ndarray scene_center
        -float max_tile_distance
        -float tile_density
        -int total_splats
        -int tile_count
        +__init__(lod_dir, target_splat_count)
        +compute(view_mode) TileLODResult
        -_load_tiles() void
        -_tile_simulation(base, M) int
        -_predict(base, M, view_mode) int
        -_solve_base(view_mode) float
    }
    
    class TileLODResult {
        +float base_distance
        +float multiplier
        +List~float~ distances
        +int predicted_splat_count
        +int iterations
        +bool converged
        +int tile_count
        +str view_mode
    }
    
    TileLODCalculatorV5 ..> TileLODResult : returns
```

---

## 5. 状态转换图（二分法收敛过程）

```mermaid
stateDiagram-v2
    [*] --> Initializing: 加载数据
    Initializing --> BoundaryCheck: 边界检查
    
    BoundaryCheck --> TooLow: pred_hi < target_min
    BoundaryCheck --> TooHigh: pred_lo > target_max
    BoundaryCheck --> Searching: 目标在区间内
    
    TooLow --> [*]: 返回 base_hi
    TooHigh --> [*]: 返回 base_lo
    
    Searching --> Predicting: base_mid = (lo+hi)/2
    Predicting --> Converged: 0.8M ≤ pred ≤ 1.2M
    Predicting --> TooLowPred: pred < 0.8M
    Predicting --> TooHighPred: pred > 1.2M
    
    TooLowPred --> Searching: base_lo = base_mid
    TooHighPred --> Searching: base_hi = base_mid
    
    Converged --> [*]: 返回 base_mid
    
    Searching --> MaxIter: iter ≥ 15
    MaxIter --> [*]: 返回 (lo+hi)/2
```

---

## 6. 时序图（完整调用链）

```mermaid
sequenceDiagram
    participant User
    participant Calculator as TileLODCalculatorV5
    participant Loader as _load_tiles
    participant Solver as _solve_base
    participant Predictor as _predict
    participant Simulator as _tile_simulation
    
    User->>Calculator: compute(view_mode="ground")
    activate Calculator
    
    Calculator->>Loader: 加载 tile 数据
    activate Loader
    Loader-->>Calculator: tiles, levels
    deactivate Loader
    
    Calculator->>Calculator: 计算场景统计<br/>(center, max_dist, tile_density)
    
    Calculator->>Solver: _solve_base(view_mode)
    activate Solver
    
    loop 二分法迭代 (最多15次)
        Solver->>Solver: base_mid = (lo + hi) / 2
        Solver->>Solver: 计算 M
        
        Solver->>Predictor: _predict(base_mid, M, view_mode)
        activate Predictor
        
        Predictor->>Simulator: _tile_simulation(base, M)
        activate Simulator
        
        loop 遍历所有 tile
            Simulator->>Simulator: 计算距离<br/>分配 LOD<br/>累加 splat
        end
        
        Simulator-->>Predictor: sim_total
        deactivate Simulator
        
        Predictor->>Predictor: 计算 visibility_factor<br/>(基于 view_mode + tile_density)
        Predictor->>Predictor: predicted = sim_total × visibility
        
        Predictor-->>Solver: predicted
        deactivate Predictor
        
        alt 收敛
            Solver-->>Calculator: base_mid
        else 未收敛
            Solver->>Solver: 调整 [lo, hi] 区间
        end
    end
    
    deactivate Solver
    
    Calculator->>Calculator: 计算最终参数<br/>(M, distances, predicted)
    Calculator-->>User: TileLODResult
    deactivate Calculator
```

---

## 7. 决策树：View Mode 分支

```mermaid
graph TD
    Root{view_mode?}
    
    Root -->|ground| Ground[Ground Mode]
    Root -->|aerial| Aerial[Aerial Mode]
    
    Ground --> IndoorCheck{max_tile_distance < 100m?}
    IndoorCheck -->|是| Indoor[室内/小场景<br/>visibility = 1.0<br/>封闭空间全部可见]
    IndoorCheck -->|否| OutdoorVis[大型室外<br/>visibility = 0.11<br/>FOV=60°×45°]
    
    Aerial --> DensCheck{tile_density?}
    
    DensCheck -->|< 0.0008| Sparse[极稀疏场景<br/>visibility = 1.0<br/>几乎无遮挡]
    DensCheck -->|0.0008-0.0015| MediumLow[稀疏→中等<br/>线性插值<br/>1.0 → 0.4]
    DensCheck -->|0.0015-0.003| MediumHigh[中等→密集<br/>线性插值<br/>0.4 → 0.25]
    DensCheck -->|> 0.003| Dense[高密度场景<br/>visibility = 0.25<br/>遮挡严重]
    
    Indoor --> Predict[predicted = sim × visibility]
    OutdoorVis --> Predict
    Sparse --> Predict
    MediumLow --> Predict
    MediumHigh --> Predict
    Dense --> Predict
    
    style Root fill:#ffe6e6
    style Ground fill:#e6f3ff
    style Aerial fill:#fff4e6
    style Predict fill:#e1f5e1
```

---

## 8. 部署架构图

```mermaid
graph TB
    subgraph Python原型
        A[lod_tile_calculator_v5.py]
        B[test_v5_final.py]
        C[lod_v5_final_report.md]
    end
    
    subgraph 数据
        D[lod-meta.json<br/>octree + tile stats]
    end
    
    subgraph JavaScript移植计划
        E[lodCalculator.js]
        F[tileSimulation.js]
        G[visibilityAdaptive.js]
    end
    
    subgraph 运行时集成
        H[渲染器<br/>Renderer]
        I[升降级策略<br/>LOD Upgrade/Downgrade]
        J[性能监控<br/>FPS Profiler]
    end
    
    D --> A
    A --> B
    A --> C
    
    A -.移植.-> E
    E --> F
    E --> G
    
    E --> H
    H <--> I
    I <--> J
    
    style A fill:#e6f3ff
    style E fill:#fff4e6
    style H fill:#e1f5e1
```

---

**[布偶猫/宪宪 claude-opus-4-6 🐾]**  
*2026-07-24*
