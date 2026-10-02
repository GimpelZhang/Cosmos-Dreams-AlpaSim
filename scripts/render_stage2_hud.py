#!/usr/bin/env python3
"""Stage 2 Task 4.3: compose HUD overlay videos (plan §7.5).

Per camera frame: top bar (variant id, time, prompt snippet), 260x260 BEV
picture-in-picture top-right, bottom bar with SPEED / STEER / OBS DIST and
colored STATE. SPEED comes from ego dynamics (controller_return states),
STEER is a derived bicycle-model steering angle (wheelbase 2.8 m), OBS DIST
and collision/offroad flags come from the per-frame metrics.parquet.

Frames render as JPEGs in a tmp dir on /mnt then ffmpeg encodes H.264
yuv420p crf20 30fps.
"""
from __future__ import annotations

import argparse
import csv
import io
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stage2_asl_replay import load_replay

W, H = 1280, 704
TOP_H = 54
BOT_H = 58
BEV_X = W - 260 - 20
BEV_Y = 62
WHEELBASE = 2.8

FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

NORMAL = (70, 200, 120)
CAUTION = (255, 170, 40)
EMERGENCY = (240, 70, 70)
WHITE = (235, 235, 235)


def load_metrics(asl_path: Path):
    df = pd.read_parquet(asl_path.parent / "metrics.parquet")
    df["values"] = pd.to_numeric(df["values"], errors="coerce")
    series = {}
    for name, grp in df.groupby("name"):
        grp = grp.groupby("timestamps_us", as_index=False)["values"].max()
        grp = grp.sort_values("timestamps_us")
        series[name] = (grp["timestamps_us"].to_numpy(), grp["values"].to_numpy())
    return series


def metric_at(series, name, ts):
    if name not in series:
        return None
    mts, vals = series[name]
    i = int(np.abs(mts - ts).argmin())
    return vals[i]


def overlay_bar(img, box, alpha=0.65):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rectangle(box, fill=(10, 10, 10, int(255 * alpha)))
    img.alpha_composite(layer)


def ascii_only(text: str) -> str:
    return text.encode("ascii", "replace").decode("ascii")[:90]


def render_variant(variant_id: str, asl_path: str, variants_spec: str,
                   bev_dir: Path, tmp_dir: Path) -> int:
    asl_path = Path(asl_path)
    replay = load_replay(asl_path, variants_spec)
    metrics = load_metrics(asl_path)

    font_bold = ImageFont.truetype(FONT_BOLD, 22)
    font_reg = ImageFont.truetype(FONT_REG, 18)
    font_small = ImageFont.truetype(FONT_REG, 15)
    font_state = ImageFont.truetype(FONT_BOLD, 20)

    out_tmp = tmp_dir
    out_tmp.mkdir(parents=True, exist_ok=True)

    bev_pngs = sorted((bev_dir).glob("*.png"))

    for idx, (ts, _logical_id, jpeg_bytes) in enumerate(replay.frames):
        frame = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB").convert("RGBA")

        overlay_bar(frame, (0, 0, W, TOP_H))
        overlay_bar(frame, (0, H - BOT_H, W, H))
        draw = ImageDraw.Draw(frame)

        # Top bar.
        draw.text((16, 6), f"STAGE2 | {variant_id}", font=font_bold, fill=WHITE)
        t_s = idx / 30
        ttext = f"t={t_s:5.1f}s"
        tw = draw.textlength(ttext, font=font_bold)
        draw.text((W - tw - 16, 6), ttext, font=font_bold, fill=WHITE)
        draw.text((16, 32), ascii_only(replay.prompt_positive), font=font_small,
                  fill=(200, 200, 200))

        # BEV picture-in-picture.
        if idx < len(bev_pngs):
            bev = Image.open(bev_pngs[idx]).convert("RGBA")
            frame.alpha_composite(bev, (BEV_X, BEV_Y))
            draw.rectangle((BEV_X - 2, BEV_Y - 2, BEV_X + 260 + 1, BEV_Y + 260 + 1),
                           outline=(255, 255, 255), width=2)
            label = "BEV (AlpaSim)"
            draw.text((BEV_X + 6, BEV_Y + 4), label, font=font_small,
                      fill=(20, 20, 20),
                      stroke_width=2, stroke_fill=(255, 255, 255))

        # Bottom bar.
        ego = replay.ego_at(ts)
        speed_kmh = math.hypot(ego.vx, ego.vy) * 3.6
        if abs(ego.vx) > 0.5:
            steer_deg = math.degrees(math.atan(WHEELBASE * ego.yaw_rate / ego.vx))
        else:
            steer_deg = 0.0

        obs = metric_at(metrics, "min_distance_to_obstacle_m", ts)
        collision = metric_at(metrics, "collision_any", ts)
        offroad = metric_at(metrics, "offroad", ts)
        obs_text = f"{obs:5.1f}" if obs is not None and not math.isnan(obs) else "  -- "

        if (collision and collision > 0) or (obs is not None and obs < 5):
            state, state_name = EMERGENCY, "EMERGENCY"
        elif obs is not None and obs < 12:
            state, state_name = CAUTION, "CAUTION"
        else:
            state, state_name = NORMAL, "NORMAL"

        line = (f"SPEED {speed_kmh:5.1f} km/h    STEER {steer_deg:+5.1f} deg    "
                f"OBS DIST{obs_text} m")
        draw.text((16, H - BOT_H + 17), line, font=font_reg, fill=WHITE)
        sw = draw.textlength(state_name, font=font_state)
        draw.text((W - sw - 24, H - BOT_H + 16), state_name, font=font_state, fill=state)

        frame.convert("RGB").save(out_tmp / f"{idx + 1:06d}.jpg", quality=90)

    return len(replay.frames)


def encode(out_tmp: Path, out_mp4: Path):
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-framerate", "30", "-i", str(out_tmp / "%06d.jpg"),
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20",
           "-preset", "medium", str(out_mp4)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{r.stderr[-2000:]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", required=True)
    ap.add_argument("--variants", required=True)
    ap.add_argument("--bev-dir", default="/mnt/artifacts/stage2/bev")
    ap.add_argument("--tmp-dir", default="/mnt/artifacts/stage2/hud_tmp")
    ap.add_argument("--out-dir", default="/mnt/artifacts/stage2/videos")
    args = ap.parse_args()

    for row in csv.DictReader(open(args.map)):
        vid = row["variant_id"]
        tmp = Path(args.tmp_dir) / vid
        n = render_variant(vid, row["asl_path"], args.variants,
                           Path(args.bev_dir) / vid, tmp)
        out_mp4 = Path(args.out_dir) / f"{vid}_hud.mp4"
        encode(tmp, out_mp4)
        print(f"{vid}: {n} frames -> {out_mp4}")


if __name__ == "__main__":
    main()
