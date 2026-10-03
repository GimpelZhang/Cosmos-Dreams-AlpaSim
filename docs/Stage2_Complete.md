# Stage 2 完成全记录：反事实长尾变体闭环仿真——目标、方案、踩坑与妥协

日期：2026-10-03 整理。
时间跨度：2026-10-02（首批 11 变体）→ 2026-10-03（局部 latent patch 六变体重跑、有效期截断、报告归档）。
运行环境：无头服务器 ubuntu2，双 A100-SXM4-80GB（GPU0 = Alpamayo 1.5 driver，GPU1 = OmniDreams renderer）。
前序文档：`docs/Stage1_Complete_1.md`（系统从零搭建到闭环）、`docs/Stage2_Plan_detailed.md`（超详细计划）、
`docs/Stage2_Report.md`（v00–v10 首版报告）、`docs/Stage2_Report_v05v10_localpatch.md`（最终验收报告）。
本文是 Stage 2 的**总结性文档**：我们要完成什么、论文怎么做、我们怎么做、踩了哪些坑、妥协与最终效果之间的因果关系。

---

## 0. 一页纸结论

1. **任务**：在**同一个 base 场景**上复现 OmniDreams 论文（arXiv:2606.03159）§9.3 的反事实长尾变体——
   1 个基线 + 10 个变体（天气/光照 ×4、弱势交通参与者 ×2、大型障碍 ×1、动物 ×1、障碍组 ×1、台风复合 ×1），
   Alpamayo 1.5 做驾驶员，每个闭环 20 s，hdmap 拓扑不变。
2. **论文给了两个"手柄"**：① prompt + 首帧条件，**无需重训**即可做分布内的环境/外观编辑（§9.3.1）；
   ② **轻量定向 finetuning（post-training）**，用于"罕见动物、超大物体、罕见障碍物"等 OOD 对象（§9.3.2）。
   §9.3.2 明确记载：朴素地往首帧 RGB 里贴 OOD 物体而不更新权重，会产生 artifacts 和不一致动力学，
   论文的解法是 post-train 一个 **randomized dynamic-cuboid dropout** 变体。
3. **我们的约束**：本地权重是 NVIDIA 公开的**蒸馏 2B checkpoint**，计划阶段就把"改权重/post-training"
   列为 Out-of-Scope。因此我们选择用**条件信号一致性工程**替代权重更新：RGB cutout + 匹配的 3D cuboid +
   全窗轨迹 + 文本 prompt 四者一致，再辅以多帧重贴、红种/EDT 残像清除和**局部 latent patch**。
4. **最终结果（必须分三层陈述）**：
   - **链路层：全部成功**。首帧联合编辑、3D 演员注入、gRPC 闭环、逐帧指标、帧/BEV 导出、MP4 全部正常；
   - **天气/光照层（v01–v04）：视觉外观成功**（暴雪/夜雨/浓雾/眩光全部可见且全程保持），
     但属于**视觉-only，物理未建模**；策略没有对恶化视距做出可信的安全响应；
   - **演员层（v05–v10）：有效期内可用、20 s 整体不可用**。所有变体在激活后出现减速等驾驶反应；
     v05 碰撞成立（5.47–6.00 s ×3 帧）、v06 以 0.22 m 贴身通过、v09/v10 各 1 帧边界接触；
     但世界模型会在粘贴素材之外**自造放大的续演体**并自回归巨型化，且**零干预基线自身在 ~6.6–8.3 s
     后内生几何漂移**。因此对每个变体做**有效期截断**，漂移后帧不进入任何结论。
5. **核心因果判断**：v05–v10 的局限不是工程失误，而是"**冻结权重、用信号一致性替代论文的权重更新**"
   这一选择的**直接、预期代价**。论文 §9.3.2 的 OOD 能力本质上写在 post-trained 权重里
   （模型学会在没有 dynamic cuboid 时从视觉历史/首帧种子/场景上下文推断物体持续性）；
   我们消除了论文批评的 RGB/结构错配，但无法凭空赋予权重这种推断能力。

---

## 1. Stage 2 要完成什么

### 1.1 论文背景与任务定义

OmniDreams 是 action-conditioned 的生成式世界模型（基于 NVIDIA Cosmos-Predict2.5，2B 参数），
在闭环里根据驾驶动作生成下一帧画面。论文 §9.3 "Long-tail Coverage and Counterfactual Variations"
把仿真器当作"**数据引擎**"：在同一场景上批量生成罕见/不可能场景的反事实变体，用于平衡训练与评测集。
论文给出获得长尾场景的两条路径：

- prompt 和首帧条件 *"enables a single trained checkpoint to generate broad variations in
  environments, agents, and ego behavior without retraining"*（单一 checkpoint、无需重训）；
- *"lightweight targeted finetuning steers the model toward distributions"* that are
  *"otherwise too rare to emerge from natural data sampling"*（轻量定向 finetuning）。

Stage 2 的任务就是在**本地部署条件下**把这一节落地：不只是让画面变天，还要让闭环驾驶策略
对注入的长尾情境做出可观测、可量化的反应，并产出可播放、可核对的视频与逐帧指标。

### 1.2 场景、变体矩阵与硬约束

- **Base 场景**（所有变体共用）：`clipgt-02eadd92-02f1-46d8-86fe-a9e338fed0b6`，
  白天晴朗的城市居民街：直向两车道、两侧路侧停车、行道树、右侧白色多层建筑与蓝色雨棚。
  选它是因为几何简单、直线行驶、参照物丰富，便于发现渲染畸变。
- **驾驶员**：Alpamayo 1.5；**渲染器**：OmniDreams gRPC 服务（GPU1，`:50051`）。
- **部署 checkpoint**：`2b_res720p_30fps_i2v_hdmap_distilled.pt`——**2-step 蒸馏 FlowMatch、
  guidance_scale = 1.0（无 CFG）**、chunk-2 latent、30 fps、1280×704（704p）、589 导出帧（≈19.6 s）。
  这一 checkpoint 事实是 Stage 2 几乎所有"模型不听话"问题的根源，务必牢记。

变体矩阵（`configs/stage2/variants.yaml`）：

| ID | 类别 | 干预内容 | 演员（rig 坐标，激活时刻，x 前/y 左） |
|---|---|---|---|
| v00 | baseline | 无干预 | — |
| v01 | weather | 暴雪、能见度 ~30 m（编辑首帧+prompt） | — |
| v02 | weather/lighting | 深夜暴雨、积水反光（编辑首帧+prompt） | — |
| v03 | weather | 黎明浓雾、能见度 ~15 m（编辑首帧+prompt） | — |
| v04 | lighting | 日落强眩光、长阴影（编辑首帧+prompt） | — |
| v05 | vru_crossing | 推婴儿车行人从右侧横穿 | pedestrian_stroller，箱 1.4×0.8×1.7，@ (22, −3.2)，横穿速度 |
| v06 | rare_moving_agent | 电动轮椅沿路肩同向行驶 | powered_wheelchair，箱 1.2×0.75×1.3，@ (16, −2.2)，同向速度 |
| v07 | large_road_obstacle | 路中静止施工挖掘机 | stopped_excavator，箱 3.5×1.5×3.0，@ (28, 0)，静止 |
| v08 | animal_incident | 脱缰奶牛进入车道 | dairy_cow |
| v09 | road_obstacle_group | 施工渣土：2 桶 + 1 木箱 | barrel_1 0.6³ @ (18, −0.6)；barrel_2 @ (19.2, 0.3)；crate_1 1.4×1.0×0.9 @ (20.5, −0.2) |
| v10 | compound_long_tail | 台风暴雨（编辑首帧）+ 倒地树枝 + 持伞行人 | fallen_branch 5.0×0.8×0.6，yaw 25°，@ (20, 0)；pedestrian_umbrella 0.8×0.8×1.75，@ (24, −3)，vy +0.8 |

**硬约束（贯穿 Stage 2 全程）**：

1. 不改 OmniDreams 模型权重、不做 post-training（计划文档 Out-of-Scope 第 6 条）；
2. hdmap 拓扑保持不变：不增删车道、不改路缘，只注入合成对象与外观；
3. 所有重型下载/构建落 `/mnt`；凭据只从 `~/access` 引用；不 commit/push 除非用户明确要求；
4. renderer 严格单 session 串行（每次 `start_session` 会清掉旧 session），任务结束 GPU 显存回到 ~14 MiB。

### 1.3 明确的裁剪声明（不声称系统不具备的能力）

- 天气/光照变体是**视觉-only**：只改渲染外观，**物理未建模**——不改路面摩擦、不改传感器探测距离、
  不模拟打滑/积水动力学。"暴雪"里刹车距离不会变长，"浓雾"不会影响感知模块，驾驶员看到的只是一张雾蒙蒙的图。
- 不改 traffic sim 中其他参与者的行为逻辑；
- OOD 奇幻实体（T-Rex spike）只作探索，不进正式矩阵（v07 按预案降级为挖掘机）。

---

## 2. 论文方法对照：§9.3.1 与 §9.3.2 分别怎么做

理解 Stage 2 的全部妥协，必须先准确理解论文这两节的边界。以下引文均按论文（ar5iv 镜像核对）逐字摘录关键短句。

### 2.1 §9.3.1 Controllable Scenario Editing——两个通道 + 首帧锚定

论文把可控编辑拆成两个通道：

- *"the text prompt, which controls environmental attributes such as weather, lighting,
  and time of day"*（文本 prompt 控制天气、光照、时段等环境属性）；
- *"abstract world-scenario map (lane geometry and bounding boxes), which controls
  scene structure and agent layout"*（抽象世界场景图——车道几何与 bounding boxes——
  控制场景结构与 agent 布局）。

首帧 RGB seed 锚定编辑；未被编辑的道路几何、静态结构与远景 *"remain visually stable"*。
**关键：这一节的能力对应"手柄一"，不需要重训，是分布内的外观/布局编辑。**

### 2.2 §9.3.2 Out-of-Distribution Object Modeling——朴素 RGB 注入为何失败

论文针对的目标场景包括 *"sudden appearance of uncommon animals, oversized objects,
or other rare obstacles"*（罕见动物、超大物体、其他罕见障碍物的突然出现）——注意，
这正是我们 v07–v10 想做的事。

论文先描述"朴素方法"（naive method）：*"directly edit the first-frame RGB image by
inserting out-of-distribution objects and then generate the full video"*
（直接编辑首帧 RGB、插入 OOD 物体，然后生成整段视频）。然后明确否定：

- *"this often leads to visual artifacts and inconsistent dynamics"*
  （常导致视觉 artifacts 与不一致动力学），因为插入物
- *"not represented in the world-scenario map"*（世界场景图里没有它），且
- *"conflict with the dynamic cuboid in the world-scenario map conditioning"*
  （与世界场景图条件中的 dynamic cuboid 冲突）。

论文把错配讲得非常直白：*"the RGB image suggests the presence of an object"*，
而结构化条件给出 *"no corresponding position, extent, or trajectory"*
（RGB 说有物体，结构化输入却没有对应的位置、尺度或轨迹）。这是一种**跨模态条件冲突**。

### 2.3 §9.3.2 的解法：randomized dynamic-cuboid dropout post-training

论文的解法不是更好的贴图，而是**更新权重**：post-train 一个 OmniDreams 变体，训练时做
**随机 dynamic-cuboid dropout**——移动 agent 的 cuboid 被随机移除，而
*"static cuboids, such as parked vehicles, are left unchanged"*
（停放车辆等静态 cuboid 保持不变）。

这一训练 *"prevents the model from relying exclusively on dynamic cuboids"*
（防止模型只依赖 dynamic cuboid），使它 *"learns to infer plausible object
persistence and motion from visual history"*，借助 *"the first-frame seed,
and surrounding scene context"*（学会从视觉历史、首帧种子与周围场景上下文推断
合理的物体持续性与运动）。推理时插入物能 *"propagate more naturally through
the generated video"*，*"even when they lack an explicit cuboid trajectory"*
（即使没有显式 cuboid 轨迹也能自然传播）。

Figure 11 定性展示：给定一个插入罕见物体的首帧编辑，模型随时间生成合理运动、
*"maintaining consistency with the surrounding road geometry and scene context"*。
**论文没有为这一结果报告任何数值指标——它是定性结果。**

> **本节要点（后文因果分析的支点）**：论文把 OOD 对象能力明确放在"手柄二"（权重更新）一侧。
> "没有 cuboid 也能维持物体"是一种**训练进权重里的推断能力**，不是 prompt 或贴图能替代的。

---

## 3. v01–v04 天气/光照变体：如何发现问题、如何解决

### 3.1 首次运行的失败现象

首批 11 变体运行（`/mnt/artifacts/stage2/run_stage2_20261002`）经人工审看判定失败，
天气侧现象：**prompt 里写了暴雪/夜雨/浓雾/眩光，视频里却完全是原场景的晴天**，
文本 prompt 像被忽略了一样。

### 3.2 根因：蒸馏 checkpoint 对 prompt-only 全局外观编辑无响应

受控诊断（`scripts/diag_stage2_prompt_ab.py`）做了一个干净的 A/B：
同一首帧、同一 seed、仅替换正负 prompt，对比渲染输出。结果 A/B 输出一致——
**这个 2-step 蒸馏、guidance_scale=1.0（无 CFG）的 checkpoint 对 prompt-only 的全局外观编辑基本无响应**。

机理上可以理解：蒸馏把多步带 CFG 的教师压缩成 2 步、条件强度被削弱，
文本通道在没有"视觉抓手"时推不动全局风格。截至当时 NVIDIA 也未发布任何支持
prompt-only 全局改外观的 OmniDreams checkpoint。

### 3.3 解法：外部图像编辑模型联合编辑首帧 + 文本沿视频传播

按 §9.3.1/Fig.10 的实际做法（首帧 seed 锚定编辑），我们实现了**首帧+文本联合编辑**
（`repos/alpasim/src/runtime/alpasim_runtime/stage2/frame_edit.py`）：

1. 会话初始化时取出 renderer 的原始首帧；
2. 用 `variants.yaml` 里该变体的中文 `frame_edit_prompt`（如 v01：暴雪、能见度 30 m、
   路面积雪、几何不变）调用外部图像编辑模型（火山方舟/Doubao Seedream 图像生成接口），
   用强约束 prompt 要求保持道路几何/车道/建筑结构不变；
3. 编辑返回 2K（2880×1584）图，用 `Image.LANCZOS` 缩回会话分辨率 1280×704，JPEG q92 回种；
4. 该编辑后的首帧作为会话 seed，**文本 prompt 再把暴雪/雨/雾/眩光沿视频逐 chunk 传播**。

实现纪律：

- `frame_edit.py` 只用标准库发 HTTPS（无第三方依赖）；
- **凭据只从凭据文件读取**（文件在 `~/access`；默认路径可用 `STAGE2_EDITOR_CREDENTIALS_FILE`
  覆盖），代码、日志、文档中只出现路径不出现内容；
- wizard extras 以只读挂载凭据文件；整个特性由 `STAGE2_VARIANT_SPEC`/`STAGE2_VARIANT_ID`
  环境变量门控，普通（非 Stage 2）运行完全不受影响；
- 每次会话增加约 100–110 s 的编辑延迟。

### 3.4 天气变体的结果与诚实解读

修复后重跑（`run_fixfull_20261002`，v10 因初版漏配 `frame_edit_prompt` 单独补配重跑）：

- **视觉确认成功**：v01 全程积雪路面/纷飞雪花/灰白天空；v02 深夜/雨幕/积水路灯反光；
  v03 浓雾吞没建筑、黎明天光；v04 正前低太阳/大片眩光/长阴影。外观沿 20 s 全程保持，没有"弹回晴天"。
- **但驾驶层没有可信的安全响应**：0–8 s 速度变化 v01 −0.85、v02 −0.67、v03 −1.08、
  v04 −1.13 m/s，均为轻微减速；四个变体首次碰撞都集中在 **t=6.80 s**（基线为 7.07 s），
  是同一路段的继承事件，与"看到暴雪"没有因果关系。
- 天气改变的是渲染外观、事件后帧计数与轨迹发散程度；**因为物理未建模，不能据此声称
  "策略在低能见度下更危险"的动力学结论**——只能说"策略没有对画面上的恶化视距做出反应"。

天气类是 Stage 2 中**与论文手柄一严格对应**的部分：分布内外观迁移，首帧锚定 + 文本传播即可，
效果稳定。

---

## 4. v05–v10 演员变体：四个阶段的问题链与解决方案

演员侧的问题不是一次暴露的，而是一条四阶段的链条：**坐标错位 → 首帧锚定 → 多帧维持 → latent 历史同步**。
每解决一个问题，下一个问题才显现。下面按时间顺序如实记录（含失败方案）。

### 4.1 阶段一：坐标锚定——演员被落在 ego 身后 30 m

**失败现象**（首批 `run_stage2_20261002`）：BEV 里插入对象的红框总在 **ego 驶过之后**才出现，
画面里根本看不到注入的演员。

**根因**：合成演员的坐标按**渲染起始时刻（t=0）**的 ego 位姿做 rig→clip-global 变换，
但其轨迹窗口在 `activate_s`（2.5–3.5 s）才开启。t=0 时把演员放到世界坐标后，
ego 以 ~10 m/s 开了 3 s——演员实际落在 ego 身后约 30 m。

**修复**（`stage2/variant_actors.py`）：坐标语义改为"**在演员激活时刻**取 GT rig 位姿
做 rig→clip-global 变换（x 前、y 左、z 上）"。演员在 ego rig 坐标系里于激活时刻被编写
（例如 v05：右前方 22 m、偏右 3.2 m），再通过该时刻记录的 GT rig 位姿变换到 clip 全局。
单测覆盖直白情形："10 m/s 直行、20 m 处、3 s 激活 → 世界坐标 50 m"。

实现细节：轨迹 track 前缀 `s2_`；演员轨迹时间戳**复用 rig 视频帧时间戳**，
使 renderer 的时间 membership 判定与真实相机帧对齐；`build_synthetic_objects`
由 `variant_actors.py` 依据 rig 轨迹与渲染起止时间生成，注入前校验 spec 的
`base_scene` 与当前场景一致。

### 4.2 阶段二：首帧 RGBA cutout 锚定——让像素与 cuboid 从 t0 就一致

修复坐标后，演员的 cuboid 出现在正确位置，但 3D 注入只有结构化条件、**首帧像素里没有它**，
世界模型头几帧对"凭空出现的 cuboid"反应不稳定。这恰好接近论文 §9.3.2 批评的错配方向
（结构说有、RGB 说没有）。

**解法**（`stage2/first_frame_anchor.py`）：用相机自身的 FTheta 模型
（`_fw_poly_coeffs` + `project_points`）把演员 cuboid 的 3D 角点投影到首帧，
把预先制作好的 per-actor RGBA 抠图（`<id>.png`，如 `pedestrian_stroller.png`）
按投影位置贴入 seed frame。于是从 t0 起：**像素里有这个人、cuboid 里有这个人、
轨迹窗口已就绪、prompt 里也描述了这个人——四路信号一致**。

cutout 制作工具链：

- `scripts/stage2_make_cutout.py` 配合 `configs/stage2/cutout_prompts/*.txt`
  （9 个演员的生成 prompt：婴儿车行人、轮椅、挖掘机、奶牛、两桶、木箱、树枝、持伞行人），
  生成素材后抠图为 RGBA，供 renderer 容器从 `STAGE2_ANCHOR_PATH/assets`（默认 `/mnt/stage2/anchor/assets`）读取。

### 4.3 阶段三：多帧重贴——首帧演员几帧内被世界模型"淡出"

**新问题**：只贴首帧时，生成帧交给世界模型，**低对比度演员在几个生成帧内就被淡出/吞掉**，
即使它的 cuboid 始终存在。世界模型并不"忠于"首帧像素里的小物体。

**解法**：renderer 侧多帧锚定
（`repos/flashdreams/integrations_v2/omnidreams/impl/stage2/multiframe_anchor.py`），
由 `STAGE2_MULTIFRAME_ANCHOR=1` 门控：对**每个生成的 RGB 帧**，依据请求 `dynamic_state`
里的 cuboid 轨迹（与模型条件所用轨迹完全相同）重新投影、把演员 cutout **重贴回每一帧**。
种子帧（AR0，时间索引 0）保持不动（alpasim 侧已贴）。变体演员按其编写的 bbox 尺寸匹配；
spec 里没有的演员绝不触碰。

### 4.4 阶段四：重贴像素必须进入 latent 历史——run b→g 的迭代（核心战役）

多帧重贴后出现"幽灵"（ghost）问题：**清晰贴图之外，世界模型自己生成的演员续演体露在素材盒外**
（挖掘机向上延伸的暗色吊臂、放大的人影剪影）。如何让重贴像素进入" conditioning 下一 chunk 的
latent 历史"，同时不让模型自造的残像放大，经历了六轮迭代（`run_ac_20261003b…g`）。
每一轮失败都是有效证据，全部记录：

| 轮次 | 做法 | 结果 | 结论 |
|---|---|---|---|
| run-b | 每帧清晰重贴，不处理残像 | 素材盒外露出模型自造的残体（ghost） | 必须清除残像 |
| run-c | 只对 ghost **改色**（不动背景） | 残体变成稳定的蓝色行人留在 latent 历史里；v05 碰撞 4 帧→34 帧 | 不能只改色，必须就地填背景 |
| run-d | **红色种子** + 小膨胀模板 + EDT 最近背景填充 + 羽化贴回 | 红色 ghost 可消除 | 红种方案成立但只对红色有效（见 4.5） |
| run-e | BiRefNet/学习式显著性 + 大模板 | 掩码膨胀成几百像素平板，平面填充经 VAE 重编码**污染 clean_latent/KV 历史** | 不能用学习式显著掩码与大模板 |
| run-f | 每 chunk 过流式 VAE 缓存，但**全帧**用重编码 latent 替换 clean_latent | **背景逐 chunk 糊化**：解码 RGB 本就是有损 VAE 输出，全帧重编码等于每轮把（含未动背景的）clean latent 换成更有损的副本，误差在 KV 历史累积，背景糊成色块 | 全帧替换废弃 |
| run-g | 每 chunk 过**同一个 per-session 流式 VAE 编码缓存**；编码 latent **只在局部羽化的素材 alpha 足迹内**混入 clean_latent；**每个背景 latent 单元逐字节不变** | 背景保持清晰、演员每帧维持；**人工审核通过，用户批准应用此方法重跑 v05–v10** | 最终方案 |

run-f 与 run-g 的差别值得讲透，因为它是本次最隐蔽的坑：

- 渲染器的 latent 历史有两部分：DiT 自注意力的 KV cache，以及 `FinalState.clean_latent`
  （stock 情况下是 DiT 去噪器的直接输出）。
- Wan VAE 时间 4×、空间 8× 压缩，z-grid 88×160。解码后的 RGB 已经是一次有损 decode；
  run-f 把整帧重新编码并替换 clean_latent，等于每个 chunk 给**包括未触碰背景在内的整帧**
  再叠加一次编码损失，损失在自回归 KV 历史里持续累积 → 背景快速糊化。
- run-g 让每 chunk 仍通过 per-session 流式编码缓存（`initialize_autoregressive_cache`
  推进时间缓存，保证时间边界特征与流式语义一致），但把重编码 latent 与 clean_latent 的混合
  **严格限制在局部、羽化的素材 alpha 足迹**（`_latent_alpha`：像素掩码空间 8× max-pool、
  膨胀 2 个 latent 格、高斯羽化）。素材足迹之外的背景 latent 与 stock 生成**逐字节相同**，
  背景糊化的误差源被彻底切断。

### 4.5 红种/EDT ghost 清除机制及其根本边界

run-d 确立、后续沿用的 ghost 清除机制：

1. **红色种子检测**：`redmask = (r > 120.0) & (r - np.maximum(g, b) > 34.0)`
   ——利用素材/模型残像中偏红的像素做种子（cutout 制作时刻意保留红色衣物/部件）；
2. **小膨胀模板**：约 0.12 × 素材高（4–26 px），向上方向取 1.7× radius；
3. **EDT 就地填充**：`scipy` 的 `distance_transform_edt(return_indices=True)`
   取最近背景像素填充模板，羽化贴回；素材盒内受保护；近距离（素材高 ≥0.9 屏高）跳过。

**根本边界（后来被 v07 三轮实验严格证明）**：该机制只能消除**红色** ghost。
模型在素材上方/周围自造的**非红色**续演体（挖机吊臂平板、放大的牛身、篷布团）
没有红色种子，无法被触及，会在自回归循环中持续放大。

### 4.6 v07 三轮修复实验：证明"双侧夹逼"（均未成功，如实记录）

run-g 解决了背景糊化，但 v07 挖机的自造吊臂仍被放大。经逐轮批准，在 v07 上做了三轮受控实验：

| 轮次/产物 | 机制 | 视觉结果 | 驾驶结果（全量 20 s） |
|---|---|---|---|
| r1 `run_geomghost_v07` | 在红种外加入挖机 cuboid 投影凸包作为 ghost 种子，EDT 填充 | 生成吊臂仍逃逸并放大 | 碰撞 6.0 s 起 33 帧；progress 0.615 |
| r2 `run_geomghost2_v07` | 凸包以脚底锚点放大 2.2×，近距离 gate 提至 0.9 屏高 | 大面积平板 EDT 填充注入 latent 后，**自己长成新的巨型立柱** | 无碰撞；9.9 s 停；progress 0.502；gt_max 0.36 |
| r3 `run_rgbghost_v07` | **ghost 外环只改 RGB；latent patch 严格限于素材 alpha 足迹** | frame 29 悬浮平板仍在，frame 89 仍成跨路巨拱 | 碰撞 6.0 s 起 43 帧；progress 0.635 |

三轮实验证明这是一个**双侧夹逼**：

- 模板小 → 模型自造的非红色续演体逃出并自回归放大；
- 模板大 → 大面积平坦 EDT 填充进入自回归循环后，自身巨量化（平坦区域是世界模型最爱的"生长基底"）；
- r3 的 RGB/latent 分离保证背景 latent 不被污染（这是安全边界），但对"模型在素材上方新生成的内容"
  没有任何作用——v07 失效帧与基线一致（59→89 帧之间成巨拱）。

实验结束后默认 `STAGE2_GHOST_SCALE` 还原为 1.0；正式报告中 v07 采用主批次 run-g 版本。

### 4.7 有效期截断：发现"零干预基线自身漂移"与最终验收

在为六变体重跑做逐帧审看时，得到了 Stage 2 最重要的发现之一：

**v00 基线在没有任何 Stage 2 干预的情况下，自身在该场景上自约 frame 199（6.63 s）起几何漂移**——
树干加粗巨型化、停放车辆融化成篷布板片；frame 249（8.30 s）前景鼓起巨型篷布团；
frame 449/588 全帧退化为抽象岩板/篷布板。首帧锚定既不导致也不阻止它
（2-step 蒸馏、guidance 1.0、自回归 latent 循环的内生限制）。

这意味着：**把 20 s 全量统计当作驾驶结论是错的**——v00 全量指标自己在 7.07 s 起报碰撞 ×12、
8.4 s 起报 offroad ×43，全是漂移期假事件。为此确立**逐变体有效期截断**策略：

- 每变体审看 frame 29/44/59/89/119/149/199/249 九宫格接触印样、可疑段加密采样，
  以"首个无可歧义的几何/语义崩塌帧"保守取前一帧为有效期末帧；
- 窗口表存于 `configs/stage2/manifests/stage2_v05v10_valid_windows.csv`；
- 截断脚本 `scripts/analyze_stage2_validwindow.py`：按 30 fps 把指标时间戳映射到帧号
  （`idx = round((t − t0)/1e6 × 30)`，t0 取回放首帧时间戳），含末帧截断；
  空值输出空字符串、绝不输出 NaN。

有效期表：

| variant | 失效类型 | 有效期末帧 | 期末时间 | 判定依据 |
|---|---|---|---|---|
| v00_baseline | 内生漂移 | 199 | 6.63 s | 249 帧巨型树干+前景篷布板 |
| v05_stroller_jaywalking | 背景内生漂移（演员交互期连贯） | 199 | 6.63 s | 199 帧后背景漂移 |
| v06_wheelchair_shoulder | 内生漂移 | 199 | 6.63 s | 全量 7.07 s 碰撞在窗口外（帧 ≈212） |
| v07_construction_excavator | 演员诱导 | 59 | 1.97 s | 89 帧吊臂成跨路巨拱；29 帧已见悬浮平板 |
| v08_loose_cow | 演员诱导 | 149 | 4.97 s | 199 帧巨型牛头/毛皮墙 |
| v09_construction_debris | 演员诱导 | 149 | 4.97 s | 199 帧巨型木框架 |
| v10_typhoon_compound | 演员诱导（台风大气全程保持） | 149 | 4.97 s | 199 帧巨型篷布堆 |

**有效期内逐帧指标（最终验收依据，`analysis/validwindow_per_variant.csv`）**：

| variant | 碰撞首/末 (s) | 碰撞帧 | offroad | 最近障碍 (m) | dist_to_gt 最大 (m) | progress | 期末速度 (m/s) |
|---|---|---|---|---|---|---|---|
| v00_baseline | — | 0 | 0 | 0.37 | 1.49 | 0.583 | 7.37 |
| v05_stroller_jaywalking | 5.47 / 6.00 | 3（前部） | 0 | 1.24 | 0.529 | 0.871 | 3.37 |
| v06_wheelchair_shoulder | — | 0 | 0 | **0.22** | 1.09 | 0.580 | 7.55 |
| v07_construction_excavator | — | 0 | 0 | 1.47 | 0.00 | 0.172 | 8.52 |
| v08_loose_cow | — | 0 | 0 | 1.47 | 0.27 | 0.441 | 6.53 |
| v09_construction_debris | 4.93 | 1 | 0 | 0.10 | 0.443 | 0.443 | 6.12 |
| v10_typhoon_compound | 4.93 | 1（前部） | 0 | 0.23 | 0.446 | 0.446 | 6.68 |

有效期内的驾驶行为结论（细节见 `docs/Stage2_Report_v05v10_localpatch.md`）：

- **v05 碰撞成立**：frame 158–170 特写条显示红衣行人与婴儿车全程在车头前方、形态清晰，
  5.47 s 前部接触持续 3 帧；中距另有一道白色重影伪影，不在 ego 走廊内，不影响事件判定。
- **v06 高风险近距避让**：未急刹、以约 0.22 m 余量贴身通过轮椅使用者（远低于 0.8 m 舒适阈值），
  窗口内无碰撞；全量 7.07 s 碰撞位于漂移窗口外，不计。
- **v07 无法评估**：窗口仅到 1.97 s、早于 3.5 s 激活。全量指标"最干净"（无碰撞 flag），
  但 2 s 后画面不可信，**不能据此声称成功避让挖机**。
- **v08 减速可信、碰撞不成立**：奶牛在 frame 29–149 形态连贯，4.6 s 起减速；
  全量首次碰撞 6.27 s 正值奶牛→毛皮墙形变期，不计。
- **v09 边界性 1 帧接触**：4.93 s（帧 ≈148）碰撞 flag；特写条显示木箱/锥桶已被放大但仍可辨认为施工物，
  记为与预期注入物的边界接触；199 帧巨型木框架不计。
- **v10 接触的是树枝、不是行人**：台风大气（乌云、横向雨幕、湿路反光、风压树木）全程质量优秀，
  是视觉氛围最成功的变体；4.93 s 1 帧接触的是车道中央倒地树枝（当时外观已畸变为白色篷布状团块），
  右侧持伞行人始终在路肩、未被接触。

---

## 5. 妥协与局限的因果联系（本文核心章节）

用户要求讲清"我们的妥协和 v05–v10 具有局限性的效果之间的联系"。这条因果链可以严格地表述为五步。

### 5.1 论文把 OOD 物体能力放在了"权重更新"那一侧

如 §2 所引，论文 §9.3.2 完整逻辑是：

1. 朴素地往首帧贴 OOD 物体 → RGB 与世界场景图/dynamic cuboid 错配 → artifacts 与不一致动力学；
2. 解法是 post-train randomized dynamic-cuboid dropout 变体；
3. 训练后模型获得的关键能力是：**在没有 dynamic cuboid 的条件下，从视觉历史、首帧种子、
   场景上下文推断物体的持续性与运动**。

也就是说，"罕见物体无 cuboid 也能稳定维持"首先是一种**权重中习得的先验**，
其次才谈得上推理时怎么贴。论文自己把这条边界划得很清楚：天气/光照走 prompt（§9.3.1），
罕见动物/超大物体走 post-training（§9.3.2）。

### 5.2 我们的妥协：冻结公开蒸馏权重，用"条件信号一致性工程"替代权重更新

计划阶段（`docs/Stage2_Plan_detailed.md` Out-of-Scope 第 6 条）明确：不改 OmniDreams 权重。
客观原因也充分：本地只有公开 2B 蒸馏 checkpoint，post-training 需要完整训练数据、
教师 checkpoint 与可观算力，且蒸馏学生是否适合再训练本身存疑。

于是我们把全部努力投入到**让条件信号彼此一致**上，严格避开论文批评的 RGB/结构错配：

- RGB 首帧：FTheta 投影 + RGBA cutout，像素里有演员（`first_frame_anchor.py`）；
- 结构化条件：注入**尺寸匹配的 cuboid** 与**激活后全窗轨迹**（`variant_actors.py`）；
- 文本：per-rollout prompt 描述同一情境；
- 时序维持：每个生成帧重贴（`multiframe_anchor.py`）；
- latent 同步：run-g 每 chunk 过流式 VAE 缓存、局部羽化足迹内混合、背景逐字节不变；
- 残像清理：红种/EDT 只在 RGB 输出上擦除模型自造的红色 ghost，且其外环不进入 clean_latent。

这是一套"把论文指出的每一个信号缺口都补上"的工程。就**信号一致性**而言，
我们的输入条件比论文 §9.3.2 的朴素方法严格得多。

### 5.3 但信号工程给不了权重里的推断能力——于是出现两类必然失效

冻结的 checkpoint **从未做过 dynamic-cuboid dropout 训练**，它仍然"只依赖 dynamic cuboid"，
也从未被训练去容忍 cue 冲突。这直接导致两类失效：

**A 类：演员诱导的早期巨型化。** 模型把重贴像素解释为"表面"，在素材 alpha 之外
**生成自己更大的续演体**（挖机向上延伸的吊臂、奶牛的放大躯体、篷布/木块团块）。
这些续演体：(i) 不是红色，红种/EDT 触不到；(ii) 在 cuboid 之外，几何模板盖不住；
(iii) 一旦进入自回归 latent 循环就逐 chunk 放大。v07 三轮实验证明无法在不毒化历史的前提下
清除它们——模板小则逃逸，模板大则平板填充自己巨量化。

**B 类：渲染器内生漂移。** 同一场景、零干预下，6.6–8.3 s 后树干/车辆/建筑自行融化巨型化。
这是 2-step 蒸馏、无 CFG、自回归 latent 长循环的内生误差累积，**与 Stage 2 注入无关**
（锚定既不导致也不阻止），Stage 2 侧任何 patch 都无法根治。它还决定了有效期的"天花板"：
即使演员完美，背景也只给我们约 6–8 s。

### 5.4 因果链一图概括

```
论文 §9.3.2 的 OOD 能力 = post-trained 权重中的推断先验
                    │
        我们冻结权重（公开蒸馏 2B，Out-of-Scope）
                    │
        改用条件信号一致性工程（RGB+cuboid+轨迹+prompt+局部latent patch）
                    │
        消除了论文批评的 RGB/结构错配  ──成功──► 链路全通、时序正确
                    │
        但权重从未学会「无 cuboid 推断 OOD 持续性 / 容忍 cue 冲突」
                    │
        ┌────────────┴────────────┐
   模型自造放大续演体          自回归 latent 长循环误差累积
   （A 类：v07 1.97s，        （B 类：v00 6.63s 起，
     v08–v10 4.97s）             所有变体共享的天花板）
                    └────────────┬────────────┘
                          有效期 2–6.6s
                 20s 视频不能作为"真实反事实 rollout"
```

### 5.5 为什么 v01–v04 相对成功、v05 相对最好——同一因果链的佐证

- **v01–v04 落在论文手柄一的覆盖域内**：天气/光照是 §9.3.1 明确的 prompt + 首帧通道，
  属于分布内外观迁移，无需权重更新即可稳定。它们的局限（物理未建模、驾驶层无响应）
  也与渲染器能力无关，是裁剪声明的预期结果。
- **v05 在演员变体中有效期最长（6.63 s，与基线相同）**：行人是驾驶数据中最高频的类别，
  模型对"行人形态/步态"先验最强，且红衣使其 ghost 可被红种机制清除；交互全程演员连贯。
  越偏离训练分布（工程机械臂、动物、平板状障碍组），模型自造续演体出现得越早——
  v07（最 OOD）1.97 s 即崩，v08–v10 约 5 s。**有效期长短与演员的 OOD 程度严格负相关**，
  这正是"权重先验缺失"假说的直接观测证据。

**最终诚实结论**：v05–v10 的有限效果是"用条件信号一致性替代 §9.3.2 权重更新"的
**直接、预期代价**，不是执行缺陷。Stage 2 在该约束下能够合法声称的产出是：

1. 全链路打通且信号一致（系统成功）；
2. 分布内的天气/光照反事实外观（论文手柄一）；
3. 演员变体在 **2–6.6 s 有效期内**的驾驶反应对比（减速、碰撞、近距避让）；
4. 对所用 checkpoint 能力边界的定量刻画（OOD 程度越高越早崩、~6–8 s 内生漂移天花板）。

要获得论文 Fig.11 那种"无 cuboid 也能自然传播"的完整 20 s OOD rollout，
路径只有两条：取得/训练 dropout post-trained 权重，或等待 NVIDIA 发布更强的非蒸馏 checkpoint。

---

## 6. 踩坑与经验全清单

### 6.1 Stage 1 已验证、Stage 2 再次确认的

1. **永远不要只信 aggregate `metrics_results.txt`**：截断算子（碰撞/offroad/dist≥4 m 后删帧）
   会让早碰撞的批次数字反而好看。验收依据永远是逐帧 `metrics.parquet`（`values` 需
   `pd.to_numeric(errors="coerce")`、同 metric/时间戳多相机行取 max）+ 原始视频。
2. **renderer 只有一个活动 session**：`start_session` 先清旧 session，并发必报 `Session not found`。
   批量强制 `nr_workers=1`、所有 endpoint `n_concurrent_rollouts=1`，并用 `run_method=NONE` 核对渲染配置。
3. **Hydra 怪癖**：不支持 `key+=value`；YAML 组合对 list 是**替换不是合并**——extras 重写 volumes
   必须列全默认挂载。
4. **gated 模型需离线标志**：driver 容器挂 HF cache 但无 token 时，注入 `HF_HUB_OFFLINE=1`、
   `TRANSFORMERS_OFFLINE=1`（先 run_method=NONE 验证 environment）。
5. **模型表现 ≠ 系统正常**：11 个 rollout 完整、证据一致是系统成功；驾驶通过率 0/10 是模型结果，
   两件事必须分开写。
6. **资源纪律**：任务结束停 renderer/批量容器，两 GPU 显存回到 ~14 MiB；长任务用
   `setsid bash -c '...' </dev/null >log 2>&1 & disown`，靠日志确认、不空等。

### 6.2 Stage 2 新增的坑与经验

7. **蒸馏 checkpoint 对 prompt-only 全局外观编辑无响应**：A/B（同帧同 seed 换 prompt）是判定
   "模型听不听 prompt"的标准动作；解法是联合编辑首帧，给文本一个视觉抓手。
8. **注入坐标的"时刻语义"**：rig 相对坐标必须在**演员激活时刻**用 GT rig 位姿变换，
   不能用渲染起始位姿；并让演员轨迹时间戳复用视频帧时间戳。
9. **只贴首帧不够，只改色更糟**：演员几帧内被淡出；改色留下稳定彩色残像（碰撞 4→34 帧）。
   必须多帧重贴 + 就地背景填充。
10. **全帧 VAE 重编码必然糊背景**：有损 decode→encode 的循环每 chunk 叠加损失并进 KV 历史。
    任何"重编码"只能做**局部、羽化足迹**内的混合，背景 latent 必须逐字节不变；
    同时每 chunk 推进 per-session 流式 VAE 缓存以维持时间一致性。
11. **EDT 平板填充是一把双刃剑（双侧夹逼）**：模板小→非红色续演体逃逸；模板大→平板自己长成
    巨型结构。安全边界是"ghost 外环只改 RGB，latent patch 限于素材 alpha"——代价是对非红色
    自造续演体无效。不要迷信"再大一点的模板就能盖住"。
12. **学习式显著性（BiRefNet 等）不适合做注入模板**：掩码过大且边界不受控，经 VAE 放大污染历史。
13. **拉普拉斯方差锐度对语义形变失明**：巨型化/融化帧的 lapvar 仍在 580–684（与正常帧同区间，
    因为崩塌帧细节纹理依然丰富）。锐度只能查黑屏/虚焦；**验收必须逐帧（或加密接触印样）人工审看**。
14. **零干预基线必须一起审**：本次最重要的方法学收获是发现 v00 自身 6.6 s 后漂移。
    任何"变体失效"都要先与零干预基线对照，区分"演员诱导"与"渲染器内生"。
15. **有效期截断是诚实报告的必要手段**：按每变体实测失效帧截断所有事件统计；
    窗口表与截断脚本都进版本库，空值留空、不输出 NaN。宁可报告"窗口内 3 帧碰撞"，
    也不拿漂移期的 50 帧碰撞充数。
16. **外部图像编辑 API 的工程代价**：每会话 +100–110 s、强依赖网络；批量/离线复现应缓存编辑后首帧；
    编辑 prompt 必须强约束几何不变，批量使用抽查首帧对齐。
17. **OOD spike 先行、按预案降级**：T-Rex rubric 全 0 → v07 按预案降级挖掘机；
    新类别先单项 rubric 评分再进矩阵（cow box 评分 1 证明链路有效，与实体类别无关）。
18. **特性一律环境变量门控 + patch 分发**：所有 Stage 2 行为由 `STAGE2_*` 门控，
    非 Stage 2 运行零影响；alpasim 侧改动经
    `configs/local-patches/patches/alpasim-stage2-001-synthetic-scenarios.patch` 分发，
    单测先行（Stage 2 单测 21 项）。
19. **cutout 素材的红色种子是刻意设计**：红衣/红部件不是审美选择，而是给 ghost 检测留的抓手；
    制作新素材时要同时考虑"模型先验强不强"与"能不能被红种兜住"。

### 6.3 对后续阶段的可操作建议

- rollout 截短到 5–6 s、激活时刻前移到 1–2 s，让所有安全事件落入有效期；
- 同变体跑多 seed（如 3 seed）区分渲染偶然与稳定缺陷；
- 天气若要进驾驶评测，必须在交通/物理层建模摩擦与传感器距离，而不是在渲染层；
- 跟踪 NVIDIA 后续 checkpoint，若长时域漂移改善再恢复 20 s 矩阵；
- 若取得 dropout post-trained 权重，v05–v10 可直接复用现有注入链路重测，无需改代码。

---

## 7. 产物地图、运行编年与复现命令

### 7.1 代码与配置（仓库内，被 git 跟踪）

| 路径 | 作用 |
|---|---|
| `configs/stage2/variants.yaml` | 唯一变体事实源：prompt、frame_edit_prompt、演员箱尺寸/rig 坐标/速度 |
| `configs/stage2/manifests/stage2_variants.csv` | 首批 11 变体清单 |
| `configs/stage2/manifests/stage2_v05v10.csv` | v05–v10 重跑清单 |
| `configs/stage2/manifests/stage2_v05v10_valid_windows.csv` | **有效期表** |
| `configs/stage2/cutout_prompts/*.txt` | 9 个演员 cutout 生成 prompt |
| `configs/local-patches/patches/alpasim-stage2-001-synthetic-scenarios.patch` | alpasim Stage 2 全部改动（演员注入、首帧编辑、首帧锚定、服务接线） |
| `scripts/run_stage2_separate.sh` | 清单驱动串行闭环（自动拉起/停止 renderer，发布 current_variant.txt） |
| `scripts/frame` 相关实现（经 patch 落入 `repos/alpasim/src/runtime/alpasim_runtime/stage2/`） | `frame_edit.py`、`variant_actors.py`、`first_frame_anchor.py` |
| `repos/flashdreams/integrations_v2/omnidreams/impl/stage2/multiframe_anchor.py` | renderer 侧多帧重贴 + 局部 latent patch + ghost 清除（第三方仓库，不提交） |
| `scripts/analyze_stage2.py` | 全量 20 s 逐帧指标汇总（含漂移期，供对照） |
| `scripts/analyze_stage2_validwindow.py` | **有效期截断指标（验收用）** |
| `scripts/export_stage2_frames.sh` | 双路逐帧导出（每路 589 JPG） |
| `scripts/render_stage2_bev.py` / `render_stage2_hud.py` | BEV 与 HUD 视频 |
| `scripts/stage2_make_cutout.py` | cutout 素材制作 |
| `scripts/diag_stage2_prompt_ab.py` / `diag_stage2_actor_ab.py` | prompt 响应 A/B、演员 A/B 诊断 |
| `scripts/check_stage2_task*.py` | 各任务 gate（收集/闭环/导出/BEV/视频/分析） |

### 7.2 运行编年（产物均在 `/mnt/artifacts/stage2/`，不被 git 跟踪）

| 时间 | 产物 | 内容 |
|---|---|---|
| 10-02 | `run_stage2_20261002` | 首批 11 变体：天气不可见、演员在身后 → 失败 |
| 10-02 | `run_fixfull_20261002`（+`run_fixfull_v10redo.log`） | 首帧联合编辑 + 激活时刻锚定，首版正式结果 |
| 10-03 | `run_anchor_20261003` | 首帧 RGBA cutout 锚定 |
| 10-03 | `run_ac_20261003b…g` | 多帧重贴六轮迭代：b 残像 → c 改色 → d 红种EDT → e 大模板 → f 全帧糊化 → **g 局部 latent patch（批准）** |
| 10-03 | `run_geomghost_v07` / `_geomghost2_` / `run_rgbghost_v07_` | v07 三轮修复实验（双侧夹逼证明） |
| 10-03 | `run_localpatch_20261003` | **最终主批次**：v05–v10 run-g 重跑 + 后处理 + 有效期指标 |

最终批次产物（`/mnt/artifacts/stage2/run_localpatch_20261003/`）：
`map_v05v10.csv`、`frames/<variant>/`、`bev/<variant>/`、`mp4/<variant>_front.mp4` 与 `_bev.mp4`、
`analysis/per_variant.csv`、`reaction_table.csv`（全量，含漂移期，仅供对照）、
`analysis/validwindow_per_variant.csv`（**验收依据**）。

### 7.3 复现命令

```bash
source "$HOME/simulation/scripts/env.sh"

# 1) v05–v10 主批次（六变体串行；自动拉起/停止 renderer）
MANIFEST="$HOME/simulation/configs/stage2/manifests/stage2_v05v10.csv" \
RUN_TAG=localpatch_20261003 \
  bash "$HOME/simulation/scripts/run_stage2_separate.sh"

# 2) 后处理：导帧 → 全量分析 → BEV → MP4
bash /tmp/postprocess_v05v10.sh

# 3) 有效期截断指标（最终验收依据）
"$HOME/simulation/repos/alpasim/.venv/bin/python" "$HOME/simulation/scripts/analyze_stage2_validwindow.py" \
  --map /mnt/artifacts/stage2/run_localpatch_20261003/map_v05v10.csv \
  --windows "$HOME/simulation/configs/stage2/manifests/stage2_v05v10_valid_windows.csv" \
  --variants "$HOME/simulation/configs/stage2/variants.yaml" \
  --out /mnt/artifacts/stage2/run_localpatch_20261003/analysis/validwindow_per_variant.csv

# 4) 11 变体首版（天气侧方案）复现
MANIFEST=$HOME/simulation/configs/stage2/manifests/stage2_variants.csv \
RUN_TAG=fixfull_20261002 \
  sg docker -c 'bash scripts/run_stage2_separate.sh'
```

---

## 附：文档地图

- `docs/Stage2_Plan_detailed.md`：超详细计划（环境事实、变体矩阵、Out-of-Scope、Phase 0–5、预案）；
- `docs/Stage2_Report.md`：v00–v10 首版闭环报告（两首次根因、11 变体结果、天气裁剪、T-Rex spike）；
- `docs/Stage2_Report_v05v10_localpatch.md`：v05–v10 局部 latent patch 重跑与有效期截断的正式验收报告；
- `docs/Stage1_Complete_1.md` / `Stage1_Batch30_Report.md` / `Stage1_Runtime_Performance.md`：
  Stage 1 的系统搭建、批量与运行时性能（Stage 2 的实时倍率、渲染耗时等基线均源于此）。
