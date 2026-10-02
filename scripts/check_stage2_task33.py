#!/usr/bin/env python3
"""Stage 2 Task 3.3 gate: 11 serial variant rollouts integrity.

Per plan Task 3.3:
- exactly 11 rollout dirs, each with rollout.asl (>100 MB), metrics.parquet,
  and a _success marker from run_stage2_separate.sh
- per-rollout frames 75 (+/-2); img_is_black all False
- ASL video_model_session_request: prompt verbatim from variants.yaml and
  random_seed = 20261002
- traffic_session_request object count = base clip objects + variant actors
  (EGO included); injected s2_ track ids present
- secret-pattern scan over runner logs; no missing/failed rollouts
- writes /mnt/artifacts/stage2/rollout_variant_map.csv
"""
import csv
import os
import re
import sys
from pathlib import Path

import pandas as pd
import yaml

SIM = Path("/home/vipuser/simulation")
PYTHONPATH_ROOT = SIM / "repos/alpasim"
RUN_DIR = Path(os.environ.get(
    "STAGE2_RUN_DIR", "/mnt/artifacts/stage2/run_stage2_20261002"))
VARIANTS = SIM / "configs/stage2/variants.yaml"
MANIFEST = SIM / "configs/stage2/manifests/stage2_variants.csv"
MAP_OUT = Path(os.environ.get(
    "STAGE2_MAP_OUT", "/mnt/artifacts/stage2/rollout_variant_map.csv"))
SEED = 20261002

SECRET_RE = re.compile(
    r"hf_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}"
    r"|nvapi-[A-Za-z0-9]{20,}|ark-[A-Za-z0-9-]{30,}")

sys.path.insert(0, str(PYTHONPATH_ROOT / "src/utils"))
sys.path.insert(0, str(PYTHONPATH_ROOT / "src/runtime"))


def fail(msg):
    print(f"[FAIL] {msg}")
    sys.exit(1)


async def scan_asl(asl_path):
    from alpasim_utils.logs import async_read_pb_log

    info = {
        "positive": None,
        "negative": None,
        "seed": None,
        "object_ids": [],
    }
    async for entry in async_read_pb_log(str(asl_path)):
        which = entry.WhichOneof("log_entry")
        if which == "video_model_session_request":
            req = entry.video_model_session_request
            info["positive"] = req.text_prompt.positive
            info["negative"] = req.text_prompt.negative
            info["seed"] = req.random_seed
        elif which == "traffic_session_request":
            req = entry.traffic_session_request
            info["object_ids"] = [
                o.object_id for o in req.logged_object_trajectories]
    return info


def check_metrics(metrics_path):
    df = pd.read_parquet(metrics_path)
    df["values"] = pd.to_numeric(df["values"], errors="coerce")
    df = df[df["timestamps_us"].notna()]
    n_frames = df["timestamps_us"].nunique()
    black = (df[df["name"] == "img_is_black"]
             .groupby("timestamps_us")["values"].max())
    any_black = bool((black > 0.5).any())
    return n_frames, any_black


def main():
    import asyncio

    spec_doc = yaml.safe_load(VARIANTS.read_text())
    variants = {v["id"]: v for v in spec_doc["variants"]}

    with MANIFEST.open() as f:
        rows = list(csv.DictReader(f))
    if len(rows) != 11:
        fail(f"manifest has {len(rows)} rows, expected 11")

    map_rows = []
    base_object_count = None

    for row in rows:
        vid = row["variant_id"]
        n_actors = int(row["n_actors"])
        vlog = RUN_DIR / vid
        print(f"--- {vid} ---")

        if not (vlog / "_success").exists():
            fail(f"{vid}: missing _success marker ({vlog})")

        asl_candidates = list(vlog.glob("rollouts/**/rollout.asl"))
        if len(asl_candidates) != 1:
            fail(f"{vid}: expected exactly 1 rollout.asl, found {len(asl_candidates)}")
        asl_path = asl_candidates[0]
        if asl_path.stat().st_size < 100 * 1024 * 1024:
            fail(f"{vid}: rollout.asl only {asl_path.stat().st_size} bytes")

        metrics_path = asl_path.parent / "metrics.parquet"
        if not metrics_path.exists():
            fail(f"{vid}: missing metrics.parquet")

        n_frames, any_black = check_metrics(metrics_path)
        if not 73 <= n_frames <= 77:
            fail(f"{vid}: {n_frames} frames outside 75 +/- 2")
        if any_black:
            fail(f"{vid}: img_is_black present")

        info = asyncio.run(scan_asl(asl_path))
        variant = variants[vid]
        if info["positive"] != variant["prompt_positive"]:
            fail(f"{vid}: session positive prompt mismatch")
        if info["negative"] != variant["prompt_negative"]:
            fail(f"{vid}: session negative prompt mismatch")
        if info["seed"] != SEED:
            fail(f"{vid}: renderer seed {info['seed']} != {SEED}")

        if vid == "v00_baseline":
            base_object_count = len(info["object_ids"])
            if "EGO" not in info["object_ids"]:
                fail(f"{vid}: EGO missing from traffic session")
        else:
            expected = base_object_count + n_actors
            if len(info["object_ids"]) != expected:
                fail(
                    f"{vid}: {len(info['object_ids'])} session objects, "
                    f"expected {expected} (base {base_object_count} + {n_actors})")
            for actor in variant.get("actors", []):
                tid = "s2_" + actor["id"]
                if tid not in info["object_ids"]:
                    fail(f"{vid}: injected track {tid} missing")

        rollout_uuid = asl_path.parent.name
        map_rows.append({
            "variant_id": vid,
            "scene_id": row["scene_id"],
            "rollout_uuid": rollout_uuid,
            "asl_path": str(asl_path),
            "n_frames": n_frames,
            "n_session_objects": len(info["object_ids"]),
        })

    if base_object_count is None:
        fail("v00 baseline not measured")

    # Secret scan over the runner logs (console + markers; ASL skipped: binary)
    for log in RUN_DIR.glob("**/*.log"):
        text = log.read_text(errors="ignore")
        if SECRET_RE.search(text):
            fail(f"secret pattern in {log}")

    MAP_OUT.parent.mkdir(parents=True, exist_ok=True)
    with MAP_OUT.open("w", newline="") as f:
        w = csv.DictWriter(
            f, fieldnames=["variant_id", "scene_id", "rollout_uuid",
                           "asl_path", "n_frames", "n_session_objects"])
        w.writeheader()
        w.writerows(map_rows)

    print(f"[INFO] variant map written: {MAP_OUT}")
    print("[SUCCESS] Stage2 Task 3.3: 11 serial rollouts complete, "
          "prompts/actors/seed verified.")


if __name__ == "__main__":
    main()
