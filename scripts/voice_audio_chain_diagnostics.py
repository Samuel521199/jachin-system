#!/usr/bin/env python3
"""Phase 0-A / Phase 15 audio-chain diagnostics for STT robustness.

This script inspects fixed WAV sample tiers without requiring live microphone
access. It reports sample-rate drift, duration, RMS/peak level, clipping, and
rough leading/trailing silence so VAD/resampling/SV problems can be separated
from STT model errors.

Usage:
  python scripts/voice_audio_chain_diagnostics.py
  python scripts/voice_audio_chain_diagnostics.py --json-out reports/voice_audio_diag.json
  python scripts/voice_audio_chain_diagnostics.py --strict
"""
from __future__ import annotations

import argparse
import json
import math
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = ROOT / "data" / "eval_wav"
TIERS = {
    "T1": "t1_clean",
    "T2": "t2_ptt",
    "T3": "t3_vad",
    "T4": "t4_noisy",
}


@dataclass
class WavMetric:
    path: str
    tier: str
    sample_rate: int
    channels: int
    sample_width: int
    duration_ms: int
    rms_dbfs: float
    peak_dbfs: float
    clipping_ratio: float
    leading_silence_ms: int
    trailing_silence_ms: int
    resample_required: bool
    vad_risk: str


@dataclass
class TierReport:
    tier: str
    directory: str
    sample_count: int = 0
    status: str = "missing_samples"
    metrics: list[WavMetric] = field(default_factory=list)


def _iter_wavs(root: Path, tier_dir: str) -> list[Path]:
    folder = root / tier_dir
    if not folder.exists():
        return []
    return sorted(p for p in folder.rglob("*.wav") if p.is_file())


def _pcm16_values(raw: bytes, sample_width: int) -> list[int]:
    if sample_width != 2 or not raw:
        return []
    vals: list[int] = []
    for i in range(0, len(raw) - 1, 2):
        v = int.from_bytes(raw[i : i + 2], "little", signed=True)
        vals.append(v)
    return vals


def _dbfs(value: float) -> float:
    if value <= 0:
        return -120.0
    return round(20.0 * math.log10(min(1.0, value / 32768.0)), 2)


def _silence_ms(vals: list[int], sample_rate: int, *, from_end: bool = False, threshold: int = 500) -> int:
    seq = reversed(vals) if from_end else iter(vals)
    count = 0
    for v in seq:
        if abs(v) > threshold:
            break
        count += 1
    return int(count / max(sample_rate, 1) * 1000)


def inspect_wav(path: Path, tier: str) -> WavMetric:
    with wave.open(str(path), "rb") as wf:
        sample_rate = wf.getframerate()
        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        frames = wf.getnframes()
        raw = wf.readframes(frames)
    vals = _pcm16_values(raw, sample_width)
    if channels > 1 and vals:
        vals = vals[::channels]
    duration_ms = int(frames / max(sample_rate, 1) * 1000)
    peak = max((abs(v) for v in vals), default=0)
    rms = math.sqrt(sum(v * v for v in vals) / len(vals)) if vals else 0.0
    clipping = sum(1 for v in vals if abs(v) >= 32700) / max(len(vals), 1)
    leading = _silence_ms(vals, sample_rate)
    trailing = _silence_ms(vals, sample_rate, from_end=True)
    vad_risk = "ok"
    if duration_ms < 800:
        vad_risk = "too_short"
    elif leading < 80 and tier in {"T2", "T3"}:
        vad_risk = "possible_head_cut"
    elif trailing < 80 and tier in {"T2", "T3"}:
        vad_risk = "possible_tail_cut"
    elif _dbfs(rms) < -35:
        vad_risk = "low_gain"
    elif clipping > 0.005:
        vad_risk = "clipping"
    return WavMetric(
        path=str(path.relative_to(ROOT)),
        tier=tier,
        sample_rate=sample_rate,
        channels=channels,
        sample_width=sample_width,
        duration_ms=duration_ms,
        rms_dbfs=_dbfs(rms),
        peak_dbfs=_dbfs(float(peak)),
        clipping_ratio=round(clipping, 6),
        leading_silence_ms=leading,
        trailing_silence_ms=trailing,
        resample_required=sample_rate != 16000 or channels != 1,
        vad_risk=vad_risk,
    )


def run(root: Path) -> dict[str, Any]:
    tiers: list[TierReport] = []
    total = 0
    for tier, folder in TIERS.items():
        wavs = _iter_wavs(root, folder)
        report = TierReport(tier=tier, directory=str((root / folder).relative_to(ROOT)), sample_count=len(wavs))
        for wav_path in wavs:
            report.metrics.append(inspect_wav(wav_path, tier))
        total += len(wavs)
        report.status = "ok" if wavs else "missing_samples"
        tiers.append(report)
    status = "ok" if all(t.sample_count > 0 for t in tiers) else "external_samples_required"
    return {
        "status": status,
        "expected_minimums": {"T1": 20, "T2": 10, "T3": 5, "T4": 5},
        "total_samples": total,
        "tiers": [asdict(t) for t in tiers],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect fixed STT eval WAV tiers")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--strict", action="store_true", help="fail when required real samples are missing")
    args = parser.parse_args()

    payload = run(args.root)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if args.strict and payload["status"] != "ok" else 0


if __name__ == "__main__":
    raise SystemExit(main())
