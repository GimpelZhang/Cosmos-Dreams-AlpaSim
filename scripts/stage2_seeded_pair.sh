#!/usr/bin/env bash
# Stage 2 §7.3 / risk R10 empirical check: two no-variant rollouts pinned to the
# same nonzero renderer seed (20261002). If their per-frame dist_to_gt series
# agree within 0.25 m, the pinned seed makes the loop reproducible.
set -euo pipefail
source "$HOME/simulation/scripts/env.sh"
cd "$HOME/simulation/repos/alpasim"
BASE=/mnt/artifacts/stage2/regression

for tag in ${TAGS:-02_seeded_a 03_seeded_b}; do
  echo "=== seeded run $tag ==="
  mkdir -p "$BASE/$tag"
  env STAGE2_RENDER_SEED=20261002 SPIKE_EXTRA="+extras=stage2_env" \
    bash "$HOME/simulation/scripts/stage2_spike_wizard.sh" "$BASE/$tag/run" \
    > "$BASE/$tag/wizard.log" 2>&1
  grep -q "Alpasim finished" "$BASE/$tag/wizard.log"
  docker compose -f "$BASE/$tag/run/docker-compose.yaml" -p stage2reg \
    down --remove-orphans > "$BASE/$tag/compose-down.log" 2>&1 || true
done
echo "=== both seeded runs finished ==="
