#!/usr/bin/env bash
# Alpamayo-R1 (Alpamayo 1, GPU0) + OmniDreams (GPU1, external gRPC renderer).
# Must run with docker group access (sg docker -c ...) because the wizard
# invokes `docker compose` directly.
set -euo pipefail
source "$HOME/simulation/scripts/env.sh"
cd "$REPOS_DIR/alpasim"

LOGDIR="$ARTIFACTS_DIR/run_r1_omnidreams"

CUDA_VISIBLE_DEVICES=0 uv run --no-sync --project src/wizard alpasim_wizard \
  deploy=external_video_model \
  topology=1gpu \
  driver=alpamayo1_1cam \
  +chunking=8frame \
  "scenes.scene_ids=['clipgt-02eadd92-02f1-46d8-86fe-a9e338fed0b6']" \
  'wizard.external_services.renderer=["127.0.0.1:50051"]' \
  +runtime.endpoints.startup_timeout_s=900 \
  wizard.log_dir="$LOGDIR"
