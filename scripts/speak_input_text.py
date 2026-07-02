#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Interactive TTS tester (companion-mode matched parameters).",
    )
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=None,
        help="Override model dir. Default follows companion config.",
    )
    parser.add_argument(
        "--voice",
        default=None,
        help="Override voice id. Default follows companion config.",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=None,
        help="Override TTS speed. Default follows companion config.",
    )
    parser.add_argument(
        "--cpu-threads",
        type=int,
        default=None,
        help="Override ONNX Runtime thread count (maps to JACHIN_VOICE_TTS_THREADS).",
    )
    parser.add_argument(
        "--sample-mode",
        choices=("greedy", "fixed", "full"),
        default=None,
        help="Override sample mode (maps to JACHIN_VOICE_TTS_SAMPLE_MODE).",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "voice_audition_out",
        help="Directory to save generated wav files.",
    )
    parser.add_argument(
        "--once",
        default="",
        help="Run one-shot synthesis with this text and exit.",
    )
    return parser.parse_args()


def play_wav(path: Path) -> None:
    if os.name == "nt":
        import winsound

        winsound.PlaySound(str(path), winsound.SND_FILENAME)
        return
    print(f"[INFO] Audio saved: {path}")


def main() -> None:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    from voice_server.config import load_config  # pylint: disable=import-outside-toplevel
    os.environ["JACHIN_VOICE_TTS_VOICE_CLONE_MAX_TEXT_TOKENS"] = "75"
    cfg = load_config()

    # 默认完全跟随陪伴态；只有显式传参时才覆盖。
    model_dir = Path(args.model_dir) if args.model_dir else Path(cfg.tts_dir)
    voice = (args.voice or cfg.tts_voice or "zm_053").strip() or "zm_053"
    speed = float(args.speed) if args.speed is not None else float(cfg.tts_speed)
    if args.cpu_threads is not None:
        os.environ["JACHIN_VOICE_TTS_THREADS"] = str(max(1, args.cpu_threads))
    if args.sample_mode:
        os.environ["JACHIN_VOICE_TTS_SAMPLE_MODE"] = args.sample_mode

    from voice_server.services.tts_service import TtsService  # pylint: disable=import-outside-toplevel

    tts = TtsService(model_dir, default_voice=voice, default_speed=speed)
    if not tts.ready:
        raise RuntimeError(f"Model dir not ready: {model_dir}")
    if not tts._load_engine():
        raise RuntimeError(f"TTS engine load failed: {tts._load_error}")

    print("Companion-matched Kokoro interactive mode is ready.")
    print(f"model_dir={model_dir}")
    print(
        f"voice={voice} speed={speed} "
        f"threads_env={os.getenv('JACHIN_VOICE_TTS_THREADS', '') or '(companion default)'}"
    )
    print("Type text and press Enter. Type 'q' to quit.")

    while True:
        text = (args.once or input("\nInput text> ")).strip()
        args.once = ""
        if text.lower() in {"q", "quit", "exit"}:
            print("Bye.")
            break
        if not text:
            continue

        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        output_path = args.out_dir / f"tts_{ts}.wav"
        session_id = f"speak-input-{ts}"

        result = tts.synthesize(
            text=text,
            voice=voice,
            session_id=session_id,
        )
        output_path.write_bytes(result.wav_bytes)
        print(
            f"[OK] Generated: {output_path} "
            f"(synth_ms={result.synth_ms}, duration_ms={result.duration_ms}, quality={result.quality_status})"
        )
        play_wav(output_path)

        if text and not sys.stdin.isatty():
            break


if __name__ == "__main__":
    main()

