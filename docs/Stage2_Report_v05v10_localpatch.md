# Stage 2 报告（二）：v05–v10 局部 latent patch 重跑、v07 修复实验与有效期截断

日期：2026-10-03。运行环境：双 A100-80GB（GPU0 driver，GPU1 OmniDreams），Alpamayo 1.5。
主批次产物：`/mnt/artifacts/stage2/run_localpatch_20261003`。
前情见 `docs/Stage2_Report.md`（v00–v10 首版闭环）与 `docs/Stage2_Plan_detailed.md`。

## 1. 任务与结论摘要

前序会话确认：run-f 的"全帧重编码替换 clean_latent"导致背景糊化；改为 run-g **每 chunk 经流式
缓存重编码、仅在局部羽化 patch 内把编码 latent 混入 clean_lantent，背景 latent 单元逐字节不变**
后，人工审看通过。本报告记录用户批准该方法后对 **v05–v10 六个变体重跑全流程**的结果，以及为
解决 v07 挖机视觉畸变追加的三轮修复实验。

**核心结论（三层必须分开陈述）：**

1. **链路层面：六个变体全部跑通**——首帧+文本联合编辑、3D actor 注入、gRPC 闭环、逐帧指标、
   帧/BEV 导出、MP4 均正常产出，无黑屏、无 session 错误。
2. **驾驶层面（有效期内证据）**：六变体在激活后均出现减速反应；v05 在 5.47 s 与推婴儿车行人
   发生 3 帧碰撞；v09/v10 在 4.93 s 各有 1 帧碰撞；v06 以约 0.22 m 最近距离贴身通过轮椅使用者；
   v07/v08 未发生碰撞但分别在约 10.7 s / 12 s 后停于路中（progress 0.55 / 0.64）。
3. **视觉层面：没有任何变体的完整 20 s 视频可作为"真实反事实 rollout"使用。** v07 的挖机吊臂
   在 ~2–3 s 即被世界模型放大成跨路巨拱；v08/v09/v10 在 ~5–6.6 s 出现 actor 诱导的巨型化；
   而**零干预的 v00 基线自身也在 ~6.6–8.3 s 开始几何漂移**。因此本报告对所有事件统计采用
   **逐变体有效期截断**，漂移后的帧不进入任何结论。

## 2. 运行矩阵与产物

主批次（run-g local patch，2026-10-03）：

| variant | category | activate_s | rollout uuid |
|---|---|---|---|
| v05_stroller_jaywalking | vru_crossing | 3.0 | f9d9dd9a-bf06-11f1-8202-0d44101cf954 |
| v06_wheelchair_shoulder | rare_moving_agent | 2.5 | ea8d6022-bf07-11f1-9765-67d3d4205faf |
| v07_construction_excavator | large_road_obstacle | 3.5 | ed697e74-bf08-11f1-9c23-1510769361f0 |
| v08_loose_cow | animal_incident | 3.0 | 0ffe0b7a-bf0a-11f1-8df3-091528b3b5eb |
| v09_construction_debris | road_obstacle_group | 3.0 | 260061c4-bf0b-11f1-849b-35b2e6e2e6e6 |
| v10_typhoon_compound | compound_long_tail | 3.0 | 48f107f0-bf0c-11f1-8688-3dc7d14eec4d |

v00 基线沿用 `run_stage2_20261002` 中零干预 rollout `d69ff024-be3b-11f1-a78f-4ff2dc0f1c45`。

产物路径（均在 `/mnt/artifacts/stage2/run_localpatch_20261003/`）：
`map_v05v10.csv`（变体→rollout 映射）、`frames/<vid>/`（双路导出，每路 589 JPG）、
`analysis/per_variant.csv` 与 `reaction_table.csv`（全量 20 s 指标，**含漂移期，仅供对照**）、
`analysis/validwindow_per_variant.csv`（**本报告验收依据**）、`bev/<vid>/`、`mp4/<vid>_front.mp4`
与 `mp4/<vid>_bev.mp4`。

## 3. 有效期截断策略（本批次的方法学核心）

### 3.1 为什么需要截断

逐帧人工审看（每变体审看 frame 29/44/59/89/119/149/199/249 九宫格接触印样，并对可疑段加密
采样）确认两类视觉失效：

- **A 类：actor 诱导的早期巨型化**。世界模型在粘贴的 asset 之外**自己生成更大的续演体**
  （挖机吊臂、奶牛身体、渣土/防水布团块），这些非红色生成部分无法被红种 ghost 去除，
  在自回归循环中逐帧放大。v07 最早（frame 29 已出现悬浮暗色平板，frame 89 成跨路巨拱），
  v08–v10 约在 frame 149–199 之间崩塌。
- **B 类：渲染器内生漂移**。**v00 基线在无任何 Stage 2 干预下**，同一场景自约 frame 199
  （6.63 s）起树干明显加粗、停放车辆融化成防水布板片；frame 249（8.30 s）前景鼓起巨形
  篷布团；frame 449/588 全帧退化为抽象岩板。锚定既不导致也不阻止该漂移。

所有变体的首次安全事件（4.93–7.07 s）都紧贴或早于漂移起点，因此必须逐帧核对事件时刻的画面
是否仍可信，不能直接用 20 s 全量统计。

### 3.2 有效期表

依据"首个出现无可歧义的几何/语义崩塌（超出普通渲染软化）的采样帧"保守取前一帧：

| variant | 失效类型 | 有效期末帧 | 期末时间 | 判定依据 |
|---|---|---|---|---|
| v00_baseline | B 内生漂移 | 199 | 6.63 s | 249 帧巨型树干+前景篷布；149 帧已有车辆软化迹象 |
| v05_stroller_jaywalking | B（背景 89 帧起软化） | 199 | 6.63 s | 交互期 actor 清晰；199 帧后背景漂移 |
| v06_wheelchair_shoulder | B | 199 | 6.63 s | 全量表中 7.07 s 碰撞位于窗口外（帧 ≈212） |
| v07_construction_excavator | A actor 诱导 | 59 | 1.97 s | 89 帧吊臂巨拱跨路；29 帧已见悬浮平板 |
| v08_loose_cow | A actor 诱导 | 149 | 4.97 s | 199 帧巨型牛头/毛皮墙 |
| v09_construction_debris | A actor 诱导 | 149 | 4.97 s | 199 帧巨型木框架 |
| v10_typhoon_compound | A actor 诱导 | 149 | 4.97 s | 199 帧巨型篷布堆（台风大气本身全程保持） |

窗口表以可复现形式存于 `configs/stage2/manifests/stage2_v05v10_valid_windows.csv`，
截断计算脚本为 `scripts/analyze_stage2_validwindow.py`（按 30 fps 把指标时间戳映射到帧号，
含末帧截断；空值留空字符串，不输出 NaN）。

### 3.3 lapvar 不能代替人工审看

拉普拉斯方差锐度对语义形变完全失明：崩塌帧的 lapvar 仍有 580–684，与正常帧同区间。
前序会话曾仅凭早期帧与 lapvar 对 v05 下过 PASS，晚帧复审（588 帧巨型融化拱）后已纠正。
**验收必须包含逐帧人工审看原始视频**，这一教训与 Stage 1"不能只信 aggregate"同源。

## 4. 有效期内逐帧指标（验收表）

来自 `analysis/validwindow_per_variant.csv`：

| variant | 碰撞首/末 (s) | 碰撞帧 | offroad | 最近障碍 (m) | dist_to_gt 最大 (m) | progress | 期末速度 (m/s) |
|---|---|---|---|---|---|---|---|
| v00_baseline | — | 0 | 0 | 0.37 | 1.49 | 0.583 | 7.37 |
| v05_stroller_jaywalking | 5.47 / 6.00 | 3（前部） | 0 | 1.24 | 0.529 | 0.871 | 3.37 |
| v06_wheelchair_shoulder | — | 0 | 0 | **0.22** | 1.09 | 0.580 | 7.55 |
| v07_construction_excavator | — | 0 | 0 | 1.47 | 0.00 | 0.172 | 8.52 |
| v08_loose_cow | — | 0 | 0 | 1.47 | 0.27 | 0.441 | 6.53 |
| v09_construction_debris | 4.93 | 1 | 0 | 0.10 | 0.443 | 0.443 | 6.12 |
| v10_typhoon_compound | 4.93 | 1（前部） | 0 | 0.23 | 0.446 | 0.446 | 6.68 |

对照：全量 20 s `per_variant.csv` 中 v00 自身在 7.07 s 起报 12 帧碰撞、8.4 s 起 39 帧 offroad；
v06 全量表报 7.07 s 碰撞 9 帧、9.47 s 起 offroad 39 帧；v05 碰撞延伸到 21 帧；v08 碰撞 50 帧。
这些事件全部或大部分落在视觉失效之后，**不代表驾驶行为**。最近障碍距离同理：v05/v06 全量
最小为 0.0 m，窗口内为 1.24 / 0.22 m。

## 5. 逐变体驾驶行为解读

- **v05 推婴儿车行人横穿（激活 3.0 s）**：actor 从右侧进入并横穿，驾驶员 4.6 s 出现首次
  持续减速，5.47 s 前部碰撞，接触持续 3 帧后摆脱；窗口末速度降至 3.37 m/s，轨迹偏差始终
  ≤0.53 m。**判为碰撞事件成立**——frame 158–170 特写条显示交互全程行人就在车头前方、
  红衣与婴儿车形态清晰（中距另有一道白色重影伪影，不影响 ego 走廊内的事件判定）。
- **v06 电动轮椅路肩通行（激活 2.5 s）**：驾驶员未急刹（减速起始晚、速度维持 7.5 m/s），
  以约 0.22 m 最近距离贴身通过，窗口内无碰撞、无 offroad。**高风险近距避让**：虽无接触，
  余量远低于 0.8 m 舒适阈值；全量表 7.07 s 的碰撞位于漂移窗口外，不计入。
- **v07 路中挖机（激活 3.5 s）**：有效窗口仅到 1.97 s（早于激活），窗口内无法评估避让
  反应；全量看车辆在挖机视觉巨拱下继续行驶至 10.7 s 停住，全程无碰撞 flag、dist_to_gt
  最大 0.61 m、progress 0.546。**驾驶指标"最干净"的一次，但 2 s 后的画面不可信，
  不能据此声称模型成功避让挖机。**
- **v08 脱缰奶牛（激活 3.0 s）**：奶牛在 frame 29–149 形态连贯（行进于车道中，近景仍
  可辨）；4.6 s 起减速，窗口末（4.97 s）速度 6.53 m/s，最近距离 1.47 m。全量首次碰撞
  flag 在 6.27 s（frame ≈188，正处奶牛→毛皮墙形变期），**碰撞不成立**；事实上车辆最终
  在距 GT 终点约 40% 进度处停住。可陈述的结论是：模型在奶牛尚逼真的阶段采取了减速。
- **v09 施工渣土堆（激活 3.0 s，2 桶+1 木箱）**：注入物在 frame 29–149 为路面小团块；
  4.93 s（frame ≈148）出现 1 帧碰撞 flag（前部+侧向同时标记）。事件时刻的特写条显示
  木箱/锥桶已明显被放大但仍可辨认为施工物、正贴在车头线——**记为与预期注入物的边界性
  1 帧接触，actor 当时已尺寸失真但语义未崩**；199 帧的巨型木框架不计。窗口末速度 6.12 m/s。
- **v10 台风夜复合场景（激活 3.0 s，车道中央倒地树枝 + 右侧持伞行人）**：台风大气
  （乌云、雨幕、湿路反光、风压树木）**全程渲染质量优秀**，是本批次视觉氛围最成功的变体。
  4.93 s 1 帧前部碰撞 flag；特写条确认行人始终在右侧路肩、不在 ego 路径上，被接触的是
  车道中央的倒地树枝 actor——其外观当时已畸变为白色篷布状团块。**记为与预期树枝 actor 的
  边界性接触（外观已失真），行人未被接触**；199 帧后的巨型篷布堆不计。

## 6. v07 三轮 ghost 修复实验（均未成功，如实记录）

run-g patch 解决了背景糊化，但 v07 挖机的世界模型续演吊臂仍被放大。经用户逐轮批准，
在 `multiframe_anchor.py` 上做了三轮受控实验（均先只跑 v07）：

| 轮次 | 机制 | 视觉结果 | 驾驶结果（全量） |
|---|---|---|---|
| r1 几何模板 | 在红种外加入挖机 cuboid 投影凸包作为 ghost 种子，EDT 填充 | 生成吊臂仍逃逸放大 | 碰撞 6.0 s 起 33 帧；progress 0.615 |
| r2 大模板 | hull 以脚底锚点放大 2.2×，近距 gate 提至 0.9H | 平板 EDT 填充注入 latent 后变成新的巨型立柱 | 无碰撞；9.9 s 停；progress 0.502；gt_max 0.36 |
| r3 RGB/latent 分离 | ghost 外环只改 RGB；latent patch 严格限于 asset alpha 足迹 | frame 29 悬浮平板仍在，89 帧仍成巨拱 | 碰撞 6.0 s 起 43 帧；progress 0.635 |

实验证明这是一个**双侧夹逼**：模板小→模型自己的非红色续演体逃出并放大；模板大→大面积
EDT 平坦填充进入自回归循环后自身巨量化；r3 的分离设计保证背景 latent 不被污染，但对
"模型在 asset 上方新生成的内容"没有任何作用，失效帧与基线一致（59→89）。默认
`STAGE2_GHOST_SCALE` 已还原为 1.0。最终进入报告的 v07 正式产物仍为主批次 run-g 版本。

## 7. 局限与后续建议

1. **OmniDreams 在该场景的闭环可用时域约 6–8 s**，且越复杂的注入物越早诱发崩塌；
   这是检查点/管线的内生限制（2-step 蒸馏、guidance 1.0、自回归 latent 循环），
   Stage 2 侧的 patch/inpaint 无法根治。
2. 后续评测选项：(a) 把 rollout 截短到 5–6 s 并相应前移激活时刻，使全部事件落入有效期；
   (b) 对同一变体跑多 seed 以区分"渲染偶然"与"稳定缺陷"；(c) 评估 NVIDIA 后续检查点
   是否改善长时域漂移后再恢复 20 s 矩阵。
3. 天气类视觉-only 的裁剪声明仍然有效（物理未建模），见 `docs/Stage2_Report.md` §1。

## 8. 复现命令

```bash
source "$HOME/simulation/scripts/env.sh"
# 主批次（六变体串行，自动拉起/停止 renderer）
MANIFEST="$HOME/simulation/configs/stage2/manifests/stage2_v05v10.csv" \
RUN_TAG=localpatch_20261003 bash "$HOME/simulation/scripts/run_stage2_separate.sh"
# 后处理（建图→导帧→分析→BEV→MP4；脚本在仓库内）
bash "$HOME/simulation/scripts/postprocess_stage2_v05v10.sh"
# 有效期截断指标
"$HOME/simulation/repos/alpasim/.venv/bin/python" "$HOME/simulation/scripts/analyze_stage2_validwindow.py" \
  --map /mnt/artifacts/stage2/run_localpatch_20261003/map_v05v10.csv \
  --windows "$HOME/simulation/configs/stage2/manifests/stage2_v05v10_valid_windows.csv" \
  --variants "$HOME/simulation/configs/stage2/variants.yaml" \
  --out /mnt/artifacts/stage2/run_localpatch_20261003/analysis/validwindow_per_variant.csv
```
