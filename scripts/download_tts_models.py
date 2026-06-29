#!/usr/bin/env python3
"""
TTS 模型预下载脚本（MOSS ONNX）- 供 Tier 2 与 Tier 3 使用

下载并校验：
- MOSS-TTS-Nano-100M-ONNX
- MOSS-Audio-Tokenizer-Nano-ONNX
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def get_tts_dir() -> Path:
    env_dir = os.getenv("TTS_MODELS_DIR", "").strip()
    if env_dir:
        return Path(env_dir)
    return _PROJECT_ROOT / "data" / "models" / "voice" / "tts"


def ensure_moss_models(target_dir: Path) -> bool:
    try:
        from modelscope.hub.snapshot_download import snapshot_download
    except Exception:
        print("[错误] 需要 modelscope，请运行: pip install modelscope")
        return False

    target_dir.mkdir(parents=True, exist_ok=True)
    ok = True

    repo_map = {
        "openmoss/MOSS-TTS-Nano-100M-ONNX": target_dir / "MOSS-TTS-Nano-100M-ONNX",
        "openmoss/MOSS-Audio-Tokenizer-Nano-ONNX": target_dir / "MOSS-Audio-Tokenizer-Nano-ONNX",
    }

    for repo_id, dst in repo_map.items():
        if dst.exists():
            print(f"[SKIP] 已存在: {dst}")
            continue
        try:
            print(f"[INFO] 正在下载: {repo_id}")
            snapshot_download(repo_id=repo_id, local_dir=str(dst))
            print(f"[OK] 下载完成: {dst}")
        except Exception as e:
            ok = False
            print(f"[WARN] 下载失败 {repo_id}: {e}")

    manifest = target_dir / "MOSS-TTS-Nano-100M-ONNX" / "browser_poc_manifest.json"
    codec_dir = target_dir / "MOSS-Audio-Tokenizer-Nano-ONNX"
    if not manifest.exists() or not codec_dir.exists():
        ok = False
        print("[WARN] 模型校验未通过：缺少 manifest 或 tokenizer 目录")

    return ok


def main() -> int:
    print("=" * 60)
    print("  Jachin TTS (MOSS ONNX) 模型预下载")
    print("=" * 60)
    target_dir = get_tts_dir()
    print(f"\n目标目录: {target_dir}\n")

    if ensure_moss_models(target_dir):
        print("\n" + "=" * 60)
        print("[成功] MOSS ONNX 模型已就绪。")
        print("=" * 60)
        return 0

    print("\n" + "=" * 60)
    print("[提示] 自动下载未完成，可手动下载并放入:")
    print(f"  {target_dir}")
    print("  所需目录: MOSS-TTS-Nano-100M-ONNX, MOSS-Audio-Tokenizer-Nano-ONNX")
    print("=" * 60)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
