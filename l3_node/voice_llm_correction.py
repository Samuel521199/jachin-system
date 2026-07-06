"""Timeout-bounded optional LLM correction adapter for voice STT text.

The deterministic rule layer remains the primary path. This adapter is only
used when `JACHIN_VOICE_LLM_CORRECTOR_CMD` is configured and must return within
the configured timeout. It is intentionally process-based so the correction
model stays physically isolated from the L3 mission model.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import replace
from typing import Any

from l3_node.voice_entity_correction import VoiceCorrectionResult, correction_payload


def _timeout_seconds() -> float:
    raw = os.getenv("JACHIN_VOICE_LLM_CORRECTOR_TIMEOUT_MS", "500")
    try:
        return max(0.05, min(0.5, int(raw) / 1000.0))
    except ValueError:
        return 0.5


def run_bounded_llm_correction(result: VoiceCorrectionResult) -> VoiceCorrectionResult:
    cmd = os.getenv("JACHIN_VOICE_LLM_CORRECTOR_CMD", "").strip()
    if not cmd or not result.suspect_tokens:
        return result
    try:
        proc = subprocess.run(
            shlex.split(cmd),
            input=json.dumps(correction_payload(result), ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=_timeout_seconds(),
            check=False,
        )
    except Exception:
        return result
    if proc.returncode != 0 or not proc.stdout.strip():
        return result
    try:
        payload: dict[str, Any] = json.loads(proc.stdout)
    except Exception:
        return result
    corrected_text = str(payload.get("corrected_text") or "").strip()
    if not corrected_text:
        return result
    # External correction is only allowed to resolve unknown pre-message slots.
    # It may not edit a confirmed message body.
    marker_index = result.corrected_text.find("内容是")
    if marker_index >= 0 and corrected_text[marker_index:] != result.corrected_text[marker_index:]:
        return result
    return replace(result, corrected_text=corrected_text)
