#!/usr/bin/env bash
# Stage 2 Task 4.1: export 30 fps raw frames for all variants.
#
# Iterates the Task 3.3 variant map and runs the asl_to_frames module per
# variant (the asl-to-frames console script entry point is broken upstream;
# module invocation provides the same conversion).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/env.sh"

MAP="${MAP:-/mnt/artifacts/stage2/rollout_variant_map.csv}"
OUT_BASE="${OUT_BASE:-/mnt/artifacts/stage2/frames}"
PY="$REPOS_DIR/alpasim/.venv/bin/python"

[ -f "$MAP" ]
mkdir -p "$OUT_BASE"

while IFS=, read -r vid scene uuid asl_path rest; do
  [ "$vid" = "variant_id" ] && continue
  [ -z "$vid" ] && continue
  out="$OUT_BASE/$vid"
  if find "$out" -path '*/rollout/camera_*' -type d 2>/dev/null | grep -q .; then
    echo "$vid: frames already exported, skipping"
    continue
  fi
  echo "=== $vid -> $out ==="
  "$PY" -m alpasim_utils.asl_to_frames "$asl_path" \
    --format frames --log-save-dir "$out"
done < "$MAP"
