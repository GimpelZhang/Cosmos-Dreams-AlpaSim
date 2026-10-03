#!/bin/bash
# Sample GPU/RAM/container resource usage every 5 s while a closed-loop run
# is in progress. Reconstructed from docs/Stage1_Runtime_Performance.md
# appendix (the original lived in /tmp).
#
# Output CSVs (OUT defaults to /tmp/perf_samples):
#   gpu.csv          wall,gpu_index,mem_used_mib,util_gpu_pct
#   ram.csv          wall,mem_total_kib,mem_available_kib
#   docker_stats.csv wall,container,cpu_pct,mem_usage,mem_pct
set -u

OUT="${OUT:-/tmp/perf_samples}"
INTERVAL="${INTERVAL:-5}"
mkdir -p "$OUT"

: > "$OUT/gpu.csv"
: > "$OUT/ram.csv"
: > "$OUT/docker_stats.csv"

while true; do
  ts=$(date -Is)
  nvidia-smi --query-gpu=index,memory.used,utilization.gpu \
    --format=csv,noheader,nounits | tr -d ' ' | \
    while IFS=, read -r idx mem util; do
      echo "$ts,$idx,$mem,$util" >> "$OUT/gpu.csv"
    done
  awk -v ts="$ts" \
    '/MemTotal:/ {t=$2} /MemAvailable:/ {a=$2} END {print ts","t","a}' \
    /proc/meminfo >> "$OUT/ram.csv"
  if command -v docker >/dev/null; then
    docker stats --no-stream --format \
      "$ts,{{.Name}},{{.CPUPerc}},{{.MemUsage}},{{.MemPerc}}" 2>/dev/null \
      >> "$OUT/docker_stats.csv" || true
  fi
  sleep "$INTERVAL"
done
