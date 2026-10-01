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
import sys

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
        "frame_dt_s": round(frame_dt_s, 4) if n_frames >= 2 else "",
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("log_dir")
    ap.add_argument(
        "--manifest",
        default=None,
        help="scene manifest CSV; strongly recommended so missing clips are reported",
    )
    ap.add_argument("--out", default=None)
    ap.add_argument(
        "--strict",
        action="store_true",
        help="exit with code 2 when any clip is missing/partial/unparseable",
    )
    args = ap.parse_args()

    # One metrics.parquet per rollout dir; if a scene has several (retries or a
    # resumed partial run), summarize only the newest one and record the rest
    # as superseded so a failed early attempt is never double-counted.
    paths_by_scene: dict[str, list[str]] = {}
    for parquet_path in sorted(
        glob.glob(os.path.join(args.log_dir, "rollouts", "*", "*", "metrics.parquet"))
    ):
        rollout_dir = os.path.dirname(parquet_path)
        clip = os.path.basename(os.path.dirname(rollout_dir))
        paths_by_scene.setdefault(clip, []).append(parquet_path)

    manifest_scenes: set[str] = set()
    if args.manifest:
        with open(args.manifest, newline="") as f:
            manifest_scenes = {rec["scene_id"] for rec in csv.DictReader(f)}
    else:
        print(
            "WARNING: no --manifest given; clips that failed before producing a "
            "rollout will not appear in the summary",
            file=sys.stderr,
        )

    rows: list[dict] = []
    for clip, paths in sorted(paths_by_scene.items()):
        if args.manifest and clip not in manifest_scenes:
            print(f"WARNING: rollout present but not in manifest: {clip}", file=sys.stderr)
        paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        canonical, extra = paths[0], paths[1:]
        try:
            row = summarize_rollout(canonical)
        except Exception as exc:  # corrupt/empty/schema-changed parquet
            rows.append(
                {
                    "scene_id": clip,
                    "rollout_id": os.path.basename(os.path.dirname(canonical)),
                    "status": "parse_error",
                    "outcome": "pipeline_failed",
                    "error": str(exc),
                }
            )
        else:
            rows.append(row)
        for old_path in extra:
            rows.append(
                {
                    "scene_id": clip,
                    "rollout_id": os.path.basename(os.path.dirname(old_path)),
                    "status": "superseded",
                    "outcome": "superseded",
                }
            )

    seen = set(paths_by_scene)
    for scene_id in sorted(manifest_scenes - seen):
        rows.append({"scene_id": scene_id, "status": "missing", "outcome": "pipeline_failed"})

    # Flag truncated rollouts (well under the batch-typical frame count).
    frame_counts = [
        r["n_frames"]
        for r in rows
        if r.get("status") == "ok" and isinstance(r.get("n_frames"), int)
    ]
    if frame_counts:
        median_frames = pd.Series(frame_counts).median()
        for r in rows:
            if r.get("status") == "ok" and r["n_frames"] < 0.8 * median_frames:
                r["status"] = "partial"

    rows.sort(key=lambda r: (r["scene_id"], r.get("rollout_id", "")))
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

    # Outcomes are tallied over the active rollouts only.
    active = [r for r in rows if r.get("status") in ("ok", "partial")]
    bad = [r for r in rows if r.get("status") not in ("ok", "superseded")]
    print(f"clips: {len(paths_by_scene)} scenes, {len(active)} active rollouts -> {out}")
    counts: dict[str, int] = {}
    for r in active:
        counts[r.get("outcome", "unknown")] = counts.get(r.get("outcome", "unknown"), 0) + 1
    for outcome, count in sorted(counts.items()):
        print(f"  {outcome}: {count}")
    if active:
        for metric in ["collision_any", "offroad", "safety_monitor_triggered"]:
            clips = sum(1 for r in active if r.get(f"{metric}_frames", 0) > 0)
            frames = sum(r.get(f"{metric}_frames", 0) for r in active)
            print(f"  {metric}: {clips} clips, {frames} frames")
    for r in bad:
        print(f"  {r['status']}: {r['scene_id']}")

    if args.strict and bad:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
