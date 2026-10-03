# Stage 2 端到端复现指南：OmniDreams 罕见场景反事实闭环仿真

> 编写日期：2026-10-03
> 目标读者：人类开发者 / AI Agent。按本文顺序操作，可在已完成 Stage 1 部署的
> 双 A100 服务器上复现 **Stage 2：OmniDreams 反事实长尾场景闭环仿真**——
> 在同一个真实场景上生成 11 个变体（4 个天气/光照、6 个注入式罕见场景、
> 1 个零干预基线），每个变体跑 20 s 闭环，产出逐帧指标、前摄视频与 BEV 视频。
> 配套阅读：`docs/Stage2_Complete.md`（**完整复盘：原理、踩坑、妥协与局限，强烈建议先读**）、
> `docs/Stage1_Reproduction_Guide.md`（Stage 1 环境从零搭建）、
> `docs/Stage2_Report_v05v10_localpatch.md`（v05–v10 最终批次报告）。

---

## 0. 一页概览

**做什么**：对一条真实 NuRec 街景（base scene），在**不更新任何模型权重**的前提下，
用论文 OmniDreams §9.3 的两个条件通道——**文本提示 + 首帧条件**——生成反事实长尾
变体，并注入合成 3D 演员（hdmap 拓扑不变），由 Alpamayo 1.5 作为 driver 完成 20 s 闭环。

| 项 | 值 |
|---|---|
| Base scene | `clipgt-02eadd92-02f1-46d8-86fe-a9e338fed0b6` |
| 变体数 | 11（v00 基线 + v01–v04 天气/光照 + v05–v10 注入演员，见 §4.1） |
| Driver | Alpamayo 1.5（GPU0），preset `alpamayo15_1cam_local`，单前视相机 |
| Renderer | OmniDreams 2B distilled（GPU1，gRPC :50051），704p，30 fps |
| Rollout | 20 s；渲染流 589 帧；指标帧 75 帧（3.75 Hz） |
| 共享随机种子 | `20261002`（driver + renderer + traffic 全链路确定性） |
| 外部图像模型 | 火山方舟 Ark **`doubao-seedream-5-0-pro-260628`**（首帧编辑 + 演员 cutout 生成） |
| 运行方式 | **严格串行**：11 个独立 wizard 共用一个常驻 renderer |

总体数据流：

```
variants.yaml ──► wizard (per variant, GPU0 containers)
  STAGE2_VARIANT_ID ──► runtime: 变体 prompt / frame edit / 合成演员 cuboid
                                          │
                                          ▼
            常驻 OmniDreams renderer (GPU1):
            STAGE2_MULTIFRAME_ANCHOR=1 ──► 每 chunk 重贴演员 + 局部 latent patch
                                          │
                                          ▼
              rollout.asl + metrics.parquet + 前摄/BEV MP4
```

---

## 1. 前置条件

### 1.1 Stage 1 环境必须已就绪

本文假设你已按 `docs/Stage1_Reproduction_Guide.md` 完成：

- [ ] 双 A100-80GB、Ubuntu 22.04、500 GB 数据盘挂载 `/mnt`；
- [ ] 四个内层仓库已 clone 并锁定提交：alpasim `affc2ea`、flashdreams `0957cf0`、
      alpamayo `11a0e01`、omni-dreams `cd85f39`；
- [ ] Stage 1 的补丁与 wizard 配置已应用（`alpasim-001`、`alpasim-002`、
      `alpamayo-001`（可选）+ 四个 wizard 配置）；
- [ ] 镜像 `alpasim-base:0.134.0` 已构建；CUDA 13 JIT 工具链就绪；
- [ ] Alpamayo 1.5 权重与 HF 缓存（含 Cosmos-Reason2 tokenizer）已就位。

快速核对：

```bash
source ~/simulation/scripts/env.sh
git -C repos/alpasim  log -1 --format='%h'   # 应输出 affc2ea
git -C repos/flashdreams log -1 --format='%h' # 应输出 0957cf0
docker images alpasim-base --format '{{.Tag}}' # 应输出 0.134.0
nvidia-smi --query-gpu=memory.used --format=csv,noheader # 应均为 14 MiB
```

### 1.2 账号与权限清单（两类，勿遗漏）

| 服务 | 用途 | 何时需要 |
|---|---|---|
| HuggingFace（gated 授权） | NuRec 场景、OmniDreams 权重、A15/CR2 权重 | Stage 1 已办；BiRefNet 抠图模型为公开仓库无需授权 |
| **火山引擎方舟 Ark** | 首帧天气编辑（v01–v04、v10）、演员 cutout 生成（v05–v10） | **Stage 2 新增，必须办理** |

---

## 2. 权限办理

### 2.1 HuggingFace（沿用 Stage 1，核对即可）

以下页面需用正确账号登录并点 **Agree**（token 不绕过 gated 授权）：

- `nvidia/PhysicalAI-Autonomous-Vehicles-NuRec`（dataset 类型，场景必需）；
- `nvidia/omni-dreams-models`（渲染权重）；
- `nvidia/Alpamayo-1.5-10B`、`nvidia/Cosmos-Reason2-8B`（1.5 driver 路线）。

BiRefNet（`ZhengPeng7/BiRefNet`，cutout 抠 matting 用）为公开模型，首次运行自动下载到
HF 缓存，无需申请。中国大陆网络故障时可设 `HF_ENDPOINT=https://hf-mirror.com`。

### 2.2 火山引擎方舟：开通 `doubao-seedream-5-0-pro-260628`（Stage 2 关键新增）

Stage 2 用同一个**火山方舟（Volcengine Ark）图像模型**完成两类调用（均为
`https://ark.cn-beijing.volces.com/api/v3/images/generations`）：

1. **图片编辑**（image + prompt → image）：把晴朗首帧编辑成暴雪/暴雨/浓雾/眩光/台风；
2. **文生图**（prompt → image）：生成绿幕演员图，再本地抠成 RGBA cutout。

开通流程：

```text
① 注册并实名认证
   访问 https://www.volcengine.com 注册火山引擎账号，完成实名认证
   （个人实名即可；若模型页要求企业认证，则需企业账号）。

② 开通"火山方舟"大模型服务
   进入火山方舟控制台 https://console.volcengine.com/ark
   首次使用按提示开通方舟服务（同意服务协议）。

③ 开通目标模型
   在方舟"模型广场"找到 Seedream 5.0 Pro（图像生成/编辑模型），
   确认模型版本 ID：doubao-seedream-5-0-pro-260628
   点击"开通/申请开通"，完成付费/授权确认（该模型按生成张数计费，
   账户需有余额或已领取额度）。

④ 创建 API Key
   方舟控制台 → "API Key 管理" → 创建 API Key
   （Key 形如 ark-xxxxxxxx-...，只显示一次，立即保存）。

⑤ 确认区域
   代码固定走北京区端点 ark.cn-beijing.volces.com，
   开通模型时无需选择区域（模型 ID 全局可用）。
```

> 计费提示：Stage 2 共约 **9 次文生图**（cutout 素材）+ **5 个变体首帧各 1 次编辑**
> （v01–v04、v10；单相机）。失败重试（代码内置 2 次）与重新生成 cutout 会增加调用量。

### 2.3 写入凭据文件（格式必须精确）

凭据**只能**放在 `~/access/`，不入库、不写进脚本/docs/日志。新建
**`~/access/image_editor_model.txt`**，内容必须严格匹配代码里的两条正则：

```text
ARK_API_KEY: <你的方舟 API Key>
模型：doubao-seedream-5-0-pro-260628
```

注意：

- `ARK_API_KEY:` 后是**半角冒号 + 空格**（正则 `ARK_API_KEY:\s*(\S+)`）；
- `模型：` 后是**全角冒号**（正则 `模型：(\S+)`，冒号紧跟模型 ID 不留空格）；
- 两行之外可以有其他说明文字，但两行的上述模式必须各出现一次。

校验文件可被解析（不会打印密钥本体）：

```bash
grep -qE 'ARK_API_KEY:\s*\S+' ~/access/image_editor_model.txt && echo "key line ok"
grep -qE '模型：\S+'       ~/access/image_editor_model.txt && echo "model line ok"
```

如果你的 host 用户不是 `vipuser`，wizard 挂载凭据的默认 host 路径
（`/home/vipuser/access/image_editor_model.txt`）需要用环境变量覆盖：

```bash
export STAGE2_EDITOR_CREDENTIALS_PATH="$HOME/access/image_editor_model.txt"
export STAGE2_VARIANT_FILE="$HOME/simulation/configs/stage2/variants.yaml"
export STAGE2_ANCHOR_HOST="/mnt/artifacts/stage2/anchor"
```

---

## 3. 应用 Stage 2 补丁

在 Stage 1 补丁之上，再应用两个 Stage 2 补丁（内层仓库必须仍在 pinned commit、
工作树无其他改动）：

```bash
cd ~/simulation

cd repos/alpasim
git apply ../../configs/local-patches/patches/alpasim-stage2-001-synthetic-scenarios.patch
cd ../flashdreams
git apply ../../configs/local-patches/patches/flashdreams-stage2-001-multiframe-anchor.patch
cd ~/simulation
```

补丁内容概览（详见 `docs/Stage2_Complete.md` §4）：

| 补丁 | 作用 |
|---|---|
| `alpasim-stage2-001` | 新增 `alpasim_runtime/stage2/`（`variant_actors` 合成演员、`frame_edit` 首帧编辑、`first_frame_anchor` 首帧贴图、`seeds` 共享种子）、`tests/stage2/`、wizard extras `stage2_env.yaml`（STAGE2_* 门控 + 只读挂载 + 强制串行）；接线 event loop / session configs / video model service；首帧时间戳透传 |
| `flashdreams-stage2-001` | renderer 侧新增 `omnidreams/impl/stage2/multiframe_anchor.py`（每 chunk 重贴演员 + asset-alpha 局部 latent patch + 红种/EDT ghost 清除），并在 gRPC server 接线。**缺它 `STAGE2_MULTIFRAME_ANCHOR=1` 是空开关** |

### 3.1 验证补丁

```bash
cd ~/simulation/repos/alpasim
.venv/bin/python -m pytest src/runtime/tests/stage2 src/runtime/tests/video_model -q
# 期望：85 passed

# Task 2.4 总 gate（补丁可反向应用 + Stage1 回归完好）
.venv/bin/python ~/simulation/scripts/check_stage2_task24.py
# 期望：[SUCCESS] Stage2 Task 2.4: patches exported, Stage1 regression intact.
```

> 关键点：Stage 2 代码全部由 STAGE2_* 环境变量门控；**不设这些变量时行为与
> Stage 1 逐字节一致**（已由 seeded 像素回归证明），因此补丁可以安全地常驻。

---

## 4. 准备变体规格与 anchor 目录

### 4.1 变体矩阵（权威定义在 `configs/stage2/variants.yaml`）

| ID | 类别 | 首帧编辑 | 注入演员（rig 坐标见 variants.yaml） |
|---|---|---|---|
| v00_baseline | 零干预基线 | — | — |
| v01_heavy_snow_blizzard | 天气 | 暴雪 | — |
| v02_torrential_rain_night | 天气+光照 | 深夜暴雨 | — |
| v03_dense_fog_dawn | 天气 | 黎明浓雾 | — |
| v04_blinding_sunset_glare | 光照 | 日落眩光 | — |
| v05_stroller_jaywalking | VRU 横穿 | — | 推婴儿车行人（右侧横穿） |
| v06_wheelchair_shoulder | 罕见移动代理 | — | 电动轮椅（右侧路肩同向） |
| v07_construction_excavator | 大型道路障碍 | — | 停止的挖掘机 |
| v08_loose_cow | 动物事件 | — | 静止奶牛 |
| v09_construction_debris | 障碍组 | — | 2 个桶 + 1 个木箱 |
| v10_typhoon_compound | 复合长尾 | 台风 | 落枝 + 撑伞行人 |

`variants.yaml` 的关键字段：

- 每个演员的 `start_xy_rig` / `yaw_deg` 是**演员激活时刻的 ego-rig 相对坐标**
  （x 前、y 左、z 上）；patch 用该时刻的 rig GT 位姿变换到 clip 全局坐标；
- `activate_s`：演员轨迹窗口开启时刻（相对首帧）；`traj_end_s`：轨迹结束；
- `frame_edit_prompt`（中文）：仅 v01–v04、v10 有，在 session 起步时调用 Ark 编辑首帧；
- 文件头 `base_scene` 必须与运行场景一致，否则 Stage2 逻辑静默不生效（重要排错点）。

### 4.2 创建 anchor 目录

anchor 是外环（wizard 容器只读挂载）与 renderer（host 直读）共享的资产根目录：

```bash
mkdir -p /mnt/artifacts/stage2/anchor/assets
mkdir -p /mnt/artifacts/stage2/anchor/debug
```

结构：

```
/mnt/artifacts/stage2/anchor/
├── assets/          # 每个演员一张 <actor_id>.png（RGBA cutout），缺失即报错
├── debug/           # ghost 清除调试图（STAGE2_ANCHOR_DEBUG=1 时产出）
└── current_variant.txt  # 批量驱动文件：每个变体开跑前由脚本写入当前 variant id
```

### 4.3 生成 9 张演员 cutout

在 **flashdreams 项目环境**中运行（脚本文生图调用 Ark，随后用 BiRefNet 自动抠图，
首次运行会下载 BiRefNet 到 HF 缓存；GPU 用于 matting，任一空闲卡即可）：

```bash
cd ~/simulation/repos/flashdreams
C=../../configs/stage2/cutout_prompts

uv run --package flashdreams-omnidreams python ../../scripts/stage2_make_cutout.py \
  --name pedestrian_stroller --prompt-file $C/pedestrian_stroller.txt
uv run --package flashdreams-omnidreams python ../../scripts/stage2_make_cutout.py \
  --name powered_wheelchair --prompt-file $C/powered_wheelchair.txt
uv run --package flashdreams-omnidreams python ../../scripts/stage2_make_cutout.py \
  --name stopped_excavator --prompt-file $C/stopped_excavator.txt
uv run --package flashdreams-omnidreams python ../../scripts/stage2_make_cutout.py \
  --name dairy_cow --prompt-file $C/dairy_cow.txt
uv run --package flashdreams-omnidreams python ../../scripts/stage2_make_cutout.py \
  --name barrel_1 --prompt-file $C/barrel_1.txt
uv run --package flashdreams-omnidreams python ../../scripts/stage2_make_cutout.py \
  --name barrel_2 --prompt-file $C/barrel_2.txt
uv run --package flashdreams-omnidreams python ../../scripts/stage2_make_cutout.py \
  --name crate_1 --prompt-file $C/crate_1.txt
uv run --package flashdreams-omnidreams python ../../scripts/stage2_make_cutout.py \
  --name fallen_branch --prompt-file $C/fallen_branch.txt
uv run --package flashdreams-omnidreams python ../../scripts/stage2_make_cutout.py \
  --name pedestrian_umbrella --prompt-file $C/pedestrian_umbrella.txt
```

完整映射（`--name` 必须与 `variants.yaml` 中演员 `id` 完全一致）：

| `--name` | prompt 文件 | 所属变体 |
|---|---|---|
| pedestrian_stroller | pedestrian_stroller.txt | v05 |
| powered_wheelchair | powered_wheelchair.txt | v06 |
| stopped_excavator | stopped_excavator.txt | v07 |
| dairy_cow | dairy_cow.txt | v08 |
| barrel_1 / barrel_2 | barrel_1.txt / barrel_2.txt | v09 |
| crate_1 | crate_1.txt | v09 |
| fallen_branch | fallen_branch.txt | v10 |
| pedestrian_umbrella | pedestrian_umbrella.txt | v10 |

注意：

- 默认 matting 为 `birefnet`（推荐，软 alpha + 去绿溢色）；`--matting chroma`
  为纯绿幕抠图后备路径（需 cv2）；
- 重新生成同名 cutout 时，旧文件自动备份为 `<name>.png.prev`；
- 产物自检：9 张 PNG 均为 RGBA、非透明区域明显、无大片绿边。

```bash
ls -la /mnt/artifacts/stage2/anchor/assets/*.png | wc -l   # 应为 9
```

---

## 5. 启动 OmniDreams renderer（GPU1，务必手动启动）

**必须在跑批量之前手动启动 renderer**，并带上 Stage 2 环境变量：

```bash
STAGE2_MULTIFRAME_ANCHOR=1 \
STAGE2_VARIANT_SPEC=~/simulation/configs/stage2/variants.yaml \
STAGE2_ANCHOR_PATH=/mnt/artifacts/stage2/anchor \
  bash ~/simulation/scripts/start_renderer.sh
```

说明：

- **`STAGE2_VARIANT_ID` 故意不设**——批量时 renderer 从
  `<anchor>/current_variant.txt`（每个变体开跑前由批量脚本写入）读取当前变体；
  若只跑单个变体（如排错），则显式设 `STAGE2_VARIANT_ID=<vid>`；
- `start_renderer.sh` 会把上述变量转发给 renderer 进程；renderer 在 host 上运行，
  直接读取 host 上的 variants.yaml 与 anchor 目录；
- 就绪标志（数分钟，含权重加载与 CUDA13 插件 JIT）：

```text
Server started successfully. Press Ctrl+C to stop.
```

**为什么不能依赖批量脚本自动拉起 renderer**：`run_stage2_separate.sh` 检测到端口未开时，
会自动调用 `scripts/start_renderer_direct.sh`（绕开 torchrun 的直启脚本），而该脚本
**不转发任何 STAGE2_* 变量**——那样 renderer 会正常服务，但多帧锚定全程静默关闭，
产出的是质量更差的旧方法结果。手动启动后端口已开，批量脚本会跳过自动拉起。

> 若 torchrun 在你的机器上卡死（TCPStore 反向解析问题，确认 `/etc/hosts` 含
> `127.0.0.1 pc_3` 后通常即解决），可改用 `start_renderer_direct.sh`，
> 但需自行把上述 STAGE2_* 变量以 env 前缀传入（参照该脚本内现有 env 列表）。

### 5.1 可选调参变量（默认值通常不用动）

| 变量 | 默认 | 含义 |
|---|---|---|
| `STAGE2_GEOM_GHOST` | `1` | 是否启用 cuboid 包络几何 ghost 种子清除；设 `0` 关闭 |
| `STAGE2_GHOST_SCALE` | `1.0` | ghost 包络相对脚部锚点的放大倍数（覆盖模型续演的大部件）；`2.2` 曾在 v07 实验期使用，最终批次已还原为 1.0（见 `docs/Stage2_Complete.md`） |
| `STAGE2_CLOSE_GATE` | `0.9` | 相对演员高度的闭合判定阈值 |
| `STAGE2_ANCHOR_DEBUG` | 未设 | 设 `1` 输出 ghost 清除调试图到 `<anchor>/debug/` |

注意 `start_renderer.sh` 默认只转发 MULTIFRAME / VARIANT_SPEC / VARIANT_ID /
ANCHOR_PATH / ANCHOR_DEBUG；其余变量需调整时，请在启动 shell 中 export 后
自行在 `start_renderer.sh` 的转发列表中补上变量名。

---

## 6. 运行 11 个变体

另开一个 shell（renderer 持续运行不要关）：

```bash
cd ~/simulation
bash scripts/run_stage2_separate.sh
```

可调环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `MANIFEST` | `configs/stage2/manifests/stage2_variants.csv` | 11 行变体清单（含每变体 seed） |
| `RUN_TAG` | `stage2_20261002` | 产物目录后缀：`/mnt/artifacts/stage2/run_$RUN_TAG` |
| `RENDERER_PORT` | `50051` | renderer gRPC 端口（脚本内部变量名是 `PORT`，请通过 `RENDERER_PORT` 覆盖） |

脚本行为：

1. 确认 renderer 端口可达（不可达才自动拉起——见 §5 的警告）；
2. 按 manifest 逐行串行处理：写入 `current_variant.txt` → 以
   `STAGE2_VARIANT_ID` / `STAGE2_RENDER_SEED` 启动一个独立 wizard
   （`+extras=stage2_env`，driver=alpamayo15_1cam_local）；
3. 每个变体结束后 `docker compose down` 清理本变体容器；成功则写 `_success`；
4. 全部完成写 `<run_dir>/_all_success`。

断点续跑：已存在 `_success` 的变体自动跳过，失败中断后**原样重跑同一命令**即可。

各变体内部触发：

- **v01–v04 / v10**：session 起步时调用 Ark 编辑首帧（每次约数十秒，超时 180 s，
  内置 2 次尝试），编辑后的首帧作为 renderer seed，变体文本 prompt 全程传播外观；
- **v05–v10**：合成演员 cuboid 从首帧即存在（`anchor_first_frame` 默认 true），
  演员 cutout 在首帧按 FTheta 投影贴到 cuboid 位置（含色调匹配与接触阴影）；
  renderer 每 chunk 把演员重贴进生成帧，并只在 asset-alpha 足迹内做局部 latent patch；
- **v00**：零干预，所有 Stage2 逻辑 no-op。

预计耗时：每变体约 4–6 分钟（含 driver 容器拉起，20 s rollout 在 ~0.13× 实时下
约 2.5–3 分钟），**11 个共约 1 小时**；首帧编辑变体另加 API 等待。

---

## 7. 验收（基于逐帧证据）

批量完成后依次运行（将 `<RUN>` 换成你的运行目录）：

```bash
cd ~/simulation
PY=repos/alpasim/.venv/bin/python
RUN=/mnt/artifacts/stage2/run_stage2_20261002

# ① 11 个 rollout 完整性核验（同时写出 11 行 variant map）
STAGE2_RUN_DIR=$RUN STAGE2_MAP_OUT=$RUN/rollout_variant_map.csv \
  $PY scripts/check_stage2_task33.py

# ② 导出 30 fps 原始帧
MAP=$RUN/rollout_variant_map.csv OUT_BASE=$RUN/frames \
  bash scripts/export_stage2_frames.sh
$PY scripts/check_stage2_task41.py    # 11 变体帧完整、无黑帧

# ③ BEV 渲染与核验
$PY scripts/render_stage2_bev.py $RUN \
  --variants configs/stage2/variants.yaml \
  --map $RUN/rollout_variant_map.csv --out-dir $RUN/bev
STAGE2_BEV_DIR=$RUN/bev STAGE2_FRAMES_DIR=$RUN/frames \
STAGE2_MAP=$RUN/rollout_variant_map.csv $PY scripts/check_stage2_task42.py

# ④ HUD 视频（前摄 + BEV + 指标条）与核验
$PY scripts/render_stage2_hud.py \
  --map $RUN/rollout_variant_map.csv --variants configs/stage2/variants.yaml \
  --bev-dir $RUN/bev --out-dir $RUN/videos
STAGE2_VIDEO_DIR=$RUN/videos STAGE2_MAP=$RUN/rollout_variant_map.csv \
  $PY scripts/check_stage2_task43.py
```

验收硬性纪律（与 Stage 1 一致）：

- **不要只看 aggregate `metrics_results.txt`**——截断算子会让越早碰撞的变体数字越"好看"；
  权威依据是 `rollouts/<clip>/<rollout>/metrics.parquet`（long format，
  `values` 需 `pd.to_numeric(errors="coerce")`，同 metric/时间戳多相机行取 max）+ 原始视频；
- **逐帧人工审看视频**：Laplacian 方差等无参考指标对语义形变失明（见 §9）。

产物地图：

```
<RUN>/
├── _all_success
├── <vid>/
│   ├── _success, wizard-console.log, docker-compose.yaml
│   └── rollouts/<scene>/<uuid>/{rollout.asl, metrics.parquet, 前摄 MP4}
├── rollout_variant_map.csv     # variant_id, scene_id, rollout_uuid, asl_path, ...
├── frames/<vid>/.../rollout/camera_front_wide_120fov/*.jpg
├── bev/<vid>/*.png
├── videos/<vid>_hud.mp4
└── analysis/ （§8 生成）
```

---

## 8. 后处理与报告

### 8.1 v05–v10 快速路线（最终批次已验证）

`postprocess_stage2_v05v10.sh` 一条命令完成：建图（v00 + v05–v10 共 7 行）
→ 导帧 → 逐帧分析 → BEV → 前摄/BEV MP4：

```bash
RUN_DIR=<你的运行目录> bash scripts/postprocess_stage2_v05v10.sh
# 不带 RUN_DIR 时处理默认批次 /mnt/artifacts/stage2/run_localpatch_20261003
```

输出 `<RUN_DIR>/{map_v05v10.csv, frames/, analysis/, bev/, mp4/}`。
注意该脚本只覆盖 v00、v05–v10；**v01–v04 请走 §7 的完整流程**。

### 8.2 全 11 变体分析表

```bash
RUN=/mnt/artifacts/stage2/run_stage2_20261002
repos/alpasim/.venv/bin/python scripts/analyze_stage2.py $RUN \
  --variants configs/stage2/variants.yaml \
  --map $RUN/rollout_variant_map.csv --out-dir $RUN/analysis
# 产出 per_variant.csv（碰撞/offroad/进度/轨迹偏差/首末事件时刻）、
# reaction_table.csv（首次减速/最大减速度/末速）等，无 NaN
```

### 8.3 有效期（valid-window）分析——报告事件统计的正确口径

OmniDreams 长闭环在**每个变体各自的时刻**之后会发生明确的几何/语义崩解
（v00/v05/v06 为渲染器固有漂移，v07–v10 为演员诱发），因此事件统计必须按
有效期截断，窗口定义在 `configs/stage2/manifests/stage2_v05v10_valid_windows.csv`：

| 变体 | 有效末帧 | 有效末刻 | 窗口外现象 |
|---|---|---|---|
| v00 / v05 / v06 | 199 | 6.63 s | 树干巨型化、前景防水布化（固有漂移） |
| v07 | 59 | 1.97 s | 模型续演的机械臂变成跨路巨型拱 |
| v08 / v09 / v10 | 149 | 4.97 s | 巨型毛墙/木架/伞丘抽象 |

```bash
repos/alpasim/.venv/bin/python scripts/analyze_stage2_validwindow.py \
  --map $RUN/rollout_variant_map.csv \
  --windows configs/stage2/manifests/stage2_v05v10_valid_windows.csv \
  --variants configs/stage2/variants.yaml \
  --out $RUN/analysis/validwindow_per_variant.csv
```

（该 manifest 覆盖 v00、v05–v10；v01–v04 天气变体外观寿命更长，未列入。）

报告生成与归档参考 `scripts/generate_stage2_report.py` 和
`docs/Stage2_Report_v05v10_localpatch.md`。

---

## 9. 结果解读：妥协与已知局限（复现时务必理解）

**Stage 2 的画面成功不等于全片段有效**。两个核心事实（完整推导见
`docs/Stage2_Complete.md` §5）：

1. **我们刻意避开了论文 §9.3.2 的 post-training**（randomized dynamic-cuboid dropout，
   需要更新模型权重），只做外环 RGB-only 干预 + asset-only latent patch。
   代价是：模型对 OOD 演员会**自发生成自己的、更大的续演体**（出现在粘贴资产之外，
   红种/EDT 无法去除），并在 autoregressive 循环中不断放大。
2. **有效期与 OOD 程度负相关**：外观最异常的 v07 挖掘机 ~2 s 后失效；
   v08–v10 约 5 s；最贴近训练分布的 v05/v06 与零干预 v00 同寿（~6.6 s，止于渲染器
   固有漂移）。

因此复现验收的正确期望是：

- **天气/光照（v01–v04）**：首帧编辑后外观成功传播；但驾驶行为对天气**无响应**
  （部署的 distilled checkpoint 对 prompt-only 条件的动作通道不敏感，已做 A/B 证实）；
- **注入演员（v05–v10）**：在各自有效期内，演员像素、cuboid 与驾驶反应三者一致
  （v05 触发制动/碰撞、v07–v10 触发避让等）；窗口外的巨型化是已知模型局限，
  **不是你的部署错误**——但必须在报告中按有效期截断并如实说明；
- v00 零干预基线同样在 ~6.6 s 后漂移，用于区分"干预导致"与"渲染器固有"两类现象，
  **每次复现都必须一起跑**。

---

## 10. 时间与磁盘预算

| 项目 | 数值 |
|---|---|
| Ark API：cutout 文生图 | 9 张（建议一次准备完毕，约 10–20 分钟含人工检查） |
| Ark API：首帧编辑 | 5 个变体各 1 张（v01–v04、v10） |
| 11 变体串行闭环 | 约 1 小时（每变体 4–6 分钟） |
| 单变体产物 | rollout.asl ~0.4–1 GB + metrics.parquet + MP4 |
| 11 变体全部后处理产物 | 数 GB（落在 `/mnt/artifacts`） |

收尾（必须执行）：

```bash
bash scripts/stop_renderer.sh
nvidia-smi --query-gpu=memory.used --format=csv,noheader   # 两卡均应回到 14 MiB
docker ps -q | xargs -r docker stop; docker container prune -f
```

---

## 11. 故障排查速查

| 症状 | 原因 / 处理 |
|---|---|
| 变体看起来与基线完全一样 | ① `variants.yaml` 的 `base_scene` 与运行场景不一致（逻辑静默 no-op）；② STAGE2_VARIANT_ID 拼错；③ 补丁未应用。用 `scripts/extract_stage2_seed.py` 查 asl 中的实际 seed/prompt |
| renderer 日志无 multiframe 相关行为 | renderer 由 `start_renderer_direct.sh` 自动拉起，STAGE2_* 未转发。停掉后按 §5 手动启动；确认 `STAGE2_MULTIFRAME_ANCHOR=1` |
| `STAGE2_MULTIFRAME_ANCHOR=1 requires STAGE2_VARIANT_SPEC ...` | renderer 读不到变体规格或 id：检查 VARIANT_SPEC 路径存在；批量模式下确认 `<anchor>/current_variant.txt` 已写入 |
| 起步报 `credentials file missing` | frame-edit 变体（v01–v04、v10）需要凭据：确认 host 上 `~/access/image_editor_model.txt` 存在，wizard extras 挂载路径正确（见 §2.3） |
| `ARK_API_KEY` / `模型` 正则报错 | 凭据文件格式不符：半角冒号在 ARK_API_KEY 后，**全角冒号**在"模型"后（见 §2.3） |
| Ark 返回 401/403 | Key 错误、已删除，或模型未开通/欠费；回方舟控制台核对 API Key、模型开通状态与余额 |
| Ark 请求超时/URLError | 代码自动重试 2 次；持续失败检查网络（北京区端点），必要时换网络后重跑该变体（有续跑机制） |
| `missing anchor cutout asset: .../assets/<id>.png` | 演员 cutout 未生成或命名不符；按 §4.3 生成，`--name` 必须等于演员 id |
| 首帧编辑后构图/透视被改变 | 编辑 prompt 约束不足；检查 variants.yaml 中"保持几何/透视不变"语句是否保留，必要时重新生成（成本仅一次 API 调用） |
| v07–v10 数秒后物体变大变抽象 | **已知模型局限**（§9），不是部署错误；核对是否与 §8.3 记录的有效期一致 |
| `Session not found` | renderer 单活动 session 约束被破坏：确认 `+extras=stage2_env` 生效、nr_workers/各 n_concurrent_rollouts 均为 1（可用 run_method=NONE 检查，见 §8 of Stage1 guide） |
| torchrun 卡 TCPStore 5 分钟 | `/etc/hosts` 加 `127.0.0.1 pc_3`；或用 `start_renderer_direct.sh` 并自行传入 STAGE2_* 变量 |
| 变体运行中断 | 直接重跑同一命令：`_success` 变体会跳过，renderer 复用（若 renderer 也停了，先按 §5 重启） |

---

## 12. Git 纪律

- 每个 commit author = **Junchuan Zhang <zjunchuan@gmail.com>**，提交后用
  `git log -1 --format='%an <%ae>'` 核实；message 末尾加
  `Co-Authored-By: Claude Code <noreply@anthropic.com>`。
- **Ark API Key（`ark-…`）与其他密钥不得入库**；提交前扫描
  `hf_…`、`ghp_…`、`nvapi-…`、`ark-…`。凭据只存在 `~/access/`。
- `repos/` 内层仓库的改动不在外层跟踪范围；重建机器时按 Stage1 指南 §4 与本文 §3
  重新应用全部补丁，按 §4–§5 重建 anchor 资产。

---

## 附录 A：从零复现检查单（TL;DR）

```bash
# 前提：Stage 1 已部署完成（见 docs/Stage1_Reproduction_Guide.md）
source ~/simulation/scripts/env.sh

# 1. 权限：火山方舟开通 doubao-seedream-5-0-pro-260628 → 写 ~/access/image_editor_model.txt（§2.3）

# 2. 应用 Stage 2 补丁并验证（§3）
(cd repos/alpasim     && git apply ../../configs/local-patches/patches/alpasim-stage2-001-synthetic-scenarios.patch)
(cd repos/flashdreams && git apply ../../configs/local-patches/patches/flashdreams-stage2-001-multiframe-anchor.patch)
(cd repos/alpasim && .venv/bin/python -m pytest src/runtime/tests/stage2 src/runtime/tests/video_model -q)

# 3. anchor 目录 + 9 张 cutout（§4）
mkdir -p /mnt/artifacts/stage2/anchor/{assets,debug}
# 按 §4.3 表在 repos/flashdreams 内逐张生成（共 9 条命令）

# 4. 手动启动 renderer（§5）
STAGE2_MULTIFRAME_ANCHOR=1 \
STAGE2_VARIANT_SPEC=~/simulation/configs/stage2/variants.yaml \
STAGE2_ANCHOR_PATH=/mnt/artifacts/stage2/anchor \
  bash scripts/start_renderer.sh

# 5. 另开 shell 跑 11 变体（§6）
bash scripts/run_stage2_separate.sh

# 6. 验收 + 后处理（§7、§8），按有效期口径解读结果（§9），最后释放资源（§10）
```
