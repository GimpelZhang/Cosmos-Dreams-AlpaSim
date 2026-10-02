#!/usr/bin/env python3
"""Compare a new regression digest against the baseline digest.

usage: check_stage2_regression.py <new.json> <baseline.json> [--check-series]

Tolerances per plan §3.3: frame count exact; final values ±5%; collision/offroad
event first frame and count exact; per-step / per-chunk wall means ±15%;
with --check-series, per-frame dist_to_gt_trajectory ≤ 0.25 m.
"""
import argparse
import json
import sys
from pathlib import Path

FINAL_KEYS = ["progress", "progress_rel", "progress_rel_to_total", "dist_traveled_m"]
EVENT_KEYS = ["collision_any", "collision_front", "collision_rear",
              "collision_lateral", "offroad"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("new")
    ap.add_argument("baseline")
    ap.add_argument("--check-series", action="store_true")
    args = ap.parse_args()

    new = json.loads(Path(args.new).read_text())
    base = json.loads(Path(args.baseline).read_text())
    nm, bm = new["metrics"], base["metrics"]

    violations = []

    if nm["n_frames"] != bm["n_frames"]:
        violations.append(
            f"n_frames {nm['n_frames']} != baseline {bm['n_frames']}")

    for key in FINAL_KEYS:
        nv, bv = nm["final"][key], bm["final"][key]
        if abs(bv) > 1e-9:
            rel = abs(nv - bv) / abs(bv)
            if rel > 0.05:
                violations.append(f"final.{key}: {nv:.4f} vs {bv:.4f} ({rel:.1%})")
        elif abs(nv) > 1e-9:
            violations.append(f"final.{key}: {nv:.4f} vs 0")

    for key in EVENT_KEYS:
        ne, be = nm["events"][key], bm["events"][key]
        if ne["first_frame"] != be["first_frame"]:
            violations.append(
                f"{key}.first_frame: {ne['first_frame']} != {be['first_frame']}")
        if ne["n_frames"] != be["n_frames"]:
            violations.append(
                f"{key}.n_frames: {ne['n_frames']} != {be['n_frames']}")

    nr, br = new["runtime"], base["runtime"]
    step_mean = nr["step_wall_s"]["mean"]
    step_base = br["step_wall_s"]["mean"]
    if abs(step_mean - step_base) / step_base > 0.15:
        violations.append(
            f"step_wall mean {step_mean:.3f} vs {step_base:.3f} (>15%)")
    chunk_mean = nr["chunk_render_s"]["mean"]
    chunk_base = br["chunk_render_s"]["mean"]
    if abs(chunk_mean - chunk_base) / chunk_base > 0.15:
        violations.append(
            f"chunk_render mean {chunk_mean:.3f} vs {chunk_base:.3f} (>15%)")

    if args.check_series:
        ns, bs = nm["series"]["dist_to_gt_trajectory"], bm["series"]["dist_to_gt_trajectory"]
        if len(ns) != len(bs):
            violations.append(
                f"dist_to_gt series length {len(ns)} != baseline {len(bs)}")
        else:
            worst = max(abs(a - b) for a, b in zip(ns, bs))
            if worst > 0.25:
                idx = max(range(len(ns)), key=lambda i: abs(ns[i] - bs[i]))
                violations.append(
                    f"dist_to_gt per-frame worst {worst:.3f} m at frame {idx} "
                    f"(new {ns[idx]:.3f} vs base {bs[idx]:.3f})")

    if violations:
        print("[FAIL] Regression violations:")
        for v in violations:
            print(f"  - {v}")
        sys.exit(1)

    scope = "event+wall+pixel" if args.check_series else "event+wall"
    print(f"[SUCCESS] REGRESSION ({scope}) against {Path(args.baseline).parent.name}")


if __name__ == "__main__":
    main()
