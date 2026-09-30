#!/usr/bin/env python3
"""Task 1 gate：双 GPU、ffmpeg、Rust、uv、Docker、toolkit、双卡容器穿透、磁盘红线。"""
import os, re, subprocess, sys, shutil, pathlib

ROOT = pathlib.Path.home() / "simulation"
SUDO_PW = re.sub(r"^sudo password: *", "",
                 (pathlib.Path.home() / "access/access_methods.txt").read_text().strip())

def bash(cmd: str, timeout=600):
    return subprocess.run(["bash", "-c", f"source {ROOT}/scripts/env.sh; {cmd}"],
                          capture_output=True, text=True, timeout=timeout)

def docker_cmd(args, timeout=900):
    r = subprocess.run(["docker"] + args, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        r = subprocess.run(["sudo", "-S", "docker"] + args, input=SUDO_PW + "\n",
                           capture_output=True, text=True, timeout=timeout)
    return r

def main():
    # 双 GPU，且为 80GB
    r = bash("nvidia-smi -L")
    gpus = [ln for ln in r.stdout.splitlines() if ln.startswith("GPU")]
    assert len(gpus) >= 2, f"期望 >=2 GPU，实际 {len(gpus)}: {r.stdout}{r.stderr}"
    r = bash("nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits")
    for i, mib in enumerate(r.stdout.split()):
        assert int(mib) >= 80000, f"GPU {i} 不是 80GB 卡: {mib} MiB"

    # ffmpeg
    assert shutil.which("ffmpeg"), "ffmpeg 缺失"

    # rustc（位于 CARGO_HOME/bin）
    r = bash("rustc --version")
    assert r.returncode == 0, f"rustc 缺失: {r.stderr}"

    # uv
    r = bash("uv --version")
    assert r.returncode == 0 and "uv" in r.stdout, f"uv 缺失: {r.stderr}"

    # Docker daemon
    info = docker_cmd(["info"])
    assert info.returncode == 0, f"docker daemon 不可用: {info.stderr}"

    # nvidia-ctk
    r = bash("nvidia-ctk --version")
    assert r.returncode == 0, f"nvidia-ctk 缺失: {r.stderr}"

    # 容器双卡穿透
    run = docker_cmd(["run", "--rm", "--gpus", "all",
                      "nvidia/cuda:12.8.0-base-ubuntu22.04", "nvidia-smi", "-L"])
    assert run.returncode == 0, f"GPU 穿透失败: {run.stderr}"
    cg = [ln for ln in run.stdout.splitlines() if ln.startswith("GPU")]
    assert len(cg) == 2, f"容器内应见 2 张卡，实际 {len(cg)}: {run.stdout}"

    # 根分区红线
    st = os.statvfs("/")
    free_gb = st.f_bavail * st.f_frsize / 1e9
    assert free_gb >= 50, f"根分区剩余 {free_gb:.1f}G < 50G 红线"

    print("[SUCCESS] Task 1 passed: 2x A100-80GB, ffmpeg, rust, uv, docker + toolkit GPU passthrough OK.")

if __name__ == "__main__":
    main()
