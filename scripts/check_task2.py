#!/usr/bin/env python3
"""Task 2 gate：仓库齐备、wizard/utils_rs 可用、omnidreams gRPC 入口可用、alpamayo 就绪。"""
import subprocess, pathlib, sys, re

ROOT = pathlib.Path.home() / "simulation"
SECRET_PATTERNS = (
    re.compile(r"hf_[A-Za-z0-9]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"nvapi-[A-Za-z0-9_-]{20,}"),
)

def bash(cmd: str, timeout=900):
    return subprocess.run(["bash", "-c", f"source {ROOT}/scripts/env.sh; {cmd}"],
                          capture_output=True, text=True, timeout=timeout)

def main():
    for repo in ("alpasim", "alpamayo", "flashdreams"):
        d = ROOT / "repos" / repo
        assert d.is_dir() and (d / ".git").exists(), f"仓库缺失: {repo}"

    # alpasim：wizard 可运行
    r = bash(f"cd {ROOT}/repos/alpasim && uv run alpasim_wizard --help")
    assert r.returncode == 0, f"alpasim_wizard 不可用: {r.stderr[-800:]}"

    # alpasim：Rust 扩展已编译
    r = bash(f"cd {ROOT}/repos/alpasim && uv run python -c 'import utils_rs'")
    assert r.returncode == 0, f"utils_rs 未编译: {r.stderr[-800:]}"

    # flashdreams：omnidreams gRPC server 入口
    r = bash(f"cd {ROOT}/repos/flashdreams && "
             "uv run --package flashdreams-omnidreams python -m omnidreams.impl.grpc.server --help")
    assert r.returncode == 0, f"omnidreams.grpc.server 不可用: {r.stderr[-800:]}"

    # alpamayo：环境与冒烟脚本就位
    a = ROOT / "repos" / "alpamayo"
    assert (a / ".venv").is_dir(), "alpamayo .venv 未创建（uv sync）"
    assert (a / "src" / "alpamayo_r1" / "test_inference.py").is_file(), "test_inference.py 缺失"

    # 防密钥扫描
    for dname in ("scripts", "configs"):
        for f in (ROOT / dname).rglob("*"):
            if f.is_file() and f.suffix in (".py", ".sh", ".yaml", ".yml", ".json"):
                t = f.read_text(errors="ignore")
                for pat in SECRET_PATTERNS:
                    assert not pat.search(t), f"疑似凭据泄漏: {f}"

    print("[SUCCESS] Task 2 passed: repos cloned, wizard+utils_rs+omnidreams gRPC entry ready.")

if __name__ == "__main__":
    main()
