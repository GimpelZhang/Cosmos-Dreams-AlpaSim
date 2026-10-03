#!/usr/bin/env python3
"""Stage 2 diagnostic: why are injected actors visible in BEV but not RGB?

The ASL records the exact gRPC traffic the runtime sent to OmniDreams. This
tool replays it against a live renderer with controlled modifications so the
ghostly actor pixels can be attributed. One variant per invocation (default
v05 stroller), shared seed and identical initial frames.

Modes (dependency-tolerant imports; run each with the matching env):

  extract   dump SessionRequest / VideoChunkRequest wire bytes + s2 bbox list
            and the baseline prompt from variants.yaml.
            run: repos/alpasim/.venv/bin/python

  condition skip video generation, return hdmap condition frames; two passes
            (s2 wireframe cubes on / off) to see exactly what the model gets.
            run: cd repos/flashdreams && uv run --package flashdreams-omnidreams

  replay    real generation, four serial arms:
              full     = recorded request verbatim
              noCube   = s2 actors stripped from dynamic_state
              basePrompt = s2 actors kept, session prompt replaced by v00 prompt
              neither  = s2 actors stripped + v00 prompt
  analyze   per-frame mean abs diff vs arm "neither" + comparison montage.
            run: repos/alpasim/.venv/bin/python

All outputs go to /mnt (STAGE2_ACTOR_AB_DIR, default
/mnt/artifacts/stage2/diag/actor_ab).
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path

DEFAULT_DIR = Path(os.environ.get(
    "STAGE2_ACTOR_AB_DIR", "/mnt/artifacts/stage2/diag/actor_ab"))
VARIANTS_YAML = Path(os.environ.get(
    "STAGE2_VARIANTS", "/home/vipuser/simulation/configs/stage2/variants.yaml"))
ASL_BY_VID = {
    "v05_stroller_jaywalking":
        "/mnt/artifacts/stage2/run_fixfull_20261002/v05_stroller_jaywalking/"
        "rollouts/clipgt-02eadd92-02f1-46d8-86fe-a9e338fed0b6/"
        "f123930c-be6c-11f1-80fa-d355703b093b/rollout.asl",
}


# ---------------------------------------------------------------- extract

def run_extract(out_dir: Path, variant_id: str) -> None:
    import asyncio
    import yaml
    from alpasim_utils.logs import async_read_pb_log

    asl_path = ASL_BY_VID[variant_id]
    spec = yaml.safe_load(VARIANTS_YAML.read_text())
    variant = next(v for v in spec["variants"] if v["id"] == variant_id)
    v00 = next(v for v in spec["variants"] if v["id"] == "v00_baseline")
    s2_bboxes = [tuple(float(x) for x in a["box_size_xyz"])
                 for a in variant.get("actors", [])]

    chunks_dir = out_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    async def _read():
        n_chunks = 0
        async for le in async_read_pb_log(asl_path):
            which = le.WhichOneof("log_entry")
            if which == "video_model_session_request":
                (out_dir / "session_request.pb").write_bytes(
                    le.video_model_session_request.SerializeToString())
            elif which == "video_model_chunk_request":
                (chunks_dir / f"{n_chunks:03d}.pb").write_bytes(
                    le.video_model_chunk_request.SerializeToString())
                n_chunks += 1
        return n_chunks

    n_chunks = asyncio.run(_read())
    (out_dir / "manifest.json").write_text(json.dumps({
        "variant_id": variant_id,
        "n_chunks": n_chunks,
        "s2_bboxes": s2_bboxes,
    }, indent=2))
    (out_dir / "baseline_prompt.json").write_text(json.dumps({
        "positive": v00["prompt_positive"],
        "negative": v00.get("prompt_negative", ""),
    }, ensure_ascii=False))
    print(f"extracted 1 session + {n_chunks} chunks; s2_bboxes={s2_bboxes}")


# ---------------------------------------------------------------- replay core

def _load_protos():
    from omnidreams.impl.grpc.protos import (
        common_pb2, video_model_pb2, video_model_pb2_grpc)
    return common_pb2, video_model_pb2, video_model_pb2_grpc


def _load_requests(out_dir: Path, n_chunks: int):
    _, video_model_pb2, _ = _load_protos()
    session = video_model_pb2.SessionRequest()
    session.ParseFromString((out_dir / "session_request.pb").read_bytes())
    chunks = []
    for i in range(n_chunks):
        req = video_model_pb2.VideoChunkRequest()
        req.ParseFromString((out_dir / "chunks" / f"{i:03d}.pb").read_bytes())
        chunks.append(req)
    return session, chunks


def _is_s2_actor(actor, s2_bboxes: list[tuple[float, float, float]]) -> bool:
    b = (actor.bbox_dims.size_x, actor.bbox_dims.size_y, actor.bbox_dims.size_z)
    return any(all(abs(b[j] - t[j]) < 1e-6 for j in range(3)) for t in s2_bboxes)


def _strip_s2(chunk, video_model_pb2, s2_bboxes):
    kept = [a for a in chunk.dynamic_state.actors
            if not _is_s2_actor(a, s2_bboxes)]
    del chunk.dynamic_state.actors[:]
    chunk.dynamic_state.actors.extend(kept)


def _connect(server: str):
    import grpc
    _, video_model_pb2, video_model_pb2_grpc = _load_protos()
    channel = grpc.insecure_channel(
        server,
        options=[("grpc.max_send_message_length", 200 * 1024 * 1024),
                 ("grpc.max_receive_message_length", 200 * 1024 * 1024)])
    stub = video_model_pb2_grpc.WorldModelServiceStub(channel)
    return channel, stub, video_model_pb2


def _run_arm(
    stub, video_model_pb2, *,
    session, chunks, arm: str, out_dir: Path,
    s2_bboxes, baseline_prompt, condition_only: bool,
):
    """One session; replay every chunk; save returned frames."""
    if arm in ("basePrompt", "neither"):
        session.text_prompt.positive = baseline_prompt["positive"]
        session.text_prompt.negative = baseline_prompt["negative"]
    if condition_only:
        session.debug_options.skip_video_generation = True
        session.debug_options.return_hdmap_frames = True

    sid = stub.start_session(session, timeout=300).session_id
    arm_dir = out_dir / (("cond_" + arm) if condition_only else arm)
    arm_dir.mkdir(parents=True, exist_ok=True)
    try:
        for i, chunk0 in enumerate(chunks):
            chunk = video_model_pb2.VideoChunkRequest()
            chunk.CopyFrom(chunk0)
            chunk.session_id.session_id = sid
            if arm in ("noCube", "neither"):
                _strip_s2(chunk, video_model_pb2, s2_bboxes)
            resp = stub.render_video_chunk(chunk, timeout=600)
            out = resp.camera_outputs[0]
            if condition_only:
                for j, img in enumerate(out.hdmap_condition_frames):
                    (arm_dir / f"{i:03d}_{j}.bin").write_bytes(img.data)
            else:
                for j, img in enumerate(out.rgb_frames):
                    frame_idx = (0 if i == 0 else 5 + (i - 1) * 8) + j + 1
                    (arm_dir / f"{frame_idx:04d}.jpg").write_bytes(img.data)
    finally:
        stub.close_session(
            video_model_pb2.SessionCloseRequest(session_id=sid), timeout=60)
    print(f"arm {arm} done -> {arm_dir}")


def _parse_args_common(ap):
    ap.add_argument("--server", default="127.0.0.1:50051")
    ap.add_argument("--variant", default="v05_stroller_jaywalking")
    ap.add_argument("--out-dir", default=str(DEFAULT_DIR))


def run_condition(args) -> None:
    out_dir = Path(args.out_dir)
    manifest = json.loads((out_dir / "manifest.json").read_text())
    session, chunks = _load_requests(out_dir, manifest["n_chunks"])
    channel, stub, video_model_pb2 = _connect(args.server)
    try:
        # Both condition arms reuse one session spec: neither changes the
        # prompt, and the debug flags are the same for both.
        for arm in ("full", "noCube"):
            _run_arm(
                stub, video_model_pb2,
                session=session, chunks=chunks, arm=arm, out_dir=out_dir,
                s2_bboxes=[tuple(x) for x in manifest["s2_bboxes"]],
                baseline_prompt=None, condition_only=True)
    finally:
        channel.close()


def run_replay(args) -> None:
    out_dir = Path(args.out_dir)
    manifest = json.loads((out_dir / "manifest.json").read_text())
    session, chunks = _load_requests(out_dir, manifest["n_chunks"])
    baseline_prompt = json.loads((out_dir / "baseline_prompt.json").read_text())
    s2_bboxes = [tuple(x) for x in manifest["s2_bboxes"]]
    channel, stub, video_model_pb2 = _connect(args.server)
    try:
        for arm in ("full", "noCube", "basePrompt", "neither"):
            sess = video_model_pb2.SessionRequest()
            sess.CopyFrom(session)
            _run_arm(
                stub, video_model_pb2,
                session=sess, chunks=chunks, arm=arm, out_dir=out_dir,
                s2_bboxes=s2_bboxes, baseline_prompt=baseline_prompt,
                condition_only=False)
    finally:
        channel.close()


# ---------------------------------------------------------------- analyze

def run_analyze(args) -> None:
    import numpy as np
    from PIL import Image

    out_dir = Path(args.out_dir)
    arms = ["full", "noCube", "basePrompt", "neither"]
    ref_files = sorted((out_dir / "neither").glob("*.jpg"))
    curves = {a: [] for a in arms}
    for rf in ref_files:
        ref = np.asarray(Image.open(rf).convert("RGB"), dtype=np.float32)
        for a in arms:
            p = out_dir / a / rf.name
            if not p.exists():
                curves[a].append(np.nan); continue
            arr = np.asarray(Image.open(p).convert("RGB"), dtype=np.float32)
            curves[a].append(float(np.abs(arr - ref).mean()))

    lines = ["frame," + ",".join(arms)]
    for i, rf in enumerate(ref_files):
        lines.append(rf.stem + "," + ",".join(
            "" if np.isnan(curves[a][i]) else f"{curves[a][i]:.3f}" for a in arms))
    (out_dir / "diff_curves.csv").write_text("\n".join(lines))

    print("mean |frame - neither| over frames 90-180:")
    i0, i1 = 89, 180
    for a in arms:
        seg = np.array(curves[a][i0:min(i1, len(curves[a]))])
        print(f"  {a:11s} {np.nanmean(seg):.3f}")

    frames = [100, 130, 156, 170]
    W, H = 640, 352
    canvas = Image.new("RGB", (W * 4, H * len(frames)), (0, 0, 0))
    for r, f in enumerate(frames):
        for c, a in enumerate(arms):
            p = out_dir / a / f"{f:04d}.jpg"
            if p.exists():
                canvas.paste(Image.open(p).convert("RGB").resize((W, H)),
                             (c * W, r * H))
    canvas.save(out_dir / "ab_montage.png")
    print(f"montage -> {out_dir / 'ab_montage.png'}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="mode", required=True)
    ap_e = sub.add_parser("extract"); _parse_args_common(ap_e)
    ap_c = sub.add_parser("condition"); _parse_args_common(ap_c)
    ap_r = sub.add_parser("replay"); _parse_args_common(ap_r)
    ap_a = sub.add_parser("analyze"); _parse_args_common(ap_a)
    args = ap.parse_args()

    if args.mode == "extract":
        run_extract(Path(args.out_dir), args.variant)
    elif args.mode == "condition":
        run_condition(args)
    elif args.mode == "replay":
        run_replay(args)
    else:
        run_analyze(args)


if __name__ == "__main__":
    main()
