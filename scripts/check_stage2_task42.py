#!/usr/bin/env python3
"""Stage 2 Task 4.2 gate: BEV Plan A frames aligned with camera frames.

Per variant:
- BEV png count equals camera frame count (+/-1)
- BEV non-blank: ink pixels (non-white) above threshold, sampled across run
- yellow predicted trajectory visible
- injected actors: red box pixels appear after activation; non-actor variants
  must contain no red-box pixels
"""
from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image

BEV_DIR = Path(os.environ.get("STAGE2_BEV_DIR", "/mnt/artifacts/stage2/bev"))
FRAMES_DIR = Path(os.environ.get(
    "STAGE2_FRAMES_DIR", "/mnt/artifacts/stage2/frames"))
MAP_CSV = Path(os.environ.get(
    "STAGE2_MAP", "/mnt/artifacts/stage2/rollout_variant_map.csv"))
VARIANTS_YAML = Path("/home/vipuser/simulation/configs/stage2/variants.yaml")

MIN_INK = 0.02


def fail(msg):
    print(f"[FAIL] {msg}")
    sys.exit(1)


def color_mask(arr, target, tol=40):
    return (np.abs(arr.astype(int) - np.array(target)).max(axis=2) <= tol)


def main():
    import yaml
    spec = yaml.safe_load(VARIANTS_YAML.read_text())
    n_actors = {v["id"]: len(v.get("actors", [])) for v in spec["variants"]}
    activate = {v["id"]: min([a["activate_s"] for a in v["actors"]], default=0)
                for v in spec["variants"]}

    rows = list(csv.DictReader(open(MAP_CSV)))
    if len(rows) != 11:
        fail(f"map must have 11 rows, got {len(rows)}")

    for row in rows:
        vid = row["variant_id"]
        d = BEV_DIR / vid
        pngs = sorted(d.glob("*.png"))
        # BEV is rendered per camera frame (30 fps); the map's n_frames is the
        # 75 metric-frame count, so count the exported camera stream instead.
        cam_stream = (FRAMES_DIR / vid / row["scene_id"] / row["rollout_uuid"]
                      / "rollout" / "camera_front_wide_120fov")
        n_cam = len(list(cam_stream.glob("*.jpg")))
        if n_cam == 0:
            fail(f"{vid}: no exported camera frames at {cam_stream}")
        if not pngs or abs(len(pngs) - n_cam) > 1:
            fail(f"{vid}: BEV frames {len(pngs)} vs camera {n_cam}")

        sample_idx = [0, n_cam // 4, n_cam // 2, 3 * n_cam // 4, n_cam - 1]
        ink_fractions = []
        saw_yellow = False
        for i in sample_idx:
            arr = np.array(Image.open(pngs[min(i, len(pngs) - 1)]).convert("RGB"))
            ink = (arr < 245).any(axis=2)
            ink_fractions.append(ink.mean())
            if color_mask(arr, (235, 190, 30), tol=35).sum() > 15:
                saw_yellow = True

        # Dense red scan: an injected box may be on-screen only briefly as it
        # crosses behind the ego and leaves the 32.5 m BEV window, so fixed
        # sample points cannot be relied upon.
        red_frames = []
        for i, png in enumerate(pngs):
            arr = np.array(Image.open(png).convert("RGB"))
            if color_mask(arr, (220, 50, 50)).sum() > 10:
                red_frames.append(i)

        if min(ink_fractions) < MIN_INK:
            fail(f"{vid}: blank BEV (min ink {min(ink_fractions):.3f})")
        if not saw_yellow:
            fail(f"{vid}: predicted trajectory not visible")

        if n_actors[vid] > 0:
            if not red_frames:
                fail(f"{vid}: injected actor red box never drawn")
            first_red_s = red_frames[0] / 30
            if first_red_s < activate[vid] - 0.5:
                fail(f"{vid}: red box at t={first_red_s:.2f}s before "
                     f"activation t={activate[vid]}s")
            # The original timing bug left the actor behind the ego by the time
            # it entered the BEV window: the first red box must be drawn ahead
            # of the ego (screen row above center) and within 3 s of activation.
            if first_red_s > activate[vid] + 3.0:
                fail(f"{vid}: first red box t={first_red_s:.2f}s appears more "
                     f"than 3 s after activation t={activate[vid]}s")
            first_arr = np.array(Image.open(pngs[red_frames[0]]).convert("RGB"))
            red_rows = np.where(color_mask(first_arr, (220, 50, 50)))[0]
            if red_rows.mean() >= first_arr.shape[0] / 2:
                fail(f"{vid}: first red box already behind the ego "
                     f"(centroid row {red_rows.mean():.0f})")
        else:
            if red_frames:
                fail(f"{vid}: red box present despite zero injected actors")

        first_red = f"{red_frames[0] / 30:.2f}s" if red_frames else "none"
        print(f"ok {vid}: {len(pngs)} frames, ink>={min(ink_fractions):.3f}, "
              f"first red {first_red}, {len(red_frames)} red frames")

    print("[SUCCESS] Stage2 Task 4.2: BEV Plan A frames aligned and non-blank.")


if __name__ == "__main__":
    main()
