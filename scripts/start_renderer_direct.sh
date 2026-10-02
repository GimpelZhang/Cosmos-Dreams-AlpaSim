#!/usr/bin/env bash
# GPU1 OmniDreams gRPC 渲染服务 —— 直启版（绕开 torchrun）。
# 背景：本机 torchrun --standalone 因 pc_0 反向 DNS 解析挂死（2026-10-02 Spike A 实测），
# 单卡渲染只需 LOCAL_RANK/RANK/WORLD_SIZE + MASTER_ADDR/MASTER_PORT。
# 不修改 scripts/start_renderer.sh（torchrun 路径保留备用）。
set -euo pipefail
source "$HOME/simulation/scripts/env.sh"
cd "$REPOS_DIR/flashdreams"

PORT="${RENDERER_PORT:-50051}"
SLUG="${OMNIDREAMS_SLUG:-omnidreams}"
RESOLUTION="${OMNIDREAMS_RESOLUTION:-704p}"

exec env CUDA_VISIBLE_DEVICES=1 \
  LOCAL_RANK=0 RANK=0 WORLD_SIZE=1 \
  MASTER_ADDR=127.0.0.1 MASTER_PORT="${MASTER_PORT:-29500}" \
  uv run --package flashdreams-omnidreams python \
    -m omnidreams.impl.grpc.server \
    --pipeline_config_name "$SLUG" \
    --host 0.0.0.0 --port "$PORT" \
    --resolution "$RESOLUTION" \
    --output_format jpeg --jpeg_quality 90
