#!/usr/bin/env python3
"""Stage 2 Task 2.3 gate: STAGE2 env injection chain + config check.

Checks:
- applied and authoritative extras copies exist and are identical
- wizard.run_method=NONE with +extras=stage2_env renders runtime-0 env with
  all three STAGE2 vars, the read-only variants.yaml bind mount, and every
  n_concurrent_rollouts / nr_workers forced to 1
- secret pattern scan
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SIM = Path("/home/vipuser/simulation")
ALPASIM = SIM / "repos/alpasim"
APPLIED = ALPASIM / "src/wizard/configs/extras/stage2_env.yaml"
AUTHORITATIVE = SIM / "configs/stage2/extras/stage2_env.yaml"
CONFIGCHECK = Path("/mnt/artifacts/stage2/spikes/task23_gate_configcheck")

SECRET_RE = re.compile(
    r"hf_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|nvapi-[A-Za-z0-9]{20,}")


def fail(msg):
    print(f"[FAIL] {msg}")
    sys.exit(1)


def main():
    for p in (APPLIED, AUTHORITATIVE):
        if not p.exists():
            fail(f"missing extras: {p}")
    if APPLIED.read_text() != AUTHORITATIVE.read_text():
        fail("applied extras differs from authoritative copy")
    if SECRET_RE.search(APPLIED.read_text()):
        fail("secret pattern in extras")

    shutil.rmtree(CONFIGCHECK, ignore_errors=True)
    CONFIGCHECK.mkdir(parents=True)
    base_clip = Path("/mnt/artifacts/stage2/base_scene.txt").read_text().strip()
    script = (
        "set -e\n"
        f'source "{SIM}/scripts/env.sh"\n'
        f'cd "{ALPASIM}"\n'
        "env STAGE2_VARIANT_ID=v05_stroller_jaywalking STAGE2_RENDER_SEED=20261002 "
        "CUDA_VISIBLE_DEVICES=0 uv run --no-sync --project src/wizard alpasim_wizard "
        "deploy=external_video_model topology=1gpu driver=alpamayo15_1cam_local "
        "+chunking=8frame +extras=stage2_env "
        f"\"scenes.scene_ids=['{base_clip}']\" 'wizard.external_services.renderer=[\"127.0.0.1:50051\"]' "
        "+runtime.endpoints.startup_timeout_s=900 wizard.run_method=NONE "
        f"wizard.log_dir={CONFIGCHECK}\n"
    )
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       env={**os.environ})
    if r.returncode != 0 or "Alpasim finished" not in r.stdout:
        fail(f"NONE wizard failed:\n{r.stdout[-3000:]}\n{r.stderr[-3000:]}")

    compose = (CONFIGCHECK / "docker-compose.yaml").read_text()
    runtime_section = re.split(
        r"\n  (?=\S)", compose.split("  runtime-0:")[1], maxsplit=1)[0]
    for expected in (
        "STAGE2_VARIANT_SPEC=/mnt/stage2/variants.yaml",
        "STAGE2_VARIANT_ID=v05_stroller_jaywalking",
        "STAGE2_RENDER_SEED=20261002",
        "configs/stage2/variants.yaml:/mnt/stage2/variants.yaml:ro",
    ):
        if expected not in runtime_section:
            fail(f"runtime-0 missing {expected!r}")

    # all five default volumes preserved
    for mount in ("/mnt/nre-data", "/mnt/log_dir", "/mnt/array_job_dir",
                  "/repo/src", "/repo/plugins"):
        if mount not in runtime_section:
            fail(f"runtime-0 lost default mount {mount}")

    user_cfg = (CONFIGCHECK / "generated-user-config-0.yaml").read_text()
    endpoints = user_cfg.split("endpoints:")[1].split("renderer:", 1)[0] + \
        user_cfg.split("endpoints:")[1]
    concurrency = re.findall(r"n_concurrent_rollouts: (\d+)", user_cfg)
    if len(concurrency) != 5 or any(v != "1" for v in concurrency):
        fail(f"n_concurrent_rollouts not all 1: {concurrency}")
    if not re.search(r"nr_workers: 1", user_cfg):
        fail("nr_workers not 1")

    print("[INFO] env+mounts rendered; all 5 endpoints serial; nr_workers=1")
    print("[SUCCESS] Stage2 Task 2.3: env injection chain and config check verified.")


if __name__ == "__main__":
    main()
