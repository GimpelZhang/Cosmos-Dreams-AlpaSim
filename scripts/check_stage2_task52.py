#!/usr/bin/env python3
"""Stage 2 Task 5.2 gate: Stage2 report and HTML board complete.

- docs/Stage2_Report.md >4KB and contains all 11 variant ids
- /mnt/artifacts/stage2/report/index.html exists
- every mp4 referenced by the HTML exists on disk
- secret-pattern scan over report, HTML, scripts and stage2 configs
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

SIM = Path("/home/vipuser/simulation")
REPORT_MD = SIM / "docs/Stage2_Report.md"
HTML = Path("/mnt/artifacts/stage2/report/index.html")
VIDEO_DIR = Path("/mnt/artifacts/stage2/videos")
VARIANTS_YAML = SIM / "configs/stage2/variants.yaml"

SECRET_RE = re.compile(
    r"hf_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|nvapi-[A-Za-z0-9]{20,}")


def fail(msg):
    print(f"[FAIL] {msg}")
    sys.exit(1)


def main():
    import yaml
    vids = [v["id"] for v in yaml.safe_load(VARIANTS_YAML.read_text())["variants"]]

    if not REPORT_MD.exists() or REPORT_MD.stat().st_size < 4096:
        fail("Stage2_Report.md missing or <4KB")
    md = REPORT_MD.read_text()
    for vid in vids:
        if vid not in md:
            fail(f"report missing variant {vid}")

    if not HTML.exists():
        fail(f"missing {HTML}")
    html_text = HTML.read_text()

    referenced = set(re.findall(r"""["']((?:\.\./)?videos/[^"']+\.mp4)["']""",
                                html_text))
    if len(referenced) < 11:
        fail(f"HTML references {len(referenced)} mp4 paths, expected >=11")
    for rel in referenced:
        p = (HTML.parent / rel).resolve()
        if not p.exists():
            fail(f"HTML references missing file {p}")

    scan_targets = [REPORT_MD, HTML, *SIM.glob("scripts/*.py"),
                    *SIM.glob("configs/stage2/**/*"),
                    *SIM.glob("configs/local-patches/**/*")]
    for target in scan_targets:
        if target.is_file():
            if SECRET_RE.search(target.read_text(errors="ignore")):
                fail(f"secret pattern in {target}")

    print(f"ok: report {REPORT_MD.stat().st_size} bytes, {len(referenced)} linked mp4s")
    print("[SUCCESS] Stage2 Task 5.2: report and HTML board verified.")


if __name__ == "__main__":
    main()
