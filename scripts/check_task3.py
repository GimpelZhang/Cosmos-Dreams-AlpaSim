#!/usr/bin/env python3
"""Task 3 gate：渲染权重、R1 权重（safetensors 完整性）、VaVAM 资产、NuRec 场景齐备。"""
import json, subprocess, pathlib, sys

ROOT = pathlib.Path.home() / "simulation"

def bash(cmd, timeout=1800):
    return subprocess.run(["bash", "-c", f"source {ROOT}/scripts/env.sh; {cmd}"],
                          capture_output=True, text=True, timeout=timeout)

def main():
    r = bash("sim_storage_ready && echo READY")
    assert "READY" in r.stdout, "数据盘未就绪（sim_storage_ready 失败），Task 3 禁止在根分区执行"

    # --- OmniDreams 渲染权重：精确字节数 + ZIP(.pt) 魔数 ---
    od = ROOT / "weights" / "omnidreams" / "single_view" / "2b_res720p_30fps_i2v_hdmap_distilled.pt"
    assert od.is_file(), f"OmniDreams 权重缺失: {od}"
    assert od.stat().st_size == 4118900683, f"OmniDreams 权重大小异常: {od.stat().st_size}"
    assert od.read_bytes()[:2] == b"PK", ".pt 不是合法 ZIP 容器，下载损坏"

    # --- Alpamayo-R1：5 分片 + index + safetensors 可打开 ---
    r1 = ROOT / "weights" / "alpamayo-r1"
    shards = sorted(r1.glob("model-*.safetensors"))
    assert len(shards) == 5, f"R1 应有 5 个分片，实际 {len(shards)}"
    total = sum(s.stat().st_size for s in shards)
    assert total >= 21.5e9, f"R1 总分片仅 {total/1e9:.1f}GB，下载不完整"
    idx = json.loads((r1 / "model.safetensors.index.json").read_text())
    assert idx["metadata"]["total_size"] > 21e9, "R1 index total_size 异常"
    code = ("from safetensors import safe_open; "
            "import glob,sys; [safe_open(f,'pt') for f in sorted(glob.glob(sys.argv[1]))]; "
            "print('shards readable')")
    r = bash(f"cd {ROOT}/repos/alpasim && uv run python -c \"{code}\" '{r1}/model-*.safetensors'")
    assert r.returncode == 0 and "readable" in r.stdout, f"safetensors 校验失败: {r.stderr[-500:]}"

    # --- VaVAM-B 资产 ---
    vv = ROOT / "repos" / "alpasim" / "data" / "drivers" / "vavam"
    pt = list(vv.glob("VAM_width_1024_pretrained_*.pt"))
    assert pt and pt[0].stat().st_size > 1.5e9, "VaVAM 策略权重缺失或过小（软链是否生效？）"
    jits = list(vv.glob("*.jit"))
    assert len(jits) >= 2, f"VQ tokenizer/detokenizer 缺失: {len(jits)}"

    # --- NuRec 场景 ---
    sc = ROOT / "assets" / "nurec" / "sample_set/26.01_release" / \
         "02eadd92-02f1-46d8-86fe-a9e338fed0b6" / "02eadd92-02f1-46d8-86fe-a9e338fed0b6.usdz"
    assert sc.is_file() and sc.stat().st_size > 1.7e9, f"NuRec 场景缺失/不完整: {sc}"
    assert sc.read_bytes()[:2] == b"PK", ".usdz 不是合法 ZIP，下载损坏"

    print("[SUCCESS] Task 3 passed: OmniDreams 3.84GB, Alpamayo-R1 22GB(5 shards valid), VaVAM, NuRec scene ready.")

if __name__ == "__main__":
    main()
