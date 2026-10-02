#!/usr/bin/env python3
"""Stage 2 Task 1.1 gate: 11 variant descriptors generated and schema-valid.

Checks:
- variants.yaml parses with exactly the 11 expected variants (v07 per spike verdict)
- v00 prompt equals Stage 1 external_video_model.yaml prompt verbatim
- v01-v10 prompts contain the distinctive §5.2 fragments; actor geometry exact
- manifest CSV: 11 rows, exact columns, all on the base scene
- secret pattern scan over configs/stage2
"""
import csv
import re
import sys
from pathlib import Path

import yaml

REPO_YAML = Path("/home/vipuser/simulation/configs/stage2/variants.yaml")
REPO_CSV = Path("/home/vipuser/simulation/configs/stage2/manifests/stage2_variants.csv")
DEPLOY_YAML = Path(
    "/home/vipuser/simulation/repos/alpasim/src/wizard/configs/deploy/external_video_model.yaml")
SPIKE_VERDICT = Path("/mnt/artifacts/stage2/spikes/spike_verdict.json")

EXPECTED_IDS = [
    "v00_baseline", "v01_heavy_snow_blizzard", "v02_torrential_rain_night",
    "v03_dense_fog_dawn", "v04_blinding_sunset_glare", "v05_stroller_jaywalking",
    "v06_wheelchair_shoulder", None, "v08_loose_cow", "v09_construction_debris",
    "v10_typhoon_compound",
]

# distinctive fragments from §5.2 prompts
PROMPT_FRAGMENTS = {
    "v01_heavy_snow_blizzard": ["heavy blizzard", "snow accumulating", "low visibility about 30 meters"],
    "v02_torrential_rain_night": ["torrential rain", "wet puddles", "midnight"],
    "v03_dense_fog_dawn": ["dense ground fog", "visibility reduced to about 15 meters", "dawn"],
    "v04_blinding_sunset_glare": ["blinding glare", "lens flare", "golden hour"],
    "v05_stroller_jaywalking": ["baby stroller", "crossing the street from the right"],
    "v06_wheelchair_shoulder": ["powered wheelchair", "right roadway shoulder"],
    "v07_construction_excavator": ["stopped excavator", "blocking the road"],
    "v08_loose_cow": ["dairy cow", "center of the driving lane"],
    "v09_construction_debris": ["orange traffic barrels", "wooden crates"],
    "v10_typhoon_compound": ["tree branch", "umbrella", "typhoon"],
}

# exact actor geometry from §5.2 (and downgraded v07)
EXPECTED_ACTORS = {
    "v05_stroller_jaywalking": [
        ("pedestrian_stroller", "pedestrian", [1.4, 0.8, 1.7], [22.0, -3.2], [0.0, 1.2], 3.0)],
    "v06_wheelchair_shoulder": [
        ("powered_wheelchair", "pedestrian", [1.2, 0.75, 1.3], [16.0, -2.2], [2.5, 0.0], 2.5)],
    "v07_construction_excavator": [
        ("stopped_excavator", "other_vehicle", [3.5, 1.5, 3.0], [28.0, 0.0], [0.0, 0.0], 3.5)],
    "v08_loose_cow": [
        ("dairy_cow", "animal", [2.4, 1.0, 1.6], [26.0, 0.0], [0.0, 0.0], 3.0)],
    "v09_construction_debris": [
        ("barrel_1", "protruding_object", [0.6, 0.6, 0.9], [18.0, -0.6], [0.0, 0.0], 3.0),
        ("barrel_2", "protruding_object", [0.6, 0.6, 0.9], [19.2, 0.3], [0.0, 0.0], 3.0),
        ("crate_1", "protruding_object", [1.4, 1.0, 0.9], [20.5, -0.2], [0.0, 0.0], 3.0)],
    "v10_typhoon_compound": [
        ("fallen_branch", "protruding_object", [5.0, 0.8, 0.6], [20.0, 0.0], [0.0, 0.0], 3.0),
        ("pedestrian_umbrella", "pedestrian", [0.8, 0.8, 1.75], [24.0, -3.0], [0.0, 0.8], 3.0)],
}

SECRET_RE = re.compile(
    r"hf_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|nvapi-[A-Za-z0-9]{20,}")


def fail(msg):
    print(f"[FAIL] {msg}")
    sys.exit(1)


def close(a, b, tol=1e-6):
    return abs(a - b) <= tol


def main():
    for p in (REPO_YAML, REPO_CSV):
        if not p.exists():
            fail(f"missing output: {p}")

    doc = yaml.safe_load(REPO_YAML.read_text())
    variants = doc["variants"]
    if len(variants) != 11:
        fail(f"expected 11 variants, got {len(variants)}")

    # v07 id must follow the spike verdict
    import json
    verdict = json.loads(SPIKE_VERDICT.read_text())["conclusions"]["v07"]
    EXPECTED_IDS[7] = verdict

    ids = [v["id"] for v in variants]
    if ids != EXPECTED_IDS:
        fail(f"variant ids differ:\n got {ids}\n exp {EXPECTED_IDS}")

    by_id = {v["id"]: v for v in variants}

    # v00 verbatim cross-source check against the Stage 1 deploy config
    deploy = yaml.safe_load(DEPLOY_YAML.read_text())
    stage1_prompt = deploy["runtime"]["renderer"]["video_model_config"]["text_prompt_positive"]
    if by_id["v00_baseline"]["prompt_positive"] != stage1_prompt:
        fail("v00 prompt not verbatim-equal to external_video_model.yaml prompt")
    if by_id["v00_baseline"]["prompt_negative"] != "":
        fail("v00 negative prompt must stay empty (Stage 1 default)")

    for vid, fragments in PROMPT_FRAGMENTS.items():
        prompt = by_id[vid]["prompt_positive"]
        for frag in fragments:
            if frag not in prompt:
                fail(f"{vid}: prompt lacks §5.2 fragment {frag!r}")
        if not by_id[vid]["prompt_negative"]:
            fail(f"{vid}: universal negative prompt missing")

    for vid, expected in EXPECTED_ACTORS.items():
        actors = by_id[vid]["actors"]
        if len(actors) != len(expected):
            fail(f"{vid}: expected {len(expected)} actors, got {len(actors)}")
        for got, exp in zip(actors, expected):
            aid, label, box, start, vel, act = exp
            if got["id"] != aid or got["label_class"] != label:
                fail(f"{vid}: actor id/label mismatch: {got['id']}/{got['label_class']}")
            for i in range(3):
                if not close(got["box_size_xyz"][i], box[i]):
                    fail(f"{vid}/{aid}: box mismatch {got['box_size_xyz']}")
                if not close(got["start_xy_rig"][i % 2], start[i % 2]):
                    fail(f"{vid}/{aid}: start mismatch {got['start_xy_rig']}")
                if i < 2 and not close(got["velocity_xy"][i], vel[i]):
                    fail(f"{vid}/{aid}: velocity mismatch {got['velocity_xy']}")
            if not close(got["activate_s"], act):
                fail(f"{vid}/{aid}: activate_s mismatch {got['activate_s']}")

    # no T-Rex remnants after downgrade
    if verdict == "v07_construction_excavator":
        if "tyrannosaurus" in REPO_YAML.read_text().lower():
            fail("T-Rex spec still present after downgrade")

    # manifest CSV
    with REPO_CSV.open() as f:
        rows = list(csv.reader(f))
    if rows[0] != ["index", "variant_id", "scene_id", "prompt_id",
                   "n_actors", "activate_s", "seed"]:
        fail(f"CSV header mismatch: {rows[0]}")
    if len(rows) - 1 != 11:
        fail(f"CSV expected 11 rows, got {len(rows) - 1}")
    base_scene = doc["base_scene"]
    for i, row in enumerate(rows[1:]):
        if int(row[0]) != i or row[1] != EXPECTED_IDS[i]:
            fail(f"CSV row {i} index/id mismatch")
        if row[2] != base_scene:
            fail(f"CSV row {i} scene not base scene")
        exp_actors = by_id[row[1]]["actors"]
        if int(row[4]) != len(exp_actors):
            fail(f"CSV row {i} n_actors mismatch")
        if int(row[6]) != doc["seed"]:
            fail(f"CSV row {i} seed mismatch")

    if SECRET_RE.search(REPO_YAML.read_text()) or SECRET_RE.search(REPO_CSV.read_text()):
        fail("secret pattern found in generated configs")

    print(f"[INFO] 11 variants validated; v07={verdict}; CSV 11 rows on {base_scene}")
    print("[SUCCESS] Stage2 Task 1.1: 11 variant descriptors generated and schema-valid.")


if __name__ == "__main__":
    main()
