#!/usr/bin/env python3
"""Build the variant -> rollout map CSV for the v05-v10 local-patch batch.

Scans ``<run_dir>/<vid>/rollouts/**/rollout.asl`` (expect exactly one per
variant) and prepends the zero-intervention v00 baseline rollout (same
scene/seed, from the first Stage 2 batch) so ``analyze_stage2.py`` can
compute its baseline deltas.

Defaults are specific to the 2026-10-03 run; override the baseline rollout
location with ``STAGE2_V00_ASL`` when reusing this for another batch.
"""
import csv
import os
import sys
from pathlib import Path

DEFAULT_V00_ASL = (
    "/mnt/artifacts/stage2/run_stage2_20261002/v00_baseline/rollouts/"
    "clipgt-02eadd92-02f1-46d8-86fe-a9e338fed0b6/"
    "d69ff024-be3b-11f1-a78f-4ff2dc0f1c45/rollout.asl")
V00_ASL = os.environ.get("STAGE2_V00_ASL", DEFAULT_V00_ASL)
SCENE = "clipgt-02eadd92-02f1-46d8-86fe-a9e338fed0b6"
VARIANTS = [
    "v05_stroller_jaywalking",
    "v06_wheelchair_shoulder",
    "v07_construction_excavator",
    "v08_loose_cow",
    "v09_construction_debris",
    "v10_typhoon_compound",
]


def main(run_dir: str, out_csv: str) -> None:
    run_dir = Path(run_dir)
    rows = []
    v00 = Path(V00_ASL)
    assert v00.exists(), v00
    rows.append({
        "variant_id": "v00_baseline",
        "scene_id": SCENE,
        "rollout_uuid": v00.parent.name,
        "asl_path": str(v00),
        "n_frames": "",
        "n_session_objects": "",
    })
    for vid in VARIANTS:
        hits = list((run_dir / vid).glob("rollouts/**/rollout.asl"))
        if len(hits) != 1:
            sys.exit(f"{vid}: found {len(hits)} rollout.asl under {run_dir / vid}")
        rows.append({
            "variant_id": vid,
            "scene_id": SCENE,
            "rollout_uuid": hits[0].parent.name,
            "asl_path": str(hits[0]),
            "n_frames": "",
            "n_session_objects": "",
        })
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {out_csv} ({len(rows)} rows)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
