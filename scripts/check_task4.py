#!/usr/bin/env python3
"""Task 4 gate：
1) Renderer 服务端口在监听且 GPU1 已加载模型（VRAM 阈值）；
2) Driver R1 推理退出码 0，且运行期间 GPU0 VRAM 超阈值。"""
import socket, subprocess, time, pathlib, sys, os

ROOT = pathlib.Path.home() / "simulation"
PORT = 50051
GPU1_MIN_MB = 15000
GPU0_MIN_MB = 20000
START_DEADLINE_S = 900

def bash(cmd, timeout=3600):
    return subprocess.run(["bash", "-c", f"source {ROOT}/scripts/env.sh; {cmd}"],
                          capture_output=True, text=True, timeout=timeout)

def gpu_used():
    r = bash("nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits")
    return [int(x) for x in r.stdout.split()]

def wait_renderer():
    t0 = time.time()
    while time.time() - t0 < START_DEADLINE_S:
        used = gpu_used()
        up = False
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            up = (s.connect_ex(("127.0.0.1", PORT)) == 0)
        print(f"[renderer] port_up={up} VRAM GPU0={used[0]} GPU1={used[1]}MB")
        if up and used[1] > GPU1_MIN_MB:
            return
        r = bash(f"kill -0 $(cat {ROOT}/artifacts/renderer.pid) 2>/dev/null && echo ALIVE || echo DEAD")
        if "DEAD" in r.stdout:
            raise RuntimeError("Renderer 进程提前退出，见 artifacts/renderer_gpu1.log")
        time.sleep(15)
    raise RuntimeError("Renderer 在 900s 内未就绪")

def run_driver_with_vram_gate():
    cmd = (f"cd {ROOT}/repos/alpamayo && "
           "CUDA_VISIBLE_DEVICES=0 PYTHONUNBUFFERED=1 uv run python src/alpamayo_r1/test_inference.py")
    p = subprocess.Popen(["bash", "-c", f"source {ROOT}/scripts/env.sh; {cmd}"],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         bufsize=1)
    lines, hit = [], False
    deadline = time.time() + 1800
    for ln in p.stdout:
        lines.append(ln)
        if time.time() % 5 < 1:
            used = gpu_used()
            if used[0] > GPU0_MIN_MB:
                hit = True
        if time.time() > deadline:
            p.kill()
            raise RuntimeError("Driver 推理超时 1800s")
    rc = p.wait()
    log = "".join(lines)
    (ROOT / "artifacts/driver_gpu0.log").write_text(log)
    assert rc == 0, f"Driver 推理失败 rc={rc}，日志尾部: {log[-800:]}"
    assert hit, f"运行期间 GPU0 显存未超 {GPU0_MIN_MB}MB，模型可能未上 GPU0"
    assert ("pred_xyz" in log or "pred_" in log), "日志未见轨迹输出"

def main():
    wait_renderer()
    run_driver_with_vram_gate()
    print("[SUCCESS] Task 4 passed: OmniDreams renderer live on GPU1, Alpamayo-R1 inference OK on GPU0.")

if __name__ == "__main__":
    main()
