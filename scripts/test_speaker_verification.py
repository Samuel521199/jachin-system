#!/usr/bin/env python3
"""
声纹识主能力测试（你 + 同事先后说话场景）

用途：
  - 麦克风录一段混合语音（你说 + 同事插话）
  - 同时输出：
      1) 原始整段 STT 文本
      2) 主人轨过滤后 STT 文本
  - 用于验证“同事的话是否被过滤掉”

前置：
  - JVS 已启动（默认 http://127.0.0.1:18982）
  - 已完成认主，存在 owner_voiceprint.json
  - pip 包：sounddevice soundfile numpy

示例：
  python scripts/test_speaker_verification.py
  python scripts/test_speaker_verification.py --rounds 5 --record 10 --save-dir data/sv_test_out
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


DEFAULT_BASE = os.getenv("JACHIN_VOICE_SERVER_URL", "http://127.0.0.1:18982").rstrip("/")
DEFAULT_PROFILE = Path.home() / ".jachin" / "voice" / "owner_voiceprint.json"
STT_SAMPLE_RATE = 16000


@dataclass
class RoundResult:
    raw_text: str
    owner_text: str
    owner_duration_ms: int
    total_duration_ms: int
    skipped_segments: list[dict[str, Any]]
    windows: list[dict[str, Any]]
    window_params: dict[str, Any]


def _http_json(url: str, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _build_multipart(
    *,
    wav_bytes: bytes,
    file_field_name: str = "audio",
    file_name: str = "capture.wav",
    extra_fields: dict[str, str] | None = None,
) -> tuple[bytes, str]:
    boundary = f"----jachin-sv-{int(time.time() * 1000)}"
    body = bytearray()
    if extra_fields:
        for k, v in extra_fields.items():
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode())
            body.extend(str(v).encode("utf-8"))
            body.extend(b"\r\n")
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        f'Content-Disposition: form-data; name="{file_field_name}"; filename="{file_name}"\r\n'.encode()
    )
    body.extend(b"Content-Type: audio/wav\r\n\r\n")
    body.extend(wav_bytes)
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    return bytes(body), boundary


def _http_post_multipart_json(url: str, wav_bytes: bytes, extra_fields: dict[str, str] | None = None) -> dict:
    payload, boundary = _build_multipart(wav_bytes=wav_bytes, extra_fields=extra_fields)
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=120.0) as resp:
        return json.loads(resp.read().decode("utf-8"))


def record_wav_bytes(duration_sec: float) -> bytes:
    import sounddevice as sd
    import soundfile as sf

    sec = max(1.0, min(float(duration_sec), 60.0))
    print(f"[录音] {sec:.1f}s，开始说话...")
    frames = int(sec * STT_SAMPLE_RATE)
    audio = sd.rec(frames, samplerate=STT_SAMPLE_RATE, channels=1, dtype="int16")
    sd.wait()
    buf = io.BytesIO()
    sf.write(buf, audio, STT_SAMPLE_RATE, subtype="PCM_16", format="WAV")
    return buf.getvalue()


def wav_duration_ms(wav_bytes: bytes) -> int:
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate() or STT_SAMPLE_RATE
            return int(frames * 1000 / rate)
    except Exception:
        return 0


def stt_transcribe(base: str, wav_bytes: bytes) -> str:
    data = _http_post_multipart_json(f"{base}/v1/stt/transcribe", wav_bytes)
    return (data.get("text") or "").strip()


def _resolve_window_params(profile: dict) -> dict[str, str]:
    wl = (profile.get("window_label") or {}) if isinstance(profile, dict) else {}
    return {
        "centroid": json.dumps(profile.get("centroid") or []),
        "win_step_ms": str(wl.get("win_step_ms", 250)),
        "win_len_ms": str(wl.get("win_len_ms", 900)),
        "win_threshold_high": str(wl.get("win_threshold_high", 0.38)),
        "win_threshold_low": str(wl.get("win_threshold_low", 0.25)),
        "min_owner_duration_ms": str(wl.get("min_owner_duration_ms", 300)),
        "debounce_count": str(wl.get("debounce_count", 1)),
    }


def label_windows(
    base: str,
    wav_bytes: bytes,
    profile: dict,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    extra = _resolve_window_params(profile)
    data = _http_post_multipart_json(f"{base}/v1/sv/label_windows", wav_bytes, extra_fields=extra)
    windows = data.get("windows") or []
    params = {
        "win_step_ms": int(extra["win_step_ms"]),
        "win_len_ms": int(extra["win_len_ms"]),
        "win_threshold_high": float(extra["win_threshold_high"]),
        "win_threshold_low": float(extra["win_threshold_low"]),
        "debounce_count": int(extra["debounce_count"]),
        "min_owner_duration_ms": int(extra["min_owner_duration_ms"]),
    }
    return windows, params


def filter_owner_track(
    base: str,
    wav_bytes: bytes,
    profile: dict,
) -> tuple[bytes | None, int, list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    wl = (profile.get("window_label") or {}) if isinstance(profile, dict) else {}
    extra = _resolve_window_params(profile)
    windows, params = label_windows(base, wav_bytes, profile)
    data = _http_post_multipart_json(f"{base}/v1/sv/filter_owner_track", wav_bytes, extra_fields=extra)
    b64 = (data.get("owner_wav_b64") or "").strip()
    owner_duration_ms = int(data.get("owner_duration_ms") or 0)
    skipped = data.get("skipped_segments") or []
    if not b64:
        return None, owner_duration_ms, skipped, windows, params
    import base64

    try:
        owner_wav = base64.b64decode(b64)
        return owner_wav, owner_duration_ms, skipped, windows, params
    except Exception:
        return None, owner_duration_ms, skipped, windows, params


def split_sentences(text: str) -> list[str]:
    t = (text or "").strip()
    if not t:
        return []
    parts = re.split(r"(?<=[。！？!?；;])\s*", t)
    return [p.strip() for p in parts if p.strip()]


def run_one_round(base: str, profile: dict, record_sec: float) -> RoundResult:
    wav = record_wav_bytes(record_sec)
    total_ms = wav_duration_ms(wav)
    raw_text = stt_transcribe(base, wav)
    owner_wav, owner_ms, skipped, windows, params = filter_owner_track(base, wav, profile)
    owner_text = stt_transcribe(base, owner_wav) if owner_wav else ""
    return RoundResult(
        raw_text=raw_text,
        owner_text=owner_text,
        owner_duration_ms=owner_ms,
        total_duration_ms=total_ms,
        skipped_segments=skipped,
        windows=windows,
        window_params=params,
    )


def save_round_audio(save_dir: Path, idx: int, raw_wav: bytes | None, owner_wav: bytes | None) -> None:
    save_dir.mkdir(parents=True, exist_ok=True)
    if raw_wav:
        (save_dir / f"round_{idx:02d}_raw.wav").write_bytes(raw_wav)
    if owner_wav:
        (save_dir / f"round_{idx:02d}_owner.wav").write_bytes(owner_wav)


def load_profile(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"owner profile 不存在: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not data.get("centroid"):
        raise ValueError("owner profile 缺少 centroid")
    return data


def check_health(base: str) -> None:
    h = _http_json(f"{base}/health")
    if not h.get("ok"):
        raise RuntimeError(f"JVS health not ok: {h}")
    print(f"[健康] JVS ok, stt_ready={h.get('stt_ready')} tts_ready={h.get('tts_ready')} sv_ready={h.get('sv_ready')}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="声纹识主混说测试（你 + 同事先后说话）")
    p.add_argument("--base-url", default=DEFAULT_BASE, help=f"JVS 根 URL（默认 {DEFAULT_BASE}）")
    p.add_argument("--profile", type=Path, default=DEFAULT_PROFILE, help="owner_voiceprint.json 路径")
    p.add_argument("--rounds", type=int, default=3, help="测试轮数（默认 3）")
    p.add_argument("--record", type=float, default=10.0, help="每轮录音秒数（默认 10）")
    p.add_argument("--save-dir", type=Path, default=None, help="可选：保存每轮 raw/owner wav 的目录")
    return p


def main() -> int:
    args = build_parser().parse_args()
    base = args.base_url.rstrip("/")
    try:
        import sounddevice  # noqa: F401
        import soundfile  # noqa: F401
        import numpy  # noqa: F401
    except Exception:
        print("[错误] 需要依赖: pip install sounddevice soundfile numpy", file=sys.stderr)
        return 1

    try:
        check_health(base)
    except Exception as e:
        print(f"[错误] JVS 不可用: {e}", file=sys.stderr)
        return 1

    try:
        profile = load_profile(args.profile)
    except Exception as e:
        print(f"[错误] 无法读取 owner profile: {e}", file=sys.stderr)
        return 1

    print("\n=== 测试说明 ===")
    print("每轮开始后，请按这个顺序说：")
    print("  1) 你先说一句完整指令")
    print("  2) 同事插一句无关话")
    print("  3) 你再补一句")
    print("脚本会输出：原始 STT vs 主人轨过滤后 STT\n")

    for i in range(1, max(1, args.rounds) + 1):
        try:
            input(f"\n[第 {i}/{args.rounds} 轮] 按 Enter 开始录音...")
        except KeyboardInterrupt:
            print("\n已取消。")
            return 130

        wav = None
        owner_wav = None
        try:
            wav = record_wav_bytes(args.record)
            total_ms = wav_duration_ms(wav)
            raw_text = stt_transcribe(base, wav)
            owner_wav, owner_ms, skipped, windows, window_params = filter_owner_track(base, wav, profile)
            owner_text = stt_transcribe(base, owner_wav) if owner_wav else ""

            if args.save_dir:
                save_round_audio(args.save_dir, i, wav, owner_wav)

            keep_ratio = (owner_ms / total_ms * 100.0) if total_ms > 0 else 0.0
            print(f"\n--- Round {i} ---")
            print(f"总时长: {total_ms} ms | 主人轨时长: {owner_ms} ms | 保留比例: {keep_ratio:.1f}%")
            print(f"跳过片段数: {len(skipped)}")
            print(f"原始 STT: {raw_text or '[空]'}")
            print(f"主人轨 STT: {owner_text or '[空]'}")
            raw_sentences = split_sentences(raw_text)
            owner_sentences = split_sentences(owner_text)
            print(f"原始 STT 分句数: {len(raw_sentences)}")
            for idx, sent in enumerate(raw_sentences, start=1):
                print(f"  RAW[{idx:02d}] {sent}")
            print(f"主人轨 STT 分句数: {len(owner_sentences)}")
            for idx, sent in enumerate(owner_sentences, start=1):
                print(f"  OWNER[{idx:02d}] {sent}")
            print(
                "滑窗参数: "
                f"step={window_params.get('win_step_ms')}ms, "
                f"len={window_params.get('win_len_ms')}ms, "
                f"high={window_params.get('win_threshold_high')}, "
                f"low={window_params.get('win_threshold_low')}, "
                f"debounce={window_params.get('debounce_count')}"
            )
            print(f"滑窗数量: {len(windows)}")
            for idx, w in enumerate(windows, start=1):
                start_ms = int(w.get("start_ms") or 0)
                end_ms = int(w.get("end_ms") or 0)
                score = float(w.get("score") or 0.0)
                label = str(w.get("label") or "")
                print(
                    f"  WIN[{idx:02d}] {start_ms:>5}..{end_ms:<5} ms "
                    f"dur={max(0, end_ms - start_ms):>4} ms "
                    f"score={score:+.4f} label={label}"
                )
            if raw_text and owner_text and raw_text != owner_text:
                print("结论: 已观察到文本差异（疑似过滤生效）")
            elif raw_text and not owner_text:
                print("结论: 主人轨为空（可能阈值偏严/说话太短/同事占比过高）")
            else:
                print("结论: 本轮差异不明显，建议再测 1-2 轮")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            print(f"[错误] HTTP {e.code}: {detail}", file=sys.stderr)
            return 1
        except Exception as e:
            print(f"[错误] 第 {i} 轮失败: {e}", file=sys.stderr)
            return 1

    print("\n测试完成。")
    if args.save_dir:
        print(f"音频已保存到: {args.save_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
