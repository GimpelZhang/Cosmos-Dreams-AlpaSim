#!/usr/bin/env python3
"""Stage 2 Task 1.1: generate the authoritative variant descriptors.

Writes:
- configs/stage2/variants.yaml (human-readable authority)
- configs/stage2/manifests/stage2_variants.csv

Built from docs/Stage2_Plan_detailed.md §5.2. If Spike C downgraded v07
(/mnt/artifacts/stage2/spikes/spike_verdict.json), the T-Rex spec is replaced
by the stopped-excavator spec and no T-Rex variant is emitted.

usage: generate_stage2_variants.py --base-scene <id> --seed <int>
       --out-yaml <path> --out-csv <path> [--spike-verdict <path>]
"""
import argparse
import csv
import json
import math
import sys
from pathlib import Path

import yaml

# Stage 1 default prompt, verbatim from
# src/wizard/configs/deploy/external_video_model.yaml (text_prompt_positive)
STAGE1_DEFAULT_POSITIVE = (
    "Wide-angle urban street scene from a low, dashboard-level viewpoint. "
    "A straight two-lane road with a faded center line and curbside parking on both sides. "
    "Parked sedans and SUVs in neutral colors line the curbs. On the right, a white stucco "
    "mid-rise building with blue fabric awnings, rectangular windows, and small storefronts "
    "at street level. On the left, a low commercial strip with dark trim, glass fronts, "
    "signage, and shaded sidewalks. Mature green trees punctuate both sides. Clear blue sky "
    "with sparse soft clouds. Bright midday sunlight, natural colors, realistic materials, "
    "crisp shadows, clean asphalt texture."
)
STAGE1_DEFAULT_NEGATIVE = ""

UNIVERSAL_NEGATIVE = (
    "deformed geometry, distorted road, warped lanes, melted textures, text, watermark, "
    "logo, oversaturated, low-quality, blurry, cartoon, CGI look"
)

ROLLOUT_END_S = 20.0
Y_RANGE = (-8.0, 8.0)


def actor(aid, label_class, box, start_xy, velocity=(0.0, 0.0),
          yaw_deg=None, activate_s=3.0):
    """Build one actor spec. Moving actors' yaw defaults to velocity direction."""
    if yaw_deg is None:
        vx, vy = velocity
        yaw_deg = math.degrees(math.atan2(vy, vx)) if (vx or vy) else 0.0
    return {
        "id": aid,
        "label_class": label_class,
        "box_size_xyz": [float(box[0]), float(box[1]), float(box[2])],
        "start_xy_rig": [float(start_xy[0]), float(start_xy[1])],
        "velocity_xy": [float(velocity[0]), float(velocity[1])],
        "yaw_deg": float(yaw_deg),
        "activate_s": float(activate_s),
    }


def base_variants():
    return [
        {
            "index": 0, "id": "v00_baseline", "category": "baseline",
            "prompt_positive": STAGE1_DEFAULT_POSITIVE,
            "prompt_negative": STAGE1_DEFAULT_NEGATIVE,
            "actors": [],
        },
        {
            "index": 1, "id": "v01_heavy_snow_blizzard", "category": "weather",
            "prompt_positive": (
                "photorealistic dashcam footage, heavy blizzard, dense snowfall with large "
                "snowflakes, snow accumulating on the asphalt road and curbside parked cars, "
                "low visibility about 30 meters, overcast dusk sky, cold muted colors, "
                "realistic road geometry unchanged"),
            "prompt_negative": UNIVERSAL_NEGATIVE,
            "actors": [],
        },
        {
            "index": 2, "id": "v02_torrential_rain_night", "category": "weather_lighting",
            "prompt_positive": (
                "photorealistic dashcam footage, midnight, torrential rain, dense rain "
                "streaks, large wet puddles with bright reflections from streetlights and "
                "headlights, glossy wet asphalt, dark blue night color palette, "
                "realistic road geometry unchanged"),
            "prompt_negative": UNIVERSAL_NEGATIVE,
            "actors": [],
        },
        {
            "index": 3, "id": "v03_dense_fog_dawn", "category": "weather",
            "prompt_positive": (
                "photorealistic dashcam footage, early dawn, extremely dense ground fog, "
                "visibility reduced to about 15 meters, soft glowing halos around vehicle "
                "headlights, pale gray and faint orange tones, realistic road geometry unchanged"),
            "prompt_negative": UNIVERSAL_NEGATIVE,
            "actors": [],
        },
        {
            "index": 4, "id": "v04_blinding_sunset_glare", "category": "lighting",
            "prompt_positive": (
                "photorealistic dashcam footage, golden hour, low sun directly ahead "
                "producing intense blinding glare and large lens flare across the windshield, "
                "strongly washed-out forward scene, long sharp shadows, "
                "realistic road geometry unchanged"),
            "prompt_negative": UNIVERSAL_NEGATIVE,
            "actors": [],
        },
        {
            "index": 5, "id": "v05_stroller_jaywalking", "category": "vru_crossing",
            "prompt_positive": (
                "photorealistic dashcam footage, clear sunny day, a woman pushing a baby "
                "stroller suddenly crossing the street from the right side, casual clothing, "
                "residential two-lane road, sharp shadows, natural colors"),
            "prompt_negative": UNIVERSAL_NEGATIVE,
            "actors": [
                actor("pedestrian_stroller", "pedestrian", (1.4, 0.8, 1.7),
                      (22.0, -3.2), (0.0, 1.2), activate_s=3.0),
            ],
        },
        {
            "index": 6, "id": "v06_wheelchair_shoulder", "category": "rare_moving_agent",
            "prompt_positive": (
                "photorealistic dashcam footage, sunny afternoon, a person riding a powered "
                "wheelchair along the right roadway shoulder, moving in the same direction as "
                "traffic, residential street, natural colors"),
            "prompt_negative": UNIVERSAL_NEGATIVE,
            "actors": [
                actor("powered_wheelchair", "pedestrian", (1.2, 0.75, 1.3),
                      (16.0, -2.2), (2.5, 0.0), activate_s=2.5),
            ],
        },
        # index 7 is replaced per Spike C verdict (see apply_spike_verdict)
        {
            "index": 8, "id": "v08_loose_cow", "category": "animal_incident",
            "prompt_positive": (
                "photorealistic dashcam footage, daytime, an escaped black-and-white dairy "
                "cow standing motionless in the center of the driving lane, realistic fur, "
                "residential street"),
            "prompt_negative": UNIVERSAL_NEGATIVE,
            "actors": [
                actor("dairy_cow", "animal", (2.4, 1.0, 1.6),
                      (26.0, 0.0), activate_s=3.0),
            ],
        },
        {
            "index": 9, "id": "v09_construction_debris", "category": "road_obstacle_group",
            "prompt_positive": (
                "photorealistic dashcam footage, daytime roadworks, fallen wooden crates and "
                "several bright orange traffic barrels scattered across the driving lane, "
                "construction debris, residential street"),
            "prompt_negative": UNIVERSAL_NEGATIVE,
            "actors": [
                actor("barrel_1", "protruding_object", (0.6, 0.6, 0.9),
                      (18.0, -0.6), activate_s=3.0),
                actor("barrel_2", "protruding_object", (0.6, 0.6, 0.9),
                      (19.2, 0.3), activate_s=3.0),
                actor("crate_1", "protruding_object", (1.4, 1.0, 0.9),
                      (20.5, -0.2), activate_s=3.0),
            ],
        },
        {
            "index": 10, "id": "v10_typhoon_compound", "category": "compound_long_tail",
            "prompt_positive": (
                "photorealistic dashcam footage, violent typhoon storm, heavy wind and rain, "
                "a large broken tree branch lying across the road, a pedestrian holding a bent "
                "umbrella fighting the wind near the right curb, dark dramatic sky"),
            "prompt_negative": UNIVERSAL_NEGATIVE,
            "actors": [
                actor("fallen_branch", "protruding_object", (5.0, 0.8, 0.6),
                      (20.0, 0.0), yaw_deg=25.0, activate_s=3.0),
                actor("pedestrian_umbrella", "pedestrian", (0.8, 0.8, 1.75),
                      (24.0, -3.0), (0.0, 0.8), activate_s=3.0),
            ],
        },
    ]


_TREX = {
    "index": 7, "id": "v07_t_rex_ood", "category": "ood_exploratory",
    "prompt_positive": (
        "photorealistic cinematic footage, a massive realistic Tyrannosaurus rex dinosaur "
        "standing in the middle of the road, detailed reptilian skin, roaring, cars parked "
        "along the curbs, clear daylight"),
    "prompt_negative": UNIVERSAL_NEGATIVE,
    "actors": [
        actor("t_rex", "other", (6.0, 2.5, 5.0), (28.0, 0.0), activate_s=3.5),
    ],
}

_EXCAVATOR = {
    "index": 7, "id": "v07_construction_excavator", "category": "large_road_obstacle",
    "prompt_positive": (
        "photorealistic dashcam footage, daytime roadworks, a large stopped excavator "
        "construction vehicle blocking the road, yellow paint, residential street"),
    "prompt_negative": UNIVERSAL_NEGATIVE,
    "actors": [
        actor("stopped_excavator", "other_vehicle", (3.5, 1.5, 3.0),
              (28.0, 0.0), activate_s=3.5),
    ],
}


def apply_spike_verdict(variants, verdict_path):
    verdict = json.loads(Path(verdict_path).read_text())
    v07 = verdict["conclusions"]["v07"]
    if v07 == "v07_t_rex_ood":
        chosen = _TREX
    elif v07 == "v07_construction_excavator":
        chosen = _EXCAVATOR
    else:
        raise ValueError(f"unknown v07 verdict: {v07}")
    variants.append(chosen)
    variants.sort(key=lambda v: v["index"])
    return variants


def trim_crossing_trajectories(variants):
    """Cap actor trajectory windows so y stays within Y_RANGE; warn on trim."""
    warnings = []
    for v in variants:
        for a in v["actors"]:
            vx, vy = a["velocity_xy"]
            if vy == 0.0:
                a["traj_end_s"] = ROLLOUT_END_S
                continue
            y0 = a["start_xy_rig"][1]
            y_limit = Y_RANGE[1] if vy > 0 else Y_RANGE[0]
            t_to_limit = (y_limit - y0) / vy
            t_abs = a["activate_s"] + t_to_limit
            if t_abs < ROLLOUT_END_S:
                a["traj_end_s"] = round(t_abs, 3)
                warnings.append(
                    f"{v['id']}/{a['id']}: trajectory trimmed to t={a['traj_end_s']}s "
                    f"(y would leave {Y_RANGE})")
            else:
                a["traj_end_s"] = ROLLOUT_END_S
    return warnings


def validate(variants):
    if len(variants) != 11:
        raise AssertionError(f"expected 11 variants, got {len(variants)}")
    ids = [v["id"] for v in variants]
    if len(set(ids)) != 11:
        raise AssertionError("variant ids not unique")
    for v in variants:
        if not v["prompt_positive"]:
            raise AssertionError(f"{v['id']}: empty positive prompt")
        act_times = [a["activate_s"] for a in v["actors"]]
        if any(not (2.5 <= t <= 10) for t in act_times):
            raise AssertionError(f"{v['id']}: activation outside [2.5, 10]s")
        for a in v["actors"]:
            box = a["box_size_xyz"]
            if any(not (0 < d <= 8) for d in box):
                raise AssertionError(f"{v['id']}/{a['id']}: box dims invalid: {box}")
            speed = math.hypot(*a["velocity_xy"])
            if speed > 5 + 1e-9:
                raise AssertionError(f"{v['id']}/{a['id']}: speed {speed} > 5 m/s")
            if a["traj_end_s"] < a["activate_s"]:
                raise AssertionError(f"{v['id']}/{a['id']}: end before activation")
            y_end_motion = (a["start_xy_rig"][1] +
                            a["velocity_xy"][1] * (a["traj_end_s"] - a["activate_s"]))
            if not (Y_RANGE[0] - 1e-6 <= y_end_motion <= Y_RANGE[1] + 1e-6):
                raise AssertionError(
                    f"{v['id']}/{a['id']}: trimmed end y={y_end_motion} out of range")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-scene", required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--out-yaml", required=True)
    ap.add_argument("--out-csv", required=True)
    ap.add_argument("--spike-verdict",
                    default="/mnt/artifacts/stage2/spikes/spike_verdict.json")
    args = ap.parse_args()

    variants = base_variants()
    variants = apply_spike_verdict(variants, args.spike_verdict)
    warnings = trim_crossing_trajectories(variants)
    for w in warnings:
        print(f"[WARN] {w}", file=sys.stderr)
    validate(variants)

    out_yaml = Path(args.out_yaml)
    out_csv = Path(args.out_csv)
    out_yaml.parent.mkdir(parents=True, exist_ok=True)
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    doc = {
        "seed": args.seed,
        "base_scene": args.base_scene,
        "universal_negative_prompt": UNIVERSAL_NEGATIVE,
        "coordinate_convention": (
            "actor coordinates in ego start rig frame: x forward, y left (z up); "
            "patch transforms to clip global via rig GT trajectory at render start"),
        "variants": variants,
    }
    with out_yaml.open("w") as f:
        yaml.safe_dump(doc, f, sort_keys=False, allow_unicode=True, width=1000)

    with out_csv.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["index", "variant_id", "scene_id", "prompt_id",
                    "n_actors", "activate_s", "seed"])
        for v in variants:
            act = [a["activate_s"] for a in v["actors"]]
            w.writerow([
                v["index"], v["id"], args.base_scene, v["id"],
                len(v["actors"]), min(act) if act else "", args.seed,
            ])

    print(f"[INFO] wrote {out_yaml} and {out_csv} ({len(variants)} variants)")


if __name__ == "__main__":
    main()
