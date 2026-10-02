#!/usr/bin/env bash
# Stage 2 Task 0.5 helper: run one spike wizard with optional prompt/actor flags.
# Usage: stage2_spike_wizard.sh <log_dir> [prompt_positive]
# Env: SPIKE_EXTRA may carry extra hydra overrides (space-separated string).
set -euo pipefail
source "$HOME/simulation/scripts/env.sh"
LOG_DIR="$1"; shift || true
PROMPT="${1:-}"
BASE_CLIP="$(cat /mnt/artifacts/stage2/base_scene.txt)"
mkdir -p "$LOG_DIR"
cd "$HOME/simulation/repos/alpasim"

ARGS=(
  deploy=external_video_model topology=1gpu driver=alpamayo15_1cam_local
  +chunking=8frame
  "scenes.scene_ids=['$BASE_CLIP']"
  'wizard.external_services.renderer=["127.0.0.1:50051"]'
  +runtime.endpoints.startup_timeout_s=900
  wizard.log_dir="$LOG_DIR"
)
if [[ -n "$PROMPT" ]]; then
  ARGS+=("runtime.renderer.video_model_config.text_prompt_positive=\"$PROMPT\"")
fi
if [[ -n "${SPIKE_EXTRA:-}" ]]; then
  # shellcheck disable=SC2206
  ARGS+=( $SPIKE_EXTRA )
fi

exec env CUDA_VISIBLE_DEVICES=0 uv run --no-sync --project src/wizard alpasim_wizard "${ARGS[@]}"
