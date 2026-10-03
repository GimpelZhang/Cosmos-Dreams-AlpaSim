#!/usr/bin/env python3
"""Stage 2: per-frame metrics restricted to each variant's valid window.

OmniDreams closed-loop rollouts on the validation street scene undergo
unmistakable geometric/semantic morphing after a per-variant onset (actor
induced for v07-v10, intrinsic renderer drift for v00/v05/v06). The valid
windows are recorded in configs/stage2/manifests/*_valid_windows.csv.

This script mirrors the event/distance/progress computations of
analyze_stage2.py but truncates every metric series at the window end
(inclusive frame index, 30 fps), so reported events do not include frames
generated after the world model has visibly broken down.

Writes validwindow_per_variant.csv: concrete numbers, empty strings when
inapplicable, never NaN.
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stage2_asl_replay import load_replay

METRIC_NAMES = [
    "collision_any", "collision_front", "collision_rear", "collision_lateral",
    "offroad", "min_distance_to_obstacle_m", "dist_to_gt_trajectory",
    "progress", "progress_rel",
]


def load_windowed(asl_path: Path, end_frame: int, ts0: float, fps: int = 30):
    df = pd.read_parquet(asl_path.parent / "metrics.parquet")
    df["values"] = pd.to_numeric(df["values"], errors="coerce")
    out = {"ts0": ts0}
    for name in METRIC_NAMES:
        grp = df[df["name"] == name]
        if grp.empty:
            continue
        grp = grp.groupby("timestamps_us", as_index=False)["values"].max()
        grp = grp.sort_values("timestamps_us")
        mts = grp["timestamps_us"].to_numpy()
        vals = grp["values"].to_numpy()
        idx = np.rint((mts - ts0) / 1e6 * fps).astype(int)
        keep = idx <= end_frame
        out[name] = (mts[keep], vals[keep], idx[keep])
    return out


def event_window(series, name):
    """First/last positive-frame time within window, and positive count."""
    if name not in series:
        return "", "", 0
    mts, vals, _ = series[name]
    ts0 = series["ts0"]
    hits = np.nan_to_num(vals) > 0
    count = int(hits.sum())
    if not count:
        return "", "", 0
    first = round(float((mts[hits][0] - ts0) / 1e6), 2)
    last = round(float((mts[hits][-1] - ts0) / 1e6), 2)
    return first, last, count


def value_at_end(series, name):
    if name not in series:
        return ""
    _, vals, _ = series[name]
    if not vals.size:
        return ""
    v = vals[-1]
    return round(float(v), 3) if not np.isnan(v) else ""


def window_speed(replay, end_frame: int, fps: int = 30):
    fts = np.array([f[0] for f in replay.frames])
    ego_ts = np.array([e.ts_us for e in replay.ego])
    idx = np.abs(ego_ts[None, :] - fts[:, None]).argmin(axis=1)
    v = np.array([math.hypot(replay.ego[i].vx, replay.ego[i].vy) for i in idx])
    end = min(end_frame, len(v) - 1)
    return round(float(v[0]), 2), round(float(v[end]), 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", required=True)
    ap.add_argument("--windows", required=True)
    ap.add_argument("--variants", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    windows = {r["variant_id"]: int(r["valid_end_frame"])
               for r in csv.DictReader(open(args.windows))}

    records = []
    for row in csv.DictReader(open(args.map)):
        vid = row["variant_id"]
        end_frame = windows[vid]
        asl_path = Path(row["asl_path"])
        replay = load_replay(asl_path, args.variants)
        ts0 = float(replay.frames[0][0])
        series = load_windowed(asl_path, end_frame, ts0)

        col_first, col_last, col_count = event_window(series, "collision_any")
        front_first, _, _ = event_window(series, "collision_front")
        rear_first, _, _ = event_window(series, "collision_rear")
        lat_first, _, _ = event_window(series, "collision_lateral")
        off_first, off_last, off_count = event_window(series, "offroad")

        obs = series.get("min_distance_to_obstacle_m")
        obs_min = ""
        if obs is not None and obs[1].size:
            m = np.nanmin(obs[1])
            obs_min = round(float(m), 2) if not np.isnan(m) else ""

        gt = series.get("dist_to_gt_trajectory")
        gt_max = ""
        if gt is not None and gt[1].size:
            m = np.nanmax(gt[1])
            gt_max = round(float(m), 2) if not np.isnan(m) else ""

        v0, vend = window_speed(replay, end_frame)
        records.append({
            "variant_id": vid,
            "valid_end_frame": end_frame,
            "valid_end_s": round(end_frame / 30, 2),
            "collision_first_s": col_first,
            "collision_last_s": col_last,
            "collision_frames": col_count,
            "collision_front_first_s": front_first,
            "collision_rear_first_s": rear_first,
            "collision_lateral_first_s": lat_first,
            "offroad_first_s": off_first,
            "offroad_last_s": off_last,
            "offroad_frames": off_count,
            "obs_dist_min_m": obs_min,
            "dist_to_gt_max_m": gt_max,
            "progress_end": value_at_end(series, "progress"),
            "progress_rel_end": value_at_end(series, "progress_rel"),
            "speed_start_mps": v0,
            "speed_at_window_end_mps": vend,
        })

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(records[0]))
        w.writeheader()
        w.writerows(records)
    print(f"wrote {out} ({len(records)} rows)")


if __name__ == "__main__":
    main()
