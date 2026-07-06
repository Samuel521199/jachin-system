#!/usr/bin/env python3
"""Audit VOICE_STT_ROBUSTNESS_PROPOSAL phase closure artifacts."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def _exists(rel: str) -> bool:
    return (ROOT / rel).exists()


def _run(cmd: list[str]) -> dict[str, Any]:
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
    return {"ok": proc.returncode == 0, "returncode": proc.returncode, "tail": proc.stdout.splitlines()[-6:]}


def audit() -> dict[str, Any]:
    t0 = _run([sys.executable, "scripts/test_voice_stt_robustness_eval.py", "--cases", "data/eval/t0_text_cases.jsonl"])
    t0_cn = _run([sys.executable, "scripts/test_voice_stt_robustness_eval.py", "--cases", "data/eval/t0_chinese_cases.jsonl"])
    audio = _run([sys.executable, "scripts/voice_audio_chain_diagnostics.py"])
    engine = _run([sys.executable, "scripts/evaluate_stt_engine.py"])

    phases = {
        "0-A": {"status": "framework_done_external_samples_required", "artifacts": ["scripts/voice_audio_chain_diagnostics.py", "data/eval_wav/"]},
        "0-B": {"status": "done" if t0_cn["ok"] else "failed", "artifacts": ["l3_node/voice_semantic_guard.py", "data/eval/t0_chinese_cases.jsonl"]},
        "0-C": {"status": "done", "artifacts": ["l3_node/voice_risk_gate.py"]},
        "1": {"status": "done", "artifacts": ["l3_node/mission_runtime.py"]},
        "2": {"status": "done", "artifacts": ["l3_node/voice_entity_correction.py", "data/voice/user_aliases.json"]},
        "3": {"status": "done", "artifacts": ["l3_node/voice_entity_correction.py"]},
        "4": {"status": "done", "artifacts": ["l3_node/capability_router.py", "l3_node/semantic_slot_parser.py"]},
        "5": {"status": "done", "artifacts": ["l3_node/capability_router.py"]},
        "6": {"status": "done", "artifacts": ["l3_node/mission_runtime.py"]},
        "7": {"status": "done_optional_bounded_adapter", "artifacts": ["l3_node/voice_llm_correction.py", "JACHIN_VOICE_LLM_CORRECTOR_CMD"]},
        "8": {"status": "done", "artifacts": ["suspect_tokens in l3_node/voice_entity_correction.py"]},
        "9": {"status": "code_done_engine_unsupported", "artifacts": ["voice_server/services/stt_hotwords.py", "voice_server/services/stt_service.py"]},
        "10": {"status": "contract_done", "artifacts": ["data/eval/t0_text_cases.jsonl"]},
        "11": {"status": "done", "artifacts": ["correction_payload", "STT hotword metadata responses"]},
        "12": {"status": "done", "artifacts": ["teach_alias/list/deactivate/bulk_import", "scripts/manage_voice_aliases.py"]},
        "13": {"status": "done", "artifacts": ["active/source/updated_at user alias lifecycle", "scripts/manage_voice_aliases.py"]},
        "14": {"status": "done", "artifacts": ["deterministic fast-path correction, no LLM dependency"]},
        "15": {"status": "framework_done_external_samples_required", "artifacts": ["scripts/voice_audio_chain_diagnostics.py", "data/eval_wav/"]},
        "16": {"status": "done_with_replacement_recommendation", "artifacts": ["scripts/evaluate_stt_engine.py"]},
    }

    missing_files = [rel for rel in [
        "l3_node/voice_entity_correction.py",
        "l3_node/voice_semantic_guard.py",
        "l3_node/voice_risk_gate.py",
        "l3_node/voice_llm_correction.py",
        "voice_server/services/stt_hotwords.py",
        "scripts/test_voice_stt_robustness_eval.py",
        "scripts/voice_audio_chain_diagnostics.py",
        "scripts/evaluate_stt_engine.py",
        "data/eval/t0_text_cases.jsonl",
        "data/eval/t0_chinese_cases.jsonl",
        "data/eval_wav/manifest.example.jsonl",
        "scripts/manage_voice_aliases.py",
    ] if not _exists(rel)]
    code_ok = t0["ok"] and t0_cn["ok"] and not missing_files
    external_blockers = [
        "collect real T1-T4 WAV sample set",
        "configure cloud ASR fallback or replace current SenseVoice build if hotword weighting is required",
    ]
    return {
        "code_closure_ok": code_ok,
        "full_real_world_closure_ok": code_ok and not external_blockers,
        "external_blockers": external_blockers,
        "missing_files": missing_files,
        "phases": phases,
        "checks": {"t0": t0, "t0_chinese": t0_cn, "audio": audio, "engine": engine},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check voice STT robustness phase closure")
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    payload = audit()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if args.strict and not payload["full_real_world_closure_ok"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
