#!/usr/bin/env python3
"""Stage 2 visualization: per-variant event grid + metric overlay figures
for the v00 baseline + v05-v10 final closed-loop batch.

Outputs (PNG):
  stage2_event_grid.png    4x2 small multiples: ego speed with activation,
                           valid-window shading, collision/offroad events
  stage2_metric_overlay.png  2x2: speed / dist_to_gt / progress overlays +
                           valid-window timeline
Inputs: map_v05v10.csv (variant_id, asl_path), variants.yaml, valid-window
manifest. Run with the alpasim venv (pandas/pyarrow/matplotlib/PIL).
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates  # noqa: F401
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stage2_asl_replay import load_replay

ORDER = [
    "v00_baseline",
    "v05_stroller_jaywalking",
    "v06_wheelchair_shoulder",
    "v07_construction_excavator",
    "v08_loose_cow",
    "v09_construction_debris",
    "v10_typhoon_compound",
]

COLORS = {
    "v00_baseline": "#9aa0a6",
    "v05_stroller_jaywalking": "#e05656",
    "v06_wheelchair_shoulder": "#f0a23b",
    "v07_construction_excavator": "#e0c24b",
    "v08_loose_cow": "#65b86e",
    "v09_construction_debris": "#4fb3d9",
    "v10_typhoon_compound": "#9b7ed8",
}


def short(vid: str) -> str:
    p = vid.split("_", 1)
    return f"{p[0]} {p[1].replace('_', ' ')}" if len(p) > 1 else vid


def load_windows(path: Path) -> dict[str, tuple[int, float]]:
    out = {}
    for row in csv.DictReader(open(path)):
        out[row["variant_id"]] = (
            int(row["valid_end_frame"]), float(row["valid_end_s"]))
    return out


def load_activations(path: Path) -> dict[str, float]:
    import yaml
    spec = yaml.safe_load(Path(path).read_text())
    out = {}
    for v in spec["variants"]:
        act = None
        for a in v.get("actors", []) or []:
            act = a.get("activate_s") if act is None else min(
                act, a.get("activate_s", act))
        if act is not None:
            out[v["id"]] = float(act)
    return out


def metric_series(df: pd.DataFrame, name: str):
    grp = df[df["name"] == name].copy()
    grp["values"] = pd.to_numeric(grp["values"], errors="coerce")
    grp = grp.groupby("timestamps_us", as_index=False)["values"].max()
    grp = grp.sort_values("timestamps_us")
    return grp["timestamps_us"].to_numpy(), grp["values"].to_numpy()


def gather(map_csv: Path, variants_yaml: Path,
           windows_csv: Path) -> dict:
    windows = load_windows(windows_csv)
    activations = load_activations(variants_yaml)
    data = {}
    for row in csv.DictReader(open(map_csv)):
        vid = row["variant_id"]
        if vid not in ORDER:
            continue
        asl = Path(row["asl_path"])
        replay = load_replay(asl, variants_yaml)
        t0 = replay.frames[0][0]

        ts = np.array([f[0] for f in replay.frames])
        speed = np.array([
            np.hypot(replay.ego_at(t).vx, replay.ego_at(t).vy) * 3.6
            for t in ts])
        t_s = (ts - t0) / 1e6

        mdf = pd.read_parquet(asl.parent / "metrics.parquet")
        mts, mval = {}, {}
        for name in ("collision_any", "offroad", "dist_to_gt_trajectory",
                     "progress"):
            a, b = metric_series(mdf, name)
            mts[name] = (a - t0) / 1e6
            mval[name] = b

        wf, ws = windows[vid]
        data[vid] = dict(
            t=t_s, speed=speed,
            mts=mts, mval=mval,
            valid_frame=wf, valid_s=ws,
            activate=activations.get(vid))
    return data


def event_spans(t: np.ndarray, dt: float = 0.267) -> list[tuple[float, float]]:
    return [(float(x) - dt / 2, dt) for x in t]


def fig_event_grid(data: dict, out: Path):
    fig, axes = plt.subplots(4, 2, figsize=(14, 13), sharex=True)
    axes = axes.ravel()
    for k, vid in enumerate(ORDER):
        ax = axes[k]
        d = data[vid]
        ax.plot(d["t"], d["speed"], color=COLORS[vid], lw=1.8)
        ax.axvspan(d["valid_s"], 20, color="#e8a33d", alpha=0.13)
        ax.axvline(d["valid_s"], color="#c98a2b", ls=":", lw=1.1)
        if d["activate"] is not None:
            ax.axvline(d["activate"], color="#3d8f4e", ls="--", lw=1.2)
        col_t = d["mts"]["collision_any"][d["mval"]["collision_any"] > 0]
        off_t = d["mts"]["offroad"][d["mval"]["offroad"] > 0]
        ax.broken_barh(event_spans(col_t), (-4, 70), color="#d5483f",
                       alpha=0.25)
        ax.broken_barh(event_spans(off_t), (-4, 70), color="#3f73a8",
                       alpha=0.18)
        ax.set_ylim(-4, 46)
        ax.set_xlim(0, 20)
        ax.set_title(short(vid), loc="left", fontsize=11,
                     color=COLORS[vid], fontweight="bold")
        ax.grid(alpha=0.2)
    axes[-1].axis("off")

    # legend on the spare axis
    handles = [
        plt.Line2D([], [], color="#666", label="ego speed (km/h)"),
        plt.Line2D([], [], color="#3d8f4e", ls="--", label="actor activation"),
        plt.Line2D([], [], color="#c98a2b", ls=":", label="valid-window end"),
        plt.Rectangle((0, 0), 1, 1, fc="#d5483f", alpha=0.3,
                      label="collision frame"),
        plt.Rectangle((0, 0), 1, 1, fc="#3f73a8", alpha=0.25,
                      label="offroad frame"),
        plt.Rectangle((0, 0), 1, 1, fc="#e8a33d", alpha=0.18,
                      label="post-window (render drift)"),
    ]
    axes[-1].legend(handles=handles, loc="upper left", frameon=False,
                    fontsize=11)
    axes[-1].set_title("Legend", loc="left", fontsize=11)
    fig.suptitle("Stage 2 — v00 baseline + v05–v10 ego speed and safety "
                 "events (Alpamayo 1.5, 20 s rollout)", fontsize=14)
    fig.tight_all = None
    fig.tight_layout(rect=(0, 0, 1, 0.98))
    fig.savefig(out, dpi=130)
    plt.close(fig)
    print(f"wrote {out}")


def fig_overlay(data: dict, out: Path):
    fig, axes = plt.subplots(2, 2, figsize=(14, 9))

    ax = axes[0, 0]
    for vid in ORDER:
        d = data[vid]
        m = d["t"] <= 7.5
        ax.plot(d["t"][m], d["speed"][m], color=COLORS[vid], lw=1.7,
                label=short(vid))
        ax.axvline(d["valid_s"], color=COLORS[vid], ls=":", lw=0.8, alpha=.7)
    ax.set_title("Ego speed (valid regime, ≤7.5 s)")
    ax.set_xlabel("t (s)")
    ax.set_ylabel("km/h")
    ax.grid(alpha=0.2)
    ax.legend(fontsize=8, frameon=False, ncol=2)

    ax = axes[0, 1]
    for vid in ORDER:
        d = data[vid]
        t = d["mts"]["dist_to_gt_trajectory"]
        ax.plot(t, d["mval"]["dist_to_gt_trajectory"], color=COLORS[vid],
                lw=1.6, label=short(vid))
        ax.axvline(d["valid_s"], color=COLORS[vid], ls=":", lw=0.8, alpha=.7)
    ax.set_title("Distance to GT trajectory (full 20 s)")
    ax.set_xlabel("t (s)")
    ax.set_ylabel("m")
    ax.grid(alpha=0.2)

    ax = axes[1, 0]
    for vid in ORDER:
        d = data[vid]
        ax.plot(d["mts"]["progress"], d["mval"]["progress"],
                color=COLORS[vid], lw=1.6, label=short(vid))
        ax.axvline(d["valid_s"], color=COLORS[vid], ls=":", lw=0.8, alpha=.7)
    ax.set_title("Route progress (fraction of base route)")
    ax.set_xlabel("t (s)")
    ax.set_ylim(0, 1.08)
    ax.grid(alpha=0.2)

    # timeline overview
    ax = axes[1, 1]
    for k, vid in enumerate(ORDER):
        d = data[vid]
        ax.barh(k, d["valid_s"], left=0, height=0.5,
                color=COLORS[vid], alpha=0.85)
        col_t = d["mts"]["collision_any"][d["mval"]["collision_any"] > 0]
        if len(col_t):
            ax.scatter(col_t, np.full_like(col_t, k), marker="x",
                       color="#c5392f", s=28, zorder=3)
        if d["activate"] is not None:
            ax.scatter([d["activate"]], [k], marker="|", color="#2f6f3c",
                       s=160, lw=2)
    ax.set_yticks(range(len(ORDER)))
    ax.set_yticklabels([short(v) for v in ORDER], fontsize=9)
    ax.set_xlim(0, 20)
    ax.invert_yaxis()
    ax.set_title("Timeline: valid window (bar), activation (|), collision (×)")
    ax.set_xlabel("t (s)")
    ax.grid(axis="x", alpha=0.2)

    fig.suptitle("Stage 2 metric overlays — dotted lines mark each variant's "
                 "valid-window end", fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(out, dpi=130)
    plt.close(fig)
    print(f"wrote {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", required=True)
    ap.add_argument("--variants", required=True)
    ap.add_argument("--windows", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = gather(Path(args.map), Path(args.variants), Path(args.windows))
    missing = [v for v in ORDER if v not in data]
    if missing:
        raise SystemExit(f"missing rows in map: {missing}")
    fig_event_grid(data, out_dir / "stage2_event_grid.png")
    fig_overlay(data, out_dir / "stage2_metric_overlay.png")


if __name__ == "__main__":
    main()
