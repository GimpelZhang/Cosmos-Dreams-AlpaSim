#!/usr/bin/env bash
# GPU1 OmniDreams gRPC 渲染服务（AlpaSim external_video_model 对接的真实端点）
set -euo pipefail
source "$HOME/simulation/scripts/env.sh"
cd "$REPOS_DIR/flashdreams"

PORT="${RENDERER_PORT:-50051}"
SLUG="${OMNIDREAMS_SLUG:-omnidreams}"
RESOLUTION="${OMNIDREAMS_RESOLUTION:-704p}"

mkdir -p "$ARTIFACTS_DIR"

# Forward Stage 2 multi-frame anchoring configuration when the caller sets
# it; absent means the renderer behaves as the stock OmniDreams service.
stage2_env=()
for varname in \
  STAGE2_MULTIFRAME_ANCHOR \
  STAGE2_VARIANT_SPEC \
  STAGE2_VARIANT_ID \
  STAGE2_ANCHOR_PATH \
  STAGE2_ANCHOR_DEBUG
do
  if [[ -n "${!varname:-}" ]]; then
    stage2_env+=("${varname}=${!varname}")
  fi
done

exec env CUDA_VISIBLE_DEVICES=1 "${stage2_env[@]}" \
  uv run --package flashdreams-omnidreams torchrun \
    --standalone --nnodes=1 --nproc_per_node=1 \
    -m omnidreams.impl.grpc.server \
    --pipeline_config_name "$SLUG" \
    --host 0.0.0.0 --port "$PORT" \
    --resolution "$RESOLUTION" \
    --output_format jpeg --jpeg_quality 90
