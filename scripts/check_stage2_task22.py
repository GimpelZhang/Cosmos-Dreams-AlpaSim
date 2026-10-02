#!/usr/bin/env python3
"""Stage 2 Task 2.2 gate: per-variant prompt + random_seed propagation.

Checks:
- stage2 unit tests all pass
- start_session accepts prompt/seed params and places them on SessionRequest
- _initialize_session resolves prompt + seed behind the STAGE2 flag
- secret pattern scan
- wizard.run_method=NONE with flags unset generates configs with no STAGE2
  references (Stage 1 configuration path unchanged)
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SIM = Path("/home/vipuser/simulation")
ALPASIM = SIM / "repos/alpasim"
PY = ALPASIM / ".venv/bin/python"
SERVICE = ALPASIM / "src/runtime/alpasim_runtime/services/video_model_service.py"
CONFIGCHECK = Path("/mnt/artifacts/stage2/spikes/task22_gate_configcheck")

SECRET_RE = re.compile(
    r"hf_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|nvapi-[A-Za-z0-9]{20,}")


def fail(msg):
    print(f"[FAIL] {msg}")
    sys.exit(1)


def run(cmd, cwd=ALPASIM, env=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env)


def main():
    r = run([str(PY), "-m", "pytest", "src/runtime/tests/stage2", "-q"])
    if r.returncode != 0:
        fail(f"pytest failed:\n{r.stdout}\n{r.stderr}")
    n_tests = re.search(r"(\d+) passed", r.stdout)
    if not n_tests or int(n_tests.group(1)) < 10:
        fail("pytest did not report expected passing tests")

    src = SERVICE.read_text()
    for marker in (
        "text_prompt_negative: str | None = None",
        "random_seed: int = 0",
        "resolve_variant_prompt",
        'os.environ.get("STAGE2_RENDER_SEED")',
    ):
        if marker not in src:
            fail(f"video_model_service.py missing {marker!r}")
    if "TEMP SPIKE" in src:
        fail("temp spike markers present in video_model_service.py")
    if SECRET_RE.search(src):
        fail("secret pattern found in video_model_service.py")

    # run_method=NONE with no STAGE2 flags: generated config must be flag-free
    shutil.rmtree(CONFIGCHECK, ignore_errors=True)
    CONFIGCHECK.mkdir(parents=True)
    base_clip = Path("/mnt/artifacts/stage2/base_scene.txt").read_text().strip()
    wizard = (
        "set -e\n"
        f'source "{SIM}/scripts/env.sh"\n'
        f'cd "{ALPASIM}"\n'
        "env CUDA_VISIBLE_DEVICES=0 uv run --no-sync --project src/wizard alpasim_wizard "
        "deploy=external_video_model topology=1gpu driver=alpamayo15_1cam_local +chunking=8frame "
        f"\"scenes.scene_ids=['{base_clip}']\" 'wizard.external_services.renderer=[\"127.0.0.1:50051\"]' "
        "+runtime.endpoints.startup_timeout_s=900 wizard.run_method=NONE "
        f"wizard.log_dir={CONFIGCHECK}\n"
    )
    r = run(["bash", "-c", wizard], env={**os.environ})
    if r.returncode != 0 or "Alpasim finished" not in r.stdout:
        fail(f"NONE wizard failed:\n{r.stdout[-3000:]}\n{r.stderr[-3000:]}")

    yamls = list(CONFIGCHECK.glob("*.yaml"))
    if not yamls:
        fail("wizard generated no yaml configs")
    bad = [p.name for p in yamls if "STAGE2" in p.read_text()]
    if bad:
        fail(f"STAGE2 references leaked into baseline configs: {bad}")

    print(f"[INFO] {n_tests.group(1)} tests passed; NONE config flag-free")
    print("[SUCCESS] Stage2 Task 2.2: per-variant prompt + random_seed propagation verified.")


if __name__ == "__main__":
    main()
