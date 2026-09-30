#!/usr/bin/env python3
"""Task 0 gate：工作区初始化、软链、env.sh、密钥泄漏扫描。"""
import os, re, subprocess, sys, pathlib

ROOT = pathlib.Path.home() / "simulation"
# 真实凭据形态（裸前缀如 hf_transfer / hf_hub_download 属正常词汇，不视为泄漏）
SECRET_PATTERNS = (
    re.compile(r"hf_[A-Za-z0-9]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"nvapi-[A-Za-z0-9_-]{20,}"),
)

def run_bash(cmd: str):
    return subprocess.run(["bash", "-c", f"source {ROOT}/scripts/env.sh >/dev/null 2>&1; {cmd}"],
                          capture_output=True, text=True)

def main():
    assert (ROOT / ".git").is_dir(), "缺少 .git：未执行 git init"
    assert (ROOT / ".gitignore").is_file(), "缺少 .gitignore"
    assert (ROOT / "scripts" / "env.sh").is_file(), "缺少 scripts/env.sh"

    for name in ("repos", "weights", "assets", "configs", "artifacts", "scripts", "docs"):
        assert (ROOT / name).exists(), f"缺少目录/链接: {name}"
    for link, sub in (("weights", "weights"), ("assets", "assets"), ("venvroot", "venvs")):
        p = ROOT / link
        assert p.is_symlink(), f"{link} 不是软链接"
        assert os.readlink(str(p)).endswith(sub), f"{link} 软链目标异常: {os.readlink(str(p))}"

    # env.sh 可被 source 且关键变量有值
    for var in ("REPO_ROOT", "STORAGE_ROOT", "HF_HOME", "TORCH_HOME", "CARGO_HOME"):
        r = run_bash(f"echo -n ${var}")
        assert r.stdout.strip(), f"env.sh 未导出 {var}"

    # 密钥泄漏扫描：scripts/configs/docs 中禁止出现真实凭据
    for base in ("scripts", "configs", "docs"):
        d = ROOT / base
        if not d.exists():
            continue
        for f in d.rglob("*"):
            if f.is_file() and f.suffix in (".py", ".sh", ".md", ".yaml", ".yml", ".json"):
                txt = f.read_text(errors="ignore")
                for pat in SECRET_PATTERNS:
                    m = pat.search(txt)
                    assert not m, f"疑似凭据泄漏: {f} 含 {m.group(0)[:8]}..."

    # git 至少有一条提交
    log = subprocess.run(["git", "-C", str(ROOT), "log", "--oneline"],
                         capture_output=True, text=True)
    assert log.returncode == 0 and log.stdout.strip(), "git 仓库无提交记录"
    print("[SUCCESS] Task 0 passed: git repo, skeleton, symlinks, env.sh verified, no secret leakage.")

if __name__ == "__main__":
    main()
