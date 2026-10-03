# CLAUDE.md — Cosmos-Dreams-AlpaSim 项目指导

本文件给在本仓库工作的 AI Agent 提供明确指导。**开始任何实质性任务前先通读本文件**；
详细背景见 `docs/`（见文末"文档地图"）。

## 1. 项目是什么

自动驾驶**闭环仿真**项目：驾驶策略（driver）输出控制 → 物理/交通仿真 →
NVIDIA 神经渲染器（OmniDreams）渲染下一帧画面 → 回传给 driver，循环 20s，产出
可播放的 MP4 与逐帧指标。

- **Stage 1**：真实场景闭环（VaVAM-B / Alpamayo R1 / Alpamayo 1.5 × 单 clip / 30 场景批量）。
- **Stage 2**：在**一个 base scene** 上做 OmniDreams 论文 §9.3 的**反事实长尾变体**
  （`clipgt-02eadd92-02f1-46d8-86fe-a9e338fed0b6`，11 个变体 v00–v10）：
  文本 prompt + 首帧条件（不重训任何权重）+ 注入合成 3D 演员，hdmap 拓扑不变，
  Alpamayo 1.5 为 driver。**复现必须严格按 `docs/Stage2_Reproduction_Guide.md`**。
- 运行环境：无头服务器 `ubuntu2`，Ubuntu 22.04，2× NVIDIA A100-SXM4-80GB
  （**GPU0 = driver，GPU1 = OmniDreams renderer**），94GB RAM。
- 核心组件：AlpaSim wizard（Hydra 编排，位于 `repos/alpasim`）、Alpamayo
  R1 / Alpamayo 1.5 驾驶模型、OmniDreams gRPC 渲染服务（`repos/flashdreams`）。
- 远程：`https://github.com/GimpelZhang/Cosmos-Dreams-AlpaSim`，主分支 `main`。

## 2. 目录结构（务必先理解）

工作目录 `~/simulation`（`/home/vipuser/simulation`）：

- **被 git 跟踪**：`scripts/`、`docs/`、`configs/`、`.gitignore`、`.gitattributes`、本文件。
  - `configs/stage2/`：Stage 2 变体规格 `variants.yaml`、manifests、cutout prompts、extras；
  - `configs/local-patches/`：对内层仓库的**补丁与 wizard 配置权威副本**（重建机器时用，见 §4 开头）。
- **不被跟踪**（`.gitignore` 已排除）：
  - `repos/`：第三方代码（alpasim、flashdreams、omni-dreams、alpamayo），**不要往里提交**；
  - `artifacts/`：所有运行产物（wizard 日志、rollout、视频、aggregate；Stage 2 在 `artifacts/stage2/`）；
  - `weights`、`assets`、`caches`、`venvroot`：指向 `/mnt` 的**符号链接**；
  - `.omc/`：编排层运行状态。
- 场景数据实体在 `/mnt/alpasim-data/`，经 `repos/alpasim/data/nre-artifacts` 引用。
- Stage 2 anchor 目录（资产/变体同步）：`/mnt/artifacts/stage2/anchor/`
  （`assets/` 演员 cutout、`current_variant.txt`、`debug/`）。

## 3. 硬性规则（不可违反）

1. **大文件下载、镜像/数据构建必须落在 `/mnt`**（500GB 数据盘；根分区空间小）。
   **永远不要执行 mkfs / 重新格式化任何磁盘**。先 `df -h / /mnt` 再动手。
2. **凭据只存在于 `~/access/`**：
   - `access_methods.txt`：sudo 密码；
   - `user_access_methods.txt`：HuggingFace / GitHub / NGC token；
   - `image_editor_model.txt`：火山方舟 Ark API Key + 图像模型（Stage 2 用，格式见 §5.12）。
   - **严禁**把任何 token/密码写入被跟踪文件、commit message、docs、脚本注释或日志；
     脚本/文档中只能引用路径。提交前对改动做密钥模式扫描
     （`hf_…`、`ghp_…`、`nvapi-…`、`ark-…`）。
3. **每个 commit 的 author 必须是 `Junchuan Zhang <zjunchuan@gmail.com>`**
   （提交后用 `git log -1 --format='%an <%ae>'` 核实）。commit message 末尾加：
   `Co-Authored-By: Claude Code <noreply@anthropic.com>`。用户要求时才 commit/push。
4. 渲染器/批量容器是重型资源：任务结束后确认停止，GPU 显存回到 ~14 MiB。

## 4. 常用操作

所有脚本先 `source "$HOME/simulation/scripts/env.sh"`（单一事实源：路径、缓存、
CUDA 13、从 `~/access` 解析凭据、`sudosw`/`dk` 工具函数）。

**内层仓库补丁**：重建/新机器时，内层仓库 checkout 到 pinned commit
（alpasim `affc2ea`、flashdreams `0957cf0`、alpamayo `11a0e01`、omni-dreams `cd85f39`）
后按 `configs/local-patches/README.md` 应用补丁与 wizard 配置；两个 Stage 2 补丁
（`alpasim-stage2-001`、`flashdreams-stage2-001`）必须在 Stage 1 补丁之后应用。

| 目的 | 命令/脚本 |
|---|---|
| 单 clip 闭环：R1 | `scripts/run_closed_loop_r1.sh` |
| 单 clip 闭环：Alpamayo 1.5 | `scripts/run_closed_loop_a15.sh` |
| 批量闭环：A15（清单驱动，区别于单 clip 脚本） | `scripts/run_batch_a15.sh`（清单 `scripts/a15_batch_scenes_20261001.csv`） |
| 启动 / 停止 OmniDreams renderer（GPU1，gRPC :50051） | `scripts/start_renderer.sh` / `scripts/stop_renderer.sh` |
| **Stage 2：跑 11 变体串行闭环** | `bash scripts/run_stage2_separate.sh`（先按 Stage2 指南手动启动 renderer！见 §5.10） |
| **Stage 2：生成演员 cutout** | `scripts/stage2_make_cutout.py`（在 `repos/flashdreams` 内 uv run，见 Stage2 指南 §4.3） |
| **Stage 2：后处理 + 视频** | `RUN_DIR=<run> bash scripts/postprocess_stage2_v05v10.sh`（map→导帧→分析→BEV→MP4） |
| **Stage 2：对比网格 / 指标曲线图** | `scripts/stage2_compare_grid.py` / `scripts/stage2_plot_metrics.py`（HUD 视频用 `scripts/render_stage2_hud.py`） |
| 逐 clip 批量分析 | `repos/alpasim/.venv/bin/python scripts/analyze_batch_a15.py <log_dir> --manifest <csv>` |
| 只生成配置/下载场景、不起容器（验证 override） | wizard 加 `wizard.run_method=NONE` |

- **Docker 权限**：wizard 直接调用 `docker compose`。无 socket 权限时用
  `sg docker -c '...'` 运行（批量脚本已内置重入）；或用 `dk` 辅助函数。
- **分析脚本用 `repos/alpasim/.venv/bin/python`**（含 pandas/pyarrow/matplotlib），不要用系统 python。
- Renderer 就绪标志：日志出现 `Server started successfully. Press Ctrl+C to stop.`
- 前台 Bash 命令有约 120s 时限。长任务用
  `setsid bash -c '...' < /dev/null > /path/log 2>&1 & disown` 后台运行，
  再通过日志/等待循环确认，不要空等。

## 5. 关键坑与注意事项（都付过代价，不要重蹈）

### Stage 1 通用

1. **永远不要只信 aggregate `metrics_results.txt`**：其中
   `RemoveTimestepsAfterEvent(offroad_or_collision)`、
   `RemoveTimestepsAfterEvent(dist_to_gt_trajectory >= 4)` 等截断算子会砍掉事件后的
   所有帧——碰撞/offroad 越早，aggregate 数字反而越"好看"（批量实测：aggregate
   dist_to_gt=3.02m vs 逐帧真值 20.06m）。**验收依据**：逐帧
   `rollouts/<clip>/<rollout>/metrics.parquet`（long format；`values` 需
   `pd.to_numeric(errors="coerce")`；同 metric/时间戳多相机行取 max）+ 原始 rollout 视频。
2. **OmniDreams renderer 只保留一个活动 session**：每次 `start_session` 都会先清掉旧
   session，并发 clip 会报 `Session not found`。批量必须强制串行：
   `runtime.nr_workers=1` 且全部 `runtime.endpoints.*.n_concurrent_rollouts=1`，
   改完用 `run_method=NONE` 确认配置中全为 1。
3. **约 40% 的 26.01 USDZ 缺 `clipgt/calibration_estimate.parquet`**（video session
   必需，起步即失败）。批量选样要先按 zip 内容预检，并按 ≥1.5× 数量预下载候选补足。
4. **多 revision 目录取"最新 artifact"**：同一 scene_id 同时挂 26.01/26.04 时，wizard
   强制用最新版。要求整批版本同质时，必须按 scene_id **跨所有 revision** 过滤。
5. **Wizard/Hydra 怪癖**：不支持 `key+=value`（`+key=value` 仅在 key 不存在时可用）；
   YAML 组合对 list 是**替换不是合并**——extras 中重写 volumes 必须列全默认挂载。
6. **Gated 模型离线**：driver 容器挂了 HF cache 但无 token，访问 gated 仓库（如
   `nvidia/Cosmos-Reason2-8B`）会 401。修复是在 extras 注入 `HF_HUB_OFFLINE=1`、
   `TRANSFORMERS_OFFLINE=1`（先用 run_method=NONE 验证 environment 已渲染）。
7. **指标陷阱**：`min_distance_to_lane_boundary_m` 可能整批恒为 0、无区分度；
   `min_ade@5.0s(gt)` 在 8frame chunking 下无输出；offroad（路面区域二值标志）
   与 dist_to_gt_trajectory（轨迹偏差）是两个维度，clean clip 也可能轨迹偏差很大。
8. **模型表现 ≠ 系统正常**：30 场景批量中 Alpamayo 1.5 的 90% 出现碰撞/offroad、
   安全监控从未触发。报告模型结果时必须如实区分"链路跑通"与"驾驶质量"。

### Stage 2 专属

9. **Stage 2 逻辑静默 no-op 的三个原因**（出问题先按此排查）：① `variants.yaml` 头部
   `base_scene` 与运行场景不一致；② 环境变量未设（wizard 侧 `STAGE2_VARIANT_ID`、
   renderer 侧 `STAGE2_MULTIFRAME_ANCHOR=1`）；③ 补丁未应用。系统不会为这些报错。
10. **renderer 必须带 Stage 2 变量手动启动**（`STAGE2_MULTIFRAME_ANCHOR=1 STAGE2_VARIANT_SPEC=…
    STAGE2_ANCHOR_PATH=… bash scripts/start_renderer.sh`）。`run_stage2_separate.sh`
    发现端口未开时自动拉起的 `start_renderer_direct.sh` **不转发任何 STAGE2_* 变量**，
    会静默产出无多帧锚定的劣质结果。`STAGE2_VARIANT_ID` 故意不设，由
    `<anchor>/current_variant.txt` 逐变体切换。
11. **长闭环有两类失效，事件统计必须按"有效期"截断**（窗口表
    `configs/stage2/manifests/stage2_v05v10_valid_windows.csv`，
    用 `scripts/analyze_stage2_validwindow.py`）：
    - **family A（演员诱发巨型化）**：模型在粘贴资产外自发生成更大的续演体，
      红种/EDT 去不掉，自回归放大；OOD 越强寿命越短（v07 ~2s，v08–v10 ~5s）；
    - **family B（渲染器固有漂移）**：v00 零干预基线也在 ~6.6s（frame 199）后
      树干巨型化/车辆融化。v00 每次都必须一起跑，用于区分两类现象。
12. **Ark 凭据文件格式必须精确**（`~/access/image_editor_model.txt`）：
    `ARK_API_KEY: <key>` 用**半角冒号+空格**；`模型：doubao-seedream-5-0-pro-260628`
    用**全角冒号**。需先在火山引擎完成实名+开通方舟+开通该模型+创建 API Key。
13. **cutout 资产名必须等于演员 `id`**（`<anchor>/assets/<id>.png`，共 9 张），
    缺失起步即 `FileNotFoundError`。默认 BiRefNet matting（公开 HF 模型）。
14. **天气 prompt 改变外观但不改变驾驶**：distilled checkpoint 对 prompt-only 条件的
    动作通道不敏感（已 A/B 证实）。报告 v01–v04 时只能声称外观反事实成功，
    不得夸大为"模型在暴雪下驾驶"。
15. `STAGE2_GHOST_SCALE` 代码默认 `1.0`；`2.2` 仅 v07 实验期临时用过，最终批次已还原。

## 6. 完成标准（声称完成前自查）

- 零遗留后台任务；renderer 已按需停止，两 GPU 显存回到 ~14 MiB；
- 结论基于逐帧证据而非 aggregate；Stage 2 的事件统计按有效期截断，窗口外现象如实标注；
  改动过的脚本做了语法检查（`bash -n` / `py_compile`）和必要的实际运行验证；
- 无密钥泄漏（含 `ark-` 模式）；如已 commit，author 与 trailer 正确；
- 不允许假完成：TODO 占位、`test.skip`/`.only`、空实现都是阻塞项，要么实现要么明确上报。

## 7. 文档地图

### Stage 1

- `docs/Stage1_Architecture.md`：**代码级架构解析**（六个 gRPC 服务、事件堆闭环时序、
  每跳传输的数据与接口、OmniDreams/Alpamayo 内部实现）；
- `docs/Stage1_Driver_IO_Cycle.md`：**Driver 视角的一个输入/输出轮回**（A15 为例：
  OmniDreams/Alpasim 分别给 Driver 什么输入与接口、Driver 输出给谁、轨迹→MPC→ego
  位姿→下一帧渲染→评价报告的代码级闭环）；
- `docs/Stage1_Reproduction_Guide.md`：**从零复现用的端到端指南**（VaVAM/R1/A15 × 单次/批量，逐步可执行）；
- `docs/Stage1_Plan_detailed.md`：最初的超详细执行计划（环境事实、变量、预案）；
- `docs/Stage1_Complete_1.md`：Stage 1 完成全记录（环境搭建、VaVAM-B→R1→A15
  单 clip 闭环、踩坑、复查纠正、§7.3 批量）；
- `docs/Stage1_Batch30_Report.md`：A15 × 30 场景批量闭环报告（选样、架构、
  分析方法、完整结果、7 条教训、复现命令）；
- `docs/Stage1_Runtime_Performance.md`：**运行时性能实测报告**（峰值 RAM/显存/线程/磁盘、
  实时倍率 ~0.13×、闭环 ~3.9 fps、每帧渲染 ~110 ms、每 step ~2.04 s，基于批量+复测证据）；
- `docs/Stage1_Plan.md`：早期总体计划。

### Stage 2

- `docs/Stage2_Reproduction_Guide.md`：**Stage 2 端到端复现指南（首要参考）**——
  补丁应用、Ark/`doubao-seedream-5-0-pro-260628` 权限办理、cutout、11 变体运行、
  有效期分析、验收与拆除；
- `docs/Stage2_Complete.md`：**完整复盘**（目标、方法、run-g 方案、两类失效、
  妥协与局限、踩坑记录）；
- `docs/Stage2_Report_v05v10_localpatch.md`：v05–v10 最终批次报告（局部 latent patch
  版本、有效期指标、逐变体结果）；
- `docs/Stage2_Report.md`：早期主批次报告；
- `docs/Stage2_Plan_detailed.md` / `docs/Stage2_Plan.md`：Stage 2 详细计划与早期计划。
