# local-patches：内层仓库的关键本地改动（权威副本）

本目录保存部署 Stage 1 时对**内层上游仓库**所做改动的可追溯副本。

**为什么需要这里**：内层仓库（`repos/alpasim`、`repos/flashdreams`、
`repos/alpamayo`、`repos/omni-dreams`）直接 clone 自 NVIDIA/NVlabs 上游，外层项目
仓库的 `.gitignore` 又排除了整个 `repos/` 目录——因此这些改动既无法随外层项目
push 到 `GimpelZhang/Cosmos-Dreams-AlpaSim`，也不应提交到上游。重建机器时，在
内层仓库 checkout 到指定 commit 后，按本 README 应用/拷贝这些文件即可。

适用版本（与 `docs/Stage1_Reproduction_Guide.md` §4 一致）：

| 内层仓库 | commit |
|---|---|
| `repos/alpasim` | `affc2ea` |
| `repos/alpamayo` | `11a0e01` |
| `repos/flashdreams` | `0957cf0` |
| `repos/omni-dreams`（参考仓库，无补丁） | `cd85f39` |

Stage 1 复现所需的最小补丁集合、应用时机与拷贝命令见
`docs/Stage1_Reproduction_Guide.md` §4；下方命令块是含 Stage 2 补丁的全集。

## 内容清单

### `wizard-configs/` — 四个新建的 wizard 配置

原样拷贝到 `repos/alpasim/src/wizard/configs/` 对应位置：

| 本目录文件 | 目标路径 | 用途 |
|---|---|---|
| `driver/alpamayo1_1cam.yaml` | `…/configs/driver/` | Alpamayo-R1 单相机 preset |
| `driver/alpamayo15_1cam_local.yaml` | `…/configs/driver/` | Alpamayo 1.5 本地权重单相机 preset |
| `extras/r1_weights.yaml` | `…/configs/extras/` | R1 driver 挂载（含权重只读挂载，列全默认卷） |
| `extras/a15_weights.yaml` | `…/configs/extras/` | 1.5 driver 挂载 + 离线环境变量（gated 401 修复） |

```bash
cp configs/local-patches/wizard-configs/driver/*.yaml \
   repos/alpasim/src/wizard/configs/driver/
cp configs/local-patches/wizard-configs/extras/*.yaml \
   repos/alpasim/src/wizard/configs/extras/
```

### `patches/` — 对内层跟踪文件的修改（git diff 格式）

在对应内层仓库根目录执行 `git apply`：

```bash
cd repos/alpasim
git apply ../../configs/local-patches/patches/alpasim-001-dockerignore-uvlock.patch
git apply ../../configs/local-patches/patches/alpasim-002-dockerfile-mirrors-frozen.patch
git apply ../../configs/local-patches/patches/alpasim-stage2-001-synthetic-scenarios.patch
cd ../alpamayo
git apply ../../configs/local-patches/patches/alpamayo-001-test-inference-local-path.patch
cd ../flashdreams
git apply ../../configs/local-patches/patches/flashdreams-stage2-001-multiframe-anchor.patch
```

补丁内容与原因：

1. **`alpasim-001-dockerignore-uvlock.patch`**：`.dockerignore` allowlist 放行
   `uv.lock`、`.python-version`。否则镜像构建时 uv 重新解析依赖树报
   "No solution found"（复现指南 §6.1）。
2. **`alpasim-002-dockerfile-mirrors-frozen.patch`**：Dockerfile 内 rustup/crates.io
   /PyPI 全部走 TUNA 镜像，所有 `uv sync` 加 `--frozen`、compile-protos 加
   `--frozen --no-sync`（中国大陆构建必需；非大陆环境可保留 `--frozen` 部分、
   将镜像 URL 换回官方源）。
3. **`alpamayo-001-test-inference-local-path.patch`**：`test_inference.py` 的
   clip_id 与模型路径改为环境变量（`ALPAMAYO_CLIP_ID`、`ALPAMAYO_R1_PATH`），
   支持本地权重路径，不再硬编码在线仓库。

4. **`alpasim-stage2-001-synthetic-scenarios.patch`**（Stage 2）：新增
   `alpasim_runtime/stage2/`（变体演员注入 `variant_actors.py`、首帧外部图像编辑
   `frame_edit.py`、首帧 RGBA cutout 锚定 `first_frame_anchor.py`、渲染种子 `seeds.py`）、
   `tests/stage2/`（21 项单测）、wizard extras `stage2_env.yaml`（STAGE2_* 特性门控、
   凭据/variants/anchor 只读挂载、强制串行），并接线 event loop、session configs、
   video model service、unbound rollout；`video_model/utils.py` 额外返回首帧时间戳。
   细节见 `docs/Stage2_Complete.md`。
5. **`flashdreams-stage2-001-multiframe-anchor.patch`**（Stage 2）：renderer 侧新增
   `omnidreams/impl/stage2/`（多帧重贴 + 局部 latent patch + 红种/EDT ghost 清除，
   即经人工审核批准的 run-g 方法），并在 `impl/grpc/server.py` 接线调用。
   renderer 由 `scripts/start_renderer.sh` 直接从本仓库源码启动，缺此补丁则
   STAGE2_MULTIFRAME_ANCHOR 无任何效果。细节见 `docs/Stage2_Complete.md` §4。

五个补丁均已在对应 pinned commit 的干净 worktree 上用 `git apply --check` 验证，
两个 Stage 2 补丁另在当前工作树做过 `git apply -R` / `git apply` 往返。

后处理脚本（建图→导帧→逐帧分析→BEV→MP4）在 `scripts/postprocess_stage2_v05v10.sh`，
其变体映射建图为 `scripts/build_stage2_map.py`（基线 rollout 路径可用 `STAGE2_V00_ASL`
覆盖）。

### `reference-data/` — 参考结果

- `batch30_summary.csv`：A15 × 30 场景批量闭环的逐 clip 分析结果
  （由 `scripts/analyze_batch_a15.py` 生成），用于回归对照，详见
  `docs/Stage1_Batch30_Report.md`。

## 未纳入的本地改动（有意排除）

- 内层仓库中的 `.omc/`、`.venv`（指向 `/mnt` 的软链）、`data/drivers`、
  `data/nre-artifacts`（指向 `/mnt` 的数据软链）：均为环境/工具状态，非代码改动。
- `repos/alpasim` 中 `data/nre-artifacts/ego-hoods/*.png` 的 "删除"：把该目录替换成
  `/mnt` 软链时产生的表象，并非有意删除文件。
- `artifacts/`：运行产物（日志、视频、指标），体积大且可再生，不入库。
