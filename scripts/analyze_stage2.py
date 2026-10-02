#!/usr/bin/env python3
"""Stage 2 Task 5.1: per-frame analysis for the 11 variants (plan §8).

Reads the per-frame metrics.parquet (long format, numeric coercion,
same-name/same-ts max) and the ASL ego dynamics, then writes:
- per_variant.csv: one row per variant with event frames/counts, distances,
  progress, derived speed/steer reactions, derived TTC, PASS verdict
- reaction_table.csv: deltas vs v00 baseline plus PASS/FAIL

All values are concrete numbers; inapplicable cells are empty strings,
never NaN.
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
    "progress", "progress_rel", "img_is_black",
]


def load_metrics(asl_path: Path):
    df = pd.read_parquet(asl_path.parent / "metrics.parquet")
    df["values"] = pd.to_numeric(df["values"], errors="coerce")
    out = {}
    for name in METRIC_NAMES:
        grp = df[df["name"] == name]
        if grp.empty:
            continue
        grp = grp.groupby("timestamps_us", as_index=False)["values"].max()
        grp = grp.sort_values("timestamps_us")
        out[name] = (grp["timestamps_us"].to_numpy(), grp["values"].to_numpy())
    return out


def at(series, name, ts):
    if name not in series:
        return None
    mts, vals = series[name]
    return vals[int(np.abs(mts - ts).argmin())]


def event_first_count(series, name, fps=30, ts0=None):
    if name not in series:
        return "", 0
    mts, vals = series[name]
    hits = np.nan_to_num(vals) > 0
    count = int(hits.sum())
    if not count:
        return "", 0
    first_us = mts[np.argmax(hits)]
    return round((first_us - ts0) / 1e6 * fps) / fps, count


def speed_series(replay):
    """Ego speed sampled onto camera-frame indices; t = idx/30."""
    n = len(replay.frames)
    ts = np.array([f[0] for f in replay.frames])
    ego_ts = np.array([e.ts_us for e in replay.ego])
    idx = np.abs(ego_ts[None, :] - ts[:, None]).argmin(axis=1)
    v = np.array([math.hypot(replay.ego[i].vx, replay.ego[i].vy) for i in idx])
    t = np.arange(n) / 30
    return t, v, np.array([replay.ego[i].yaw_rate for i in idx])


def first_decel(t, v, after=0.0, threshold=0.8):
    """First sustained deceleration onset (s), via >=0.8 m/s drop window."""
    dv = np.full(len(t), np.nan)
    win = int(0.5 * 30)
    for i in range(len(t) - win):
        dv[i] = v[i + win] - v[i]
    mask = (t >= after) & (dv < -threshold)
    if not mask.any():
        return ""
    return round(float(t[mask][0]), 2)


def max_deceleration(t, v, after=0.0):
    dt = np.diff(t)
    a = np.diff(v) / dt
    m = t[:-1] >= after
    if not m.any() or len(a) == 0:
        return ""
    neg = a[m]
    return round(float(neg.min()), 2)


def lateral_offset(replay):
    n = len(replay.frames)
    ts = np.array([f[0] for f in replay.frames])
    ego_ts = np.array([e.ts_us for e in replay.ego])
    idx = np.abs(ego_ts[None, :] - ts[:, None]).argmin(axis=1)
    y = np.array([replay.ego[i].y for i in idx])
    return round(float(np.abs(y).max()), 2)


def derived_ttc(replay, series, activate_s, ts0):
    """TTC against nearest injected obstacle while distance is falling."""
    if "min_distance_to_obstacle_m" not in series:
        return ""
    mts, dist = series["min_distance_to_obstacle_m"]
    injected = [oid for oid in replay.actor_tracks if oid.startswith("s2_")]
    if not injected:
        return ""
    ttcs = []
    for oid in injected:
        track = replay.actor_tracks[oid]
        trk_ts = np.array([p[0] for p in track])
        for i in range(1, len(mts)):
            t_s = (mts[i] - ts0) / 1e6
            if t_s < activate_s:
                continue
            d = dist[i]
            if d is None or np.isnan(d):
                continue
            j = int(np.abs(trk_ts - mts[i]).argmin())
            if j < 1 or j >= len(track) - 1:
                continue
            dt = (track[j + 1][0] - track[j - 1][0]) / 1e6
            if dt <= 0:
                continue
            ovx = (track[j + 1][1] - track[j - 1][1]) / dt
            ego = replay.ego_at(mts[i])
            closing = ego.vx - ovx
            if closing > 0.3:
                ttcs.append(d / closing)
    if not ttcs:
        return ""
    return round(float(min(ttcs)), 2)


def analyze_variant(vid, spec_variant, asl_path: Path, variants_spec: str):
    replay = load_replay(asl_path, variants_spec)
    series = load_metrics(asl_path)

    ts0 = replay.frames[0][0]
    actors = spec_variant.get("actors", [])
    activate_s = min([a["activate_s"] for a in actors], default=0.0)
    category = spec_variant["category"]

    col_first, col_count = event_first_count(series, "collision_any", ts0=ts0)
    col_front_first, _ = event_first_count(series, "collision_front", ts0=ts0)
    col_rear_first, _ = event_first_count(series, "collision_rear", ts0=ts0)
    col_lat_first, _ = event_first_count(series, "collision_lateral", ts0=ts0)
    off_first, off_count = event_first_count(series, "offroad", ts0=ts0)

    dist = series.get("min_distance_to_obstacle_m", (None, np.array([])))[1]
    obs_min = round(float(np.nanmin(dist)), 2) if dist.size else ""
    mts = series.get("min_distance_to_obstacle_m", (np.array([]), None))[0]
    after_mask = (mts - ts0) / 1e6 >= activate_s if mts.size else np.array([])
    obs_after_min = (round(float(np.nanmin(dist[after_mask])), 2)
                     if after_mask.any() else "")

    gt_dist = series.get("dist_to_gt_trajectory", (None, np.array([])))[1]
    gt_max = round(float(np.nanmax(gt_dist)), 2) if gt_dist.size else ""
    progress_end = at(series, "progress", replay.frames[-1][0])
    progress_rel_end = at(series, "progress_rel", replay.frames[-1][0])

    black_rate = 0.0
    if "img_is_black" in series:
        black_rate = float(np.nan_to_num(series["img_is_black"][1]).mean())

    t, v, yaw = speed_series(replay)
    final_speed = round(float(v[-1]), 2)
    decel_t = first_decel(t, v, after=activate_s if actors else 0.0)
    max_dec = max_deceleration(t, v, after=activate_s if actors else 0.0)
    lat_max = lateral_offset(replay)
    speed_change_8s = ""
    if category in ("weather", "weather_lighting", "lighting"):
        i8 = int(8 * 30)
        if i8 < len(v):
            speed_change_8s = round(float(v[i8] - v[0]), 2)
    ttc = derived_ttc(replay, series, activate_s, ts0)

    if black_rate > 0.02:
        verdict = "SYSTEM_ERROR"
    elif category in ("weather", "weather_lighting", "lighting"):
        verdict = "PASS" if col_count == 0 and off_count == 0 else "FAIL"
    elif category == "baseline":
        verdict = ""
    else:
        avoid = decel_t != "" or lat_max > 0.5
        passed = (col_count == 0 and off_count == 0
                  and (obs_after_min == "" or obs_after_min >= 0.8) and avoid)
        verdict = "PASS" if passed else "FAIL"

    return {
        "variant_id": vid,
        "category": category,
        "activate_s": activate_s if actors else "",
        "collision_first_s": col_first,
        "collision_frames": col_count,
        "collision_front_first_s": col_front_first,
        "collision_rear_first_s": col_rear_first,
        "collision_lateral_first_s": col_lat_first,
        "offroad_first_s": off_first,
        "offroad_frames": off_count,
        "obs_dist_min_m": obs_min,
        "obs_dist_after_activation_min_m": obs_after_min,
        "dist_to_gt_max_m": gt_max,
        "progress_end": round(progress_end, 2) if progress_end is not None else "",
        "progress_rel_end": round(progress_rel_end, 2) if progress_rel_end is not None else "",
        "first_decel_s": decel_t,
        "max_decel_mps2": max_dec,
        "final_speed_mps": final_speed,
        "max_lateral_offset_m": lat_max,
        "speed_change_0_8s_mps": speed_change_8s,
        "derived_ttc_min_s": ttc,
        "black_frame_rate": round(black_rate, 4),
        "verdict": verdict,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--variants", required=True)
    ap.add_argument("--map", default="/mnt/artifacts/stage2/rollout_variant_map.csv")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    import yaml
    spec = yaml.safe_load(Path(args.variants).read_text())
    spec_by_id = {v["id"]: v for v in spec["variants"]}

    records = []
    for row in csv.DictReader(open(args.map)):
        vid = row["variant_id"]
        records.append(analyze_variant(vid, spec_by_id[vid],
                                       Path(row["asl_path"]), args.variants))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    per_variant = out_dir / "per_variant.csv"
    with open(per_variant, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(records[0]))
        w.writeheader()
        w.writerows(records)

    base = next(r for r in records if r["variant_id"] == "v00_baseline")
    reaction_rows = []
    for r in records:
        def delta(key):
            a, b = r[key], base[key]
            if a == "" or b == "":
                return ""
            return round(a - b, 2)
        reaction_rows.append({
            "variant_id": r["variant_id"],
            "first_decel_delta_s": delta("first_decel_s"),
            "max_decel_delta_mps2": delta("max_decel_mps2"),
            "obs_dist_min_m": r["obs_dist_min_m"],
            "collision_frames_delta": delta("collision_frames"),
            "offroad_frames_delta": delta("offroad_frames"),
            "dist_to_gt_max_delta_m": delta("dist_to_gt_max_m"),
            "verdict": r["verdict"],
        })
    reaction = out_dir / "reaction_table.csv"
    with open(reaction, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(reaction_rows[0]))
        w.writeheader()
        w.writerows(reaction_rows)

    print(f"wrote {per_variant} and {reaction} ({len(records)} rows)")


if __name__ == "__main__":
    main()
