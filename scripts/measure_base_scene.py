#!/usr/bin/env python3
"""Stage 2 Task 0.4 spike: measure base-scene GT initial speed and road curvature
from a rollout ASL, cross-check speed against per-frame metrics, and verify
rig-frame axis handedness.

Data sources (see docs/Stage2_Plan_detailed.md §6 Task 0.4):
- video_model_chunk_request.rig_trajectory: continuous global/clip-frame ego
  poses at video rate — the trajectory actually consumed by the renderer.
- rollout_metadata.ego_rig_recorded_ground_truth_trajectory: recorded GT poses
  used only for the rig-axis handedness check.
- metrics.parquet dist_traveled_m: independent speed cross-check during the
  force-GT window (gt_dist_traveled_m is a constant route total here).

Note: ActorPoses.ActorPose.actor_pose is an active local->aabb transform whose
anchor frame resets during a rollout, so it MUST NOT be used for global
kinematics.

usage: measure_base_scene.py <asl_file> <metrics_parquet> <out_json>
"""
import asyncio
import json
import math
import sys

import pandas as pd

from alpasim_utils.logs import async_read_pb_log


def quat_to_yaw(q) -> float:
    # yaw from quaternion (w, x, y, z)
    siny_cosp = 2 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1 - 2 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)


def turn_stats(poses: list[tuple]) -> dict:
    """poses: sorted unique (timestamp_us, x, y, yaw). Returns arc/turn/radius."""
    arc = 0.0
    total_turn = 0.0
    for i in range(1, len(poses)):
        arc += math.hypot(poses[i][1] - poses[i - 1][1],
                          poses[i][2] - poses[i - 1][2])
        dyaw = poses[i][3] - poses[i - 1][3]
        total_turn += math.atan2(math.sin(dyaw), math.cos(dyaw))
    return {
        "arc_length_m": round(arc, 2),
        "total_turn_rad": round(abs(total_turn), 4),
        "curvature_radius_m": round(arc / abs(total_turn), 1) if abs(total_turn) > 1e-6 else "inf",
    }


def initial_speed(poses: list[tuple], n: int = 8) -> dict:
    """Median per-step speed over the first n steps (video-rate poses)."""
    speeds = []
    for i in range(1, min(n + 1, len(poses))):
        dt = (poses[i][0] - poses[i - 1][0]) / 1e6
        if dt <= 0:
            continue
        v = math.hypot(poses[i][1] - poses[i - 1][1],
                       poses[i][2] - poses[i - 1][2]) / dt
        speeds.append(v)
    speeds.sort()
    med = speeds[len(speeds) // 2] if speeds else float("nan")
    return {"gt_v0_kmh": round(med * 3.6, 2), "v0_n_steps": len(speeds)}


def metrics_speed_crosscheck(parquet_path: str) -> dict:
    """Speed cross-check from ego dist_traveled_m differences during the
    force-GT window (ego == recorded GT for the first ~1.7s).

    The first two control steps are physics/force-GT ramp artifacts (0 then a
    jump), so speeds are taken from steps 3..7. gt_dist_traveled_m is a
    constant route-total value in this pipeline and cannot be differenced.
    """
    df = pd.read_parquet(parquet_path)
    df["values"] = pd.to_numeric(df["values"], errors="coerce")
    df = df[df["timestamps_us"].notna()]
    sub = (df[df["name"] == "dist_traveled_m"]
           .groupby("timestamps_us")["values"].max().sort_index())
    ts = list(sub.index)
    vals = list(sub.values)
    speeds = []
    for i in range(3, min(8, len(vals))):
        dt = (ts[i] - ts[i - 1]) / 1e6
        if dt > 0 and pd.notna(vals[i]) and pd.notna(vals[i - 1]):
            speeds.append((vals[i] - vals[i - 1]) / dt)
    speeds.sort()
    out = {"n_steps": len(speeds)}
    out["gt_v0_kmh"] = round(speeds[len(speeds) // 2] * 3.6, 2) if speeds else None
    # final GT progress and ego progress for scene screening
    for name in ("progress_rel", "progress_rel_to_total", "dist_traveled_m"):
        s = (df[df["name"] == name].groupby("timestamps_us")["values"].max().sort_index())
        out[name] = float(s.iloc[-1]) if len(s) else None
    return out


async def extract(asl_path: str, parquet_path: str) -> dict:
    rig_poses = []  # (timestamp_us, x, y, yaw) global/clip frame
    rig_gt = None
    metadata = None

    async for entry in async_read_pb_log(asl_path):
        kind = entry.WhichOneof("log_entry")
        if kind == "video_model_chunk_request":
            tr = entry.video_model_chunk_request.rig_trajectory
            for p in tr.poses:
                rig_poses.append((int(p.timestamp_us), p.pose.vec.x,
                                  p.pose.vec.y, quat_to_yaw(p.pose.quat)))
        elif kind == "rollout_metadata":
            metadata = entry.rollout_metadata
            rig_gt = metadata.ego_rig_recorded_ground_truth_trajectory

    rig_poses.sort(key=lambda p: p[0])
    uniq = []
    for p in rig_poses:
        if not uniq or uniq[-1][0] != p[0]:
            uniq.append(p)

    v0 = initial_speed(uniq)
    geom = turn_stats(uniq)
    xcheck = metrics_speed_crosscheck(parquet_path)

    # Rig-frame handedness check: longitudinal delta positive, lateral ~0.
    rig_check = {"n_samples": 0, "long_deltas": [], "lat_abs_max": None}
    if rig_gt is not None and len(rig_gt.poses) >= 4:
        poses = rig_gt.poses
        for i in range(1, min(6, len(poses))):
            p0, p1 = poses[i - 1].pose, poses[i].pose
            dx = p1.vec.x - p0.vec.x
            dy = p1.vec.y - p0.vec.y
            rig_check["long_deltas"].append(round(dx, 4))
            rig_check["lat_abs_max"] = max(rig_check["lat_abs_max"] or 0.0, abs(dy))
            rig_check["n_samples"] += 1

    speed_agree = (
        xcheck.get("gt_v0_kmh") is not None
        and abs(xcheck["gt_v0_kmh"] - v0["gt_v0_kmh"]) <= 3.0
    )

    return {
        "asl": asl_path,
        "metrics_parquet": parquet_path,
        "scene_id": metadata.session_metadata.scene_id if metadata else None,
        "n_rig_poses": len(uniq),
        **v0,
        **geom,
        "metrics_crosscheck": xcheck,
        "speed_sources_agree": bool(speed_agree),
        "rig_handedness_check": rig_check,
        "interpretation": (
            "rig x=longitudinal forward positive confirmed"
            if rig_check["n_samples"] and
            all(d > 0 for d in rig_check["long_deltas"]) and
            (rig_check["lat_abs_max"] or 1) < 0.5 else "inconclusive"),
    }


def main():
    if len(sys.argv) != 4:
        print("usage: measure_base_scene.py <asl_file> <metrics_parquet> <out_json>")
        sys.exit(2)
    out = asyncio.run(extract(sys.argv[1], sys.argv[2]))
    with open(sys.argv[3], "w") as f:
        json.dump(out, f, indent=2)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
