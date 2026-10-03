#!/usr/bin/env python3
"""Stage 2: generate an RGBA actor cutout for first-frame anchoring.

Text-to-image call to the Ark image model (same credentials as the frame
editor) renders the actor on a flat pure-green chroma-key background; the
green is then keyed out locally so the result is an RGBA PNG with a baked
contact shadow. Assets are cached under the Stage 2 anchor directory on
/mnt (STAGE2_ANCHOR_PATH, default /mnt/artifacts/stage2/anchor).

Run inside the flashdreams project env:

  cd repos/flashdreams && uv run --package flashdreams-omnidreams python \\
    ../../scripts/stage2_make_cutout.py --name pedestrian_stroller \\
    --prompt-file ../../configs/stage2/cutout_prompts/pedestrian_stroller.txt
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

_GEN_URL = "https://ark.cn-beijing.volces.com/api/v3/images/generations"
_DEFAULT_CREDENTIALS = str(Path.home() / "access/image_editor_model.txt")
_TIMEOUT_S = 180
_ATTEMPTS = 2


def read_credentials(path: str) -> tuple[str, str]:
    text = Path(path).read_text()
    key = re.search(r"ARK_API_KEY:\s*(\S+)", text).group(1)
    model = re.search(r"模型：(\S+)", text).group(1)
    return model, key


def generate(model: str, key: str, prompt: str) -> bytes:
    body = {
        "model": model,
        "prompt": prompt,
        "size": "2K",
        "response_format": "b64_json",
        "watermark": False,
    }
    req = urllib.request.Request(
        _GEN_URL,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    last_error: Exception | None = None
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as response:
                payload = json.loads(response.read())
            return base64.b64decode(payload["data"][0]["b64_json"])
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
            last_error = exc
            print(f"generate attempt {attempt}/{_ATTEMPTS} failed: {exc}")
    raise RuntimeError(f"image generation failed after {_ATTEMPTS}: {last_error}")


def _flood_background(
    arr: np.ndarray, dist: np.ndarray, bg_color: np.ndarray
) -> np.ndarray:
    """Boolean mask of background reachable from the image border.

    Pixels confidently unlike the backdrop are pre-marked as mask barriers
    (value 1); the flood therefore cannot leak through a soft anti-aliased
    silhouette into the subject. The flood writes value 255, so the two are
    distinguishable afterwards.
    """
    import cv2

    h, w = arr.shape[:2]
    mask = np.zeros((h + 2, w + 2), np.uint8)
    mask[1:-1, 1:-1][dist > 55.0] = 1  # flood barriers

    work = arr.astype(np.uint8).copy()
    flags = 4 | cv2.FLOODFILL_MASK_ONLY | (255 << 8)
    diff = (32, 32, 32)
    step = 14
    for x in range(0, w, step):
        for y in (0, h - 1):
            if mask[y + 1, x + 1] == 0:
                cv2.floodFill(work, mask, (x, y), 0, diff, diff, flags)
    for y in range(0, h, step):
        for x in (0, w - 1):
            if mask[y + 1, x + 1] == 0:
                cv2.floodFill(work, mask, (x, y), 0, diff, diff, flags)
    return mask[1:-1, 1:-1] == 255


def birefnet_cutout(
    data: bytes, model_name: str = "ZhengPeng7/BiRefNet"
) -> Image.Image:
    """Learned foreground matting via BiRefNet (torch/transformers only).

    Avoids the chroma-key failure modes (teal backdrop, magenta fringe,
    halo); the network emits a soft alpha for the subject and excludes the
    painted ground shadow, which is replaced by a procedural contact shadow
    at paste time.
    """
    import io

    import torch
    import torch.nn.functional as F
    from torchvision import transforms
    from transformers import AutoModelForImageSegmentation

    img = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = img.size
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = AutoModelForImageSegmentation.from_pretrained(
        model_name, trust_remote_code=True
    ).to(device).eval()
    model_dtype = next(model.parameters()).dtype
    tfm = transforms.Compose([
        transforms.Resize((1024, 1024)),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ])
    inp = tfm(img).unsqueeze(0).to(device, dtype=model_dtype)
    with torch.no_grad():
        preds = model(inp)[-1].sigmoid().float()
    alpha = F.interpolate(
        preds, size=(h, w), mode="bilinear", align_corners=False
    )[0, 0].cpu().numpy()
    alpha = np.clip(alpha * 255.0, 0, 255).astype(np.uint8)
    # De-spill green backdrop contamination on partial-coverage edges so the
    # cutout carries no green outline over the (dark) road background.
    rgb = np.asarray(img).astype(np.float32).copy()
    edge = (alpha > 8) & (alpha < 235)
    spill = edge & (
        rgb[..., 1] > np.maximum(rgb[..., 0], rgb[..., 2]) + 10.0
    )
    rgb[spill, 1] = np.maximum(rgb[spill, 0], rgb[spill, 2])
    rgba = np.dstack([rgb.astype(np.uint8), alpha])
    cut = Image.fromarray(rgba, "RGBA")

    ys, xs = np.where(alpha > 8)
    cut = cut.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
    out_alpha = cut.split()[3].filter(ImageFilter.GaussianBlur(0.6))
    cut.putalpha(out_alpha)
    return cut


def chroma_key_background(data: bytes) -> Image.Image:
    """Key out the backdrop; alpha ramp + edge color decontamination."""
    import io

    img = Image.open(io.BytesIO(data)).convert("RGB")
    arr = np.asarray(img).astype(np.float32)

    border = np.concatenate([
        arr[:10].reshape(-1, 3), arr[-10:].reshape(-1, 3),
        arr[:, :10].reshape(-1, 3), arr[:, -10:].reshape(-1, 3),
    ])
    bg_color = np.median(border, axis=0)
    dist = np.linalg.norm(arr - bg_color, axis=2)

    flooded = _flood_background(arr, dist, bg_color)
    enclosed_bg = dist < 20.0  # enclosed backdrop pockets (stroller gaps)

    # Bright green fringe/floor patches: the model outlines silhouettes with
    # pure green even when the rendered backdrop itself came out teal. Green
    # excess over both other channels is background coverage.
    r0, g0, b0 = arr[..., 0], arr[..., 1], arr[..., 2]
    g_excess = np.minimum(g0 - r0, g0 - b0)
    fringe_cov = np.clip((g_excess - 35.0) / 110.0, 0, 1)

    ramp = np.clip((dist - 20.0) / 35.0, 0, 1)
    alpha = np.where(flooded | enclosed_bg, 0.0, ramp * 255.0)
    alpha = alpha * (1.0 - fringe_cov * 0.95)

    # De-spill the green fringe in color space before the coverage pull.
    despilled = arr.copy()
    spill_mask = g_excess > 30.0
    despilled[..., 1] = np.where(
        spill_mask, np.maximum(r0, b0), g0)

    # Un-mult decontamination: pull partial-coverage edge colors away from
    # the backdrop so the cutout does not carry a teal halo over new bgs.
    a = np.clip(alpha / 255.0, 1e-3, 1.0)[..., None]
    fg = bg_color + (despilled - bg_color) / a
    fg = np.where(alpha[..., None] > 0, np.clip(fg, 0, 255), despilled)

    rgba = np.dstack([fg, alpha]).astype(np.uint8)
    cut = Image.fromarray(rgba, "RGBA")

    # Autocrop to the covered region, then feather the edge halo.
    ys, xs = np.where(alpha > 8)
    cut = cut.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
    out_alpha = cut.split()[3].filter(ImageFilter.GaussianBlur(0.7))
    cut.putalpha(out_alpha)
    return cut


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--prompt", default=None)
    ap.add_argument("--prompt-file", default=None)
    ap.add_argument(
        "--credentials",
        default=os.environ.get("STAGE2_EDITOR_CREDENTIALS_PATH", _DEFAULT_CREDENTIALS),
    )
    ap.add_argument(
        "--anchor-dir",
        default=os.environ.get(
            "STAGE2_ANCHOR_PATH", "/mnt/artifacts/stage2/anchor"),
    )
    ap.add_argument(
        "--matting",
        choices=["birefnet", "chroma"],
        default="birefnet",
    )
    args = ap.parse_args()

    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text()
    else:
        prompt = args.prompt
    if not prompt:
        raise SystemExit("one of --prompt/--prompt-file is required")

    assets_dir = Path(args.anchor_dir) / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    out_path = assets_dir / f"{args.name}.png"

    model, key = read_credentials(args.credentials)
    raw = generate(model, key, prompt)
    if args.matting == "chroma":
        cut = chroma_key_background(raw)
    else:
        cut = birefnet_cutout(raw)
    if out_path.exists():
        bak = out_path.with_suffix(".png.prev")
        out_path.rename(bak)
        print(f"previous asset backed up to {bak}")
    cut.save(out_path)
    print(f"cutout {cut.size} -> {out_path}")


if __name__ == "__main__":
    main()
