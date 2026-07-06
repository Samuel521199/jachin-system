#!/usr/bin/env python3
"""Phase 16 STT engine readiness and replacement gate."""
from __future__ import annotations

import argparse
import inspect
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
VOICE_SERVER = ROOT / "voice_server"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(VOICE_SERVER) not in sys.path:
    sys.path.insert(0, str(VOICE_SERVER))


def _run_eval(case_file: Path) -> dict[str, Any]:
    cmd = [sys.executable, "scripts/test_voice_stt_robustness_eval.py", "--cases", str(case_file)]
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
    return {"ok": proc.returncode == 0, "returncode": proc.returncode, "stdout_tail": proc.stdout.splitlines()[-8:]}


def _hotword_support() -> dict[str, Any]:
    try:
        from funasr_onnx import SenseVoiceSmall

        source = inspect.getsource(SenseVoiceSmall.__call__)
        supported = "hotword" in source or "hotwords" in source
        return {"engine": "funasr_onnx.SenseVoiceSmall", "hotword_supported": supported}
    except Exception as exc:
        return {"engine": "funasr_onnx.SenseVoiceSmall", "hotword_supported": False, "error": repr(exc)}


def _audio_diag() -> dict[str, Any]:
    try:
        from scripts.voice_audio_chain_diagnostics import run

        return run(ROOT / "data" / "eval_wav")
    except Exception as exc:
        return {"status": "error", "error": repr(exc)}


def evaluate() -> dict[str, Any]:
    t0 = _run_eval(ROOT / "data" / "eval" / "t0_text_cases.jsonl")
    t0_cn = _run_eval(ROOT / "data" / "eval" / "t0_chinese_cases.jsonl")
    hotword = _hotword_support()
    audio = _audio_diag()

    blockers: list[str] = []
    recommendations: list[str] = []
    if not t0["ok"]:
        blockers.append("t0_text_eval_failed")
    if not t0_cn["ok"]:
        blockers.append("t0_chinese_eval_failed")
    if not hotword.get("hotword_supported"):
        blockers.append("current_engine_hotword_unsupported")
        recommendations.append("enable cloud ASR fallback or replace local STT engine for Phase 9 hotword weighting")
    if audio.get("status") != "ok":
        blockers.append("real_audio_samples_missing_or_incomplete")
        recommendations.append("collect T1-T4 fixed WAV samples before claiming Phase 15 metrics")

    decision = "keep_with_correction_layer"
    if "current_engine_hotword_unsupported" in blockers:
        decision = "replace_engine_or_enable_cloud_fallback"
    if any(b.endswith("_failed") for b in blockers):
        decision = "do_not_ship_until_text_gates_pass"

    return {
        "decision": decision,
        "blockers": blockers,
        "recommendations": recommendations,
        "checks": {"t0": t0, "t0_chinese": t0_cn, "hotword": hotword, "audio": audio},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate STT engine readiness against Phase 16 gates")
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--strict", action="store_true", help="fail unless no blockers remain")
    args = parser.parse_args()

    payload = evaluate()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if args.strict and payload["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
