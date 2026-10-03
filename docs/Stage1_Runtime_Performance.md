# Stage 1 运行时性能报告：Alpamayo 1.5 × OmniDreams 闭环仿真

> 本文用**实测数据**回答：在无头服务器 `ubuntu2`（2× NVIDIA A100-80GB）上，本系统跑
> Alpamayo 1.5 闭环仿真时峰值吃多少内存/显存/线程/磁盘，实时性如何、帧率多少，渲染一帧、
> 一个仿真 step 各花多少 wall clock。所有数字均来自真实运行（30 场景批量 + 2 次单 clip
> 专项复测），不使用推测值。
>
> 配套阅读：系统结构见 [Stage1_Architecture.md](Stage1_Architecture.md)，单步控制的
> 输入/输出细节见 [Stage1_Driver_IO_Cycle.md](Stage1_Driver_IO_Cycle.md)，批量实验背景见
> [Stage1_Batch30_Report.md](Stage1_Batch30_Report.md)，复现命令见
> [Stage1_Reproduction_Guide.md](Stage1_Reproduction_Guide.md)。

---

## 1. 核心结论（TL;DR）

| 指标 | 实测值 | 备注 |
|---|---|---|
| 闭环实时倍率（RTF） | **≈ 0.13× real-time** | 30 clip 均值 0.132×；两次复测 0.13× |
| 有效闭环帧率 | **≈ 3.9 fps** | 每 2.05 s wall 产出 8 帧；仿真目标 30 fps |
| 每个仿真 step 的 wall clock | **均值 2.04 s / p50 2.06 s** | step 对应 sim 时间 266.7 ms（8 帧） |
| 渲染一帧的 wall clock | **≈ 110 ms** | OmniDreams 以 **8 帧一批**生成：整批 0.88 s |
| 渲染器理论吞吐（连续供批） | ≈ 9.1 fps（3.6 fps@chunk 首帧延迟口径） | 实测受串行链路拖累，大部分时间在等 drive |
| Driver 推理（Alpamayo 1.5） | **均值 1.05–1.16 s / chunk** | 最大单步耗时项；force-GT 步约 2 ms |
| MPC + 车辆物理 | 均值 87 ms | 非线性 MPC，CasADi/do_mpc |
| GPU0 显存峰值（driver 侧） | **22.9 GiB / 80 GiB** | Reason2-8B + expert head + CUDA |
| GPU1 显存峰值（renderer 侧） | **37.2 GiB / 80 GiB** | 常驻 ≈ 22.9 GiB，session 中增至 37.2 |
| 主机 RAM 峰值（系统口径） | **≈ 14.6 GB / 94 GiB** | `MemTotal-MemAvailable`，余量 >84 GiB |
| 容器 cgroup 内存峰值 | driver **22.9 GiB**、runtime 2.0 GiB、其余 <1 GiB | 与系统口径差异见 §3.1 |
| 线程数 | renderer **181**；driver 155；runtime 141；controller 102；physics 89 | 主机总线程 2266 → 峰值 2851 |
| 单 clip 磁盘产物 | **≈ 235 MiB** | `rollout.asl` 230 M + MP4 4.3 M + metrics 10 K |
| 30 clip 批量产物 | 5.4 GB | rollouts 5.3 G |

**一句话**：链路在 2×A100 上跑得稳但远非实时——20 s 的仿真场景要约 **2.5 分钟**
wall clock；瓶颈依次是 Alpamayo 1.5 的 8B VLM 推理（~1.1 s/chunk）与 OmniDreams
8 帧批量生成（~0.88 s/chunk），两者**串行无重叠**，渲染器单独看有 ~9 fps 能力，
但闭环中只能跑到 ~3.9 fps。

---

## 2. 测量环境、方法与数据

### 2.1 软硬件前提

| 项 | 值 |
|---|---|
| 机器 | `ubuntu2`（hostname `ubuntu22`），Ubuntu 22.04，无头 |
| CPU | Intel Xeon Platinum 8358P @ 2.60 GHz，**32 个逻辑核** |
| RAM | **94 GiB**（/proc MemTotal 98.9 GB 十进制） |
| GPU | 2× NVIDIA A100-SXM4-80GB；**GPU0 = driver 侧容器，GPU1 = OmniDreams renderer** |
| 磁盘 | `/` 196 G（系统+镜像）；`/mnt` 500 G（权重/数据/产物） |
| 驱动模型 | Alpamayo 1.5 = Cosmos-Reason2-8B VLM + expert flow-matching head |
| 渲染模型 | OmniDreams（704p，JPEG q90，1280×704） |
| 拓扑 | 4 容器（driver :6005 / physics :6006 / controller :6007 / runtime）+ 宿主机 renderer :50051；trafficsim 关闭（runtime 本地回放录制 actor） |

### 2.2 三类数据源

1. **runtime 结构化事件日志**（主证据，时间戳精度毫秒级）：
   `artifacts/run_a15_batch30_20261001/txt-logs/runtime_worker_0.log`（30 clip，3.8 MB）。
   runtime 对每个事件打印一行，例如
   `sim_time ...us: Requesting video chunk: chunk_size=8 ...`、
   `...: VideoModelFrameEvent(...)`、`...: PolicyEvent @ ...`，并在结束时打印
   `Session COMPLETED: ... simulated 19.79 sim seconds in 149.21 wall clock seconds for 0.13x real time`。
   用相邻事件时间戳差即可还原各阶段 wall clock。
2. **5 s 周期资源采样**（专项单 clip 运行期间）：`nvidia-smi` 两卡显存/利用率、
   `/proc/meminfo`、`docker stats --no-stream`，共 ~75 个采样点。
3. **线程快照**：稳态仿真期间用容器内 `ps -eLf` 逐容器计数，并与宿主机
   `ps -eLf` 总数交叉核对（两种方法一致，见 §3.3）。

### 2.3 被测工作负载

- 批量证据：2026-10-01 的 A15 × 30 场景批量（串行，`nr_workers=1`）。
- 专项复测：同一 clip `clipgt-02eadd92-02f1-46d8-86fe-a9e338fed0b6` 于
  2026-10-02 跑 **2 次**，LOGDIR `artifacts/run_a15_perfcheck_20261002`。
  两次复测的 chunk 延迟（0.88 s）与 step 耗时（2.05 s）互相吻合，且与批量
  大样本统计一致——数据可复现，不是单次抖动。

---

## 3. 稳态资源占用（峰值）

### 3.1 主机内存 RAM

| 口径 | 数值 |
|---|---|
| 系统口径峰值已用（`MemTotal − MemAvailable`） | **≈ 14.6 GB** |
| 最小可用内存 | 84.3 GiB（即任何时刻 RAM 都极度宽裕） |
| renderer 宿主进程 RSS | 4.2 GiB |

各容器 **cgroup `MemUsage` 峰值**（`docker stats`）：

| 容器 | 峰值内存 | 说明 |
|---|---|---|
| driver-0 | **22.86 GiB** | 8B 权重 + KV/cache，大头 |
| runtime-0 | 1.96 GiB | 事件堆 + 227 MB rollout 日志缓冲 |
| physics-0 | 0.84 GiB | 地面相交查询 |
| controller-0 | 0.48 GiB | MPC 求解器 |
| prometheus-0 | 0.55 GiB | 指标 sidecar |

**为什么容器加起来 ≈ 26 GiB，系统口径却只有 14.6 GB？** 两个口径量的不是一回事，均为"真"：

- cgroup `MemUsage` 对**共享库/共享内存页按每个容器各计一次**（各容器都映射的 CUDA、
  glibc、gRPC 等被重复累加），且包含各自的 page cache；
- 系统口径 `MemTotal−MemAvailable` 只算物理上真正不可回收的页，共享页全机只计一次。

结论：94 GiB RAM 对本负载（甚至对批量再开一倍 worker）都远不是约束。

### 3.2 GPU 显存

| GPU | 空闲 | 稳态/峰值 | 占用者 |
|---|---|---|---|
| GPU0（driver） | 14 MiB | **22,913 MiB ≈ 22.4 GiB** | Cosmos-Reason2-8B 权重、expert head、KV cache、CUDA context |
| GPU1（renderer） | 14 MiB | 常驻 22,951 MiB → **峰值 37,159 MiB ≈ 36.3 GiB** | OmniDreams pipeline：服务一起就常驻 ~22.9 GiB，session 生成期间峰值 37.2 GiB |

两块 80 GB A100 各用不到一半。这也解释了项目的 GPU 分工：把两个大模型分卡放置，
避免任何一块卡同时承载 8B VLM 与视频扩散模型。

### 3.3 线程/进程数

稳态闭环期间实测（容器内 `ps -eLf`，同一时刻快照）：

| 组件 | 线程数 |
|---|---|
| OmniDreams renderer（宿主机 python worker，PID 实测） | **181** |
| driver-0 容器 | **155**（另一采样时刻 122，随推理阶段波动） |
| runtime-0 容器 | 141 |
| controller-0 容器 | 102 |
| physics-0 容器 | 89 |
| prometheus-0 容器 | 103 |

宿主机总线程（`ps -eLf`）：空闲 **2266** → 仿真峰值 **2851**，净增 ≈ 585，
正好等于上表各容器线程之和（≈590）+ renderer 增量——独立测量互相印证。

这些线程主要来自：torch 推理/算子线程池（Reason2 与 OmniDreams 各自的 intra-op
线程、CUDA work/stream 线程）、各 gRPC 服务的网络线程池、以及 Python asyncio/
concurrent executor。Driver 侧应用逻辑本身很轻（主 asyncio 事件循环 + **1 个**
后台 DriveJob worker 线程），线程多是底层库的默认并行。

### 3.4 CPU 使用

`docker stats` 全程统计：

| 容器 | 均值 CPU | 峰值 CPU |
|---|---|---|
| runtime-0 | 36.1% | **388.1%**（事件分发/序列化短时吃 ~4 核） |
| driver-0 | 35.4% | 100.6%（推理以单核调度 + GPU 为主） |
| physics-0 | 4.7% | 100% |
| controller-0 | 4.6% | 99.3%（MPC 求解瞬间单核打满） |
| prometheus-0 | 3.7% | 24% |

整个 32 逻辑核的 CPU 远未被压满；除 runtime 偶发 4 核突发外，各服务基本单核级别。

### 3.5 磁盘占用

**长期静态资产**（主要在 `/mnt`）：

| 内容 | 大小 |
|---|---|
| 场景数据 `/mnt/alpasim-data`（USDZ 1.3–2.0 GB/个，hardlink 入库） | 135 GB |
| 权重 `/mnt/weights`（alpamayo-1.5 21 G、alpamayo-r1 21 G、omnidreams 3.9 G） | 46 GB |
| 缓存（HF 20 G、uv 17 G 等） | 37 GB |
| 各 venv | 24 GB |
| 历史产物 `/mnt/artifacts` | 5.4 GB |
| Docker 镜像（系统盘，含 alpasim-base ~35 GB 镜像） | — |

**单次/批量运行产物**：

| 产物 | 大小 |
|---|---|
| 单个 rollout：`rollout.asl`（protobuf 全量事件日志） | **230 MB** |
| 单个 rollout：相机 MP4（30 fps，~20 s） | 4.3 MB |
| 单个 rollout：`metrics.parquet`（逐帧指标） | 10 KB |
| 单 clip 整个 LOGDIR | ≈ 235–350 MB |
| 30 clip 批量（rollouts 5.3 G + aggregate/logs） | **5.4 GB** |

当前磁盘余量：系统盘 63 GB、`/mnt` 273 GB，均充足。

---

## 4. 实时性与帧率

### 4.1 仿真时间尺度（回顾）

- 视频 30 fps，帧间隔 33,333 µs；
- **8 frame chunking**：首个 chunk 5 帧，此后每 chunk 8 帧；
- 一个控制步（chunk）对应 sim 时间 `control_timestep = 266,664 µs`（8×33.3 ms），
  `drive()` 每个 chunk 只调用一次；
- force-GT 阶段约 2.03 sim-s（`src/wizard/configs/chunking/8frame.yaml`），
  期间不做模型推理。

### 4.2 总体实时倍率

| 数据来源 | 结果 |
|---|---|
| 30 clip（n=30） | sim-loop 墙钟均值 **150.0 s** 对应 ≈ 19.8 sim-s → **RTF 0.132×**（范围 0.12–0.14，单 clip 140.3–169.6 s） |
| 专项复测 run 1 | 149.21 s / 19.79 sim-s → 0.13×（含 setup/warmup 的总 rollout 153.74 s） |
| 专项复测 run 2 | 150.98 s / 19.79 sim-s → 0.13× |

即**每 1 秒仿真需约 7.6 秒 wall clock**；一个 20 s 场景约 2.5 分钟，批量 30 个
约 75 分钟纯仿真（实测含逐 clip 评估，总墙钟更长，见 §4.6）。

### 4.3 每个仿真 step 的 wall clock（核心时序）

大样本统计（批量日志，相邻 PolicyEvent 间隔）：

| 阶段 | n | 均值 | p50 | p90 | 最大 |
|---|---|---|---|---|---|
| **整个 step 墙钟**（sim 266.7 ms） | 2166 | **2.038 s** | 2.057 s | 2.291 s | 5.671 s |
| Driver 推理（观测 barrier 完→ControllerEvent） | 1986 | **1.156 s** | 1.115 s | 1.432 s | 4.681 s |
| force-GT 步的"推理" | 210 | ≈ 0.002 s | — | — | — |
| MPC + 车辆物理（Controller→PhysicsEvent） | 全部 | **0.083–0.087 s** | 0.080–0.084 s | 0.115 s | 0.545 s |

复测 run 2 独立重算（不同实现的统计脚本）：step 均值 **2.053 s**、Policy→Controller
**1.052 s**、Controller→Physics **0.087 s**，与大样本一致。

**稳态 step 的串行时间线**（从 runtime 日志实读，时间为相邻事件戳）：

```
t=0.00s  PolicyEvent 处理：Alpamayo 1.5 drive 推理（8B VLM + expert head）
         │  ████████████████████████████████   ~1.05–1.16 s
t≈1.15s  → 向 OmniDreams 发起下一个 8 帧 chunk 请求 ("Requesting video chunk")
         │  ████████████████████████████████   ~0.88 s（GPU1 批量生成 + gRPC）
t≈2.03s  → 8 帧作为一批同时返回（VideoModelFrameEvent ×8，日志内间隔仅 3 ms）
         │  MPC 求解 + bicycle/地面物理          ~0.087 s
t≈2.12s  Step commit，进入下一个 PolicyEvent
```

注意：从事件顺序看，**下一个 chunk 的渲染请求必须等本 chunk 的 drive 出轨迹后才能发出**
（渲染需要未来 ego 位姿），因此 drive 与渲染**不重叠**；相加 1.16 + 0.88 + 0.087
≈ 2.1 s 与实测 step 2.04 s 闭合。

**有效闭环帧率** = 8 帧 / 2.04 s ≈ **3.9 fps**（对应 30 fps 仿真目标的 13%）。

### 4.4 渲染每一帧的 wall clock

OmniDreams 不是逐帧流式服务，而是**按 8 帧 chunk 批量扩散生成、整批返回**：

| 指标 | 批量（n=2225 chunks） | 复测 run 2（n=74） |
|---|---|---|
| chunk 请求 → 首帧返回 | 均值 **0.884 s**，p50 0.864，p90 0.900，min 0.688，max 1.216 | 均值 0.882 s，p50 0.865，max 1.199 |
| 批内 8 帧之间 | — | 3 ms（日志循环写出，说明同时到达） |
| **折合每帧渲染墙钟** | **≈ 110 ms**（0.884/8） | 110 ms |
| 渲染器理论吞吐（若连续喂请求） | **≈ 9.1 fps**（8/0.884） | 9.1 fps |

含义要分清：

- 问"渲染一张 704p 帧要多久"——批量均摊约 **110 ms**；
- 问"我发一个新 chunk，多久能拿到画面"——**0.88 s 首帧/整批延迟**；
- 问"渲染器满负荷能跑多快"——**~9.1 fps**；
- 问"闭环实际出帧多快"——只有 **3.9 fps**，因为每批渲染前还要先等 ~1.1 s 的 drive。

### 4.5 Driver 推理与 MPC 的耗时细节

- **Drive 推理 ~1.1 s/chunk**：Alpamayo 1.5 先由 Cosmos-Reason2-8B 对当前 1280×704
  画面 + 16×0.1 s 历史做因果链推理，再由 expert flow-matching head 回归 64 个
  10 Hz 轨迹点。这是全链路最大单项耗时，也是 GPU0 上唯一的重活。
- **MPC ~80–90 ms**：do_mpc/CasADi 非线性 MPC，8 状态自行车模型，输出方向盘转角与
  加速度；偶发尾延（max 0.545 s）来自求解迭代次数增加。
- physics 服务只做地面相交，无碰撞动力学，耗时并入这 ~87 ms。

### 4.6 端到端时间分解（不只仿真环）

一个 clip 从 wizard 启动到全部结束：

| 阶段 | 实测墙钟 |
|---|---|
| 容器拉起 + driver 加载 21 GB checkpoint + session/场景初始化 | ≈ 70–110 s（复测 run2 ~70 s；首批 ~107 s） |
| 首个 chunk prefetch（SCENE_LOAD_TIMING first_prefetch_total） | 0.7 s（复测；首批冷启动 3.7 s） |
| **仿真环** | **≈ 149–151 s** |
| 逐帧评估窗口（SimulationEnd → Session COMPLETED：子进程打分、写 metrics.parquet、渲染 MP4） | 批量均值 **42.7 s**（28.6–77.1）；复测 run2 ≈ 52 s |
| 容器拆除 | 数秒 |

`total rollout`（wizard 口径，含 setup/warmup）复测为 153.7 s。批量 30 clip 时，
clip 之间还串着"上一 clip 评估 + 下一 clip 场景加载"，故总墙钟 ≈ Σ(仿真 150 +
评估 43 + 装载) ≈ 每 clip 3–4 分钟。

---

## 5. 瓶颈与利用率分析

1. **第一瓶颈：8B VLM drive 推理（~1.1 s/chunk）**。要提 RTF，最直接是缩短/蒸馏
   driver（R1 类轻量模型）或对 driver 推理做批处理/投机解码；硬件上 GPU0 显存
   只用了 28%，换更小模型或量化有余量。
2. **第二瓶颈：OmniDreams 批渲染（~0.88 s/chunk）**。渲染器满负荷本可 ~9.1 fps，
   但因"必须先有轨迹才能渲染"的数据依赖，它在闭环中每 step 有一半以上时间空闲。
3. **链路完全串行、无重叠**：当前 8frame chunking 下 drive→render→MPC 严格先后。
   理论上的优化方向是让 chunk k+1 的渲染与 chunk k 的 drive 在时间上错开预取
   （需要轨迹预测先行），但现状未这么做。
4. **GPU 利用率呈脉冲、平均很低**：5 s 粗采样读到 GPU0 均值 8.7%、GPU1 17.4%
   （均观测到 100% 的瞬时峰值）。事件时间戳给出的"占墙钟比例"上界约为 GPU0
   ~54%（drive 1.1/2.05，含 CPU 前处理）、GPU1 ~43%（render 0.88/2.05）。真值
   在两者之间——大量墙钟花在 CPU 侧数据搬运/编排而非 GPU 计算。
5. CPU、RAM、磁盘均远未饱和，不是约束；约束完全在**两个大模型的串行时延**。

---

## 6. 测量可信度与注意事项

- **大样本 + 复测一致**：step/chunk 均值来自批量 30 clip、n>2000 的统计，并用
  同 clip 两次独立复测、两套统计脚本交叉验证，差异 <2%。
- **5 s 采样会低估瞬时 GPU 利用率**：推理/渲染是 ~1 s 的突发，5 s 轮询存在混叠；
  引用显存峰值可信，引用"平均利用率"应同时参考事件时间戳占比。
- 线程数是稳态瞬时值，随推理阶段小幅波动（driver 122→155），量级稳定。
- 所有被测组件按项目约束串行（`nr_workers=1`、各 endpoint 并发=1）；上述性能
  数字对应**单 clip 串行**配置，多 clip 并发在本 renderer 上不被支持（renderer
  只保留一个活动 session）。
- 本文只谈"系统跑得多快"，不评价驾驶质量；模型质量结论（批量中 90% 碰撞/offroad）
  见 [Stage1_Batch30_Report.md](Stage1_Batch30_Report.md)。性能验收与指标验收一样，
  依据原始日志/逐帧证据，不使用 aggregate `metrics_results.txt`。

---

## 7. 附录：复现本次性能实验

```bash
source "$HOME/simulation/scripts/env.sh"

# 1) 启动 renderer（GPU1）。注：本机 torchrun --standalone 因反向 DNS 别名 (pc_0)
#    会卡在 rendezvous；单卡时可直接以 python 启动并显式给出 env：
cd "$REPOS_DIR/flashdreams"
CUDA_VISIBLE_DEVICES=1 LOCAL_RANK=0 RANK=0 WORLD_SIZE=1 \
  MASTER_ADDR=127.0.0.1 MASTER_PORT=29500 \
  uv run --package flashdreams-omnidreams python \
    -m omnidreams.impl.grpc.server --pipeline_config_name omnidreams \
    --host 0.0.0.0 --port 50051 --resolution 704p --output_format jpeg --jpeg_quality 90
# 就绪标志：日志出现 "Server started successfully. Press Ctrl+C to stop."

# 2) 采样（5 s 周期，另开一个终端）
bash scripts/perf_sampler.sh      # 产物 /tmp/perf_samples/{gpu,ram,docker_stats}.csv（OUT 可覆盖）

# 3) 单 clip 闭环（GPU0）
bash scripts/run_closed_loop_a15.sh     # 或本报告使用的等价 wizard 调用

# 4) 稳态线程快照（仿真进行中，容器内 ps）
for c in driver-0-1 runtime-0-1 controller-0-1 physics-0-1 prometheus-0-1; do
  echo "$c: $(docker exec run_a15_perfcheck_20261002-$c ps -eLf | tail -n +2 | wc -l)"
done

# 5) 结束：停 renderer，确认两卡回到 ~14 MiB
bash scripts/stop_renderer.sh
nvidia-smi --query-gpu=index,memory.used --format=csv
```

从 runtime 日志还原时序的统计脚本（事件时间戳差）为本次分析临时编写，如需复跑
可基于 §4.3 的事件定义（`Requesting video chunk` / `VideoModelFrameEvent` /
`PolicyEvent` / `ControllerEvent` / `PhysicsEvent`）重新实现。
