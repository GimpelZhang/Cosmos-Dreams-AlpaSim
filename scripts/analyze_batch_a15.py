#!/usr/bin/env python3
"""Summarize per-clip closed-loop outcomes from rollout metrics.parquet files.

Each rollout's metrics.parquet (long format, per-timestep rows) is the
authoritative source: the wizard's aggregate metrics_results.txt applies
RemoveTimestepsAfterEvent modifiers and can report zero collision/offroad
even when the raw rollout contains them.

Usage:
  python scripts/analyze_batch_a15.py <log_dir> [--manifest <csv>] [--out <csv>]

The manifest (uuid,scene_id,...) ensures missing/failed clips appear in the
summary instead of being silently skipped.
"""
from __future__ import annotations

import argparse
import csv
import glob
import os

import pandas as pd

BOOL_METRICS = [
    "collision_any",
    "collision_front",
    "collision_lateral",
    "collision_rear",
    "offroad",
    "wrong_lane",
    "safety_monitor_triggered",
    "open_loop_collision",
]
# metric -> reduction across the clip
VALUE_METRICS = {
    "dist_to_gt_trajectory": "max",
    "dist_to_gt_location": "max",
    "progress": "last",
    "progress_rel": "last",
    "dist_traveled_m": "last",
    "gt_dist_traveled_m": "last",
    "min_distance_to_obstacle_m": "min",
    "min_distance_to_lane_boundary_m": "min",
    "plan_deviation": "mean",
}
MINADES = [
    "min_ade@0.5s(gt)",
    "min_ade@1.0s(gt)",
    "min_ade@2.5s(gt)",
    "min_ade@5.0s(gt)",
]


def load_per_timestep(parquet_path: str) -> pd.DataFrame:
    df = pd.read_parquet(parquet_path)
    df["val"] = pd.to_numeric(df["values"], errors="coerce")
    # If the same (metric, timestamp) repeats (camera rows), keep the max.
    return df.groupby(["name", "timestamps_us"], as_index=False)["val"].max()


def summarize_rollout(parquet_path: str) -> dict:
    g = load_per_timestep(parquet_path)
    rollout_dir = os.path.dirname(parquet_path)
    clip = os.path.basename(os.path.dirname(rollout_dir))

    all_ts = g["timestamps_us"].drop_duplicates().sort_values()
    t0, t1 = all_ts.iloc[0], all_ts.iloc[-1]
    n_frames = len(all_ts)
    # Median frame delta is the video-model frame interval (~266.7ms = 3.75fps).
    frame_dt_s = all_ts.diff().median() / 1e6

    row: dict = {
        "scene_id": clip,
        "rollout_id": os.path.basename(rollout_dir),
        "status": "ok",
        "n_frames": n_frames,
        "clip_s": round((t1 - t0) / 1e6, 2),
        "frame_dt_s": round(frame_dt_s, 4),
    }

    series = {
        name: sub.sort_values("timestamps_us")
        for name, sub in g.groupby("name")
    }

    for metric in BOOL_METRICS:
        sub = series.get(metric)
        if sub is None or sub["val"].notna().sum() == 0:
            row[f"{metric}_frames"] = 0
            row[f"{metric}_first_s"] = ""
            row[f"{metric}_last_s"] = ""
            continue
        hit = sub[sub["val"] >= 0.5]
        row[f"{metric}_frames"] = len(hit)
        if len(hit):
            row[f"{metric}_first_s"] = round((hit["timestamps_us"].iloc[0] - t0) / 1e6, 2)
            row[f"{metric}_last_s"] = round((hit["timestamps_us"].iloc[-1] - t0) / 1e6, 2)

    for metric, reduction in VALUE_METRICS.items():
        sub = series.get(metric)
        vals = sub["val"] if sub is not None else None
        if vals is None or vals.notna().sum() == 0:
            row[metric] = ""
        elif reduction == "last":
            row[metric] = round(vals.dropna().iloc[-1], 3)
        elif reduction == "max":
            row[metric] = round(vals.max(), 3)
        elif reduction == "min":
            row[metric] = round(vals.min(), 3)
        else:
            row[metric] = round(vals.mean(), 3)

    for metric in MINADES:
        sub = series.get(metric)
        vals = sub["val"] if sub is not None else None
        row[metric] = "" if vals is None or vals.notna().sum() == 0 else round(vals.mean(), 3)

    coll = row["collision_any_frames"] > 0
    off = row["offroad_frames"] > 0
    if coll and off:
        row["outcome"] = "collision+offroad"
    elif coll:
        row["outcome"] = "collision"
    elif off:
        row["outcome"] = "offroad"
    else:
        row["outcome"] = "clean"
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("log_dir")
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    rows: list[dict] = []
    seen: set[str] = set()
    for parquet_path in sorted(
        glob.glob(os.path.join(args.log_dir, "rollouts", "*", "*", "metrics.parquet"))
    ):
        row = summarize_rollout(parquet_path)
        rows.append(row)
        seen.add(row["scene_id"])

    if args.manifest:
        with open(args.manifest, newline="") as f:
            for rec in csv.DictReader(f):
                if rec["scene_id"] not in seen:
                    rows.append(
                        {"scene_id": rec["scene_id"], "status": "missing", "outcome": "pipeline_failed"}
                    )

    rows.sort(key=lambda r: r["scene_id"])
    out = args.out or os.path.join(args.log_dir, "batch_summary.csv")
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    ok = [r for r in rows if r.get("status") == "ok"]
    print(f"clips: {len(rows)} total, {len(ok)} with metrics -> {out}")
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.get("outcome", "unknown")] = counts.get(r.get("outcome", "unknown"), 0) + 1
    for outcome, count in sorted(counts.items()):
        print(f"  {outcome}: {count}")
    if ok:
        for metric in ["collision_any", "offroad", "safety_monitor_triggered"]:
            clips = sum(1 for r in ok if r.get(f"{metric}_frames", 0) > 0)
            frames = sum(r.get(f"{metric}_frames", 0) for r in ok)
            print(f"  {metric}: {clips} clips, {frames} frames")


if __name__ == "__main__":
    main()
