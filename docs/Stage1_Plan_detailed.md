# Stage 1 超详细执行计划（AI Agent 本地无歧义执行版）

> 版本：v1.0　编制日期：2026-09-27
> 上游输入：`~/simulation/docs/Stage1_Plan.md`（原始 5-Task 计划）
> 适用执行者：AI Coding Agent（Claude Code / Cursor / Devin 等），在本机以用户 `vipuser` 身份执行
> 执行铁律：**严格按 Task 0 → Task 5 顺序执行；每个 Task 末尾运行对应的 `scripts/check_taskN.py`，只有打印 `[SUCCESS]` 才允许进入下一 Task（Gate 机制）。**

---

## 0. 文档导读

- 第 1 节：环境事实表（已实测，直接采信）
- 第 2 节：全局变量与 `scripts/env.sh`（所有步骤的单一事实源）
- 第 3 节：与原计划的关键差异 / 勘误（联网调研结论，含来源）
- 第 4 节：Task 0 —— 仓库初始化、目录骨架、环境变量落盘
- 第 5 节：Task 1 —— 系统预检、apt 依赖、Docker + NVIDIA Container Toolkit、Rust、数据盘
- 第 6 节：Task 2 —— 代码仓库克隆与真实环境安装
- 第 7 节：Task 3 —— 模型权重与场景资产下载、校验
- 第 8 节：Task 4 —— Driver / Renderer 独立冒烟测试（按真实架构）
- 第 9 节：Task 5 —— 最小闭环仿真 + MP4 导出与有效性断言
- 第 10 节：风险与回退预案
- 第 11 节：时间 / 磁盘预算估算表
- 第 12 节：交付物清单
- 第 13 节：Sources

执行完成后的补充记录（本计划之外的实测进展）：
- [Stage1_Reproduction_Guide.md](Stage1_Reproduction_Guide.md)：**端到端复现指南**，从零部署到 VaVAM/R1/A15 单次与批量闭环；
- [Stage1_Complete_1.md](Stage1_Complete_1.md)：环境搭建全过程、VaVAM-B→R1→A15 单 clip 闭环、踩坑与复查纠正；
- [Stage1_Batch30_Report.md](Stage1_Batch30_Report.md)：A15 × 30 个新场景批量闭环（30/30 跑通，10% clean），含批量脚本、分析方法与系统性教训。

凭据约定（**全文不出现任何明文 token / 密码**）：

| 用途 | 凭据文件（Agent 执行时读取，禁止回显、禁止写入任何被 git 跟踪的文件） |
|---|---|
| HuggingFace token / GitHub token / NVIDIA NGC API Key | `~/access/user_access_methods.txt` |
| sudo 密码 | `~/access/access_methods.txt` |

---

## 1. 环境事实表（2026-09-27 实测）

| 项 | 实测值 | 对计划的影响 |
|---|---|---|
| GPU | 2 × NVIDIA A100-SXM4-**80GB**（81920 MiB/卡），当前均 14 MiB 空闲 | 原计划"40GB 卡"有误；VRAM 预算按 80GB 重设 |
| 驱动 | 580.178.04（内核模块编译于 2026-07-07） | 支持 CUDA 13.0，向后兼容 CUDA 12.x 容器（见第 3 节勘误） |
| OS | Ubuntu 22.04.3 LTS（jammy） | Docker / toolkit 一律用 **jammy** 仓库 |
| 内核 | 6.8.0-138-generic（HWE） | — |
| 内存 / Swap | 94 GiB RAM（可用约 90 GiB）/ 2 GiB swap | 充足 |
| 根分区 | `/dev/vda3` ext4，196G，**可用约 130G** | 大文件严禁落根分区；剩余 <50G 触发红线告警 |
| 数据盘 | **当前整机块设备中不存在第二块盘**（`lsblk` 仅见 vda）；`/mnt`、`/mnt/data` 已存在且为空（root 所有） | 500GB 云盘需用户稍后挂载；计划提供"盘未到位 / 盘到位"双模式 |
| 已装 | `/usr/bin/git`；conda 环境 `cc`（`~/.conda/envs/cc/bin/python3`，Python 3.10.21） | — |
| 未装 | docker、nvidia-ctk、ffmpeg、rustc、git-lfs | Task 1 全部安装 |
| sudo | `vipuser` 有 sudo 权限 | 密码从 `~/access/access_methods.txt` 读取 |

---

## 2. 全局变量定义（单一事实源）

所有命令都必须先 `source ~/simulation/scripts/env.sh`。该文件由 Task 0 创建；**数据盘是否到位决定 `STORAGE_ROOT` 取值**。

### 2.1 变量清单

| 变量 | 盘未到位时（默认） | 盘到位后 | 说明 |
|---|---|---|---|
| `REPO_ROOT` | `~/simulation` | `~/simulation` | 代码与本计划唯一开发根目录（原计划的 `/workspace/av_demo` 作废） |
| `STORAGE_ROOT` | `~/simulation/_local_storage` | `/mnt/data`（或 `/mnt`，挂载时确认） | 全部大体积数据根，可配置 |
| `REPOS_DIR` | `$REPO_ROOT/repos` | 同左 | 代码仓库（体积累加可控，保留在根分区便于 git 管理） |
| `WEIGHTS_DIR` | `$STORAGE_ROOT/weights` | 同左 | 模型权重 |
| `ASSETS_DIR` | `$STORAGE_ROOT/assets` | 同左 | 场景 / HDMap 资产 |
| `ARTIFACTS_DIR` | `$REPO_ROOT/artifacts`（小产物） | `$STORAGE_ROOT/artifacts`（大产物时可改） | 视频 / 帧 / 日志 |
| `CACHES_DIR` | `$STORAGE_ROOT/caches` | 同左 | 各类缓存总目录 |
| `HF_HOME` / `HUGGINGFACE_HUB_CACHE` | `$CACHES_DIR/hf` | 同左 | HuggingFace 缓存 |
| `TORCH_HOME` | `$CACHES_DIR/torch` | 同左 | torch hub 缓存 |
| `TRITON_CACHE_DIR` | `$CACHES_DIR/triton` | 同左 | torch.compile / triton 编译缓存 |
| `PIP_CACHE_DIR` | `$CACHES_DIR/pip` | 同左 | pip 缓存 |
| `CARGO_HOME` / `RUSTUP_HOME` | `$CACHES_DIR/cargo` / `$CACHES_DIR/rustup` | 同左 | Rust 工具链（避免占根分区） |
| `VENV_DIR` | `$STORAGE_ROOT/venvs/sim` | 同左 | Python 虚拟环境（位于数据盘；`$REPO_ROOT/venv` 软链指向它） |
| `DOCKER_DATA_ROOT` | —（未装 Docker） | `$STORAGE_ROOT/docker-data` | 写入 `/etc/docker/daemon.json` |
| `HF_ENDPOINT` | 默认 `https://huggingface.co`；GFW 故障时切 `https://hf-mirror.com` | 同左 | 见第 10 节 |

### 2.2 目录变量（`~/simulation` 下骨架）

`repos/ weights(软链) assets(软链) configs/ artifacts/ scripts/ docs/` —— 其中 `weights`、`assets`、`caches`、`venvroot` 为指向 `$STORAGE_ROOT` 的**软链接**，使代码与文档中的相对路径保持稳定，数据盘到位后仅需重建软链（见 Task 1.7 迁移）。

---

## 3. 与原计划的关键差异 / 勘误（联网核实日期：2026-09-27）

> 原计划写作时间较早，多处细节经与官方仓库 / HuggingFace / 官方文档核对为臆造或已过期。以下每条给出原计划说法、真实情况、来源。执行时**以本表与后续 Task 命令为准**。

### 3.1 路径与磁盘
1. **GPU 显存**：原计划 VRAM 预算按 40GB（`vram_budget_gb: 40/48`）。实测两卡均为 **A100-80GB**；且官方 TUTORIAL 给出 Alpamayo-R1 单样本约 40GB、带 CFG 约 60GB，FlashDreams 单视预设约 48GB——在 80GB 卡上有充足余量（来源：`docs/TUTORIAL.md`，https://raw.githubusercontent.com/NVlabs/alpasim/main/docs/TUTORIAL.md）。
2. **开发根目录**：原计划 `/workspace/av_demo`。现改为 **`~/simulation`**（用户要求；`/workspace` 不存在且不创建）。
3. **CUDA 版本**：原计划要求"CUDA 12.8+"作为宿主要求——AlpaSim 官方文档同样要求宿主机 CUDA 12.8+ / 驱动 ≥570，但那指**渲染容器基线与驱动门槛**；本机驱动 580（CUDA 13.0）满足门槛且向后兼容 CUDA 12.x，无需在宿主机安装 CUDA toolkit（来源：https://docs.nvidia.com/deploy/cuda-compatibility/ ；AlpaSim `docs/ONBOARDING.md`）。
4. **500GB 数据盘尚未出现**：2026-09-27 `lsblk` 只有系统盘 vda，"已挂载或待挂载"的说法不准确——盘在块设备层尚不可见，需用户在云控制台完成"创建/挂载卷→在实例内识别新设备"两步。

### 3.2 关于 AlpaSim（仓库本身真实存在，但细节出入大）
5. **安装方式**：原计划暗示简单 venv + pip install grpcio 即可使用。真实情况：AlpaSim 是 **uv workspace monorepo**（13+ 子包，根 pyproject 默认不装依赖），要求 **uv ≥ 0.9.17、Python 3.11–3.12（`.python-version=3.12`）**；标准路径为 `source setup_local_env.sh`（编译 gRPC proto + 编译 Rust 扩展 + `uv sync --extra all`），或直接用根目录 **Dockerfile**（基础 `nvidia/cuda:12.4.1-cudnn-devel-ubuntu22.04`）。无 requirements.txt、无 environment.yml（来源：https://github.com/NVlabs/alpasim 的 `pyproject.toml`、`setup_local_env.sh`、`Dockerfile`）。
6. **Rust 依赖属实但写法要改**：真实 Rust crate 是 **`src/utils_rs/`**（pyo3 0.23 + numpy 0.23 + glam，maturin 构建），由 `uv pip install --force-reinstall -e src/utils_rs` 或 Dockerfile 内 maturin 编译；不是可选项，proto 也要先编译（来源：`src/utils_rs/Cargo.toml`、`setup_local_env.sh`）。
7. **入口命令全部不同**：原计划 `python3 -m alpasim.services.driver`、`python3 -m alpasim.orchestrator.run`、`python3 -m alpasim.utils.download_sample_scene` **均不存在**。真实入口：`alpasim_wizard`（统一部署/运行入口，由 wizard 根据选择动态生成 docker-compose 与网络配置）、`alpasim_driver_main`、`alpasim-eval`、`physics_server`、`catk_trafficsim_server`、`asl-to-frames`；模块入口如 `python -m alpasim_runtime.simulate`（来源：各子包 `pyproject.toml [project.scripts]`、`src/wizard/configs/base_config.yaml`）。
8. **gRPC 微服务真实存在，但 50052/50053 纯属臆造**：proto 在 `src/grpc/alpasim_grpc/v0/`（renderer=sensorsim.proto、driver=egodriver.proto、physics、traffic、controller、runtime 共 6 服务）；端口由 wizard **从 baseport 6000 起动态分配**，写入运行目录的 `generated-network-config.yaml`；全文 grep 不到 5005x（来源：`src/grpc/README.md`、`src/wizard/alpasim_wizard/context.py`、`docs/DESIGN.md`）。
9. **没有内置 docker-compose.yml**：compose 文件由 wizard 运行时生成；仓库内无此文件（来源：GitHub 递归树 747 条目核对）。
10. **数据/场景下载方式**：原计划的 `download_sample_scene` 模块不存在；不存在 S3 预签名/gdown 通道。真实情况：场景走 **HuggingFace gated 数据集 `nvidia/PhysicalAI-Autonomous-Vehicles-NuRec`**（gated=auto，页面接受条款即时开通），由 wizard 经 `hf_hub_download` 自动下载 zip→解压 `.usdz`；场景集版本 `public_2601`(revision `26.01`) / `public_2604`(`26.04`) 等（来源：https://huggingface.co/datasets/nvidia/PhysicalAI-Autonomous-Vehicles-NuRec ；`data/scenes/README.md`、`src/wizard/alpasim_wizard/scenes/sceneset.py`）。
11. **渲染器归属**：AlpaSim 自身不含渲染器实现。默认渲染器是 NGC 镜像 **`nvcr.io/nvidia/nre/nre-ga:26.04`**（NuRec Neural Rendering Engine，外部容器，sensorsim gRPC 协议）；**OmniDreams 是可选视频模型后端，经 FlashDreams 框架（https://github.com/NVIDIA/flashdreams）构建本地镜像后以 `deploy=managed_flashdreams` 接入**（来源：`src/wizard/configs/base_config.yaml`、`docs/VIDEO_MODEL.md`、`docs/TUTORIAL.md`）。
12. **默认教程 driver 不是 Alpamayo**：官方 TUTORIAL 的最小路径用 **VaVAM-B**（Valeo VideoActionModel，权重从 GitHub Releases 下载，约 1–2GB 级）以在单卡快速打通；Alpamayo-R1 + NRE 是后续更大配置。本计划 Stage 1 采用"分层打通"策略：先用官方 VaVAM+NRE 默认路径打通闭环，再切换到 Alpamayo-R1（GPU0）+ FlashDreams/NRE（GPU1）双卡拓扑（来源：`docs/TUTORIAL.md`、`data/download_vavam_assets.sh`）。

### 3.3 关于 Alpamayo 权重
13. **repo_id 错误**：`nvidia/Alpamayo-1` 不存在；真实公开仓库为 **`nvidia/Alpamayo-R1-10B`（22.16 GB，5 个 safetensors 分片，2026-09-27 实测非 gated，OpenMDW-1.1）**；另有 `nvidia/Alpamayo-1.5-10B`（22GB）、`nvidia/Alpamayo2-Super`（71.65GB，34B，峰值显存约 72GB）。CES 2026 起 "Alpamayo-R1" 对外改称 "Alpamayo 1"，但仓库名保留 `-R1-10B`（来源：https://huggingface.co/nvidia/Alpamayo-R1-10B ；HF API `?search=alpamayo`）。
14. **权重形态**：全部为 **safetensors 分片 + index**，无 .pt；R1 仓库**不含 tokenizer**（由代码包内提供），要求 transformers ≥ 4.57.1、PyTorch ≥ 2.8、Flash-Attention 2（可回退 SDPA）（来源：HF 文件树与 config.json）。
15. **输入/输出形态**：不是输出单个 steering 标量。输入为 **4 相机 × 4 帧（10Hz，0.4s 窗）+ 文本指令 + 16 点自车历史位姿**；输出为 **CoC 因果推理文本 + 未来 6.4s/64 点轨迹（xyz+rot）**，内部动作为加速度+曲率 unicycle 表示（来源：模型卡 config.json；`NVlabs/alpamayo` 的 `src/alpamayo_r1/test_inference.py`）。
16. **推理代码**：真实仓库 **https://github.com/NVlabs/alpamayo**（Python 3.12 + uv：`uv venv && uv sync --active`，运行 `python src/alpamayo_r1/test_inference.py`）；训练配方在 https://github.com/NVlabs/alpamayo-recipes（来源：GitHub API 证实两仓库存在）。

### 3.4 关于容器与 NGC
17. **GPU 穿透验证镜像**：`nvidia/cuda:12.8.0-base-ubuntu22.04` 经 Docker Hub API 核实**真实存在**（约 95MB，2025-01 推送），可用于穿透验证；`nvidia-smi` 由 toolkit 从宿主挂载，base 镜像内可运行。
18. **NGC 登录**：`docker login nvcr.io --username '$oauthtoken'`（密码为 NGC API Key）；NRE 镜像在 `nvcr.io/nvidia/nre/...`，需要登录后拉取（来源：NGC User Guide）。
19. **Docker 安装路径**：用官方 `download.docker.com` deb822 仓库（`docker.sources` + `docker.asc`），不用 Ubuntu 的 docker.io；nvidia-container-toolkit 用 `nvidia.github.io/libnvidia-container` 仓库，安装后必须 `nvidia-ctk runtime configure --runtime=docker` 并重启 docker（来源见第 13 节 Sources）。
20. **headless 渲染**：容器需 `NVIDIA_DRIVER_CAPABILITIES=graphics,compute,utility`（默认仅 utility,compute，不带 graphics 时 OpenGL/EGL 不可用）；NRE-GA 作为 NGC 镜像通常已内置 EGL 栈，仍以镜像文档为准（来源：container-toolkit user-guide）。


---

## 4. Task 0：仓库初始化、目录骨架与全局环境落盘

**Gate：完成后运行 `python3 ~/simulation/scripts/check_task0.py`，打印 `[SUCCESS]` 方可进入 Task 1。**

### 4.1 创建目录骨架与 STORAGE_ROOT 实体目录

```bash
# 代码侧骨架（在根分区）
mkdir -p ~/simulation/{repos,configs,artifacts,scripts,docs}

# 数据侧实体目录：盘未到位时落在 ~/simulation/_local_storage（受 .gitignore 忽略）
mkdir -p ~/simulation/_local_storage/{weights,assets,caches,artifacts,venvs}

# 软链接：代码中永远使用 ~/simulation/weights 等稳定路径
ln -sfn ~/simulation/_local_storage/weights  ~/simulation/weights
ln -sfn ~/simulation/_local_storage/assets   ~/simulation/assets
ln -sfn ~/simulation/_local_storage/caches   ~/simulation/caches
ln -sfn ~/simulation/_local_storage/venvs    ~/simulation/venvroot
```

预期：`ls -l ~/simulation | grep '^l'` 可见 4 个软链。数据盘到位后由 5.7 的迁移步骤把软链改指新盘。

### 4.2 写入全局环境脚本 `scripts/env.sh`

用任意方式新建 `~/simulation/scripts/env.sh`，内容**逐字如下**：

```bash
# ~/simulation/scripts/env.sh —— 所有步骤统一 source 的单一事实源（被 git 跟踪，禁止写入任何密钥）
# shellcheck shell=bash

# ---- 开关：数据盘已格式化并挂载后改为 true（或调用时临时传入 USE_STORAGE_DISK=true）----
export USE_STORAGE_DISK="${USE_STORAGE_DISK:-false}"

export REPO_ROOT="$HOME/simulation"
if [ "$USE_STORAGE_DISK" = "true" ]; then
  export STORAGE_ROOT="${STORAGE_ROOT:-/mnt/data}"   # 盘到位后的挂载点，可按实际改为 /mnt
else
  export STORAGE_ROOT="${STORAGE_ROOT:-$REPO_ROOT/_local_storage}"
fi

export REPOS_DIR="$REPO_ROOT/repos"
export WEIGHTS_DIR="$STORAGE_ROOT/weights"
export ASSETS_DIR="$STORAGE_ROOT/assets"
export CACHES_DIR="$STORAGE_ROOT/caches"
export ARTIFACTS_DIR="$REPO_ROOT/artifacts"

export VENV_COMMON="$STORAGE_ROOT/venvs/sim"
export DOCKER_DATA_ROOT="$STORAGE_ROOT/docker-data"

# ---- 各类缓存全部指向 STORAGE_ROOT ----
export HF_HOME="$CACHES_DIR/hf"
export HUGGINGFACE_HUB_CACHE="$HF_HOME/hub"
export HF_HUB_ENABLE_HF_TRANSFER="${HF_HUB_ENABLE_HF_TRANSFER:-1}"
export TORCH_HOME="$CACHES_DIR/torch"
export TRITON_CACHE_DIR="$CACHES_DIR/triton"
export PIP_CACHE_DIR="$CACHES_DIR/pip"
export UV_CACHE_DIR="$CACHES_DIR/uv"
export CARGO_HOME="$CACHES_DIR/cargo"
export RUSTUP_HOME="$CACHES_DIR/rustup"
export TOKENIZERS_PARALLELISM=false
export HF_ENDPOINT="${HF_ENDPOINT:-https://huggingface.co}"   # GFW 故障时改为 https://hf-mirror.com

export PATH="$HOME/.local/bin:$CARGO_HOME/bin:$PATH"

# ---- 凭据：执行时从本机凭据文件解析（不在本文件、命令行或日志中落明文）----
_ACCESS_FILE="$HOME/access/user_access_methods.txt"
if [ -f "$_ACCESS_FILE" ]; then
  export HF_TOKEN="$(sed -n 's/^huggingface token: *//p' "$_ACCESS_FILE" | head -1)"
  export GITHUB_TOKEN="$(sed -n 's/^github access token: *//p' "$_ACCESS_FILE" | head -1)"
  export NGC_API_KEY="$(sed -n 's/^NVIDIA NGC API Key: *//p' "$_ACCESS_FILE" | head -1)"
fi

# ---- 工具函数 ----
# sudo：自动从凭据文件喂密码
sudosw() {
  local _pw
  _pw="$(sed -n 's/^sudo password: *//p' "$HOME/access/access_methods.txt" | head -1)"
  printf '%s\n' "$_pw" | sudo -S -p '' "$@"
}

# docker：有权限直接跑，否则自动 sudo
dk() {
  if docker info >/dev/null 2>&1; then docker "$@"; else
    local _pw
    _pw="$(sed -n 's/^sudo password: *//p' "$HOME/access/access_methods.txt" | head -1)"
    printf '%s\n' "$_pw" | sudo -S -p '' docker "$@"
  fi
}

# STORAGE 是否已位于独立挂载盘
sim_storage_ready() {
  [ "$(stat -c '%d' "$STORAGE_ROOT" 2>/dev/null)" != "$(stat -c '%d' / 2>/dev/null)" ]
}

mkdir -p "$WEIGHTS_DIR" "$ASSETS_DIR" "$CACHES_DIR" "$ARTIFACTS_DIR" \
         "$HF_HOME" "$TORCH_HOME" "$TRITON_CACHE_DIR" "$PIP_CACHE_DIR" \
         "$UV_CACHE_DIR" "$CARGO_HOME" "$RUSTUP_HOME"
```

### 4.3 写入 `.gitignore`

新建 `~/simulation/.gitignore`，内容如下：

```gitignore
# 第三方代码与大体积数据
repos/
_local_storage/
weights/
assets/
caches/
venvroot/
artifacts/
*.mp4

# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
venv/

# 工具与系统
.omc/
.DS_Store
*.log
*.pid
```

注意：`weights/ assets/ caches/ venvroot/` 同时匹配软链接名，因此软链接不会被提交。

### 4.4 git 初始化与首次提交

```bash
cd ~/simulation
git init -b main
# 若全局未配置身份，设置仅本仓库生效的占位身份（可后续修改）
git config user.name >/dev/null 2>&1 || git config user.name "vipuser"
git config user.email >/dev/null 2>&1 || git config user.email "vipuser@localhost"

git add .gitignore scripts docs
git commit -m "chore: initialize Stage 1 workspace with env script and detailed plan

Co-Authored-By: Claude Code <noreply@anthropic.com>"
git log --oneline
```

预期：输出一条初始 commit；`git status` 显示 clean（`repos/ _local_storage/` 等均被忽略）。

### 4.5（可选）登录 shell 自动 source

为让每个新终端都带齐变量，可在 `~/.bashrc` 末尾追加一行（不强制；所有文档命令都会显式 source）：

```bash
echo '[ -f "$HOME/simulation/scripts/env.sh" ] && source "$HOME/simulation/scripts/env.sh" >/dev/null 2>&1' >> ~/.bashrc
```

### 4.6 验证脚本 `scripts/check_task0.py`（完整内容）

```python
#!/usr/bin/env python3
"""Task 0 gate：工作区初始化、软链、env.sh、密钥泄漏扫描。"""
import os, subprocess, sys, pathlib

ROOT = pathlib.Path.home() / "simulation"
SECRET_MARKERS = ("hf_", "ghp_", "nvapi-")

def run_bash(cmd: str):
    return subprocess.run(["bash", "-c", f"source {ROOT}/scripts/env.sh >/dev/null 2>&1; {cmd}"],
                          capture_output=True, text=True)

def main():
    assert (ROOT / ".git").is_dir(), "缺少 .git：未执行 git init"
    assert (ROOT / ".gitignore").is_file(), "缺少 .gitignore"
    assert (ROOT / "scripts" / "env.sh").is_file(), "缺少 scripts/env.sh"

    for name in ("repos", "weights", "assets", "configs", "artifacts", "scripts", "docs"):
        assert (ROOT / name).exists(), f"缺少目录/链接: {name}"
    for link, sub in (("weights", "weights"), ("assets", "assets"), ("venvroot", "venvs")):
        p = ROOT / link
        assert p.is_symlink(), f"{link} 不是软链接"
        assert os.readlink(str(p)).endswith(sub), f"{link} 软链目标异常: {os.readlink(str(p))}"

    # env.sh 可被 source 且关键变量有值
    for var in ("REPO_ROOT", "STORAGE_ROOT", "HF_HOME", "TORCH_HOME", "CARGO_HOME"):
        r = run_bash(f"echo -n ${var}")
        assert r.stdout.strip(), f"env.sh 未导出 {var}"

    # 密钥泄漏扫描：scripts/configs/docs 中禁止出现真实凭据前缀
    for base in ("scripts", "configs", "docs"):
        d = ROOT / base
        if not d.exists():
            continue
        for f in d.rglob("*"):
            if f.is_file() and f.suffix in (".py", ".sh", ".md", ".yaml", ".yml", ".json"):
                txt = f.read_text(errors="ignore")
                for m in SECRET_MARKERS:
                    assert m not in txt, f"疑似凭据泄漏: {f} 含 {m}"

    # git 至少有一条提交
    log = subprocess.run(["git", "-C", str(ROOT), "log", "--oneline"],
                         capture_output=True, text=True)
    assert log.returncode == 0 and log.stdout.strip(), "git 仓库无提交记录"
    print("[SUCCESS] Task 0 passed: git repo, skeleton, symlinks, env.sh verified, no secret leakage.")

if __name__ == "__main__":
    main()
```

运行：

```bash
source ~/simulation/scripts/env.sh
python3 ~/simulation/scripts/check_task0.py
```

---

## 5. Task 1：系统预检、apt 依赖、Docker + NVIDIA Container Toolkit、Rust/uv、数据盘

**Gate：`python3 ~/simulation/scripts/check_task1.py` 输出 `[SUCCESS]`。**

### 5.1 系统预检（只读）

```bash
source ~/simulation/scripts/env.sh

# OS / 内核（预期：Ubuntu 22.04 jammy，内核 6.8.0-138-generic）
grep PRETTY_NAME /etc/os-release; uname -r

# GPU（预期：2× A100-SXM4-80GB，驱动 580.178.04，两卡显存占用约 14MiB）
nvidia-smi --query-gpu=index,name,memory.total,memory.used,driver_version --format=csv

# 内存 / 磁盘（预期：RAM 94Gi；根分区可用约 130G）
free -h
df -h /
[ "$(df --output=avail -BG / | tail -1 | tr -dc '0-9')" -ge 50 ] || echo "[WARN] 根分区剩余 <50G，红线！"

# 块设备（确认数据盘是否已可见；预期当前只有 vda）
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT
```

失败排查分支：
- `nvidia-smi` 报错 → 驱动未加载，`sudosw systemctl status nvidia` 查日志，不要自行重装驱动。
- 根分区剩余 <50G → 先执行 5.6/5.7 挂盘，或清理空间后再继续。

### 5.2 安装 apt 基础依赖

```bash
sudosw apt-get update
sudosw apt-get install -y \
    build-essential cmake pkg-config \
    git git-lfs ffmpeg jq curl wget ca-certificates \
    libsm6 libxext6 libgl1 libegl1 libgles2 libglib2.0-0 \
    python3-pip python3-venv
git lfs install   # 预期：Git LFS initialized（当前用户级配置）
ffmpeg -version | head -1
```

说明：`libgl1/libegl1/libgles2` 是 headless EGL 光栅化（OmniDreams 的 Ludus 渲染器用 EGL surfaceless 上下文）的加载器；NVIDIA vendor 实现由 580 驱动提供。

### 5.3 安装 Docker Engine（官方 download.docker.com 仓库，jammy）

```bash
sudosw apt-get install -y ca-certificates curl
sudosw install -m 0755 -d /etc/apt/keyrings
sudosw curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudosw chmod a+r /etc/apt/keyrings/docker.asc

printf 'Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: %s\nComponents: stable\nArchitectures: %s\nSigned-By: /etc/apt/keyrings/docker.asc\n' \
  "$(. /etc/os-release && echo "$UBUNTU_CODENAME")" "$(dpkg --print-architecture)" \
  | sudosw tee /etc/apt/sources.list.d/docker.sources >/dev/null

sudosw apt-get update
sudosw apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
docker --version
```

失败排查：若 `download.docker.com` 不通，参见第 10 节镜像/代理方案，切勿改用旧版 `docker.io` 混装。

### 5.4 安装 NVIDIA Container Toolkit 并验证双卡穿透

```bash
sudosw apt-get install -y --no-install-recommends gnupg2
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudosw gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudosw tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null

sudosw apt-get update
sudosw apt-get install -y nvidia-container-toolkit
nvidia-ctk --version

sudosw nvidia-ctk runtime configure --runtime=docker
sudosw systemctl restart docker

# 免 sudo 使用 docker（当前 shell 不立即生效；env.sh 的 dk 函数会自动处理）
sudosw usermod -aG docker vipuser
```

双卡穿透验证（镜像 tag 已于 2026-09-27 在 Docker Hub 核实存在，约 95MB，首次需联网拉取）：

```bash
dk run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu22.04 nvidia-smi -L
# 预期输出两行：GPU 0: NVIDIA A100-SXM4-80GB ... / GPU 1: NVIDIA A100-SXM4-80GB ...

dk run --rm --gpus device=0 nvidia/cuda:12.8.0-base-ubuntu22.04 nvidia-smi -L
# 预期仅 1 行，证明可按卡分配
```

失败排查：
- 容器内只看到 1 张卡 → 确认命令是 `--gpus all`；`sudosw nvidia-ctk runtime configure --runtime=docker` 后是否重启了 docker。
- 拉取超时 → 第 10 节 Docker registry mirror 方案；或代理 `HTTP_PROXY` 配置到 `/etc/systemd/system/docker.service.d/http-proxy.conf`。
- `could not select device driver "" with capabilities: [[gpu]]` → toolkit 未配置成功，重做 configure + restart。

### 5.5 安装 Rust 与 uv（缓存与工具链全部落在 STORAGE_ROOT）

```bash
# Rust（CARGO_HOME/RUSTUP_HOME 已由 env.sh 指到 $CACHES_DIR）
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
source "$CARGO_HOME/env" 2>/dev/null || true
rustc --version   # 预期：rustc 1.xx.x

# uv（官方独立安装器）
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$HOME/.local/bin" sh
uv --version      # 预期：uv >= 0.9.x（AlpaSim 要求 ≥0.9.17；低于则 uv self update）

# 由 uv 托管安装 Python 3.12（Ubuntu 22.04 自带 3.10；无需 deadsnakes PPA）
uv python install 3.12
uv python list | grep 3.12
```

### 5.6 数据盘：云控制台挂载（用户操作）+ 机内识别

当前 `lsblk` 无第二块盘。**用户需先在云控制台把 500GB 卷 attach 到本机**，然后 Agent 执行：

```bash
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL
# 预期出现新设备，如 /dev/vdb（500G）。记录设备名，以下以 $DISK 表示
DISK=/dev/vdb   # ← 按 lsblk 实际结果修改
```

**破坏性操作红线**：
- 若新设备已有 FSTYPE/UUID 或大小与预期不符，**禁止 mkfs**；先只读挂载检查内容，或向用户确认。
- `mkfs` 仅对"全新空白卷"执行：

```bash
# 仅全新盘！执行前再次确认设备名与容量（500G）
sudosw mkfs.ext4 -L simdata -m 1 "$DISK"
```

### 5.7 数据盘：fstab、挂载、权限、缓存迁移

```bash
UUID=$(sudosw blkid -s UUID -o value "$DISK")
echo "新盘 UUID=$UUID"   # 记录此值

# 挂载点二选一（推荐 /mnt/data；如要挂到 /mnt 本身则改用 /mnt）
MP=/mnt/data
sudosw mkdir -p "$MP"

# nofail 保证缺盘时系统仍可启动；noatime 减少写放大
printf 'UUID=%s %s ext4 defaults,noatime,nofail 0 2\n' "$UUID" "$MP" \
  | sudosw tee -a /etc/fstab >/dev/null
sudosw mount "$MP"
mount -a
df -h "$MP"                       # 预期看到 500G 卷
sudosw chown -R vipuser:vipuser "$MP"
```

把盘到位前的临时数据迁移上盘（迁移脚本）：

```bash
sudosw mkdir -p "$MP"
# 先拷数据（盘挂在 /mnt/data 时，STORAGE_ROOT=/mnt/data）
rsync -aH --info=progress2 ~/simulation/_local_storage/ /mnt/data/

# 改 env.sh 开关并重建软链
sed -i 's#USE_STORAGE_DISK:-false#USE_STORAGE_DISK:-true#' ~/simulation/scripts/env.sh
ln -sfn /mnt/data/weights ~/simulation/weights
ln -sfn /mnt/data/assets  ~/simulation/assets
ln -sfn /mnt/data/caches  ~/simulation/caches
ln -sfn /mnt/data/venvs   ~/simulation/venvroot

# 验证后再删旧数据
source ~/simulation/scripts/env.sh
sim_storage_ready && echo "STORAGE_READY=true"
rm -rf ~/simulation/_local_storage
```

### 5.8 Docker data-root 迁移到数据盘（盘到位后执行）

```bash
sudosw mkdir -p "$DOCKER_DATA_ROOT"
sudosw systemctl stop docker docker.socket containerd
sudosw rsync -aX /var/lib/docker/ "$DOCKER_DATA_ROOT/"

# 在已有 daemon.json 上合并 data-root（nvidia-ctk 已写入 runtime 配置，不可整体覆盖）
sudosw cp /etc/docker/daemon.json /etc/docker/daemon.json.bak
sudosw bash -c "jq '. + {\"data-root\": \"$DOCKER_DATA_ROOT\"}' /etc/docker/daemon.json.bak > /etc/docker/daemon.json"
sudosw systemctl start docker
dk info 2>/dev/null | grep -i "docker root dir"   # 预期：/mnt/data/docker-data
dk images                                          # 预期镜像仍在
```

### 5.9 验证脚本 `scripts/check_task1.py`（完整内容）

```python
#!/usr/bin/env python3
"""Task 1 gate：双 GPU、ffmpeg、Rust、uv、Docker、toolkit、双卡容器穿透、磁盘红线。"""
import os, re, subprocess, sys, shutil, pathlib

ROOT = pathlib.Path.home() / "simulation"
SUDO_PW = re.sub(r"^sudo password: *", "",
                 (pathlib.Path.home() / "access/access_methods.txt").read_text().strip())

def bash(cmd: str, timeout=600):
    return subprocess.run(["bash", "-c", f"source {ROOT}/scripts/env.sh; {cmd}"],
                          capture_output=True, text=True, timeout=timeout)

def docker_cmd(args, timeout=900):
    r = subprocess.run(["docker"] + args, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        r = subprocess.run(["sudo", "-S"] + args, input=SUDO_PW + "\n",
                           capture_output=True, text=True, timeout=timeout)
    return r

def main():
    # 双 GPU，且为 80GB
    r = bash("nvidia-smi -L")
    gpus = [ln for ln in r.stdout.splitlines() if ln.startswith("GPU")]
    assert len(gpus) >= 2, f"期望 >=2 GPU，实际 {len(gpus)}: {r.stdout}{r.stderr}"
    r = bash("nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits")
    for i, mib in enumerate(r.stdout.split()):
        assert int(mib) >= 80000, f"GPU {i} 不是 80GB 卡: {mib} MiB"

    # ffmpeg
    assert shutil.which("ffmpeg"), "ffmpeg 缺失"

    # rustc（位于 CARGO_HOME/bin）
    r = bash("rustc --version")
    assert r.returncode == 0, f"rustc 缺失: {r.stderr}"

    # uv
    r = bash("uv --version")
    assert r.returncode == 0 and "uv" in r.stdout, f"uv 缺失: {r.stderr}"

    # Docker daemon
    info = docker_cmd(["info"])
    assert info.returncode == 0, f"docker daemon 不可用: {info.stderr}"

    # nvidia-ctk
    r = bash("nvidia-ctk --version")
    assert r.returncode == 0, f"nvidia-ctk 缺失: {r.stderr}"

    # 容器双卡穿透（镜像已存在则秒级，否则需拉取）
    run = docker_cmd(["run", "--rm", "--gpus", "all",
                      "nvidia/cuda:12.8.0-base-ubuntu22.04", "nvidia-smi", "-L"])
    assert run.returncode == 0, f"GPU 穿透失败: {run.stderr}"
    cg = [ln for ln in run.stdout.splitlines() if ln.startswith("GPU")]
    assert len(cg) == 2, f"容器内应见 2 张卡，实际 {len(cg)}: {run.stdout}"

    # 根分区红线
    st = os.statvfs("/")
    free_gb = st.f_bavail * st.f_frsize / 1e9
    assert free_gb >= 50, f"根分区剩余 {free_gb:.1f}G < 50G 红线"

    print("[SUCCESS] Task 1 passed: 2x A100-80GB, ffmpeg, rust, uv, docker + toolkit GPU passthrough OK.")

if __name__ == "__main__":
    main()
```

---

## 6. Task 2：代码仓库克隆与真实环境安装

**Gate：`python3 ~/simulation/scripts/check_task2.py` 输出 `[SUCCESS]`。**

真实架构（经仓库源码核实）：
- AlpaSim = uv workspace monorepo，统一入口 `alpasim_wizard`；本地安装走 `setup_local_env.sh`（编译 gRPC proto + 编译 Rust `utils_rs` + `uv sync --extra all`）。
- OmniDreams 作为渲染器，由 **FlashDreams 运行时**承载（`flashdreams-omnidreams` 包，gRPC 服务模块 `omnidreams.grpc.server`）。
- Alpamayo-R1 独立策略代码仓库 `NVlabs/alpamayo`，冒烟脚本 `src/alpamayo_r1/test_inference.py`。

### 6.1 配置 GitHub 凭据（仅本地，防 rate limit；仓库均公开，不配置也可克隆）

```bash
source ~/simulation/scripts/env.sh
git config --global credential.helper store
umask 077
printf 'https://x-access-token:%s@github.com\n' "$GITHUB_TOKEN" > ~/.git-credentials
chmod 600 ~/.git-credentials
umask 022
# 验证（不回显 token）：
git config --global --get credential.helper
```

### 6.2 克隆三个必需仓库（浅克隆，节省空间）

```bash
cd "$REPOS_DIR"
git clone --depth 1 https://github.com/NVlabs/alpasim.git
git clone --depth 1 https://github.com/NVlabs/alpamayo.git
git clone --depth 1 https://github.com/NVIDIA/flashdreams.git
# 可选：OmniDreams 仓库本体（只含 post-training/论文样例，运行闭环不需要）
git clone --depth 1 https://github.com/nv-tlabs/omni-dreams.git || echo "[INFO] omni-dreams clone skipped"
```

失败排查：克隆失败先 `curl -I https://github.com`；GFW 场景见第 10 节（github proxy）。

### 6.3 把 AlpaSim 的大数据子目录导向 STORAGE_ROOT

wizard 默认把 driver 资产放 `data/drivers/`、场景缓存放 `data/nre-artifacts/`（均在仓库内=根分区）。克隆后先建软链：

```bash
cd "$REPOS_DIR/alpasim"
mkdir -p "$STORAGE_ROOT/alpasim-data/drivers" "$STORAGE_ROOT/alpasim-data/nre-artifacts"
rm -rf data/drivers data/nre-artifacts
ln -sfn "$STORAGE_ROOT/alpasim-data/drivers"       data/drivers
ln -sfn "$STORAGE_ROOT/alpasim-data/nre-artifacts" data/nre-artifacts
ls -l data/ | grep -E 'drivers|nre-artifacts'
```

### 6.4 安装 AlpaSim 本地环境（Python 3.12 + Rust 扩展 + proto）

```bash
cd "$REPOS_DIR/alpasim"
# 官方一键脚本：编译 proto、重装 utils_rs（maturin 调用本机 Rust）、uv sync --extra all
bash setup_local_env.sh

# 验证关键产物
uv run python -c "import utils_rs; print('utils_rs OK:', utils_rs.__file__)"
uv run --project src/wizard alpasim_wizard --help | head -20
```

预期：`utils_rs OK: .../site-packages/utils_rs...`；wizard 打印 hydra 风格帮助。
失败排查：
- `utils_rs` 编译失败（pyo3/numpy/glam）：确认 `rustc --version` 可用、`build-essential` 已装、Python 为 3.12（`cat .python-version`）；单独重跑 `uv pip install --force-reinstall -e src/utils_rs`。
- proto 相关 ImportError：重跑 `uv run compile-protos`。
- 磁盘：根分区占用异常增长 → 检查 6.3 软链是否生效，`UV_CACHE_DIR` 是否在 STORAGE_ROOT。

### 6.5 安装 FlashDreams 的 OmniDreams 集成环境

```bash
cd "$REPOS_DIR/flashdreams"
# 与 AlpaSim 一致使用 CUDA 12 依赖组（本机 R580 同时支持默认 CUDA13 组，二选一，不可混用）
uv sync --group cuda12 --package flashdreams-omnidreams

# 验证 gRPC 服务入口可加载
uv run --package flashdreams-omnidreams python -m omnidreams.grpc.server --help | head -25
```

预期：帮助中含 `--pipeline_config_name --host --port --output_format` 等参数。
失败排查：若 group/package 名与当前 main 分支不符，先 `grep -nE 'cuda12|name *=.*omnidreams' pyproject.toml` 与 `uv tree --package flashdreams-omnidreams` 核对后按实际名称执行（文档以 2026-09-27 main 分支为准）。

### 6.6 安装 Alpamayo-R1 环境

```bash
cd "$REPOS_DIR/alpamayo"
uv sync
ls src/alpamayo_r1/test_inference.py
```

说明：该仓库官方要求 Python 3.12 + uv（`uv sync` 自动创建 `.venv`）；`test_inference.py` 首次运行会自动下载示例片段与 `nvidia/Alpamayo-R1-10B`（权重在 Task 3 预下，直接命中缓存）。

### 6.7（可选，容器化路径备用）构建 FlashDreams 镜像

仅当要走 AlpaSim `deploy=managed_flashdreams`（渲染器也容器化）时才需要，约 15–25GB 镜像：

```bash
cd "$REPOS_DIR/flashdreams"
dk build -t flashdreams-base:local -f docker/Dockerfile .
dk build -t flashdreams-alpasim:local -f docker/Dockerfile.alpasim .
```

本计划主路径使用"GPU1 宿主机 gRPC 渲染服务"（5.6.5），不执行本步。

### 6.8 验证脚本 `scripts/check_task2.py`（完整内容）

```python
#!/usr/bin/env python3
"""Task 2 gate：仓库齐备、wizard/utils_rs 可用、omnidreams gRPC 入口可用、alpamayo 就绪。"""
import subprocess, pathlib, sys

ROOT = pathlib.Path.home() / "simulation"

def bash(cmd: str, timeout=900):
    return subprocess.run(["bash", "-c", f"source {ROOT}/scripts/env.sh; {cmd}"],
                          capture_output=True, text=True, timeout=timeout)

def main():
    for repo in ("alpasim", "alpamayo", "flashdreams"):
        d = ROOT / "repos" / repo
        assert d.is_dir() and (d / ".git").exists(), f"仓库缺失: {repo}"

    # alpasim：wizard 可运行
    r = bash(f"cd {ROOT}/repos/alpasim && uv run --project src/wizard alpasim_wizard --help")
    assert r.returncode == 0, f"alpasim_wizard 不可用: {r.stderr[-800:]}"

    # alpasim：Rust 扩展已编译
    r = bash(f"cd {ROOT}/repos/alpasim && uv run python -c 'import utils_rs'")
    assert r.returncode == 0, f"utils_rs 未编译: {r.stderr[-800:]}"

    # flashdreams：omnidreams gRPC server 入口
    r = bash(f"cd {ROOT}/repos/flashdreams && "
             "uv run --package flashdreams-omnidreams python -m omnidreams.grpc.server --help")
    assert r.returncode == 0, f"omnidreams.grpc.server 不可用: {r.stderr[-800:]}"

    # alpamayo：环境与冒烟脚本就位
    a = ROOT / "repos" / "alpamayo"
    assert (a / ".venv").is_dir(), "alpamayo .venv 未创建（uv sync）"
    assert (a / "src" / "alpamayo_r1" / "test_inference.py").is_file(), "test_inference.py 缺失"

    # 防密钥扫描
    for dname in ("scripts", "configs"):
        for f in (ROOT / dname).rglob("*"):
            if f.is_file() and f.suffix in (".py", ".sh", ".yaml", ".yml", ".json"):
                t = f.read_text(errors="ignore")
                for m in ("hf_", "ghp_", "nvapi-"):
                    assert m not in t, f"疑似凭据泄漏: {f}"

    print("[SUCCESS] Task 2 passed: repos cloned, wizard+utils_rs+omnidreams gRPC entry ready.")

if __name__ == "__main__":
    main()
```

---

## 7. Task 3：模型权重与场景资产下载、校验

**前置硬条件：`sim_storage_ready` 返回 true（500GB 盘已挂载）。盘未到位只允许执行本任务的 7.1 授权步骤。**
**Gate：`python3 ~/simulation/scripts/check_task3.py` 输出 `[SUCCESS]`。**

### 7.1 HuggingFace gated 授权（需用户在浏览器手动点击，无法用 API 代替）

以下数据集/权重需登录对应 HF 账号（凭据文件中的 huggingface 账号）后到页面点击 "Agree and access"；类型均为 `auto`，接受后即时生效：

| 仓库 | 用途 | URL |
|---|---|---|
| `nvidia/PhysicalAI-Autonomous-Vehicles-NuRec` | NuRec 场景（.usdz） | https://huggingface.co/datasets/nvidia/PhysicalAI-Autonomous-Vehicles-NuRec |
| `nvidia/PhysicalAI-Autonomous-Vehicles` | Alpamayo 冒烟示例片段 | https://huggingface.co/datasets/nvidia/PhysicalAI-Autonomous-Vehicles |
| `nvidia/omni-dreams-models` | OmniDreams 渲染权重 | https://huggingface.co/nvidia/omni-dreams-models |

Agent 授权完成后做访问自检：

```bash
source ~/simulation/scripts/env.sh
cd "$REPOS_DIR/alpasim"
uv run python - <<'PY'
from huggingface_hub import HfApi
api = HfApi()
for repo, rt in [("nvidia/omni-dreams-models","model"),
                 ("nvidia/PhysicalAI-Autonomous-Vehicles-NuRec","dataset"),
                 ("nvidia/PhysicalAI-Autonomous-Vehicles","dataset")]:
    try:
        info = api.repo_info(repo, repo_type=rt)
        print(f"[OK] access: {repo} gated={getattr(info,'gated',None)}")
    except Exception as e:
        print(f"[FAIL] {repo}: {type(e).__name__}: {e}")
PY
```

预期三行 `[OK]`。若仍报 GatedRepoError → 用户尚未在页面接受条款。

### 7.2 安装全局下载工具（复用 conda cc 环境，缓存仍全部指向 STORAGE_ROOT）

```bash
source ~/simulation/scripts/env.sh
~/.conda/envs/cc/bin/python3 -m pip install -U "huggingface_hub[cli]" hf_transfer
~/.conda/envs/cc/bin/hf --version
```

### 7.3 下载 OmniDreams 渲染权重（3.84 GiB，精确字节数 4,118,900,683）

```bash
~/.conda/envs/cc/bin/hf download nvidia/omni-dreams-models \
  single_view/2b_res720p_30fps_i2v_hdmap_distilled.pt \
  --local-dir "$WEIGHTS_DIR/omnidreams"
ls -l "$WEIGHTS_DIR/omnidreams/single_view/"
```

断点续传：`hf download` 自动续传，失败重跑同一命令即可（建议最多重试 3 次）。

### 7.4 下载 Alpamayo-R1 权重（22.16 GB，公开非 gated）

```bash
~/.conda/envs/cc/bin/hf download nvidia/Alpamayo-R1-10B \
  --local-dir "$WEIGHTS_DIR/alpamayo-r1"
ls "$WEIGHTS_DIR/alpamayo-r1"
# 预期：config.json、model.safetensors.index.json、model-00001..00005-of-00005.safetensors
```

### 7.5 下载 VaVAM-B Driver 资产（约 1.75 GB 权重 + VQ tokenizer jit，官方脚本带 sha256 校验）

```bash
cd "$REPOS_DIR/alpasim"
bash data/download_vavam_assets.sh --model vavam-b
ls -lh data/drivers/vavam/
```

预期目录内含 `VAM_width_1024_pretrained_139k.pt` 与 VQ encoder/decoder `.jit`；脚本任何一步校验失败都会非零退出。

### 7.6 预下载一个 NuRec 场景资产（.usdz 约 1.8GB，作为场景访问与渲染输入验证）

```bash
~/.conda/envs/cc/bin/hf download --repo-type dataset nvidia/PhysicalAI-Autonomous-Vehicles-NuRec \
  --revision 26.01 \
  --local-dir "$ASSETS_DIR/nurec" \
  sample_set/26.01_release/02eadd92-02f1-46d8-86fe-a9e338fed0b6/02eadd92-02f1-46d8-86fe-a9e338fed0b6.usdz
ls -lh "$ASSETS_DIR/nurec/sample_set/26.01_release/02eadd92-02f1-46d8-86fe-a9e338fed0b6/"
```

注意：wizard 在 Task 5 还会把所用场景缓存到其 `data/nre-artifacts`（软链已指向 STORAGE_ROOT），属正常重复，不占根分区。

### 7.7（仅 Task 5-B 需要）Alpamayo-1.5 + Cosmos-Reason2 权重

```bash
~/.conda/envs/cc/bin/hf download nvidia/Alpamayo-1.5-10B      --local-dir "$WEIGHTS_DIR/alpamayo-1.5"
~/.conda/envs/cc/bin/hf download nvidia/Cosmos-Reason2-8B     --local-dir "$WEIGHTS_DIR/cosmos-reason2"
```

两仓库均 gated，需先在页面接受许可（R1 主闭环不需要本步）。

### 7.8 验证脚本 `scripts/check_task3.py`（完整内容）

```python
#!/usr/bin/env python3
"""Task 3 gate：渲染权重、R1 权重（safetensors 完整性）、VaVAM 资产、NuRec 场景齐备。"""
import json, subprocess, pathlib, sys

ROOT = pathlib.Path.home() / "simulation"

def bash(cmd, timeout=1800):
    return subprocess.run(["bash", "-c", f"source {ROOT}/scripts/env.sh; {cmd}"],
                          capture_output=True, text=True, timeout=timeout)

def main():
    r = bash("sim_storage_ready && echo READY")
    assert "READY" in r.stdout, "数据盘未就绪（sim_storage_ready 失败），Task 3 禁止在根分区执行"

    # --- OmniDreams 渲染权重：精确字节数 + ZIP(.pt) 魔数 ---
    od = ROOT / "weights" / "omnidreams" / "single_view" / "2b_res720p_30fps_i2v_hdmap_distilled.pt"
    assert od.is_file(), f"OmniDreams 权重缺失: {od}"
    assert od.stat().st_size == 4118900683, f"OmniDreams 权重大小异常: {od.stat().st_size}"
    assert od.read_bytes()[:2] == b"PK", ".pt 不是合法 ZIP 容器，下载损坏"

    # --- Alpamayo-R1：5 分片 + index + safetensors 可打开 ---
    r1 = ROOT / "weights" / "alpamayo-r1"
    shards = sorted(r1.glob("model-*.safetensors"))
    assert len(shards) == 5, f"R1 应有 5 个分片，实际 {len(shards)}"
    total = sum(s.stat().st_size for s in shards)
    assert total >= 21.5e9, f"R1 总分片仅 {total/1e9:.1f}GB，下载不完整"
    idx = json.loads((r1 / "model.safetensors.index.json").read_text())
    assert idx["metadata"]["total_size"] > 21e9, "R1 index total_size 异常"
    # 用 alpasim 环境里的 safetensors 逐个校验头部与张量条目
    code = "from safetensors import safe_open; " \
           "import glob,sys; [safe_open(f,'pt') for f in sorted(glob.glob(sys.argv[1]))]; print('shards readable')"
    r = bash(f"cd {ROOT}/repos/alpasim && uv run python -c \"{code}\" '{r1}/model-*.safetensors'")
    assert r.returncode == 0 and "readable" in r.stdout, f"safetensors 校验失败: {r.stderr[-500:]}"

    # --- VaVAM-B 资产 ---
    vv = ROOT / "repos" / "alpasim" / "data" / "drivers" / "vavam"
    pt = list(vv.glob("VAM_width_1024_pretrained_*.pt"))
    assert pt and pt[0].stat().st_size > 1.5e9, "VaVAM 策略权重缺失或过小（软链是否生效？）"
    jits = list(vv.glob("*.jit"))
    assert len(jits) >= 2, f"VQ tokenizer/detokenizer 缺失: {len(jits)}"

    # --- NuRec 场景 ---
    sc = ROOT / "assets" / "nurec" / "sample_set/26.01_release" / \
         "02eadd92-02f1-46d8-86fe-a9e338fed0b6" / "02eadd92-02f1-46d8-86fe-a9e338fed0b6.usdz"
    assert sc.is_file() and sc.stat().st_size > 1.7e9, f"NuRec 场景缺失/不完整: {sc}"
    assert sc.read_bytes()[:2] == b"PK", ".usdz 不是合法 ZIP，下载损坏"

    print("[SUCCESS] Task 3 passed: OmniDreams 3.84GB, Alpamayo-R1 22GB(5 shards valid), VaVAM, NuRec scene ready.")

if __name__ == "__main__":
    main()
```

---

## 8. Task 4：Driver / Renderer 独立冒烟测试（按真实架构）

**Gate：`python3 ~/simulation/scripts/check_task4.py` 输出 `[SUCCESS]`。**

真实情况：AlpaSim 不存在固定 50052/50053 端口的 driver/renderer 服务。本任务按真实组件分别冒烟：
- Renderer = GPU1 上的 OmniDreams gRPC `WorldModelService`（FlashDreams 运行时）；
- Driver = GPU0 上的 Alpamayo-R1 最小推理（官方 `test_inference.py`）。

### 8.1 写入 Renderer 启停脚本

新建 `~/simulation/scripts/start_renderer.sh`：

```bash
#!/usr/bin/env bash
# GPU1 OmniDreams gRPC 渲染服务（AlpaSim external_video_model 对接的真实端点）
set -euo pipefail
source "$HOME/simulation/scripts/env.sh"
cd "$REPOS_DIR/flashdreams"

PORT="${RENDERER_PORT:-50051}"
SLUG="${OMNIDREAMS_SLUG:-omnidreams-sv-2steps-chunk2-loc6-lightvae-lighttae-perf}"

mkdir -p "$ARTIFACTS_DIR"
exec env CUDA_VISIBLE_DEVICES=1 \
  uv run --package flashdreams-omnidreams torchrun \
    --standalone --nnodes=1 --nproc_per_node=1 \
    -m omnidreams.grpc.server \
    --pipeline_config_name "$SLUG" \
    --host 0.0.0.0 --port "$PORT" \
    --output_format jpeg --jpeg_quality 90
```

新建 `~/simulation/scripts/stop_renderer.sh`：

```bash
#!/usr/bin/env bash
source "$HOME/simulation/scripts/env.sh"
[ -f "$ARTIFACTS_DIR/renderer.pid" ] && kill "$(cat "$ARTIFACTS_DIR/renderer.pid")" 2>/dev/null || true
pkill -f "omnidreams.grpc.server" 2>/dev/null || true
```

赋权并启动（首次有 checkpoint 加载 + torch.compile / CUDA graph warmup，约需 3–8 分钟）：

```bash
chmod +x ~/simulation/scripts/start_renderer.sh ~/simulation/scripts/stop_renderer.sh
nohup bash ~/simulation/scripts/start_renderer.sh \
  > ~/simulation/artifacts/renderer_gpu1.log 2>&1 &
echo $! > ~/simulation/artifacts/renderer.pid
```

排查分支：
- 日志中 `unknown pipeline config` → slug 在当前版本不可用：`grep -rhoE 'omnidreams-[a-z0-9-]+' "$REPOS_DIR/flashdreams/integrations_v2/omnidreams" | sort -u` 列出真实 slug，换非 perf 版（如 `omnidreams-sv-2steps-chunk2-loc6-lightvae-lighttae`）后重启。
- EGL 报错（`eglMakeCurrent` / no device）：`sudosw ldconfig`；`ls /usr/share/glvnd/egl_vendor.d/` 需有 nvidia vendor json；必要时 `export __EGL_VENDOR_LIBRARY_FILENAMES=/usr/share/glvnd/egl_vendor.d/10_nvidia.json`。
- 显存基线：该单视图 2B 模型 + 文本编码器，GPU1 占用约 20–40GB（官方标称配置最小 48GB 指含峰值余量）。

### 8.2 Driver 冒烟（GPU0，R1 官方推理脚本）

```bash
source ~/simulation/scripts/env.sh
cd "$REPOS_DIR/alpamayo"
CUDA_VISIBLE_DEVICES=0 PYTHONUNBUFFERED=1 \
  uv run python src/alpamayo_r1/test_inference.py \
  2>&1 | tee "$ARTIFACTS_DIR/driver_gpu0.log"
```

预期：脚本正常结束（退出码 0），日志打印 `pred_xyz / pred_rot` 形状（64 个未来轨迹点）；期间 GPU0 显存 >20GB（官方标称约 40GB）。
排查分支：
- 数据 GatedRepoError（示例片段）→ 完成 7.1 的三个授权后重跑。
- Flash Attention 报错 → 脚本/配置回退 SDPA（按仓库 README 的 attention backend 说明），或确认 `flash-attn` 随 uv 依赖装齐。
- OOM → 确认 CUDA_VISIBLE_DEVICES=0 且 GPU0 空闲；R1 单样本 80GB 卡不应 OOM。

### 8.3 验证脚本 `scripts/check_task4.py`（完整内容）

```python
#!/usr/bin/env python3
"""Task 4 gate：
1) Renderer 服务端口在监听且 GPU1 已加载模型（VRAM 阈值）；
2) Driver R1 推理退出码 0，且运行期间 GPU0 VRAM 超阈值。"""
import socket, subprocess, time, pathlib, sys, os

ROOT = pathlib.Path.home() / "simulation"
PORT = 50051
GPU1_MIN_MB = 15000
GPU0_MIN_MB = 20000
START_DEADLINE_S = 900

def bash(cmd, timeout=3600):
    return subprocess.run(["bash", "-c", f"source {ROOT}/scripts/env.sh; {cmd}"],
                          capture_output=True, text=True, timeout=timeout)

def gpu_used():
    r = bash("nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits")
    return [int(x) for x in r.stdout.split()]

def wait_renderer():
    t0 = time.time()
    while time.time() - t0 < START_DEADLINE_S:
        used = gpu_used()
        up = False
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            up = (s.connect_ex(("127.0.0.1", PORT)) == 0)
        print(f"[renderer] port_up={up} VRAM GPU0={used[0]} GPU1={used[1]}MB")
        if up and used[1] > GPU1_MIN_MB:
            return
        # 进程是否已退出
        r = bash("kill -0 $(cat %s/artifacts/renderer.pid) 2>/dev/null && echo ALIVE || echo DEAD" % ROOT)
        if "DEAD" in r.stdout:
            raise RuntimeError("Renderer 进程提前退出，见 artifacts/renderer_gpu1.log")
        time.sleep(15)
    raise RuntimeError("Renderer 在 900s 内未就绪")

def run_driver_with_vram_gate():
    cmd = (f"cd {ROOT}/repos/alpamayo && "
           "CUDA_VISIBLE_DEVICES=0 PYTHONUNBUFFERED=1 uv run python src/alpamayo_r1/test_inference.py")
    p = subprocess.Popen(["bash", "-c", f"source {ROOT}/scripts/env.sh; {cmd}"],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         bufsize=1)
    lines, hit = [], False
    deadline = time.time() + 1800
    for ln in p.stdout:
        lines.append(ln)
        if time.time() % 5 < 1:
            used = gpu_used()
            if used[0] > GPU0_MIN_MB:
                hit = True
        if time.time() > deadline:
            p.kill()
            raise RuntimeError("Driver 推理超时 1800s")
    rc = p.wait()
    log = "".join(lines)
    (ROOT / "artifacts/driver_gpu0.log").write_text(log)
    assert rc == 0, f"Driver 推理失败 rc={rc}，日志尾部: {log[-800:]}"
    assert hit, f"运行期间 GPU0 显存未超 {GPU0_MIN_MB}MB，模型可能未上 GPU0"
    assert ("pred_xyz" in log or "pred_" in log), "日志未见轨迹输出"

def main():
    wait_renderer()
    run_driver_with_vram_gate()
    print("[SUCCESS] Task 4 passed: OmniDreams renderer live on GPU1, Alpamayo-R1 inference OK on GPU0.")

if __name__ == "__main__":
    main()
```

说明：`check_task4.py` 通过后，**GPU1 的 Renderer 保持运行**（nohup 独立会话），直接供 Task 5 使用；Task 5 开头会探活，若已停止则自动重启。

---

## 9. Task 5：最小闭环仿真 + MP4 导出与有效性断言

**Gate：`python3 ~/simulation/scripts/check_task5.py` 输出 `[SUCCESS]`。**

真实数据流（官方 `deploy=external_video_model`）：AlpaSim runtime 居中编排——driver 在 GPU0 产生轨迹 → physics/controller 更新 → runtime 通过 gRPC 把 HD 地图条件与轨迹发给 GPU1 的 OmniDreams `WorldModelService.render_video_chunk` → 返回 JPEG 帧 → runtime 落盘并自动生成评测视频。

### 9.1 确认 Renderer 存活

```bash
source ~/simulation/scripts/env.sh
python3 - <<'PY'
import socket,sys,os,subprocess
alive = False
with socket.socket() as s:
    s.settimeout(2)
    alive = (s.connect_ex(("127.0.0.1", 50051)) == 0)
if not alive:
    print("Renderer 不在运行，重新启动...")
    subprocess.Popen(["bash","-c",
      "nohup bash ~/simulation/scripts/start_renderer.sh > ~/simulation/artifacts/renderer_gpu1.log 2>&1 &"])
    import time; time.sleep(240)
print("renderer check done")
PY
```

### 9.2 运行最小闭环（GPU0 全部本地服务 + GPU1 外部渲染器）

```bash
source ~/simulation/scripts/env.sh
cd "$REPOS_DIR/alpasim"

LOGDIR="$ARTIFACTS_DIR/run_vavam_omnidreams"

CUDA_VISIBLE_DEVICES=0 uv run --project src/wizard alpasim_wizard \
  deploy=external_video_model \
  topology=1gpu \
  driver=vavam_video_model \
  +chunking=8frame \
  scenes.test_suite_id=public_2601_video_model \
  'wizard.external_services.renderer=["127.0.0.1:50051"]' \
  +runtime.endpoints.startup_timeout_s=600 \
  wizard.log_dir="$LOGDIR"
```

说明（均为官方真实选择器，来源：AlpaSim `docs/VIDEO_MODEL.md`、`docs/TUTORIAL.md`）：
- `driver=vavam_video_model` 是视频模型渲染路径下最轻量的官方 driver（VaVAM-B，1.75GB 已在 7.5 下载）；
- `+chunking=8frame`：首个 chunk 5 帧，之后每 chunk 8 帧（30Hz）；
- `topology=1gpu` + `CUDA_VISIBLE_DEVICES=0` 保证本地 6 个服务全部落在 GPU0；渲染器经外部地址走到 GPU1；
- 默认每个场景 1 个 rollout；如默认未取场景，可用 `scenes.scene_ids="['clipgt-02eadd92-02f1-46d8-86fe-a9e338fed0b6']"` 指定（注意 `clipgt-` 前缀）。

失败排查：
- renderer 连接被拒 → 9.1 探活；确认防火墙/端口；容器/服务端日志。
- 场景下载失败 → NuRec 授权（7.1）；HF 网络故障走第 10 节。
- 首帧超时 → 已加 `startup_timeout_s=600`；编译/warmup 慢可加到 900。

### 9.3 截取前 20 帧并编码为 10fps MP4

wizard 自动产出的 rollout 视频位于 `$LOGDIR/eval/videos/`。执行：

```bash
source ~/simulation/scripts/env.sh
LOGDIR="$ARTIFACTS_DIR/run_vavam_omnidreams"

# 找到真实 rollout mp4（排除聚合目录的软链）
INPUT="$(find "$LOGDIR" -type f -name '*.mp4' -path '*videos*' | head -1)"
[ -n "$INPUT" ] || { echo "未找到 rollout mp4"; exit 1; }
echo "$INPUT" > "$ARTIFACTS_DIR/rollout_video_path.txt"
echo "source rollout: $INPUT"

# 清旧帧并抽前 20 帧
rm -rf "$ARTIFACTS_DIR/raw_frames" && mkdir -p "$ARTIFACTS_DIR/raw_frames"
ffmpeg -y -i "$INPUT" -frames:v 20 "$ARTIFACTS_DIR/raw_frames/%03d.png"

# 按 10fps 编码为最终交付视频（20 帧 / 10fps = 2 秒；yuv420p 保证浏览器/VLC 可播）
ffmpeg -y -framerate 10 -i "$ARTIFACTS_DIR/raw_frames/%03d.png" \
  -c:v libx264 -pix_fmt yuv420p -crf 20 \
  "$ARTIFACTS_DIR/stage1_closed_loop.mp4"

ls -lh "$ARTIFACTS_DIR/stage1_closed_loop.mp4"
```

### 9.4（升级，Stage1 最终目标）Alpamayo-R1 + OmniDreams

Stage1 的最终目标是 **NVIDIA 世界模型 OmniDreams + AlpaSim 闭环**，GPU0 跑 Alpamayo-R1、GPU1 跑 OmniDreams。VaVAM-B 仅为分层打通时的临时 driver。

关键事实（2026-10-01 调研确认）：
- **Alpamayo-R1 即 Alpamayo 1**（权重 README："Alpamayo-R1 has been renamed to Alpamayo 1"），现有 `driver=alpamayo1` 已通过 `Alpamayo1Model` 完整封装 R1，driver 循环/推理全在共享基类 `AlpamayoBaseModel`。
- R1 消息协议只吃图片序列（无相机索引、无导航条件），1 相机×4 帧与 4 相机×4 帧同构，**单相机在模型协议层零障碍**。
- 唯一缺失：单相机 preset。已在仓库新增 `src/wizard/configs/driver/alpamayo1_1cam.yaml`（仿 `alpamayo1_5_1cam`：`/cameras:1cam_1080` + `use_cameras=[front_wide]` + `subsample_factor=3` + `checkpoint_path=/mnt/weights/alpamayo-r1`）。
- 权重以 bind mount 挂入 driver 容器（wizard 默认挂载不含 /mnt/weights）。本项目 Hydra 1.3.2 的 override parser **不支持 `+=` 语法**，且 YAML 组合时 list 合并语义是**替换不是追加**——故新建 `configs/extras/r1_weights.yaml`（`@package _global_`）在 preset defaults 中引入，其中必须列全 driver 的 6 个挂载（默认 5 个 + R1 权重），只写新挂载会把默认挂载全部冲掉（driver 曾因此报 `/mnt/output` not found 秒退）。
- R1 processor/tokenizer 取自 HF cache 中的 Qwen3-VL-8B（cache 已完整挂载，离线可用）。

执行（先配置检查、后真实闭环）：

```bash
# 0) 起 GPU1 renderer
setsid bash ~/simulation/scripts/start_renderer.sh < /dev/null > ~/simulation/artifacts/renderer_gpu1_r1.log 2>&1 &

# 1) 配置兼容性检查（wizard.run_method=NONE 只生成配置/不启动容器）
#    注意：不要用 CLI 的 volumes+=...（本项目 Hydra 1.3.2 拒绝 +=）；
#    preset 在 defaults 中已组合 extras/r1_weights，挂载自动生效。
CUDA_VISIBLE_DEVICES=0 uv run --no-sync --project src/wizard alpasim_wizard \
  deploy=external_video_model topology=1gpu driver=alpamayo1_1cam +chunking=8frame \
  "scenes.scene_ids=['clipgt-02eadd92-02f1-46d8-86fe-a9e338fed0b6']" \
  'wizard.external_services.renderer=["127.0.0.1:50051"]' \
  wizard.run_method=NONE \
  wizard.log_dir="$ARTIFACTS_DIR/run_r1_omnidreams_configcheck"

# 2) 真实闭环（脚本 scripts/run_closed_loop_r1.sh）
sg docker -c "bash ~/simulation/scripts/run_closed_loop_r1.sh"
```

> **复查警告（2026-10-01）**：该次运行虽然 aggregate 表字面显示 collision_any=0/pass，但逐帧原始 metrics 与原始视频证实 **R1 在视频 13.8s 前向撞上人行横道行人**（连续 3 帧），且碰撞前它连续 5 个 chunk 输出"Yield to the pedestrian"却未实际减速——aggregate 的 pass 仅因 `dist_to_gt_trajectory>=4m` modifier 在碰撞前 2.4s 已截断计分。"链路跑通"不等于"安全通过"，复盘务必以逐帧数据和原始视频为准。详见 `docs/Stage1_Complete_1.md` §7.1.1。

#### 9.4.1 额外组合（2026-10-01 已完成）：Alpamayo 1.5 + OmniDreams

同一 `deploy=external_video_model topology=1gpu` + 外部 renderer 模式，driver 换为本地 preset `alpamayo15_1cam_local`（官方 `alpamayo1_5_1cam` 走在线权重；本地权重需自建 preset：alpamayo1_5 + 1cam_1080 + extras + subsample_factor=3 + checkpoint_path=/mnt/weights/alpamayo-1.5），入口 `scripts/run_closed_loop_a15.sh`（必须 `sg docker -c` 启动）。

1.5 特有的额外前提与坑：
- VLM/backbone 为 gated 仓库 **`nvidia/Cosmos-Reason2-8B`，许可独立于 PhysicalAI，需单独在 HF 页面 Agree**；驱动实际只加载其 processor/tokenizer，用 `snapshot-download` 配合 allow-patterns 只拉 tokenizer 文件（约 10 个、无 safetensors）即可，不必下载 8B 权重。
- **driver 容器只挂载 cache、不带 HF token**，transformers 默认联网校验授权 → gated 401（"You are trying to access a gated repo"）。修复：extras 中给 driver 注入 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1` 强制缓存解析；跑真实闭环前先用 `wizard.run_method=NONE` 检查 compose 已渲染 environment 块。
- 结果（本场景，逐帧复查后）：链路端到端跑通（rc=0，75 帧，66 条 CoT），但评估未通过。时间线：1.5 按其 CoT "Nudge left due to stopped vehicle blocking right side" 平滑向左绕行（"不走直线"是有意动作、转向无抖动），**7.33s（x≈61m）绕行裕量不足前向撞车**，碰撞状态持续 4.3s，**8.4–14.6s offroad=1（骑右侧路缘，6.2s）**，重刹到 2m/s 后蠕行。aggregate 表却显示 offroad=0、dist_traveled=58m：`RemoveTimestepsAfterEvent(collision)` 删除了首碰之后 47 行，事故细节全在截除窗口——与 R1 同类的"aggregate 掩盖"。这次 CoT 与动作一致（说绕行就绕行、说跟停就刹车），失败是裕量判断不足。详见 `docs/Stage1_Complete_1.md` §7.2。

其他组合（不属 Stage1 目标，仅备查）：R1 + NRE 多相机协议需 `deploy=local` 与 NRE 镜像 `nvcr.io/nvidia/nre/nre-ga:26.04`，2026-10-01 曾短暂试拉（manifest 14.3GB）后按用户决策停止。

### 9.5 停止 Renderer、收尾

```bash
bash ~/simulation/scripts/stop_renderer.sh
nvidia-smi --query-gpu=index,memory.used --format=csv   # 预期回落至约 14MiB
```

### 9.6 验证脚本 `scripts/check_task5.py`（完整内容）

```python
#!/usr/bin/env python3
"""Task 5 gate：rollout 视频存在；20 帧抽取与 10fps 编码完成；最终 mp4 非黑屏/非纯色。"""
import subprocess, pathlib, sys

ROOT = pathlib.Path.home() / "simulation"
ART = ROOT / "artifacts"

def bash(cmd, timeout=1800):
    return subprocess.run(["bash", "-c", f"source {ROOT}/scripts/env.sh; {cmd}"],
                          capture_output=True, text=True, timeout=timeout)

def main():
    pth = ART / "rollout_video_path.txt"
    assert pth.is_file(), "缺少 rollout_video_path.txt（未执行 9.3）"
    src = pth.read_text().strip()
    assert pathlib.Path(src).is_file(), f"rollout 视频丢失: {src}"
    assert pathlib.Path(src).stat().st_size > 100_000, "rollout 视频过小，可能损坏"

    frames = sorted((ART / "raw_frames").glob("*.png"))
    assert len(frames) == 20, f"期望 20 帧 PNG，实际 {len(frames)}"

    out = ART / "stage1_closed_loop.mp4"
    assert out.is_file(), "缺少 stage1_closed_loop.mp4"
    assert out.stat().st_size > 50_000, "最终视频过小"

    # cv2 有效性断言
    code = (
        "import cv2, numpy as np\n"
        f"cap=cv2.VideoCapture(r'{out}')\n"
        "n=int(cap.get(cv2.CAP_PROP_FRAME_COUNT))\n"
        "assert n>=19, f'encoded frame count {n}'\n"
        "bad=0\n"
        "for i in (0,9,19):\n"
        " cap.set(cv2.CAP_PROP_POS_FRAMES,i); ret,f=cap.read()\n"
        " assert ret, f'cannot read frame {i}'\n"
        " m,s=float(np.mean(f)),float(np.std(f))\n"
        " print(f'frame {i}: mean={m:.1f} std={s:.1f}')\n"
        " if not (m>15 and s>10): bad+=1\n"
        "cap.release()\n"
        "assert bad==0, f'{bad}/3 sampled frames are black/solid-color'\n"
        "print('frames valid')\n"
    )
    r = bash(f"cd {ROOT}/repos/alpasim && uv run python -c \"{code}\"")
    # 若 alpasim 环境无 cv2，退回 conda cc
    if r.returncode != 0:
        r = subprocess.run([str(pathlib.Path.home()/".conda/envs/cc/bin/python3"), "-c", code],
                           capture_output=True, text=True)
    assert r.returncode == 0, f"视频有效性断言失败: {r.stdout}{r.stderr}"
    assert "frames valid" in r.stdout, r.stdout + r.stderr

    print("[SUCCESS] Task 5 passed: 20-frame / 10fps closed-loop mp4 generated and validated (not black/solid).")

if __name__ == "__main__":
    main()
```

---

## 10. 风险与回退预案

### 10.1 磁盘
- **盘未到位不阻塞 Task 0–2**：代码、uv/缓存目录在根分区的临时策略，总占用约 5–15GB；硬红线：根分区剩余 **<50G 立即停手**挂盘；<30G 禁止任何 pip/uv/docker 拉取。
- Task 3 起必须 `sim_storage_ready`；check_task3/4/5 内置该断言。
- 重复下载（场景 usdz 在 assets 与 wizard 缓存各一份，约 3.6GB）可接受；如需省空间，闭环验证后删除 `$ASSETS_DIR/nurec` 副本。
- 删除大文件后空间未释放 → 有进程持有句柄：`sudosw lsof +L1` 排查。

### 10.2 网络 / GFW
按故障层级依次切换，每层切换后用最小请求验证：
1. HuggingFace：`export HF_ENDPOINT=https://hf-mirror.com`（写入 env.sh 的同名变量），hf_transfer 与镜像不兼容时临时 `HF_HUB_ENABLE_HF_TRANSFER=0`。
2. GitHub 克隆/raw：使用前缀代理（执行时确认可用性）：`https://ghfast.top/https://github.com/...`、`https://gh-proxy.com/https://github.com/...`；或在 `~/.gitconfig` 配置 `url."https://ghfast.top/https://github.com/".insteadOf "https://github.com/"`。
3. apt：换国内镜像（如清华 `mirrors.tuna.tsinghua.edu.cn` / 阿里 `mirrors.aliyun.com`），仅改 jammy 与 security 源。
4. Docker 拉取：`/etc/docker/daemon.json` 增加 `registry-mirrors`（公开镜像加速器近年变动大，配置后必须 `dk info | grep -A5 'Registry Mirrors'` 实测；不可达就删除，勿叠加多个失效镜像）。
5. 给 dockerd 配代理（仅本机有代理时）：`/etc/systemd/system/docker.service.d/http-proxy.conf` 写 `HTTP_PROXY/HTTPS_PROXY` 后 `daemon-reload + restart docker`。

### 10.3 GPU / OOM
- 80GB 卡上单视图 OmniDreams（2B+文本编码器）与 R1 单样本均不应 OOM；若 OOM 先查是否被其他进程占用（`nvidia-smi`）、是否漏配 `CUDA_VISIBLE_DEVICES` 导致双卡任务挤进一卡。
- 回退手段：降低分辨率（704p→480p，gRPC `--resolution 480p`）、关闭 CFG、减少 chunk 帧数、`PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`。
- 禁止用 `--gpus all` 跑本应单卡的服务。

### 10.4 权限 / 私有 / gated
- 三个 HF gated 仓库必须用户手动接受条款（agent 无法代办）；接受后仍 401 用 `hf auth whoami` 确认登录账号。
- 若 OmniDreams 权重将来转为受限：回退方案 = 用 wizard 默认 VaVAM + NRE 路径，或用公开的 `nvidia/omni-dreams-scenes` 静态场景先打通文件流（不做神经生成的那一环），并把缺口记入交付日志。
- 商业使用需另行申请 NVIDIA 授权（OpenMDW / NVIDIA Open Model License 默认非商业）。

### 10.5 驱动 / CUDA
- R580 原生 CUDA 13.0，向后兼容 12.x。若 AlpaSim/FlashDreams 某组件坚持需要 12.8 用户态库，优先在容器/venv 内满足，**不要在宿主机降级驱动**。
- 升级驱动前必须确认新版仍支持 A100（Ampere）；580 已满足本项目，无需升级。

### 10.6 headless EGL / 渲染
- 症状 `EGL_NOT_INITIALIZED` / `No EGL display`：检查 `libegl1`、`/usr/share/glvnd/egl_vendor.d/10_nvidia.json`、`sudosw ldconfig -p | grep -i egl`。
- Ludus 光栅器要求 EGL 上下文固定单线程（官方代码注释：Blackwell+新驱动下跨线程 `eglMakeCurrent` 失败）；不要自行多线程化该组件。
- 容器化渲染时加 `--gpus all -e NVIDIA_DRIVER_CAPABILITIES=graphics,compute,utility`。
- 不使用 OSMesa / PyOpenGL（官方栈不存在该依赖）。

### 10.7 NGC / NRE 路径专属
- 登录：`dk login nvcr.io --username '$oauthtoken'`，密码粘贴 `~/access/user_access_methods.txt` 中的 NGC API Key。
- 拉取前先 `dk manifest inspect nvcr.io/nvidia/nre/nre-ga:26.04` 记录镜像大小（需登录后才可 inspect）。
- 若组织内 NGC key 无 nre 仓库权限 → 申请 NGC 权限，或走本计划主路径（external_video_model，不依赖 nvcr.io）。

---

## 11. 时间 / 磁盘预算估算表

> 联网日期 2026-09-27；标注「实测」的来自官方 API，「估算」为经验值，执行时以实际输出为准并记录。

| 项目 | 大小 | 耗时（参考，视网络） | 落盘位置 | 空间检查点 |
|---|---|---|---|---|
| Docker CUDA 穿透镜像 12.8.0-base | ~95MB（实测存在） | 1–3 min | 初期 /var/lib/docker，迁移后 $DOCKER_DATA_ROOT | 拉取前根分区 >50G |
| apt 依赖（ffmpeg/编译工具等） | ~1GB | 3–8 min | 根分区 | — |
| Rust minimal 工具链 | ~1.2GB | 3–10 min | $CACHES_DIR/rustup+cargo | — |
| uv 及托管 Python 3.12 | ~200MB | 2–5 min | ~/.local/bin + uv 数据 | — |
| AlpaSim uv 环境（--extra all，含 torch2.8/cu128、PyG 等） | 12–18GB（估算） | 10–30 min | repos/alpasim/.venv（根分区） | 安装后根分区 >50G |
| FlashDreams omnidreams 环境（cuda12 组） | 8–14GB（估算） | 10–30 min | repos/flashdreams/.venv（根分区） | 同上 |
| Alpamayo uv 环境 | 6–10GB（估算） | 5–20 min | repos/alpamayo/.venv（根分区） | 同上 |
| OmniDreams 权重 | 3.84GiB（实测 4,118,900,683B） | 2–10 min | $WEIGHTS_DIR/omnidreams | 需 storage ready |
| Alpamayo-R1 权重 | 22.16GB（实测） | 10–40 min | $WEIGHTS_DIR/alpamayo-r1 | 盘可用 >60G |
| VaVAM-B 资产 | ~2.0GB（实测主权重 1.75GB） | 3–10 min | $STORAGE_ROOT/alpasim-data/drivers（软链） | — |
| NuRec 单场景 usdz | 1.80GB（实测） | 3–15 min | $ASSETS_DIR/nurec | — |
| wizard 场景缓存（重复一份） | 1.80GB | 随闭环运行 | $STORAGE_ROOT/alpasim-data/nre-artifacts | — |
| （可选 5-B）1.5 22GB + Reason2 ~16GB | ~38GB | 20–60 min | weights/ | 盘可用 >80G |
| （可选）NRE-GA 镜像 | 15–30GB（估算，manifest 核实） | 10–40 min | $DOCKER_DATA_ROOT | 盘可用 >60G |

主路径（不含可选项）累计：根分区约 30GB 代码/venv；数据盘约 33GB。根分区 venv 是大头——如担心 130G 根分区，可在 Task 2 前把各仓库 `.venv` 也用软链建到 `$STORAGE_ROOT/venvs/<repo>`（命令模式与 6.3 相同：先 `mkdir` 目标，`ln -s` 后再 `uv sync`）。

---

## 12. 交付物清单（Acceptance Deliverables）

| # | 交付物 | 路径 |
|---|---|---|
| 1 | 最终闭环视频（20 帧 / 10fps / 2 秒，浏览器或 VLC 可播） | `~/simulation/artifacts/stage1_closed_loop.mp4` |
| 2 | 原始帧序列（20 张 PNG） | `~/simulation/artifacts/raw_frames/0001–0020.png` |
| 3 | wizard 原始 rollout 视频（完整时长） | 路径记录于 `~/simulation/artifacts/rollout_video_path.txt`，位于 `~/simulation/artifacts/run_vavam_omnidreams/eval/videos/` |
| 4 | Renderer（GPU1）日志 | `~/simulation/artifacts/renderer_gpu1.log` |
| 5 | Driver（GPU0）日志 | `~/simulation/artifacts/driver_gpu0.log` |
| 6 | 运行配置（wizard 生成，真实文件） | `~/simulation/artifacts/run_vavam_omnidreams/generated-user-config-0.yaml`、`generated-network-config.yaml`、`wizard-config.yaml` |
| 7 | 全局环境脚本与启停脚本 | `~/simulation/scripts/env.sh`、`start_renderer.sh`、`stop_renderer.sh` |
| 8 | Gate 验证脚本及通过记录 | `~/simulation/scripts/check_task0.py … check_task5.py` 全部输出 `[SUCCESS]` |
| 9 | git 管理记录 | `~/simulation` 为 git 仓库（main 分支），scripts/configs/docs 已提交 |
| 10 | 权重与资产（数据盘） | `$STORAGE_ROOT/weights/`、`$STORAGE_ROOT/assets/`（通过 `~/simulation/weights|assets` 软链访问） |

---

## 13. 仍需用户决策 / 配合的未决事项

1. **挂载点**：500GB 盘挂到 `/mnt/data`（推荐）还是 `/mnt`？决定 env.sh 中 `STORAGE_ROOT`。
2. **新盘身份确认**：执行 mkfs 前用户确认该卷确为全新空盘（防止误格式化）。
3. **gated 授权**：用户需亲自在 HuggingFace 页面接受 3 个仓库条款（7.1），属人工动作。
4. **最终 driver 选择**：验收主路径为 VaVAM-B + OmniDreams（最小、官方文档化）；是否在 Stage 1 内继续完成 Alpamayo-1.5+OmniDreams（额外约 38GB 下载）或 R1+NRE 路径。
5. **R1 + OmniDreams 精确组合**：官方公开 wizard preset 未暴露该组合；如确需，需额外做 preset 兼容性验证，可能成功也可能需要自定义 hydra 覆盖。
6. **商业用途**：如非纯研究使用，需提前申请 NVIDIA 商业授权。

---

## 14. Sources（2026-09-27 访问核实）

AlpaSim：
- https://github.com/NVlabs/alpasim
- https://api.github.com/repos/NVlabs/alpasim
- https://raw.githubusercontent.com/NVlabs/alpasim/main/README.md
- https://raw.githubusercontent.com/NVlabs/alpasim/main/Dockerfile
- https://raw.githubusercontent.com/NVlabs/alpasim/main/pyproject.toml
- https://raw.githubusercontent.com/NVlabs/alpasim/main/setup_local_env.sh
- https://raw.githubusercontent.com/NVlabs/alpasim/main/docs/TUTORIAL.md
- https://raw.githubusercontent.com/NVlabs/alpasim/main/docs/VIDEO_MODEL.md
- https://raw.githubusercontent.com/NVlabs/alpasim/main/docs/ONBOARDING.md
- https://raw.githubusercontent.com/NVlabs/alpasim/main/docs/DESIGN.md
- https://raw.githubusercontent.com/NVlabs/alpasim/main/src/wizard/configs/base_config.yaml
- https://raw.githubusercontent.com/NVlabs/alpasim/main/src/utils_rs/Cargo.toml
- https://raw.githubusercontent.com/NVlabs/alpasim/main/data/scenes/README.md
- https://raw.githubusercontent.com/NVlabs/alpasim/main/data/auto-init.sh
- https://raw.githubusercontent.com/NVlabs/alpasim/main/data/download_vavam_assets.sh

Alpamayo：
- https://huggingface.co/nvidia/Alpamayo-R1-10B
- https://huggingface.co/api/models?search=alpamayo
- https://huggingface.co/nvidia/Alpamayo-1.5-10B
- https://huggingface.co/nvidia/Alpamayo2-Super
- https://github.com/NVlabs/alpamayo
- https://github.com/NVlabs/alpamayo-recipes
- https://arxiv.org/abs/2511.00088
- https://nvidianews.nvidia.com/news/alpamayo-autonomous-vehicle-development
- https://www.nvidia.com/en-us/solutions/autonomous-vehicles/alpamayo/

OmniDreams / FlashDreams：
- https://github.com/nv-tlabs/omni-dreams
- https://github.com/NVIDIA/flashdreams
- https://huggingface.co/nvidia/omni-dreams-models
- https://huggingface.co/datasets/nvidia/omni-dreams-samples
- https://huggingface.co/datasets/nvidia/omni-dreams-scenes
- https://arxiv.org/abs/2606.03159
- https://research.nvidia.com/labs/sil/projects/omnidreams-blog/

NuRec：
- https://huggingface.co/datasets/nvidia/PhysicalAI-Autonomous-Vehicles-NuRec
- https://huggingface.co/datasets/nvidia/PhysicalAI-Autonomous-Vehicles
- https://docs.nvidia.com/nurec/

容器与工具链：
- https://docs.docker.com/engine/install/ubuntu/
- https://docs.docker.com/reference/cli/dockerd/
- https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html
- https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/sample-workload.html
- https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/1.13.5/user-guide.html
- https://docs.nvidia.com/ngc/gpu-cloud/ngc-user-guide/index.html
- https://docs.nvidia.com/cuda/cuda-toolkit-release-notes/
- https://docs.nvidia.com/deploy/cuda-compatibility/
- https://hub.docker.com/r/nvidia/cuda/tags
- https://www.rust-lang.org/tools/install
- https://docs.astral.sh/uv/
- https://packages.ubuntu.com/search?keywords=git-lfs
- https://huggingface.co/docs/huggingface_hub
- https://hf-mirror.com

---

> 执行顺序总览：Task 0（初始化）→ Task 1（系统+Docker+工具链；盘到位时完成 5.6–5.8）→ Task 2（仓库+环境）→ Task 3（gated 授权+权重资产，硬依赖数据盘）→ Task 4（双组件冒烟）→ Task 5（闭环+MP4）。任何 Gate 未输出 `[SUCCESS]`，一律停在当前 Task 排查，不得进入下一步。
