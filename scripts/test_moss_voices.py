#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parent.parent
    default_model_dir = project_root / "data" / "models" / "voice" / "tts"
    default_out_dir = project_root / "data" / "voice_audition_out"
    parser = argparse.ArgumentParser(
        description="试听 MOSS ONNX 内置音色（支持交互与批量循环）"
    )
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=default_model_dir,
        help="MOSS 模型目录（默认 data/models/voice/tts）",
    )
    parser.add_argument(
        "--text",
        default="你好，我是 Jachin，现在是音色试听。",
        help="试听文案",
    )
    parser.add_argument(
        "--voice",
        action="append",
        default=[],
        help="指定音色，可重复传参；不传则进入交互选择",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=default_out_dir,
        help="输出 wav 目录",
    )
    parser.add_argument(
        "--no-play",
        action="store_true",
        help="只生成 wav，不自动播放",
    )
    return parser.parse_args()


def play_wav(path: Path) -> None:
    try:
        import winsound

        winsound.PlaySound(str(path), winsound.SND_FILENAME)
    except Exception as exc:
        print(f"[WARN] 播放失败: {exc}")


def select_voices_interactive(all_voices: list[str]) -> list[str]:
    print("\n可用音色：")
    for i, v in enumerate(all_voices, start=1):
        print(f"  {i:>2}. {v}")
    print("\n输入编号试听（如 1 或 1,3,5），输入 all 全部，输入 q 退出。")

    while True:
        raw = input("选择> ").strip().lower()
        if raw in {"q", "quit", "exit"}:
            return []
        if raw == "all":
            return all_voices
        if not raw:
            continue
        try:
            picks = []
            for part in raw.split(","):
                idx = int(part.strip())
                if idx < 1 or idx > len(all_voices):
                    raise ValueError(f"编号超出范围: {idx}")
                picks.append(all_voices[idx - 1])
            # 去重并保持顺序
            seen = set()
            ordered = []
            for p in picks:
                if p not in seen:
                    seen.add(p)
                    ordered.append(p)
            return ordered
        except Exception as exc:
            print(f"[WARN] 输入无效: {exc}")


def main() -> int:
    args = parse_args()
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    args.out_dir.mkdir(parents=True, exist_ok=True)

    try:
        from voice_server.services.tts_service import TtsService
    except Exception as exc:
        print(f"[ERROR] 无法导入 TtsService: {exc}")
        return 1

    service = TtsService(args.model_dir)
    if not service.ready:
        print(f"[ERROR] 模型目录不完整: {args.model_dir}")
        return 1
    if not service._load_engine():
        print(f"[ERROR] TTS 引擎加载失败: {service._load_error}")
        return 1

    voices = service.list_voices()
    if not voices:
        print("[ERROR] 未获取到可用音色")
        return 1

    chosen = [v.strip() for v in args.voice if v.strip()]
    if not chosen:
        chosen = select_voices_interactive(voices)
        if not chosen:
            print("已退出。")
            return 0

    unknown = [v for v in chosen if v not in voices]
    if unknown:
        print(f"[ERROR] 这些音色不存在: {unknown}")
        print(f"[INFO] 可用音色: {voices}")
        return 1

    print(f"\n[INFO] 开始试听 {len(chosen)} 个音色")
    print(f"[INFO] 文案: {args.text}\n")

    for voice in chosen:
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        wav_path = args.out_dir / f"{ts}_{voice}.wav"
        print(f"[VOICE] {voice}")
        try:
            result = service.synthesize(args.text, voice=voice, session_id=f"audition-{voice}")
            wav_path.write_bytes(result.wav_bytes)
            print(f"  [OK] 已生成: {wav_path}")
            if not args.no_play:
                play_wav(wav_path)
        except Exception as exc:
            print(f"  [FAIL] {exc}")

    print("\n完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

