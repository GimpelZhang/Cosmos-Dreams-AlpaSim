#!/usr/bin/env bash
# Stage 2 Task 3.2 fallback: 11 separate serial wizards, one variant each.
#
# Per the Task 2.1 rollout_index verdict, one wizard cannot internally select
# variants, so each variant gets its own wizard. The OmniDreams renderer host
# process stays up across variants; each runtime opens a fresh session.
#
# Env knobs:
#   MANIFEST   (default configs/stage2/manifests/stage2_variants.csv)
#   RUN_TAG    (default stage2_20261002)
#   PORT       (default 50051)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/env.sh"

MANIFEST="${MANIFEST:-$SCRIPT_DIR/../configs/stage2/manifests/stage2_variants.csv}"
PORT="${RENDERER_PORT:-50051}"
RUN_TAG="${RUN_TAG:-stage2_20261002}"
HOST_LOGDIR="/mnt/artifacts/stage2/run_$RUN_TAG"
RENDERER_LOG="$HOST_LOGDIR/renderer.log"

# Re-run under the docker group when this shell cannot talk to the daemon.
if ! docker info >/dev/null 2>&1; then
  if [ -n "${_BATCH_REEXEC:-}" ]; then
    echo "docker daemon unreachable even after switching to the docker group" >&2
    exit 1
  fi
  exec sg docker -c "RUN_TAG='$RUN_TAG' MANIFEST='$MANIFEST' PORT='$PORT' _BATCH_REEXEC=1 bash '$0'"
fi

[ -f "$MANIFEST" ]
mkdir -p "$HOST_LOGDIR"

port_open() { bash -c "exec 3<>/dev/tcp/127.0.0.1/$PORT" 2>/dev/null; }
if ! port_open; then
  echo "Starting OmniDreams renderer (direct, GPU1), log: $RENDERER_LOG"
  setsid bash "$SCRIPT_DIR/start_renderer_direct.sh" < /dev/null > "$RENDERER_LOG" 2>&1 &
  ready=0
  for _ in $(seq 1 180); do
    if grep -q "Server started successfully" "$RENDERER_LOG" 2>/dev/null; then ready=1; break; fi
    sleep 2
  done
  [ "$ready" = 1 ] || { echo "Renderer failed to become ready"; tail -20 "$RENDERER_LOG"; exit 1; }
fi

cd "$REPOS_DIR/alpasim"

# CSV: index,variant_id,scene_id,prompt_id,n_actors,activate_s,seed
first=1
while IFS=, read -r idx vid scene prompt_id n_actors activate seed || [ -n "${idx:-}" ]; do
  vid="${vid%$'\r'}"; scene="${scene%$'\r'}"; seed="${seed%$'\r'}"
  [ -z "${vid:-}" ] && continue
  if [ "$first" = 1 ]; then first=0; continue; fi  # header

  VARLOG="$HOST_LOGDIR/$vid"
  if [ -f "$VARLOG/_success" ]; then
    echo "[$idx] $vid already complete, skipping"
    continue
  fi
  mkdir -p "$VARLOG"
  # Publish the active variant for the long-lived renderer, which reads this
  # file when its own STAGE2_VARIANT_ID is unset.
  printf '%s\n' "$vid" > "${STAGE2_ANCHOR_PATH:-/mnt/artifacts/stage2/anchor}/current_variant.txt"
  echo "=== [$idx] $vid -> $VARLOG ==="

  set +e
  env STAGE2_VARIANT_ID="$vid" STAGE2_RENDER_SEED="$seed" CUDA_VISIBLE_DEVICES=0 \
    uv run --no-sync --project src/wizard alpasim_wizard \
      deploy=external_video_model topology=1gpu driver=alpamayo15_1cam_local \
      +chunking=8frame +extras=stage2_env \
      "scenes.scene_ids=['$scene']" \
      'wizard.external_services.renderer=["127.0.0.1:'"$PORT"'"]' \
      +runtime.endpoints.startup_timeout_s=900 wizard.timeout=7200 \
      eval.allow_aggregation_with_failed_rollouts=true \
      "wizard.run_name=$vid" "wizard.log_dir=$VARLOG" \
      2>&1 | tee "$VARLOG/wizard-console.log"
  rc=${PIPESTATUS[0]}
  set -e

  docker compose -f "$VARLOG/docker-compose.yaml" -p "$vid" down --remove-orphans \
    >>"$VARLOG/compose-down.log" 2>&1 || true

  if [ "$rc" -eq 0 ] && grep -q "Alpasim finished" "$VARLOG/wizard-console.log"; then
    touch "$VARLOG/_success"
  else
    echo "[$idx] $vid wizard failed (rc=$rc); see $VARLOG/wizard-console.log" >&2
    exit 1
  fi
done < "$MANIFEST"

echo "All 11 variants complete: $HOST_LOGDIR"
touch "$HOST_LOGDIR/_all_success"
