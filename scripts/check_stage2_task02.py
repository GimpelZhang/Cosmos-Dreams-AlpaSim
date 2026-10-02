#!/usr/bin/env python3
"""Stage 2 Task 0.2 gate: Stage 1 regression baseline digest recorded."""
import glob
import json
import subprocess
import sys
from pathlib import Path

REGDIR = Path("/mnt/artifacts/stage2/regression/00_baseline")
DIGEST = REGDIR / "digest.json"


def fail(msg):
    print(f"[FAIL] {msg}")
    sys.exit(1)


def main():
    if not DIGEST.exists():
        fail(f"digest missing: {DIGEST} (run check_stage2_collect_digest.py first)")
    digest = json.loads(DIGEST.read_text())

    rollout_dirs = glob.glob(str(REGDIR / "run/rollouts/*/*/"))
    if len(rollout_dirs) != 1:
        fail(f"expected 1 rollout dir, found {len(rollout_dirs)}")
    rd = Path(rollout_dirs[0])
    if not (rd / "_complete").exists():
        fail(f"_complete marker missing in {rd}")

    m = digest["metrics"]
    n = m["n_frames"]
    if not (73 <= n <= 77):
        fail(f"frame count {n} outside 75±2")
    black = m["events"]["img_is_black"]
    if black["n_frames"] != 0:
        fail(f"{black['n_frames']} black frames recorded")

    r = digest["runtime"]
    if not r.get("session"):
        fail("runtime log lacks Session COMPLETED record")

    print(f"[INFO] n_frames={n}; session={r['session']['sim_seconds']}sim s / "
          f"{r['session']['wall_seconds']}wall s (RTF {r['session']['rtf']}); "
          f"chunk mean={r['chunk_render_s']['mean']:.3f}s; step mean={r['step_wall_s']['mean']:.3f}s")
    print("[SUCCESS] Stage2 Task 0.2: Stage1 regression baseline recorded.")


if __name__ == "__main__":
    main()
