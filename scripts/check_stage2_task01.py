#!/usr/bin/env python3
"""Stage 2 Task 0.1 gate: baseline snapshot recorded, preflight green."""
import re
import subprocess
import sys
from pathlib import Path

SNAPSHOT = Path("/mnt/artifacts/stage2/baseline_snapshot.txt")
REPO_ROOT = Path("/home/vipuser/simulation")
EXPECTED_REPOS = ["alpasim " , "flashdreams ", "alpamayo ", "omni-dreams "]

# Real credential shapes (long suffix after prefix); descriptive prose in docs won't match.
SECRET_PATTERNS = [
    re.compile(r"hf_[A-Za-z0-9]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"gho_[A-Za-z0-9]{20,}"),
    re.compile(r"nvapi-[A-Za-z0-9]{20,}"),
]


def fail(msg: str) -> None:
    print(f"[FAIL] {msg}")
    sys.exit(1)


def main() -> None:
    if not SNAPSHOT.exists():
        fail(f"snapshot missing: {SNAPSHOT}")
    text = SNAPSHOT.read_text()

    hashes = re.findall(r"(?:^|\s)([0-9a-f]{40})(?:\s|$)", text, flags=re.MULTILINE)
    if len(hashes) < 4:
        fail(f"expected 4 legal 40-char repo hashes, found {len(hashes)}")

    # df free space: parse the two df lines (avail column)
    df = subprocess.run(["df", "-BG", "--output=target,avail", "/", "/mnt"],
                       capture_output=True, text=True, check=True)
    free = {}
    for line in df.stdout.strip().splitlines()[1:]:
        parts = line.split()
        free[parts[0]] = int(parts[1].rstrip("G"))
    if free.get("/", 0) < 20:
        fail(f"/ free space {free.get('/')}G < 20G")
    if free.get("/mnt", 0) < 30:
        fail(f"/mnt free space {free.get('/mnt')}G < 30G")

    # GPU VRAM idle baseline
    smi = subprocess.run(
        ["nvidia-smi", "--query-gpu=index,memory.used", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, check=True)
    for line in smi.stdout.strip().splitlines():
        idx, used = line.split(",")
        used = int(used.strip())
        if abs(used - 14) > 50:
            fail(f"GPU {idx.strip()} VRAM {used} MiB, expected idle ~14 (±50)")

    # Secret scan over tracked dirs (text files only)
    scanned = 0
    for sub in ("scripts", "configs", "docs"):
        for p in (REPO_ROOT / sub).rglob("*"):
            if not p.is_file() or p.stat().st_size > 2_000_000:
                continue
            try:
                content = p.read_text(errors="ignore")
            except OSError:
                continue
            scanned += 1
            for pat in SECRET_PATTERNS:
                m = pat.search(content)
                if m:
                    fail(f"credential pattern {pat.pattern} matched in {p}: {m.group(0)[:12]}...")

    print(f"[INFO] snapshot hashes: {hashes}; / free={free['/']}G /mnt free={free['/mnt']}G; "
          f"scanned {scanned} files")
    print("[SUCCESS] Stage2 Task 0.1: baseline snapshot recorded, preflight green.")


if __name__ == "__main__":
    main()
