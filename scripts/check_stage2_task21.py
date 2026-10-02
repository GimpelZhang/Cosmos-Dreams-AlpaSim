#!/usr/bin/env python3
"""Stage 2 Task 2.1 gate: synthetic TrafficObject merge patch.

Checks:
- new module exists and imports inside the alpasim venv
- unit tests (src/runtime/tests/stage2) all pass
- unbound_rollout.py contains the guarded STAGE2_VARIANT_SPEC call and no
  leftover temp-spike injection markers
- tracked modification reverse-applies then re-applies cleanly via git apply
- secret pattern scan over touched files
"""
import re
import subprocess
import sys
from pathlib import Path

ALPASIM = Path("/home/vipuser/simulation/repos/alpasim")
PY = ALPASIM / ".venv/bin/python"
MODULE = ALPASIM / "src/runtime/alpasim_runtime/stage2/variant_actors.py"
TEST = ALPASIM / "src/runtime/tests/stage2/test_variant_actors.py"
UNBOUND = ALPASIM / "src/runtime/alpasim_runtime/unbound_rollout.py"

SECRET_RE = re.compile(
    r"hf_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|nvapi-[A-Za-z0-9]{20,}")


def fail(msg):
    print(f"[FAIL] {msg}")
    sys.exit(1)


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=ALPASIM, capture_output=True, text=True, **kw)


def main():
    for p in (MODULE, TEST, PY, UNBOUND):
        if not p.exists():
            fail(f"missing: {p}")

    r = run([str(PY), "-c",
             "from alpasim_runtime.stage2 import variant_actors; "
             "assert callable(variant_actors.merge_synthetic_actors)"])
    if r.returncode != 0:
        fail(f"module import failed:\n{r.stderr}")

    r = run([str(PY), "-m", "pytest", "src/runtime/tests/stage2", "-q"])
    if r.returncode != 0:
        fail(f"pytest failed:\n{r.stdout}\n{r.stderr}")
    n_tests = re.search(r"(\d+) passed", r.stdout)
    if not n_tests or int(n_tests.group(1)) < 5:
        fail("pytest did not report the expected passing tests")

    src = UNBOUND.read_text()
    if 'os.environ.get("STAGE2_VARIANT_SPEC")' not in src:
        fail("guarded STAGE2_VARIANT_SPEC call missing from unbound_rollout.py")
    if "TEMP SPIKE" in src or "stage2_spike" in src:
        fail("temp spike injection markers still present in unbound_rollout.py")
    if "merge_synthetic_actors(" not in src:
        fail("merge_synthetic_actors call missing from unbound_rollout.py")

    if SECRET_RE.search(MODULE.read_text()) or SECRET_RE.search(TEST.read_text()):
        fail("secret pattern found in new files")

    # tracked patch must reverse-apply and re-apply cleanly
    r = run(["git", "diff", "--", "src/runtime/alpasim_runtime/unbound_rollout.py"])
    if r.returncode != 0 or not r.stdout.strip():
        fail("no git diff for unbound_rollout.py")
    patch = Path("/tmp/stage2_task21.patch")
    patch.write_text(r.stdout)
    r = run(["git", "apply", "-R", str(patch)])
    if r.returncode != 0:
        fail(f"git apply -R failed:\n{r.stdout}\n{r.stderr}")
    try:
        if "STAGE2_VARIANT_SPEC" in UNBOUND.read_text():
            fail("reverse-apply did not remove the guarded call")
        r = run(["git", "apply", str(patch)])
        if r.returncode != 0:
            fail(f"re-apply failed:\n{r.stdout}\n{r.stderr}")
        if 'os.environ.get("STAGE2_VARIANT_SPEC")' not in UNBOUND.read_text():
            fail("re-apply did not restore the guarded call")
    finally:
        # ensure working tree ends with the patch applied
        if 'os.environ.get("STAGE2_VARIANT_SPEC")' not in UNBOUND.read_text():
            run(["git", "apply", str(patch)])

    print(f"[INFO] {n_tests.group(1)} tests passed; patch reverse/apply clean")
    print("[SUCCESS] Stage2 Task 2.1: synthetic TrafficObject merge patch verified.")


if __name__ == "__main__":
    main()
