# Stage 1 实施复盘：VaVAM-B + OmniDreams 闭环（Complete-1）

> 时间：2026-09-27 → 2026-10-01
> 验收状态：Task 0 → Task 5 全部 `[SUCCESS]`
> 交付物：`artifacts/stage1_closed_loop.mp4`（20 帧 / 10fps）、`artifacts/raw_frames/`（20 张 PNG）、wizard 原始 rollout（75 帧 @37.5fps）
> 本文记录此阶段的操作经验、踩坑与修复，供后续 Alpamayo-R1 + NRE 升级及机器重建时参考。

---

## 1. 环境基线（最终状态）

| 项目 | 实际值 |
|---|---|
| GPU | 2× NVIDIA A100-SXM4-**80GB**（driver 580.178.04，CUDA 13.0） |
| OS | Ubuntu 22.04，内核 6.8.0-138-generic，94GB RAM |
| 数据盘 | `/dev/vdb` 500GB xfs，挂载 `/mnt`（fstab: `defaults,noatime,nofail`，UUID `f926c9bf-…`） |
| Docker | data-root = `/mnt/docker-data`（`/etc/docker/daemon.json`） |
| 仓库 | `~/simulation`（outer repo），`repos/{alpasim,flashdreams}` 为内层 git 仓库 |
| 版本 | AlpaSim 0.134.0（uv workspace，Python 3.12）；flashdreams venv 中 torch **2.12.1+cu130** |

**存储布局（outer repo 内全部为指向 /mnt 的软链，已被 .gitignore 忽略）：**

```
weights   -> /mnt/weights      # R1 22GB、VaVAM 1.7GB 等权重
assets    -> /mnt/assets       # NuRec usdz 场景（1.7GB+）
caches    -> /mnt/caches       # HF_HOME、torch、triton、pip、uv、cargo、rustup 缓存（约 37GB）
venvroot  -> /mnt/venvs        # 各内层仓库 venv（约 24GB）
```

原则贯彻：**模型/数据下载、docker 构建、venv、缓存全部落在 /mnt，根分区只放代码。**

---

## 2. 六关执行回顾

| Task | 内容 | 关键证据 |
|---|---|---|
| 0 | 系统与存储基线 | xfs 挂载、docker、GPU 可见 |
| 1 | 仓库与容器工具链 | `repos/` 就位、镜像可构建 |
| 2 | 内层仓库与依赖 | uv sync、flashdreams 可导入 |
| 3 | 权重/场景下载 | OmniDreams 4,118,900,683B、R1 5 shards、VaVAM、NuRec usdz |
| 4 | Driver/Renderer 独立冒烟 | R1 推理 rc=0，GPU0 峰值 22,981MB；renderer GPU1 22,951MB 端口 50051 |
| 5 | 闭环 rollout | 5 容器健康 → 75 帧 rollout → 20 帧/10fps 交付视频 |

每关对应 `scripts/check_taskN.py`，**只有 `[SUCCESS]` 才进入下一关**——这条纪律避免了"差不多能用"的假完成。

---

## 3. 踩坑全记录（按主题分类）

### 3.1 网络与镜像源（中国大陆环境）

直连 PyPI / docker.io / nvcr.io / ghcr.io 速度极低（~1MB/min）甚至超时。最终采用：

| 资源 | 镜像 |
|---|---|
| PyPI | `https://pypi.tuna.tsinghua.edu.cn/simple` |
| rustup | `RUSTUP_DIST_SERVER=https://mirrors.tuna.tsinghua.edu.cn/rustup` |
| crates.io | `sparse+https://mirrors.tuna.tsinghua.edu.cn/crates.io-index/`（`~/.cargo/config.toml`） |
| docker.io | `docker.m.daocloud.io` |
| nvcr.io | `nvcr.1ms.run` |
| ghcr.io | `ghcr.1ms.run` / `ghcr.m.daocloud.io`（均不稳定，见 3.5） |

**经验：**
- docker 拉基础镜像优先用 **mirror + 摘要（digest）拉取**再 `docker tag` 回原名，digest 一致即内容等价，且绕过 wizard 对镜像名的硬编码。
- 镜像源不稳定时多准备两个；`docker pull` 卡住超过几分钟就换源，不要死等。

### 3.2 HuggingFace：gated 授权与下载工具

- 三个 gated 页面（NuRec、PhysicalAI、omni-dreams-models）必须**浏览器内登录正确账号并 Agree**，token 本身不绕过授权。曾因登错账号误判，复查后通过。
- **`hf_transfer` 陷阱**：新版 huggingface hub 已弃用 hf_transfer，而项目 venv 未装该包；环境里 `HF_HUB_ENABLE_HF_TRANSFER=1` 会直接抛 ValueError。`scripts/env.sh` 已将默认值改为 0。
- **NuRec 是 dataset 仓库**：`hf download` 必须加 `--repo-type dataset`，否则按 model 解析找不到。
- 大文件断点续传：R1 曾因下载被 kill 留下孤儿 `.incomplete` shard（~9GB），与新下载并存。判别方法：**30 秒内连续 `ls -la` 看哪个文件在增长**，完成后删除所有 `.incomplete`。
- `HF_ENDPOINT` 可在 GFW 故障时切 `https://hf-mirror.com`（env.sh 中留了注释）。

### 3.3 Docker：权限、data-root 与 wizard 的隐式调用

- wizard 内部直接调用**裸 `docker compose`**（不是 docker SDK），所以运行 wizard 的用户必须在 docker 组。`sudo usermod -aG docker vipuser` 后，**同一会话需 `sg docker -c '...'` 才生效**（重新登录才完全生效）。
- `/` 空间有限，Docker data-root 改到 /mnt 后所有镜像自动落数据盘。
- 排查 docker 类问题时保留 sudo 兜底：`scripts/env.sh` 的 `dk()` 封装了"先直连、失败 sudo"。

### 3.4 alpasim-base 镜像构建（坑最密集）

wizard 依赖基础镜像 `alpasim-base:0.134.0`，必须手工构建（`docker_build_only` 只生成配置，不构建）。

- **致命坑：`.dockerignore` 排除了 `uv.lock`**。结果容器内 uv 被迫重新解析整个依赖树，遇到 `typing_inspect` 旧包"has no publish time"、alpasim-utils vs dataclasses-json 冲突等，报 "No solution found"。修复：`.dockerignore` allowlist 加 `!uv.lock`、`!.python-version`，所有 sync 命令统一加 **`--frozen`**。
- 所有 `uv run` 也加 `--no-sync`，防止容器运行时因 lock 轻微变化触发重装（曾因此触发 flash-attn 源码编译）。
- Rust 工具链在容器内安装（utils_rs maturin 需要），必须配置 TUNA 的 rustup 与 crates.io 源，否则构建长时间停滞。
- protos 编译：`uv run --frozen --no-sync compile-protos`。
- 最终镜像约 12GB，构建成功后 wizard 才能启动闭环容器。

### 3.5 ghcr.io/astral-sh/uv 镜像拉不下来

Dockerfile 引用 `ghcr.io/astral-sh/uv:latest`，两个 ghcr 镜像源均 stalled。解决方案：本机已有 uv 二进制（`~/.local/bin/uv`,`uvx`），直接构建等价本地镜像：

```dockerfile
FROM scratch
COPY uv uvx /
```

tag 成 `ghcr.io/astral-sh/uv:latest` 即可被 Dockerfile 的 `COPY --from=` 解析。

### 3.6 Renderer 启动三连坑

1. **主机名解析失败**：torchrun 的 TCPStore 要连 `(pc_3, 端口)`，容器/主机无法解析该名，5 分钟超时。修复：`/etc/hosts` 加 `127.0.0.1 pc_3`。
2. **flash-attn 源码编译**：某次 `uv run` 因环境变化触发重装，flash-attn 去找 `:/usr/local/cuda-11.8/bin/nvcc`（CUDA_HOME 前导冒号）编译失败。修复：统一 `uv run --no-sync`，venv 内已有 flash_attn 2.8.3 wheel。
3. **JIT 扩展 CUDA 版本不匹配（本阶段最大坑，见 3.7）**。

### 3.7 CUDA 13 JIT 工具链（ludus / nvjpeg 插件）

**现象**：renderer gRPC `start_session` 失败，报 `ludus_renderer_plugin.so: cannot open shared object file`，构建目录里只有 `build.ninja` 没有 `.so`——ninja 的真实编译错误被 `torch.utils.cpp_extension` 吞掉。

**根因有两层**：
1. `.bashrc` 导出 `export CUDA_HOME=$CUDA_HOME:/usr/local/cuda-11.8`：变量未设置时变成**畸形的 `:/usr/local/cuda-11.8`**（前导冒号），且 CUDA 11.8 与 torch cu130 主版本不符。
2. 系统只有 CUDA 11.8 toolkit，**没有 cu13 的 nvcc/头文件**。

**修复方案（用 pip wheel 拼一个 CUDA 13 prefix）**：

在 flashdreams venv 安装：`nvidia-cuda-nvcc==13.0.88`、`nvidia-nvvm==13.0.88`、`nvidia-cuda-crt==13.0.88`、`nvidia-cuda-cccl==13.0.85`、`nvidia-nvjpeg==13.0.4.44`；在 `/mnt/cuda13/{bin,include,lib64}` 用软链指向 venv 的 `nvidia/cu13/*`；手补 `libcudart.so → libcudart.so.13`、`libnvjpeg.so → libnvjpeg.so.13` 两个符号链接（wheel 只带版本化 SONAME）。

- **版本必须锁齐**：首次安装没锁 nvvm，被解析成 13.4，cicc 产出 PTX 9.4，而 13.0 的 ptxas 只支持 9.0（`Unsupported .version 9.4`）。同套组件必须同版本。
- 缺 CCCL 时报 `fatal error: nv/target: No such file`；缺 nvJPEG 时报 `fatal error: nvjpeg.h`。
- **如何让被吞的错误现形**：手动触发编译——
  ```bash
  CUDA_HOME=/mnt/cuda13 PATH=$PWD/.venv/bin:/mnt/cuda13/bin:$PATH \
    .venv/bin/python -c "from ludus_renderer._ops._plugin import _get_plugin; _get_plugin()"
  ```
  ninja 的完整命令行和错误即打印到 stderr。
- 插件 .so 没有 RUNPATH，运行时依赖已加载的同 SONAME 库；`import nvjpeg_encoder_plugin` 前需先 `import torch`，且 `LD_LIBRARY_PATH` 含 `/mnt/cuda13/lib64`。

最终两个插件（`ludus_renderer_plugin`、`nvjpeg_encoder_plugin`）均编译加载成功，第二次闭环即跑通。修复已固化进 `scripts/env.sh`（export `CUDA_HOME=/mnt/cuda13` 并前置其 bin/lib）。

### 3.8 Wizard 配置类小坑

- **`scenes.scene_ids` 与 `scenes.test_suite_id` 互斥**，同时设置直接报错。固定单场景时只留 `scenes.scene_ids=['clipgt-…']`（注意 NuRec 场景在 wizard 里带 `clipgt-` 前缀）。
- 场景/标定解析：runtime 从 usdz 解出 HD map（~1.1MB）与 10 相机标定，"Starting video model session" 之后才进入逐 chunk 渲染。
- 首帧/首 chunk 慢：`+runtime.endpoints.startup_timeout_s=900` 防止 warmup 阶段超时。
- 闭环 chunking：`+chunking=8frame`，首 chunk 5 帧、其后每 chunk 8 帧；日志可见动态 actor 数（本场景 33）。

### 3.9 显存测量：tqdm 的 CR 输出

Task 4 门禁最初按"stdout 逐行"轮询显存，但 tqdm 只输出回车（`\r`）不换行，采样实际没在跑，rc=0 却报"显存未超 20000MB"。修复：改为 **daemon 线程每 2 秒独立采样一次**，与子进程输出解耦。实测 GPU0 峰值 22,981MB。

**通用经验：不要把子进程输出的行时序当作采样时钟；轮询要独立于被观测进程。**

---

## 4. 脚本与产物清单

| 路径 | 作用 |
|---|---|
| `scripts/env.sh` | 统一环境：存储路径、缓存、CUDA13 prefix、凭据从 `~/access/` 解析（不落地明文）、`dk()`/`sudosw()` |
| `scripts/check_task0..5.py` | 六关验收门 |
| `scripts/start_renderer.sh` / `stop_renderer.sh` | GPU1 OmniDreams gRPC renderer（torchrun，端口 50051） |
| `scripts/run_closed_loop.sh` | VaVAM-B + OmniDreams 闭环（`sg docker -c` 包裹） |
| `artifacts/rollout_video_path.txt` | wizard 原始 rollout 路径 |
| `artifacts/raw_frames/` | 前 20 帧 PNG |
| `artifacts/stage1_closed_loop.mp4` | 最终交付视频 |

凭据安全纪律：**token/密码只存在 `~/access/`，绝不进入 tracked 文件、commit message、文档**；门禁脚本会扫描 `hf_…`/`ghp_…`/`nvapi-…` 模式。

## 5. Git 记录

全部 commit author = `Junchuan Zhang <zjunchuan@gmail.com>`，push 至 https://github.com/GimpelZhang/Cosmos-Dreams

```
99136c8 fix: CUDA13 toolkit env for JIT plugins; add VaVAM closed-loop runner
832c87c fix: task4 VRAM sampling thread; --no-sync driver; local R1 path support
b625f75 fix: ignore storage symlinks; use --repo-type dataset for NuRec download
5d0194a chore: add task 2-5 gate scripts and renderer start/stop scripts
6eca6ea fix: include docker binary in task1 gate sudo fallback
25e051c fix: correct shell var expansion in task0 gate script
4c5d9ba chore: initialize Stage 1 workspace with env script and detailed plan
```

注意：`repos/` 内层仓库的改动（alpasim Dockerfile、.dockerignore、test_inference.py 等）**不在 outer repo 跟踪范围**，机器重建时需参照本文 §3 重新应用。

---

## 6. 可复用检查清单（下次重建/升级照做）

1. ☐ 数据盘挂载 /mnt；docker data-root、所有缓存/venv/权重指向 /mnt。
2. ☐ 配置国内镜像源（PyPI/rust/crates/docker/nvcr），digest 拉取 + retag。
3. ☐ gated HF 页面浏览器 Agree；`hf download --repo-type dataset`（NuRec）。
4. ☐ 关 hf_transfer（默认 0）。
5. ☐ 用户加入 docker 组，wizard 用 `sg docker -c` 运行。
6. ☐ 构建 alpasim-base：`.dockerignore` 放 uv.lock/.python-version，全程 `uv … --frozen`、`uv run --no-sync`。
7. ☐ `/etc/hosts` 加 `127.0.0.1 pc_3`。
8. ☐ CUDA13 prefix `/mnt/cuda13`（组件版本锁齐 13.0.x），env.sh 导出。
9. ☐ 先手动预编译两个 JIT 插件，再跑闭环。
10. ☐ scene_ids/test_suite_id 只设一个；startup_timeout 给足。
11. ☐ 每关跑 check_taskN.py，[SUCCESS] 才前进。
12. ☐ 收尾停 renderer、确认 VRAM 释放，commit/push（author 正确）。

---

## 7. 下一阶段：Alpamayo-R1 + OmniDreams（Stage1 最终目标）

Stage1 从始至终的目标是 **NVIDIA 世界模型 OmniDreams + AlpaSim 的闭环仿真**：GPU0 跑 Alpamayo-R1 策略，GPU1 跑 OmniDreams 渲染（本复盘路径的同一外部 gRPC renderer）。VaVAM-B 只是分层打通时的临时 driver。

- 目标组合：保持 `deploy=external_video_model topology=1gpu` + 外部 OmniDreams renderer（与已跑通路径一致），将 driver 从 `vavam_video_model` 换成 Alpamayo-R1。
- 已知约束（2026-09-27 调查）：wizard 未直接暴露 "R1 + video_model" 的组合 driver；公开的视频模型 driver 只有 `vavam_video_model`、`alpamayo1_5_1cam`。预计需仿照 `alpamayo1_5_1cam` 的单相机视频驱动方式，新建一个 R1 的 video-model driver（Hydra config + driver 封装），让 R1 以"单目视频帧 + 动作历史"为输入推理 action。
- 权重已就位：`/mnt/weights/alpamayo-r1`（5 shards，HF 格式，22GB）。
- NRE 路径（`nvcr.io/nvidia/nre/nre-ga:26.04`，sensorsim 多相机协议）**不是 Stage1 目标**；2026-10-01 曾短暂试拉该镜像（已登录 nvcr.io、manifest 核实 14.3GB），方向调整后已停止拉取。
- 实施方法：调研配置/接口 → 文档固化升级步骤 → `wizard.run_method=NONE` 生成配置做兼容性检查 → 跑真实闭环 → 抽帧/编码 → 验收。

### 7.1 升级完成记录（2026-10-01）

R1+OmniDreams 闭环一次跑通（wizard rc=0，75 帧 rollout）。实施中新增/修改的文件：

- `repos/alpasim/src/wizard/configs/driver/alpamayo1_1cam.yaml`（新 preset：alpamayo1 + 1cam_1080 + extras + subsample_factor=3 + checkpoint_path=/mnt/weights/alpamayo-r1）
- `repos/alpasim/src/wizard/configs/extras/r1_weights.yaml`（driver 全量 6 挂载；list 合并为替换语义，必须列全）
- `scripts/run_closed_loop_r1.sh`（外层仓库，新闭环入口）

踩坑：①本项目 Hydra override parser 拒绝 `+=`；②extras 只写新挂载会把默认 volumes 全部冲掉，driver 报 `/mnt/output not found` 秒退（rc=1，compose 整体 143）；列全 6 挂载后通过。

R1 真实驾驶证据（driver 日志 Chain-of-Causation）：
- "Nudge left to pass the parked car on the right."
- "Keep at the center of the lane to continue driving since no critical agent needs attention."

**注意：本节初次记录时据 aggregate 表写的"collision_any=0、闭环无碰撞"结论是误导性的，复查后已纠正，见 §7.1.1。**

聚合表（`artifacts/run_r1_omnidreams/aggregate/metrics_results.txt`）字面上显示 collision_any=0、dist_traveled=97.87m（GT 93.85m）、dist_to_gt_trajectory=4.12m，但这只是"计分窗口内"的结果（原因见 §7.1.1）。

交付物：`artifacts/stage1_r1_closed_loop.mp4`（20 帧/10fps，帧 mean≈166/std≈98 非空帧；注意该视频只取了每 4 帧，**未覆盖碰撞时刻**，需看原始 75 帧 rollout）、`artifacts/r1_raw_frames/`（20 PNG）、`artifacts/r1_video_path.txt`（原始 75 帧 rollout 路径）。

### 7.1.1 复查纠正（2026-10-01）：R1 在视频 13.8s 撞到人行横道行人

用户在原始 rollout 约 13s 处发现 ego 车直接撞向过街行人。复查 rollout 目录下逐帧原始 metrics（`rollouts/.../metrics.parquet`，长表 75 帧）与原始视频，**碰撞属实**：

- `collision_any=1` 连续 3 帧（时间戳 4798884112017/4378681/4645345，约 0.8s），首帧同时 `collision_front=1`；对应原始视频 **13.8–14.3s**（75 帧 @3.75fps，总时长 20s）。碰撞帧画面左前方有撞击烟尘特效，之后行人消失。offroad 全程 0。
- 速度证据：controller CSV 显示 ego vx 全程保持 8.3–8.5 m/s（约 30 km/h），纵向指令从未出现强制动；ego 最终位置 x=166.7m，而原始记录（GT）全程仅 93.85m——**GT 车在人行横道前停下让行，R1 没有停，直接开过**。这同时确证 R1 真实控制了车辆：若回放 GT 不可能产生碰撞和 72.9m 的轨迹偏离。

**最值得记录的失败模式——CoT 与动作不一致**：碰撞前 R1 连续 5 个推理 chunk 明确输出 "Yield to the pedestrian in the crosswalk since they are crossing the ego lane"（"Yield due to the pedestrian in the crosswalk" 等），即模型感知到了行人、文字上承诺让行，但其选中的预测轨迹并未减速；约 0.8s 后它转而输出 "no critical agent needs special attention" / "lane ahead is clear"，随即撞上。结论：Alpamayo 的文本因果链不是对实际轨迹的可靠描述，存在"说让行、实际不减速"的脱节。叠加配置因素：`num_traj_samples=1`（只生成 1 条轨迹）+ `selection_strategy=ALWAYS_FIRST`（无安全拒绝采样）、`safety_monitor_triggered=0`（无兜底防护）、单前宽相机偏离 R1 训练时的多相机分布。研究模型出现此类失效是预期内的，但任何对外展示都不能把它说成"安全通过"。

**为什么 aggregate 表却显示 pass/collision=0**：指标流水线末尾的 modifier `RemoveTimestepsAfterEvent(dist_to_gt_trajectory >= 4)` 删除了 9 个采样帧——自视频 11.4s 起 ego 相对 GT 偏离超过 4m（GT 在减速/停车让行、R1 继续以 8m/s 行驶，偏离以约 8m/s 速率累积），计分在此截断；碰撞发生在截断点之后 2.4s。即 aggregate 的 "pass" 语义是"轨迹先偏离出计分窗口，之后事件不再计分"，**不是"没有发生碰撞"**。读这类报告时必须同时看逐帧原始 metrics 和原始视频。

复查辅助产物：`artifacts/r1_check_frames/`（视频 9–17s 逐帧，含碰撞瞬间）。

### 7.2 额外目标：Alpamayo 1.5 + OmniDreams 闭环（2026-10-01）

在 R1 路径跑通后，按同一模式额外打通 Alpamayo 1.5 + OmniDreams，wizard rc=0，完整 75 帧 rollout。新增/修改的文件：

- `repos/alpasim/src/wizard/configs/driver/alpamayo15_1cam_local.yaml`（新 preset：alpamayo1_5 + 1cam_1080 + extras + subsample_factor=3 + checkpoint_path=/mnt/weights/alpamayo-1.5；官方自带的 `alpamayo1_5_1cam` 走在线权重路径，本 preset 用于本地权重）
- `repos/alpasim/src/wizard/configs/extras/a15_weights.yaml`（driver 全量 6 挂载 + 权重目录只读挂载 + 2 个 offline 环境变量）
- `scripts/run_closed_loop_a15.sh`（外层仓库，新闭环入口；必须经 `sg docker -c` 启动）

权重与依赖：主权重 `/mnt/weights/alpamayo-1.5`（HF 格式本地下载）；1.5 的 VLM/backbone 配置为 gated 仓库 `nvidia/Cosmos-Reason2-8B`（独立于 PhysicalAI 许可，需单独在 HF 页面 Agree），实际只需其 processor/tokenizer 文件，用 `huggingface_hub snapshot_download --allow-patterns` 只拉取 tokenizer 相关 10 个文件（无 safetensors），缓存在 `/mnt/caches/hf`；`BASE_PROCESSOR_NAME=Qwen/Qwen3-VL-2B-Instruct` 两个候选 processor 文件名缓存中均已具备。

**唯一新坑——容器无 HF token + gated 元数据请求 401**：首次真实运行 driver 在 `AutoProcessor.from_pretrained("nvidia/Cosmos-Reason2-8B")` 处报 "You are trying to access a gated repo … 401"。driver 容器虽挂载了 HF cache，但没有 HF token 环境变量，transformers 默认仍尝试联网校验授权。修复：在 extras 中为 driver 注入 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`，强制只走本地缓存（config check 先确认 compose 已渲染 environment 块，再跑真实闭环即通过）。备选方案（未使用）：运行时从 `~/access/` 取 token 注入容器环境。

1.5 驾驶证据（driver 日志，共 66 条 Chain-of-Causation，显著多于 R1——1.5 每个 chunk 均输出推理）：
- "Keep lane since the lane ahead is clear and no lead vehicle is present"
- "Nudge left due to a vehicle pulling out from the right curb"
- "Keep lane since the lane is clear ahead"

**注意：本节初次记录时据 aggregate 表写的“offroad=0、只撞了一下”不完整，用户复查后据逐帧数据补充，见下方时间线（与 §7.1.1 同类的 aggregate 截断现象）。**

逐帧时间线（`rollouts/.../metrics.parquet` + controller CSV + 原始 20s 视频）：
- 0–2s：force-GT，直行 ~8.5 m/s；
- 2–7.3s：1.5 先正常跟车，随后连续 10+ 个 chunk 输出 **“Nudge left due to a stopped vehicle blocking the right side of our lane”**，车辆以平滑小转向（帧间最大变化 0.055，无抖动）向左绕行，横向 y 从 0 缓慢到约 −3m——“不沿直线”是模型**有意执行的绕行动作，且与其文字一致**；
- **7.33s（x≈61m）**：绕行余量不足，**前向撞上右侧停驶/占道车辆**，collision_front=1；
- 7.3–11.6s：碰撞状态持续 17 帧（4.3s，重叠滑过），其间 collision_rear 也被判为 1（持续重叠时检测盒同时报前后角，非另有车追尾）；
- **8.4–14.6s：offroad=1 共 24 帧（6.2s）**——碰撞后骑/沿右侧路缘行驶（画面中行道树和建筑贴脸），同时重刹，速度从 8 降到 2 m/s；
- 14.6–20s：回到路面，以 2–3 m/s 蠕行通过路口区域，末态 x=96.2m（GT 全程 93.85m，末端向右转弯）。

聚合表（`aggregate/metrics_results.txt`）字面显示 offroad=0、dist_to_gt_location=4.12、dist_traveled=58.13，原因与 R1 同：modifier `RemoveTimestepsAfterEvent(collision)` 删除首次碰撞帧之后的 47 行——offroad、rear 碰撞、14.34m 的位置差全部落在截除窗口内。**必须结合逐帧 metrics 和原始视频解读。**

计分窗口内指标：minADE@0.5/1.0/2.5s = 1.46/1.73/2.70m（R1 为 3.55/3.98/5.59），dist_to_gt_trajectory 最大 2.21m（R1 4.12m）。

与 R1 的对比结论：①1.5 **确实在亲自控制车辆**，且这次 CoT 与动作**一致**（说 nudge left 就平滑左转、说 keep distance to stopped lead 就重刹）——失败性质是**判断/裕量不足**（绕行不够 + 碰撞后骑上路缘），而非 R1 那种“说让行却完全不减速”的文本-动作脱节；②闭环链路（推理→动作→OmniDreams 渲染→碰撞/offroad 检测）端到端成立，事故是模型在该场景（单前宽视角偏离训练多相机分布）的真实策略结果，不是系统故障。

交付物：`artifacts/stage1_a15_closed_loop.mp4`（20 帧/10fps，帧 mean≈164–168/std≈92–97 非空帧；同样是稀疏抽帧、不覆盖 7.3s 碰撞，需看原始 rollout）、`artifacts/a15_raw_frames/`（20 PNG）、`artifacts/a15_video_path.txt`（原始 75 帧 900×1000 rollout 路径）、`artifacts/run_a15_omnidreams/aggregate/`（metrics_results.txt/.parquet/.png、results-summary.json）、`artifacts/a15_check_frames/`（全 75 帧缩图，含碰撞/offroad 全段）。

### 7.3 批量闭环：Alpamayo 1.5 × 30 个新场景（2026-10-01）

在 §7.1/§7.2 单 clip 验证之后，另下载 30 个此前未用过的 26.01 场景，用**独立的批量脚本**一次 wizard 调用完成批量闭环，30/30 跑通。结果：仅 3 clip（10%）clean，碰撞 11 clip（共 163 帧）、offroad 21 clip（共 342 帧），安全监控全程未触发；平均 3.45 min/clip，全批约 1h44m。批量层面再次定量证实 aggregate 截断效应（aggregate dist_to_gt_trajectory=3.02 vs 逐帧真值 20.06m）。

- 完整报告：[Stage1_Batch30_Report.md](Stage1_Batch30_Report.md)
- 批量脚本：`scripts/run_batch_a15.sh`（区别于 `run_closed_loop_a15.sh`：清单驱动、单次 wizard、自动拉起 renderer、强制串行）
- 场景清单：`scripts/a15_batch_scenes_20261001.csv`；逐 clip 分析器：`scripts/analyze_batch_a15.py`
- 产物：`artifacts/run_a15_batch30_20261001/`（batch_summary.csv + 30 个 rollout MP4，共 5.4GB，实体在 /mnt）
