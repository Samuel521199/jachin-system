#!/usr/bin/env python3
"""Record a small STT entity evaluation set.

This script is experiment-only. It records WAV files and writes a JSONL
manifest for later ASR/entity-correction evaluation; it does not call or modify
the Jachin runtime.

Examples:
  python scripts/record_stt_entity_eval_wavs.py
  python scripts/record_stt_entity_eval_wavs.py --repeats 5
  python scripts/record_stt_entity_eval_wavs.py --list-devices
  python scripts/record_stt_entity_eval_wavs.py --device 1 --duration 3
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SAMPLE_RATE = 16000
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = ROOT / "data" / "eval_wav" / "stt_entity"
DEFAULT_MANIFEST = DEFAULT_OUT_DIR / "manifest.jsonl"


CORE_CASES: list[dict[str, Any]] = [
    {
        "id_prefix": "open_lark",
        "spoken": "打开 Lark",
        "expected": {"intent": "open_app", "entity": "Lark"},
        "notes": "Target common confusion: Lark -> LUCK / lock / 拉克.",
    },
    {
        "id_prefix": "open_lark_cn",
        "spoken": "帮我打开 Lark",
        "expected": {"intent": "open_app", "entity": "Lark"},
        "notes": "Chinese command with short English app name.",
    },
    {
        "id_prefix": "find_vivian",
        "spoken": "找到 Vivian",
        "expected": {"intent": "find_contact", "entity": "Vivian"},
        "notes": "Target common confusion: Vivian -> 威廉 / 里面 / 媳帘.",
    },
    {
        "id_prefix": "find_vivian_cn",
        "spoken": "帮我找一下 Vivian",
        "expected": {"intent": "find_contact", "entity": "Vivian"},
        "notes": "Chinese command with English contact name.",
    },
    {
        "id_prefix": "open_feishu",
        "spoken": "打开飞书",
        "expected": {"intent": "open_app", "entity": "飞书"},
        "notes": "Target common confusion: 飞书 -> 飞蕊.",
    },
    {
        "id_prefix": "find_feishu",
        "spoken": "找到飞书",
        "expected": {"intent": "find_app", "entity": "飞书"},
        "notes": "Target common confusion: 找到飞书 -> 遭到飞蕊.",
    },
    {
        "id_prefix": "send_lark_vivian",
        "spoken": "在 Lark 给 Vivian 发消息",
        "expected": {"intent": "send_message", "app": "Lark", "entity": "Vivian", "requires_confirmation": True},
        "notes": "High-risk action should be recognized and later confirmed.",
    },
    {
        "id_prefix": "send_lark_vivian_content",
        "spoken": "在 Lark 给 Vivian 发消息内容是今晚吃什么",
        "expected": {
            "intent": "send_message",
            "app": "Lark",
            "entity": "Vivian",
            "content": "今晚吃什么",
            "requires_confirmation": True,
        },
        "notes": "Full command with app, contact, and message content.",
    },
]


def _require_module(name: str, install_hint: str) -> Any:
    try:
        return __import__(name)
    except ImportError as exc:
        raise SystemExit(f"Missing dependency {name}. Install with: {install_hint}") from exc


def configure_console() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9_\-]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "sample"


def list_devices() -> int:
    sd = _require_module("sounddevice", "python -m pip install sounddevice")
    print(sd.query_devices())
    print(f"\nDefault input device: {sd.default.device[0]}")
    return 0


def next_sample_id(out_dir: Path, id_prefix: str, repeat_index: int) -> str:
    base = f"{slugify(id_prefix)}_{repeat_index:03d}"
    sample_id = base
    suffix = 2
    while (out_dir / f"{sample_id}.wav").exists():
        sample_id = f"{base}_{suffix}"
        suffix += 1
    return sample_id


def audio_stats(audio: Any) -> dict[str, float]:
    np = _require_module("numpy", "python -m pip install numpy")
    flat = np.asarray(audio, dtype=np.float32).reshape(-1)
    if flat.size == 0:
        return {"duration_sec": 0.0, "peak": 0.0, "rms": 0.0}
    peak = float(np.max(np.abs(flat)))
    rms = float(np.sqrt(np.mean(np.square(flat))))
    duration_sec = float(flat.size / SAMPLE_RATE)
    return {"duration_sec": round(duration_sec, 3), "peak": round(peak, 4), "rms": round(rms, 4)}


def record_ptt(device: int | None) -> Any:
    np = _require_module("numpy", "python -m pip install numpy")
    sd = _require_module("sounddevice", "python -m pip install sounddevice")
    chunks: list[Any] = []

    def callback(indata, _frames, _time_info, status) -> None:
        if status:
            print(f"[record] {status}", file=sys.stderr)
        chunks.append(indata.copy())

    print("[ptt] Press Enter to start recording")
    input()
    print("[ptt] Recording. Press Enter to stop")
    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        device=device,
        blocksize=1024,
        callback=callback,
    ):
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass
    if not chunks:
        return np.zeros((0, 1), dtype=np.float32)
    return np.concatenate(chunks, axis=0)


def record_fixed(duration_sec: float, device: int | None) -> Any:
    sd = _require_module("sounddevice", "python -m pip install sounddevice")
    duration_sec = max(0.5, min(float(duration_sec), 120.0))
    frames = int(duration_sec * SAMPLE_RATE)
    print(f"[record] Recording {duration_sec:.1f}s @ {SAMPLE_RATE} Hz")
    audio = sd.rec(frames, samplerate=SAMPLE_RATE, channels=1, dtype="float32", device=device)
    sd.wait()
    return audio


def write_wav(path: Path, audio: Any) -> None:
    sf = _require_module("soundfile", "python -m pip install soundfile")
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), audio, SAMPLE_RATE, subtype="PCM_16", format="WAV")


def append_manifest(manifest: Path, record: dict[str, Any]) -> None:
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def build_cases(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.spoken:
        expected: dict[str, Any] = {}
        if args.intent:
            expected["intent"] = args.intent
        if args.entity:
            expected["entity"] = args.entity
        if args.app:
            expected["app"] = args.app
        if args.requires_confirmation:
            expected["requires_confirmation"] = True
        return [
            {
                "id_prefix": args.id_prefix or slugify(args.entity or args.intent or "custom"),
                "spoken": args.spoken,
                "expected": expected,
                "notes": args.notes or "Custom sample.",
            }
        ]
    return CORE_CASES


def confirm_or_skip(prompt: str, *, yes: bool) -> bool:
    if yes:
        return True
    answer = input(f"{prompt} [Enter=record, s=skip, q=quit] ").strip().lower()
    if answer == "q":
        raise KeyboardInterrupt
    return answer != "s"


def main() -> int:
    configure_console()
    parser = argparse.ArgumentParser(description="Record WAV samples for STT entity evaluation")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--repeats", type=int, default=1, help="How many times to record each case")
    parser.add_argument("--duration", type=float, default=0.0, help="Fixed recording seconds. Default uses press-to-talk")
    parser.add_argument("--device", type=int, default=None)
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--yes", action="store_true", help="Do not ask before each sample")
    parser.add_argument("--spoken", help="Record one custom spoken phrase instead of the built-in set")
    parser.add_argument("--id-prefix", default="")
    parser.add_argument("--intent", default="")
    parser.add_argument("--entity", default="")
    parser.add_argument("--app", default="")
    parser.add_argument("--requires-confirmation", action="store_true")
    parser.add_argument("--notes", default="")
    args = parser.parse_args()

    if args.list_devices:
        return list_devices()

    args.repeats = max(1, int(args.repeats))
    cases = build_cases(args)
    total = len(cases) * args.repeats

    print(f"[out] {args.out_dir}")
    print(f"[manifest] {args.manifest}")
    print(f"[plan] {len(cases)} case(s) x {args.repeats} repeat(s) = {total} WAV file(s)")
    print("[tip] Speak naturally. Keep a short pause before and after the phrase.")

    count = 0
    try:
        for case in cases:
            for repeat in range(1, args.repeats + 1):
                count += 1
                sample_id = next_sample_id(args.out_dir, case["id_prefix"], repeat)
                wav_path = args.out_dir / f"{sample_id}.wav"
                print()
                print(f"[{count}/{total}] {sample_id}")
                print(f"Say: {case['spoken']}")
                print(f"Expected: {json.dumps(case['expected'], ensure_ascii=False)}")
                if not confirm_or_skip("Ready?", yes=args.yes):
                    print("[skip]")
                    continue

                started_at = datetime.now(timezone.utc).isoformat()
                t0 = time.perf_counter()
                audio = record_fixed(args.duration, args.device) if args.duration > 0 else record_ptt(args.device)
                elapsed_sec = time.perf_counter() - t0
                stats = audio_stats(audio)
                if stats["duration_sec"] <= 0:
                    print("[warn] empty audio, not saved")
                    continue
                write_wav(wav_path, audio)

                record = {
                    "id": sample_id,
                    "wav": str(wav_path.relative_to(ROOT)).replace("\\", "/") if wav_path.is_relative_to(ROOT) else str(wav_path),
                    "spoken": case["spoken"],
                    "expected": case["expected"],
                    "notes": case.get("notes", ""),
                    "sample_rate": SAMPLE_RATE,
                    "duration_sec": stats["duration_sec"],
                    "peak": stats["peak"],
                    "rms": stats["rms"],
                    "record_elapsed_sec": round(elapsed_sec, 3),
                    "device": args.device,
                    "created_at": started_at,
                }
                append_manifest(args.manifest, record)
                print(f"[saved] {wav_path}")
                print(f"[audio] duration={stats['duration_sec']}s peak={stats['peak']} rms={stats['rms']}")
    except KeyboardInterrupt:
        print("\n[stop] recording interrupted")
        return 130

    print()
    print(f"[done] manifest: {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
