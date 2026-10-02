#!/usr/bin/env python3
"""Collect a regression digest from a wizard run logdir.

Inputs: <run_dir> <out.json>
Parses per-frame metrics.parquet (never aggregate) and runtime txt-log wall clocks.
"""
import glob
import json
import re
import sys
from pathlib import Path

import pandas as pd

BOOL_EVENTS = [
    "collision_any", "collision_front", "collision_lateral", "collision_rear",
    "offroad", "wrong_lane", "safety_monitor_triggered",
    "open_loop_collision", "img_is_black",
]
FINAL_VALUES = ["progress", "progress_rel", "progress_rel_to_total", "dist_traveled_m"]

# Wall clock prefix: "HH:MM:SS.cs"
_TS_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})\.(\d{3})")
_COMPLETED_RE = re.compile(
    r"Session COMPLETED: uuid=(\S+) scene=(\S+) simulated ([\d.]+) sim seconds "
    r"in ([\d.]+) wall clock seconds for ([\d.]+)x real time")


def wall_seconds(line: str) -> float | None:
    m = _TS_RE.match(line)
    if not m:
        return None
    h, mi, s, ms = (int(x) for x in m.groups())
    return h * 3600 + mi * 60 + s + ms / 1000.0


def parse_metrics(parquet_path: str) -> dict:
    df = pd.read_parquet(parquet_path)
    df["values"] = pd.to_numeric(df["values"], errors="coerce")
    # Some aggregate-only rows (e.g. min_ade@5.0s) carry no timestep.
    df = df[df["timestamps_us"].notna()]
    timestamps = sorted(df["timestamps_us"].unique())
    ts_index = {ts: i for i, ts in enumerate(timestamps)}

    out = {
        "parquet": parquet_path,
        "n_frames": len(timestamps),
        "timestamps_us": [int(t) for t in timestamps],
        "events": {},
        "final": {},
        "series": {},
    }
    for name in BOOL_EVENTS:
        sub = (df[df["name"] == name]
               .groupby("timestamps_us")["values"].max().sort_index())
        truthy = sub[sub > 0.5]
        first = ts_index[int(truthy.index[0])] if len(truthy) else None
        out["events"][name] = {"first_frame": first, "n_frames": int(len(truthy))}

    for name in FINAL_VALUES:
        sub = (df[df["name"] == name]
               .groupby("timestamps_us")["values"].max().sort_index())
        out["final"][name] = float(sub.iloc[-1]) if len(sub) else None

    for name in ("dist_to_gt_trajectory", "min_distance_to_obstacle_m"):
        sub = (df[df["name"] == name]
               .groupby("timestamps_us")["values"].max().sort_index())
        # align to the full frame grid
        aligned = [float(sub.get(t, float("nan"))) for t in timestamps]
        out["series"][name] = aligned

    return out


def parse_runtime_log(log_path: str) -> dict:
    lines = Path(log_path).read_text(errors="ignore").splitlines()
    # take the last worker section (log may contain appended reruns)
    last_start = 0
    for i, line in enumerate(lines):
        if "Worker 0 starting" in line:
            last_start = i
    lines = lines[last_start:]

    chunk_lat, step_wall = [], []
    pending_chunk = None
    last_policy = None
    completed = None

    for line in lines:
        t = wall_seconds(line)
        if t is None:
            continue
        if "Requesting video chunk" in line:
            pending_chunk = t
        elif "VideoModelFrameEvent" in line and pending_chunk is not None:
            chunk_lat.append(t - pending_chunk)
            pending_chunk = None
        if "PolicyEvent @" in line:
            if last_policy is not None:
                step_wall.append(t - last_policy)
            last_policy = t
        m = _COMPLETED_RE.search(line)
        if m:
            completed = {
                "rollout_uuid": m.group(1), "scene_id": m.group(2),
                "sim_seconds": float(m.group(3)), "wall_seconds": float(m.group(4)),
                "rtf": float(m.group(5)),
            }

    def stats(v):
        if not v:
            return None
        s = sorted(v)
        return {
            "n": len(v), "mean": sum(v) / len(v),
            "p50": s[len(s) // 2], "max": s[-1], "min": s[0],
        }

    return {
        "log": log_path,
        "chunk_render_s": stats(chunk_lat),
        "step_wall_s": stats(step_wall),
        "session": completed,
    }


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: check_stage2_collect_digest.py <run_dir> <out.json>")
        sys.exit(2)
    run_dir, out_path = sys.argv[1], sys.argv[2]

    parquets = sorted(glob.glob(f"{run_dir}/rollouts/*/*/metrics.parquet"))
    if len(parquets) != 1:
        print(f"[FAIL] expected exactly 1 metrics.parquet under {run_dir}, "
              f"found {len(parquets)}")
        sys.exit(1)

    logs = sorted(glob.glob(f"{run_dir}/txt-logs/runtime_worker_*.log"))
    if not logs:
        print(f"[FAIL] no runtime worker log under {run_dir}/txt-logs")
        sys.exit(1)

    digest = {
        "run_dir": run_dir,
        "metrics": parse_metrics(parquets[0]),
        "runtime": parse_runtime_log(logs[0]),
    }
    Path(out_path).write_text(json.dumps(digest, indent=2))
    print(f"[INFO] digest written: {out_path}")
    print(f"[INFO] n_frames={digest['metrics']['n_frames']} "
          f"events={ {k: v['n_frames'] for k, v in digest['metrics']['events'].items()} }")
    r = digest["runtime"]
    if r["chunk_render_s"]:
        print(f"[INFO] chunk render mean={r['chunk_render_s']['mean']:.3f}s; "
              f"step wall mean={r['step_wall_s']['mean']:.3f}s")


if __name__ == "__main__":
    main()
