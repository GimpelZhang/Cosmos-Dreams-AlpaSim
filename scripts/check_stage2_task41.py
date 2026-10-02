#!/usr/bin/env python3
"""Stage 2 Task 4.1 gate: 30 fps raw frames exported per variant.

asl-to-frames writes frames at
<frames>/<variant>/<scene>/<uuid>/rollout/camera_front_wide_*/<ts>.jpg
plus a second video_model_rgb_* stream; the driver-camera stream is the one
this gate checks.

Per variant: frames in [580, 610], first frame 1280x704, sampled frames
non-black (std > 10).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image

FRAMES_DIR = Path(os.environ.get(
    "STAGE2_FRAMES_DIR", "/mnt/artifacts/stage2/frames"))
MAP_CSV = Path(os.environ.get(
    "STAGE2_MAP", "/mnt/artifacts/stage2/rollout_variant_map.csv"))


def fail(msg):
    print(f"[FAIL] {msg}")
    sys.exit(1)


def main():
    import csv
    rows = list(csv.DictReader(open(MAP_CSV)))
    if len(rows) != 11:
        fail(f"map must have 11 rows, got {len(rows)}")

    for row in rows:
        vid = row["variant_id"]
        vdir = FRAMES_DIR / vid
        if not vdir.is_dir():
            fail(f"missing frames dir {vdir}")

        cam_dirs = [p for p in vdir.glob("**/rollout/*")
                    if p.is_dir() and not p.name.startswith("video_model_")]
        if len(cam_dirs) != 1:
            fail(f"{vid}: expected 1 camera stream dir, found {len(cam_dirs)}")
        files = sorted(cam_dirs[0].glob("*.jpg"))
        n = len(files)
        if not 580 <= n <= 610:
            fail(f"{vid}: frame count {n} outside [580,610]")

        sample = [files[0], files[n // 4], files[n // 2], files[3 * n // 4], files[-1]]
        for f in sample:
            im = Image.open(f)
            if im.size != (1280, 704):
                fail(f"{vid}: {f.name} size {im.size} != (1280,704)")
            std = np.asarray(im).std()
            if std <= 10:
                fail(f"{vid}: {f.name} black/blank (std={std:.1f})")

        print(f"ok {vid}: {n} frames @1280x704")

    print("[SUCCESS] Stage2 Task 4.1: 11 variants exported, frames valid.")


if __name__ == "__main__":
    main()
