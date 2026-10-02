# Stage 2 报告：反事实长尾场景变体闭环仿真与可视化评测

日期：2026-10-02。运行环境：双 A100-80GB（GPU0 driver，GPU1 OmniDreams），Alpamayo 1.5。

## 1. 范围与裁剪声明

Stage 2 基于 OmniDreams 论文（arXiv:2606.03159）§9.3 的反事实变体思路，在同一 base 场景 `clipgt-02eadd92-02f1-46d8-86fe-a9e338fed0b6` 上构建 10 个变体并闭环运行 20 s。本阶段实现并验证的能力是：**per-rollout 文本 prompt 改写**与**3D 合成交通对象轨迹注入**（hdmap 拓扑保持不变）。

明确裁剪（系统不具备、不在本阶段声称）：
- 天气/光照变体是**视觉-only**：只改变渲染外观，**物理未建模**——不改变路面摩擦、不改变传感器探测距离，不模拟打滑/积水的动力学效应；
- 不改变 traffic sim 的其他参与者行为逻辑；
- OOD 奇幻实体（T-Rex）仅作为 spike 探索项保留，不进入正式矩阵。

## 2. 论文 vs 实现对照

| 论文要点 | 实现情况 |
|---|---|
| §9.3.1 prompt 驱动场景编辑 | 已实现：per-rollout positive/negative prompt，11 次独立串行 wizard 注入 |
| §9.3.2 OOD 实体注入 | 已实现：合成 TrafficObject（AABB + 全窗轨迹）合并链路；T-Rex spike 评分为 0，正式矩阵降级为施工挖掘机 |
| HDMap 拓扑一致 | 已保持：map 数据取自 ASL 内 hdmap zip，不依赖外部产物 |
| Camera + BEV 轨迹重叠可视化 | 已实现：30fps HUD 增强版，BEV 含当帧预测轨迹黄线 |

## 3. 11 变体结果总表

| ID | 类别 | 激活(s) | 首减速(s) | 碰撞首(s) | 碰撞帧 | 下路首(s) | 下路帧 | 激活后最近(m) | 轨迹偏差最大(m) | 末速(m/s) | 结论 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| v00_baseline | baseline |  | 7.53 | 7.07 | 12 | 8.40 | 43 | 0.00 | 3.81 | 0.00 |  |
| v01_heavy_snow_blizzard | weather |  |  | 7.07 | 7 |  | 0 | 0.00 | 41.47 | 4.40 | FAIL |
| v02_torrential_rain_night | weather_lighting |  | 7.53 | 6.80 | 10 | 7.87 | 9 | 0.00 | 7.25 | 0.05 | FAIL |
| v03_dense_fog_dawn | weather |  | 9.93 | 7.07 | 8 | 8.13 | 8 | 0.00 | 7.80 | 0.00 | FAIL |
| v04_blinding_sunset_glare | lighting |  | 9.67 | 7.07 | 11 | 8.13 | 44 | 0.00 | 4.72 | 0.00 | FAIL |
| v05_stroller_jaywalking | vru_crossing | 3.0 | 11.53 | 7.07 | 8 | 9.47 | 28 | 0.00 | 19.09 | 3.32 | FAIL |
| v06_wheelchair_shoulder | rare_moving_agent | 2.5 |  | 7.07 | 14 | 8.93 | 31 | 0.00 | 12.36 | 0.00 | FAIL |
| v07_construction_excavator | large_road_obstacle | 3.5 | 8.07 | 3.60 | 12 | 9.20 | 40 | 0.00 | 3.14 | 0.00 | FAIL |
| v08_loose_cow | animal_incident | 3.0 |  | 3.07 | 10 |  | 0 | 0.00 | 31.91 | 3.40 | FAIL |
| v09_construction_debris | road_obstacle_group | 3.0 | 7.80 | 6.80 | 11 | 8.40 | 43 | 0.00 | 4.00 | 0.01 | FAIL |
| v10_typhoon_compound | compound_long_tail | 3.0 | 6.47 | 7.07 | 12 | 8.40 | 43 | 0.00 | 5.03 | 0.00 | FAIL |

## 4. 每变体事实描述

### v00_baseline
First observable deceleration begins at t=7.53s, peak deceleration 19.72 m/s^2. Collision spans 12 frames, first at t=7.07s (front from t=7.07s, rear from t=7.60s). Offroad spans 43 frames, first at t=8.40s. Maximum trajectory deviation is 3.81 m, final speed 0.00 m/s.

### v01_heavy_snow_blizzard
No sustained deceleration is observed. Collision spans 7 frames, first at t=7.07s (front from t=7.07s, rear from t=7.60s, lateral from t=8.40s). No offroad is recorded. Maximum trajectory deviation is 41.47 m, final speed 4.40 m/s.

### v02_torrential_rain_night
First observable deceleration begins at t=7.53s, peak deceleration 18.37 m/s^2. Collision spans 10 frames, first at t=6.80s (front from t=6.80s, rear from t=7.60s). Offroad spans 9 frames, first at t=7.87s. Maximum trajectory deviation is 7.25 m, final speed 0.05 m/s.

### v03_dense_fog_dawn
First observable deceleration begins at t=9.93s, peak deceleration 17.49 m/s^2. Collision spans 8 frames, first at t=7.07s (front from t=7.07s, rear from t=7.60s). Offroad spans 8 frames, first at t=8.13s. Maximum trajectory deviation is 7.80 m, final speed 0.00 m/s.

### v04_blinding_sunset_glare
First observable deceleration begins at t=9.67s, peak deceleration 15.69 m/s^2. Collision spans 11 frames, first at t=7.07s (front from t=7.07s, rear from t=7.87s). Offroad spans 44 frames, first at t=8.13s. Maximum trajectory deviation is 4.72 m, final speed 0.00 m/s.

### v05_stroller_jaywalking
Injected content activates at t=3.0s. First observable deceleration begins at t=11.53s, peak deceleration 15.42 m/s^2. Collision spans 8 frames, first at t=7.07s (front from t=7.07s, rear from t=7.60s). Offroad spans 28 frames, first at t=9.47s. Post-activation minimum obstacle distance is 0.00 m. Maximum trajectory deviation is 19.09 m, final speed 3.32 m/s. Derived minimum TTC is 0.00 s (derived, not a native metric).

### v06_wheelchair_shoulder
Injected content activates at t=2.5s. No sustained deceleration is observed. Collision spans 14 frames, first at t=7.07s (front from t=7.07s, rear from t=7.60s, lateral from t=14.27s). Offroad spans 31 frames, first at t=8.93s. Post-activation minimum obstacle distance is 0.00 m. Maximum trajectory deviation is 12.36 m, final speed 0.00 m/s. Derived minimum TTC is 0.00 s (derived, not a native metric).

### v07_construction_excavator
Injected content activates at t=3.5s. First observable deceleration begins at t=8.07s, peak deceleration 25.39 m/s^2. Collision spans 12 frames, first at t=3.60s (front from t=7.07s, rear from t=3.60s). Offroad spans 40 frames, first at t=9.20s. Post-activation minimum obstacle distance is 0.00 m. Maximum trajectory deviation is 3.14 m, final speed 0.00 m/s. Derived minimum TTC is 0.00 s (derived, not a native metric).

### v08_loose_cow
Injected content activates at t=3.0s. No sustained deceleration is observed. Collision spans 10 frames, first at t=3.07s (front from t=7.07s, rear from t=3.07s, lateral from t=8.67s). No offroad is recorded. Post-activation minimum obstacle distance is 0.00 m. Maximum trajectory deviation is 31.91 m, final speed 3.40 m/s. Derived minimum TTC is 0.00 s (derived, not a native metric).

### v09_construction_debris
Injected content activates at t=3.0s. First observable deceleration begins at t=7.80s, peak deceleration 18.05 m/s^2. Collision spans 11 frames, first at t=6.80s (front from t=6.80s, rear from t=7.60s). Offroad spans 43 frames, first at t=8.40s. Post-activation minimum obstacle distance is 0.00 m. Maximum trajectory deviation is 4.00 m, final speed 0.01 m/s. Derived minimum TTC is 0.00 s (derived, not a native metric).

### v10_typhoon_compound
Injected content activates at t=3.0s. First observable deceleration begins at t=6.47s, peak deceleration 16.94 m/s^2. Collision spans 12 frames, first at t=7.07s (front from t=7.07s, rear from t=7.87s). Offroad spans 43 frames, first at t=8.40s. Post-activation minimum obstacle distance is 0.00 m. Maximum trajectory deviation is 5.03 m, final speed 0.00 m/s. Derived minimum TTC is 0.00 s (derived, not a native metric).

## 5. 天气变体标注

v01–v04 均为**视觉-only 条件，物理未建模**。速度变化是策略对画面的反应观察，不反应不判 FAIL。天气变体的碰撞首时（6.8–7.07s）与 v00（7.07s）基本重合，属于继承自 baseline 的同一碰撞事件而非天气新诱发；天气改变的是渲染外观与事件后的帧计数。
- v01_heavy_snow_blizzard：0–8s 速度变化 -0.72 m/s。
- v02_torrential_rain_night：0–8s 速度变化 -1.59 m/s。
- v03_dense_fog_dawn：0–8s 速度变化 -1.17 m/s。
- v04_blinding_sunset_glare：0–8s 速度变化 -2.18 m/s。

## 6. OOD spike 结论

T-Rex（OTHER 6.0×2.5×5.0 box + prompt）逐帧 rubric 评分全为 0，正式矩阵 v07 按风险预案降级为静止施工挖掘机（3.5×1.5×3.0）；T-Rex 仅保留 spike 记录。对照之下，cow box 注入评分为 1，证明注入链路本身有效。
详见 `/mnt/artifacts/stage2/spikes/spike_verdict.json`。

## 7. 通过率与结论

总通过率 = 0/10（通过率分母 = 11 - SYSTEM_ERROR - 1(baseline)）。

**模型表现 ≠ 系统正常**：11 个 rollout 完整、prompt/actor/seed 证据一致，即系统成功；驾驶策略通过率是另一回事。所有结论均基于逐帧 metrics.parquet 与 HUD/原始视频，不引用 aggregate metrics_results.txt。

## 8. 复现命令

```bash
source "$HOME/simulation/scripts/env.sh"
sg docker -c 'bash scripts/run_stage2_separate.sh'
repos/alpasim/.venv/bin/python scripts/check_stage2_task33.py
bash scripts/export_stage2_frames.sh
repos/alpasim/.venv/bin/python scripts/check_stage2_task41.py
repos/alpasim/.venv/bin/python scripts/render_stage2_bev.py unused \
  --variants configs/stage2/variants.yaml \
  --map /mnt/artifacts/stage2/rollout_variant_map.csv
repos/alpasim/.venv/bin/python scripts/check_stage2_task42.py
repos/alpasim/.venv/bin/python scripts/render_stage2_hud.py \
  --map /mnt/artifacts/stage2/rollout_variant_map.csv \
  --variants configs/stage2/variants.yaml
repos/alpasim/.venv/bin/python scripts/check_stage2_task43.py
repos/alpasim/.venv/bin/python scripts/analyze_stage2.py \
  /mnt/artifacts/stage2/run_stage2_20261002 \
  --variants configs/stage2/variants.yaml --out-dir /mnt/artifacts/stage2/report
```

## 9. 遗留开放问题

- 天气的物理效应（摩擦/传感器距离）未建模，需要交通/物理层扩展而非渲染层；
- `min_distance_to_obstacle_m` 计入路边静止停放车辆，正常超车经过时也会出现很小读数，HUD 的 STATE 灯因此可能在无真实威胁时进入 EMERGENCY；
- 注入对象的视觉一致性依赖渲染器对合成对象的泛化，cow/excavator 已验证，更多类别需逐项 rubric 评分。
