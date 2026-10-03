#!/bin/bash
# Post-process the v05-v10 local-patch batch:
# map -> frames -> per-frame analysis -> BEV -> front/BEV mp4s.
#
# RUN_DIR defaults to the 2026-10-03 batch; override it to reuse this for
# another run directory.
set -euo pipefail
source "$HOME/simulation/scripts/env.sh"

RUN_DIR="${RUN_DIR:-/mnt/artifacts/stage2/run_localpatch_20261003}"
VARIANTS_YAML="$HOME/simulation/configs/stage2/variants.yaml"
MAP="$RUN_DIR/map_v05v10.csv"
PY="$REPOS_DIR/alpasim/.venv/bin/python"
SIM="$HOME/simulation"

[ -f "$RUN_DIR/_all_success" ]

python3 "$SIM/scripts/build_stage2_map.py" "$RUN_DIR" "$MAP"

echo "=== export frames ==="
MAP="$MAP" OUT_BASE="$RUN_DIR/frames" \
  bash "$SIM/scripts/export_stage2_frames.sh"

echo "=== per-frame analysis ==="
"$PY" "$SIM/scripts/analyze_stage2.py" "$RUN_DIR" \
  --variants "$VARIANTS_YAML" --map "$MAP" \
  --out-dir "$RUN_DIR/analysis"

echo "=== BEV rendering ==="
"$PY" "$SIM/scripts/render_stage2_bev.py" "$RUN_DIR" \
  --variants "$VARIANTS_YAML" --map "$MAP" \
  --out-dir "$RUN_DIR/bev"

echo "=== mp4 packaging ==="
mkdir -p "$RUN_DIR/mp4"
while IFS=, read -r vid scene uuid asl_path rest; do
  [ "$vid" = "variant_id" ] && continue
  [ "$vid" = "v00_baseline" ] && continue
  FRONT=$(find "$RUN_DIR/frames/$vid" -path '*/rollout/camera_front_wide_120fov' -type d | head -1)
  if [ -n "$FRONT" ] && [ ! -f "$RUN_DIR/mp4/${vid}_front.mp4" ]; then
    ffmpeg -y -framerate 30 -pattern_type glob -i "$FRONT/*.jpg" \
      -c:v libx264 -pix_fmt yuv420p -crf 20 \
      "$RUN_DIR/mp4/${vid}_front.mp4" </dev/null
  fi
  if [ -d "$RUN_DIR/bev/$vid" ] && [ ! -f "$RUN_DIR/mp4/${vid}_bev.mp4" ] && \
     [ "$(find "$RUN_DIR/bev/$vid" -name '*.png' | head -1)" ]; then
    ffmpeg -y -framerate 30 -start_number 1 -i "$RUN_DIR/bev/$vid/%06d.png" \
      -c:v libx264 -pix_fmt yuv420p -crf 20 \
      "$RUN_DIR/mp4/${vid}_bev.mp4" </dev/null
  fi
done < "$MAP"

echo "POSTPROCESS_DONE $(date +%T)"
