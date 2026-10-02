#!/usr/bin/env python3
"""Stage 2 diagnostic: is the deployed renderer sensitive to per-session text prompts?

Controlled A/B: identical initial frame, hdmap, rig trajectory and random
seed; only the text prompt differs (prompts taken verbatim from
configs/stage2/variants.yaml). Each session renders the same straight
constant-speed trajectory; returned JPEGs are saved and compared across
sessions (md5 equality, brightness, near-white fraction).

Run inside the flashdreams project env so the omnidreams protos resolve:

  cd repos/flashdreams && uv run --package flashdreams-omnidreams python \\
    <...>/scripts/diag_stage2_prompt_ab.py \\
    --variants <...>/configs/stage2/variants.yaml
"""
from __future__ import annotations



import argparse
import hashlib
import io
import json
from pathlib import Path

import grpc
import numpy as np
import yaml
from omnidreams.impl.grpc.protos import (
    common_pb2,
    video_model_pb2,
    video_model_pb2_grpc,
)
from PIL import Image

T0_US = 1_000_000
FRAME_INTERVAL_US = 33_333
CAMERA = "camera_front_wide_120fov"
FIRST_CHUNK = 5
CHUNK = 8
SPEED_MPS = 9.0


def build_camera_spec():
    from omnidreams.impl.grpc.protos import camera_pb2

    focal = 1280 / (2.0 * np.deg2rad(60.0))
    return camera_pb2.CameraSpec(
        logical_id=CAMERA,
        resolution_h=704,
        resolution_w=1280,
        shutter_type=camera_pb2.ShutterType.GLOBAL,
        ftheta_param=camera_pb2.FthetaCameraParam(
            principal_point_x=640,
            principal_point_y=352,
            reference_poly=camera_pb2.FthetaCameraParam.ANGLE_TO_PIXELDIST,
            angle_to_pixeldist_poly=[focal],
            linear_cde=camera_pb2.LinearCde(linear_c=1.0, linear_d=0.0, linear_e=0.0),
        ),
    )


def trajectory(indices: list[int]):
    poses = [
        common_pb2.PoseAtTime(
            timestamp_us=T0_US + i * FRAME_INTERVAL_US,
            pose=common_pb2.Pose(
                vec=common_pb2.Vec3(x=SPEED_MPS * i / 30.0, y=0.0, z=0.0),
                quat=common_pb2.Quat(w=1.0),
            ),
        )
        for i in indices
    ]
    return common_pb2.Trajectory(poses=poses)


def run_session(stub, variant, hdmap: bytes, frame0: bytes, seed: int,
                total_frames: int, out_dir: Path):
    sid = stub.start_session(
        video_model_pb2.SessionRequest(
            static_world_map=video_model_pb2.StaticWorldMap(hdmap_parquets=hdmap),
            text_prompt=video_model_pb2.TextPrompt(
                positive=variant["prompt_positive"],
                negative=variant.get("prompt_negative", ""),
            ),
            camera_specs=[build_camera_spec()],
            initial_frames=[
                video_model_pb2.Image(data=frame0, format=video_model_pb2.ImageFormat.JPEG)
            ],
            rig_to_camera=[common_pb2.Pose(quat=common_pb2.Quat(w=1.0))],
            random_seed=seed,
        ),
        timeout=300,
    ).session_id

    jpegs: list[bytes] = []
    next_idx = 0
    while len(jpegs) < total_frames:
        n = FIRST_CHUNK if next_idx == 0 else CHUNK
        indices = list(range(next_idx, next_idx + n))
        resp = stub.render_video_chunk(
            video_model_pb2.VideoChunkRequest(
                session_id=video_model_pb2.SessionId(session_id=sid),
                rig_trajectory=trajectory(indices),
                dynamic_state=video_model_pb2.DynamicWorldState(),
            ),
            timeout=600,
        )
        frames = resp.camera_outputs[0].rgb_frames
        jpegs.extend(f.data for f in frames)
        next_idx += n

    stub.close_session(
        video_model_pb2.SessionCloseRequest(session_id=sid), timeout=60)

    out_dir.mkdir(parents=True, exist_ok=True)
    stats = []
    for i, data in enumerate(jpegs):
        (out_dir / f"{i + 1:04d}.jpg").write_bytes(data)
        arr = np.array(Image.open(io.BytesIO(data)).convert("RGB"), dtype=np.float32)
        stats.append({
            "i": i,
            "md5": hashlib.md5(data).hexdigest(),
            "mean": float(arr.mean()),
            "white_frac": float((arr.min(axis=2) > 200).mean()),
        })
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="127.0.0.1:50051")
    ap.add_argument("--variants", required=True)
    ap.add_argument("--data-dir", default="/mnt/artifacts/stage2/diag")
    ap.add_argument("--out-dir", default="/mnt/artifacts/stage2/diag/frames")
    ap.add_argument("--seed", type=int, default=20261002)
    ap.add_argument("--duration-s", type=float, default=6.0)
    ap.add_argument("--ids", nargs="+",
                    default=["v00_baseline", "v01_heavy_snow_blizzard",
                             "v03_dense_fog_dawn"])
    args = ap.parse_args()

    doc = yaml.safe_load(Path(args.variants).read_text())
    variants = {v["id"]: v for v in doc["variants"]}
    hdmap = (Path(args.data_dir) / "hdmap.zip").read_bytes()
    frame0 = (Path(args.data_dir) / "frame0.jpg").read_bytes()
    total = int(args.duration_s * 30) + 1

    channel = grpc.insecure_channel(
        args.server,
        options=[("grpc.max_send_message_length", 100 * 1024 * 1024),
                 ("grpc.max_receive_message_length", 100 * 1024 * 1024)],
    )
    stub = video_model_pb2_grpc.WorldModelServiceStub(channel)

    all_stats = {}
    try:
        for vid in args.ids:
            print(f"== {vid}")
            all_stats[vid] = run_session(
                stub, variants[vid], hdmap, frame0, args.seed, total,
                Path(args.out_dir) / vid)
    finally:
        channel.close()

    # Pairwise comparison vs baseline.
    base = {s["i"]: s for s in all_stats[args.ids[0]]}
    summary = {}
    for vid in args.ids:
        same = sum(1 for s in all_stats[vid] if base[s["i"]]["md5"] == s["md5"])
        means = np.array([s["mean"] for s in all_stats[vid]])
        whites = np.array([s["white_frac"] for s in all_stats[vid]])
        summary[vid] = {
            "frames": len(all_stats[vid]),
            "md5_identical_to_baseline": same,
            "mean_brightness": float(means.mean()),
            "mean_white_frac_tail": float(whites[30:].mean()),
        }
        print(summary[vid])
    (Path(args.out_dir) / "summary.json").write_text(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
