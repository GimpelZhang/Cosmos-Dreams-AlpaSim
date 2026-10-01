#!/usr/bin/env bash
# Batch closed-loop: Alpamayo 1.5 (GPU0) + external OmniDreams renderer (GPU1)
# over all scenes listed in a manifest CSV.
#
# Difference from run_closed_loop_a15.sh:
#   * scene set is read from a manifest (scripts/a15_batch_scenes_20261001.csv);
#   * one wizard invocation serves every scene in a single compose project, so
#     the driver model is loaded once and the runtime runs one session per clip;
#   * log dir lives on /mnt (artifacts symlinked into the repo);
#   * the OmniDreams renderer is started automatically if its port is closed.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/env.sh"

MANIFEST="${MANIFEST:-$SCRIPT_DIR/a15_batch_scenes_20261001.csv}"
PORT="${RENDERER_PORT:-50051}"
RUN_TAG="${RUN_TAG:-a15_batch30_20261001}"
HOST_LOGDIR="/mnt/artifacts/run_${RUN_TAG}"
LINK_LOGDIR="$ARTIFACTS_DIR/run_${RUN_TAG}"
RENDERER_LOG="$ARTIFACTS_DIR/renderer_${RUN_TAG}.log"

# The wizard shells out to bare `docker compose`; re-run under the docker
# group when this shell cannot talk to the daemon. The _BATCH_REEXEC guard
# keeps an unreachable daemon from causing infinite sg->bash self-execution.
if ! docker info >/dev/null 2>&1; then
  if [ -n "${_BATCH_REEXEC:-}" ]; then
    echo "docker daemon unreachable even after switching to the docker group" >&2
    exit 1
  fi
  exec sg docker -c "RUN_TAG='$RUN_TAG' MANIFEST='$MANIFEST' RENDERER_PORT='$PORT' _BATCH_REEXEC=1 bash '$0'"
fi

[ -f "$MANIFEST" ]
mkdir -p "$HOST_LOGDIR"
# -L also matches a dangling symlink, which would make a plain ln -s fail EEXIST.
[ -e "$LINK_LOGDIR" ] || [ -L "$LINK_LOGDIR" ] || ln -s "$HOST_LOGDIR" "$LINK_LOGDIR"

# ---- Hydra list literal from manifest rows: ['scene_id1','scene_id2',...] ----
# The `|| [ -n "$uuid" ]` tail handles a final line without a trailing newline;
# blank lines and non-header rows with an empty/invalid scene_id are skipped.
SCENE_LIST="["
n=0
while IFS=, read -r uuid scene_id _rest || [ -n "${uuid:-}" ]; do
  uuid="${uuid%$'\r'}"
  scene_id="${scene_id%$'\r'}"
  [ -z "$uuid" ] && continue
  [ "$uuid" = "uuid" ] && continue
  if ! [[ "$scene_id" =~ ^[A-Za-z0-9_-]+$ ]]; then
    echo "Skipping invalid scene_id in manifest: '$scene_id'" >&2
    continue
  fi
  [ "$n" -gt 0 ] && SCENE_LIST+=","
  SCENE_LIST+="'$scene_id'"
  n=$((n + 1))
done < "$MANIFEST"
SCENE_LIST+="]"
[ "$n" -gt 0 ] || { echo "No valid scenes found in $MANIFEST" >&2; exit 1; }
echo "Batch: $n scenes from $MANIFEST"

# ---- Start the OmniDreams renderer if nothing is listening on the port ----
port_open() { bash -c "exec 3<>/dev/tcp/127.0.0.1/$PORT" 2>/dev/null; }
if ! port_open; then
  echo "Starting OmniDreams renderer (GPU1), log: $RENDERER_LOG"
  setsid bash "$SCRIPT_DIR/start_renderer.sh" < /dev/null > "$RENDERER_LOG" 2>&1 &
  echo "$!" > "$ARTIFACTS_DIR/renderer.pid"
  ready=0
  for _ in $(seq 1 180); do
    if grep -q "Server started successfully" "$RENDERER_LOG" 2>/dev/null; then ready=1; break; fi
    sleep 2
  done
  [ "$ready" = 1 ] || { echo "Renderer failed to become ready"; tail -20 "$RENDERER_LOG"; exit 1; }
fi

cd "$REPOS_DIR/alpasim"

# The external OmniDreams renderer keeps only one active session (each
# start_session closes the previous one), so clips must run strictly one at a
# time; topology=1gpu otherwise advertises 4 concurrent rollouts per endpoint.
CUDA_VISIBLE_DEVICES=0 uv run --no-sync --project src/wizard alpasim_wizard \
  deploy=external_video_model \
  topology=1gpu \
  driver=alpamayo15_1cam_local \
  +chunking=8frame \
  "scenes.scene_ids=$SCENE_LIST" \
  'wizard.external_services.renderer=["127.0.0.1:'"$PORT"'"]' \
  +runtime.endpoints.startup_timeout_s=900 \
  wizard.timeout=10800 \
  "wizard.run_name=$RUN_TAG" \
  eval.allow_aggregation_with_failed_rollouts=true \
  runtime.nr_workers=1 \
  runtime.endpoints.renderer.n_concurrent_rollouts=1 \
  runtime.endpoints.driver.n_concurrent_rollouts=1 \
  runtime.endpoints.physics.n_concurrent_rollouts=1 \
  runtime.endpoints.controller.n_concurrent_rollouts=1 \
  runtime.endpoints.trafficsim.n_concurrent_rollouts=1 \
  "wizard.log_dir=$HOST_LOGDIR"
