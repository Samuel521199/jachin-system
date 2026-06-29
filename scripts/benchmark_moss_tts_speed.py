#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import statistics
import subprocess
import sys
import time
import wave
from pathlib import Path


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="基准测试：MOSS TTS 合成耗时与本机朗读耗时")
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=project_root / "data" / "models" / "voice" / "tts",
        help="MOSS 模型目录（默认 data/models/voice/tts）",
    )
    parser.add_argument("--voice", default="Junhao", help="音色（默认 Junhao）")
    parser.add_argument("--text", action="append", default=[], help="待测文本（可重复传参）")
    parser.add_argument("--text-file", type=Path, default=None, help="从文件读取多行文本（每行一条）")
    parser.add_argument("--runs", type=int, default=3, help="每条文本测试次数（默认 3）")
    parser.add_argument("--warmup", type=int, default=1, help="预热次数（默认 1）")
    parser.add_argument(
        "--playback",
        choices=("none", "winsound", "powershell"),
        default="none",
        help="是否测本机播放耗时（默认 none）",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=project_root / "data" / "tts_benchmark_out",
        help="保存样本 wav 目录",
    )
    return parser.parse_args()


def load_texts(args: argparse.Namespace) -> list[str]:
    texts = [t.strip() for t in args.text if t.strip()]
    if args.text_file:
        if not args.text_file.exists():
            raise FileNotFoundError(f"text-file not found: {args.text_file}")
        lines = [ln.strip() for ln in args.text_file.read_text(encoding="utf-8").splitlines()]
        texts.extend([ln for ln in lines if ln])
    if not texts:
        texts = [
            "你好，我是 Jachin，现在开始测试 MOSS 语音速度。",
            "请帮我总结今天的任务优先级，并用三句话给出执行建议。",
            "这是一段稍长的文本，用来观察合成时延和最终朗读时长之间的比例关系。",
        ]
    return texts


def wav_duration_ms(wav_bytes: bytes) -> float:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        frames = wf.getnframes()
        sr = max(1, wf.getframerate())
        return frames * 1000.0 / sr


def quality_limit_ms(tts: object, text: str) -> float:
    if hasattr(tts, "_max_allowed_duration_ms"):
        return float(tts._max_allowed_duration_ms(text))  # pylint: disable=protected-access
    speakable_len = sum(1 for ch in text if not ch.isspace())
    expected_ms = max(1600.0, speakable_len * 180.0)
    return min(12000.0, expected_ms * 2.8)


def frame_budget(tts: object, text: str) -> int | str:
    if hasattr(tts, "_resolve_max_new_frames"):
        return int(tts._resolve_max_new_frames(text))  # pylint: disable=protected-access
    return "?"


def play_wav_and_measure(path: Path, mode: str) -> float:
    started = time.perf_counter()
    if mode == "winsound":
        import winsound

        winsound.PlaySound(None, winsound.SND_PURGE)
        winsound.PlaySound(str(path), winsound.SND_FILENAME)
    elif mode == "powershell":
        safe = str(path).replace("'", "''")
        script = f"$p = New-Object System.Media.SoundPlayer '{safe}'; $p.PlaySync()"
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        return 0.0
    return (time.perf_counter() - started) * 1000.0


def fmt_ms(values: list[float]) -> str:
    if not values:
        return "-"
    return (
        f"min={min(values):.1f}  p50={statistics.median(values):.1f}  "
        f"mean={statistics.fmean(values):.1f}  max={max(values):.1f}"
    )


def main() -> int:
    args = parse_args()
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from voice_server.services.tts_service import TtsService  # pylint: disable=import-outside-toplevel

    texts = load_texts(args)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    tts = TtsService(args.model_dir)
    if not tts.ready:
        print(f"[ERROR] 模型目录未就绪: {args.model_dir}")
        return 1
    if not tts._load_engine():
        print(f"[ERROR] TTS 引擎加载失败: {tts._load_error}")
        return 1

    print("=== MOSS TTS 速度基准 ===")
    print(f"model_dir   : {args.model_dir}")
    print(f"voice       : {args.voice}")
    print(f"sample_mode : {tts.sample_mode}")
    print(f"do_sample   : {tts.do_sample}")
    print(f"speed       : {tts.default_speed}")
    print(f"text_count  : {len(texts)}")
    print(f"runs        : {args.runs}")
    print(f"warmup      : {args.warmup}")
    print(f"playback    : {args.playback}")
    print()

    for i in range(max(0, args.warmup)):
        _ = tts.synthesize("预热测试。", voice=args.voice, session_id=f"warmup-{i}")

    all_synth_ms: list[float] = []
    all_audio_ms: list[float] = []
    all_rtf: list[float] = []
    all_play_ms: list[float] = []
    abnormal_count = 0

    for idx, text in enumerate(texts, start=1):
        synth_ms_list: list[float] = []
        audio_ms_list: list[float] = []
        rtf_list: list[float] = []
        play_ms_list: list[float] = []
        limit_ms = quality_limit_ms(tts, text)
        frames = frame_budget(tts, text)

        print(f"[TEXT {idx}] {text}")
        print(f"  budget: frames={frames} quality_limit={limit_ms:.1f}ms")
        for run in range(1, args.runs + 1):
            sid = f"bench-{idx}-{run}"
            t0 = time.perf_counter()
            result = tts.synthesize(text=text, voice=args.voice, session_id=sid)
            synth_ms = (time.perf_counter() - t0) * 1000.0
            audio_ms = wav_duration_ms(result.wav_bytes)
            rtf = synth_ms / max(1e-6, audio_ms)
            quality = "ok" if audio_ms <= limit_ms else "OUTLIER"
            if quality != "ok":
                abnormal_count += 1

            wav_path = args.out_dir / f"text{idx}_run{run}_{args.voice}.wav"
            wav_path.write_bytes(result.wav_bytes)

            play_ms = 0.0
            if args.playback != "none":
                play_ms = play_wav_and_measure(wav_path, args.playback)

            synth_ms_list.append(synth_ms)
            audio_ms_list.append(audio_ms)
            rtf_list.append(rtf)
            if play_ms > 0:
                play_ms_list.append(play_ms)

            print(
                f"  run={run} synth={synth_ms:.1f}ms audio={audio_ms:.1f}ms "
                f"rtf={rtf:.3f} playback={play_ms:.1f}ms quality={quality}"
            )

        print(f"  synth(ms): {fmt_ms(synth_ms_list)}")
        print(f"  audio(ms): {fmt_ms(audio_ms_list)}")
        print(f"  rtf      : {fmt_ms(rtf_list)}")
        if play_ms_list:
            print(f"  play(ms) : {fmt_ms(play_ms_list)}")
        print()

        all_synth_ms.extend(synth_ms_list)
        all_audio_ms.extend(audio_ms_list)
        all_rtf.extend(rtf_list)
        all_play_ms.extend(play_ms_list)

    print("=== 汇总 ===")
    print(f"synth(ms): {fmt_ms(all_synth_ms)}")
    print(f"audio(ms): {fmt_ms(all_audio_ms)}")
    print(f"rtf      : {fmt_ms(all_rtf)}")
    if all_play_ms:
        print(f"play(ms) : {fmt_ms(all_play_ms)}")
    print(f"outliers : {abnormal_count}")
    print(f"wav_out  : {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
