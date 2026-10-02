#!/usr/bin/env python3
"""Stage 2 Task 5.4 gate: final regression, resource teardown, gate rerun.

1. final pinned-seed regression (regression/02_final/digest.json) passes the
   pixel comparator against both deterministic gold copies (06_det_a,
   07_det_b), and the two gold copies pass each other
2. both GPUs back near idle (<=60 MiB), no docker containers, no leftover
   renderer/wizard processes
3. every earlier check_stage2_task*.py reruns and prints [SUCCESS]
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SIM = Path("/home/vipuser/simulation")
REG = Path("/mnt/artifacts/stage2/regression")
PY = SIM / "repos/alpasim/.venv/bin/python"
COMPARATOR = SIM / "scripts/check_stage2_regression.py"


def fail(msg):
    print(f"[FAIL] {msg}")
    sys.exit(1)


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def compare(new: str, gold: str):
    r = run([str(PY), str(COMPARATOR),
             f"{REG}/{new}/digest.json", f"{REG}/{gold}/digest.json",
             "--check-series"])
    if r.returncode != 0:
        fail(f"regression {new} vs {gold} failed:\n{r.stdout}")
    return r.stdout.strip()


def docker_ps():
    r = run(["sg", "docker", "-c", "docker ps -aq"])
    if r.returncode != 0:
        r = run(["docker", "ps", "-aq"])
    return r.stdout.split()


def main():
    for pair in (("02_final", "06_det_a"), ("02_final", "07_det_b"),
                 ("07_det_b", "06_det_a")):
        print(compare(*pair))

    # GPU memory.
    r = run(["nvidia-smi", "--query-gpu=index,memory.used",
             "--format=csv,noheader"])
    if r.returncode != 0:
        fail("nvidia-smi failed")
    print(r.stdout.strip())
    for line in r.stdout.strip().splitlines():
        idx, mem = [x.strip() for x in line.split(",")]
        if int(mem.rstrip(" MiB")) > 60:
            fail(f"GPU {idx} still using {mem}")

    leftovers = docker_ps()
    if leftovers:
        fail(f"docker containers still running: {leftovers}")

    r = run(["pgrep", "-af", r"omnidreams|alpasim_wizard|asl_to_frames"])
    lines = [l for l in r.stdout.splitlines() if "check_stage2_task54" not in l]
    if lines:
        fail(f"leftover processes:\n" + "\n".join(lines))

    # Rerun every earlier gate in order.
    gates = ["check_stage2_task24.py", "check_stage2_task33.py",
             "check_stage2_task41.py", "check_stage2_task42.py",
             "check_stage2_task43.py", "check_stage2_task51.py",
             "check_stage2_task52.py"]
    for gate in gates:
        r = run([str(PY), str(SIM / "scripts" / gate)])
        success = [l for l in r.stdout.splitlines() if l.startswith("[SUCCESS]")]
        if not success:
            fail(f"rerun of {gate} did not succeed:\n{r.stdout}\n{r.stderr}")
        print(success[0])

    print("[SUCCESS] Stage2 Task 5.4: final regression passed, all resources "
          "released, Stage2 complete.")


if __name__ == "__main__":
    main()
