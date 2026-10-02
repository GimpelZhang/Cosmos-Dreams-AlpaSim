"""Shared ASL replay reader for Stage 2 offline tooling.

Loads one rollout ASL and exposes everything the BEV renderer, the HUD
composer and the per-frame analysis need:

- session prompt/seed and the self-contained hdmap zip (20 parquets)
- camera frames: (frame_start_us, logical_id, jpeg bytes)
- ego states at control steps: pose in the ego-start rig/map frame plus
  linear velocity and yaw rate (controller_return.states)
- actor tracks: pose per broadcast timestamp (actor_poses)
- object definitions with AABB sizes (traffic_session_request), injected
  actors ("s2_" ids) sized from variants.yaml
- driver predicted trajectories (driver_return.trajectory), same map frame

Coordinate frame (configs/stage2/variants.yaml): x forward, y left, z up,
origin at ego start.
"""
from __future__ import annotations

import asyncio
import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from alpasim_utils.logs import async_read_pb_log

try:
    import yaml
except ImportError:  # pragma: no cover - pyyaml is always present via Hydra
    yaml = None


@dataclass
class EgoState:
    ts_us: int
    x: float
    y: float
    qx: float
    qy: float
    qz: float
    qw: float
    vx: float
    vy: float
    yaw_rate: float


@dataclass
class ObjectDef:
    object_id: str
    size_x: float
    size_y: float
    size_z: float
    is_static: bool
    label_class: str

    @property
    def injected(self) -> bool:
        return self.object_id.startswith("s2_")


@dataclass
class Replay:
    asl_path: Path
    prompt_positive: str
    prompt_negative: str
    session_seed: int
    hdmap_zip: bytes
    frames: list[tuple[int, int, bytes]]
    ego: list[EgoState]
    actor_ts: list[int]
    actor_tracks: dict[str, list[tuple[int, float, float, float, float, float, float, float]]]
    objects: dict[str, ObjectDef]
    predictions: list[tuple[int, list[tuple[int, float, float, float, float, float, float, float]]]]

    def ego_at(self, ts_us: int) -> EgoState:
        return _nearest(self.ego, ts_us, key=lambda e: e.ts_us)

    def actor_pose_at(self, object_id: str, ts_us: int):
        track = self.actor_tracks.get(object_id)
        if not track:
            return None
        return _nearest(track, ts_us, key=lambda p: p[0])

    def hdmap_zipfile(self) -> zipfile.ZipFile:
        return zipfile.ZipFile(io.BytesIO(self.hdmap_zip))


def _nearest(items, ts_us, key):
    best = items[0]
    best_d = abs(key(best) - ts_us)
    for item in items[1:]:
        d = abs(key(item) - ts_us)
        if d < best_d:
            best, best_d = item, d
    return best


async def _read(asl_path: Path) -> Replay:
    prompt_positive = ""
    prompt_negative = ""
    session_seed = 0
    hdmap_zip = b""
    frames: list[tuple[int, int, bytes]] = []
    ego: list[EgoState] = []
    actor_ts: list[int] = []
    actor_tracks: dict[str, list[tuple]] = {}
    objects: dict[str, ObjectDef] = {}
    predictions: list[tuple[int, list[tuple]]] = []

    async for le in async_read_pb_log(str(asl_path)):
        which = le.WhichOneof("log_entry")

        if which == "video_model_session_request":
            sr = le.video_model_session_request
            prompt_positive = sr.text_prompt.positive
            prompt_negative = sr.text_prompt.negative
            session_seed = sr.random_seed
            hdmap_zip = sr.static_world_map.hdmap_parquets

        elif which == "driver_camera_image":
            ci = le.driver_camera_image.camera_image
            frames.append((ci.frame_start_us, le.driver_camera_image.camera_image.logical_id,
                           ci.image_bytes))

        elif which == "controller_return" and le.controller_return.states:
            s = le.controller_return.states[0]
            v = s.pose_local_to_rig.vec
            q = s.pose_local_to_rig.quat
            d = s.dynamic_state
            ego.append(EgoState(
                s.timestamp_us, v.x, v.y, q.x, q.y, q.z, q.w,
                d.linear_velocity.x, d.linear_velocity.y, d.angular_velocity.z))

        elif which == "actor_poses":
            ap = le.actor_poses
            actor_ts.append(ap.timestamp_us)
            for a in ap.actor_poses:
                p = a.actor_pose
                actor_tracks.setdefault(a.actor_id, []).append(
                    (ap.timestamp_us, p.vec.x, p.vec.y, p.vec.z,
                     p.quat.x, p.quat.y, p.quat.z, p.quat.w))

        elif which == "traffic_session_request":
            for obj in le.traffic_session_request.logged_object_trajectories:
                objects[obj.object_id] = ObjectDef(
                    obj.object_id, obj.aabb.size_x, obj.aabb.size_y, obj.aabb.size_z,
                    obj.is_static, obj.label_class)

        elif which == "driver_return":
            poses = [(pa.timestamp_us, pa.pose.vec.x, pa.pose.vec.y, pa.pose.vec.z,
                      pa.pose.quat.x, pa.pose.quat.y, pa.pose.quat.z, pa.pose.quat.w)
                     for pa in le.driver_return.trajectory.poses]
            predictions.append((le.driver_return.trajectory.poses[0].timestamp_us if poses else 0,
                                poses))

    actor_ts.sort()
    for track in actor_tracks.values():
        track.sort(key=lambda p: p[0])
    ego.sort(key=lambda e: e.ts_us)
    frames.sort(key=lambda f: f[0])
    predictions.sort(key=lambda p: p[0])

    return Replay(
        asl_path, prompt_positive, prompt_negative, session_seed, hdmap_zip,
        frames, ego, actor_ts, actor_tracks, objects, predictions)


def load_replay(asl_path: str | Path, variants_spec: str | Path | None = None) -> Replay:
    """Load a rollout ASL; injected-object sizes are filled from the spec."""
    asl_path = Path(asl_path)
    replay = asyncio.run(_read(asl_path))

    if variants_spec is not None:
        if yaml is None:
            raise RuntimeError("pyyaml required to read the variants spec")
        spec = yaml.safe_load(Path(variants_spec).read_text())
        sizes = {}
        for variant in spec.get("variants", []):
            for actor in variant.get("actors", []):
                sx, sy, sz = actor["box_size_xyz"]
                sizes["s2_" + actor["id"]] = (sx, sy, sz, actor.get("label_class", ""))
        for object_id, (sx, sy, sz, label) in sizes.items():
            if object_id in replay.actor_tracks and object_id not in replay.objects:
                replay.objects[object_id] = ObjectDef(object_id, sx, sy, sz, True, label)

    return replay
