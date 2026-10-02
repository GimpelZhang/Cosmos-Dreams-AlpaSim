# Stage 1 端到端复现指南：OmniDreams × AlpaSim 闭环仿真

> 编写日期：2026-10-01
> 目标读者：人类开发者 / AI Agent。按本文顺序操作，可在一台双 A100 服务器上从零复现
> **NVIDIA OmniDreams 神经渲染器 + AlpaSim 闭环仿真**，覆盖三种 driver
> （**VaVAM-B、Alpamayo-R1、Alpamayo 1.5**）与两种运行形态（**单 clip / 批量**）。
> 配套阅读：根目录 `CLAUDE.md`、`docs/Stage1_Architecture.md`（代码级架构与接口解析）、
> `docs/Stage1_Driver_IO_Cycle.md`（Driver 视角单步输入/输出轮回）、
> `docs/Stage1_Complete_1.md`（踩坑叙事）、
> `docs/Stage1_Batch30_Report.md`（批量结果）、
> `docs/Stage1_Runtime_Performance.md`（运行时性能实测：RTF、帧率、每帧/每 step 墙钟）。

---

## 0. 你将得到什么

闭环：driver（GPU0）根据当前画面推理驾驶动作 → physics/controller/trafficsim 推进 →
OmniDreams 世界模型（GPU1）渲染下一帧 → 回传 driver，循环 ~75 帧（3.75fps，20s），
产出 rollout MP4 与逐帧指标。

| Driver | 单 clip | 批量 |
|---|---|---|
| VaVAM-B（分层打通用） | `scripts/run_closed_loop.sh` | — |
| Alpamayo-R1（Alpamayo 1） | `scripts/run_closed_loop_r1.sh` | 换 driver 即可 |
| Alpamayo 1.5 | `scripts/run_closed_loop_a15.sh` | `scripts/run_batch_a15.sh`（已验证 30/30） |

总体拓扑固定：`deploy=external_video_model topology=1gpu`，外部 gRPC renderer 监听
`127.0.0.1:50051`。

---

## 1. 硬件与系统前提

| 项目 | 要求（实测值） |
|---|---|
| GPU | 2× NVIDIA A100-SXM4-**80GB**；驱动 580.178.04，CUDA 13.0 |
| OS | Ubuntu 22.04，内核 6.8.0，RAM 94GB |
| 数据盘 | 独立 500GB 盘挂载 `/mnt`（xfs；持久化挂载按目标机实际配置） |

原则：**模型/数据下载、docker 镜像与 data-root、venv、缓存全部落 `/mnt`；根分区只放代码。
永远不要对任何磁盘执行 mkfs。**

网络环境若在中国大陆，先按 §3.1 配镜像源。

---

## 2. 存储与目录骨架

```bash
# 数据盘子目录
sudo mkdir -p /mnt/{weights,assets,caches,venvs,docker-data,alpasim-data,cuda13,artifacts}
sudo chown -R "$USER:$USER" /mnt/{weights,assets,caches,venvs,alpasim-data,cuda13,artifacts}

# 外层仓库（含全部 scripts/、docs/、CLAUDE.md）
cd ~
git clone https://github.com/GimpelZhang/Cosmos-Dreams-AlpaSim.git simulation
cd simulation

# 外层软链（.gitignore 已忽略）
ln -sfn /mnt/weights  weights
ln -sfn /mnt/assets   assets
ln -sfn /mnt/caches   caches
ln -sfn /mnt/venvs    venvroot
```

`scripts/env.sh` 是所有脚本的单一事实源（路径、缓存、CUDA13、凭据解析）。
凭据文件由用户自备于 `~/access/`（**不得入库、不得写入任何被跟踪文件**）：

- `access_methods.txt`：含一行 `sudo password: ...`
- `user_access_methods.txt`：含 `huggingface token:` / `github access token:` / `NVIDIA NGC API Key:`

后续所有命令默认已 `source ~/simulation/scripts/env.sh`。

---

## 3. 基础工具链

### 3.1 镜像源（中国大陆）

| 资源 | 镜像 |
|---|---|
| PyPI | `https://pypi.tuna.tsinghua.edu.cn/simple` |
| rustup | `RUSTUP_DIST_SERVER=https://mirrors.tuna.tsinghua.edu.cn/rustup` |
| crates.io | `sparse+https://mirrors.tuna.tsinghua.edu.cn/crates.io-index/`（写入 `~/.cargo/config.toml`） |
| docker.io | `docker.m.daocloud.io` |
| nvcr.io | `nvcr.1ms.run` |
| ghcr.io | `ghcr.1ms.run` / `ghcr.m.daocloud.io` |

docker registry mirrors 写入 `/etc/docker/daemon.json`（见 §3.3）。
拉镜像优先 **mirror + digest 拉取 → `docker tag` 回原名**，可绕过 Dockerfile 里的硬编码全名。

### 3.2 安装 uv 与 Rust

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh    # 实测 uv 0.12.21
# Rust（utils_rs 的 maturin 构建必需），已配 TUNA 源后：
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
```

### 3.3 Docker

```bash
sudo usermod -aG docker "$USER"
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json >/dev/null <<'JSON'
{
  "data-root": "/mnt/docker-data",
  "registry-mirrors": ["https://docker.m.daocloud.io"]
}
JSON
sudo systemctl restart docker
```

wizard 内部直接调用裸 `docker compose`。当前会话组身份未生效前，用
`sg docker -c '...'` 包裹命令（各闭环脚本已内置/文档化此要求）。

### 3.4 `/etc/hosts` 小坑（必做）

torchrun 的 TCPStore 默认解析主机名 `pc_3`，否则卡 5 分钟超时：

```bash
echo "127.0.0.1 pc_3" | sudo tee -a /etc/hosts
```

---

## 4. 获取内层代码（版本必须对齐）

```bash
mkdir -p repos && cd repos
git clone https://github.com/NVlabs/alpasim.git          # AlpaSim 0.134.0
git clone https://github.com/NVIDIA/flashdreams.git
git clone https://github.com/nv-tlabs/omni-dreams.git   # 参考仓库（可选，闭环本身不依赖）
git clone https://github.com/NVlabs/alpamayo.git

# 锁定到本次验证过的提交
git -C alpasim     checkout affc2ea
git -C flashdreams checkout 0957cf0
git -C omni-dreams checkout cd85f39
git -C alpamayo    checkout 11a0e01

# 场景/驱动资产目录软链到数据盘；目标目录先建好，否则只复现 R1/1.5
# （跳过 §5.4 VaVAM 下载）时 data/drivers 是悬空软链，wizard 挂载校验会失败
mkdir -p /mnt/alpasim-data/drivers /mnt/alpasim-data/nre-artifacts
ln -sfn /mnt/alpasim-data/nre-artifacts alpasim/data/nre-artifacts
ln -sfn /mnt/alpasim-data/drivers       alpasim/data/drivers

# 三个内层仓库的 .venv 全部软链到 /mnt，否则首次 uv run 会把 venv 建在根分区
ln -sfn /mnt/venvs/alpasim      alpasim/.venv
ln -sfn /mnt/venvs/flashdreams  flashdreams/.venv
ln -sfn /mnt/venvs/alpamayo     alpamayo/.venv
```

此后各仓库首次 `uv sync` / `uv run` 会把依赖装进 `/mnt/venvs/*`。

> **快捷方式**：本文 §6.1/§9 对内层仓库的全部本地改动与新建配置，已在外层仓库
> `configs/local-patches/`（含 README 与可直接 `git apply` 的补丁）中保存权威副本——
> 不必手工逐字创建，按该目录 README 应用即可。

`repos/` 不被外层仓库跟踪；对内层仓库的修改（§5.1、§6）需在重建时重新应用。

---

## 5. 授权、权重与场景下载

### 5.1 HuggingFace gated 授权

以下页面必须用**正确账号在浏览器登录并点 Agree**（token 不绕过授权）：

- `nvidia/PhysicalAI-Autonomous-Vehicles-NuRec`（**dataset** 类型）
- `nvidia/omni-dreams-models`
- `nvidia/Alpamayo-1.5-10B`、`nvidia/Cosmos-Reason2-8B`（1.5 路线才需要）

`nvidia/Alpamayo-R1-10B` 为公开仓库。

下载工具注意：① NuRec 必须加 `--repo-type dataset`；② 新版 hub 已弃用
`hf_transfer`，保持 `HF_HUB_ENABLE_HF_TRANSFER=0`（env.sh 已默认）；GFW 故障时
`HF_ENDPOINT=https://hf-mirror.com`。

### 5.2 OmniDreams 渲染权重（3.84 GiB，精确 4,118,900,683 字节）

renderer 也会在首次启动时按 `integrations_v2/omnidreams/config.py` 中的 URL 自行拉取到
HF 缓存；也可预下载：

```bash
hf download nvidia/omni-dreams-models \
  single_view/2b_res720p_30fps_i2v_hdmap_distilled.pt \
  --local-dir "$WEIGHTS_DIR/omnidreams"
```

### 5.3 Alpamayo-R1（22.16 GB，5 个 safetensors 分片）

```bash
hf download nvidia/Alpamayo-R1-10B --local-dir "$WEIGHTS_DIR/alpamayo-r1"
```

### 5.4 VaVAM-B 资产（约 1.75 GB，自带 sha256 校验）

```bash
cd "$REPOS_DIR/alpasim"
bash data/download_vavam_assets.sh --model vavam-b
# 产物在 data/drivers/vavam/（实体 /mnt/alpasim-data/drivers）
```

### 5.5 Alpamayo 1.5 + Cosmos-Reason2（仅 1.5 路线）

```bash
# 1.5 主权重（22GB 级）
hf download nvidia/Alpamayo-1.5-10B --local-dir "$WEIGHTS_DIR/alpamayo-1.5"

# CR2：driver 实际只需要 tokenizer/processor 文件，且必须落在 HF hub 缓存
# （driver 容器把缓存挂到 /root/.cache/huggingface），不要用 --local-dir：
hf download nvidia/Cosmos-Reason2-8B \
  --include 'tokenizer*' --include 'processor*' --include '*.json' --include '*.txt'
# CR2 processor 引用的基座 processor 一并备好：
hf download Qwen/Qwen3-VL-2B-Instruct \
  --include 'tokenizer*' --include 'processor*' --include '*.json' --include '*.txt'
```

### 5.6 场景

无需手工长期预下载：wizard 在运行（或 `run_method=NONE`）时会按 `scenes.scene_ids`
自动把 USDZ 拉进 `data/nre-artifacts`（实体在 `/mnt/alpasim-data`）。
固定单 clip 场景用：

```
clipgt-02eadd92-02f1-46d8-86fe-a9e338fed0b6
```

（wizard 中 NuRec 场景统一带 `clipgt-` 前缀；手工预取的路径写法见
`docs/Stage1_Plan_detailed.md` §7.6。）

---

## 6. 构建 alpasim-base 镜像（坑最密集，严格照做）

wizard 依赖镜像 **`alpasim-base:0.134.0`**，必须手工构建。

### 6.1 放行 uv.lock

新克隆的 `.dockerignore` 是 allowlist 且可能未包含 lock 文件——若 `uv.lock` 被排除，
容器内 uv 会重新解析依赖树并报 "No solution found"。确认 `repos/alpasim/.dockerignore`
中存在（没有则追加）：

```
!uv.lock
!.python-version
```

### 6.2 准备 uv 引导镜像

Dockerfile 第 1 阶段 `COPY --from=ghcr.io/astral-sh/uv:latest`；ghcr 源拉不动时，
用本机 uv 二进制构建等价镜像：

```bash
mkdir -p /tmp/uvimg && cd /tmp/uvimg
cp ~/.local/bin/uv ~/.local/bin/uvx .
printf 'FROM scratch\nCOPY uv uvx /\n' > Dockerfile
sg docker -c 'docker build -t ghcr.io/astral-sh/uv:latest .'
```

### 6.3 构建

```bash
cd "$REPOS_DIR/alpasim"
# Dockerfile 还引用 nvcr.io 的 dcgm-exporter（@digest），不通时用 §3.1 的
# nvcr mirror 先 digest 拉取再 tag 回原名。
sg docker -c 'docker build -t alpasim-base:0.134.0 .'
```

最终镜像约 12GB。容器内 Rust/proto 构建由 Dockerfile 完成；**所有手工 `uv run` 一律加
`--no-sync`，需要 sync 时加 `--frozen`**，防止运行时触发 flash-attn 等源码编译。

---

## 7. CUDA 13 JIT 工具链（renderer 插件必需）

torch cu130 的两个渲染插件（`ludus_renderer_plugin`、`nvjpeg_encoder_plugin`）运行时
JIT 编译，需要 cu13 的 nvcc/头文件；系统自带的 CUDA 11.8（且 `.bashrc` 里
`CUDA_HOME=:/usr/local/cuda-11.8` 是畸形值）不可用。

### 7.1 用 pip wheel 拼一个 CUDA 13 prefix

轮子安装到 **flashdreams venv**（即 `repos/flashdreams/.venv → /mnt/venvs/flashdreams`；
需先执行过一次 §8 的 `uv run` 让 venv 建好，或先用 `uv sync --package flashdreams-omnidreams`）：

```bash
cd "$REPOS_DIR/flashdreams"
uv pip install --python .venv/bin/python \
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  nvidia-cuda-nvcc==13.0.88 \
  nvidia-nvvm==13.0.88 \
  nvidia-cuda-crt==13.0.88 \
  nvidia-cuda-cccl==13.0.85 \
  nvidia-nvjpeg==13.0.4.44
```

**同套组件版本必须锁齐**（nvvm 若被解析成 13.4，cicc 产出 PTX 9.4，13.0 的 ptxas
只支持 9.0，报 `Unsupported .version 9.4`）。

把 venv 内 `nvidia/cu13/` 的三个目录整体软链成 CUDA prefix（`cicc`/`libdevice`
等在独立的 `cu13/nvvm/` 树下，逐文件软链容易漏，目录整体链接是实测做法）：

```bash
CU13="$PWD/.venv/lib/python3.12/site-packages/nvidia/cu13"
ln -sfn "$CU13/bin"  /mnt/cuda13/bin
ln -sfn "$CU13/include" /mnt/cuda13/include
ln -sfn "$CU13/lib"  /mnt/cuda13/lib64

# 手补两个 wheel 不携带的未版本化 SONAME：
ln -sfn libcudart.so.13 /mnt/cuda13/lib64/libcudart.so
ln -sfn libnvjpeg.so.13 /mnt/cuda13/lib64/libnvjpeg.so
```

`scripts/env.sh` 已导出 `CUDA_HOME=/mnt/cuda13` 并把其 `bin`、`lib64` 前置到
PATH / LD_LIBRARY_PATH。

### 7.2 手动预编译插件（先让错误现形）

torch 的 cpp_extension 会吞掉 ninja 的真实报错；先手动触发一次：

```bash
cd "$REPOS_DIR/flashdreams"
CUDA_HOME=/mnt/cuda13 PATH=$PWD/.venv/bin:/mnt/cuda13/bin:$PATH \
  .venv/bin/python -c "from ludus_renderer._ops._plugin import _get_plugin; _get_plugin()"
```

缺 CCCL 会报 `fatal error: nv/target`，缺 nvJPEG 报 `fatal error: nvjpeg.h`。
两个插件最终均能加载后再进下一步。（注意：`import nvjpeg_encoder_plugin` 前需先
`import torch`。）

---

## 8. 启动 OmniDreams renderer（GPU1）

```bash
# 脚本默认前台运行（直接观察启动日志）；需要后台时：
setsid bash ~/simulation/scripts/start_renderer.sh </dev/null > artifacts/renderer.log 2>&1 &
# 批量脚本 run_batch_a15.sh 内部就是用这种 setsid 方式自动拉起 renderer 的
```

脚本实质（flashdreams 首次 `uv run --package` 会自动建好该包的 venv，缓存在 /mnt）：

```bash
cd "$REPOS_DIR/flashdreams"
CUDA_VISIBLE_DEVICES=1 uv run --package flashdreams-omnidreams torchrun \
  --standalone --nnodes=1 --nproc_per_node=1 \
  -m omnidreams.impl.grpc.server \
  --pipeline_config_name omnidreams \
  --host 0.0.0.0 --port 50051 \
  --resolution 704p --output_format jpeg --jpeg_quality 90
```

就绪标志（约数分钟，含权重加载/插件初始化）：

```
Server started successfully. Press Ctrl+C to stop.
```

停止：`bash scripts/stop_renderer.sh`。

---

## 9. Wizard driver 配置（R1 / 1.5 新 preset，文件需自建）

wizard 未内置 "R1 + external_video_model" 组合，需仿照 `alpamayo1_5_1cam` 新建 preset。
以下四个文件是完整内容（位于 `repos/alpasim/src/wizard/configs/`）：

**`driver/alpamayo1_1cam.yaml`**

```yaml
defaults:
  - alpamayo1
  - /cameras: 1cam_1080
  - /extras: r1_weights
  - _self_

inference:
  use_cameras:
    - camera_front_wide_120fov
  subsample_factor: 3

model:
  checkpoint_path: "/mnt/weights/alpamayo-r1"
```

**`extras/r1_weights.yaml`**（Hydra 的 list 合并是**替换**语义，默认挂载必须列全）

```yaml
# @package _global_
services:
  driver:
    volumes:
      - ${defines.drivers}:/mnt/drivers
      - ${wizard.log_dir}:/mnt/output
      - ${repo-relative:'src'}:/repo/src
      - ${repo-relative:'plugins'}:/repo/plugins
      - ${defines.hf_cache}:/root/.cache/huggingface
      - /mnt/weights/alpamayo-r1:/mnt/weights/alpamayo-r1:ro
```

**`driver/alpamayo15_1cam_local.yaml`**

```yaml
defaults:
  - alpamayo1_5
  - /cameras: 1cam_1080
  - /extras: a15_weights
  - _self_

inference:
  use_cameras:
    - camera_front_wide_120fov
  subsample_factor: 3

model:
  checkpoint_path: "/mnt/weights/alpamayo-1.5"
```

**`extras/a15_weights.yaml`**

```yaml
# @package _global_
services:
  driver:
    # 容器无 HF token 而 CR2 是 gated 仓库；强制只走本地缓存，避免 401
    environments:
      - HF_HUB_OFFLINE=1
      - TRANSFORMERS_OFFLINE=1
    volumes:
      - ${defines.drivers}:/mnt/drivers
      - ${wizard.log_dir}:/mnt/output
      - ${repo-relative:'src'}:/repo/src
      - ${repo-relative:'plugins'}:/repo/plugins
      - ${defines.hf_cache}:/root/.cache/huggingface
      - /mnt/weights/alpamayo-1.5:/mnt/weights/alpamayo-1.5:ro
```

Hydra 注意：override parser **不支持 `key+=value`**；`+key=value` 仅在 key 不存在时可用。

---

## 10. 单 clip 闭环

renderer 就绪后，另开 shell：

```bash
# VaVAM-B
bash scripts/run_closed_loop.sh
# Alpamayo-R1
bash scripts/run_closed_loop_r1.sh
# Alpamayo 1.5
bash scripts/run_closed_loop_a15.sh
```

三者命令结构一致（以 R1 为例）：

```bash
cd "$REPOS_DIR/alpasim"
CUDA_VISIBLE_DEVICES=0 uv run --no-sync --project src/wizard alpasim_wizard \
  deploy=external_video_model \
  topology=1gpu \
  driver=alpamayo1_1cam \
  +chunking=8frame \
  "scenes.scene_ids=['clipgt-02eadd92-02f1-46d8-86fe-a9e338fed0b6']" \
  'wizard.external_services.renderer=["127.0.0.1:50051"]' \
  +runtime.endpoints.startup_timeout_s=900 \
  wizard.log_dir="$ARTIFACTS_DIR/run_r1_omnidreams"
```

要点：

- `scenes.scene_ids` 与 `scenes.test_suite_id` **互斥**，只能设一个。
- `+chunking=8frame`：首 chunk 5 帧，其后每 chunk 8 帧。
- `startup_timeout_s=900`：首帧 warmup（解 HD map、10 相机标定、模型加载）不超时。
- 无 docker socket 权限时 `sg docker -c 'bash scripts/run_closed_loop_r1.sh'`。
- 产物：`<log_dir>/rollouts/<clip>/<rollout>/{metrics.parquet, rollout 视频, rollout.asl}`。

---

## 11. 批量闭环（Alpamayo 1.5 × 30 场景）

### 11.1 准备场景清单

清单 `scripts/a15_batch_scenes_20261001.csv`（列：`uuid,scene_id,path,hf_revision`）。
自行选样时两条硬性纪律：

1. **跨 revision 去重**：同一 scene_id 同时挂 26.01/26.04 时，wizard 强制用最新
   artifact（日志 "The newest artifact per scene will be used"）。要整批同质，必须
   按 scene_id 排除所有在其他 revision 出现过的 ID（26.01 独有池为 756 个）。
2. **calibration 预检**：约 40% 的 26.01 USDZ 不含
   `clipgt/calibration_estimate.parquet`（video session 必需，缺则起步即失败）。
   按 zip 内容筛查（`unzip -l xxx.usdz | grep calibration_estimate`），并按 **≥1.5×**
   数量预下载候选补足。

CSV 统一用 LF 行尾（`.gitattributes` 已强制 `*.csv text eol=lf`）。

### 11.2 先做配置检查（不起容器）

在 wizard 命令末尾加 `wizard.run_method=NONE`，确认：场景可下载、生成的 compose 配置中
**所有并发数均为 1**。

### 11.3 跑批量

```bash
bash scripts/run_batch_a15.sh
# 自定义：MANIFEST=... RUN_TAG=... RENDERER_PORT=... bash scripts/run_batch_a15.sh
```

与单 clip 脚本的区别：清单 CSV 驱动、**一次 wizard 调用服务全部 clip（driver 模型只
加载一次）**、日志在 `/mnt/artifacts/run_<tag>`、renderer 端口未开时自动拉起并等待就绪。

**强制串行的原因**：外部 renderer 只保留一个活动 session，每次 `start_session` 都会
"Cleaning up N existing session(s)" 关掉旧 session；`topology=1gpu` 默认每端点 4 并发，
并发 clip 必报 `Session not found`。脚本已覆盖：

```
runtime.nr_workers=1
runtime.endpoints.{renderer,driver,physics,controller,trafficsim}.n_concurrent_rollouts=1
```

单 clip 失败由 `eval.allow_aggregation_with_failed_rollouts=true` 容忍；
`wizard.timeout=10800`。

### 11.4 批量逐帧分析（分析器无论 wizard 成败都要跑）

```bash
repos/alpasim/.venv/bin/python scripts/analyze_batch_a15.py \
  artifacts/run_a15_batch30_20261001 \
  --manifest scripts/a15_batch_scenes_20261001.csv
# --strict：存在 missing/partial/parse_error 时退出码 2
```

输出 `<log_dir>/batch_summary.csv`：每 clip 的碰撞/offroad/wrong_lane 帧数与首末时刻、
progress、里程、轨迹偏差、minADE、outcome（clean/offroad/collision/collision+offroad）。
损坏 parquet 记 `parse_error`；同 scene 多 rollout 只统计最新、其余 `superseded`；
帧数显著偏短记 `partial`；清单中缺产物的 clip 记 `missing`，不会被静默吞掉。

---

## 12. 结果判读（最重要的纪律）

### 12.1 永远不要只看 aggregate `metrics_results.txt`

aggregate 表带截断算子，例如：

- `RemoveTimestepsAfterEvent(offroad_or_collision)`：删掉首次事件后的所有帧；
- `RemoveTimestepsAfterEvent(dist_to_gt_trajectory >= 4)`：轨迹偏离超 4m 后停止计分。

事件越早、越早偏离 GT 的 clip 被截得越多，数字反而越"好看"。批量实测对照：

| 指标 | aggregate 表 | 逐帧真值 |
|---|---|---|
| dist_to_gt_trajectory | 3.02 m | 20.06 m（per-clip 最大偏差均值） |
| dist_traveled_m | 89.02 m | 158.83 m |
| collision_any | 0.23（rate） | 11/30 clip、163 帧 |

"pass" 的语义是"先偏离出计分窗口"，**不是"没发生事故"**。

### 12.2 逐帧核验方法

权威源：`rollouts/<clip>/<rollout>/metrics.parquet`（long format）+ rollout 原始视频。

```python
import pandas as pd
df = pd.read_parquet(".../metrics.parquet")
df["val"] = pd.to_numeric(df["values"], errors="coerce")
g = df.groupby(["name", "timestamps_us"], as_index=False)["val"].max()  # 多相机行去重
g[g.name.eq("collision_any") & (g.val >= 0.5)]   # 碰撞帧
```

指标陷阱：

- `min_distance_to_lane_boundary_m` 可能整批恒为 0，无区分度；
- `min_ade@5.0s(gt)` 在 8frame chunking 下无输出；
- offroad（路面区域二值标志）与轨迹偏差是两个维度——clean clip 也可能偏差很大。

### 12.3 本次基线结果（供回归对照）

- **单 clip**：三种 driver 均完成 75 帧 rollout。复核原始视频后：R1 在 13.8s 前碰
  过街行人（CoT 连续 5 chunk 写 "Yield" 却未减速，文本-动作脱节）；A15 在 7.33s
  前碰停驶车辆、8.4–14.6s offroad（CoT 与动作一致，失败属裕量不足）。
- **批量 A15 × 30**：clean 3（10%）、collision 6（20%）、collision+offroad 5
 （16.7%）、offroad 16（53.3%）；碰撞 11 clip/163 帧，offroad 21 clip/342 帧；
  safety_monitor 全程 0 触发。详见 `docs/Stage1_Batch30_Report.md`。
- **"链路跑通" ≠ "驾驶安全"**：报告必须区分这两者。

---

## 13. 时间与磁盘预算

| 项目 | 数值 |
|---|---|
| alpasim-base 镜像 | ~12 GB |
| 权重 | OmniDreams 3.84 GB、R1 22.2 GB、1.5 ~22 GB、VaVAM 1.75 GB |
| caches / venvs（/mnt） | ~37 GB / ~24 GB |
| 30 clip 批量产物（含 MP4） | 5.4 GB |
| 单 clip 闭环 | 数分钟（含场景下载/模型加载） |
| 30 clip 批量 | 约 1h44m，平均 3.45 min/clip（模型只加载一次，严格串行） |

收尾：停 renderer，`nvidia-smi` 两卡显存回到 ~14 MiB，无遗留容器。

---

## 14. 故障排查速查

| 症状 | 原因 / 处理 |
|---|---|
| 并发 clip 报 `Session not found` | renderer 单 session；按 §11.3 强制全串行，用 run_method=NONE 验证 |
| clip 起步即报 `calibration_estimate.parquet not found` | 该 USDZ 缺标定；按 §11.1 预检并换 clip |
| driver 报 gated 401（CR2） | extras 注入 `HF_HUB_OFFLINE/​TRANSFORMERS_OFFLINE=1`，CR2 processor 文件在 HF 缓存中齐备 |
| renderer 报 plugin `.so cannot open shared object file` | JIT 编译失败；按 §7.2 手动编译看真实错误，检查 CUDA_HOME 与版本锁齐 |
| `Unsupported .version 9.4`（ptxas） | nvcc/nvvm/crt 版本不一致，全部锁 13.0.88 |
| torchrun 卡在 TCPStore 5 分钟 | `/etc/hosts` 加 `127.0.0.1 pc_3` |
| docker build 内 uv "No solution found" | `.dockerignore` 放行 `uv.lock`，sync 加 `--frozen` |
| flash-attn 触发源码编译失败 | 一律 `uv run --no-sync`，CUDA_HOME 指向 /mnt/cuda13 |
| wizard 报 scenes 参数错误 | `scene_ids` 与 `test_suite_id` 只设一个 |
| 下载留下 9GB `.incomplete` | `ls -la` 间隔观察确认非增长后删除，重跑同一 hf 命令 |

---

## 15. Git 纪律

- 每个 commit author = **Junchuan Zhang <zjunchuan@gmail.com>**，提交后用
  `git log -1 --format='%an <%ae>'` 核实；message 末尾加
  `Co-Authored-By: Claude Code <noreply@anthropic.com>`。
- 凭据/密钥不得进入仓库；提交前对改动扫描 `hf_…`、`ghp_…`、`nvapi-…`。
- `repos/` 内层仓库的改动不在外层跟踪范围，重建时按 §6、§9 重新应用。
