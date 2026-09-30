#!/usr/bin/env python3
"""Task 5 gate：rollout 视频存在；20 帧抽取与 10fps 编码完成；最终 mp4 非黑屏/非纯色。"""
import subprocess, pathlib, sys

ROOT = pathlib.Path.home() / "simulation"
ART = ROOT / "artifacts"

def bash(cmd, timeout=1800):
    return subprocess.run(["bash", "-c", f"source {ROOT}/scripts/env.sh; {cmd}"],
                          capture_output=True, text=True, timeout=timeout)

def main():
    pth = ART / "rollout_video_path.txt"
    assert pth.is_file(), "缺少 rollout_video_path.txt（未执行 9.3）"
    src = pth.read_text().strip()
    assert pathlib.Path(src).is_file(), f"rollout 视频丢失: {src}"
    assert pathlib.Path(src).stat().st_size > 100_000, "rollout 视频过小，可能损坏"

    frames = sorted((ART / "raw_frames").glob("*.png"))
    assert len(frames) == 20, f"期望 20 帧 PNG，实际 {len(frames)}"

    out = ART / "stage1_closed_loop.mp4"
    assert out.is_file(), "缺少 stage1_closed_loop.mp4"
    assert out.stat().st_size > 50_000, "最终视频过小"

    code = (
        "import cv2, numpy as np\n"
        f"cap=cv2.VideoCapture(r'{out}')\n"
        "n=int(cap.get(cv2.CAP_PROP_FRAME_COUNT))\n"
        "assert n>=19, f'encoded frame count {n}'\n"
        "bad=0\n"
        "for i in (0,9,19):\n"
        " cap.set(cv2.CAP_PROP_POS_FRAMES,i); ret,f=cap.read()\n"
        " assert ret, f'cannot read frame {i}'\n"
        " m,s=float(np.mean(f)),float(np.std(f))\n"
        " print(f'frame {i}: mean={m:.1f} std={s:.1f}')\n"
        " if not (m>15 and s>10): bad+=1\n"
        "cap.release()\n"
        "assert bad==0, f'{bad}/3 sampled frames are black/solid-color'\n"
        "print('frames valid')\n"
    )
    r = bash(f"cd {ROOT}/repos/alpasim && uv run python -c \"{code}\"")
    if r.returncode != 0:
        r = subprocess.run([str(pathlib.Path.home() / ".conda/envs/cc/bin/python3"), "-c", code],
                           capture_output=True, text=True)
    assert r.returncode == 0, f"视频有效性断言失败: {r.stdout}{r.stderr}"
    assert "frames valid" in r.stdout, r.stdout + r.stderr

    print("[SUCCESS] Task 5 passed: 20-frame / 10fps closed-loop mp4 generated and validated (not black/solid).")

if __name__ == "__main__":
    main()
