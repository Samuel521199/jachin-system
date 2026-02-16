#!/usr/bin/env python3
"""
TTS 模型预下载脚本 - 供 Tier 2 与 Tier 3 使用

将 kokoro-v0_19.onnx、voices.json、zm.bin 下载到 data/tts/ 目录。
Tier 3 桌面端首次使用时会从 Tier 2 拉取，故 Tier 2 需先准备好模型。

支持来源：
- HuggingFace: onnx-community/Kokoro-82M-ONNX, NeuML/kokoro-base-onnx
- 环境变量 TTS_MODELS_DIR 指定目标目录
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Windows 控制台 UTF-8
if sys.platform == "win32":
    try:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass


def get_tts_dir() -> Path:
    """获取 TTS 模型目录"""
    env_dir = os.getenv("TTS_MODELS_DIR")
    if env_dir:
        return Path(env_dir)
    return _PROJECT_ROOT / "data" / "tts"


def download_from_huggingface(target_dir: Path) -> bool:
    """从 HuggingFace 下载模型"""
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print("[错误] 需要 huggingface_hub，请运行: pip install huggingface_hub")
        return False

    import shutil
    target_dir.mkdir(parents=True, exist_ok=True)
    downloaded = False

    # 1. model.onnx -> kokoro-v0_19.onnx
    try:
        print("[INFO] 正在下载 Kokoro ONNX 模型 (~326MB)...")
        model_path = hf_hub_download(
            repo_id="onnx-community/Kokoro-82M-ONNX",
            filename="onnx/model.onnx",
            local_dir=str(target_dir / "_hf_cache"),
            local_dir_use_symlinks=False,
        )
        dest = target_dir / "kokoro-v0_19.onnx"
        shutil.copy(model_path, dest)
        print(f"[OK] 模型已保存: {dest}")
        downloaded = True
    except Exception as e:
        print(f"[WARN] 模型下载失败: {e}")

    # 2. voices.json（若 NeuML 有）
    voices_dest = target_dir / "voices.json"
    if not voices_dest.exists():
        try:
            hf_hub_download(
                repo_id="NeuML/kokoro-base-onnx",
                filename="voices.json",
                local_dir=str(target_dir),
                local_dir_use_symlinks=False,
            )
            print("[OK] voices.json 已保存")
            downloaded = True
        except Exception as e:
            print(f"[WARN] voices.json 下载失败: {e}")

    # 3. zm.bin（中英混合用，以 af_sarah 为 fallback）
    zm_bin = target_dir / "zm.bin"
    if not zm_bin.exists():
        try:
            af_path = hf_hub_download(
                repo_id="onnx-community/Kokoro-82M-ONNX",
                filename="voices/af_sarah.bin",
                local_dir=str(target_dir / "_hf_voices"),
                local_dir_use_symlinks=False,
            )
            shutil.copy(af_path, zm_bin)
            print("[OK] zm.bin 已创建（来自 af_sarah）")
            downloaded = True
        except Exception as e:
            print(f"[WARN] zm.bin 未创建: {e}")

    # 4. config.json（vocab 等）
    config_dest = target_dir / "config.json"
    if not config_dest.exists():
        try:
            hf_hub_download(
                repo_id="onnx-community/Kokoro-82M-ONNX",
                filename="config.json",
                local_dir=str(target_dir),
                local_dir_use_symlinks=False,
            )
            print("[OK] config.json 已保存")
            downloaded = True
        except Exception as e:
            print(f"[WARN] config.json 下载失败: {e}")

    # 清理临时缓存
    for cache in [target_dir / "_hf_cache", target_dir / "_hf_voices"]:
        if cache.exists():
            try:
                shutil.rmtree(cache)
            except Exception:
                pass

    return downloaded


def main() -> int:
    print("=" * 60)
    print("  Jachin TTS 模型预下载")
    print("=" * 60)

    target_dir = get_tts_dir()
    print(f"\n目标目录: {target_dir}\n")

    if download_from_huggingface(target_dir):
        print("\n" + "=" * 60)
        print("[成功] TTS 模型已就绪，Tier 2 可提供模型下载服务。")
        print("=" * 60)
        return 0
    else:
        print("\n" + "=" * 60)
        print("[提示] 自动下载未完成，可手动下载并放入:")
        print(f"  {target_dir}")
        print("  所需文件: kokoro-v0_19.onnx, voices.json, zm.bin")
        print("  来源: https://huggingface.co/onnx-community/Kokoro-82M-ONNX")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(main())
