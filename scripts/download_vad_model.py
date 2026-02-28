#!/usr/bin/env python3
"""
Silero VAD ONNX 模型预下载脚本

将 silero_vad.onnx 下载到 data/vad/ 目录，供桌面端 VAD 截断使用。
可通过环境变量 JACHIN_VAD_DEBUG_PATH 或 VAD_MODELS_DIR 指定目标目录的父目录（其下需有 vad 子目录）。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

if sys.platform == "win32":
    try:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass


def get_vad_dir() -> Path:
    """目标目录：data/vad 或 环境变量指定的 vad 子目录"""
    env = os.getenv("VAD_MODELS_DIR") or os.getenv("JACHIN_VAD_DEBUG_PATH")
    if env:
        return Path(env).resolve() / "vad"
    return _PROJECT_ROOT / "data" / "vad"


def download_via_hf() -> Path | None:
    """从 HuggingFace 下载（若仓库存在且包含兼容的 onnx）"""
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        return None
    for repo_id, filename in [
        ("AXERA-TECH/SileroVAD", "silero_vad.onnx"),
        ("snakers4/silero-vad-models", "silero_vad.onnx"),
    ]:
        try:
            print(f"[INFO] 尝试从 HuggingFace {repo_id} 下载...")
            path = hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                local_dir=str(_PROJECT_ROOT / "data" / "vad" / "_hf_cache"),
                local_dir_use_symlinks=False,
            )
            return Path(path)
        except Exception as e:
            print(f"[WARN] {repo_id} 失败: {e}")
    return None


def download_via_torch_hub() -> Path | None:
    """通过 silero-vad / torch.hub 触发下载，并从缓存复制 onnx 文件"""
    try:
        import torch
    except ImportError:
        print("[WARN] 未安装 torch，跳过 torch.hub 方式")
        return None
    # 使用 silero-vad 包会下载到包内或 torch hub 缓存
    try:
        from silero_vad import load_silero_vad
        print("[INFO] 使用 silero-vad 包下载 ONNX 模型...")
        model, utils = load_silero_vad(onnx=True, opset_version=16)
        # 包内可能带文件路径；否则在 torch hub 缓存
        hub_dir = Path(torch.hub.get_dir())
        for pattern in ["**/silero_vad*.onnx", "**/silero*vad*.onnx"]:
            for f in hub_dir.glob(pattern):
                if f.is_file() and f.stat().st_size > 100_000:
                    return f
        # 某些版本在 site-packages/silero_vad/files/
        import silero_vad
        pkg = Path(silero_vad.__file__).resolve().parent
        for name in ["silero_vad.onnx", "silero_vad_16khz.onnx"]:
            candidate = pkg / "files" / name
            if candidate.exists():
                return candidate
    except ImportError:
        print("[WARN] 未安装 silero-vad，请运行: pip install silero-vad")
        return None
    except Exception as e:
        print(f"[WARN] silero-vad 加载失败: {e}")
    return None


def download_via_urllib() -> Path | None:
    """直接 HTTP 下载已知可用的镜像或发布地址"""
    try:
        import urllib.request
    except ImportError:
        return None
    # 官方仓库中 ONNX 实际路径：src/silero_vad/data/silero_vad.onnx
    urls = [
        "https://raw.githubusercontent.com/snakers4/silero-vad/master/src/silero_vad/data/silero_vad.onnx",
        "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx",
    ]
    dest_dir = _PROJECT_ROOT / "data" / "vad"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "silero_vad.onnx"
    for url in urls:
        try:
            print(f"[INFO] 尝试直接下载: {url[:60]}...")
            urllib.request.urlretrieve(url, dest)
            if dest.exists() and dest.stat().st_size > 100_000:
                return dest
        except Exception as e:
            print(f"[WARN] 下载失败: {e}")
    return None


def main() -> int:
    vad_dir = get_vad_dir()
    vad_dir.mkdir(parents=True, exist_ok=True)
    dest = vad_dir / "silero_vad.onnx"

    if dest.exists() and dest.stat().st_size > 100_000:
        print(f"[OK] 模型已存在: {dest}")
        return 0

    source: Path | None = None
    source = download_via_hf()
    if source is None:
        source = download_via_torch_hub()
    if source is None:
        source = download_via_urllib()

    if source is None:
        print("[ERROR] 未能下载 silero_vad.onnx。请手动从以下任一方式获取并放到 data/vad/silero_vad.onnx：")
        print("  1. pip install silero-vad 后，从 Python 缓存或 site-packages/silero_vad/files/ 复制")
        print("  2. HuggingFace: snakers4/silero-vad-models 或 AXERA-TECH/SileroVAD")
        return 1

    if source.resolve() != dest.resolve():
        shutil.copy(source, dest)
        print(f"[OK] 已保存: {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
