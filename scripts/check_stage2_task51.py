#!/usr/bin/env python3
"""Stage 2 Task 5.1 gate: analysis tables complete and NaN-free.

- per_variant.csv and reaction_table.csv each have 11 data rows
- no cell contains NaN (empty string allowed for inapplicable values)
- v00 event counts/timings match the pinned-seed regression digest
  (06_det_a: collision 12 frames first=27, offroad 43 frames first=32)
"""
from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

REPORT_DIR = Path("/mnt/artifacts/stage2/report")
DIGEST = Path("/mnt/artifacts/stage2/regression/06_det_a/digest.json")


def fail(msg):
    print(f"[FAIL] {msg}")
    sys.exit(1)


def read_rows(name):
    path = REPORT_DIR / name
    if not path.exists():
        fail(f"missing {path}")
    rows = list(csv.DictReader(open(path)))
    if len(rows) != 11:
        fail(f"{name}: expected 11 rows, got {len(rows)}")
    for r in rows:
        for k, v in r.items():
            if isinstance(v, float) and math.isnan(v):
                fail(f"{name} {r['variant_id']}.{k} is NaN")
            if v.strip().lower() == "nan":
                fail(f"{name} {r['variant_id']}.{k} literal NaN")
    return rows


def main():
    per = read_rows("per_variant.csv")
    reaction = read_rows("reaction_table.csv")

    base = next(r for r in per if r["variant_id"] == "v00_baseline")
    digest = json.load(open(DIGEST))
    events = digest["metrics"]["events"]
    if int(base["collision_frames"]) != events["collision_any"]["n_frames"]:
        fail("v00 collision frame count mismatch vs regression digest")
    if int(base["offroad_frames"]) != events["offroad"]["n_frames"]:
        fail("v00 offroad frame count mismatch vs regression digest")

    ts = digest["metrics"]["timestamps_us"]
    first_cam_ts = 4798870645485  # first camera frame (same absolute clock)
    for metric_name, event_key in (("collision_first_s", "collision_any"),
                                   ("offroad_first_s", "offroad")):
        # Digest first_frame is a 0-based index into timestamps_us.
        fi = events[event_key]["first_frame"]
        expect_s = round((ts[fi] - first_cam_ts) / 1e6, 1)
        got = float(base[metric_name])
        if abs(got - expect_s) > 0.1:
            fail(f"v00 {metric_name}: {got} vs digest {expect_s}")

    v00_reaction = next(r for r in reaction if r["variant_id"] == "v00_baseline")
    for k in ("first_decel_delta_s", "max_decel_delta_mps2",
              "collision_frames_delta", "offroad_frames_delta"):
        if v00_reaction[k] not in ("", "0", "0.0"):
            fail(f"v00 baseline {k} must be zero/empty, got {v00_reaction[k]}")

    print("[SUCCESS] Stage2 Task 5.1: 11-row tables written, v00 matches digest.")


if __name__ == "__main__":
    main()
