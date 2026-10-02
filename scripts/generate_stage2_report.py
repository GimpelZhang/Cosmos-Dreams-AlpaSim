#!/usr/bin/env python3
"""Stage 2 Task 5.2: generate docs/Stage2_Report.md and the HTML board.

Inputs: per_variant.csv, reaction_table.csv, variants.yaml. The report
contains the scope/scope-cut statement, paper-vs-implementation comparison,
the 11-variant result table, per-variant factual narratives, the weather
vision-only note, the OOD spike conclusion, pass rate, reproduction commands
and open issues. The HTML board is a single inline-CSS file with the table
and <video controls> referencing ../videos/*.mp4.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import yaml

PASS_RATE_DENOM_NOTE = "通过率分母 = 11 - SYSTEM_ERROR - 1(baseline)"


def fmt(v):
    return "" if v is None else str(v)


def fnum(v, digits=2):
    """Format a CSV numeric string with fixed digits; '' stays ''."""
    if v is None or str(v) == "":
        return ""
    return f"{float(v):.{digits}f}"


def has_frames(v):
    return bool(str(v).strip()) and int(float(v)) > 0


def narrative(r: dict) -> str:
    sentences = []

    if r["activate_s"]:
        sentences.append(f"Injected content activates at t={fnum(r['activate_s'], 1)}s.")
    if r["first_decel_s"]:
        sentences.append(
            f"First observable deceleration begins at t={fnum(r['first_decel_s'])}s, "
            f"peak deceleration {abs(float(r['max_decel_mps2'])):.2f} m/s^2.")
    else:
        sentences.append("No sustained deceleration is observed.")

    if has_frames(r["collision_frames"]):
        parts = []
        for key, label in (("collision_front_first_s", "front"),
                           ("collision_rear_first_s", "rear"),
                           ("collision_lateral_first_s", "lateral")):
            if r[key]:
                parts.append(f"{label} from t={fnum(r[key])}s")
        parts_text = (" (" + ", ".join(parts) + ")") if parts else ""
        sentences.append(
            f"Collision spans {int(float(r['collision_frames']))} frames, first at "
            f"t={fnum(r['collision_first_s'])}s{parts_text}.")
    else:
        sentences.append("No collision is recorded.")

    if has_frames(r["offroad_frames"]):
        sentences.append(
            f"Offroad spans {int(float(r['offroad_frames']))} frames, first at "
            f"t={fnum(r['offroad_first_s'])}s.")
    else:
        sentences.append("No offroad is recorded.")
    if r["activate_s"]:
        sentences.append(
            f"Post-activation minimum obstacle distance is "
            f"{fnum(r['obs_dist_after_activation_min_m'])} m.")
    sentences.append(
        f"Maximum trajectory deviation is {fnum(r['dist_to_gt_max_m'])} m, "
        f"final speed {fnum(r['final_speed_mps'])} m/s.")
    if r["derived_ttc_min_s"]:
        sentences.append(
            f"Derived minimum TTC is {fnum(r['derived_ttc_min_s'])} s (derived, "
            "not a native metric).")
    return " ".join(sentences)


def build_md(records, reaction_rows, spec, spike_verdict: str | None) -> str:
    by_id = {r["variant_id"]: r for r in records}
    pass_n = sum(1 for r in records if r["verdict"] == "PASS")
    error_n = sum(1 for r in records if r["verdict"] == "SYSTEM_ERROR")
    denom = 11 - error_n - 1
    rate = f"{pass_n}/{denom}" if denom > 0 else "n/a"

    lines = []
    lines.append("# Stage 2 报告：反事实长尾场景变体闭环仿真与可视化评测")
    lines.append("")
    lines.append("日期：2026-10-02。运行环境：双 A100-80GB（GPU0 driver，GPU1 "
                 "OmniDreams），Alpamayo 1.5。")
    lines.append("")

    lines.append("## 1. 范围与裁剪声明")
    lines.append("")
    lines.append("Stage 2 基于 OmniDreams 论文（arXiv:2606.03159）§9.3 的反事实变体思路，"
                 "在同一 base 场景 "
                 "`clipgt-02eadd92-02f1-46d8-86fe-a9e338fed0b6` 上构建 10 个变体并闭环运行 "
                 "20 s。本阶段实现并验证的能力是：**per-rollout 文本 prompt 改写**与"
                 "**3D 合成交通对象轨迹注入**（hdmap 拓扑保持不变）。")
    lines.append("")
    lines.append("明确裁剪（系统不具备、不在本阶段声称）：")
    lines.append("- 天气/光照变体是**视觉-only**：只改变渲染外观，**物理未建模**——不改变路面摩擦、"
                 "不改变传感器探测距离，不模拟打滑/积水的动力学效应；")
    lines.append("- 不改变 traffic sim 的其他参与者行为逻辑；")
    lines.append("- OOD 奇幻实体（T-Rex）仅作为 spike 探索项保留，不进入正式矩阵。")
    lines.append("")

    lines.append("## 2. 论文 vs 实现对照")
    lines.append("")
    lines.append("| 论文要点 | 实现情况 |")
    lines.append("|---|---|")
    lines.append("| §9.3.1 prompt 驱动场景编辑 | 已实现：per-rollout positive/negative prompt，"
                 "11 次独立串行 wizard 注入 |")
    lines.append("| §9.3.2 OOD 实体注入 | 已实现：合成 TrafficObject（AABB + 全窗轨迹）合并链路；"
                 "T-Rex spike 评分为 0，正式矩阵降级为施工挖掘机 |")
    lines.append("| HDMap 拓扑一致 | 已保持：map 数据取自 ASL 内 hdmap zip，不依赖外部产物 |")
    lines.append("| Camera + BEV 轨迹重叠可视化 | 已实现：30fps HUD 增强版，BEV 含当帧预测轨迹黄线 |")
    lines.append("")

    lines.append("## 3. 11 变体结果总表")
    lines.append("")
    # (key, header, digits); digits=None means no numeric formatting.
    cols = [("variant_id", "ID", None), ("category", "类别", None),
            ("activate_s", "激活(s)", 1), ("first_decel_s", "首减速(s)", 2),
            ("collision_first_s", "碰撞首(s)", 2), ("collision_frames", "碰撞帧", 0),
            ("offroad_first_s", "下路首(s)", 2), ("offroad_frames", "下路帧", 0),
            ("obs_dist_after_activation_min_m", "激活后最近(m)", 2),
            ("dist_to_gt_max_m", "轨迹偏差最大(m)", 2),
            ("final_speed_mps", "末速(m/s)", 2), ("verdict", "结论", None)]
    lines.append("| " + " | ".join(c[1] for c in cols) + " |")
    lines.append("|" + "---|" * len(cols))
    for r in records:
        cells = []
        for key, _, digits in cols:
            v = fmt(r[key])
            if digits is not None and v != "":
                v = f"{float(v):.{digits}f}"
            cells.append(v)
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")

    lines.append("## 4. 每变体事实描述")
    lines.append("")
    for r in records:
        lines.append(f"### {r['variant_id']}")
        lines.append(narrative(r))
        lines.append("")

    lines.append("## 5. 天气变体标注")
    lines.append("")
    lines.append("v01–v04 均为**视觉-only 条件，物理未建模**。速度变化是策略对画面的反应观察，"
                 "不反应不判 FAIL。天气变体的碰撞首时（6.8–7.07s）与 v00（7.07s）基本重合，"
                 "属于继承自 baseline 的同一碰撞事件而非天气新诱发；天气改变的是渲染外观与事件后的帧计数。")
    for r in records:
        if r["speed_change_0_8s_mps"]:
            lines.append(f"- {r['variant_id']}：0–8s 速度变化 "
                         f"{r['speed_change_0_8s_mps']} m/s。")
    lines.append("")

    lines.append("## 6. OOD spike 结论")
    lines.append("")
    lines.append("T-Rex（OTHER 6.0×2.5×5.0 box + prompt）逐帧 rubric 评分全为 0，"
                 "正式矩阵 v07 按风险预案降级为静止施工挖掘机（3.5×1.5×3.0）；"
                 "T-Rex 仅保留 spike 记录。对照之下，cow box 注入评分为 1，"
                 "证明注入链路本身有效。")
    if spike_verdict:
        lines.append(f"详见 `{spike_verdict}`。")
    lines.append("")

    lines.append("## 7. 通过率与结论")
    lines.append("")
    lines.append(f"总通过率 = {rate}（{PASS_RATE_DENOM_NOTE}）。")
    lines.append("")
    lines.append("**模型表现 ≠ 系统正常**：11 个 rollout 完整、prompt/actor/seed 证据一致，"
                 "即系统成功；驾驶策略通过率是另一回事。所有结论均基于逐帧 metrics.parquet 与 "
                 "HUD/原始视频，不引用 aggregate metrics_results.txt。")
    lines.append("")

    lines.append("## 8. 复现命令")
    lines.append("")
    lines.append("```bash")
    lines.append("source \"$HOME/simulation/scripts/env.sh\"")
    lines.append("sg docker -c 'bash scripts/run_stage2_separate.sh'")
    lines.append("repos/alpasim/.venv/bin/python scripts/check_stage2_task33.py")
    lines.append("bash scripts/export_stage2_frames.sh")
    lines.append("repos/alpasim/.venv/bin/python scripts/check_stage2_task41.py")
    lines.append("repos/alpasim/.venv/bin/python scripts/render_stage2_bev.py unused \\")
    lines.append("  --variants configs/stage2/variants.yaml \\")
    lines.append("  --map /mnt/artifacts/stage2/rollout_variant_map.csv")
    lines.append("repos/alpasim/.venv/bin/python scripts/check_stage2_task42.py")
    lines.append("repos/alpasim/.venv/bin/python scripts/render_stage2_hud.py \\")
    lines.append("  --map /mnt/artifacts/stage2/rollout_variant_map.csv \\")
    lines.append("  --variants configs/stage2/variants.yaml")
    lines.append("repos/alpasim/.venv/bin/python scripts/check_stage2_task43.py")
    lines.append("repos/alpasim/.venv/bin/python scripts/analyze_stage2.py \\")
    lines.append("  /mnt/artifacts/stage2/run_stage2_20261002 \\")
    lines.append("  --variants configs/stage2/variants.yaml --out-dir /mnt/artifacts/stage2/report")
    lines.append("```")
    lines.append("")

    lines.append("## 9. 遗留开放问题")
    lines.append("")
    lines.append("- 天气的物理效应（摩擦/传感器距离）未建模，需要交通/物理层扩展而非渲染层；")
    lines.append("- `min_distance_to_obstacle_m` 计入路边静止停放车辆，正常超车经过时也会出现"
                 "很小读数，HUD 的 STATE 灯因此可能在无真实威胁时进入 EMERGENCY；")
    lines.append("- 注入对象的视觉一致性依赖渲染器对合成对象的泛化，cow/excavator 已验证，"
                 "更多类别需逐项 rubric 评分。")
    lines.append("")
    return "\n".join(lines)


CSS = """
body{font-family:Arial,sans-serif;margin:24px;background:#f7f7f7;color:#222}
h1{font-size:22px} table{border-collapse:collapse;margin:12px 0;background:#fff}
th,td{border:1px solid #ccc;padding:6px 10px;font-size:13px;text-align:left}
th{background:#eee} video{width:300px} .pass{color:#1a7f37;font-weight:bold}
.fail{color:#c0392b;font-weight:bold} .note{background:#fff4e5;padding:10px;border-radius:6px}
"""


def build_html(records) -> str:
    cells = ["variant_id", "category", "collision_frames", "offroad_frames",
             "obs_dist_after_activation_min_m", "dist_to_gt_max_m", "verdict"]
    parts = ["<!doctype html><html><head><meta charset='utf-8'>",
             "<title>Stage2 Board</title>", f"<style>{CSS}</style></head><body>",
             "<h1>Stage 2 - Counterfactual Long-tail Variants</h1>",
             "<p class='note'>Weather variants are <b>vision-only: physics is "
             "not modeled</b>. All evidence comes from per-frame metrics + videos.</p>",
             "<table><tr><th>HUD video</th>" +
             "".join(f"<th>{c}</th>" for c in cells) + "</tr>"]
    for r in records:
        vid = r["variant_id"]
        cls = r["verdict"].lower()
        parts.append("<tr>"
                     f"<td><video controls preload='none' "
                     f"src='../videos/{vid}_hud.mp4'></video></td>")
        for c in cells:
            v = fmt(r[c])
            if c == "verdict" and v in ("PASS", "FAIL"):
                parts.append(f"<td class='{cls}'>{v}</td>")
            else:
                parts.append(f"<td>{v}</td>")
        parts.append("</tr>")
    parts.append("</table></body></html>")
    return "".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per", default="/mnt/artifacts/stage2/report/per_variant.csv")
    ap.add_argument("--reaction", default="/mnt/artifacts/stage2/report/reaction_table.csv")
    ap.add_argument("--variants", default="configs/stage2/variants.yaml")
    ap.add_argument("--out-md", default="docs/Stage2_Report.md")
    ap.add_argument("--out-html", default="/mnt/artifacts/stage2/report/index.html")
    args = ap.parse_args()

    records = list(csv.DictReader(open(args.per)))
    reaction_rows = list(csv.DictReader(open(args.reaction)))
    spec = yaml.safe_load(Path(args.variants).read_text())

    Path(args.out_md).write_text(
        build_md(records, reaction_rows, spec,
                 "/mnt/artifacts/stage2/spikes/spike_verdict.json"))
    Path(args.out_html).write_text(build_html(records))
    print(f"wrote {args.out_md} and {args.out_html}")


if __name__ == "__main__":
    main()
