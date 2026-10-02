# Stage 1 代码级架构解析：OmniDreams × AlpaSim 闭环仿真

> 本文基于内层仓库 pinned commit 解析：`repos/alpasim` @ `affc2ea`（AlpaSim 0.134.0）、
> `repos/flashdreams` @ `0957cf0`（OmniDreams 渲染服务）、`repos/alpamayo` @ `11a0e01`
> （Alpamayo R1 模型包；1.5 模型代码以 pip 依赖形式装入 driver 镜像）。
> 文中路径均相对各仓库根目录。复现步骤见 [Stage1_Reproduction_Guide.md](Stage1_Reproduction_Guide.md)，
> 运行结果见 [Stage1_Batch30_Report.md](Stage1_Batch30_Report.md)。
> 想只跟踪一个控制步内 Driver 的输入/输出（含 RPC 载荷），见
> [Stage1_Driver_IO_Cycle.md](Stage1_Driver_IO_Cycle.md)。

---

## 0. 一句话与一张图

**AlpaSim 是一组 gRPC 微服务**：`runtime` 是唯一的"大脑/调度器"，每个仿真步它按固定顺序
调用五个端点——driver（驾驶策略）→ controller（控制 + 车辆动力学）→ physics（地面贴合）
→ trafficsim（其他交通参与者）→ renderer（OmniDreams 神经渲染），把渲染出的图像再喂回
driver，如此循环约 20 秒，结束后由评估子进程从完整记录算出逐帧指标。

```
┌─────────────────────────────── 宿主机 ubuntu2 ───────────────────────────────┐
│                                                                              │
│  scripts/run_closed_loop_a15.sh / run_batch_a15.sh                           │
│   └─ uv run alpasim_wizard  (Hydra 编排，只在启动阶段工作)                     │
│        ├─ 下载场景 USDZ → /mnt/alpasim-data、repos/alpasim/data/nre-artifacts │
│        ├─ 渲染 docker-compose.yaml + 每个服务的 YAML 配置                     │
│        └─ docker compose up --exit-code-from runtime-0                       │
│                                                                              │
│  ┌──── GPU0 (CUDA_VISIBLE_DEVICES=0) ──────────────┐  ┌──── GPU1 ──────────┐ │
│  │ docker 容器 (alpasim-base 镜像, host 网络)       │  │ 宿主机进程          │ │
│  │  ┌────────────┐ ┌────────────┐ ┌──────────────┐ │  │ OmniDreams server  │ │
│  │  │ driver-0   │ │ physics-0  │ │ controller-0 │ │  │ omnidreams.impl.   │ │
│  │  │ Egodriver  │ │ Physics    │ │ VDCService   │ │  │ grpc.server        │ │
│  │  │ Service    │ │ Service    │ │ (无GPU保留)   │ │  │ WorldModelService  │ │
│  │  │ :6005      │ │ :6006      │ │ :6007        │ │  │ 0.0.0.0:50051      │ │
│  │  └────▲───────┘ └────▲───────┘ └──────▲───────┘ │  │          ▲          │ │
│  │       │              │                │         │  │          │          │ │
│  │  ┌────┴──────────────────────────────────────┐  │  │          │          │ │
│  │  │ trafficsim：本部署 skip=true，无容器        │  │  │          │          │ │
│  │  │ 其他 actors 由 runtime 本地按录制轨迹回放； │  │  │          │          │ │
│  │  │ CATK/SMART 学习式预测未启用（见 §7.4）      │  │  │          │          │ │
│  │  └───────────────────────────────────────────┘  │  └──────────┼──────────┘ │
│  │                   ┌─┴─────────── gRPC (video_model.proto) ────┘            │
│  │  ┌────────────────┴───────────────┐            render_video_chunk          │
│  │  │ runtime-0 (无GPU保留)           │────────── rig_trajectory +            │
│  │  │ RuntimeService / DaemonEngine  │            dynamic_world_state         │
│  │  │ 事件堆 + 地址池 + worker        │◄────────── JPEG 帧 (1280×704 q90)     │
│  │  └────────────────────────────────┘                                       │
│  └───────────────────────────────────────────────────────────────────────────┘
│         全部产物 → /mnt/artifacts/run_<tag>/  (rollouts/、aggregate/、日志)    │
└──────────────────────────────────────────────────────────────────────────────┘
```

实际端口由 wizard 从 `baseport=6000` 起探测空闲端口分配，上图 6005–6007 为示意；
renderer 固定 :50051。`deploy=external_video_model` 设 `debug_flags.use_localhost=true`，
故全部容器以 `network_mode: host` 运行，runtime 经 `127.0.0.1:<port>` 访问各端点。

**本项目实际的托管服务只有四个容器**：`driver-0`、`physics-0`、`controller-0`、`runtime-0`
（外加外部 OmniDreams 进程）。默认 `trafficsim=disabled` 渲染为
`runtime.endpoints.trafficsim.skip=true`：不启动 trafficsim 容器、不发起 gRPC，
场景中其他 actors 由 runtime **在本地按录制轨迹回放**（见 §7.4）；handover 之后的
CATK/SMART 学习式预测全程未启用（权重未下载）。批量脚本里的
`runtime.endpoints.trafficsim.n_concurrent_rollouts=1` 只是对空池无害的配置项。

---

## 1. 代码地图

### repos/alpasim（uv workspace，每个端点一个独立 Python 包）

| 路径 | 包/角色 |
|---|---|
| `src/wizard/alpasim_wizard/` | 部署向导：Hydra 入口、配置 schema、场景下载、compose/配置渲染、容器生命周期 |
| `src/wizard/configs/` | 全部 Hydra 配置（见 §2） |
| `src/runtime/alpasim_runtime/` | 运行时核心：daemon（engine/scheduler）、worker、事件堆、五个端点的 gRPC 客户端 |
| `src/driver/src/alpasim_driver/` | Egodriver 服务端：模型加载、推理批处理、`drive` RPC；`models/` 下是 Alpamayo 适配器 |
| `src/controller/alpasim_controller/` | VDC 服务端：MPC 控制 + 自行车模型车辆动力学 |
| `src/physics/alpasim_physics/` | Physics 服务端：仅地面相交（ray-mesh），不做动力学/碰撞 |
| `src/trafficsim/alpasim_trafficsim/` | Traffic 服务端：日志回放 → CATK/SMART 学习式预测的混合模型 |
| `src/grpc/alpasim_grpc/v0/*.proto` | 9 个 gRPC 契约（见 §5） |
| `src/eval/src/eval/` | 评估：指标打分器（碰撞/offroad/minADE…）、聚合、`metrics.parquet`、视频渲染 |

### repos/flashdreams（uv workspace；OmniDreams 是其中一个集成包）

| 路径 | 角色 |
|---|---|
| `integrations_v2/omnidreams/impl/grpc/server.py` | gRPC 服务端（`WorldModelService` + `WorldModelEngine`），~1500 行 |
| `integrations_v2/omnidreams/impl/grpc/protos/` | 独立 vendor 的 `video_model.proto`/`camera.proto`/`common.proto`（与 alpasim 副本线上兼容） |
| `integrations_v2/omnidreams/impl/pipeline.py` | 生成流水线：条件渲染 → VAE 编码 → DiT 去噪 → TAE 解码 |
| `integrations_v2/omnidreams/impl/conditioning/` | 条件包装器、Ludus 地图/线框盒子光栅化 |
| `integrations_v2/omnidreams/config.py` | pipeline 配置注册表（模型权重 URL、调度器参数） |
| `integrations_v2/omnidreams/impl/ludus-renderer/` | FTheta CUDA 光栅器（JIT 插件）、nvJPEG、ClipGT 加载 |
| `flashdreams/flashdreams/` | 底层框架：VAE（Wan/Taehv）、扩散调度器、DiT、pipeline 基类 |

### repos/alpamayo（模型代码包，被 driver 镜像内的 alpasim_driver 以库形式导入）

| 路径 | 角色 |
|---|---|
| `src/alpamayo_r1/models/alpamayo_r1.py` | `AlpamayoR1(ReasoningVLA)`：VLM 思维链 + expert 扩散动作头 |
| `src/alpamayo_r1/models/base_model.py` | VLM 构建（R1 默认 `Qwen/Qwen3-VL-8B-Instruct`） |
| `src/alpamayo_r1/action_space/unicycle_accel_curvature.py` | 动作空间：(加速度, 曲率) → 64 个轨迹点的独轮车积分 |
| `src/alpamayo_r1/diffusion/flow_matching.py` | Flow matching 采样（10 步 Euler） |
| `src/alpamayo_r1/helper.py` | chat template、图像预处理、system/user prompt |

> **注意一个与直觉相反的事实**：R1 的 VLM 骨干是 **Qwen3-VL-8B**；
> Alpamayo 1.5 的 VLM 骨干是 **nvidia/Cosmos-Reason2-8B**（见 1.5 权重 `config.json`
> 的 `vlm_name_or_path` 字段）。两者都是 **ReasoningVLA**：VLM 先生成文字形式的因果链
> （chain-of-thought），随后一个独立的 **expert LLM 骨干**在 VLM 的 KV-cache 条件下，
> 对 **(加速度, 曲率) 动作序列做 flow-matching 扩散**，再积分成轨迹。

---

## 2. Wizard：配置 → 容器的编排层

Wizard 只在启动阶段存在；容器全部起来、`runtime-0` 开始运行后，wizard 的工作就是等待
`runtime-0` 退出并返回其退出码。

### 2.1 入口与调用链

- 控制台脚本注册：`src/wizard/pyproject.toml:48` →
  `alpasim_wizard = "alpasim_wizard.__main__:main"`。
- `__main__.py` 的 `run_wizard(cfg)`（共 26 行）依次：
  1. `cfg.wizard.log_dir = os.path.abspath(...)`；
  2. `validate_config(cfg)`（`setup_omegaconf.py:144`）——校验必须选择 `deploy`/`topology`，
     driver 启动时必须选择 `driver`，并检查 NRE 版本与外部 driver 设置无冲突；
  3. `update_scene_config(cfg)`——使用 test suite 时清空默认 scene 列表；
  4. `await AlpasimWizard.create(cfg)`。
- Hydra 装饰在 `setup_omegaconf.py:286`：
  `@hydra.main(config_name="base_config.yaml", config_path="…/configs", version_base="1.3")`，
  根配置即 `src/wizard/configs/base_config.yaml`；同时通过
  `cs.store(name="config_schema", node=AlpasimConfig)` 注册结构化 schema
  （`schema.py`），并注册自定义 resolver：`repo-relative`、`cmd-line-args`、`or`、
  `repo-version`、`repo-base-image-tag`。

### 2.2 配置组与本项目的选择

`base_config.yaml` defaults 中的配置组，以及本项目命令行的选择：

| 配置组 | 作用 | 本项目取值 |
|---|---|---|
| `deploy` | 部署形态：`local` / `external_video_model` / `managed_flashdreams` / `docker_build_only` | `external_video_model`（renderer 是宿主机外部进程） |
| `topology` | GPU 映射、副本数、各端点并发、`runtime.nr_workers`（`1gpu`/`2gpu`/`daemon`/`8gpu_*`） | `1gpu`（容器视角只用 GPU0；renderer 在另一物理卡上） |
| `driver` | 驾驶模型 preset（检查点路径、相机、上下文长度、采样数） | R1：`alpamayo1_1cam`；A15：`alpamayo15_1cam_local` |
| `chunking` | 视频块帧数与控制步长 | `+chunking=8frame` |
| `controller` | MPC 实现/时域 | 默认 `default`（非线性 MPC） |
| `trafficsim` | 交通模型 | **默认 `disabled`（本项目所有运行均未改）；`catk` 为可选的学习式预测实现** |
| `renderer` | 渲染器（NRE sensorsim 形态） | 未选——renderer 已从托管服务中移除 |

`RunMethod` 枚举（`schema.py:70`）共四种：

- `DOCKER_COMPOSE`（默认）：生成配置后执行
  `docker compose -f <log_dir>/docker-compose.yaml up --remove-orphans --exit-code-from runtime-0`
  （`deployment/docker_compose.py:58`）；
- `SLURM`：每个容器用 `srun --overlap` 作为重叠 step 启动，可选 NVIDIA MPS；
- `SLURM_ENROOT`：先把镜像转成 `.sqsh` 再经 Enroot 运行；
- `NONE`：**只生成全部文件不启动任何东西**，日志提示用户自行执行 compose up。
  我们用它验证 override（如串行配置、离线环境变量）是否正确渲染。

### 2.3 WizardContext：场景、GPU、端口、目录

`AlpasimWizard.create` → `WizardContext.create`（`context.py:178`）：

1. **场景获取** `fetch_artifacts`（`context.py:22`）：`USDZManager`（`scenes/sceneset.py:202`）
   按 scene_ids 或 test suite 查询、按 scene_id 去重，从 HuggingFace（5 线程）或 S3 下载
   USDZ 到 `all-usdzs/`，再创建一个以 `md5(uuid 集合)` 命名的 **sceneset 目录**，里面是
   指向各 USDZ 的相对符号链接。该目录名就是配置模板里 `{sceneset}` 的替换值。
2. **GPU 探测** `detect_gpus`（`context.py:98`）：nvidia-smi 获取数量/UUID，供拓扑校验。
3. **端口分配**：从 `wizard.baseport=6000` 起用 `create_port_assigner` 探测实际空闲端口，
   并为 telemetry（worker/prometheus/DCGM exporter）预分配端口。
4. **目录** `setup_directories`：在 log_dir 下建 `rollouts/`、`txt-logs/`、`controller/`、
   `prometheus/`（777）。

### 2.4 cast：永远先渲染文件，再决定如何执行

`AlpasimWizard.cast`（`wizard.py:68`）的顺序：

1. （Slurm 模式）检查 allocation；
2. **无条件**生成 compose：`DockerComposeDeployment.generate_docker_compose()`
   （`deployment/docker_compose.py:42`）→ 写 `<log_dir>/docker-compose.yaml`；
3. **无条件**生成每个服务的 YAML 配置：
   `ConfigurationManager(log_dir).generate_all(container_set, context)`
   （`configuration.py:45`）；
4. 按 run_method 执行（compose up / srun / NONE 仅文件）。

`generate_all` 写出的关键文件：

| 文件 | 内容 | 生成函数 |
|---|---|---|
| `docker-compose.yaml` | 五个端点容器 + runtime + prometheus | `deployment/docker_compose.py:196` |
| `generated-user-config-0.yaml` | runtime 主配置：场景列表、USDZ data_dir（`/mnt/nre-data/<sceneset>`）、force-GT 缓存 | `configuration.py:83` |
| `generated-network-config.yaml` | 每个服务的端点地址（managed）+ 外部 renderer（`managed: false`） | `configuration.py:192` |
| `driver-config.yaml` / `controller-config.yaml` / `trafficsim-config.yaml` / `eval-config.yaml` | 各服务静态配置 | 同文件各 `_generate_*` |
| `run_metadata.yaml`、wizard 配置快照 | 运行元数据 | — |

### 2.5 Compose 渲染细节

`_to_docker_compose_service`（`docker_compose.py:96`）：

- **网络**：`use_localhost=true`（external_video_model 自动设置）时用
  `network_mode: host`，否则用 bridge 网络 `microservices_network`，服务间以容器名做 DNS。
- **GPU**：不通过 `CUDA_VISIBLE_DEVICES` 环境变量，而是写 NVIDIA device 预留：
  ```yaml
  deploy.resources.reservations.devices:
    - driver: nvidia
      capabilities: ["gpu"]
      device_ids: ["0"]
  ```
  GPU id 由 `create_gpu_assigner`（`services.py:277`）从服务的 `gpus` 列表中循环分配。
  `runtime-0` 与 `controller-0` 的 `gpus: null`（无 GPU 预留，纯 CPU 服务）。
- 容器入口统一包装为 `bash -c "umask 0000; <cmd>"`；非 external 镜像带 `build:` 块
  （context=仓库根、`Dockerfile`、tag=镜像名），external 镜像只按 `pull_policy` 拉取。
- 服务/容器命名 `"{name}-{container_idx}"`（如 `runtime-0`），既是 compose 服务名也是
  bridge DNS 名。

镜像有三类：

1. **alpasim-base**（tag 如 `alpasim-base:0.134.0`）：仓库根 `Dockerfile` 基于
   `nvidia/cuda:12.4.1-cudnn-devel-ubuntu22.02`，driver/physics/controller/runtime/
   trafficsim 全部共用，仅 command/volumes 不同；本部署构建时加了 TUNA 镜像与
   `uv sync --frozen`（补丁见 `configs/local-patches/`）。
2. **外部拉取镜像**（`external_image: true`），如默认 NRE renderer；
3. **外部独立进程**：本项目的 OmniDreams 根本不在 compose 里，由 `start_renderer.sh`
   在宿主机直接启动，compose 中只有一个 `external_services.renderer=???` 占位，
   命令行经 `wizard.external_services.renderer=["127.0.0.1:50051"]` 注入，
   network-config 中标记 `managed: false`。

---

## 3. Runtime：事件堆驱动的仿真内核

runtime 容器命令（`base_config.yaml` services.runtime）：

```
uv run python -m alpasim_runtime.simulate
  --user-config=/mnt/log_dir/generated-user-config-0.yaml
  --network-config=/mnt/log_dir/generated-network-config.yaml
  --log-dir=/mnt/log_dir
  --eval-config=/mnt/log_dir/eval-config.yaml
```

### 3.1 启动 → 请求 → 调度 → worker

入口 `src/runtime/alpasim_runtime/simulate/__main__.py`：

1. `build_simulation_request`（`:146`）：把 user-config 里每个场景转成一个
   `RolloutSpec(scenario_id, nr_rollouts, session_uuids, start_time_offset_us, random_seed)`；
2. `run_simulation`（`:200`）：解析配置、确定 `rollouts_dir`、校验 worker 数，调用
   `_run_one_shot_request`；结束后若 eval 启用，调用
   `run_aggregation_from_runtime(...)` 产出 aggregate；
3. `_run_one_shot_request`（`:290`）：`DaemonEngine.startup() → simulate(request) → shutdown()`。
   （`run_mode=SERVER` 时改为常驻 `RuntimeDaemonApp` gRPC 服务，本项目使用 ONESHOT。）

**DaemonEngine**（`daemon/engine.py`）：

- `startup`（`:273`）：构建 `RuntimeContext`（各端点地址池、最大在途数）、启动 worker
  运行时、构建 `DaemonScheduler`；
- `simulate`（`:402`）：`build_pending_jobs_from_request`（`:163`）把每个 RolloutSpec
  展开为 `nr_rollouts` 个 `PendingRolloutJob`（种子 = `random_seed + rollout 序号`），
  然后 `scheduler.submit_request → wait_request`，最后 `build_simulation_return`
  （`:68`）汇总成 gRPC 返回。

**DaemonScheduler**（`daemon/scheduler.py`，~900 行）两种派发策略：

- `FifoDispatch`（`:169`）：先进先出；
- `SceneAffineDispatch`（`:214`）：对 renderer 场景缓存敏感、限制同时活动的场景数
  （与 video_model 不兼容，故本项目不用）。

核心派发 `_dispatch_once_locked`（`:773`）：

1. `try_reserve` 一个 renderer 槽位；
2. `try_acquire_all` **同时**获取其余各端点池的一个槽位（全有或全无，失败回滚）；
3. 构建 `AssignedRolloutJob(endpoints=ServiceEndpoints(driver, renderer, physics,
   trafficsim, controller))`，提交给 worker；
4. `on_result`（`:826`）释放所有槽位；失败的 job 按 `max_rollout_retries=2` 重试
   （`INVALID_SCENE` 不重试）。

### 3.2 地址池：并发能力 = 五个端点容量的最小值

`AddressPool`（`address_pool.py:33`）：每个物理地址贡献 `n_concurrent_rollouts` 个槽位
token；`skip: true` 的池不限制（返回合成槽位）。池在启动时由 `create_address_pools`
（`runtime_context.py:36`）从 network-config 的端点地址构建；
`compute_max_in_flight`（`runtime_context.py:61`）取五个端点总容量的**最小值**。

> 这就是批量必须强制串行的落点：OmniDreams 只保留一个 session，所以
> `runtime.endpoints.renderer.n_concurrent_rollouts=1`；再把
> `runtime.nr_workers=1` 及 driver/physics/controller/trafficsim 的并发全部置 1，
> max_in_flight=1，30 个 clip 严格顺序执行。串行限制是槽位计数保证的，不是代码硬编码。

### 3.3 Worker：槽位 → gRPC 客户端 → 一次 rollout

`worker/main.py:70` 的 `run_single_rollout`：用 job 分配到的地址构建五个轻量客户端
（`DriverService`/`PhysicsService`/`TrafficService`/`ControllerService`，renderer 按
`renderer.kind` 选 `VideoModelService` 或 `SensorsimService`），然后执行
`create_event_rollout(...).run()`。`nr_workers=1` 时 worker 作为进程内 asyncio 任务运行
（`worker/runtime.py:151`）；每个 consumer 同时只处理一个 rollout。评估打分通过
`ProcessPoolExecutor`（spawn）在独立子进程执行，不阻塞事件循环。

### 3.4 事件堆：没有"for 每帧调用一切"的过程式代码

一个 rollout 的执行顺序由**按 `(时间戳, 优先级)` 排序的事件堆**涌现。主循环
（`event_loop.py:622`）：

```python
while event_queue:
    event = event_queue.pop()
    await event.handle(state, event_queue)
await state.step_context.drain_outstanding_tasks()
eval_result = await self._runtime_evaluator.run_evaluation(...)
```

事件优先级（`events/base.py:21`）：

| 优先级 | 事件 | 职责 |
|---|---|---|
| 10 / 11 | Camera（sensorsim 渲染/刷新） | 请求 RGB、把图像异步提交给 driver |
| 20 | **PolicyEvent** | 推送自车状态/路由，调用 `driver.drive()` |
| 30 | SimulationEndEvent | 抛出结束异常 |
| 40 | **ControllerEvent** | 轨迹 → MPC → 车辆传播状态 |
| 50 | **PhysicsEvent (EGO)** | 自车地面贴合 |
| 60 | **TrafficEvent** | 调 trafficsim 推进其他 agents |
| 70 | **PhysicsEvent (TRAFFIC)** | 其他 agents 地面贴合、轨迹累积 |
| 80 | **StepEvent** | 提交轨迹、广播日志、重置步上下文 |
| 90 | VideoModelPrefetchEvent | （video_model 专用）一个 chunk 的渲染，排在 StepEvent 之后 |

`RecurringEvent.handle`（`base.py:155`）执行后把时间戳推进一个 interval，超过终点不再
重新调度。

---

## 4. 一次闭环仿真的完整时间线

### 4.1 时序常量（chunking=8frame，本项目实际值）

来源 `src/wizard/configs/chunking/8frame.yaml` 与 renderer pipeline：

| 量 | 值 | 含义 |
|---|---|---|
| 视频帧率 | 30 FPS | `frame_interval_us = 1_000_000//30 = 33_333 µs` |
| `first_chunk_frames` | **5** | VAE 时间压缩 4:1：首块 = `(8//4−1)*4+1 = 5` |
| `chunk_frames` | **8** | 常规块 8 帧，对应一次扩散生成 |
| `control_timestep_us` | **266_664** | = 8 × 33_333；**driver 每 8 帧被查询一次** |
| `force_gt_duration_us` | **2_033_313** | 开头的"录制真值接管"预热时长（约 7.6 个控制步） |
| rollout 长度 | 约 75 个控制步 / ~20s | `n_sim_steps` 按录制长度裁剪 |

时序锚点在 `unbound_rollout.py:67` `_build_rollout_timing` 中计算：渲染起点 = 第一个相机
帧的快门关闭时刻；`first_policy_timestamp` 由 renderer 客户端的
`required_policy_start_timestmap_us`（`services/video_model_service.py:185`）给出
（= 渲染起点 + 5 帧 ×33_333）；`closed_loop_start = 渲染起点 + force_gt_duration`。

### 4.2 会话启动顺序（`EventBasedRollout.run`，`event_loop.py:500`）

每个服务客户端都提供 async 上下文管理器 `rollout_session`（`services/service_base.py:91`，
内部就是 `_initialize_session`/`_cleanup_session`）。启动严格按此顺序：

1. 广播元数据、构建 runtime 相机目录；
2. **renderer**：`WorldModelService.start_session`（§6.1）；
3. **physics + controller**：各自 `start_session`；
4. **driver**：`EgodriverService.start_session`；
5. **trafficsim**：`rollout_session` 仍会进入上下文（`event_loop.py:580`），其中
   `handover_time_us = start + force_gt_duration + control_timestep`——保证预热期交通
   完全确定；**skip 模式下 `start_session` RPC 是 no-op**（`traffic_service.py:108`），
   仅在客户端本地登记场景的录制 actors；
6. 构建 force-GT 物理混合；随后 `_create_initial_events`（`:407`）把首批事件入堆。

### 4.3 Force-GT 预热阶段（前 2.03s）

`force_gt` 标志为真时：

- **不调用 driver**：PolicyEvent 直接把"录制的参考轨迹"当作 driver 输出
  （`events/policy.py:153`）；
- ControllerEvent 以录制轨迹为参考、`coerce_dynamic_state=true`，用录制的动力学状态
  强制对齐，防止起步漂移（`events/controller.py:97`）；
- trafficsim 处于 handover 之前的**纯日志回放**。

意义：renderer 的 KV-cache、各服务状态都从真实录制平滑起步，闭环控制权在
`closed_loop_start_us` 才交给 driver。

### 4.4 一个闭环控制步内发生什么（时间戳 t → t+266_664µs）

```
PolicyEvent (prio20)
  ├─ 把新增的自车估计位姿/速度批量提交 driver: submit_egomotion_observation
  ├─ 提交 route（导航）与录制 GT（基准，benchmark 模式可不下发以防泄漏）
  ├─ await drain_outstanding_tasks()   ← 屏障：确保上一 chunk 的图像观测已送达
  ├─ driver.drive(time_now=t, time_query=t+266664)
  │     → DriveResponse.trajectory（local 系，规划轨迹）
  └─ 把轨迹从"含噪估计 local 系"变换到"真实 local 系"，写入 StepContext
        ControllerEvent (prio40)
  ├─ planner_delay_buffer（planner_delay=0 时直通）
  ├─ VDC.run_controller_and_vehicle(state, planned_trajectory, future=t+dt)
  │     → MPC 每 0.1s 解一次，自行车模型逐步推进
  │     → repeated PropagatedState（真实/估计位姿 + 动力学状态）
  └─ 结果写入 StepContext（ego_true / ego_estimated）
        PhysicsEvent EGO (prio50)
  └─ Physics.ground_intersection(ego 轨迹) → 仅修正 z 与姿态（x,y 强制保持）
        TrafficEvent (prio60)
  ├─ trafficsim 启用：Traffic.simulate(ego AABB 位姿, t+dt)
  │     → 每个 agent 在 t+dt 的位姿（学习式预测可带 1.5s 预测）
  └─ 【本部署 skip 模式】不发 gRPC；客户端对录制 actors 本地插值到 t+dt
        （traffic_service.py:142），场景中没有记录的 actor 则画面为空
        PhysicsEvent TRAFFIC (prio70)
  └─ 对各 agent 未来位姿做地面贴合；累积到 traffic_trajectories
        StepEvent (prio80)
  ├─ 把新位姿提交进 state.ego_trajectory / 各 agent 轨迹
  ├─ 广播 ActorPoses 日志（写 rollout.asl + 喂评估器）
  └─ 重置 StepContext
        VideoModelPrefetchEvent (prio90)   ← 必须在 StepEvent 之后
  ├─ 用刚提交的自车轨迹构造 chunk（首块 5 帧，其后每块 8 帧）
  ├─ 从 traffic objects 构造 DynamicWorldState
  ├─ WorldModelService.render_video_chunk(rig_trajectory, dynamic_state)
  │     → 1280×704 JPEG q90（首块 5 帧 / 常规 8 帧）
  └─ 每帧产生一个 VideoModelFrameEvent，各自 driver.submit_image_observation
        ── 下一个控制步，PolicyEvent 的屏障等待这些图像全部送达 ──
```

即 **`drive()` 每个控制步（8 个视频帧）调用一次**；8 帧图像仍然逐帧作为观测提交给
driver。"8"是渲染块大小，不是 driver 的动作块——driver 输出规划后，runtime 只推进一个
控制步就重新查询。

---

## 5. 接口形式：9 个 gRPC 契约总览

全部为 proto3、未加密 HTTP/2（`grpc.insecure_channel`），单请求均为 unary（无流式 RPC）。
图像一律以**编码后的字节**传输（JPEG），不传原始张量。

### 5.1 服务与 RPC 一览

| 服务（proto） | RPC | 传输内容摘要 |
|---|---|---|
| `RuntimeService`（runtime.proto） | `simulate` | 仿真请求（RolloutSpec 列表 + driver 地址）→ 逐时间步指标 + 聚合指标。仅 SERVER 模式对外服务；ONESHOT 时进程内直接构造 engine |
| | `prefetch_scene` / `get_runtime_info` / `shut_down` | 场景预取 / 版本与容量信息 / 关闭 |
| `WorldModelService`（video_model.proto） | `start_session` | HDMap parquet zip + 相机规格 + 每视图初始帧 + rig→camera 外参 + seed → session_id |
| | `render_video_chunk` | ego rig 轨迹（首块 5 位姿/常规 8 位姿）+ 动态世界状态 → 每相机 JPEG 帧 |
| | `close_session` / `get_version` | — |
| `EgodriverService`（egodriver.proto） | `start_session` / `close_session` | 会话（可用相机清单）/ 关闭 |
| | `submit_image_observation` | 单张相机 JPEG + 起止时间戳 + logical_id |
| | `submit_egomotion_observation` | 估计 ego 轨迹 + 1:1 对应的动力学状态（速度/加速度） |
| | `submit_route` / `submit_recording_ground_truth` | 导航路点（rig 系）/ 录制真值轨迹 |
| | `drive` | 当前/查询时间戳 + 可选 renderer 数据 → 规划轨迹（+ 采样候选 + 调试信息） |
| `VDCService`（controller.proto） | `start_session` / `close_session` | rig 文件 / 关闭 |
| | `run_controller_and_vehicle` | 当前状态 + rig 系规划轨迹 + 未来时刻 → 传播状态序列（位姿 + 动力学，真实/估计各一） |
| `PhysicsService`（physics.proto） | `ground_intersection` | 场景 id + ego/其他物体 AABB 与位姿对 → 地面修正后的位姿 + 每姿态状态码 |
| | `get_version` / `get_available_scenes` | — |
| `TrafficService`（traffic.proto） | `start_session` / `close_session` | 全量 logged 轨迹 + handover 时刻 / 关闭 |
| | `simulate` | ego 更新轨迹 + 查询时刻 → roster 中各物体（含预测范围的）轨迹更新 |
| | `get_metadata` / `get_available_scenes` | 最小历史长度等元数据 |
| `SensorsimService`（sensorsim.proto） | 一组 `render_rgb`/`batch_render_rgb`/`render_aggregated`/`render_lidar` 等 | NRE 传感器仿真接口。本项目 renderer 走 video_model，**不使用此服务**；其中 `CameraSpec` 消息被 video_model 复用 |

### 5.2 公共消息（common.proto）

```proto
message Vec3  { float x; float y; float z; }
message Quat  { float w; float x; float y; float z; }
message Pose  { Vec3 vec = 1; Quat quat = 2; }   // 先平移后旋转
message DynamicState { Vec3 angular_velocity; Vec3 linear_velocity;
                       Vec3 linear_acceleration; Vec3 angular_acceleration; }
message AABB  { float size_x; float size_y; float size_z; }
message PoseAtTime { Pose pose; fixed64 timestamp_us; }
message StateAtTime { fixed64 timestamp_us; Pose pose; DynamicState state; }
message Trajectory { repeated PoseAtTime poses; }
```

### 5.3 渲染块契约（video_model.proto，关键字段）

```proto
message SessionRequest {
  StaticWorldMap static_world_map = 1;              // HDMap parquet 的 zip 字节
  TextPrompt text_prompt = 2;
  DebugOptions debug_options = 3;                   // 可只出 HDMap 条件帧
  repeated sensorsim.CameraSpec camera_specs = 4;   // 每视图一个（仅支持 FTheta 模型）
  repeated Image initial_frames = 5;                // 每视图一个条件起始帧
  repeated common.Pose rig_to_camera = 6;
  fixed64 random_seed = 7;
}
message VideoChunkRequest {
  SessionId session_id = 1;
  common.Trajectory rig_trajectory = 2;             // ego rig 位姿（首块5/常规8）
  DynamicWorldState dynamic_state = 3;              // 动态 actors，空 = 画面中无 actor
}
message DynamicActor { ActorClassId class_id; common.AABB bbox_dims;
                       common.Trajectory trajectory; }   // CAR/TRUCK/PEDESTRIAN/CYCLIST/OTHER
message VideoChunkReturn { repeated CameraOutput camera_outputs; }
message CameraOutput { string camera_logical_id; repeated Image rgb_frames;
                       repeated Image hdmap_condition_frames; }
message Image { bytes data; ImageFormat format; }   // JPEG / PNG / ...
```

### 5.4 坐标系约定（理解所有接口字段的前提）

接口注释中反复出现的 "active transform `local→rig` / `local→aabb`"：

- **local**：世界/场景局部坐标系；**rig**：自车 rig 原点（FLU：+x 前、+y 左、+z 上）；
  **aabb**：物体包围盒中心。
- 位姿消息表达的是**主动变换**：如 `local→rig` 把 rig 系坐标变换到 local 系，其平移分量
  就是 rig 原点在 local 系的位置。
- driver 输出与 route 都在 **rig 系**（相对当前 rig 原点）；physics/trafficsim 的物体轨迹
  用 `local→aabb`（盒中心）。
- driver 接收的是 **ego_est（含噪估计）** 观测：当启用误差模型时，估计位姿可以偏离真实
  位姿；PolicyEvent 在拿到 driver 结果后用
  `transform_trajectory_from_noisy_to_true_local_frame`（`events/policy.py:208`）把规划
  还原到真实 local 系再交给 controller。

---

## 6. OmniDreams 渲染端内部实现

### 6.1 服务端结构与单 session 铁律

入口模块 `omnidreams.impl.grpc.server`：

- `WorldModelService(video_model_pb2_grpc.WorldModelServiceServicer)`（`server.py:747`）：
  gRPC 处理器；`WorldModelEngine`（`server.py:254`）：拥有模型 pipeline、负责跨 rank 编排。
  注册于 `server.py:1446`。
- RPC：`start_session`（`:854`）、`render_video_chunk`（`:1011`，额外持
  `_finalization_lock`）、`close_session`（`:1149`，同锁）、`get_version`（`:820`）。
- **单 session**：start_session 时若存在旧会话，服务层（`server.py:879`）与 engine 层
  （`:362`）都会先记录并清理：
  `"Cleaning up N existing session(s) before starting new one"`。这就是并发 clip 报
  `Session not found` 的根因。`render_video_chunk` 还会先等待上一块的 KV-cache 后台更新
  完成（`_finalization_overlap.wait()`，`:1038`）。
- 就绪标志在 `server.py:1460`：`"Server started successfully. Press Ctrl+C to stop."`

### 6.2 start_session 吃进什么

1. **HDMap zip**：解 zip（带 zip-slip 防护）到临时目录，经 ClipGT loader 构建 `SceneData`
   并构建 GPU 上的 Ludus 场景（`grpc/utils.py:113` `load_static_world_from_zip_bytes`，
   `include_dynamic_obstacles=False`——动态障碍物以每步 gRPC 消息为准）。
2. **camera_specs**：仅接受 **FTheta** 相机模型；OpenCV pinhole/fisheye 会显式报错
   （`utils.py:538`）。`logical_id` 缺省时生成 `camera_i`。
3. **initial_frames**：数量必须与相机数一致；解码后 LANCZOS 缩放至目标分辨率，堆叠为
   `[1, V, 3, H, W]`。
4. **rig_to_camera**：每相机一个，转 4×4 矩阵（空 pose = 单位阵）。
5. text_prompt（缺省用内置驾驶场景正向 prompt）、debug_options、random_seed
   （0 = server 自选）。session_id 由 server 生成 uuid4；**实际生成被延迟到首个
   render_video_chunk**。

这些字段由 alpasim runtime 客户端从 USDZ 提取（`services/video_model_service.py:403`
`_initialize_session`）：HDMap 来自 USDZ 内全部 `clipgt/*` 条目重新打包
（`video_model/utils.py:344`）；初始帧取 `frames/<相机>/` 下最早时间戳的 JPEG
（校验 SOI `ff d8 ff`）；相机内/外参来自 `clipgt/calibration_estimate.parquet`
（`video_model/usdz_calibration.py:54`）。约 40% 的 26.01 USDZ 缺该文件，起步即失败。

### 6.3 模型构成（pipeline slug = `omnidreams`）

`WorldModelEngine.__init__`（`server.py:254`）→ `OmnidreamsConditioningWrapper`
（`conditioning_wrapper.py:93`）；配置为 `OMNIDREAMS_PIPELINE_CONFIG`
（`integrations_v2/omnidreams/config.py:46`）：

| 组件 | 配置 | 权重 |
|---|---|---|
| 文本编码器 | Cosmos-Reason1 Text Encoder | — |
| 条件/图像编码器 | Wan **LightVAE**（cuda graph） | `AVAILABLE_WAN_VAE_CHECKPOINT_PATHS["lightvae"]` |
| 解码器 | **LightTAE**（Taehv），时间压缩比 4 | `AVAILABLE_TAEHV_CHECKPOINT_PATHS["lighttae"]` |
| 扩散主干 | Cosmos DiT，`num_views=1`、`len_t=2`、`window_size_t=6`、`guidance_scale=1.0`、蒸馏 2 步 | `single_view/2b_res720p_30fps_i2v_hdmap_distilled.pt`（~2B 参数 I2V、HDMap 条件） |

关键尺寸（`conditioning_wrapper.py:151`）：`frame_chunk_size = len_t*4 = 8`；
`initial_frame_chunk_size = 1+(len_t−1)*4 = 5`。全部模型常驻 GPU1。

### 6.4 一个 8 帧块的生成过程

`OmnidreamsPipeline.generate`（`impl/pipeline.py:477`），每块：

1. **渲染条件帧**：对块内全部相机位姿，用 Ludus 光栅器一次性批量渲染 HDMap + 线框 actor
   盒子（`conditioning/renderer.py:167`），得 `[V, T, 3, H, W]` uint8；
2. **编码**：Wan LightVAE（`pipeline.py:478` `generate`）把 5/8 帧条件图编码成 **2 个时间
   潜帧**（起始 seed 图只在缓存初始化时编码一次）；
3. **去噪**：FlowMatch 调度器仅 **2 步**（`denoising_timesteps=[1000,500]`、shift 5.0、
   warp、`extra_one_step`），即每个块 DiT 只前向 2 次：
   `v=predict_flow(x_t,t); x0=x_t−σv; x_t=(1−σ)x0+σeps`；
4. **解码**：LightTAE 把 2 个潜帧解码为首块 **5 像素帧** / 常规块 **8 像素帧**；
5. **KV-cache 收尾**：响应发出后，在宿主机后台线程再跑一次 DiT（`finalize_kv_cache`）
   把缓存滚动到下一自回归块（`model/base.py:240`），与网络传输重叠。

**输出**：仅 RGB。分辨率 1280×704（704p；`RESOLUTION_MAP`，`server.py:100`），GPU 端
nvJPEG 编码（`ludus_renderer/nvjpeg.py:106`），质量 90。**没有深度/语义/分割等辅助输出**
（`hdmap_condition_frames` 仅在 debug 选项下返回）。gRPC 消息上限 server 100MB / client
64MB。

### 6.5 条件如何进入模型

- **其他 agents → 像素**：`DynamicActor` 列表经 `dynamic_state_to_ludus_cube_pool`
  （`grpc/utils.py:331`）：按每帧时间戳对 actor 位姿采样（平移线性插值、四元数 Slerp，
  超出时间范围跳过），构建带类别颜色的 **3D 线框 CubePool**——小汽车蓝色梯度、卡车橙色、
  行人洋红等（`ludus_renderer/clipgt.py:111`）。盒子光栅进条件帧，即以**像素**而非独立
  box 张量进入扩散模型。
- **静态世界**：条件帧还含车道线（虚实/白黄）、道路边界、人行横道、停止线、杆件、路面
  标记、信号灯/标志、自车轨迹折线等，全部来自 HDMap zip。
- **动作条件：不存在**。proto 中没有转向/油门字段，ego 运动完全由 `rig_trajectory` 的位姿
  表达；也无引擎盖 overlay。DiT 以 `additional_concat_ch=16` 把 16 通道 HDMap 条件潜变量
  拼接到输入。
- **多相机**：栈本身支持任意视图，但注册的 `omnidreams` pipeline 为 `num_views=1`（单前
  宽视角）。相机名在 USDZ 中为 `camera:front:wide:120fov`，规范为
  `camera_front_wide_120fov`。

### 6.6 JIT 插件与 CUDA 13

`ludus_renderer/_ops/_plugin.py:63` `_get_plugin()` 在首次调用时经
`torch.utils.cpp_extension.load(...)` 即时编译 `ludus_renderer_plugin`：10 个 C++/CUDA
源文件（FTheta CudaRaster 框架、Ludus 渲染 op、torch 绑定），`-lcuda`，arch 自动检测。
插件提供 FTheta 光栅器与 nvJPEG 绑定。torch 为 cu130，故必须用 CUDA 13 nvcc——本部署由
`/mnt/cuda13`（pip cu13 wheel 拼装，`scripts/env.sh` 导出 `CUDA_HOME`）提供。

---

## 7. Driver / Controller / Physics / Trafficsim 内部实现

### 7.1 Driver 服务（`src/driver/src/alpasim_driver/`）

- 服务类 `EgoDriverService`（`main.py:456`）。**权重在服务启动时加载一次**（不是每会话）：
  `_create_model`（`:481`）经 entry-point `alpasim.models` 取模型类，`from_config` 构建；
  R1 适配器 `models/alpamayo1_model.py:67`（`AlpamayoR1.from_pretrained`），A15 适配器
  `models/alpamayo1_5_model.py:86`（sdpa 注意力）。单个后台工作线程（`:518`）串行/批量
  处理推理任务。`start_session` 只登记帧缓存与相机规格。
- **`drive`（`main.py:918`）的输入**（`PredictionInput`，`models/base.py:45`）：
  每相机 `context_length` 帧历史图像（不做归一化，shape `[相机数, 帧数,3,H,W]`）、
  导航命令、车速（线速度模）、纵向加速度、16 步 ego 位姿历史（0.1s 等间隔重建）、
  上一次规划、路由。
- **模型推理**（`models/alpamayo_base.py:552`）：
  `sample_trajectories_from_data_with_vlm_rollout(...)`——VLM 按 chat template 生成
  因果链文本（R1 无相机编号/导航文本；A15 带相机索引、帧号与导航文本，可做 CFG），
  expert 在 (加速度, 曲率) 动作空间做 flow-matching，积分还原轨迹。
- **输出**：`num_trajectory_samples=1`（`ALWAYS_FIRST` 策略，候选间按与上一规划的时序
  一致性选择）时单条轨迹，**64 个路点 @10Hz（6.4s）**，rig 系、米制，时间戳从 t0 后
  100ms 起。转 proto 时首点位姿为 t0 的 rig 位姿、其后为 local 系路点
  （`main.py:1011`）；采样候选与推理文本放入 `debug_info`。
- 本项目 preset（`alpamayo15_1cam_local.yaml`）：仅 `camera_front_wide_120fov`、
  `subsample_factor=3`（图像历史抽稀）、检查点 `/mnt/weights/alpamayo-1.5`（只读挂载），
  extras 注入 `HF_HUB_OFFLINE=1`/`TRANSFORMERS_OFFLINE=1` 解决 gated 401。
- 容器内启动：`uv run -m alpasim_driver.main --config-path=/mnt/output
  --config-name=driver-config.yaml host=0.0.0.0 port=<port>`。

### 7.2 Controller 服务（`src/controller/alpasim_controller/`）

- 服务类 `VDCSimService`（`server.py:36`），`run_controller_and_vehicle`（`:72`）在锁内
  委托 `SystemManager`→`System`（`system_manager.py:15`）。
- **内部控制量 = `[前轮转向角(rad), 目标纵向加速度(m/s²)]`**，无油门/刹车踏板接口。
- **控制律**（`mpc_controller.py:87`，时域 20、dt 0.1）：
  - 线性 MPC：`mpc_impl/linear_mpc.py`，OSQP 解 QP，自行车动力学线性化；
  - 非线性 MPC（**wizard 默认**，`controller/default.yaml`）：CasADi + do_mpc，IPOPT。
- **车辆动力学**（`vehicle_model.py`，状态 `[x,y,yaw,vx,vy,yaw_rate,steer,accel]`）：
  5 m/s 以下运动学自行车、以上线性轮胎动力学自行车；转向/加速度执行器一阶滞后 τ=0.1s；
  RK2 + ≤0.01s 子步积分；默认参数为 Ford Fusion（mass 2014kg，轴距 2.85m）。
- `System.run_controller_and_vehicle_model`（`system.py:96`）：在每个 MPC 边界把参考轨迹
  变换到当前 rig 系、解 MPC、逐步推进车辆；一次 RPC 只传播到 `future_time_us`，可按
  `pose_reporting_interval_us` 回传中间状态，响应含真实与估计两套状态。

### 7.3 Physics 服务（`src/physics/alpasim_physics/`）

- **只做一件事**：`ground_intersection` 防止 AABB 嵌入地面。**没有动力学步进、没有碰撞
  RPC、没有固定 dt**——车辆前向演化在 controller，碰撞在评估器事后计算。
- 算法（`backend.py:132` `update_pose`）：在 AABB 底面按网格取 16 点，用 Warp kernel 对
  地面三角形 mesh（场景 `.ply`）打**双向垂直射线**，对命中点 SVD 拟合平面得到 z 平移与
  姿态修正；修正量超过阈值（1.5m / 10°）则拒绝并返回状态码
  （`INSUFFICIENT_POINTS_FITPLANE`/`HIGH_TRANSLATION`/`HIGH_ROTATION`）。
- 修正后 **x,y 平移强制还原为预测值**（`backend.py:259`）——只影响 z 与姿态，杜绝横向
  漂移。容器启动：`physics_server --artifact-glob=/mnt/nre-data/<sceneset>/**/*.usdz
  --use-ground-mesh`。

### 7.4 Trafficsim 服务（`src/trafficsim/alpasim_trafficsim/`）

> **部署状态：本项目未启用该服务**（默认 `trafficsim=disabled` → `skip=true`，
> `/mnt/trafficsim-models/` 不存在，CATK 权重未下载）。下文描述的是代码中可用的完整
> 实现；skip 模式下 runtime 客户端在本地回放录制轨迹（`services/traffic_service.py:142`），
> 不与该服务通信。

- 服务类 `TrafficServiceServicer`（`grpc/servicer.py:82`）。**混合模型**：
  `start_session` 吃进全量 logged 轨迹与 `handover_time_us`；查询时刻 ≤ handover 时
  **原样回放录制位姿**（`_simulate_logged_replay`，`:274`），其后交给学习模型
  （`_apply_model_predictions`，`:236`）。
- 学习模型：`CATK`（`catk/model_adapter.py`）加载 **SMART** 网络（`catk/smart/`）——
  token 化的 agent 轨迹码本（shift=5：2Hz token ↔ 10Hz 细步），地图编码器 + 时空/交互
  注意力 agent 解码器，自回归预测后 token 轮廓角点均值上采样为 10Hz 位姿。推理在进程内
  torch + 批处理线程；ego 永远由外部轨迹条件化、不参与预测；静态 agent 默认冻结。
- 部署参数（`trafficsim/catk.yaml`）：细步 dt=0.1s、历史 16 步、预测 15 步（1.5s）；
  权重 `/mnt/trafficsim-models/catk_v120/{config.yaml,latest.ckpt}` + token 码本；
  端口 6200；查不到结果时返回 FAILED_PRECONDITION，不伪造静态兜底。

### 7.5 碰撞到底在哪算？——评估器，事后、多边形

**运行期没有任何碰撞响应**。碰撞在 rollout 结束后由 eval 打分器从完整日志计算：

- `src/eval/src/eval/scorers/collision.py:8`：每时间步把各 agent footprint 建成 Shapely
  多边形与 `STRtree`，查询与 ego 多边形相交者，并按接触的是 ego **前保险杠线 / 后保险杠
  线 / 其他**分类：
  ```python
  if other_polygon.intersects(ego_front_bumper): front = True
  elif other_polygon.intersects(ego_rear_bumper): rear = True
  else: lateral = True
  ```
  产出 `collision_front/rear/lateral/any`，时间维 MAX。
- `scorers/open_loop_collision.py`：把 ego 盒沿规划的前 3s 扫掠、与同时刻 agents 的
  **录制**盒子相交 → `open_loop_collision`（多少次推理本身就规划进障碍）。
- 多边形几何（footprint、保险杠线）在 `src/eval/src/eval/data.py:974` 从插值轨迹构建。

---

## 8. 指标与产物

### 8.1 落盘布局

`<log_dir>`（实体 `/mnt/artifacts/run_<tag>`，仓库内 `artifacts/run_<tag>` 为符号链接）：

```
docker-compose.yaml
generated-user-config-0.yaml / generated-network-config.yaml
driver-config.yaml / controller-config.yaml / trafficsim-config.yaml / eval-config.yaml
rollouts/
  <scene_id>/<rollout_uuid>/
     metrics.parquet                 # 逐帧指标 long format（评估子进程一次性写出）
     <clip>_<rollout>_<camera>_<layout>.mp4
     rollout.asl                    # 完整 rollout 记录（~数百 MB）
     _complete                      # 零字节完成标记（断点续跑依据）
aggregate/
     metrics_results.txt / .parquet / results_summary.json
txt-logs/、controller/、prometheus/
```

### 8.2 metrics.parquet 的产生与字段

- rollout 运行中，每个 LogEntry（各微服务请求/响应、ActorPoses）由 `MessageBroadcaster`
  同时扇给 ASL writer 与 `RuntimeEvaluator`（`event_loop.py:187`）；
- 打分只在结束时、在 spawn 子进程里做（`runtime_evaluator.py:35`）：
  `ScenarioEvaluator.evaluate`（`scenario_evaluator.py:78`）调用各打分器
  `calculate → aggregate`，再 `create_metrics_dataframe`，一次性 `write_parquet`。
- DataFrame 为 **long format**，列：`name, timestamps_us, values(float64), valid,
  time_aggregation, clipgt_id, rollout_id, run_uuid, run_name`。读取时 `values` 需
  `pd.to_numeric(errors="coerce")`，同 metric/时间戳多相机行取 max。
- 指标名（`scorers/`）：`collision_front/rear/lateral/any`、`offroad`、`wrong_lane`、
  `min_distance_to_lane_boundary_m`、`min_distance_to_obstacle_m`、`open_loop_collision`、
  `progress`、`progress_rel(_to_total)`、`dist_to_gt_trajectory`、`dist_to_gt_location`、
  `dist_traveled_m`、`gt_dist_traveled_m`、`min_ade@<0.5/1/2.5/5>s`、`plan_deviation`、
  `img_is_black`、`safety_monitor_triggered`、`eval_relevant`。

### 8.3 Aggregate 与已知的系统性偏差

`run_aggregation_from_runtime`（`src/eval/src/eval/aggregation/main.py:174`）收集全部
metrics.parquet，应用配置的截断算子（`eval.aggregation_modifiers`，
`max_dist_to_gt_trajectory=4.0`；以及 `RemoveTimestepsAfterEvent(offroad_or_collision)`），
再写出 `metrics_results.txt` 富文本表。

**这些截断算子丢弃事件之后的所有帧**：碰撞/offroad/偏离越早的 clip，被截越多，aggregate
数字反而越"好看"（批量实测 aggregate dist_to_gt=3.02m vs 逐帧真值 20.06m）。
**验收只能依据逐 clip 的 `metrics.parquet` + 原始视频**，永远不要只信 aggregate。

---

## 9. 本部署拓扑速查

| 项 | 实际值 |
|---|---|
| 硬件 | 2× A100-80GB；GPU0 = driver 侧全部容器，GPU1 = OmniDreams 宿主机进程 |
| Renderer | `scripts/start_renderer.sh`：`uv run --package flashdreams-omnidreams torchrun -m omnidreams.impl.grpc.server --resolution 704p --output_format jpeg --jpeg_quality 90`，:50051 |
| 托管容器（共 4 个） | `driver-0`、`physics-0`、`controller-0`、`runtime-0`；wizard 生成 compose，alpasim-base 镜像，`network_mode: host` |
| Trafficsim | **未启用**：`trafficsim=disabled` → `skip=true`，无容器；其他 actors 本地录制回放 |
| 单次运行 | `scripts/run_closed_loop_r1.sh` / `run_closed_loop_a15.sh`（单 clip `clipgt-02eadd92…`） |
| 批量运行 | `scripts/run_batch_a15.sh`：30 clip 清单、自动拉起 renderer、`nr_workers=1` + 四端点并发全 1（trafficsim skip）、~3.45 min/clip |
| 场景实体 | `/mnt/alpasim-data`，经 `repos/alpasim/data/nre-artifacts` 引用 |
| 权重 | R1 `/mnt/weights/alpamayo-r1`；A15 `/mnt/weights/alpamayo-1.5`；OmniDreams 模型在 HF 缓存；CATK 未下载 |
| 产物 | `/mnt/artifacts/run_<tag>/` |
| 分析 | `repos/alpasim/.venv/bin/python scripts/analyze_batch_a15.py <log_dir> --manifest <csv>` |

---

## 10. 关键文件索引

**Wizard / Runtime**

- `src/wizard/alpasim_wizard/{__main__.py,wizard.py,context.py,configuration.py,services.py,setup_omegaconf.py,schema.py}`
- `src/wizard/alpasim_wizard/deployment/{docker_compose.py,slurm.py}`
- `src/wizard/alpasim_wizard/scenes/sceneset.py`
- `src/runtime/alpasim_runtime/simulate/__main__.py`
- `src/runtime/alpasim_runtime/{event_loop.py,unbound_rollout.py,endpoints.py}`
- `src/runtime/alpasim_runtime/daemon/{engine.py,scheduler.py,servicer.py,app.py}`
- `src/runtime/alpasim_runtime/worker/{main.py,runtime.py}`
- `src/runtime/alpasim_runtime/events/{base.py,policy.py,controller.py,physics.py,traffic.py,step.py}`
- `src/runtime/alpasim_runtime/events/video_model/prefetch.py`
- `src/runtime/alpasim_runtime/services/{service_base.py,video_model_service.py,driver_service.py,controller_service.py,physics_service.py,traffic_service.py}`

**OmniDreams**

- `integrations_v2/omnidreams/impl/grpc/{server.py,utils.py}`
- `integrations_v2/omnidreams/impl/{pipeline.py}`
- `integrations_v2/omnidreams/impl/conditioning/{conditioning_wrapper.py,renderer.py}`
- `integrations_v2/omnidreams/config.py`
- `integrations_v2/omnidreams/impl/ludus-renderer/ludus_renderer/{clipgt.py,nvjpeg.py,_ops/_plugin.py}`

**Driver 模型 / Controller / Physics / Eval**

- `src/driver/src/alpasim_driver/{main.py}`、`models/{alpamayo_base.py,alpamayo1_model.py,alpamayo1_5_model.py,trajectory_selection.py}`
- `src/alpamayo_r1/{helper.py}`、`models/{alpamayo_r1.py,base_model.py}`、
  `action_space/unicycle_accel_curvature.py`、`diffusion/flow_matching.py`
- `src/controller/alpasim_controller/{server.py,system.py,vehicle_model.py,mpc_controller.py}`
- `src/physics/alpasim_physics/{server.py,backend.py}`
- `src/trafficsim/alpasim_trafficsim/grpc/{servicer.py,catk_predictor.py}`
- `src/eval/src/eval/{runtime_evaluator.py,scenario_evaluator.py,data.py}`
- `src/eval/src/eval/scorers/{collision.py,offroad.py,ground_truth.py,minADE.py,open_loop_collision.py}`
- `src/eval/src/eval/aggregation/{main.py,processing.py}`
- `src/grpc/alpasim_grpc/v0/*.proto`
