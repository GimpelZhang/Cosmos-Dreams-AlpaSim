# ~/simulation/scripts/env.sh —— 所有步骤统一 source 的单一事实源（被 git 跟踪，禁止写入任何密钥）
# shellcheck shell=bash

# ---- 数据盘（500GB XFS 已挂载于 /mnt，2026-09-30 核实）----
export USE_STORAGE_DISK="${USE_STORAGE_DISK:-true}"

export REPO_ROOT="$HOME/simulation"
if [ "$USE_STORAGE_DISK" = "true" ]; then
  export STORAGE_ROOT="${STORAGE_ROOT:-/mnt}"
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
# hf_transfer is uninstalled in project venvs and deprecated in new hub;
# leave it off unless the caller explicitly enables it.
export HF_HUB_ENABLE_HF_TRANSFER="${HF_HUB_ENABLE_HF_TRANSFER:-0}"
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
