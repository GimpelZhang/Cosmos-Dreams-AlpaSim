#!/usr/bin/env python3
"""Stage 2 Task 0.4 gate: Spike B — base scene selected with measured GT initial
speed, curvature, handedness, and qualitative shoulder check.

Selection criteria (docs/Stage2_Plan_detailed.md §6 Task 0.4):
- GT initial speed 15..35 km/h, confirmed by two independent sources
- progress_rel >= 0.9
- curvature radius > 200 m (straight road)
- right-side shoulder qualitatively >= 2.5 m
"""
import json
import sys
from pathlib import Path

SPIKES = Path("/mnt/artifacts/stage2/spikes")
BASE_SCENE_FILE = Path("/mnt/artifacts/stage2/base_scene.txt")

# geom files measured from existing rollouts (no reruns needed)
GEOMS = {
    "clipgt-02eadd92-02f1-46d8-86fe-a9e338fed0b6": SPIKES / "geom_02eadd92.json",
    "clipgt-3d343ae9-6d5b-415f-be2c-aa8d0ebc03c9": SPIKES / "geom_3d343ae9.json",
    "clipgt-46252225-453d-474c-963c-bc0cd917e7e4": SPIKES / "geom_46252225.json",
    "clipgt-5a38c811-1e28-4a67-9239-54ba2b695950": SPIKES / "geom_5a38c811.json",
}
SHOULDER = SPIKES / "shoulder_assessment.json"


def fail(msg):
    print(f"[FAIL] {msg}")
    sys.exit(1)


def evaluate(geom: dict) -> tuple[bool, list[str]]:
    reasons = []
    v0 = geom["gt_v0_kmh"]
    xcheck = geom["metrics_crosscheck"].get("gt_v0_kmh")
    progress = geom["metrics_crosscheck"].get("progress_rel")
    radius = geom["curvature_radius_m"]

    if not geom["speed_sources_agree"] or xcheck is None or abs(v0 - xcheck) > 3.0:
        reasons.append(f"speed sources disagree: rig={v0} metrics={xcheck}")
    if not (15 <= v0 <= 35):
        reasons.append(f"v0 {v0} outside 15..35 km/h")
    if progress is None or progress < 0.9:
        reasons.append(f"progress_rel {progress} < 0.9")
    if radius == "inf" or radius <= 200:
        reasons.append(f"curvature radius {radius} <= 200 m")
    if geom["interpretation"] != "rig x=longitudinal forward positive confirmed":
        reasons.append("rig handedness not confirmed")
    return (not reasons), reasons


def main():
    candidates = {}
    for scene, path in GEOMS.items():
        if not path.exists():
            fail(f"missing geometry measurement: {path}")
        geom = json.loads(path.read_text())
        ok, reasons = evaluate(geom)
        status = "PASS" if ok else "reject"
        print(f"[INFO] {scene}: {status} "
              f"(v0={geom['gt_v0_kmh']} km/h, radius={geom['curvature_radius_m']} m, "
              f"progress={geom['metrics_crosscheck'].get('progress_rel')})")
        for r in reasons:
            print(f"       - {r}")
        if ok:
            candidates[scene] = geom

    if not SHOULDER.exists():
        fail(f"missing shoulder assessment: {SHOULDER}")
    shoulder = json.loads(SHOULDER.read_text())
    if not shoulder.get("shoulder_ok"):
        fail("shoulder assessment not OK")
    for frame in shoulder.get("evidence_frames", []):
        if not (SPIKES / frame).exists():
            fail(f"shoulder evidence frame missing: {frame}")

    if shoulder["scene_id"] not in candidates:
        fail(f"shoulder-verified scene {shoulder['scene_id']} is not a passing candidate")
    winner = shoulder["scene_id"]

    # write base scene pointer (id only, no other content)
    BASE_SCENE_FILE.write_text(winner + "\n")

    print(f"[INFO] candidates: {sorted(candidates)}")
    print(f"[INFO] selected base scene: {winner}")
    print("[SUCCESS] Stage2 Task 0.4: base scene geometry measured and scene selected.")


if __name__ == "__main__":
    main()
