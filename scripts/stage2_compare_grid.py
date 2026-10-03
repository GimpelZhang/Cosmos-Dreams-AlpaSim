#!/usr/bin/env python3
"""Stage 2 visualization: tile the v00 baseline + v05-v10 front-camera
streams into a single 3x3 comparison video (30 fps, 589 frames).

Tiles whose valid window (per stage2_v05v10_valid_windows.csv) has ended are
desaturated and tagged "render drift / post valid window" so the known
OmniDreams failure regime is visually separated from valid content.

Frames are read from the exported per-variant jpgs produced by
export_stage2_frames.sh (camera_front_wide_120fov stream, 589 jpgs each).
"""
from __future__ import annotations

import argparse
import csv
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# Grid order (3x3); trailing two cells stay blank.
ORDER = [
    "v00_baseline",
    "v05_stroller_jaywalking",
    "v06_wheelchair_shoulder",
    "v07_construction_excavator",
    "v08_loose_cow",
    "v09_construction_debris",
    "v10_typhoon_compound",
]

COLS, ROWS = 3, 3
TW, TH = 426, 234          # tile image size (1280x704 at scale 1/3)
LABEL_H = 30
CW, CH = TW, TH + LABEL_H  # cell size
GAP = 6
MARGIN = 10
TITLE_H = 44

CANVAS_W = MARGIN * 2 + COLS * CW + (COLS - 1) * GAP
CANVAS_H = MARGIN + TITLE_H + ROWS * CH + (ROWS - 1) * GAP + MARGIN

FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

BG = (12, 12, 14)
CELL_BG = (28, 28, 32)
WHITE = (235, 235, 235)
GREY = (170, 170, 170)
AMBER = (255, 170, 40)


def find_stream(variant_frames_dir: Path) -> Path:
    cams = sorted(variant_frames_dir.glob(
        "*/*/rollout/camera_front_wide_120fov"))
    if not cams:
        raise FileNotFoundError(
            f"no camera_front_wide_120fov stream under {variant_frames_dir}")
    return cams[-1]


def load_windows(path: Path) -> dict[str, int]:
    out = {}
    for row in csv.DictReader(open(path)):
        out[row["variant_id"]] = int(row["valid_end_frame"])
    return out


def short_label(vid: str) -> str:
    parts = vid.split("_", 1)
    return f"{parts[0]}  {parts[1].replace('_', ' ')}" if len(parts) > 1 else vid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames-dir", required=True,
                    help="exported frames root with <variant>/.../rollout/<cam>")
    ap.add_argument("--windows", required=True,
                    help="stage2_v05v10_valid_windows.csv")
    ap.add_argument("--tmp-dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    frames_dir = Path(args.frames_dir)
    tmp_dir = Path(args.tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    windows = load_windows(Path(args.windows))

    streams = {v: find_stream(frames_dir / v) for v in ORDER}
    flists = {v: sorted(streams[v].glob("*.jpg")) for v in ORDER}
    n = min(len(flists[v]) for v in ORDER)
    print(f"rendering {n} frames for {len(ORDER)} tiles")

    font_bold = ImageFont.truetype(FONT_BOLD, 18)
    font_title = ImageFont.truetype(FONT_BOLD, 24)
    font_small = ImageFont.truetype(FONT_REG, 15)
    font_tag = ImageFont.truetype(FONT_BOLD, 15)

    for i in range(n):
        canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), BG)
        draw = ImageDraw.Draw(canvas)
        draw.text((MARGIN + 2, MARGIN + 6),
                  "Stage 2 counterfactual variants — front camera "
                  f"(t={i / 30:5.2f}s, seed 20261002)",
                  font=font_title, fill=WHITE)

        for k, vid in enumerate(ORDER):
            r, c = divmod(k, COLS)
            x0 = MARGIN + c * (CW + GAP)
            y0 = MARGIN + TITLE_H + r * (CH + GAP)
            draw.rectangle((x0, y0, x0 + CW, y0 + CH), fill=CELL_BG)

            tile = Image.open(flists[vid][i]).convert("RGB").resize(
                (TW, TH), Image.LANCZOS)

            post = i > windows.get(vid, n)
            if post:
                grey = tile.convert("L").convert("RGB")
                tile = Image.blend(tile, grey, 0.75)

            canvas.paste(tile, (x0, y0 + LABEL_H))
            label = short_label(vid)
            draw.text((x0 + 6, y0 + 5), label, font=font_bold, fill=WHITE)

            if post:
                tag = "render drift"
                tw = draw.textlength(tag, font=font_tag)
                draw.text((x0 + CW - tw - 8, y0 + 6), tag,
                          font=font_tag, fill=AMBER)

            # time + activation marker on the image
            ttext = f"t={i / 30:4.1f}s"
            draw.rectangle((x0 + 4, y0 + LABEL_H + 4,
                            x0 + 70, y0 + LABEL_H + 22), fill=(0, 0, 0))
            draw.text((x0 + 8, y0 + LABEL_H + 6), ttext,
                      font=font_small, fill=WHITE)

        canvas.save(tmp_dir / f"{i + 1:06d}.jpg", quality=88)
        if (i + 1) % 100 == 0:
            print(f"  {i + 1}/{n}")

    cmd = ["ffmpeg", "-y", "-framerate", "30",
           "-i", str(tmp_dir / "%06d.jpg"),
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20",
           "-preset", "medium", args.out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{r.stderr[-2000:]}")
    print(f"wrote {args.out} ({CANVAS_W}x{CANVAS_H})")


if __name__ == "__main__":
    main()
