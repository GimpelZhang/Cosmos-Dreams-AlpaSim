#!/usr/bin/env python3
"""Stage 2 Task 4.3 gate: 11 HUD videos encoded and readable.

Per variant mp4: 1280x704, duration 19.5-20.1s, >=580 frames; sampled
frames at t=1/5/10/19s non-black (std>10) and HUD bars present (top bar
rows darker than camera content, bottom bar contains non-background pixels).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

VIDEO_DIR = Path(os.environ.get(
    "STAGE2_VIDEO_DIR", "/mnt/artifacts/stage2/videos"))
MAP_CSV = Path(os.environ.get(
    "STAGE2_MAP", "/mnt/artifacts/stage2/rollout_variant_map.csv"))


def fail(msg):
    print(f"[FAIL] {msg}")
    sys.exit(1)


def ffprobe(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"ffprobe failed for {path}: {r.stderr}")
    return json.loads(r.stdout)


def extract_frame(path, t_s) -> np.ndarray:
    r = subprocess.run(
        ["ffmpeg", "-y", "-ss", str(t_s), "-i", str(path),
         "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
        capture_output=True)
    if not r.stdout:
        fail(f"could not extract frame t={t_s} from {path}")
    return np.asarray(Image.open(__import__("io").BytesIO(r.stdout)).convert("RGB"))


def main():
    import csv
    rows = list(csv.DictReader(open(MAP_CSV)))
    if len(rows) != 11:
        fail(f"map must have 11 rows, got {len(rows)}")

    for row in rows:
        vid = row["variant_id"]
        mp4 = VIDEO_DIR / f"{vid}_hud.mp4"
        if not mp4.exists():
            fail(f"missing {mp4}")

        probe = ffprobe(mp4)
        stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
        if (stream["width"], stream["height"]) != (1280, 704):
            fail(f"{vid}: resolution {stream['width']}x{stream['height']}")
        duration = float(probe["format"]["duration"])
        if not 19.5 <= duration <= 20.1:
            fail(f"{vid}: duration {duration:.2f}s outside [19.5,20.1]")
        n_frames = int(stream["nb_frames"]) if "nb_frames" in stream else int(duration * 30)
        if n_frames < 580:
            fail(f"{vid}: only {n_frames} frames")

        for t_s in (1.0, 5.0, 10.0, 19.0):
            arr = extract_frame(mp4, min(t_s, duration - 0.1))
            if arr.std() <= 10:
                fail(f"{vid}: black frame at t={t_s}")
            top = arr[:54].mean()
            bottom = arr[-58:].mean()
            middle = arr[120:600].mean()
            if top >= middle:
                fail(f"{vid}: top HUD bar missing at t={t_s} ({top:.0f}>={middle:.0f})")
            if bottom >= middle:
                fail(f"{vid}: bottom HUD bar missing at t={t_s}")

        print(f"ok {vid}: 1280x704 {duration:.2f}s {n_frames} frames")

    print("[SUCCESS] Stage2 Task 4.3: 11 HUD videos encoded and verified.")


if __name__ == "__main__":
    main()
