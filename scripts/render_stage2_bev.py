#!/usr/bin/env python3
"""Stage 2 Task 4.2 Plan A: render top-down BEV frames from ASL replay.

One 260x260 ego-centered BEV per camera frame. Dynamic elements (ego pose,
actor poses, predicted trajectory) are sampled at the nearest control step
and broadcast to the 30 fps camera timeline. Static map rails come from the
self-contained hdmap zip: lane/lane_line/road_boundary parquets.

Style per plan Task 4.2: white background, black lane lines, injected actors
red boxes, recorded actors gray boxes, ego green box, predicted trajectory
yellow. Ego box is a display convention (4.7 x 2.0 m); no vehicle dimensions
are recorded in the data.
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
from PIL import Image, ImageDraw

from stage2_asl_replay import load_replay

SIZE = 260
PX_PER_M = 8.0
CENTER = SIZE / 2
EGO_HALF_L = 2.35
EGO_HALF_W = 1.0

BLACK = (20, 20, 20)
GRAY = (150, 150, 150)
LANE_RAIL = (200, 200, 200)
DARK_GRAY = (90, 90, 90)
RED = (220, 50, 50)
# Actor broadcasts arrive ~every 0.267 s; tolerate one missing broadcast.
TRACK_GAP_US = 300_000
GREEN = (60, 170, 90)
YELLOW = (235, 190, 30)


def _rail_xy(struct_rail):
    return np.array([(p["x"], p["y"]) for p in struct_rail], dtype=float)


def build_map_geometry(replay):
    """Static polylines: (kind, xy array, style, color)."""
    z = replay.hdmap_zipfile()
    prefix = [n for n in z.namelist() if n.endswith(".lane.parquet")][0].rsplit(".", 2)[0]
    geo = []

    def read(name):
        import io
        import pandas as pd
        return pd.read_parquet(io.BytesIO(z.read(f"{prefix}.{name}.parquet")))

    lanes = read("lane")
    for _, row in lanes.iterrows():
        lane = row["lane"]
        for rail_name in ("left_rail", "right_rail"):
            a = _rail_xy(lane[rail_name])
            if a.size:
                geo.append(("lane", a, None, None))

    lines = read("lane_line")
    for _, row in lines.iterrows():
        ll = row["lane_line"]
        a = _rail_xy(ll["line_rail"])
        if a.size:
            style = ll["styles"][0] if len(ll["styles"]) else ""
            color = ll["colors"][0] if len(ll["colors"]) else ""
            geo.append(("lane_line", a, style, color))

    boundaries = read("road_boundary")
    for _, row in boundaries.iterrows():
        rb = row["road_boundary"]
        a = _rail_xy(rb["location"])
        if a.size:
            geo.append(("boundary", a, None, None))
    return geo


def _to_ego(points, ego):
    """Map-frame xy -> ego-frame xy at the given ego state."""
    d = points - np.array([ego.x, ego.y])
    c, s = math.cos(math_yaw(ego)), math.sin(math_yaw(ego))
    rot = np.array([[c, s], [-s, c]])
    return d @ rot.T


def math_yaw(ego) -> float:
    q = (ego.qx, ego.qy, ego.qz, ego.qw)
    from scipy.spatial.transform import Rotation
    return Rotation.from_quat(q).as_euler("xyz")[2]


def _screen(points_ego):
    u = CENTER - points_ego[:, 1] * PX_PER_M
    v = CENTER - points_ego[:, 0] * PX_PER_M
    return np.column_stack([u, v])


def _polyline(draw, screen_pts, fill, width=1, dash=None):
    pts = [tuple(p) for p in screen_pts]
    if not dash:
        draw.line(pts, fill=fill, width=width)
        return
    for a, b in zip(pts, pts[1:]):
        seg_len = math.hypot(b[0] - a[0], b[1] - a[1])
        if seg_len == 0:
            continue
        step = 0
        while step < seg_len:
            t0 = step / seg_len
            t1 = min(step + dash[0], seg_len) / seg_len
            draw.line([(a[0] + (b[0] - a[0]) * t0, a[1] + (b[1] - a[1]) * t0),
                       (a[0] + (b[0] - a[0]) * t1, a[1] + (b[1] - a[1]) * t1)],
                      fill=fill, width=width)
            step += dash[0] + dash[1]


def render_variant(asl_path: str, out_dir: Path, variants_spec: str):
    replay = load_replay(asl_path, variants_spec)
    geometry = build_map_geometry(replay)
    out_dir.mkdir(parents=True, exist_ok=True)

    frame_ts = [f[0] for f in replay.frames]

    for idx, ts in enumerate(frame_ts, start=1):
        ego = replay.ego_at(ts)
        img = Image.new("RGB", (SIZE, SIZE), (255, 255, 255))
        draw = ImageDraw.Draw(img)

        # Static map.
        for kind, a, style, color in geometry:
            ego_pts = _to_ego(a, ego)
            if ego_pts[:, 0].min() > SIZE / PX_PER_M or ego_pts[:, 0].max() < -SIZE / PX_PER_M:
                continue
            screen_pts = _screen(ego_pts)
            if kind == "lane":
                _polyline(draw, screen_pts, LANE_RAIL, 1)
            elif kind == "boundary":
                _polyline(draw, screen_pts, DARK_GRAY, 2)
            else:
                fill = BLACK
                dash = (7, 6) if "DASHED" in style else None
                width = 2 if color == "YELLOW" else 1
                _polyline(draw, screen_pts, fill, width, dash)

        # Predicted trajectory (yellow), nearest driver_return.
        pred = min(replay.predictions, key=lambda p: abs(p[0] - ts)) if replay.predictions else None
        if pred and pred[1]:
            pa = np.array([(p[1], p[2]) for p in pred[1]])
            ego_pts = _to_ego(pa, ego)
            _polyline(draw, _screen(ego_pts), YELLOW, 2)

        # Actor boxes.
        for object_id in replay.actor_tracks:
            track = replay.actor_tracks[object_id]
            # Do not extrapolate beyond the track coverage (one broadcast gap
            # tolerance); injected actors must not appear before activation.
            if not (track[0][0] - TRACK_GAP_US <= ts <= track[-1][0] + TRACK_GAP_US):
                continue
            pose = replay.actor_pose_at(object_id, ts)
            if pose is None:
                continue
            from scipy.spatial.transform import Rotation
            obj_yaw = Rotation.from_quat((pose[4], pose[5], pose[6], pose[7])).as_euler("xyz")[2]
            obj_def = replay.objects.get(object_id)
            hl = obj_def.size_x / 2 if obj_def else 0.8
            hw = obj_def.size_y / 2 if obj_def else 0.6
            local = np.array([(-hl, -hw), (hl, -hw), (hl, hw), (-hl, hw)])
            c, s = math.cos(obj_yaw), math.sin(obj_yaw)
            corners = local @ np.array([[c, -s], [s, c]]).T + np.array([pose[1], pose[2]])
            ego_pts = _to_ego(corners, ego)
            screen_pts = _screen(ego_pts)
            outline = RED if (obj_def and obj_def.injected) else GRAY
            width = 2 if outline == RED else 1
            draw.polygon([tuple(p) for p in screen_pts], outline=outline, width=width)

        # Ego green box, heading tick.
        box = [(-EGO_HALF_L, -EGO_HALF_W), (EGO_HALF_L, -EGO_HALF_W),
               (EGO_HALF_L, EGO_HALF_W), (-EGO_HALF_L, EGO_HALF_W)]
        draw.polygon([(CENTER - y * PX_PER_M, CENTER - x * PX_PER_M) for x, y in box],
                     outline=GREEN, width=2)
        draw.line([(CENTER, CENTER), (CENTER, CENTER - EGO_HALF_L * PX_PER_M)],
                  fill=GREEN, width=2)

        img.save(out_dir / f"{idx:06d}.png")

    return len(frame_ts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", help="batch run directory (unused when --map given)")
    ap.add_argument("--variants", required=True)
    ap.add_argument("--map", required=True, help="rollout_variant_map.csv")
    ap.add_argument("--out-dir", default="/mnt/artifacts/stage2/bev")
    args = ap.parse_args()

    rows = list(csv.DictReader(open(args.map)))
    for row in rows:
        out_dir = Path(args.out_dir) / row["variant_id"]
        n = render_variant(row["asl_path"], out_dir, args.variants)
        print(f"{row['variant_id']}: {n} BEV frames -> {out_dir}")


if __name__ == "__main__":
    main()
