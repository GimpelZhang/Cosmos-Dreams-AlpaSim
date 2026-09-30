这是一份专为 **AI Coding Agent**（如 Cursor, Claude Code, AutoGPT, Devin 等）设计并供你审查的 **Stage 1 详细可执行计划（Actionable Plan）**。

本计划针对 **双 NVIDIA A100 GPU (Headless 云服务器)** 环境进行了微服务拓扑编排（GPU 0 跑 Driver，GPU 1 跑 Renderer），确保 Agent 可以按步骤无歧义地自动化执行、自动化校验，并最终产出包含可视化 MP4 视频的最小闭环成果。

---

# Stage 1 执行计划：环境打通、双卡拓扑编排、模型加载与最小闭环可视化验证

## 🎯 Stage 1 核心目标
1. 部署并验证双 A100 Headless 云服务器基础环境与 Docker GPU 容器支持。
2. 拉取 `AlpaSim` 与 `OmniDreams` 仓库，配置 **GPU 0（Alpamayo Driver）与 GPU 1（OmniDreams Renderer）** 的双卡微服务拓扑。
3. 下载所需的模型权重（Alpamayo-R1、OmniDreams/FlashDreams 渲染权重）和最小测试场景 Asset。
4. 完成 Driver 与 Renderer 的单卡独立冒烟测试（Smoke Test）。
5. 运行一个 20 帧（约 2 秒）的最小闭环仿真，并**自动导出为人类可播放的 `.mp4` 可视化视频文件**。

---

## 📂 项目工作区目录结构标准（Agent 必须遵守）
在执行任何任务前， Agent 必须在 `/workspace` 或用户主目录下统一创建以下目录：
```text
/workspace/av_demo/
├── repos/
│   ├── alpasim/           # NVlabs/alpasim
│   └── omni-dreams/       # nv-tlabs/omni-dreams
├── weights/
│   ├── alpamayo/          # Alpamayo-R1 策略模型权重
│   └── flashdreams/       # OmniDreams/FlashDreams 渲染模型权重
├── assets/
│   └── nurec_scenes/      # AlpaSim 测试场景与 HDMap
├── configs/
│   └── topology_dual_a100.yaml # 双卡微服务分配配置
├── artifacts/             # 可视化输出目录 (视频/图片/Logs)
└── scripts/               # Agent 生成的测试与校验脚本
```

---

## Task 1: 云服务器环境预检与基础依赖部署

### 1.1 目标
验证硬件状态、存储空间，安装/配置 CUDA 12.8+、Docker、NVIDIA Container Toolkit 和 Rust 工具链。

### 1.2 Agent 执行指令
```bash
# 1. 创建标准工作目录
mkdir -p /workspace/av_demo/{repos,weights/alpamayo,weights/flashdreams,assets/nurec_scenes,configs,artifacts,scripts}
cd /workspace/av_demo

# 2. 预检 GPU 与存储空间
nvidia-smi --query-gpu=index,name,memory.total,driver_version --format=csv
df -h /workspace

# 3. 安装/更新基础系统依赖与 Python 工具
sudo apt-get update && sudo apt-get install -y \
    build-essential cmake git git-lfs ffmpeg libsm6 libxext6 \
    python3-pip python3-venv curl wget

# 4. 验证 Docker & NVIDIA Container Toolkit 可用性
docker --version
nvidia-ctk --version || echo "NVIDIA Container Toolkit not configured, installing..."

# 5. 测试 Docker GPU 穿透 (检查双卡是否均可见)
docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu22.04 nvidia-smi

# 6. 安装 Rust (AlpaSim 底层轨迹计算 utils_rs 所需)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
source "$HOME/.cargo/env"
rustc --version
```

### 1.3 自动校验断言（Verification Assertion）
Agent 必须运行以下 Python 脚本，返回 `[SUCCESS]` 方可进入 Task 2：
```python
# scripts/check_task1.py
import subprocess, sys, shutil

def check():
    # 检查 GPU 数量 >= 2
    res = subprocess.check_output(["nvidia-smi", "-L"]).decode()
    gpus = [line for line in res.split('\n') if 'GPU' in line]
    assert len(gpus) >= 2, f"Expected >= 2 GPUs, found {len(gpus)}"
    
    # 检查 ffmpeg
    assert shutil.which("ffmpeg") is not None, "ffmpeg is missing"
    
    # 检查 rustc
    rustc = shutil.which("rustc") or shutil.which(f"{sys.prefix}/bin/rustc")
    assert rustc is not None, "rustc is missing"
    
    print("[SUCCESS] Task 1 pre-check passed: Dual GPUs, FFmpeg, Rust confirmed.")

if __name__ == "__main__":
    check()
```

---

## Task 2: 仓库拉取与双 GPU 拓扑配置文件搭建

### 2.1 目标
克隆官方代码仓库，并配置基于 gRPC 的微服务双卡隔离拓扑文件。

### 2.2 Agent 执行指令
```bash
cd /workspace/av_demo/repos

# 1. 克隆代码库
git clone https://github.com/NVlabs/alpasim.git
git clone https://github.com/nv-tlabs/omni-dreams.git

# 2. 配置 Python 虚拟环境与基础包
python3 -m venv /workspace/av_demo/venv
source /workspace/av_demo/venv/bin/activate
pip install --upgrade pip
pip install huggingface_hub opencv-python-headless matplotlib pillow pyyaml grpcio grpcio-tools

# 3. 创建 AlpaSim 双卡拓扑配置文件
cat << 'EOF' > /workspace/av_demo/configs/topology_dual_a100.yaml
version: "1.0"
orchestrator:
  host: "127.0.0.1"
  port: 50051

services:
  driver_policy:
    type: "alpamayo_r1"
    gpu_id: 0
    cuda_visible_devices: "0"
    port: 50052
    vram_budget_gb: 40

  renderer_service:
    type: "managed_flashdreams"
    gpu_id: 1
    cuda_visible_devices: "1"
    port: 50053
    vram_budget_gb: 48
    config:
      camera_views: ["front_wide"]  # Stage 1 仅开启前视主摄像头以提速验证
      resolution: [512, 896]
      fps: 10

visualization:
  headless: true
  save_video: true
  output_dir: "/workspace/av_demo/artifacts"
EOF
```

### 2.3 自动校验断言
```python
# scripts/check_task2.py
import os, yaml

def check():
    assert os.path.exists("/workspace/av_demo/repos/alpasim"), "AlpaSim repo missing"
    assert os.path.exists("/workspace/av_demo/repos/omni-dreams"), "OmniDreams repo missing"
    
    with open("/workspace/av_demo/configs/topology_dual_a100.yaml") as f:
        cfg = yaml.safe_load(f)
    assert cfg["services"]["driver_policy"]["cuda_visible_devices"] == "0"
    assert cfg["services"]["renderer_service"]["cuda_visible_devices"] == "1"
    print("[SUCCESS] Task 2 passed: Repos cloned and Dual-GPU topology verified.")

if __name__ == "__main__":
    check()
```

---

## Task 3: 模型权重与测试场景 Asset 预下载及校验

### 3.1 目标
通过 HuggingFace CLI 锁存拉取 Alpamayo-R1 权重、OmniDreams/FlashDreams 权重以及一个 AlpaSim 样例场景。

### 3.2 Agent 执行指令
*前提：系统环境变量中需存在 `export HF_TOKEN="your_huggingface_token"`。*

```bash
source /workspace/av_demo/venv/bin/activate

# 1. 登录 HuggingFace (如果设置了 HF_TOKEN)
if [ -n "$HF_TOKEN" ]; then
    huggingface-cli login --token "$HF_TOKEN"
fi

# 2. 下载 Alpamayo-1 / Alpamayo-R1 策略模型权重 (或下载示例 Checkpoint)
python3 -c "
from huggingface_hub import snapshot_download
import os

print('Downloading Alpamayo policy weights...')
snapshot_download(
    repo_id='nvidia/Alpamayo-1', 
    local_dir='/workspace/av_demo/weights/alpamayo',
    ignore_patterns=['*.msgpack', '*.h5']
)
"

# 3. 下载 OmniDreams / FlashDreams 渲染引擎权重
python3 -c "
from huggingface_hub import snapshot_download

print('Downloading FlashDreams/OmniDreams renderer weights...')
snapshot_download(
    repo_id='nvidia/Cosmos-1.0-Diffusion-7B-Video2World', # 或官方指定 FlashDreams preset
    local_dir='/workspace/av_demo/weights/flashdreams',
    allow_patterns=['*.bin', '*.safetensors', '*.json', '*.pt']
)
"

# 4. 下载/生成 AlpaSim 最小测试 Scene Asset
cd /workspace/av_demo/repos/alpasim
python3 -m alpasim.utils.download_sample_scene --output_dir /workspace/av_demo/assets/nurec_scenes/sample_scene_01
```

### 3.3 自动校验断言
```python
# scripts/check_task3.py
import os, glob

def check():
    alpamayo_files = glob.glob("/workspace/av_demo/weights/alpamayo/*")
    assert len(alpamayo_files) > 0, "Alpamayo weights directory is empty"
    
    renderer_files = glob.glob("/workspace/av_demo/weights/flashdreams/*")
    assert len(renderer_files) > 0, "FlashDreams weights directory is empty"
    
    scene_files = glob.glob("/workspace/av_demo/assets/nurec_scenes/sample_scene_01/*")
    assert len(scene_files) > 0, "Sample scene directory is empty"
    
    print("[SUCCESS] Task 3 passed: All model weights and scene assets present.")

if __name__ == "__main__":
    check()
```

---

## Task 4: 双卡微服务独立冒烟测试 (Smoke Test)

### 4.1 目标
隔离测试：分别在 **GPU 0** 启动 Alpamayo Driver 容器/进程，在 **GPU 1** 启动 OmniDreams Renderer 容器/进程，确保不发生 OOM 且 gRPC 端口响应健康。

### 4.2 Agent 执行指令
Agent 需要编写启动脚本并在后台分别运行这两个服务，进行健康检查。

```bash
source /workspace/av_demo/venv/bin/activate

# 1. 启动 GPU 0 - Driver Service (后台运行)
CUDA_VISIBLE_DEVICES=0 python3 -m alpasim.services.driver \
    --model_path /workspace/av_demo/weights/alpamayo \
    --port 50052 > /workspace/av_demo/artifacts/driver_gpu0.log 2>&1 &
DRIVER_PID=$!
echo $DRIVER_PID > /workspace/av_demo/artifacts/driver.pid

# 2. 启动 GPU 1 - Renderer Service (后台运行)
CUDA_VISIBLE_DEVICES=1 python3 -m alpasim.services.renderer \
    --model_path /workspace/av_demo/weights/flashdreams \
    --port 50053 > /workspace/av_demo/artifacts/renderer_gpu1.log 2>&1 &
RENDERER_PID=$!
echo $RENDERER_PID > /workspace/av_demo/artifacts/renderer.pid

# 3. 等待模型加载 (60秒)
echo "Waiting 60 seconds for neural models to initialize on GPU 0 and GPU 1..."
sleep 60
```

### 4.3 自动校验断言 (显存与 gRPC 检查)
```python
# scripts/check_task4.py
import subprocess, time, grpc

def check_gpu_memory():
    res = subprocess.check_output(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"]).decode()
    used_mems = [int(x.strip()) for x in res.strip().split('\n')]
    print(f"Current VRAM usage: GPU 0={used_mems[0]}MB, GPU 1={used_mems[1]}MB")
    # 两张卡显存占用均应 > 10GB，证明模型已加载至不同 GPU
    assert used_mems[0] > 10000, "GPU 0 has no model loaded"
    assert used_mems[1] > 10000, "GPU 1 has no model loaded"

def check_grpc():
    # 校验 gRPC 端口可用性
    for port in [50052, 50053]:
        channel = grpc.insecure_channel(f'localhost:{port}')
        try:
            grpc.channel_ready_future(channel).result(timeout=10)
            print(f"Port {port} is live.")
        except grpc.FutureTimeoutError:
            raise RuntimeError(f"gRPC service on port {port} failed to respond.")

if __name__ == "__main__":
    check_gpu_memory()
    check_grpc()
    print("[SUCCESS] Task 4 passed: Driver (GPU 0) & Renderer (GPU 1) microservices healthy.")
```

---

## Task 5: 极简闭环仿真运行与可视化 MP4 导出校验

### 1.5.1 目标
由 AlpaSim 编排器主导，调用 GPU 0 的 Driver 生成 Action，驱动物理引擎，再调用 GPU 1 的 OmniDreams 渲染出图像，连续闭环运行 **20 步（Step）**，并将图像序列合成编码为 `/workspace/av_demo/artifacts/stage1_closed_loop.mp4`。

### 1.5.2 Agent 执行指令
```bash
source /workspace/av_demo/venv/bin/activate

# 执行 20 步闭环仿真并强制记录视频帧
python3 -m alpasim.orchestrator.run \
    --config /workspace/av_demo/configs/topology_dual_a100.yaml \
    --scene_dir /workspace/av_demo/assets/nurec_scenes/sample_scene_01 \
    --max_steps 20 \
    --output_video /workspace/av_demo/artifacts/stage1_closed_loop.mp4 \
    --dump_frames_dir /workspace/av_demo/artifacts/raw_frames

# 杀掉后台服务进程，释放资源
kill -9 $(cat /workspace/av_demo/artifacts/driver.pid) || true
kill -9 $(cat /workspace/av_demo/artifacts/renderer.pid) || true
```

### 1.5.3 可视化文件自动断言（关键步骤）
Agent 必须验证生成的 MP4 视频是否满足以下指标：文件存在、帧数正常、不是黑屏/纯色无效画面。

```python
# scripts/check_task5_visual.py
import os, cv2, numpy as np

def check_video():
    video_path = "/workspace/av_demo/artifacts/stage1_closed_loop.mp4"
    assert os.path.exists(video_path), f"Video file {video_path} does not exist!"
    assert os.path.getsize(video_path) > 100000, "Video file size is too small (<100KB), likely corrupted."

    cap = cv2.VideoCapture(video_path)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    
    print(f"Video Stats: {width}x{height}, Total Frames: {frame_count}")
    assert frame_count >= 15, f"Expected >= 15 frames, got {frame_count}"

    # 读取第 10 帧并检测图像均值与标准差，防止 OmniDreams 输出了全黑/全白空帧
    cap.set(cv2.CAP_PROP_POS_FRAMES, 10)
    ret, frame = cap.read()
    assert ret, "Failed to read frame 10 from output video"
    
    mean_val = np.mean(frame)
    std_val = np.std(frame)
    print(f"Frame 10 Mean Pixel Value: {mean_val:.2f}, Std: {std_val:.2f}")
    
    # 图像不应为纯色 (std > 10) 且不应全黑 (mean > 15)
    assert mean_val > 15, "Generated video frame is too dark/black!"
    assert std_val > 10, "Generated video frame lacks texture/content (solid color)!"

    cap.release()
    print("[SUCCESS] Stage 1 Closed-Loop Demo Verified! Video is human-viewable and valid.")

if __name__ == "__main__":
    check_video()
```

---

## 🚩 Stage 1 交付物列表（Acceptance Deliverables）

当 AI Agent 完成 Stage 1 执行后，审查者将检查以下产出物：

1. **可视化视频文件**：`/workspace/av_demo/artifacts/stage1_closed_loop.mp4`（人类可使用 VLC / 浏览器直接播放并查看端到端仿真画面）。
2. **日志记录**：
   - GPU 0 Driver 日志：`/workspace/av_demo/artifacts/driver_gpu0.log`
   - GPU 1 Renderer 日志：`/workspace/av_demo/artifacts/renderer_gpu1.log`
3. **配置文件**：`/workspace/av_demo/configs/topology_dual_a100.yaml`。
4. **验证通过标志**：执行所有 `check_task*.py` 均输出 `[SUCCESS]`。

---

### 给 AI Coding Agent 的启动指令提示词（Prompt）：
> "Please strictly follow the Stage 1 Implementation Plan above. Execute Task 1 to Task 5 sequentially in the `/workspace/av_demo` directory. After completing each task, run the corresponding Python verification assertion script. Do not proceed to the next task until the script outputs `[SUCCESS]`. Make sure the final `stage1_closed_loop.mp4` video is generated in `/workspace/av_demo/artifacts/`."
