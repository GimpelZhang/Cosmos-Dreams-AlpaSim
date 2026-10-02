# Stage 1：Driver 视角的一个输入/输出轮回（Alpamayo 1.5 闭环）

> 本文**只回答一件事**：在闭环仿真中，站在 Driver（驾驶策略）的角度，它在一个
> loop 里**被喂了什么、以什么接口喂进来；它吐出了什么、吐给了谁；之后这些输出
> 如何变成仿真 ego 车的运动，又如何变成下一帧它看到的画面，以及评价模块在这个
> 过程中"偷听"到了什么、最终怎样产出报告**。
>
> 实例：**Alpamayo 1.5**（ReasoningVLA：Cosmos-Reason2-8B + 单专家扩散头）。
> 系统级全貌（六个 gRPC 服务、OmniDreams/Alpamayo 内部实现、部署拓扑）见
> [`Stage1_Architecture.md`](Stage1_Architecture.md)；从零跑通的操作步骤见
> [`Stage1_Reproduction_Guide.md`](Stage1_Reproduction_Guide.md)。
>
> 代码版本：alpasim @ `affc2ea`、flashdreams @ `0957cf0`、alpamayo @ `11a0e01`。
> 下文所有路径中 `repos/alpasim/` 为前缀省略。

---

## 0. 先约定坐标系（否则接口语义会读反）

proto 注释反复引用三套位姿（见 `src/grpc/alpasim_grpc/v0/egodriver.proto:77`、
`Stage1_Architecture.md` §2）：

| 坐标系 | 含义 |
|---|---|
| **local** | 当前控制步的车辆局部坐标系；每个 step 以本步起点为原点 |
| **rig** | ego rig（车身/传感器刚性框架）的**真值**位姿，active transform = `local → rig` |
| **rig_est** | runtime 给 Driver 的**估计**位姿 `local → rig_est`。注入误差模型时它可以与 rig 偏离；Driver 始终在估计系里工作 |
| **AABB / ds** | 场景全局坐标 / 真实车辆坐标系，用于贴地与渲染，由 `transform_ego_coords_ds_to_aabb` 联系 |

关键推论：**Driver 收到的观测在 `rig_est` 系，输出的轨迹也是 `local → rig_est`**
（`egodriver.proto:110` 注释原文："The drive_response trajectory stores the active
transform local->rig_est produced by the driver"）。runtime 随后负责把它翻译回
真值系，Driver 自己不感知这个差别。

---

## 1. 一个 loop 的时间尺度

- 渲染/视频时间轴 **30 fps**，`frame_interval_us = 33,333`；
- 采用 **8frame chunking**：首块 5 帧（warmup），此后每块 8 帧；
- **控制步长 `control_timestep_us = 266,664 = 8 × 33,333`**；
- Driver 的 `drive()` **每个 chunk 只被调用一次**，一次给出覆盖未来 6.4 s 的轨迹；
  但 chunk 内的 8 张画面是**逐帧、各自带时间戳**推给 Driver 的；
- rollout 开头有 `force_gt_duration = 2,033,313 µs`（约 2 s）的**强制回放期**：
  此期间不调用策略，ego 沿录制真值走，用于把状态"热启动"到真实交通场景里。

所以一个"普通 loop"= 仿真时间 **266,664 µs**，事件堆（event heap）在这段时间内
按 `(时间戳, 优先级)` 依次执行下列事件（`events/base.py:21`）：

```
优先级  事件                           Driver 视角的含义
 10    VideoModelFrameEvent   ×8       OmniDreams 渲染好的一帧 → submit_image_observation
 20    PolicyEvent                     AlpaSim 推 egomotion/route/GT，屏障后调用 drive()
 40    ControllerEvent                 drive 输出 → MPC → 转向/加速度 → 车辆模型积分
 50    PhysicsEvent(EGO)                真值位姿贴地修正
 60    TrafficEvent                     （skip 模式）回放录制的交通参与者
 70    PhysicsEvent(TRAFFIC)            交通参与者贴地
 80    StepEvent                        提交本步状态；广播 ActorPoses；开下一步
 (90)  VideoModelPrefetchEvent          用本步算出的新位姿 → 向 OmniDreams 预约渲染下一 chunk
```

下文严格按 Driver 的感受顺序展开：**先收输入（§2）→ 内部思考（§3）→ 产出输出
（§4）→ 输出怎样被下游消费（§5–§6）→ 评价模块怎样旁观（§7）**。

---

## 2. Driver 在一个 loop 里收到的输入

Driver 是一个 gRPC **server**（`EgodriverService`，容器 `driver-0`，:6005），
AlpaSim runtime 是它的 client（`services/driver_service.py`）。也就是说，
**"输入"全部是 runtime 主动调 Driver 的 RPC 推/触发进来的**。proto 契约：

```protobuf
service EgodriverService {                               // egodriver.proto:11
  rpc start_session (DriveSessionRequest) returns (common.SessionRequestStatus);
  rpc submit_image_observation (RolloutCameraImage) returns (common.Empty);
  rpc submit_egomotion_observation (RolloutEgoTrajectory) returns (common.Empty);
  rpc submit_route (RouteRequest) returns (common.Empty);
  rpc submit_recording_ground_truth (GroundTruthRequest) returns (common.Empty);
  rpc drive (DriveRequest) returns (DriveResponse);
  ...
}
```

### 2.1 会话建立时的一次性输入：`start_session`

rollout 开始时，runtime 发 `DriveSessionRequest`
（`services/driver_service.py:55` → `_initialize_session`）：

- `session_uuid`：本 rollout 会话号；
- `random_seed`：随机种子；
- `debug_info.scene_id`：场景 id（benchmark 模式可不带，防数据泄漏）；
- `rollout_spec.vehicle.available_cameras`：本车**有哪些相机**（logical id、
  规格），Driver 据此为每个相机构建帧缓存。

Driver 侧 `start_session`（`src/driver/.../main.py:776`）创建 `Session`
（`main.py:169`），持有：每相机 `frame_caches`、位姿历史 `poses`、`route`、
`current_command`（导航指令）、`last_selected_plan`（上一轮选中的轨迹，用于
多轮间一致性）、推理计数 `inference_count`。

### 2.2 OmniDreams 给的输入：渲染画面（图像观测）

**数据由谁产生**：OmniDreams（外部宿主进程，GPU1，:50051）应 runtime 的
`render_video_chunk` 请求，一次渲染一个 chunk（5 或 8 帧），分辨率
**1280×704、JPEG 质量 90**（`flashdreams`，见 `Stage1_Architecture.md` §5）。

**以什么接口、什么形式到 Driver**：

1. runtime 的 `VideoModelPrefetchEvent` 拿到 chunk 后，`_emit_frames`
   把 chunk 拆成**逐帧事件** `VideoModelFrameEvent`（prio 10）
   （`events/video_model/prefetch.py:224`，每相机每帧一个事件；
   `_build_forwarding_mask` 负责在子采样时选最近时间戳）。
2. 每个帧事件执行时（`prefetch.py:82` `handle`）：

   ```python
   if self.should_submit_to_driver:
       await self.driver.submit_image(self.frame)   # prefetch.py:92
   ```

   即一帧一帧地调 Driver 的 `submit_image_observation`，**8 帧 = 8 次 RPC**，
   不是一个张量批。
3. runtime client 打包（`services/driver_service.py:105`）：

   ```python
   RolloutCameraImage(
       session_uuid=...,
       camera_image=RolloutCameraImage.CameraImage(
           frame_start_us=..., frame_end_us=...,      # 该帧的曝光起止时间
           image_bytes=<JPEG 编码字节>,               # 不传原始 RGB 张量
           logical_id=<相机逻辑 id>,
       ),
   )
   ```

4. Driver 侧 `submit_image_observation`（`main.py:822`）：在 **GPU 上 nvJPEG
   解码**（无 CUDA 时退回 PIL，并做畸变校正），做最小尺寸检查，然后
   `session.add_image(logical_id, frame, frame_end_us)`（`main.py:289`）
   写入该相机的 `frame_cache`。

> 要点：Driver 看到的是**编码字节 + 时间戳 + 相机 id**；没有任何深度、语义、
> 点云（OmniDreams 只输出 RGB）。首块的条件帧在 session 建立时由 USDZ 内
> `frames/<相机>/` 下最早时间戳的真实 JPEG 提供（`video_model/utils.py`
> `extract_first_frames`），保证第一次推理有"上一帧"可看。

### 2.3 AlpaSim runtime 给的输入：自车运动、路线、真值

这三类在 `PolicyEvent`（prio 20，`events/policy.py:32`）里**同一步**推给 Driver。

#### (a) 自车运动观测：`submit_egomotion_observation`

PolicyEvent 从已提交的**估计轨迹** `state.ego_trajectory_estimate` 中，挑时间戳
新于上次推送的帧，插值出本 chunk 覆盖的时间区间（`policy.py:114` 附近）：

- 位姿：`ego_trajectory.trajectory().interpolate(ts_arr)`；
- 动力学：`interpolate_dynamics(ts_arr)` 并转成 rig 系
  （`geometry.array_to_dynamic_states`，含速度/加速度）。

然后调用（`services/driver_service.py:130`）：

```python
RolloutEgoTrajectory(
    session_uuid=...,
    trajectory=trajectory_to_grpc(ego_trajectory),   # 估计位姿 local→rig_est
    dynamic_states=dynamic_states_in_rig,            # 与 poses 严格 1:1
)
```

Driver 侧 `submit_egomotion_observation`（`main.py:869`）：`add_egoposes`
（`main.py:335`）并按严格 zip 写入逐时刻 dynamic state。**这就是模型 ego-history
的来源**——模型并不自己做车辆运动学，"我过去怎么动的"是 runtime 喂给它的。

#### (b) 导航路线：`submit_route`

`policy.py:132`：`route_generator.generate_route(step_start_us, ...)` 生成
polyline，两次坐标变换到 rig_est 系后提交（`services/driver_service.py:163`，
`polyline_to_grpc_route` → `RouteRequest`）。线上形式就是

```protobuf
message Route { fixed64 timestamp_us; repeated common.Vec3 waypoints; }
```

Driver 侧 `submit_route`（`main.py:886`）：存 `session.route`，并
`update_command_from_route` 得到高层指令（直行/左转/右转等）。

#### (c) 录制真值：`submit_recording_ground_truth`

`policy.py:139`：把**真实录制车辆**的 GT 轨迹变换到当前 rig 系后提交
（`services/driver_service.py:185`）。Driver 侧实现写得很直白
（`main.py:907`）：**"received but not used by driver"**——策略推理不消费它，
它进入消息流仅供评价/分析使用（benchmark 数据溯源）。

#### (d) 触发推理的"时钟输入"：`drive(DriveRequest)`

观测推完不等于推理。PolicyEvent 先设好 `step_context`
（`step_start_us`、`target_time_us`、`force_gt` 标志，`policy.py:69`），
然后发出推理请求（`services/driver_service.py:210`）：

```python
DriveRequest(
    session_uuid=...,
    time_now_us=step_start_us,      # 当前时刻
    time_query_us=target_time_us,   # 期望覆盖到的时刻
    renderer_data=renderer_data or b"",  # 渲染器可附带的任意字节；本部署为空
)
```

### 2.4 观测屏障：不等到齐不推理

在真正 `drive()` 之前，PolicyEvent 执行（`policy.py:145`）：

```python
await ctx.drain_outstanding_tasks()
```

即等所有在途 RPC（8 张图像、egomotion、route、GT）全部返回。这样 Driver worker
被唤醒时，这一 chunk 的观测在 `Session` 里必然齐了。强制回放期内则直接把 GT
参考轨迹塞进 `driver_trajectory` 并返回（`policy.py:153-156`），**根本不调用
模型**——这解释了为什么 2 s 内 GPU0 上没有 Alpamayo 推理。

### 2.5 输入清单速查

| # | 来源 | RPC（runtime → Driver） | 载荷形式 | Driver 用来干嘛 |
|---|---|---|---|---|
| 1 | session | `start_session` | uuid / seed / scene_id / 可用相机列表 | 建 Session 与帧缓存 |
| 2 | **OmniDreams** | `submit_image_observation` ×8 | JPEG 字节 + 起止时间戳 + logical_id | 视觉上下文（多相机视频） |
| 3 | **AlpaSim** | `submit_egomotion_observation` | `Trajectory`（位姿）+ `dynamic_states`（1:1） | ego history：过去位姿/速度/加速度 |
| 4 | **AlpaSim** | `submit_route` | `Vec3` 路线点 + 时间戳 | 导航文本/转向指令 |
| 5 | **AlpaSim** | `submit_recording_ground_truth` | GT 轨迹 | 不用于推理，仅评价溯源 |
| 6 | **AlpaSim** | `drive` | `time_now_us` / `time_query_us` / `renderer_data` | 触发一次规划 |

---

## 3. Driver 内部：一个 loop 里 Alpamayo 1.5 做了什么

`drive()` RPC 在 Driver 进程内并不是直接跑模型，而是**入队 → 单一后台 worker
线程串行执行**。

### 3.1 入队与阻塞取回

`EgoDriverService.drive`（`main.py:918`）：

1. 就绪检查：帧不够（`_check_frames_ready`）或没有 pose 快照，直接返回
   **空轨迹**（不会硬撑一个假规划）；
2. 以当前 pose、`time_now_us`、`session.current_command` 构造 `DriveJob`
   （`main.py:157`），丢进唯一的 job 队列；
3. 阻塞在 `future.result()` 等 worker 产出 `ModelPrediction`。

单 worker 的设计（`_worker_main`，`main.py:569`）保证多请求也严格串行——
与"OmniDreams 只保留一个 session、批量必须 `nr_workers=1`"互相呼应。

### 3.2 组装模型输入：`PredictionInput`

`_run_batch`（`main.py:704`）对每个 job 构造：

```python
PredictionInput(
    camera_images=...,          # 每相机取最新 context_length 帧 (ts, image)
    command=job.command,        # 高层导航指令枚举
    speed=speed,                # sqrt(vx² + vy²)，自车纵向速度
    acceleration=acceleration,  # 线性加速度 x 分量
    ego_pose_history=session.poses,
    inference_seed=session.seed + session.inference_count,
    previous_plan=session.last_selected_plan,  # 上一轮轨迹，保持跨轮一致
    route=session.route,
)
```

图像通过 `frame_cache.latest_frame_entries(context_length)`
（`frame_cache.py:90`）取最近若干帧。

### 3.3 Alpamayo 1.5 的具体计算

代码：`models/alpamayo1_5_model.py`（子类）+ `models/alpamayo_base.py`（基类）。

1. **图像预处理保持 uint8、不做归一化**（`alpamayo_base.py:565`
   `_preprocess_images`）：按相机标定顺序（`CAMERA_NAME_TO_INDEX`）排好，
   HWC→CHW，堆叠成 `(N_cameras, num_frames, C, H, W)`。
2. **ego history**：要求 **16 个历史步、每步 0.1 s**（共 1.6 s；
   `NUM_HISTORY_STEPS=16`、`HISTORY_TIME_STEP=0.1`，
   `alpamayo_base.py:356-358`），由 `build_ego_history`
   （`alpamayo_base.py:169`）从 §2.3(a) 的位姿历史构造，并得到
   `pose_local_to_rig_t0`（推理锚点位姿）。
3. **导航文本**：route 转成自然语言 `nav_text`（直行/转弯等），与图像一起
   进 prompt。
4. **两段式推理（ReasoningVLA）**：
   - VLM 部分 **`nvidia/Cosmos-Reason2-8B`** 先生成**因果链文本**
     （chain-of-thought / chain-of-causation）；
   - 专家头在 **unicycle 动作空间（加速度 a、曲率 κ）** 做 **flow-matching
     扩散**，输出 **T=64 个路点、频率 10 Hz、覆盖未来 6.4 s**
     （`OUTPUT_FREQUENCY_HZ=10`，`alpamayo_base.py:362`；
     `waypoint_timestamps_us = t0 + (1..64)×100,000`，
     `alpamayo_base.py:746`）。
   - 带 CFG 导航配置时使用 classifier-free guidance（部署实测约 60 GB 显存，
     故 driver 固定在 GPU0）。
5. `select_trajectory`（默认 `ALWAYS_FIRST`）选定一条；推理文本记录在
   `reasoning_text`（`alpamayo_base.py:721-723`）。返回 `ModelPrediction`
   ，含 `model_t0_us`（**最新一帧的时间戳**而非请求时间）、
   `pose_local_to_rig_t0`、路点时间戳、`selected_plan`。

---

## 4. Driver 的输出：什么形式、给到谁

### 4.1 输出形式

回到 `drive()`（`main.py:982` 起），`ModelPrediction` 经
`_convert_prediction_to_alpasim_trajectory`（`main.py:1011`）转换：

- 核心函数 `_rig_est_waypoints_to_local_trajectory`（`main.py:102`）：
  把每个路点在 **rig_est 系**的位置/朝向组成 4×4 位姿，逐个打上其路点时间戳；
- 产出的 `common.Trajectory` **以 `model_t0_us` 时刻的锚点位姿领头**，
  后接 64 个路点——即一条 `local → rig_est` 的**未来轨迹**（含时间戳，
  10 Hz，6.4 s）。

响应消息：

```protobuf
message DriveResponse {                        // egodriver.proto:109
  common.Trajectory trajectory = 2;           // local→rig_est 规划
  DebugInfo debug_info = 3;                   // pickle 的调试字典 + 采样轨迹
  bool terminate_session = 4;                 // 是否要求立即结束 rollout
}
```

`debug_info.unstructured_debug_info` 是 pickle 字典：`command`、帧数、相机数、
位姿数、轨迹点数、`reasoning_text`（即模型的因果链文本，落日志供分析）。

### 4.2 给到谁

`DriveResponse` 只给一个对象：**AlpaSim runtime 的 `PolicyEvent`**
（`services/driver_service.py:210` `drive()` 反序列化返回）。闭环的其余部分
全部由 runtime 串联，Driver 不直接接触 controller / physics / OmniDreams。

PolicyEvent 立刻做**估计系 → 真值系**的变换（`policy.py:175`，
`transform_trajectory_from_noisy_to_true_local_frame`）：

```python
drive_trajectory = (
    drive_trajectory_noisy
    .transform(state.ego_trajectory_estimate.last_pose.inverse())  # 退出估计系
    .transform(state.ego_trajectory.last_pose)                    # 进入真值系
)
state.step_context.driver_trajectory = drive_trajectory
```

这一步是"Driver 在估计系里开车、仿真在真值系里跑车"的接缝。无误差模型时两系
重合，变换为恒等。

---

## 5. 输出如何变成仿真 ego 车的控制与运动

### 5.1 ControllerEvent（prio 40）：MPC 跟踪 + 整车模型积分

`events/controller.py:19`。它把 `driver_trajectory` 当**参考轨迹**，不是当
直接控制量——Driver 给的是"想去的路"，真正的方向盘/油门由控制器解出。

1. **起步交接（handoff）**：闭环首步（force-GT 刚结束）置 `coerce_dynamic_state`
   ，用 GT 动力学强制对齐车速/加速度，避免 2 s 回放 → 闭环之间的突变
   （`controller.py:64-69`）。
2. 非强制时，速度由相邻位姿**有限差分**得到（rig 系线速度/角速度）。
3. **规划器延迟建模**：参考轨迹先进 `planner_delay_buffer`
   （`controller.py:123`），再按当前时刻取出——模拟真实系统"感知-决策-执行"
   的时延。
4. 调用 controller 容器（:6007，VDCService）：

   ```python
   controller.run_controller_and_vehicle(
       now_us, pose_local_to_rig,
       rig_linear_velocity_in_rig, rig_angular_velocity_in_rig,
       rig_linear_acceleration_in_rig,
       rig_reference_trajectory_in_rig,
       future_us=target_time_us,
       force_gt=..., coerce_dynamic_state=...,
       fallback_trajectory_local_to_rig=...,
       pose_reporting_interval_us=frame_interval,  # 按帧回报中间状态
   )
   ```

   proto：`controller.proto:15` `run_controller_and_vehicle`。
5. 控制器内部（详见 `Stage1_Architecture.md` §6）：
   - **非线性 MPC（CasADi / do_mpc，默认；线性 OSQP 备选）** 求解内部控制
     **`[steering_cmd (rad), accel_cmd (m/s²)]`** 跟踪参考轨迹；
   - 车辆模型为 **8 状态自行车模型**：车速 <5 m/s 用运动学、以上用动力学版本；
     含执行器滞后，参数按 Ford Fusion 标定；
   - 在 266,664 µs 内逐步积分，并按 33,333 µs 回报——响应里共 8（或首块 5）
     个 `PropagatedState`，每个同时含**真值位姿** `pose_local_to_rig` 与
     **估计位姿** `pose_local_to_rig_estimated` 及各自 dynamic state。
6. runtime 把它们转成 `ego_true` / `ego_estimated` 两条 `DynamicTrajectory`
   （`controller.py:157-165`），写入 `StepContext`。

> 即链路：**64 路点轨迹 → MPC 优化出方向盘转角 + 加速度 → 自行车模型数值积分
> → 未来 8 个时刻的真值/估计位姿**。Driver 全程不接触方向盘。

### 5.2 PhysicsEvent(EGO)（prio 50）：贴地

`events/physics.py:61`。把 `ego_true` 转到 AABB 系，调 physics 容器（:6006）
`ground_intersection`（`physics.py:81`）：对地面网格做 Warp ray cast + SVD
平面拟合，**只修正 z/俯仰/横滚，保持 x、y 不变**；与候选地面偏差 >1.5 m 或
倾角 >10° 则拒绝。结果写入 `ctx.corrected_ego_trajectory`
（`physics.py:90`）。注意：**没有动力学碰撞**，physics 只负责"别让车悬空/钻地"。

### 5.3 TrafficEvent / PhysicsEvent(TRAFFIC)（prio 60/70）

- 实际部署 **trafficsim 为 disabled/skip**（无 CATK 容器）。`TrafficEvent`
  （`events/traffic.py:33`）调用时，runtime 在本地**直接回放录制轨迹**
  （`services/traffic_service.py:142`：skip 模式按 `future_us` 从各 actor 的
  录制轨迹插值返回）。
- `PhysicsEvent(TRAFFIC)`（`physics.py:94`）把回放位姿累积进
  `ctx.traffic_trajectories`（默认不做全员物理）。

### 5.4 StepEvent（prio 80）：提交，成为"当前世界状态"

`events/step.py:41`——**全 loop 唯一真正改写轨迹状态的地方**：

```python
state.ego_trajectory          = state.ego_trajectory.concat(corrected_ego)   # step.py:57
state.ego_trajectory_estimate = state.ego_trajectory_estimate.concat(ego_estimated)
# 交通参与者逐对象 update_absolute(...)                                     # step.py:63-69
await log_actor_poses(state, ego_true.timestamps_us, broadcaster)           # step.py:72
state.step_context = StepContext()                                          # step.py:92
```

至此 Driver 的输出已经"变成了世界里真实发生的 8 个位姿"。

---

## 6. ego 位姿变化如何影响 OmniDreams 的下一帧渲染

闭环回到视觉的关键是 **`VideoModelPrefetchEvent`（prio 90，
`events/video_model/prefetch.py:96`）**，它在本步状态提交后（时间轴上提前一个
帧间隔）为下一 chunk 预约渲染：

1. **构造 ego 轨迹**（`prefetch.py:163` handle 内，调用
   `build_trajectory_for_video_model`，`video_model/utils.py:79`）：
   对 `[chunk_start + i×33,333]` 共 8 个时间戳，从刚提交的
   `state.ego_trajectory` **插值**出 8 个位姿（越界则 clamp），打成
   `common.Trajectory`。
2. **构造动态世界状态** `build_dynamic_world_state_for_video_model`
   （`video_model/utils.py:119`）：遍历非静态交通对象，在同 8 个时间戳上插值，
   每个 actor 给出 `class_id`（CAR/TRUCK/PEDESTRIAN/CYCLIST/OTHER）、
   `bbox_dims`、轨迹——即"未来 8 帧里别人在哪、是什么、多大"。
3. 发 RPC 给 OmniDreams（`prefetch.py:191`）：

   ```protobuf
   message VideoChunkRequest {                  // video_model.proto:87
     SessionId session_id = 1;
     common.Trajectory rig_trajectory = 2;      // ego 未来 8 个位姿
     DynamicWorldState dynamic_state = 3;       // 交通参与者未来 8 帧
   }
   ```

   OmniDreams 以这些位姿为相机运动条件、以 dynamic actors 为世界条件，扩散生成
   下一 chunk 的 RGB。
4. 返回的 chunk 经 `_emit_frames`（`prefetch.py:224`）拆成下一 loop 的 8 个
   `VideoModelFrameEvent`——它们会在下一轮以 prio 10 调
   `submit_image_observation` 把画面送进 Driver（回到 §2.2）。
5. `_schedule_next_prefetch`（`prefetch.py:206`）再预约 chunk+1，渲染始终
   提前于消费，形成流水线。

于是形成视觉闭环：

```
Driver 轨迹 → MPC/车辆模型 → ego 新位姿 ──rig_trajectory──► OmniDreams
      ▲                                                   │
      └──────────── 下一 chunk 的 JPEG 帧（submit_image）◄┘
```

> OmniDreams 端**只保留一个活动 session**，每次 `start_session` 先清旧 session，
> 因此批量必须严格串行（`nr_workers=1`、各 endpoint `n_concurrent_rollouts=1`），
> 并发 clip 会报 `Session not found`。

---

## 7. 评价模块如何获取信息、产出报告

评价模块**不在闭环数据路径上做任何拦截/改写**，而是作为广播总线的一个订阅者
"旁听"。

### 7.1 消息广播：每个 RPC 的请求/响应都留痕

`MessageBroadcaster`（`runtime/broadcaster.py`）把闭环中每一跳包成 `LogEntry`
广播。runtime 侧每个 driver RPC 在发送前后都会广播对应条目（见
`services/driver_service.py`，如 `submit_image` 的图像、`drive` 的
`driver_request` / `driver_return`）；StepEvent 还广播
`ActorPoses`（`events/step.py:122` `log_actor_poses`，逐时间戳列出
ego + 各交通参与者位姿，并已变换到 AABB 系）。

### 7.2 RuntimeEvaluator：累积 → rollout 结束子进程评分

`src/eval/src/eval/runtime_evaluator.py`：

- `on_message`（`runtime_evaluator.py:140`）实现广播 handler 协议，把所有
  评价相关条目交给 `EvalDataAccumulator.handle_message`：
  `rollout_metadata`（会话/AABB/GT）、`actor_poses`（重建各轨迹）、
  `driver_camera_image`（图像类指标）、`route_request`、
  `driver_request/driver_return`（规划轨迹指标）、相机标定等。
- rollout 结束时 `run_evaluation`（`runtime_evaluator.py:220`）在
  **`ProcessPoolExecutor` 子进程**里跑 `_evaluate_in_subprocess`
  （`runtime_evaluator.py:35`）：
  1. `ScenarioEvaluator.evaluate(scenario_input)` 跑全部 scorer；
  2. **写 `rollouts/<scene_id>/<rollout_uuid>/metrics.parquet`**
     （long format；`:54`）；
  3. 启用时渲染 rollout MP4（叠加轨迹/指标）。

评分内容包括：`dist_to_gt_trajectory`（轨迹偏差）、offroad（路面区域二值标志，
依赖 vector map）、碰撞（**事后**用 Shapely 多边形 + STRtree 判定，仿真内无碰撞
动力学）、plan deviation、速度/加速度类指标等。

### 7.3 聚合与必读警告

批量结束后各 rollout 的 parquet 被聚合成 `aggregate/metrics_results.txt`。
**不可只信 aggregate**：其中 `RemoveTimestepsAfterEvent(offroad_or_collision)`、
`RemoveTimestepsAfterEvent(dist_to_gt_trajectory >= 4)` 等截断算子会砍掉事件后
所有帧——碰撞/offroad 越早，aggregate 数字反而越"好看"（批量实测 aggregate
dist_to_gt=3.02 m vs 逐帧真值 20.06 m）。**验收依据永远是逐帧 `metrics.parquet`
（`values` 需 `pd.to_numeric(errors="coerce")`；同 metric/时间戳多相机行取 max）
+ 原始 rollout 视频**。另注意 `min_distance_to_lane_boundary_m` 可能整批恒 0、
`min_ade@5.0s(gt)` 在 8-frame chunking 下无输出。

---

## 8. 一个 loop 的全链路总表

| 阶段 | 事件/RPC（文件:行） | 传递的数据 |
|---|---|---|
| 渲染 chunk | OmniDreams `render_video_chunk`（`video_model.proto:14`） | 8 × 每相机 JPEG 1280×704 |
| 推图像 | `VideoModelFrameEvent` → `submit_image_observation`（`prefetch.py:92`；`main.py:822`） | `RolloutCameraImage`：JPEG bytes + 时间戳 + logical_id |
| 推运动 | PolicyEvent → `submit_egomotion_observation`（`policy.py:114`；`main.py:869`） | `RolloutEgoTrajectory`：位姿 + dynamic_states |
| 推路线 | → `submit_route`（`policy.py:132`；`main.py:886`） | `Route`：Vec3 点列 |
| 推 GT | → `submit_recording_ground_truth`（`policy.py:139`；`main.py:907`） | GT 轨迹（仅供评价） |
| 屏障 | `drain_outstanding_tasks`（`policy.py:145`） | 等待观测齐 |
| **推理** | `drive`（`policy.py:161`；`main.py:918`） | `DriveRequest(now, query)` → `DriveResponse(64 路点, debug, terminate)` |
| 系变换 | noisy→true（`policy.py:175`） | `local→rig_est` 轨迹 → `local→rig` |
| 控制 | `run_controller_and_vehicle`（`controller.py:129`） | MPC 出 [转向 rad, 加速度 m/s²]，自行车模型积分 8 个位姿 |
| 贴地 | `ground_intersection`（`physics.py:81`） | 修正 z/姿态 |
| 交通 | `simulate_traffic`（`traffic.py:47`，skip 回放 `traffic_service.py:142`） | 录制 actor 位姿 |
| 提交 | `StepEvent`（`step.py:41`） | concat 真值/估计轨迹；广播 `ActorPoses` |
| 预约渲染 | `VideoModelPrefetchEvent`（`prefetch.py:163`） | 下一 chunk 的 rig_trajectory + DynamicWorldState → OmniDreams |
| 评价 | `RuntimeEvaluator`（`runtime_evaluator.py:140/220`） | 累积全部 LogEntry → `metrics.parquet` + MP4 |

---

## 9. 关键代码索引

| 主题 | 位置 |
|---|---|
| Driver proto 契约 | `src/grpc/alpasim_grpc/v0/egodriver.proto` |
| 视频模型 proto（渲染输入） | `src/grpc/alpasim_grpc/v0/video_model.proto` |
| 控制器 proto | `src/grpc/alpasim_grpc/v0/controller.proto` |
| Driver gRPC servicer（会话/观测/drive/转换） | `src/driver/src/alpasim_driver/main.py` |
| 帧缓存（取最近 N 帧） | `src/driver/src/alpasim_driver/frame_cache.py` |
| Alpamayo 基类（预处理/history/predict） | `src/driver/src/alpasim_driver/models/alpamayo_base.py` |
| Alpamayo 1.5（Reason2-8B + 专家头） | `src/driver/src/alpasim_driver/models/alpamayo1_5_model.py` |
| PolicyEvent（观测/屏障/drive/系变换） | `src/runtime/alpasim_runtime/events/policy.py` |
| ControllerEvent（MPC/整车） | `src/runtime/alpasim_runtime/events/controller.py` |
| PhysicsEvent（贴地） | `src/runtime/alpasim_runtime/events/physics.py` |
| TrafficEvent + 回放 | `events/traffic.py`；`services/traffic_service.py` |
| StepEvent（提交/广播位姿） | `src/runtime/alpasim_runtime/events/step.py` |
| Prefetch（chunk 拆帧/预约渲染） | `src/runtime/alpasim_runtime/events/video_model/prefetch.py` |
| 渲染请求构造 | `src/runtime/alpasim_runtime/video_model/utils.py` |
| Driver RPC client | `src/runtime/alpasim_runtime/services/driver_service.py` |
| 评价（累积/子进程评分） | `src/eval/src/eval/runtime_evaluator.py` |
