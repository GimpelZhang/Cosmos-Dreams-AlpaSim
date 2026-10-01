# Stage1 批量闭环报告：Alpamayo 1.5 × 30 场景 × OmniDreams

> 日期：2026-10-01 ｜ 运行名：`a15_batch30_20261001`
> 产物：`artifacts/run_a15_batch30_20261001/`（实体在 `/mnt/artifacts/`）
> 逐 clip 汇总：`artifacts/run_a15_batch30_20261001/batch_summary.csv`

## 1. 目标

在单 clip 闭环（§7.1 R1、§7.2 A15）验证链路之后，用一批**此前未用过的**场景
检验 Alpamayo 1.5 + OmniDreams 闭环在多样场景下的真实表现，并建立可复用的
批量运行/分析流程。

## 2. 场景选样

- 来源：AlpaSim 自带目录 `repos/alpasim/data/scenes/sim_scenes.csv`（HF dataset
  `nvidia/PhysicalAI-Autonomous-Vehicles-NuRec`）。
- 清单：`scripts/a15_batch_scenes_20261001.csv`，**30 个 scene_id**，全部为
  `hf_revision=26.01`、artifact 版本 `26.1.112-59673f88`。
- 抽样方法：在"仅存在于 26.01"的 756 个 scene_id 上按字典序排序后**等距抽样**
  （步长 25，取每段中点），保证地理/场景分布分散且可复现。
- **关键坑（见 §6 教训 1）**：目录中有部分 scene_id 同时挂着 26.01 和 26.04 两个
  artifact；wizard 对每个 scene_id **强制选用最新版本**（日志："The newest
  artifact per scene will be used"）。初版清单因此有 9 个 clip 实际会以 26.04
  运行；已重新生成清单，排除所有在 26.04 中出现的 scene_id，保证整批 artifact
  版本同质。此前所有单 clip 运行使用的 clipgt-02eadd92 也已排除。

## 3. 执行架构

- GPU0：Alpamayo 1.5 driver 容器（CR2 VLM 走 HF 离线缓存，见 §7.2 的 401 修复）；
  GPU1：OmniDreams gRPC 渲染服务（`scripts/start_renderer.sh`，704p，jpeg q90）。
- **一次 wizard 调用服务全部 30 clip**：compose 项目内 driver 模型只加载一次，
  runtime 对每个 clip 顺序起一个 video-model session（~75 帧 @3.75fps，20s）。
- 入口脚本：`scripts/run_batch_a15.sh`
  （区别于单 clip 的 `scripts/run_closed_loop_a15.sh`）：
  - 从清单 CSV 构造 Hydra `scenes.scene_ids=[...]` 列表字面量；
  - log 目录放 `/mnt/artifacts/run_a15_batch30_20261001`，repo 内符号链接；
  - 50051 端口未监听时自动后台启动 renderer 并等待 `Server started successfully`；
  - 无 docker socket 权限时自动 `sg docker -c` 重入；
  - `eval.allow_aggregation_with_failed_rollouts=true`：单个 clip 失败不阻止
    其余 clip 出 aggregate；`wizard.timeout=10800`。
  - **强制串行**：外部 OmniDreams renderer 只保留一个活动 session（每次
    start_session 会先 "Cleaning up N existing session(s)" 关闭旧的），而
    `topology=1gpu` 默认每端点 4 并发，并发 clip 会因 "Session not found"
    失败。脚本覆盖 `runtime.nr_workers=1` 及全部五个端点的
    `n_concurrent_rollouts=1`，30 个 clip 严格顺序执行（已用 run_method=NONE
    验证覆盖生效）。
- **场景预检**：约 40% 的 26.01 USDZ 不含 `clipgt/calibration_estimate.parquet`
  （video-model session 的必需文件），这些 clip 会以
  "...calibration_estimate.parquet not found in ...usdz" 失败。等距抽样的 30 个
  中仅 18 个可用；处理方式是预下载额外候选、按 zip 内容筛查后补足 30 个。

## 4. 分析方法（不看 aggregate 表）

沿用 §7.1.1/§7.2 的教训：aggregate `metrics_results.txt` 带
`RemoveTimestepsAfterEvent(...)` 截断，碰撞/offroad 可能落在计分窗口外。
分析脚本 `scripts/analyze_batch_a15.py` 直接遍历每个 rollout 的
`rollouts/<clip>/<rollout>/metrics.parquet`（逐帧 long format），输出：

- collision_any / front / lateral / rear、offroad、wrong_lane、
  safety_monitor_triggered、open_loop_collision 的**帧数与首末时刻**；
- progress、dist_traveled、GT 行驶距离、dist_to_gt_trajectory/location、
  最小障碍/车道边界距离、plan_deviation、各档 minADE；
- 综合 outcome：clean / offroad / collision / collision+offroad；
- 清单中缺失的 clip 标 `status=missing`，不会被静默吞掉。

## 5. 结果

30 个 clip 全部跑通（30 sessions / 30 rollouts / 30 metrics.parquet，零
pipeline 失败）。每个 rollout 平均 75.2 帧（仅一个 clip 81 帧）、平均时长
19.86s。

### 5.1 Outcome 分布（逐帧真值，非 aggregate）

| Outcome | clip 数 | 占比 |
|---|---|---|
| clean | 3 | 10.0% |
| collision（仅碰撞） | 6 | 20.0% |
| collision + offroad | 5 | 16.7% |
| offroad（仅偏离路面） | 16 | 53.3% |

- **碰撞**（collision_any）：11 个 clip、共 163 帧；其中前部碰撞 11 clip、
  侧向 3 clip、后向 9 clip（同一次碰撞可在不同帧先后触发多个部位）。
- **出路面**（offroad）：21 个 clip、共 342 帧。
- **逆行/错车道**（wrong_lane）：16 个 clip；**开环 3s 预测碰撞**
  （open_loop_collision）：17 个 clip。
- **safety_monitor_triggered：0 clip / 0 帧**——整个批次中安全层从未介入。

3 个 clean clip：

- `clipgt-3d343ae9-6d5b-415f-be2c-aa8d0ebc03c9`（progress 0.785）
- `clipgt-46252225-453d-474c-963c-bc0cd917e7e4`（progress 1.0）
- `clipgt-5a38c811-1e28-4a67-9239-54ba2b695950`（progress 0.992）

### 5.2 事件发生时刻

- 首次碰撞时刻（s，11 个 clip）：5.42 / 6.75 / 6.78 / 8.04 / 8.37 / 11.57 /
  11.86 / 12.62 / 15.26 / 16.32 / 17.89。多数碰撞发生在 13s 之前。
- 首次 offroad 时刻分布在 4.38–18.73s；最早 4.38s 即下路，说明部分场景
  一开始就无法跟随。
- 注意 `collision+offroad` 的 clip 中 offroad 常常是碰撞后的结果（例如
  clipgt-095cf563：8.37s 前碰，13.17s 才 offroad），归因时需看先后。

### 5.3 进度、里程与轨迹偏差

| 指标 | 均值 | 中位 | 最小 | 最大 |
|---|---|---|---|---|
| progress（完成 GT 路线比例） | 0.80 | 0.92 | 0.32 | 1.00 |
| ego 行驶距离 (m) | 158.83 | 138.52 | 55.43 | 345.26 |
| GT 行驶距离 (m) | 190.81 | 192.27 | 33.98 | 338.73 |
| dist_to_gt_trajectory (m) | 20.06 | 14.35 | 2.17 | 79.96 |
| dist_to_gt_location (m) | 60.63 | 47.46 | 2.17 | 207.54 |

- ego 里程平均为 GT 的 83%，但 progress 中位 0.92——不少 clip 是"开到了
  路线终点附近但没走在路上"。
- **offroad 是"车体是否在路面区域内"的二值标志，与轨迹偏差是两回事**：
  clean clip 也可能有很大的 dist_to_gt（最大 21.77m），需要同时看两个量。

### 5.4 minADE

| horizon | 均值 (m) | 最优 (m) |
|---|---|---|
| 0.5s | 21.74 | 1.34 |
| 1.0s | 21.52 | 1.35 |
| 2.5s | 20.98 | 1.39 |
| 5.0s | 全空（该配置未输出） | — |

minADE 均值非常大，反映多数 clip 在 20s 窗口内已经严重偏离 GT；表现好的
clip 各 horizon 都在 ~1.4m。分布两极分化（25 分位 11.3m、75 分位 24.2m）。

### 5.5 aggregate 表与逐帧真值的对比（截断效应的批量实证）

同批的 aggregate `metrics_results.txt` 给出的是：
collision_any=0.23、dist_to_gt_trajectory=**3.02**、dist_traveled_m=**89.02**、
duration_frac_20s=0.36——而逐帧真值是 11/30 clip 碰撞、per-clip 最大轨迹
偏差均值 **20.06m**、平均里程 **158.83m**。差异全部来自
`RemoveTimestepsAfterEvent(offroad_or_collision)`（砍掉 988 行/27 条轨迹）和
`RemoveTimestepsAfterEvent(dist_to_gt_trajectory >= 4)`（187 行/11 条轨迹）
两个截断算子：事件越早、越早偏离 GT 的 clip，被截掉的帧越多，aggregate
数字反而越"好看"。这再次验证 §7.1.1/§7.2 的结论：**批量场景下 aggregate
表同样不能作为依据**，只能看逐 clip 的 metrics.parquet。

## 6. 经验与教训

1. **多版本目录 + "最新优先"解析**：scene_id 不等于唯一 artifact；批量选样
   必须按 scene_id 跨 revision 去重，否则批次内版本混杂、结果不可比。
2. **外部 renderer 单 session，批量必须强制串行**：OmniDreams 每次
   start_session 都会清掉旧 session，`topology=1gpu` 默认 4 并发时并发 clip
   全部 "Session not found"。必须同时覆盖 `runtime.nr_workers=1` 和全部五个
   端点的 `n_concurrent_rollouts=1`；改完用 `wizard.run_method=NONE` 生成
   配置确认所有并发数都是 1。
3. **约 40% 的 26.01 USDZ 缺 calibration_estimate.parquet**，video-model
   session 会在起步瞬间失败。等距/随机抽样后必须按 zip 内容预检，并**按
   ≥1.5× 的数量预下载候选**（本次抽 30 个仅 18 个可用，预下载 24 个候选
   筛出 12 个补足），否则跑批中途才发现样本不足。
4. **aggregate 截断在批量层面同样成立，且方向是系统性偏差**：事件越早的
   clip 被截断越多，整批的 collision/trajectory/里程数字全部偏低（§5.5
   有定量对比）。验收只能基于逐帧 parquet + 原始视频。
5. **Alpamayo 1.5 当前闭环能力有限**：30 个真实场景里 90% 出现碰撞或
   offroad，安全监控从未触发；碰撞多集中在前 13s，17 个 clip 的开环预测
   本身就报碰撞。这与单 clip 观察一致，不是个例。后续改进方向：安全拒绝
   采样（当前 num_traj_samples=1）、更早的裕度保持、以及换用更新 driver。
6. **批量运行的工程经济性**：一次 wizard 调用加载一次模型、顺序跑 30 clip，
   全批约 1h44m（08:45:59–10:29:35 UTC），约 **3.45 min/clip**；renderer
   全程只起一次。30 clip 产物共 5.4GB（含每 clip 的 rollout MP4）。批量
   模式比逐 clip 重启 wizard 显著省时，是后续回归测试的推荐形态。
7. **指标可用性注意**：`min_distance_to_lane_boundary_m` 在本批 30 个 clip
   恒为 0（包括 clean clip），不具区分度，分析时不要据此下结论；
   `min_ade@5.0s(gt)` 在该 chunking 配置下无输出。

## 7. 复现方式

```bash
# renderer 会被脚本自动拉起（也可先 scripts/start_renderer.sh）
scripts/run_batch_a15.sh
# 分析器无论 wizard 成功或失败都应运行（失败的 clip 才更需要对账）：
repos/alpasim/.venv/bin/python scripts/analyze_batch_a15.py \
  artifacts/run_a15_batch30_20261001 \
  --manifest scripts/a15_batch_scenes_20261001.csv
# CI/脚本链可加 --strict：有 missing/partial/parse_error 时退出码为 2。
```

分析器的失败隔离：损坏/空 parquet 记为 `parse_error`，同一 scene 多个 rollout
时只统计最新的、其余记 `superseded`，帧数显著偏短的 rollout 记 `partial`——
这些状态在 2026-10-01 代码评审后加入，均已用合成批次验证。
