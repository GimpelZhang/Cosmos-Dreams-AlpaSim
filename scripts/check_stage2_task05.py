#!/usr/bin/env python3
"""Stage 2 Task 0.5 gate: Spike C — static-actor render consistency + OOD verdict.

Inputs (under /mnt/artifacts/stage2/spikes):
- spike_actors/scores.json: rubric scores for prompt-only cow, box cow, box T-Rex
- spike_actors/{prompt_cow,box_cow,box_trex}/t{4,6,8,12}s.png
- spike_actors/constant_trajectory_rendered.txt: evidence that is_static=False
  constant-trajectory object reached the renderer
Writes:
- spike_verdict.json (also consumed by Task 1.1 generator)
Also asserts the temporary injection was reverted in the alpasim work tree.
"""
import json
import subprocess
import sys
from pathlib import Path

SPIKES = Path("/mnt/artifacts/stage2/spikes")
ACTORS = SPIKES / "spike_actors"
SCORES = ACTORS / "scores.json"
REPO = Path("/home/vipuser/simulation/repos/alpasim")
INJECT_FILE = REPO / "src/runtime/alpasim_runtime/unbound_rollout.py"
VERDICT = SPIKES / "spike_verdict.json"


def fail(msg):
    print(f"[FAIL] {msg}")
    sys.exit(1)


def main():
    if not SCORES.exists():
        fail(f"missing scores file: {SCORES}")
    scores = json.loads(SCORES.read_text())["runs"]

    for run in ("prompt_only_cow", "box_cow", "box_trex"):
        if run not in scores:
            fail(f"scores.json lacks run {run}")
        r = scores[run]
        if len(r["scores"]) != 4:
            fail(f"{run}: expected 4 frame scores")
        frame_dir = ACTORS / {
            "prompt_only_cow": "prompt_cow",
            "box_cow": "box_cow",
            "box_trex": "box_trex",
        }[run]
        for frame in r["frames"]:
            if not (frame_dir / frame).exists():
                fail(f"missing sampled frame {frame_dir/frame}")
        mean = sum(r["scores"]) / len(r["scores"])
        if abs(mean - r["mean"]) > 1e-9:
            fail(f"{run}: recorded mean {r['mean']} != computed {mean}")

    prompt_cow = scores["prompt_only_cow"]["mean"]
    box_cow = scores["box_cow"]["mean"]
    box_trex = scores["box_trex"]["mean"]

    if box_cow < 1:
        fail(f"box cow mean {box_cow} < 1 -> static obstacles must downgrade to prompt-only "
             "(route not implemented by this gate script)")
    if box_cow < prompt_cow:
        fail(f"box cow {box_cow} worse than prompt-only {prompt_cow}")

    trex_keep = box_trex >= 1
    v07_decision = (
        "v07_t_rex_ood" if trex_keep else "v07_construction_excavator"
    )

    # the temporary injection must have been removed from the work tree
    text = INJECT_FILE.read_text()
    if "STAGE2 TEMP SPIKE INJECTION" in text:
        fail("temporary spike injection still present in unbound_rollout.py (revert first)")
    status = subprocess.run(
        ["git", "-C", str(REPO), "status", "--porcelain",
         "src/runtime/alpasim_runtime/unbound_rollout.py"],
        capture_output=True, text=True, check=True).stdout.strip()
    if status:
        fail(f"unbound_rollout.py work tree not clean vs HEAD: {status!r}")

    constant_traj = ACTORS / "constant_trajectory_rendered.txt"
    if not constant_traj.exists():
        fail("missing evidence: constant_trajectory_rendered.txt")

    verdict = {
        "conclusions": {
            "v08_loose_cow": "box_injection",
            "v07": v07_decision,
            "constant_trajectory_nonstatic_rendered": True,
        },
        "scores": {
            "prompt_only_cow_mean": prompt_cow,
            "box_cow_mean": box_cow,
            "box_trex_mean": box_trex,
            "box_trex_frame_scores": scores["box_trex"]["scores"],
        },
        "notes": (
            "T-Rex accepted" if trex_keep else
            "T-Rex scored 0 -> v07 downgraded to stopped excavator OTHER 3.5x1.5x3.0; "
            "T-Rex retained only as an appendix spike video"
        ),
    }
    VERDICT.write_text(json.dumps(verdict, indent=2) + "\n")

    print(f"[INFO] prompt-only cow={prompt_cow}, box cow={box_cow}, box T-Rex={box_trex}")
    print(f"[INFO] v07 decision: {v07_decision}")
    print(f"[INFO] verdict written: {VERDICT}")
    print("[SUCCESS] Stage2 Task 0.5: static-actor rendering verified; OOD v07 decision fixed.")


if __name__ == "__main__":
    main()
