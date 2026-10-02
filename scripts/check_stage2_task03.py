#!/usr/bin/env python3
"""Stage 2 Task 0.3 gate: Spike A — direct renderer launch works."""
import json
import socket
import subprocess
import sys
from pathlib import Path

LOG = Path("/mnt/artifacts/stage2/spikes/renderer_direct.log")
VERDICT = Path("/mnt/artifacts/stage2/spikes/launch_verdict.txt")
TORCHRUN_LOG = Path("/mnt/artifacts/stage2/regression/00_baseline/renderer.log")
PORT = 50051


def fail(msg):
    print(f"[FAIL] {msg}")
    sys.exit(1)


def main():
    if not LOG.exists() or "Server started successfully" not in LOG.read_text(errors="ignore"):
        fail(f"direct-launch log missing readiness marker: {LOG}")

    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=5):
            pass
    except OSError as e:
        fail(f"renderer port {PORT} not connectable: {e}")

    smi = subprocess.run(
        ["nvidia-smi", "--query-gpu=index,memory.used", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, check=True)
    vram = {}
    for line in smi.stdout.strip().splitlines():
        idx, used = line.split(",")
        vram[idx.strip()] = int(used)
    if vram.get("1", 0) < 15000:
        fail(f"GPU1 VRAM {vram.get('1')} MiB < 15GB after readiness")

    torchrun_hung = TORCHRUN_LOG.exists() and "Server started successfully" not in TORCHRUN_LOG.read_text(errors="ignore")
    verdict = {
        "conclusion": "direct_launch",
        "direct_ready": True,
        "gpu1_vram_mib": vram["1"],
        "port": PORT,
        "torchrun_hung": torchrun_hung,
        "launcher": "scripts/start_renderer_direct.sh",
    }
    VERDICT.write_text(json.dumps(verdict, indent=2) + "\n")
    print(f"[INFO] {verdict}")
    print("[SUCCESS] Stage2 Task 0.3: direct renderer launch verified; torchrun path retired for Stage2.")


if __name__ == "__main__":
    main()
