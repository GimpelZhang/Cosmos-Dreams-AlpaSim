# Stage 2 超详细执行计划（AI Agent 本地无歧义执行版）

> 版本：v1.0　编制日期：2026-10-02
> 上游输入：`docs/Stage2_Plan.md`（用户的 Stage 2 初步构想，10 变体矩阵）
> 适用执行者：AI Coding Agent（Claude Code 等），在本机以用户 `vipuser` 身份执行
> 执行铁律：**严格按 Phase 0 → Phase 5 顺序执行；每个 Task 末尾运行对应的
> `scripts/check_stage2_taskN.py`，只有打印 `[SUCCESS]` 才允许进入下一 Task（Gate 机制）。
> 任何能力断言以代码证据为准；标「待 spike 验证」的条目必须先验证，不得跳做。**

---

## 0. 文档导读

- 第 1 节：目标与范围（明确裁剪项）
- 第 2 节：已核实事实清单（系统、API、论文，全部带 file:line 证据）
- 第 3 节：Stage 1 非破坏保障与回归门禁
- 第 4 节：存储方案（/mnt 规划、docker、磁盘预算）
- 第 5 节：变体矩阵（10 变体 + 1 baseline，prompt 全文 + 注入规格）
- 第 6 节：分阶段任务分解（Phase 0–5，每个 Task 含精确命令/校验/回滚/收尾）
- 第 7 节：关键技术点实现指引
- 第 8 节：评测方案
- 第 9 节：风险登记册
- 第 10 节：时间表与里程碑
- 第 11 节：给执行 Agent 的启动提示词

凭据约定（**全文不出现任何明文 token / 密码**，只引用路径）：

| 用途 | 凭据文件（执行时读取，禁止回显、禁止写入任何被 git 跟踪的文件） |
|---|---|
| HuggingFace / GitHub / NVIDIA NGC token | `~/access/user_access_methods.txt` |
| sudo 密码 | `~/access/access_methods.txt` |

---

# 1. 目标与范围

## 1.1 In-Scope（本阶段必须交付）

1. 基于**同一个 base scene**（Stage 1 已验证 clip，见 §2.5）构建 **10 个反事实长尾变体 + 1 个 baseline 对照**。
2. 打通两种反事实条件通道（均以**本地补丁 + feature flag、默认关闭**实现）：
   - **文本 prompt 通道**：per-rollout 文本 prompt（天气/光照/实体外观），送入 OmniDreams gRPC session；
   - **动态 actor 通道**：per-rollout 合成 TrafficObject（3D box + 时间窗轨迹），
     同时进入渲染器动态条件、trafficsim 会话与 ASL 记录，使 eval 的碰撞几何能够检测到它们。
3. 每个变体跑 **20s 闭环**（与 Stage 1 相同：30fps 渲染、~75 control step、首 1.7s force-GT），
   产出 rollout.asl + 逐帧 metrics.parquet + 相机视频。
4. 为每个变体合成 **1280×704@30fps 的 HUD 视频**：前视渲染主画面 + 右上角 BEV 画中画 +
   底部仪表条（车速、转向、最小障碍距离、安全状态）+ 顶部场景/prompt 信息条。
5. 输出**逐帧证据驱动**的评测报告（Markdown + HTML 展板）：每个变体的感知-决策反应
   （减速时刻、减速度、横向偏移、TTC 派生值）、碰撞部位帧数、offroad 帧数、通过率，与 baseline 对照。
6. Stage 1 回归门禁：开发前、Phase 2 后、全部完成后各跑一次，证据存档。

## 1.2 Out-of-Scope（本阶段明确不做）

| # | 裁剪项 | 理由（代码证据） |
|---|---|---|
| 1 | **路面摩擦系数 / 轮胎滑移 / 低附着刹不住** | 全代码库 grep 不到 friction/tire-slip 概念；physics 服务仅做地面相交（`repos/alpasim/src/physics/alpasim_physics/backend.py`、`server.py`）；MPC（CasADi/do_mpc）参数固定。天气场景一律标注 **「视觉-only，物理未建模」**，评测维度改为感知-决策反应（减速/转向/TTC/碰撞/offroad），严禁在报告中宣称打滑物理。 |
| 2 | 原计划的 `set_condition_prompt()` / `spawn_dynamic_obstacle()` API | 这两个方法在 alpasim/flashdreams 中**不存在**，属臆造 API；真实通道是 gRPC `SessionRequest.text_prompt` 与 `TrafficObjects` 合并（见 §2.2）。 |
| 3 | `/workspace/av_demo/...` 路径 | 该机无此目录；所有路径以 `/home/vipuser/simulation`（代码）与 `/mnt`（数据）为准。 |
| 4 | 传感器探测距离物理缩短（fog 场景） | 无传感器模型；雾仅靠 prompt 改变渲染画面，driver 看到什么就是什么。 |
| 5 | 升级 Alpamayo / OmniDreams / Cosmos 大版本 | Stage 2 不升级、不重装 Stage 1 依赖。2026-10-02 调研发现的新版本（`nvidia/Cosmos3-Super/Edge/Nano`、`nvidia/Alpamayo2-Super`）仅记入 §2.6「可选升级」，不在本阶段执行。 |
| 6 | 改 OmniDreams 模型权重 / post-training | 论文 §9.3.2 的 OOD 能力依赖一个 **dynamic-cuboid-dropout 再训练变体**；本地权重是公开蒸馏 2B checkpoint（`/mnt/weights/.../2b_res720p_30fps_i2v_hdmap_distilled.pt`），不做再训练。OOD 实体以实证 spike 决定保留或降级。 |
| 7 | 交通灯状态注入 | proto `DynamicWorldState` 注释明写「TODO: traffic light states」（video_model.proto:57），未实现。 |

---

# 2. 已核实事实清单（全部经代码/命令核实，日期 2026-10-02）

## 2.1 系统事实（直接采信）

| 项 | 值 |
|---|---|
| 机器 | 无头服务器 ubuntu2，Ubuntu 22.04，内核 6.8.0-138-generic |
| GPU | 2 × A100-SXM4-80GB；**GPU0 = driver 侧容器栈，GPU1 = OmniDreams 渲染器（宿主机进程，gRPC :50051）**；空闲基线 14 MiB/卡 |
| CPU/RAM | 32 逻辑核 / 94 GiB RAM |
| 磁盘 | `/` ext4 196G（已用 125G，**余 63G**）；`/mnt` XFS 500G（已用 228G，**余 273G**）。2026-10-02 `df -h / /mnt` 实测 |
| /mnt 子目录 | alpasim-data、artifacts、assets、caches、cuda13、docker-data、venvs、weights |
| Docker Root | **已是 `/mnt/docker-data`**（2026-10-02 `docker info` 实测）；无需 data-root 迁移，本计划不改 daemon 配置 |
| 环境单一事实源 | `scripts/env.sh`：`STORAGE_ROOT=/mnt`、`HF_HOME=/mnt/caches/hf`、`DOCKER_DATA_ROOT=/mnt/docker-data`、`CUDA_HOME=/mnt/cuda13`、从 `~/access` 解析 token、提供 `sudosw`/`dk` |
| Docker 权限 | wizard 直接调 `docker compose`；无 socket 权限时 `sg docker -c '...'`（批量脚本已内置重入，见 `scripts/run_batch_a15.sh:25-31`） |
| 渲染器单 session | 每次 start_session 先清旧 session → 一切批量必须串行：`runtime.nr_workers=1`、各端点 `n_concurrent_rollouts=1`（`scripts/run_batch_a15.sh:90-95`） |
| Stage 1 时序 | 30fps、frame_interval=33,333µs、首 chunk 5 帧其后 8 帧、control_timestep=266,664µs、force-GT 1.7s；RTF≈0.13×、闭环 ~3.4 min/20s clip（见 `docs/Stage1_Runtime_Performance.md`） |
| 渲染帧分辨率 | **1280×704**（704p，JPEG q90；`docs/Stage1_Runtime_Performance.md:53`） |
| eval 复合视频 | rollout 目录内相机 mp4 实测 **900×1000、3.75fps（75 帧/20s）**：顶部左 = BEV 调试图、顶部右 = Agg/Per-Ts 指标表、底部 = 相机画面（2026-10-02 抽帧核实，见图例分析见 §7.4） |

## 2.2 API 能力证据表（决定本计划可行性的核心事实）

| # | 能力 | 证据（file:line） | 结论 |
|---|---|---|---|
| 1 | 文本 prompt 端到端可用，**per-session**（非 per-chunk） | proto `repos/flashdreams/integrations_v2/omnidreams/impl/grpc/protos/video_model.proto:76-79`（`TextPrompt{positive,negative}` 在 `SessionRequest`，:91）；渲染器消费 `.../conditioning/conditioning_wrapper.py:361-363`（positive 复制到每个相机）、`:438`；AlpaSim 配置 `repos/alpasim/src/runtime/alpasim_runtime/config.py:115-116`（`text_prompt_positive/negative`）；wizard 已用 `repos/alpasim/src/wizard/configs/deploy/external_video_model.yaml:63-64` | 改 prompt 无需改 proto；wizard override 可改但**一次 wizard 全 clip 共享**；per-rollout prompt 需小补丁（§7.2） |
| 2 | 动态 actor（3D box+轨迹）注入通道存在 | proto `video_model.proto:48-52`（`DynamicActor{class_id,bbox_dims,trajectory}`）、`:54-56`（`DynamicWorldState`）；枚举仅 CAR/TRUCK/PEDESTRIAN/CYCLIST/OTHER（`:40-46`）；chunk 请求携带动态态（server 端消费 `repos/flashdreams/integrations_v2/omnidreams/impl/grpc/server.py:453`、`:514-515`）；AlpaSim 从录制 clip 的 TrafficObjects 构造 `repos/alpasim/src/runtime/alpasim_runtime/video_model/utils.py:119-157`（`build_dynamic_world_state_for_video_model`） | box+轨迹通道真实；T-Rex/牛等只能 class_id=OTHER + 文本 prompt |
| 3 | **渲染器跳过 static actor** | `utils.py:127-129`：`if traffic_obj.is_static: continue` | 注入静止障碍（牛/树/杂物）必须以 **is_static=False + 覆盖完整时间窗的恒定轨迹**发送，否则渲染器收不到；trafficsim/eval 不依赖该标志过滤，仍可正常工作（待 spike 确认） |
| 4 | actor 仅在轨迹时间窗覆盖帧时可见 | `utils.py:131-140`：逐帧检查 `int(ts) in traffic_obj.trajectory.time_range_us`，只插值有效时间戳 | 「第 N 秒突然出现」可直接用轨迹起点实现；轨迹必须按 30fps 给足采样点（§7.1） |
| 5 | label → class_id 映射 | `utils.py:159-177`：pedestrian/person→PEDESTRIAN；cyclist/bicycle/bike→CYCLIST；truck/trailer/bus→TRUCK；未知→OTHER（告警一次） | 合成对象的 `label_class` 决定 class_id |
| 6 | random_seed：**proto 和渲染器支持，AlpaSim 不传递** | proto `video_model.proto:94`（`SessionRequest.random_seed`，0=服务器自选）；渲染器使用 `server.py:959-962`（`request_seed=int(request.random_seed)`）；但 AlpaSim 构造 request 时**未设该字段**（`repos/alpasim/src/runtime/alpasim_runtime/services/video_model_service.py:240-249`） | 确定性渲染回归需要小补丁：把 session seed 写入 request.random_seed（§7.3） |
| 7 | DebugOptions：return_hdmap_frames 已暴露；skip_video_generation 未暴露 | proto `video_model.proto:82-85`；runtime `config.py:120-121`（return/forward hdmap）、`:143-145`（forward 要求 return）；service `video_model_service.py:251-258`；grep `skip_video_generation` 在 runtime/wizard **无消费** | BEV 条件帧可直接由渲染器返回（经 `runtime.renderer.video_model_config.return_hdmap_frames=true`）；fast-debug 模式需要补丁才能用（低优先） |
| 8 | TrafficObjects 生产/消费链 | 定义 `repos/alpasim/src/utils/alpasim_utils/scenario.py:303-309`（`TrafficObject{track_id,aabb,trajectory,is_static,label_class}`）、`:315-383`（`load_from_json`，从 clip artifact JSON 解析并三次样条平滑）；解析点 `repos/alpasim/src/utils/alpasim_utils/artifact.py:169`；协议接口 `scene_data_source.py:51`；rollout 裁剪 `repos/alpasim/src/runtime/alpasim_runtime/unbound_rollout.py:261-293`（clip + filter + hidden 下沉），挂到 `UnboundRollout(traffic_objs=...)`（`:329`） | **合成 actor 合并的最小补丁面 = `unbound_rollout.py:268` 之后**（filter 之后、return 之前），一处合并即贯通全部下游 |
| 9 | 下游消费 1：trafficsim | `event_loop.py:579-597`（TrafficSessionConfig 传 `unbound.traffic_objs`）；`repos/alpasim/src/runtime/alpasim_runtime/services/traffic_service.py:79-88`（逐对象构 `ObjectTrajectory` 进 TrafficSessionRequest，含 EGO GT 轨迹 :69-76） | 合并进 unbound 的对象会进 trafficsim 会话 |
| 10 | 下游消费 2：渲染器每 chunk | `repos/alpasim/src/runtime/alpasim_runtime/events/video_model/prefetch.py:180-193`（`traffic_objects=state.traffic_objs`，hidden 合并后构 dynamic_state） | 合并对象逐 chunk 进渲染器 |
| 11 | 下游消费 3：ASL → eval 碰撞几何 | eval accumulator 消费 ASL 中的 `traffic_session_request`：`repos/alpasim/src/eval/src/eval/accumulator.py:145-146`；actor 轨迹/box 构建 `:246-263`；碰撞打分 `repos/alpasim/src/eval/src/eval/scorers/collision.py:20-60`（EGO 多边形 vs 各 actor，分 front/rear/lateral）；最小距离 `scorers/min_distance_to_obstacle.py:23-46` | 碰撞检测是**基于 actor 轨迹多边形的几何后验**，不是 physics 服务算的；合并对象自动进入碰撞/距离评测 |
| 12 | 每场景多 rollout 开关 | `repos/alpasim/src/wizard/configs/base_config.yaml:308`：`runtime.simulation_config.n_rollouts`（默认 1） | **一次 wizard、同一 scene、n_rollouts=11** 即可串行产出 11 个变体 session，模型只加载一次（rollout 序号→变体映射，待 spike 确认序号可取；fallback=11 次 wizard） |
| 13 | 原始帧抽取工具 | `repos/alpasim/src/utils/alpasim_utils/asl_to_frames/__main__.py:301-321`：`asl-to-frames <asl_glob> --format mp4|frames --log-save-dir DIR`；入口 `asl-to-frames`（src/utils/pyproject.toml:32） | 可从 ASL 离线导出相机原始帧做 HUD |
| 14 | headless 字体 | `/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf`、`DejaVuSans-Bold.ttf`（fc-list 实测） | HUD 文字用 DejaVu Sans，**HUD 一律英文**避免缺中文字形 |

## 2.3 论文说法 vs 本地代码实现 对照表

论文：**《NVIDIA OmniDreams: Real-Time Generative World Model for Closed-Loop Autonomous
Vehicle Simulation》，arXiv:2606.03159v1（2026-06-02）**，标题/作者/摘要已核实真实存在。

| 论文说法（§） | 本地代码现状 | 差距 |
|---|---|---|
| §9.3.1 Controllable Scenario Editing：**text prompt 控制 weather/lighting/time-of-day**；abstract world-scenario map（车道几何+box）控制结构与 actor layout；与首帧 RGB seed 一起在保持道路几何/远景稳定的前提下做反事实编辑 | text prompt 通道端到端可用（§2.2 表 1）；world-scenario map = 每 chunk 的 hdmap + dynamic_state，通道可用；但**默认 prompt 全 rollout 共享**，per-rollout 需补丁 | 补丁后基本对齐论文编辑能力 |
| §9.3.2 OOD Object Modeling：明确指出**朴素做法（直接往首帧 RGB 贴 OOD 物体、结构化条件里无对应 cuboid）会导致 artifacts 与动态不一致**；论文的解法是**再 post-train 一个带 randomized dynamic-cuboid dropout 的 OmniDreams 变体**，让模型容忍「有 RGB 物体、无 cuboid」并随时间生成该物体 | 本地 checkpoint **不是** cuboid-dropout 再训练变体；我们采用的是「box+轨迹+prompt」（给模型一致信号，规避论文批评的不一致注入），但 OOD 外观（T-Rex）能否稳定生成**无任何保证** | **必须 spike 实证**；崩坏则降级（T-Rex → 大型施工卡车/OTHER 高把握实体），报告中如实写明 |
| §9.4 / Fig. 12：闭环结果以 **(camera, BEV) pair** 呈现；BEV 为 AlpaSim bird's-eye-view 调试叠加，ego 绿色、Alpamayo 策略预测轨迹为黄色线 | eval 已产出含 BEV 的 900×1000 复合诊断视频（§2.1）；策略预测轨迹在 ASL 的 driver 事件中可离线取得（待 spike 确认字段） | HUD 画中画直接复用/重绘该 BEV 即可对齐论文呈现 |
| §9.4.1：501 scene × 20s 闭环；replan 节流到 533ms chunk 率；指标 All Incidents / Collision(Front/Lateral/Rear) / Offroad；**incident 只在 ego 距 GT 轨迹 4m 内计数**（论文协议） | 本地 metrics.parquet 含 collision_* / offroad / dist_to_gt_trajectory 等（§2.4），aggregate 的「4m 截断」对应 RemoveTimestepsAfterEvent | 本计划**不用 aggregate**，自行按逐帧 parquet 统计，并同时报告 dist_to_gt 以标注事件是否「at fault」 |
| 摘要：21k 小时驾驶数据 mid/post-train；OmniDreams WAM 以 1/5 参数量在 NuRec 上超 Alpamayo 1.5 | 本阶段不涉及 WAM | — |

## 2.4 逐帧 metrics.parquet 可用指标清单（2026-10-02 从真实 rollout 读取）

long format：列 `name, timestamps_us, values, valid, time_aggregation, clipgt_id, rollout_id,
run_uuid, run_name`；`values` 使用时需 `pd.to_numeric(errors="coerce")`；同 name/时间戳多行取 max。

- 布尔事件类：`collision_any`、`collision_front`、`collision_lateral`、`collision_rear`、
  `offroad`、`wrong_lane`、`safety_monitor_triggered`、`open_loop_collision`、`img_is_black`、`eval_relevant`
- 数值类：`dist_to_gt_trajectory`、`dist_to_gt_location`、`progress`、`progress_rel`、
  `progress_rel_to_total`、`dist_traveled_m`、`gt_dist_traveled_m`、`min_distance_to_obstacle_m`、
  `min_distance_to_lane_boundary_m`、`plan_deviation`
- ADE 类：`min_ade@0.5s/1.0s/2.5s/5.0s(gt)`（注：5.0s 在 8frame chunking 下可能无输出，见 CLAUDE.md §5.7）
- **没有 TTC（time-to-collision）指标** → 报告中的 TTC 一律标注为「派生值」（§8.3）
- eval 复合视频表里另有 `collision_at_fault`、`offroad_or_collision_at_fault`（aggregate 协议，仅参考）

## 2.5 Base scene 候选（Phase 0 spike 后定稿，默认候选已选）

| 候选 | scene_id | 先验事实 |
|---|---|---|
| **首选（perfclip）** | `clipgt-02eadd92-02f1-46d8-86fe-a9e338fed0b6` | Stage 1 多次单 clip 验证、有 perf 复测；居民区直路、两侧有路肩/人行道与停车；资产已在 `data/nre-artifacts` 缓存 |
| 备选 clean 1 | `clipgt-3d343ae9-6d5b-415f-be2c-aa8d0ebc03c9` | batch30 clean，progress 0.785 |
| 备选 clean 2 | `clipgt-46252225-453d-474c-963c-bc0cd917e7e4` | batch30 clean，progress 1.0 |
| 备选 clean 3 | `clipgt-5a38c811-1e28-4a67-9239-54ba2b695950` | batch30 clean，progress 0.992 |

选样要求（spike 量化）：直线或小曲率（横向曲率半径 >200m）、右侧/左侧有 ≥2.5m 可用路肩或人行道、
GT 初速适中（15–35 km/h），保证横穿/静止障碍在 3–4s 激活时 ego 有反应距离但又不会远到无交互。

## 2.6 2026-10-02 版本调研（仅记录，本阶段不升级）

- HF 上 NVIDIA 已发布 **Cosmos3 系列**：`nvidia/Cosmos3-Super`、`nvidia/Cosmos3-Edge`、`nvidia/Cosmos3-Nano`；
  另有 `nvidia/Cosmos-Predict2.5-2B`、`nvidia/Cosmos-Transfer2.5-2B`、`nvidia/Cosmos-Reason2-2B`。
- `nvidia/Alpamayo2-Super`（Stage 1 计划时已知，34B/71.65GB，单 A100-80GB 仅接近极限）。
- 这些均为 Stage 3 候选升级项；Stage 2 维持 Alpamayo 1.5（10B）+ OmniDreams 蒸馏 2B 现状。

---

# 3. Stage 1 非破坏保障与回归门禁

## 3.1 新增物清单（全部不与 Stage 1 混用）

| 类型 | 新增路径 |
|---|---|
| 脚本 | `scripts/run_stage2_batch.sh`、`scripts/analyze_stage2.py`、`scripts/render_stage2_hud.py`、`scripts/generate_stage2_variants.py`、`scripts/check_stage2_task*.py` |
| 配置 | `configs/stage2/variants.yaml`（变体规格权威文件）、`configs/stage2/extras/stage2_env.yaml`（向 runtime 容器注入环境变量，待 spike 确认服务名）、`configs/stage2/manifests/`（变体清单 CSV） |
| 补丁 | `configs/local-patches/patches/alpasim-stage2-001-synthetic-actors.patch`、`alpasim-stage2-002-perclip-prompt-seed.patch` |
| 产物 | `/mnt/artifacts/stage2/...`（经 `artifacts/stage2` 软链访问） |
| 依赖 | 任何新依赖只装入 `/mnt/venvs/stage2`（新建 venv）或容器；**禁止修改 `repos/alpasim/.venv`**。优先复用 alpasim 既有环境（cv2/pandas/ffmpeg 已具备），不新增依赖 |

## 3.2 补丁铁律

1. 所有对 `repos/` 内跟踪文件的改动只能以 `configs/local-patches/` 的 git-diff 补丁存在
   （机制见 `configs/local-patches/README.md`），补丁要能 `git apply` 也能 `git apply -R`。
2. 补丁内全部 Stage 2 行为由环境变量 feature flag 控制：
   - `STAGE2_VARIANT_SPEC`：变体规格 YAML 的绝对路径；**未设或为空 → 补丁代码路径整体 no-op**；
   - `STAGE2_VARIANT_ID`：单变体运行时指定变体 id（可选；未设则按 rollout 序号映射）；
   - `STAGE2_RENDER_SEED`：渲染 seed 整数（可选；未设则用 session seed）。
   **默认（全部不设）时 Stage 1 行为逐字节不变**——此断言由 check_stage2_task4.py 以回归闭环验证。
3. 补丁新增的 Python 文件放在新目录 `src/runtime/alpasim_runtime/stage2/`（不改动任何
   Stage 1 模块的 import 图；Stage 1 两个修改点各只加一个「flag 检查后懒加载调用」）。
4. 开发前记录基线（Task 0.1 命令），任何补丁 apply/revert 后重新记录。

## 3.3 回归门禁（三次：开发前 Task 0.2 / Phase 2 后 Task 2.4 / 全部完成后 Task 5.4）

固定条件：base scene = §2.5 定稿值、driver=`alpamayo15_1cam_local`、`deploy=external_video_model`、
`topology=1gpu`、`+chunking=8frame`、固定 session seed（`STAGE2_RENDER_SEED=20261002`，
配合 Task 2.x 的 seed 补丁；开发前首次回归时 seed 尚未贯通渲染器，故首次回归以
「事件指标 + 墙钟」为门禁，seed 贯通后第二/三次回归升级为像素级指标比对）。

每次回归采集（脚本统一产出 JSON）：

| 量 | 容差 |
|---|---|
| metrics.parquet 行数（帧数） | 完全一致（±0） |
| progress / dist_traveled_m（末值） | ±5% |
| collision_any/front/rear/lateral、offroad 的首次帧与总帧数 | 完全一致（±0 帧） |
| dist_to_gt_trajectory 逐帧序列（seed 贯通后） | 每帧偏差 ≤0.25 m |
| 每 step 墙钟均值 | ±15% |
| 每 chunk 渲染墙钟均值 | ±15% |

判定脚本：`scripts/check_stage2_regression.py <new.json> <baseline.json>`，
全部断言通过输出 `[SUCCESS] REGRESSION`，否则非零退出并打印越界项。

## 3.4 资源纪律（每个 Task 的收尾校验都包含）

```bash
source "$HOME/simulation/scripts/env.sh"
bash "$HOME/simulation/scripts/stop_renderer.sh"
nvidia-smi --query-gpu=index,memory.used --format=csv,noheader   # 期望：两卡均 14 MiB
dk ps -q                                                         # 期望：空
pgrep -af "omnidreams.impl.grpc.server" || echo "no renderer process"
```

---

# 4. 存储方案

## 4.1 /mnt 目录规划（沿用现有，不新建挂载、不重格式化）

```
/mnt/artifacts/stage2/                # 全部 Stage 2 产物（root 写入，目录 777）
  baseline_snapshot.txt               # Task 0.1：inner repo hash/status/df
  regression/{00_baseline,01_postpatch,02_final}/
                                      # 回归闭环 logdir + digest.json
  spikes/                             # Phase 0 spike 的 logdir/抽帧
  run_stage2_20261002/                # 正式批量 run（wizard log_dir）
    rollouts/<scene_id>/<rollout_uuid>/{rollout.asl,metrics.parquet,*.mp4}
  frames/<variant_id>/                # asl-to-frames 导出的原始帧（JPEG）
  videos/<variant_id>_hud.mp4         # HUD 成片
  report/{Stage2_Report.md, index.html}
/mnt/venvs/stage2/                    # 仅在确有新依赖时创建（python3.12 -m venv）
```

`artifacts/stage2` 软链：`ln -sfn /mnt/artifacts/stage2 /home/vipuser/simulation/artifacts/stage2`
（artifacts/ 整体在 .gitignore 中）。

**未来若加新盘**：挂到 `/mnt/ext` 等子目录并把对应软链改指；**严禁 mkfs/重格式化任何盘**。

## 4.2 Docker / BuildKit 处置

- Docker Root 已是 `/mnt/docker-data`，**本计划不执行任何 daemon 配置变更、不重启 docker**。
- Stage 2 不构建新镜像（渲染器宿主机直跑、driver 沿用 Stage 1 镜像）。
- 如 spike 中确需临时镜像：构建上下文放 `/mnt`，`DOCKER_BUILDKIT=1` 并加
  `--build-context cache=/mnt/caches/buildkit`；禁止在 `/var/lib` 或根分区留层。

## 4.3 磁盘预算

| 项 | 单位大小 | 数量 | 小计 |
|---|---|---|---|
| rollout.asl（20s） | ~230 MB | 11（正式）+ ~6（spike/回归） | ~3.9 GB |
| rollout 相机 mp4 | ~3.5 MB | 17 | ~60 MB |
| asl-to-frames 导出帧（JPEG q92，600 帧） | ~130 MB | 11 | ~1.4 GB |
| HUD 成片（H.264 1280×704 30fps 20s） | ~20 MB | 11 | ~220 MB |
| 回归 digest/日志 | <5 MB | 3 | ~15 MB |
| 合计 | | | **≈ 5.6 GB** |

/mnt 余 273G，余量充足。红线：`/mnt` 可用 <30G 或 `/` 可用 <20G 立即停手清理；
每个 Task 开头执行 `df -h / /mnt`。删除大文件后空间未释放 → `sudosw lsof +L1` 查持有句柄。

---

# 5. 变体矩阵（10 + 1 baseline，修订版）

## 5.1 通用约定

- **坐标约定（待 spike 最终确认轴向）**：变体规格一律写「ego 起步 rig 系」坐标：
  `x`=纵向（车头方向为正）、`y`=横向（拟按左为正，spike 时用 GT 轨迹朝向验证 rig 系实际手向后固定）、
  单位 m；补丁负责用 `rig.trajectory` 在 render 起始时间戳的位姿把 rig 系坐标变换到 clip 全局系。
- **sim 时刻**：相对 render 首帧，t=0 起算；force-GT 段（0–1.7s）不安排激活，
  激活时刻一律 ≥2.5s，确保 ego 已进入闭环控制。
- 轨迹采样：30fps（33,333µs 间隔），从激活帧到 20s 末逐帧给 pose；
  运动对象按匀速直线（横向穿越）生成，yaw 朝向速度方向。
- 所有注入对象统一 `is_static=False`（理由 §2.2 表 3），静止对象给恒定轨迹。
- class 映射通过 `label_class` 走 `utils.py:159-177`；box 为 `(length_x, width_y, height_z)`。
- 每个变体统一 negative prompt：
  `"deformed geometry, distorted road, warped lanes, melted textures, text, watermark, logo, oversaturated, low-quality, blurry, cartoon, CGI look"`。

## 5.2 矩阵总表

| ID | 类别 | prompt（positive，全文） | 注入实体 | 激活 t |
|---|---|---|---|---|
| `v00_baseline` | 对照 | Stage 1 默认 prompt（`external_video_model.yaml:63` 原文，不改写） | 无 | — |
| `v01_heavy_snow_blizzard` | 天气（视觉-only） | `photorealistic dashcam footage, heavy blizzard, dense snowfall with large snowflakes, snow accumulating on the asphalt road and curbside parked cars, low visibility about 30 meters, overcast dusk sky, cold muted colors, realistic road geometry unchanged` | 无 | — |
| `v02_torrential_rain_night` | 天气/光照（视觉-only） | `photorealistic dashcam footage, midnight, torrential rain, dense rain streaks, large wet puddles with bright reflections from streetlights and headlights, glossy wet asphalt, dark blue night color palette, realistic road geometry unchanged` | 无 | — |
| `v03_dense_fog_dawn` | 天气（视觉-only） | `photorealistic dashcam footage, early dawn, extremely dense ground fog, visibility reduced to about 15 meters, soft glowing halos around vehicle headlights, pale gray and faint orange tones, realistic road geometry unchanged` | 无 | — |
| `v04_blinding_sunset_glare` | 光照（视觉-only） | `photorealistic dashcam footage, golden hour, low sun directly ahead producing intense blinding glare and large lens flare across the windshield, strongly washed-out forward scene, long sharp shadows, realistic road geometry unchanged` | 无 | — |
| `v05_stroller_jaywalking` | VRU 横穿 | `photorealistic dashcam footage, clear sunny day, a woman pushing a baby stroller suddenly crossing the street from the right side, casual clothing, residential two-lane road, sharp shadows, natural colors` | `pedestrian_stroller`：PEDESTRIAN；box 1.4×0.8×1.7；起点 rig(22.0, -3.2)；速度 vy=+1.2 m/s（右→左横穿） | 3.0s |
| `v06_wheelchair_shoulder` | 罕见移动体 | `photorealistic dashcam footage, sunny afternoon, a person riding a powered wheelchair along the right roadway shoulder, moving in the same direction as traffic, residential street, natural colors` | `powered_wheelchair`：PEDESTRIAN（label `pedestrian`）；box 1.2×0.75×1.3；起点 rig(16.0, -2.2)；速度 vx=2.5 m/s 沿路肩直行 | 2.5s |
| `v07_t_rex_ood` | **OOD（探索性）** | `photorealistic cinematic footage, a massive realistic Tyrannosaurus rex dinosaur standing in the middle of the road, detailed reptilian skin, roaring, cars parked along the curbs, clear daylight` | `t_rex`：OTHER；box 6.0×2.5×5.0；起点 rig(28.0, 0.0)；静止恒定轨迹 | 3.5s |
| `v08_loose_cow` | 动物突发 | `photorealistic dashcam footage, daytime, an escaped black-and-white dairy cow standing motionless in the center of the driving lane, realistic fur, residential street` | `dairy_cow`：OTHER；box 2.4×1.0×1.6；起点 rig(26.0, 0.0)；静止 | 3.0s |
| `v09_construction_debris` | 路面障碍群 | `photorealistic dashcam footage, daytime roadworks, fallen wooden crates and several bright orange traffic barrels scattered across the driving lane, construction debris, residential street` | 3× OTHER：`barrel_1` box 0.6×0.6×0.9 @ rig(18.0,-0.6)；`barrel_2` box 0.6×0.6×0.9 @ rig(19.2,0.3)；`crate_1` box 1.4×1.0×0.9 @ rig(20.5,-0.2)；均静止 | 3.0s |
| `v10_typhoon_compound` | 复合长尾 | `photorealistic dashcam footage, violent typhoon storm, heavy wind and rain, a large broken tree branch lying across the road, a pedestrian holding a bent umbrella fighting the wind near the right curb, dark dramatic sky` | `fallen_branch`：OTHER box 5.0×0.8×0.6（斜置，yaw=+25°）@ rig(20.0,0.0) 静止；`pedestrian_umbrella`：PEDESTRIAN box 0.8×0.8×1.75 起点 rig(24.0,-3.0) 速度 vy=+0.8 m/s | 3.0s |

## 5.3 时间窗/速度合理性检查（由生成器自动断言）

- 横穿者 v05：3.0s 时 x=22m；ego 若以 ~30km/h（8.3m/s）前进，3.0+2.6s ≈ 5.6s 到达 x=22，
  行人在 2.6s 内横向移动 3.1m——恰在穿越车道窗口，形成安全关键交互（不保证碰撞/不碰撞，正是被测项）。
- v06：轮椅 2.5m/s 与 ego 同向，在路肩；ego 接近率 = v_ego−2.5。
- 所有轨迹时间窗 = [激活帧, 20s 末]，覆盖 chunk 内全部 ≥激活 的帧时间戳（对照 `utils.py:131-140`）。
- 若 spike 发现某候选 clip GT 初速显著偏离 15–35 km/h，按实测速度等比缩放上述 x 坐标，
  规则：激活对象的 x = ego 到达该 x 前留有 2–3s 反应时间。

---

# 6. 分阶段任务分解

> 所有脚本命令一律使用绝对路径、先 `source "$HOME/simulation/scripts/env.sh"`。
> 长任务一律 `setsid bash -c '...' < /dev/null > log 2>&1 & disown` 后台运行并轮询日志。

## Phase 0 —— 基线、回归与可行性 spikes（不写任何 Stage 2 功能代码）

### Task 0.1　基线快照与开工预检

**前置**：无。**预计耗时**：2 min。**产出**：`/mnt/artifacts/stage2/baseline_snapshot.txt`。

```bash
source "$HOME/simulation/scripts/env.sh"
set -euo pipefail
S2=/mnt/artifacts/stage2
mkdir -p "$S2"/{regression/00_baseline,spikes}
ln -sfn "$S2" "$HOME/simulation/artifacts/stage2"
{
  echo "## date"; date -Is
  echo "## df"; df -h / /mnt
  echo "## gpu"; nvidia-smi --query-gpu=index,name,memory.used,driver_version --format=csv
  echo "## inner repo hashes"
  for r in alpasim flashdreams alpamayo omni-dreams; do
    printf '%s ' "$r"; git -C "$HOME/simulation/repos/$r" rev-parse HEAD 2>/dev/null || echo "n/a"
  done
  echo "## inner repo status (short)"
  for r in alpasim flashdreams alpamayo; do
    echo "-- $r"; git -C "$HOME/simulation/repos/$r" status --short 2>/dev/null | head -30
  done
  echo "## docker info"; docker info 2>/dev/null | grep -iE 'docker root|server version'
  echo "## existing applied stage1 patches check"
  grep -c . "$HOME/simulation/configs/local-patches/README.md"
} > "$S2/baseline_snapshot.txt"
cat "$S2/baseline_snapshot.txt"
```

快照中的期望哈希（2026-10-02 实测；若不一致，把实际值记入快照并继续，说明 repos 已被改动过）：

| repo | HEAD |
|---|---|
| alpasim | `affc2eab209fa43bdfa2f26c0f8d437922d78a68` |
| flashdreams | `0957cf0ca01ae1d0a8ca7259e7e8a9d4580c1db8` |
| alpamayo | `11a0e01c13a5622377c45ee37d653351453ec43b` |
| omni-dreams | `cd85f399190393a6f5247b2c080b2cdd7a0adff7` |

**校验**：

```bash
source "$HOME/simulation/scripts/env.sh"
"$HOME/simulation/repos/alpasim/.venv/bin/python" "$HOME/simulation/scripts/check_stage2_task01.py"
```

`scripts/check_stage2_task01.py` 判定逻辑：快照文件存在；含 4 个 repo 的合法 40 位哈希；
`df` 显示 `/mnt` 可用 ≥30G、`/` 可用 ≥20G；两 GPU 当前显存 = 14 MiB（容差 ±50 MiB）；
密钥扫描（scripts/configs/docs 内无 `hf_`/`ghp_`/`nvapi-` 前缀）。通过打印
`[SUCCESS] Stage2 Task 0.1: baseline snapshot recorded, preflight green.`

**失败处置**：磁盘不达标 → 清理 /mnt 或停止并报告；GPU 被占 → 查 `nvidia-smi` 进程并按 §3.4 收尾。

### Task 0.2　Stage 1 回归基线闭环（固定场景，20s）

**前置**：Task 0.1 [SUCCESS]。**预计耗时**：~8 min（渲染器若未运行含 3–5min 加载 + 3.4min 闭环）。
**产出**：`/mnt/artifacts/stage2/regression/00_baseline/`（wizard logdir 软链）+ `digest.json`。

```bash
source "$HOME/simulation/scripts/env.sh"
set -euo pipefail
BASE_CLIP=clipgt-02eadd92-02f1-46d8-86fe-a9e338fed0b6
REGDIR=/mnt/artifacts/stage2/regression/00_baseline
mkdir -p "$REGDIR"

# 渲染器未起则拉起（后台 + 就绪轮询，复用 Stage 1 机制）
if ! bash -c "exec 3<>/dev/tcp/127.0.0.1/50051" 2>/dev/null; then
  setsid bash "$HOME/simulation/scripts/start_renderer.sh" < /dev/null \
    > "$REGDIR/renderer.log" 2>&1 & disown
  for _ in $(seq 1 200); do
    grep -q "Server started successfully" "$REGDIR/renderer.log" && break
    sleep 3
  done
  grep -q "Server started successfully" "$REGDIR/renderer.log" \
    || { tail -30 "$REGDIR/renderer.log"; exit 1; }
fi

cd "$HOME/simulation/repos/alpasim"
CUDA_VISIBLE_DEVICES=0 uv run --no-sync --project src/wizard alpasim_wizard \
  deploy=external_video_model topology=1gpu driver=alpamayo15_1cam_local \
  +chunking=8frame "scenes.scene_ids=['$BASE_CLIP']" \
  'wizard.external_services.renderer=["127.0.0.1:50051"]' \
  +runtime.endpoints.startup_timeout_s=900 \
  "wizard.log_dir=$REGDIR/run"

# 采集 digest（帧/事件/墙钟从逐帧 parquet 与 wizard 日志解析）
"$HOME/simulation/repos/alpasim/.venv/bin/python" "$HOME/simulation/scripts/check_stage2_collect_digest.py" \
  "$REGDIR/run" "$REGDIR/digest.json"
```

**说明**：本次回归不使用任何 Stage 2 环境变量（即使将来补丁已 apply，flag 未设即 no-op）。
`check_stage2_collect_digest.py` 是本阶段新建的公共小工具（pandas 已在 alpasim venv），
解析：metrics.parquet 的行数与各布尔事件首帧/总帧数、progress/dist_traveled 末值、
dist_to_gt_trajectory 逐帧序列；wizard 日志中 `step`/`chunk` 墙钟（正则按
`docs/Stage1_Runtime_Performance.md` §测量方法一致的日志行；字段名以实际日志为准，脚本先探测）。

**校验**：digest.json 存在；含 1 个 rollout 目录且 `_complete` 文件在；帧数 = 75（容差 ±2）；
`img_is_black` 全 False。通过打印 `[SUCCESS] Stage2 Task 0.2: Stage1 regression baseline recorded.`

**收尾**：渲染器可保留供后续 spike（下一 Task 立即使用）；若当天停工则 stop_renderer。

### Task 0.3　Spike A：渲染器启动方式（torchrun vs 直启）

**背景**：记忆库记录 standalone torchrun 在本机因 pc_0 反向解析可能挂死；
而现有 `scripts/start_renderer.sh:14-18` 仍用 torchrun（历史日志也有成功记录，行为不稳定）。
**前置**：Task 0.2。**预计耗时**：15 min。**性质**：只读+进程实验，不改被跟踪脚本。

步骤：

1. 停止当前渲染器（`bash scripts/stop_renderer.sh`），确认 GPU1 = 14 MiB。
2. 用**直启方式**启动（单卡 env，绕开 torchrun  rendezvous）：

```bash
source "$HOME/simulation/scripts/env.sh"
cd "$HOME/simulation/repos/flashdreams"
setsid bash -c '
  CUDA_VISIBLE_DEVICES=1 LOCAL_RANK=0 RANK=0 WORLD_SIZE=1 \
  MASTER_ADDR=127.0.0.1 MASTER_PORT=29500 \
  uv run --package flashdreams-omnidreams python -m omnidreams.impl.grpc.server \
    --pipeline_config_name omnidreams --host 0.0.0.0 --port 50051 \
    --resolution 704p --output_format jpeg --jpeg_quality 90
' < /dev/null > /mnt/artifacts/stage2/spikes/renderer_direct.log 2>&1 & disown
for _ in $(seq 1 200); do
  grep -q "Server started successfully" /mnt/artifacts/stage2/spikes/renderer_direct.log && break
  sleep 3
done
grep -q "Server started successfully" /mnt/artifacts/stage2/spikes/renderer_direct.log \
  || { tail -40 /mnt/artifacts/stage2/spikes/renderer_direct.log; exit 1; }
nvidia-smi --query-gpu=index,memory.used --format=csv,noheader
```

3. 连通性验证：用 wizard `wizard.run_method=NONE` 不需要渲染器；改为 socket 探活 +
   后续 Task 0.5 的真实短闭环即端到端验证。

**判定**：直启在 10min 内就绪 → 记录结论「直启可用」，Phase 3 批量脚本使用直启
（做法：新建 `scripts/start_renderer_direct.sh`，**不修改** `start_renderer.sh`）。
check 脚本 [SUCCESS] 条件：日志含就绪标志 + GPU1 显存 ≥15GB + 端口可连。
**失败/回滚**：直启失败 → 保留 torchrun 脚本路径，记录失败日志，Phase 3 继续用原脚本
（torchrun 挂死时重试一次，历史上第二次常能成功）。

**收尾**：渲染器保留供 Task 0.4/0.5。

### Task 0.4　Spike B：base scene 几何/初速测定与定稿

**前置**：Task 0.3。**预计耗时**：10 min。**产出**：`/mnt/artifacts/stage2/spikes/base_scene_geom.json`。

用 Task 0.2 的回归 run（base clip）离线解析，无需重跑闭环：

```bash
source "$HOME/simulation/scripts/env.sh"
"$HOME/simulation/repos/alpasim/.venv/bin/python" - <<'PY'
import json, glob
import pandas as pd
run="/mnt/artifacts/stage2/regression/00_baseline/run"
mp=sorted(glob.glob(run+"/rollouts/*/*/metrics.parquet"))[0]
df=pd.read_parquet(mp)
df["values"]=pd.to_numeric(df["values"],errors="coerce")
def row(name,agg):
    s=df[df["name"]==name].groupby("timestamps_us")["values"].max()
    return agg(s)
# GT 初速：用 gt_dist_traveled 的前若干步差分（control step 0.266664s）
s=df[df["name"]=="gt_dist_traveled_m"].groupby("timestamps_us")["values"].max().sort_index()
v0=(s.iloc[3]-s.iloc[0])/(3*0.266664)*3.6
prog=df[df["name"]=="progress_rel"].groupby("timestamps_us")["values"].max()
out={"gt_v0_kmh":round(float(v0),2),
     "gt_total_progress":round(float(prog.iloc[-1]),3),
     "n_frames":int(df["timestamps_us"].nunique())}
# 曲率：从 dist_to_gt 无法直接得到；改用 ASL 中 ego/rig 轨迹相邻 heading 差分
# （如 ASL 解析过重，则降级为定性：从 rollout 相机视频首帧目视确认道路走向）
print(json.dumps(out,indent=2))
open("/mnt/artifacts/stage2/spikes/base_scene_geom.json","w").write(json.dumps(out,indent=2))
PY
```

补充对 3 个 clean 备选的同项测定（它们都在 `/mnt/artifacts/run_a15_batch30_20261001/rollouts/`，
直接读 metrics.parquet，不跑闭环），输出对比表。曲率/路肩宽度无法从 metrics 取得的部分
标注为「从首帧视频定性确认」并把结论写入 JSON。

**判定（check_stage2_task04.py，[SUCCESS]）**：至少 1 个候选满足 GT 初速 15–35 km/h、
progress_rel 末值 ≥0.9、首帧目视直路且有路肩空间；将满足者 id 写入
`/mnt/artifacts/stage2/base_scene.txt`。**默认预期 `clipgt-02eadd92` 当选**；若落选用备选。
**失败处置**：4 个候选均不满足 → 报告用户并请求从 batch30 其余 clip 中扩选（不自行下载新场景）。

### Task 0.5　Spike C：静态实体渲染一致性 + OOD 实证

**前置**：Task 0.4，渲染器运行中。**预计耗时**：25 min。
**产出**：`/mnt/artifacts/stage2/spikes/spike_actors/`（2 个单变体闭环 + 抽帧）。
本 spike **手工以最小临时代码**验证注入，不追求补丁工程化（工程化在 Phase 2）：

1. 先做**零代码 prompt-only 验证**：wizard 支持直接 override endpoint prompt：

```bash
source "$HOME/simulation/scripts/env.sh"
BASE_CLIP="$(cat /mnt/artifacts/stage2/base_scene.txt)"
cd "$HOME/simulation/repos/alpasim"
# 配置渲染检查
CUDA_VISIBLE_DEVICES=0 uv run --no-sync --project src/wizard alpasim_wizard \
  deploy=external_video_model topology=1gpu driver=alpamayo15_1cam_local +chunking=8frame \
  "scenes.scene_ids=['$BASE_CLIP']" 'wizard.external_services.renderer=["127.0.0.1:50051"]' \
  +runtime.endpoints.startup_timeout_s=900 wizard.run_method=NONE \
  'runtime.renderer.video_model_config.text_prompt_positive=photorealistic dashcam, an escaped dairy cow standing motionless in the center of the lane, daytime' \
  wizard.log_dir=/mnt/artifacts/stage2/spikes/cow_prompt_configcheck
```

确认生成配置中 prompt 已渲染，再去掉 `wizard.run_method=NONE` 跑真实闭环（log_dir
`/mnt/artifacts/stage2/spikes/cow_prompt_run`），验证 prompt-only 的 cow 视觉质量（预期：可见但不稳定）。

2. **box 注入手工验证**：按 Phase 2 的目标实现，在工作树临时改
   `unbound_rollout.py`（合并一个 OTHER 2.4×1.0×1.6 恒定轨迹对象）后跑 20s 闭环；
   跑完立即 `git -C repos/alpasim checkout -- src/runtime/alpasim_runtime/unbound_rollout.py`
   恢复工作树，并确认 `git status` 干净。两个镜头：cow（OTHER）与 T-Rex（OTHER 6×2.5×5）。

3. 每个 run 在 t=4/6/8/12s 抽帧到 spikes 目录，人工（由执行 Agent 按图像）按以下 rubric 打分：

| 分 | 标准 |
|---|---|
| 2 | 实体在全部抽样帧保持一致外观与位置，无明显融化/闪烁 |
| 1 | 实体可辨认但有 artifacts（变形、闪烁、部分消失） |
| 0 | 崩坏（不可辨认、与路面融合、场景整体畸变） |

**判定（check_stage2_task05.py，[SUCCESS]）**：
- cow box 注入 ≥1 分且不低于 prompt-only → v08 保留 box 注入路线；
- T-Rex 均值 ≥1 → v07 保留；=0 → **v07 降级**为 `v07_construction_excavator`（OTHER 大 box
  3.5×1.5×3.0 + prompt "a large stopped excavator construction vehicle blocking the road"），
  规格文件直接写降级后的版本，T-Rex 仅作为报告附录的探索性尝试保留 1 个 spike 视频。
- 同时验证 is_static=False 恒定轨迹对象能被渲染器接收（即 §2.2 表 3 的假设成立）；
  若不成立（对象不可见）→ 静态障碍整体降级为 prompt-only 路线并标注「碰撞几何无对应实体，collision 指标对该变体不适用」。

**收尾**：停止渲染器，确认两 GPU = 14 MiB、工作树干净、无遗留容器。

**Phase 0 出口条件**：Task 0.1–0.5 全部 [SUCCESS]；base_scene.txt、baseline digest、
v07 保留/降级结论、启动方式结论四件齐备。

## Phase 1 —— 变体描述符与清单（纯配置生成，不碰 repos/）

### Task 1.1　变体规格生成器与权威 YAML

**前置**：Phase 0 完成。**预计耗时**：10 min。
**产出**：`configs/stage2/variants.yaml`、`configs/stage2/manifests/stage2_variants.csv`、
`scripts/generate_stage2_variants.py`。

编写 `scripts/generate_stage2_variants.py`（用 alpasim venv，仅依赖 stdlib+PyYAML；
若该 venv 无 yaml 则脚本只写 JSON 再由 bash 转——优先直接检查 `python -c "import yaml"`）：

- 内置 §5.2 全部 11 个变体定义（id/category/prompt_positive/prompt_negative/actors）；
- actor 字段：`label_class, box, start_xy_rig, velocity_xy, yaw_deg, activate_s, box_size_xyz`；
- 输出：
  - `configs/stage2/variants.yaml`（人类可读权威文件）；
  - `configs/stage2/manifests/stage2_variants.csv`：列
    `index,variant_id,scene_id,prompt_id,n_actors,activate_s,seed`（scene_id 列统一填 base scene）；
  - 自动断言：11 行；每个非 baseline 变体 prompt 非空且不含双引号外的非法 YAML 字符；
    激活时刻 ∈[2.5, 10]s；box 各维 >0 且 ≤8m；速度绝对值 ≤5 m/s；
    横穿对象轨迹在 20s 末不超出 clip 合理范围（y ∈ [-8, 8]m，越界裁剪长度并告警）；
    T-Rex 若在 spike 被降级则生成器中不得出现（以 `base_scene_geom.json` 同目录的
    `spike_verdict.json` 为准，由 Task 0.5 写出）。

执行：

```bash
source "$HOME/simulation/scripts/env.sh"
BASE_CLIP="$(cat /mnt/artifacts/stage2/base_scene.txt)"
"$HOME/simulation/repos/alpasim/.venv/bin/python" "$HOME/simulation/scripts/generate_stage2_variants.py" \
  --base-scene "$BASE_CLIP" --seed 20261002 \
  --out-yaml "$HOME/simulation/configs/stage2/variants.yaml" \
  --out-csv "$HOME/simulation/configs/stage2/manifests/stage2_variants.csv"
bash -n "$HOME/simulation/scripts/generate_stage2_variants.py" 2>/dev/null || true
```

**校验（check_stage2_task11.py，[SUCCESS]）**：YAML 可解析且恰好 11 个变体；
v00 prompt = `external_video_model.yaml:63` 原文（逐字比对）；v01–v10 prompt 与 §5.2 一致；
注入规格表行数与 §5.2 相等；密钥扫描。打印
`[SUCCESS] Stage2 Task 1.1: 11 variant descriptors generated and schema-valid.`

**回滚**：`rm -rf configs/stage2` 重跑即可。

## Phase 2 —— 本地补丁（feature-flag，默认关闭）

### Task 2.1　补丁 1：合成 TrafficObject 合并

**前置**：Task 1.1。**预计耗时**：40 min。**目标文件（补丁形式）**：

- 新增 `src/runtime/alpasim_runtime/stage2/__init__.py`、`variant_actors.py`（补丁新增文件）；
- 修改 `src/runtime/alpasim_runtime/unbound_rollout.py`（一处 guarded 调用）。

实现指引（详见 §7.1）：`variant_actors.py` 提供
`merge_synthetic_actors(traffic_objects, scene_id, rollout_index, rig_gt_trajectory,
render_start_us, end_us) -> TrafficObjects`：

1. env `STAGE2_VARIANT_SPEC` 空/文件不存在 → 原样返回；
2. 读 variants.yaml，按 `STAGE2_VARIANT_ID` 或 rollout_index 选中变体；
   scene_id 不匹配则原样返回；
3. 对每个 actor：以 rig GT 轨迹在 render_start_us 的位姿做 rig→全局变换，
   按激活时刻到 end_us 以 30fps 生成全局 `Trajectory`（静止=恒定 pose；运动=匀速+yaw），
   构造 `TrafficObject(track_id="s2_"+lid, aabb=AABB(*box), trajectory=...,
   is_static=False, label_class=label)`；
4. 返回 `TrafficObjects({**traffic_objects, **synthetic})`。

`unbound_rollout.py` 修改点（:268 filter 之后、:277 hidden 计算之前插入，
并在 `return UnboundRollout(... traffic_objs=...)` 处使用合并后的对象）：

```python
import os as _os
if _os.environ.get("STAGE2_VARIANT_SPEC"):
    from alpasim_runtime.stage2 import variant_actors
    traffic_objects = variant_actors.merge_synthetic_actors(
        traffic_objects, scene_id=scene_id, rollout_index=_rollout_index,
        rig_gt_trajectory=gt_ego_trajectory,
        render_start_us=timing.render_start_timestamp_us,
        end_us=timing.end_timestamp_us,
    )
```

`_rollout_index` 的来源是本 Task 的 spike 点：检查 `from_scene_data_source` 的调用链
（runtime simulate / session 创建处）是否带 rollout 序号；grep
`UnboundRollout.from_scene_data_source(` 的调用方参数。若序号不可得，补丁在
`variant_actors` 内用「按 scene_id + 模块级计数」不行（跨进程），
则采用方案：**只支持 `STAGE2_VARIANT_ID` 单变体选择**（最稳），
正式批量改为 11 次 wizard（见 Task 3.2 fallback）。结论与选择必须在本 Task 定死并写入
`/mnt/artifacts/stage2/spikes/rollout_index_verdict.txt`。

**单元测试（补丁一并新增）**：`src/runtime/tests/stage2/test_variant_actors.py`
（对齐现有 `tests/video_model/test_video_model_dynamic_actors.py` 风格）：
flag 未设时恒等；坐标变换正确性（rig 系 (5,0) 经已知位姿落预期全局点）；
时间窗起止 = [激活帧, end]；30fps 采样数 = 期望帧数；静止对象轨迹恒定。

**执行**：

```bash
source "$HOME/simulation/scripts/env.sh"
cd "$HOME/simulation/repos/alpasim"
# 工作树必须干净（除 Stage1 已应用补丁）；先在临时分支无关，直接写工作树，补丁权威副本稍后导出
uv run pytest src/runtime/tests/stage2 -q
uv run python -m py_compile src/runtime/alpasim_runtime/unbound_rollout.py \
  src/runtime/alpasim_runtime/stage2/variant_actors.py
```

**校验（check_stage2_task21.py，[SUCCESS]）**：新模块可 import；pytest 全绿；
flag 未设时对一组构造 TrafficObjects 恒等（测试断言）；补丁文件可 `git apply -R` 再
`git apply`（脚本在临时 worktree 副本中验证：`cp -r` 仓库太重，改为
`git diff > /tmp/x.patch && git apply -R /tmp/x.patch && git apply /tmp/x.patch`）。

### Task 2.2　补丁 2：per-rollout prompt + random_seed 贯通

**前置**：Task 2.1。**预计耗时**：30 min。**修改（补丁）**：
`src/runtime/alpasim_runtime/services/video_model_service.py` 两处。

1. `_initialize_session`（:444-449 的 `await self.start_session(...)`）：
   flag 存在时，从 variants.yaml 按 scene_id + rollout 序号/`STAGE2_VARIANT_ID`
   解析该变体 prompt，作为 `text_prompt_positive=` 传入（start_session 已支持该参数，:215/:234-238）；
2. 同处把 seed 写入 request：给 `start_session` 的 request 构造（:240-249）加
   `random_seed=int(os.environ.get("STAGE2_RENDER_SEED") or session_seed or 0)`
   （session_seed 从 session_info/cfg 取；取法 grep `session_seed` 在 services/session_configs.py；
   取不到则只认 STAGE2_RENDER_SEED，未设保持 0=服务器自选——Stage 1 行为不变）。

**测试**：新增 service 级单测（mock start_session 断言传入的 prompt/seed）；
flag 未设时不传 prompt、不设 seed（断言字段缺省）。

**校验（check_stage2_task22.py，[SUCCESS]）**：pytest 绿；
`wizard.run_method=NONE` 在「flag 未设」时生成配置与基线配置 diff 仅含允许项
（runtime 不改配置，预期无 diff）。

### Task 2.3　环境变量进容器链路 + run_method=NONE 联调

**背景**：runtime 在容器内运行，env 必须由 wizard 注入。**预计耗时**：30 min。

1. 参照 `configs/local-patches/wizard-configs/extras/a15_weights.yaml`（driver env 注入）
   查清 runtime 各服务（尤其 runtime/physics 所在容器）的 environment/volumes 在 wizard 配置中的
   位置（grep `environment:` 于 `src/wizard/configs/`），新建
   `configs/stage2/extras/stage2_env.yaml`：注入三个 STAGE2_* 变量；
   variants.yaml 需要被容器读到 → 放 `/mnt` 并确认 runtime 容器已挂 /mnt
   （查默认 volumes 列表；未挂则在 extras 补挂，注意 list 替换语义，必须列全默认卷——
   见 CLAUDE.md §5 与记忆库 hydra quirk）。
2. 联调命令：

```bash
source "$HOME/simulation/scripts/env.sh"
BASE_CLIP="$(cat /mnt/artifacts/stage2/base_scene.txt)"
cd "$HOME/simulation/repos/alpasim"
STAGE2_VARIANT_SPEC="$HOME/simulation/configs/stage2/variants.yaml" \
STAGE2_VARIANT_ID=v05_stroller_jaywalking \
STAGE2_RENDER_SEED=20261002 \
CUDA_VISIBLE_DEVICES=0 uv run --no-sync --project src/wizard alpasim_wizard \
  deploy=external_video_model topology=1gpu driver=alpamayo15_1cam_local +chunking=8frame \
  "scenes.scene_ids=['$BASE_CLIP']" 'wizard.external_services.renderer=["127.0.0.1:50051"]' \
  +runtime.endpoints.startup_timeout_s=900 wizard.run_method=NONE \
  'wizard.repo_root='"$HOME/simulation/configs/stage2/extras" \
  wizard.log_dir=/mnt/artifacts/stage2/spikes/patch_configcheck
# 若 extras 不是这样引用：按 spike 实测的 hydra 组合方式（defaults 或 +config）调整
```

断言生成的 compose/渲染配置中：runtime 容器 environment 含三个变量；
prompt override 将出现在 session 请求（配置中不体现则跳过此项，以真实闭环日志为准）。

**校验（check_stage2_task23.py，[SUCCESS]）**：配置文件含全部三个 env；
含 /mnt 挂载；n_concurrent_rollouts 全 1（grep 生成配置）。
**失败处置**：extras 机制无法注入 runtime 容器 → fallback 把 variants.yaml 经已有挂载点
（若容器已挂 /mnt 则只需 env；env 无法注入则退回「docker compose run 时 shell export
经 wizard 进程继承」验证——wizard 进程环境通常会透传给 compose，此为首选实测项，
顺序反过来：先测「shell 环境变量自动继承」，成立则连 extras 都不需要）。

### Task 2.4　补丁导出 + Stage 1 回归（门禁第 2 次）

**预计耗时**：30 min + 8 min 回归。

1. 从工作树导出权威补丁到 outer 仓库（新增文件纳入补丁需 `git add` 后 diff）：

```bash
source "$HOME/simulation/scripts/env.sh"
cd "$HOME/simulation/repos/alpasim"
git add src/runtime/alpasim_runtime/stage2 src/runtime/tests/stage2
git diff HEAD -- src/runtime/alpasim_runtime/unbound_rollout.py \
  src/runtime/alpasim_runtime/services/video_model_service.py \
  > "$HOME/simulation/configs/local-patches/patches/alpasim-stage2-001-combined.patch"
git diff --cached HEAD >> "$HOME/simulation/configs/local-patches/patches/alpasim-stage2-001-combined" \
  2>/dev/null || true
# 校验补丁在干净状态可重新 apply（按 README 模式人工核对）
```

（补丁拆分/组织按执行时实际 git 状态，对齐 README 现有编号习惯；保证两个修改点各自
可独立 revert。）

2. **flag 全不设**重跑 §3.3 回归闭环（渲染器直启/原启按 Task 0.3 结论），
采集 `regression/01_postpatch/digest.json`，运行 `check_stage2_regression.py` 比对 00_baseline。

**校验（check_stage2_task24.py，[SUCCESS]）**：回归 [SUCCESS] REGRESSION；
补丁文件存在且非空；密钥扫描。打印 `[SUCCESS] Stage2 Task 2.4: patches exported, Stage1 regression intact.`

## Phase 3 —— 批量串行闭环（11 变体）

### Task 3.1　正式批量 run（首选：一次 wizard × n_rollouts=11）

**前置**：Phase 2 全部 [SUCCESS]。**预计耗时**：~50 min（11×3.4min + 渲染器加载）。
**产出**：`/mnt/artifacts/stage2/run_stage2_20261002/`。

新建 `scripts/run_stage2_batch.sh`（结构对齐 `scripts/run_batch_a15.sh`：
docker 组重入、渲染器自动拉起与就绪轮询、串行 override），关键差异：

- 渲染器启动按 Task 0.3 结论选择（直启则调 `scripts/start_renderer_direct.sh`）；
- wizard 调用增加：

```bash
export STAGE2_VARIANT_SPEC="$HOME/simulation/configs/stage2/variants.yaml"
export STAGE2_RENDER_SEED=20261002
# ...
CUDA_VISIBLE_DEVICES=0 uv run --no-sync --project src/wizard alpasim_wizard \
  deploy=external_video_model topology=1gpu driver=alpamayo15_1cam_local +chunking=8frame \
  "scenes.scene_ids=['$BASE_CLIP']" 'wizard.external_services.renderer=["127.0.0.1:50051"]' \
  runtime.simulation_config.n_rollouts=11 \
  runtime.nr_workers=1 \
  runtime.endpoints.renderer.n_concurrent_rollouts=1 \
  runtime.endpoints.driver.n_concurrent_rollouts=1 \
  runtime.endpoints.physics.n_concurrent_rollouts=1 \
  runtime.endpoints.controller.n_concurrent_rollouts=1 \
  runtime.endpoints.trafficsim.n_concurrent_rollouts=1 \
  +runtime.endpoints.startup_timeout_s=900 wizard.timeout=14400 \
  eval.allow_aggregation_with_failed_rollouts=true \
  "wizard.run_name=stage2_20261002" \
  "wizard.log_dir=/mnt/artifacts/stage2/run_stage2_20261002"
```

- 若 wizard 对「同 scene 多 rollout」产出的目录/ASL 命名有序号，记录映射；
  补丁的 rollout_index 必须与该序号一致（Task 2.1 verdict 已定；若不一致，
  本次 run 作废并改走 Task 3.2）。

执行：

```bash
source "$HOME/simulation/scripts/env.sh"
sg docker -c "bash $HOME/simulation/scripts/run_stage2_batch.sh" \
  > /mnt/artifacts/stage2/batch_runner.log 2>&1 & disown
# 轮询：每 60s tail 日志直到出现完成标志（监控 wizard 日志最后 summary）
```

### Task 3.2　Fallback：11 次独立 wizard（每次 STAGE2_VARIANT_ID 指定）

仅在 Task 2.1 verdict = 「rollout 序号不可用」或 Task 3.1 映射不一致时执行。
`scripts/run_stage2_separate.sh` 循环 manifest 的 11 行，每次：
`STAGE2_VARIANT_ID=<id>` 调 `run_closed_loop` 等价命令，log_dir
`/mnt/artifacts/stage2/run_stage2_20261002/<id>`。预计耗时：11×(3.4 + ~3min 模型/渲染器
session 重启) ≈ 70–80 min。渲染器宿主机进程不必重启（session 由 start_session 重建），
driver 容器每次由 compose 重建。

### Task 3.3　批量结果完整性校验

**校验（check_stage2_task33.py，[SUCCESS]）**：
- 恰好 11 个 rollout 目录，每个含 `rollout.asl`（>100MB）、`metrics.parquet`、`_complete`；
- 每个 rollout 帧数 = 75（±2）、`img_is_black` 全 False；
- 从 ASL 的 `video_model_session_request`（grep 日志解析）断言：prompt 与 variants.yaml
  对应变体逐字一致（v00 与默认一致）、`random_seed=20261002`；
- 有注入的变体（v05–v10）其 `traffic_session_request` 中 actor 数 = 规格数（含 EGO）；
  weather 变体 actor 数与 base clip 原对象数一致（无多余）；
- 密钥扫描；无 FAILED/异常退出 rollout（失败也必须是完整 rollout + 事件记录，
  不允许缺失——缺失即 FAIL 本 gate）。

打印 `[SUCCESS] Stage2 Task 3.3: 11 serial rollouts complete, prompts/actors/seed verified.`

**收尾**：渲染器保留供 Phase 4（继续读 ASL 不需要渲染器；若渲染器空闲可先停止，
Phase 4 纯离线）→ 本 Task 后停止渲染器并确认 GPU 归位。

## Phase 4 —— 帧抽取、BEV、HUD 视频合成（纯离线）

### Task 4.1　从 ASL 导出 30fps 原始帧

**预计耗时**：20 min。**产出**：`/mnt/artifacts/stage2/frames/<variant_id>/`。

```bash
source "$HOME/simulation/scripts/env.sh"
cd "$HOME/simulation/repos/alpasim"
for d in /mnt/artifacts/stage2/run_stage2_20261002/rollouts/*/*/; do
  vid="$(basename "$(dirname "$d")")"
  # 用变体映射把 rollout uuid → variant_id（映射文件由 Task 3.3 生成：
  # /mnt/artifacts/stage2/rollout_variant_map.csv）
  name="$(awk -F, -v k="$vid" '$2==k{print $1}' /mnt/artifacts/stage2/rollout_variant_map.csv)"
  uv run asl-to-frames "$d/rollout.asl" --format frames \
    --log-save-dir "/mnt/artifacts/stage2/frames/$name"
done
```

**校验（check_stage2_task41.py，[SUCCESS]）**：11 个目录，每个帧数 ∈ [580, 610]；
帧分辨率 = 1280×704（抽查首帧，PIL/cv2）；非黑屏（std>10）。

### Task 4.2　BEV 生成（两方案，A 目标 / B fallback）

**方案 A（离线绘制 top-down BEV，首选）**：从 ASL 回放中取 map 线与 actor/ego 轨迹
（数据源：accumulator 解析同一 ASL，复用 eval 已验证的解析路径），用 cv2/matplotlib 画
白底黑线车道、actor box（注入=红框、录制=灰框）、ego 绿框、策略预测轨迹黄线
（driver 事件中的轨迹；字段 spike：grep ASL 内 driver 相关 LogEntry 类型）。
每 control step 一帧 BEV，帧间按最近时间戳广播到 30fps。

**方案 B（复用原生复合视频的 BEV 区域）**：从 rollout 目录的 900×1000 mp4
裁出顶部 BEV 区（约 x[70,660], y[35,560]，坐标按 Task 0.5 抽帧实测固定），
缩放为 260×260 画中画；仅 3.75fps，按帧时间戳在 30fps 上保持显示。
该方案 BEV 不含当帧预测轨迹黄线以外的自定义信息，但与论文 Fig.12 完全同源、最可靠。

**判定**：方案 A 跑通则用 A（信息更全、30fps）；任一数据字段取不到即降级 B，
不许混合出两套。check_stage2_task42.py [SUCCESS]：每变体 BEV 帧数与相机帧数对齐
（±1）、BEV 非空白。

### Task 4.3　HUD 叠加与成片

**预计耗时**：25 min。**产出**：`scripts/render_stage2_hud.py`、
`/mnt/artifacts/stage2/videos/<variant_id>_hud.mp4`（11 个）。

管线与布局规范（详见 §7.5）：

- 画布 1280×704@30fps；PIL（DejaVuSans.ttf / Bold）绘制文字，英文；
- 顶部条（高 54px，半透明黑）：左 `STAGE2 | <variant_id>`，右 `t=xx.xs`；
  第二行小字 prompt 前 90 字符；
- 右上角 BEV 画中画 260×260（含 2px 白框 + 标题 "BEV (AlpaSim)"），位置 y=62；
- 底部条（高 58px）：`SPEED xx.x km/h　STEER +xx.x deg　OBS DIST xx.x m　[STATE]`，
  STATE ∈ NORMAL / CAUTION(obs<12m) / EMERGENCY(obs<5m 或 collision)，绿/橙/红；
- 数值源：`SPEED` 从 dist_traveled 差分（或 ASL ego dynamics，spike 取字段）；
  `STEER` 从 plan_deviation/ASL；`OBS DIST` = `min_distance_to_obstacle_m` 逐帧
  （无 actor 帧显示 "--"）；collision/offroad 用布尔事件；
- 输出：帧序列临时目录（/mnt）→ ffmpeg：

```bash
ffmpeg -y -framerate 30 -i /mnt/artifacts/stage2/hud_tmp/%06d.jpg \
  -c:v libx264 -pix_fmt yuv420p -crf 20 -preset medium \
  /mnt/artifacts/stage2/videos/<variant_id>_hud.mp4
```

**校验（check_stage2_task43.py，[SUCCESS]）**：11 个 mp4；分辨率 1280×704、
时长 19.5–20.1s、帧数 ≥580；抽样 t=1/5/10/19s 帧非黑、std>10、文字区域存在非背景像素
（HUD 条上沿行均值断言）；全部可被 ffprobe 正常读取。

**收尾**：删除/保留 hud_tmp（磁盘 <40G 占用则删）；GPU 本阶段全程不用，确认仍 14 MiB。

## Phase 5 —— 评测、报告、最终回归与收尾

### Task 5.1　逐帧分析脚本与对照表

**预计耗时**：20 min。**产出**：`scripts/analyze_stage2.py`、
`/mnt/artifacts/stage2/report/per_variant.csv`、`reaction_table.csv`。

分析口径（§8 全文）：每个变体从 metrics.parquet 取
collision 各部位首帧/帧数、offroad 首帧/帧数、min_distance_to_obstacle_m 最小值与激活后轨迹、
dist_to_gt_trajectory 最大值、progress；派生：首次减速时刻（SPEED 下降沿）、
最大减速度、横向位移、派生 TTC 曲线；与 v00 baseline 对照。
对天气变体额外报告「反应窗口」内（激活后 0–8s）速度变化。

```bash
source "$HOME/simulation/scripts/env.sh"
"$HOME/simulation/repos/alpasim/.venv/bin/python" "$HOME/simulation/scripts/analyze_stage2.py" \
  /mnt/artifacts/stage2/run_stage2_20261002 \
  --variants "$HOME/simulation/configs/stage2/variants.yaml" \
  --out-dir /mnt/artifacts/stage2/report
```

**校验（check_stage2_task51.py，[SUCCESS]）**：两份 CSV 各 11 行；
所有数字非 NaN（无意义格允许空字符串但不允许 NaN 落盘）；
v00 行事件统计与 regression baseline digest 一致。

### Task 5.2　Stage2 报告与 HTML 展板

**预计耗时**：25 min。**产出**：
`docs/Stage2_Report.md`（outer 仓库跟踪）、
`/mnt/artifacts/stage2/report/index.html`（展板，相对路径引用 ../videos/*.mp4）。

报告必含：范围与裁剪声明（§1.2 原样精神）、论文 vs 实现对照、11 变体结果总表、
每变体 2–4 句事实描述（反应时刻+事件+距离，引用具体帧/秒）、天气变体「视觉-only，
物理未建模」显著标注、OOD spike 结论（含 T-Rex 降级经过，如发生）、通过率与结论、
复现命令（本计划中的脚本调用）、遗留开放问题。

HTML 展板：单文件、内联 CSS、表格 + `<video controls>`、无外部依赖、无任何 token。

**校验（check_stage2_task52.py，[SUCCESS]）**：报告存在 >4KB 且含 11 个 variant id；
HTML 存在、引用的 11 个 mp4 路径全部真实存在；密钥扫描（含报告/HTML/脚本/配置）。

### Task 5.3　产物清点与（可选）commit

**预计耗时**：10 min。

```bash
source "$HOME/simulation/scripts/env.sh"
du -sh /mnt/artifacts/stage2
find /mnt/artifacts/stage2 -maxdepth 2 -type d | sort
# 清理临时帧（保留 asl + HUD 成片 + report + frames 可选）
[ "$(df --output=avail -BG /mnt | tail -1 | tr -dc 0-9)" -gt 60 ] || rm -rf /mnt/artifacts/stage2/hud_tmp
```

**仅当用户明确要求时** commit outer 仓库：暂存
`scripts/run_stage2_batch.sh scripts/analyze_stage2.py scripts/render_stage2_hud.py
scripts/generate_stage2_variants.py scripts/check_stage2_*.py scripts/check_stage2_collect_digest.py
scripts/check_stage2_regression.py configs/stage2 configs/local-patches docs/Stage2_Report.md
docs/Stage2_Plan_detailed.md`，commit message 示例
`docs+scripts: Stage2 counterfactual long-tail variants, HUD videos and evaluation`，
末尾 `Co-Authored-By: Claude Code <noreply@anthropic.com>`；提交后
`git log -1 --format='%an <%ae>'` 必须为 `Junchuan Zhang <zjunchuan@gmail.com>`。

### Task 5.4　最终回归（门禁第 3 次）与全量收尾

**预计耗时**：15 min（8min 回归 + 收尾）。

1. **flag 全不设**跑 §3.3 回归 → `regression/02_final/digest.json`，
   check_stage2_regression.py 两两次比对（对 00、01），[SUCCESS] REGRESSION；
2. 停止渲染器；确认：

```bash
source "$HOME/simulation/scripts/env.sh"
bash "$HOME/simulation/scripts/stop_renderer.sh"
sleep 5
nvidia-smi --query-gpu=index,memory.used --format=csv,noheader   # 14 MiB ×2
dk ps -aq                                                         # 空
pgrep -af "omnidreams|alpasim_wizard" || echo "no leftovers"
git -C "$HOME/simulation/repos/alpasim" status --short | grep -v '^??' | head
```

3. check_stage2_task54.py [SUCCESS] 条件：两 GPU 基线、无容器、无后台、
三次回归 digest 齐备且互相通过、全部 check_stage2_task*.py 可重新运行并 [SUCCESS]
（脚本汇总顺序执行一遍）。打印
`[SUCCESS] Stage2 Task 5.4: final regression passed, all resources released, Stage2 complete.`

---

# 7. 关键技术点实现指引

## 7.1 合成 TrafficObject 的构造（补丁核心）

- **rig→全局变换**：`rig.trajectory` 与 TrafficObject 轨迹同在 clip 全局系。
  取 render_start_us 的 rig 位姿 `T0`（`Trajectory.interpolate_poses_list([ts])`）；
  对 rig 系偏移 `(x,y)`：全局位置 = `T0.vec3 + R0 @ [x,y,0]`；
  rig 系单位朝向（x 轴）经 `R0` 得全局 yaw。**轴向手别（y 左/右）在 Task 0.4
  用一个已知事实验证：取 GT ego 相邻两帧位置差，其在 rig 系应近似 (+d, 0)**
  （在 delta pose 的局部表示中），以此钉死约定并写进变体生成器注释。
- **轨迹生成**：时间戳 = 激活时刻对应帧起、步长 33,333µs 至 end_us；
  静止对象每帧同一全局 pose；横穿对象每帧 `(x0, y0 + vy*t')`、yaw = 速度方向；
  用 `Trajectory.from_poses(timestamps, [Pose(pos, quat_identity_or_yaw)...])`。
- **必须 is_static=False**：理由 `utils.py:127-129`。对 trafficsim 的影响：
  trafficsim 会话 replay 全程 logged 轨迹（handover 前），不受 is_static 影响；
  CATK 对静止对象预测=原位（spike 时从 traffic return 抽查确认）。
- track_id 加 `s2_` 前缀避免与录制对象 id 冲突；重复 id 时后写覆盖（dict 合并语义，
  正好可用于「用合成对象顶替某录制对象」，本阶段不启用该用法）。
- hidden_traffic_objs 机制（unbound_rollout.py:277-293）保持不动；合成对象
  不参与 hidden 下沉。

## 7.2 per-rollout prompt

- prompt 在渲染器是**会话级**条件（conditioning wrapper 每次生成读 session 中的
  text_prompts，`conditioning_wrapper.py:406` 断言 batch=1），因此必须在
  `start_session` 之前确定；补丁挂在 `video_model_service.py:444` 的调用点。
- prompt 解析与 actor 选择共用同一个变体解析函数（新模块 stage2/variants.py：
  `resolve_variant(scene_id, rollout_index) -> VariantSpec|None`），保证 prompt 与
  actor 永远来自同一变体，杜绝错配。
- v00 baseline 不提供 override → 走 config 默认（即 Stage 1 原 prompt）。

## 7.3 random_seed 贯通

- 链路：session seed（runtime 创建 rollout 时确定）→
  `SessionRequest.random_seed`（当前断链，video_model_service.py:240-249）→
  渲染器 `server.py:959`。补丁后 STAGE2_RENDER_SEED 优先、session seed 次之。
- 注意 driver/trafficsim 各自已有 seed（traffic_service.py:94-98 等），本补丁
  只修渲染器一侧；三处统一读同一数值才能完整可复现（driver seed 的来源在
  wizard config；Task 2.3 联调时 grep 确认，不一致以渲染器逐帧一致性实证为准——
  若两次同 seed 渲染 dist_to_gt 序列在容差内，即接受）。

## 7.4 原生复合诊断视频（现成可参考物）

900×1000@3.75fps 结构（2026-10-02 抽帧实测）：顶部左 = BEV（map 线 + ego 绿框）、
顶部右 = Agg vs Per-Ts 指标表（含 offroad/collision/dist/progress 等行）、
底部 = 相机画面。其生成代码在 eval 的 video 模块（`src/eval/src/eval/video.py`）。
HUD 视频是对它的「30fps 增强重制版」而非替代——报告中两者都引用：
HUD 用于演示，eval 复合视频与逐帧 parquet 用于取证。

## 7.5 HUD 管线规范

- 读帧：cv2.imread（BGR）→ 转 RGB 交 PIL；DejaVuSans.ttf（正文 18px/小字 15px/
  Bold 标题 22px）；`ImageDraw.text`。
- 半透明条：直接在帧上填 RGFC overlay（alpha=0.65）后合成；避免每帧全图 addWeighted。
- 画中画：BEV 260×260，外白框 2px；放右上 (W-260-20, 62)，与顶部条留 8px。
- 文字安全：全部 ASCII；prompt 截断按字符数不按词；STATE 配色：
  NORMAL=(70,200,120)、CAUTION=(255,170,40)、EMERGENCY=(240,70,70)。
- 时间对齐：HUD 逐帧数据从「control step 时间戳 → 最近 30fps 帧」广播；
  帧上打印的 t 由帧序号/30 计算，不依赖其他时钟。
- 编码：H.264 yuv420p、crf 20、30fps；moov 默认在尾部即可（本地播放）。
- 备选全 ffmpeg 路线（drawtext/drawbox）可行但排版能力弱；统一用 PIL+ffmpeg，
  不引入 OpenCV VideoWriter（编码参数不可控）。

## 7.6 环境变量注入的实测顺序

1. 先测 **shell → wizard 进程 → compose 容器** 的自动继承（通常 Hydra 生成的
   compose service 不带显式 environment 时容器拿不到，但 wizard 可能把自身 env
   透传——run_method=NONE 后直接 grep 生成 compose）；
2. 不成立再用 `configs/stage2/extras/stage2_env.yaml` 显式 environment；
3. /mnt 挂载：grep 生成 compose 的 volumes，已挂则只读 variants.yaml（放
   configs 是 outer 仓库路径，容器看不到——**variants.yaml 必须复制一份到
   /mnt/artifacts/stage2/variants.yaml 供容器读**，两份以 /mnt 副本为运行时事实源，
   outer configs 副本为 git 权威；批量脚本启动前 `cp` 同步并校验 sha256）。

---

# 8. 评测方案

## 8.1 数据源铁律

- 只用逐帧 `metrics.parquet`（long format；`to_numeric(errors="coerce")`；
  同名同时间戳取 max）+ HUD/原始视频；**aggregate metrics_results.txt 仅作线索不作证据**
  （RemoveTimestepsAfterEvent 会截断事件后帧，见 CLAUDE.md §5.1）。
- 事件「是否 at fault」以 dist_to_gt_trajectory <4m 的帧为参考口径（论文协议），
  但无论是否 at fault，碰撞/offroad 帧都在报告中如实列出。

## 8.2 指标与通过率

| 维度 | 取值方式 |
|---|---|
| 碰撞 | collision_any/front/rear/lateral 的首帧、总帧数 |
| 下路 | offroad 首帧、总帧数 |
| 障碍距离 | min_distance_to_obstacle_m 全 episode 最小值 + 激活后逐帧序列 |
| 轨迹偏差 | dist_to_gt_trajectory 最大值/逐帧 |
| 通过进度 | progress / progress_rel 末值 |
| 决策反应 | SPEED 派生：首次减速时刻、激活后 3s 内最大减速度、末速；横向：STEER/plan_deviation 派生最大横向偏移 |
| 安全层 | safety_monitor_triggered（预期仍为 0；如实记录） |
| 派生 TTC | 仅当 obs 距离连续下降：TTC = dist / 接近率（ego 速度−对象速度在连线上分量），否则置 inf；报告显著标注「派生，非原生指标」 |

**单变体 PASS 定义**（天气类与 actor 类分别定义）：

- actor 类（v05–v10）：collision_any 0 帧 **且** offroad 0 帧 **且**
  激活后 min_distance_to_obstacle_m ≥0.8m **且** 激活后存在可观测减速或避让动作。
- 天气类（v01–v04）：collision_any 0 帧 且 offroad 0 帧；
  另报告感知反应（速度变化），不反应不判 FAIL（视觉-only 条件下策略选择本身是观察对象）。
- 任何 `img_is_black=True` 的帧 >2% → 该变体判 SYSTEM_ERROR，不计入通过率分母也不冒充 PASS。

总通过率 = PASS 变体数 /（11 − SYSTEM_ERROR 数），v00 单列不计入。

## 8.3 baseline 对照

`reaction_table.csv` 每变体一行，列：首减速时刻 Δ vs v00、最大减速度 Δ、
最小障碍距离、碰撞帧数 Δ、offroad 帧 Δ、dist_to_gt max Δ、PASS/FAIL。
天气变体预期效应（更慢/更保守）若未出现也如实写——**模型表现 ≠ 系统正常**：
11 个 rollout 完整、prompt/actor 链路证据一致，即系统成功；策略通过率是另一回事。

---

# 9. 风险登记册

| # | 风险 | 概率 | 影响 | 缓解 | Fallback |
|---|---|---|---|---|---|
| R1 | OOD（T-Rex）渲染崩坏/场景畸变 | 高 | 中 | Task 0.5 spike 前置、rubric 打分 | v07 降级为施工挖掘机（OTHER 大 box）；T-Rex 仅留附录 |
| R2 | 注入 actor 与画面不一致（视觉幽灵：渲染不见/位置漂移） | 中 | 高 | is_static=False 全窗轨迹；prompt 与 box 同源解析；逐 chunk 时间窗校验 | 降级 prompt-only 并标注 collision 指标不适用；或 box-only 无 prompt |
| R3 | 补丁导致 Stage 1 行为回归 | 低 | 高 | feature flag 默认关闭、三次回归门禁、补丁可独立 revert | revert 对应补丁，Stage 2 转独立分支式脚本（不碰 alpasim 源码，改在 wizard 外层包一层——能力降级，仅 prompt override） |
| R4 | 磁盘不足 | 低 | 中 | 预算 5.6GB vs 273GB；每 Task df 检查；临时帧用完即删 | 删 frames/hud_tmp，只留 asl+成片；仍不足停做报告 |
| R5 | docker daemon 配置风险 | 低 | 高 | **本计划完全不改 daemon、不重启 docker**（root 已在 /mnt） | —（不适用即无风险） |
| R6 | GFW/网络失败（HF/GitHub/NGC） | 中 | 中 | Stage 2 基本无需下载（资产已缓存）；必要时 `HF_ENDPOINT=https://hf-mirror.com`、关 hf_transfer；GitHub 走 ghfast.top 前缀 | 离线模式（HF_HUB_OFFLINE=1）；无新增下载需求 |
| R7 | torchrun 渲染器启动挂死 | 中 | 中 | Task 0.3 直启 spike + 独立 start_renderer_direct.sh | torchrun 重试；或改日运行 |
| R8 | rollout 序号不可取/与变体映射错位 | 中 | 中 | Task 2.1 verdict 定死；Task 3.3 从 ASL 逐 rollout 校验 prompt | 11 次独立 wizard（STAGE2_VARIANT_ID） |
| R9 | 渲染器单 session 被并发破坏 | 低 | 中 | 全链路 nr_workers=1、各端并发=1（run_method=NONE 检查） | 立即停并发任务重跑 |
| R10 | 同 seed 渲染仍不可复现（神经生成随机性未被 seed 控制） | 中 | 低 | seed 贯通补丁 + 逐帧 dist_to_gt 容差实证 | 回归门禁降级为事件指标+墙钟（首次回归即此标准），报告标注渲染不确定性 |
| R11 | 补丁与上游将来更新冲突（无法 apply） | 低 | 低 | 补丁最小化（两处修改点各 <15 行）、权威副本在 outer 仓库 | 手工按 §7 指引重新生成补丁 |

---

# 10. 时间表与里程碑

墙钟估算（基于 Stage 1 实测 3.4min/20s clip、渲染器加载 3–5min、模型重载 ~3min；
含人工/Agent 判读与小幅调试余量）：

| Task | 内容 | 预计耗时 |
|---|---|---|
| 0.1 | 基线快照 | 2 min |
| 0.2 | Stage1 回归基线闭环 | 8 min |
| 0.3 | Spike A 启动方式 | 15 min |
| 0.4 | Spike B base scene 测定 | 10 min |
| 0.5 | Spike C 静态实体+OOD（含 3 个 20s 闭环） | 25 min |
| 1.1 | 变体描述符/清单 | 10 min |
| 2.1 | 补丁 1 合成 actor（含测试） | 40 min |
| 2.2 | 补丁 2 prompt+seed | 30 min |
| 2.3 | 环境注入联调 | 30 min |
| 2.4 | 补丁导出+第 2 次回归 | 40 min |
| 3.1/3.2 | 11 变体批量闭环 | 50–80 min |
| 3.3 | 批量完整性校验 | 10 min |
| 4.1 | 帧抽取 | 20 min |
| 4.2 | BEV 生成 | 25 min |
| 4.3 | HUD 成片 | 25 min |
| 5.1 | 逐帧分析 | 20 min |
| 5.2 | 报告+展板 | 25 min |
| 5.3 | 清点/commit | 10 min |
| 5.4 | 第 3 次回归+收尾 | 15 min |
| 合计 | | **约 6.5–7.5 小时（可分 2–3 个工作日）** |

里程碑：

| M | 时点 | 交付 |
|---|---|---|
| M0 | Phase 0 末 | 回归基线 digest、base scene 定稿、v07 结论、启动方式结论 |
| M1 | Phase 1 末 | variants.yaml + manifest（11 行） |
| M2 | Phase 2 末 | 两个 feature-flag 补丁 + 测试 + Stage1 回归通过 |
| M3 | Phase 3 末 | 11 rollout（ASL/parquet/mp4）+ prompt/actor/seed 证据 |
| M4 | Phase 4 末 | 11 个 HUD 成片 |
| M5 | Phase 5 末 | Stage2 报告/展板、三次回归证据、资源归位 |

---

# 11. 给执行 Agent 的启动提示词（直接可用）

```text
你在 /home/vipuser/simulation 仓库工作。请通读 docs/Stage2_Plan_detailed.md 并严格执行：

1. 按 Task 0.1 → 5.4 的顺序逐个执行，任何 Task 必须先运行对应的
   scripts/check_stage2_taskN.py 并看到 [SUCCESS] 才允许进入下一个 Task；失败就停在
   当前 Task 排查或按文档的 fallback/回滚处置，不得跳 Task、不得改 Gate。
2. 所有命令先 source "$HOME/simulation/scripts/env.sh"，路径一律用绝对路径；
   长任务用 setsid 后台运行 + 日志轮询，不空等、不跑前台超 120s 的命令。
3. 对 repos/ 内代码的任何改动只能以 configs/local-patches 补丁形式存在，全部行为由
   STAGE2_VARIANT_SPEC / STAGE2_VARIANT_ID / STAGE2_RENDER_SEED 环境变量控制、默认
   关闭；每个 Phase 完成后做资源收尾（stop_renderer、两 GPU 回到 14 MiB、无遗留容器）。
4. 回归门禁在 Task 0.2、2.4、5.4 各跑一次（固定 base scene、固定 seed），用
   scripts/check_stage2_regression.py 比对 digest，容差按文档 §3.3。
5. 一切结论以逐帧 metrics.parquet 和原始/HUD 视频为证据，禁止引用 aggregate
   metrics_results.txt 下结论；天气变体必须标注「视觉-only，物理未建模」；
   不得宣称 friction/打滑等系统不存在的能力。
6. 文档/脚本/日志中严禁出现明文 token，只引用 ~/access 路径；未经我明确要求不要
   git commit/push；如获要求，author 必须为 Junchuan Zhang <zjunchuan@gmail.com> 且
   commit message 末尾加 Co-Authored-By: Claude Code <noreply@anthropic.com>。
7. 每个 Task 完成后向我简报：check 输出、关键产物绝对路径、与预期不符的发现；
   遇到「待 spike 验证」清单中的开放问题，先做 spike 并把结论写回指定路径再继续。
```

---

## 附录 A：待 spike 验证开放问题清单（进入 Phase 0 即逐项关闭）

1. rig 系坐标轴向手别（y 左正/右正）——Task 0.4 用 GT 轨迹局部差分钉死。
2. base clip 的 GT 初速/曲率/路肩宽度是否满足选样条件；不满足时改用哪个 clean 备选。
3. 渲染器直启（MASTER_ADDR env）在本机是否稳定成功；torchrun 是否仍会挂死。
4. is_static=False 恒定轨迹对象能否被渲染器稳定渲染（静止障碍注入路线成立与否）。
5. T-Rex（OTHER 6m box + prompt）的视觉一致性；是否需要按预案降级为施工挖掘机。
6. rollout 序号在 `UnboundRollout.from_scene_data_source` 调用链中是否可取
   （决定一次 wizard×n_rollouts=11 还是 11 次 wizard）。
7. STAGE2_* 环境变量经「shell→wizard→compose」的透传是否成立；不成立时 extras
   environment 的准确写法与默认卷清单。
8. 策略预测轨迹在 ASL driver 事件中的确切字段（BEV 方案 A 的黄线数据源）；
   SPEED/STEER 在 ASL 中的最佳字段（取不到则用 metrics 派生）。
9. seed 贯通后同 seed 两次渲染的逐帧一致性（dist_to_gt 序列 ≤0.25m/帧是否成立）。
10. variants.yaml 容器内读取路径（/mnt 副本同步与 sha256 校验流程）。

---

> 执行顺序总览：Phase 0（快照/回归/spikes）→ Phase 1（变体描述符）→
> Phase 2（feature-flag 补丁 + 第 2 次回归）→ Phase 3（11 变体串行闭环）→
> Phase 4（帧/BEV/HUD）→ Phase 5（评测/报告/第 3 次回归/收尾）。
> 任何 Gate 未输出 `[SUCCESS]`，一律停在当前 Task，不得进入下一步。
