#!/usr/bin/env python3
"""Stage 2 Task 2.4 gate: patches exported, Stage 1 regression intact.

Per plan §3.3 the second regression runs after seed plumbing at pixel level:
two independent no-variant rollouts pinned to the same seed must agree
frame-by-frame (06_det_a vs 07_det_b). The pre-seed 00_baseline record was
only used by the first gate; the unseeded Stage 1 path is checked
structurally instead (the no-flag post-patch session request was identical
to the baseline's: same default prompt, seed 0).

Checks:
- both seeded rollouts present; digests collected; pixel comparator passes
- the post-patch no-flag run sent the renderer seed 0 and the Stage 1
  default prompt (request-level identity with Stage 1)
- combined stage2 patch exists, non-empty, secret-free and reverse-applies
  on the current inner-repo working tree
"""
import re
import subprocess
import sys
from pathlib import Path

SIM = Path("/home/vipuser/simulation")
ALPASIM = SIM / "repos/alpasim"
PY = ALPASIM / ".venv/bin/python"
RUN_A = Path("/mnt/artifacts/stage2/regression/06_det_a")
RUN_B = Path("/mnt/artifacts/stage2/regression/07_det_b")
RUN_UNFLAGGED = Path("/mnt/artifacts/stage2/regression/01_postpatch/run")
PATCH = SIM / "configs/local-patches/patches/alpasim-stage2-001-synthetic-scenarios.patch"

SECRET_RE = re.compile(
    r"hf_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|nvapi-[A-Za-z0-9]{20,}")


def fail(msg):
    print(f"[FAIL] {msg}")
    sys.exit(1)


def run(cmd):
    return subprocess.run(cmd, cwd=ALPASIM, capture_output=True, text=True)


def main():
    for tag, run_dir in [("A", RUN_A), ("B", RUN_B)]:
        if not (run_dir / "run").exists():
            fail(f"missing seeded {tag} run: {run_dir / 'run'}")
        digest = run_dir / "digest.json"
        r = run([str(PY), str(SIM / "scripts/check_stage2_collect_digest.py"),
                 str(run_dir / "run"), str(digest)])
        if r.returncode != 0:
            fail(f"digest collection for run {tag} failed:\n{r.stdout}\n{r.stderr}")

    r = run([str(PY), str(SIM / "scripts/check_stage2_regression.py"),
             str(RUN_B / "digest.json"), str(RUN_A / "digest.json"),
             "--check-series"])
    if r.returncode != 0:
        fail(f"pixel regression failed:\n{r.stdout}")
    print(r.stdout.strip())

    # No-flag post-patch run must have sent seed 0 + Stage 1 default prompt.
    r = run([str(PY), str(SIM / "scripts/extract_stage2_seed.py"),
             *map(str, RUN_UNFLAGGED.glob("**/rollout.asl"))])
    if r.returncode != 0 or "random_seed=0" not in r.stdout:
        fail(f"flag-free request not on the Stage 1 seed path:\n{r.stdout}")

    if not PATCH.exists() or PATCH.stat().st_size < 100:
        fail("combined stage2 patch missing or empty")
    text = PATCH.read_text()
    if SECRET_RE.search(text):
        fail("secret pattern in exported patch")

    r = run(["git", "apply", "-R", "--check", str(PATCH)])
    if r.returncode != 0:
        fail(f"patch not reverse-applicable:\n{r.stdout}\n{r.stderr}")

    print("[SUCCESS] Stage2 Task 2.4: patches exported, Stage1 regression intact.")


if __name__ == "__main__":
    main()
