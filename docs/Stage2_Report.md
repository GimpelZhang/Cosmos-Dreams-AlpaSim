# Stage 2 报告：反事实长尾场景变体闭环仿真与可视化评测

日期：2026-10-02 运行，2026-10-03 修订。运行环境：双 A100-80GB（GPU0 driver，GPU1 OmniDreams），Alpamayo 1.5。
运行产物：`/mnt/artifacts/stage2/run_fixfull_20261002`，变体映射 `/mnt/artifacts/stage2/rollout_fixfull_map.csv`，
帧/BEV/HUD：`frames_fixfull` / `bev_fixfull` / `videos_fixfull`。

## 1. 范围与裁剪声明

Stage 2 基于 OmniDreams 论文（arXiv:2606.03159）§9.3 的反事实变体思路，在同一 base 场景
`clipgt-02eadd92-02f1-46d8-86fe-a9e338fed0b6` 上构建 1 个 baseline + 10 个变体（v00–v10），
每个闭环运行 20 s。本阶段实现并验证的能力是：**首帧+文本联合外观编辑（论文 §9.3.1 的实际做法）**
与**3D 合成交通对象轨迹注入**（hdmap 拓扑保持不变）。

明确裁剪（系统不具备、不在本阶段声称）：
- 天气/光照变体是**视觉-only**：只改变渲染外观，**物理未建模**——不改变路面摩擦、不改变传感器探测距离，
  不模拟打滑/积水的动力学效应；
- 不改变 traffic sim 的其他参与者行为逻辑；
- OOD 奇幻实体（T-Rex）仅作为 spike 探索项保留，不进入正式矩阵。

## 2. 首次运行失败、根因与修复（本报告相对前版的核心变化）

首次 11 变体运行（`run_stage2_20261002`）经人工审看判定失败，现象：
天气/光照在视频中完全不可见；BEV 中插入对象的红框总在 ego 驶过之后才出现。
受控诊断与代码分析确认两个根因，均已修复并经实测闭环验证：

| # | 根因 | 证据 | 修复 |
|---|---|---|---|
| 1 | 部署的蒸馏检查点（`2b_res720p_30fps_i2v_hdmap_distilled.pt`，2-step、guidance_scale=1.0 无 CFG）**不响应 prompt-only 外观编辑**；同帧同 seed 换 prompt 的 A/B 渲染结果一致 | `scripts/diag_stage2_prompt_ab.py`；NVIDIA 未发布任何支持 prompt-only 全局改外观的 OmniDreams 检查点 | 按论文 §9.3.1/Fig.10 的实际做法：用图像编辑模型（Doubao Seedream，凭据仅从 `~/access/image_editor_model.txt` 读取）**联合编辑渲染会话的首帧**，再由文本 prompt 把外观沿视频传播；编辑返回 2880×1584，按会话分辨率 1280×704 LANCZOS 缩放后回种 |
| 2 | 合成 actor 的坐标按**渲染起始时刻**的 ego 位姿变换，但其轨迹窗口在 activate_s（约 3 s）后才开启——对象被落在约 30 m 之后，ego 先驶过 | ASL 中 actor 首条位姿广播与激活时刻的位置关系 | 坐标语义改为：**在 actor 激活时刻**取 GT rig 位姿做 rig→clip-global 变换（x 前、y 左）；单测覆盖"10 m/s 直行、20 m 处、3 s 激活→世界坐标 50 m" |

涉及实现：`alpasim_runtime/stage2/frame_edit.py`（新增，stdlib HTTPS，无第三方依赖、不记录密钥）、
`alpasim_runtime/stage2/variant_actors.py`（激活时刻锚定）、`services/video_model_service.py`
（会话初始化时按变体决定是否编辑首帧）、wizard extras `stage2_env.yaml`（凭据文件只读挂载）。
全部改动经 `configs/local-patches/patches/alpasim-stage2-001-synthetic-scenarios.patch` 分发，
Stage 2 单测 21 项全部通过。

## 3. 论文 vs 实现对照

| 论文要点 | 实现情况 |
|---|---|
| §9.3.1 文本编辑天气/时段（作用于首帧条件） | 已实现：**首帧图像编辑 + per-rollout 文本 prompt 联合**（v01–v04；v10 的台风半部分同样编辑首帧），11 次独立串行会话 |
| §9.3.2 OOD 实体注入 | 已实现：合成 TrafficObject（AABB + 激活后全窗轨迹）合并链路；T-Rex spike 评分为 0，正式矩阵 v07 降级为施工挖掘机 |
| HDMap 拓扑一致 | 已保持：map 数据取自 ASL 内 hdmap zip，不依赖外部产物 |
| Camera + BEV 轨迹重叠可视化 | 已实现：30 fps HUD 增强版，BEV 含当帧预测轨迹黄线；红框在激活后、ego 之前出现 |

## 4. 11 变体结果总表（修复后运行，逐帧指标）

| ID | 类别 | 激活(s) | 首减速(s) | 碰撞首(s) | 碰撞帧 | 下路首(s) | 下路帧 | 轨迹偏差最大(m) | 末速(m/s) | 结论 |
|---|---|---|---|---|---|---|---|---|---|---|
| v00_baseline | baseline |  | 7.53 | 7.07 | 12 | 8.40 | 43 | 3.81 | 0.00 |  |
| v01_heavy_snow_blizzard | weather |  |  | 6.80 | 8 | 16.93 | 10 | 45.14 | 4.83 | FAIL |
| v02_torrential_rain_night | weather_lighting |  |  | 6.80 | 9 | 9.73 | 21 | 34.77 | 2.10 | FAIL |
| v03_dense_fog_dawn | weather |  |  | 6.80 | 13 | 10.00 | 29 | 24.60 | 1.84 | FAIL |
| v04_blinding_sunset_glare | lighting |  |  | 6.80 | 14 | 8.93 | 27 | 20.75 | 1.50 | FAIL |
| v05_stroller_jaywalking | vru_crossing | 3.0 |  | 5.20 | 21 | 10.00 | 37 | 4.78 | 0.03 | FAIL |
| v06_wheelchair_shoulder | rare_moving_agent | 2.5 | 9.93 | 7.07 | 24 | 8.67 | 16 | 6.64 | 0.02 | FAIL |
| v07_construction_excavator | large_road_obstacle | 3.5 | 7.00 | 7.07 | 48 | 7.33 | 47 | 5.38 | 0.00 | FAIL |
| v08_loose_cow | animal_incident | 3.0 | 11.80 | 5.73 | 28 | 9.20 | 13 | 7.56 | 0.02 | FAIL |
| v09_construction_debris | road_obstacle_group | 3.0 | 7.80 | 4.67 | 16 | 8.93 | 41 | 3.13 | 0.04 | FAIL |
| v10_typhoon_compound | compound_long_tail | 3.0 | 11.53 | 4.67 | 16 | 9.73 | 26 | 19.20 | 1.34 | FAIL |

碰撞/下路事件后策略轨迹往往发散，因此各变体的"轨迹偏差最大"受事件后帧影响较大，
不宜单独横向比较；事件时序（碰撞首时与激活时刻的关系）是更可信的对比维度。

## 5. 每变体事实描述

### v00_baseline
首次可观察减速 t=7.53 s，峰值减速度 19.72 m/s²。碰撞 12 帧，首帧 t=7.07 s
（前部 t=7.07 s，后部 t=7.60 s）。下路 43 帧，首帧 t=8.40 s。轨迹偏差最大 3.81 m，末速 0。

### v01_heavy_snow_blizzard
无可观察持续减速（峰值减速度 8.18 m/s²）。碰撞 8 帧，首帧 t=6.80 s。下路 10 帧，首帧 t=16.93 s。
0–8 s 速度变化 -0.85 m/s。轨迹偏差最大 45.14 m，末速 4.83 m/s。视觉确认：全程积雪路面、雪花、灰白天空。

### v02_torrential_rain_night
无可观察持续减速。碰撞 9 帧，首帧 t=6.80 s。下路 21 帧，首帧 t=9.73 s。
0–8 s 速度变化 -0.67 m/s。轨迹偏差最大 34.77 m，末速 2.10 m/s。视觉确认：深夜、雨幕、积水路灯反光。

### v03_dense_fog_dawn
无可观察持续减速。碰撞 13 帧，首帧 t=6.80 s。下路 29 帧，首帧 t=10.00 s。
0–8 s 速度变化 -1.08 m/s。轨迹偏差最大 24.60 m，末速 1.84 m/s。视觉确认：浓雾吞没建筑物、微弱黎明天光。

### v04_blinding_sunset_glare
无可观察持续减速。碰撞 14 帧，首帧 t=6.80 s。下路 27 帧，首帧 t=8.93 s。
0–8 s 速度变化 -1.13 m/s。轨迹偏差最大 20.75 m，末速 1.50 m/s。视觉确认：正前方低太阳、大片眩光、长阴影。

### v05_stroller_jaywalking
t=3.0 s 激活。无可观察持续减速。碰撞 21 帧，首帧 t=5.20 s（激活后 2.2 s，说明行人确实进入因果链）。
下路 37 帧，首帧 t=10.00 s。轨迹偏差最大 4.78 m，末速 0.03 m/s，派生最小 TTC 0 s。
视觉确认：推婴儿车行人在激活后出现在右前方并逐步逼近。

### v06_wheelchair_shoulder
t=2.5 s 激活。首次可观察减速 t=9.93 s。碰撞 24 帧，首帧 t=7.07 s。下路 16 帧，首帧 t=8.67 s。
轨迹偏差最大 6.64 m，末速 0.02 m/s，派生最小 TTC 0 s。

### v07_construction_excavator
t=3.5 s 激活。首次可观察减速 t=7.00 s，峰值减速度 22.80 m/s²。碰撞 48 帧，首帧 t=7.07 s。
下路 47 帧，首帧 t=7.33 s。轨迹偏差最大 5.38 m，末速 0，派生最小 TTC 0 s。

### v08_loose_cow
t=3.0 s 激活。首次可观察减速 t=11.80 s。碰撞 28 帧，首帧 t=5.73 s（激活后 2.7 s）。下路 13 帧，首帧 t=9.20 s。
轨迹偏差最大 7.56 m，末速 0.02 m/s，派生最小 TTC 0 s。
视觉确认：奶牛在激活后出现于车道中央（约 20 m 外），ego 约 6 s 时与其交汇后驶过。

### v09_construction_debris
t=3.0 s 激活，3 个注入对象（桶×2、板条箱）。首次可观察减速 t=7.80 s。碰撞 16 帧，首帧 t=4.67 s（激活后 1.7 s）。
下路 41 帧，首帧 t=8.93 s。轨迹偏差最大 3.13 m，末速 0.04 m/s，派生最小 TTC 0 s。

### v10_typhoon_compound
t=3.0 s 激活，2 个注入对象（倒地树枝、撑伞行人）；首帧同时编辑为台风暴雨。首次可观察减速 t=11.53 s。
碰撞 16 帧，首帧 t=4.67 s（激活后 1.7 s）。下路 26 帧，首帧 t=9.73 s。
轨迹偏差最大 19.20 m，末速 1.34 m/s，派生最小 TTC 0 s。
视觉确认：暗乌云、横向雨幕、风压弯树、积水反光、路边撑伞行人。**初版 v10 漏配 frame_edit_prompt
导致只有演员没有台风，已补配并重跑。**

## 6. 天气变体标注

v01–v04 与 v10 的风暴部分均为**视觉-only 条件，物理未建模**。速度变化是策略对画面的反应观察，
不反应不判 FAIL。修复后天气变体的碰撞首时集中在 t=6.80 s（v00 为 7.07 s），
仍是同一路段继承事件；天气确实改变的是渲染外观（积雪/雨幕/雾/眩光/台风）与事件后的帧计数、轨迹发散程度。
0–8 s 速度变化：v01 -0.85、v02 -0.67、v03 -1.08、v04 -1.13 m/s，均为轻微减速，
策略没有对恶化视距做出可信的安全响应。

## 7. OOD spike 结论

T-Rex（OTHER 6.0×2.5×5.0 box + prompt）逐帧 rubric 评分全为 0，正式矩阵 v07 按风险预案降级为
静止施工挖掘机（3.5×1.5×3.0）；T-Rex 仅保留 spike 记录。对照之下，cow box 注入评分为 1，
证明注入链路本身有效。详见 `/mnt/artifacts/stage2/spikes/spike_verdict.json`。

## 8. 通过率与结论

驾驶策略总通过率 = 0/10（通过率分母 = 11 - SYSTEM_ERROR - 1(baseline)）。

**模型表现 ≠ 系统正常**：11 个 rollout 完整、prompt/actor/seed 证据一致，天气/光照/台风视觉效果
与注入 actor 时序均经视频与 BEV 实测确认——即系统成功；驾驶策略对全部长尾场景（恶劣视距、
横穿行人、路中障碍物、动物）均未做出安全响应，通过率是另一回事。
所有结论均基于逐帧 metrics.parquet 与 HUD/原始视频，不引用 aggregate metrics_results.txt。

## 9. 复现命令

```bash
source "$HOME/simulation/scripts/env.sh"

# 11 次串行闭环（renderer 自动拉起/复用，已完成变体靠 _success 跳过）
MANIFEST=$HOME/simulation/configs/stage2/manifests/stage2_variants.csv \
RUN_TAG=fixfull_20261002 \
  sg docker -c 'bash scripts/run_stage2_separate.sh'

# 完整性 gate
STAGE2_RUN_DIR=/mnt/artifacts/stage2/run_fixfull_20261002 \
STAGE2_MAP_OUT=/mnt/artifacts/stage2/rollout_fixfull_map.csv \
  repos/alpasim/.venv/bin/python scripts/check_stage2_task33.py

# 导帧 + gate
MAP=/mnt/artifacts/stage2/rollout_fixfull_map.csv \
OUT_BASE=/mnt/artifacts/stage2/frames_fixfull \
  bash scripts/export_stage2_frames.sh
STAGE2_FRAMES_DIR=/mnt/artifacts/stage2/frames_fixfull \
STAGE2_MAP=/mnt/artifacts/stage2/rollout_fixfull_map.csv \
  repos/alpasim/.venv/bin/python scripts/check_stage2_task41.py

# BEV + gate
repos/alpasim/.venv/bin/python scripts/render_stage2_bev.py unused \
  --variants configs/stage2/variants.yaml \
  --map /mnt/artifacts/stage2/rollout_fixfull_map.csv \
  --out-dir /mnt/artifacts/stage2/bev_fixfull
STAGE2_BEV_DIR=/mnt/artifacts/stage2/bev_fixfull \
STAGE2_FRAMES_DIR=/mnt/artifacts/stage2/frames_fixfull \
STAGE2_MAP=/mnt/artifacts/stage2/rollout_fixfull_map.csv \
  repos/alpasim/.venv/bin/python scripts/check_stage2_task42.py

# HUD 视频 + gate
repos/alpasim/.venv/bin/python scripts/render_stage2_hud.py \
  --map /mnt/artifacts/stage2/rollout_fixfull_map.csv \
  --variants configs/stage2/variants.yaml \
  --bev-dir /mnt/artifacts/stage2/bev_fixfull \
  --tmp-dir /mnt/artifacts/stage2/hud_tmp_fixfull \
  --out-dir /mnt/artifacts/stage2/videos_fixfull
STAGE2_VIDEO_DIR=/mnt/artifacts/stage2/videos_fixfull \
STAGE2_MAP=/mnt/artifacts/stage2/rollout_fixfull_map.csv \
  repos/alpasim/.venv/bin/python scripts/check_stage2_task43.py

# 逐帧指标汇总
repos/alpasim/.venv/bin/python scripts/analyze_stage2.py \
  /mnt/artifacts/stage2/run_fixfull_20261002 \
  --variants configs/stage2/variants.yaml \
  --map /mnt/artifacts/stage2/rollout_fixfull_map.csv \
  --out-dir /mnt/artifacts/stage2/report_fixfull
```

## 10. 遗留开放问题

- 天气的物理效应（摩擦/传感器距离）未建模，需要交通/物理层扩展而非渲染层；
- `min_distance_to_obstacle_m` 计入路边静止停放车辆，正常超车经过时也会出现很小读数，
  HUD 的 STATE 灯因此可能在无真实威胁时进入 EMERGENCY；
- 注入对象的视觉一致性依赖渲染器对合成对象的泛化，cow/excavator/stroller/debris 已验证，
  更多类别需逐项 rubric 评分；
- 首帧编辑依赖外部图像编辑 API（每次会话增加约 100–110 s 延迟），离线/批量复现需要缓存编辑后的首帧；
- 联合编辑可能轻微改动画面前缘细节，已在编辑 prompt 中强约束几何不变，批量使用时仍应抽查首帧对齐。
