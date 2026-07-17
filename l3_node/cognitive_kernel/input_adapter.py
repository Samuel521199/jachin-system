"""Unified input adapter for the Memory-first Cognitive Kernel.

Every external entry point should become an ``AgentInputEnvelope`` through this
adapter before GoalInterpreter, TaskDecomposer, or Dispatcher see it.  The
adapter is intentionally small: it detects the source, applies voice language
normalization only for voice turns, preserves raw modality evidence, and emits a
ledger event that makes the ingress decision visible in Evidence.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .contracts import InputSource
from .ledger import append_event


@dataclass(slots=True)
class CognitiveInputAdaptation:
    turn_id: str
    source: InputSource
    raw_text: str
    normalized_text: str
    session_id: str = ""
    channel: str = ""
    confidence: float | None = None
    language: str = ""
    changed: bool = False
    desktop_companion_context: dict[str, Any] = field(default_factory=dict)
    modality_evidence: dict[str, Any] = field(default_factory=dict)
    adapter_evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "source": self.source.value,
            "raw_text": self.raw_text,
            "normalized_text": self.normalized_text,
            "session_id": self.session_id,
            "channel": self.channel,
            "confidence": self.confidence,
            "language": self.language,
            "changed": self.changed,
            "modality_evidence": self.modality_evidence,
            "adapter_evidence": self.adapter_evidence,
        }


def adapt_input_for_cognitive_kernel(
    *,
    turn_id: str,
    user_input: str,
    channel: str = "",
    session_id: str = "",
    desktop_companion_context: dict[str, Any] | None = None,
    implicit_attribution: dict[str, Any] | None = None,
) -> CognitiveInputAdaptation:
    """Normalize one incoming turn into the kernel's source-aware input.

    The function is idempotent for a single ``desktop_companion_context``.  This
    lets ``run_agent`` adapt early for user-facing logs and lets
    ``build_cognitive_turn_context`` reuse the same evidence without redoing the
    normalization.
    """

    companion = desktop_companion_context if desktop_companion_context is not None else {}
    if companion.get("input_adapter_applied"):
        return _adaptation_from_context(
            turn_id=turn_id,
            user_input=user_input,
            channel=channel,
            session_id=session_id,
            companion=companion,
        )

    source = _source_from_channel(channel, companion, implicit_attribution)
    raw_voice = _raw_voice_text(companion)
    raw_text = str(raw_voice or user_input or "").strip()
    normalized_text = str(user_input or raw_text or "").strip()
    confidence = _confidence_from_context(companion)
    adapter_steps: list[dict[str, Any]] = []
    voice_normalization_payload: dict[str, Any] = {}
    changed = False

    if source == InputSource.VOICE:
        try:
            from l3_node.voice_language_normalizer import normalize_voice_language_input

            voice_result = normalize_voice_language_input(
                user_input or raw_text,
                session_id=session_id,
                channel=channel,
                voice_context=companion,
            )
            raw_text = voice_result.raw_text or raw_text
            normalized_text = voice_result.normalized_text or normalized_text
            changed = bool(voice_result.changed)
            voice_normalization_payload = voice_result.to_dict()
            adapter_steps.append(
                {
                    "name": "voice_language_normalizer",
                    "changed": voice_result.changed,
                    "pending_confirmation_detected": voice_result.pending_confirmation_detected,
                    "pending_cancellation_detected": voice_result.pending_cancellation_detected,
                    "correction_count": len(voice_result.correction.corrections),
                    "suspect_count": len(voice_result.correction.suspect_tokens),
                }
            )
            _write_voice_result_to_context(companion, voice_result)
        except Exception as exc:
            adapter_steps.append({"name": "voice_language_normalizer", "error": type(exc).__name__})

    companion["input_adapter_applied"] = True
    companion["input_adapter_source"] = source.value
    companion["input_adapter_raw_text"] = raw_text
    companion["input_adapter_normalized_text"] = normalized_text
    companion["input_adapter_changed"] = changed
    companion["input_adapter_steps"] = adapter_steps
    companion["input_adapter_turn_id"] = turn_id

    adapter_evidence = {
        "source": source.value,
        "raw_text": raw_text[:500],
        "normalized_text": normalized_text[:500],
        "changed": changed,
        "steps": adapter_steps,
        "channel": channel,
        "session_id": session_id,
        "created_at_ms": int(time.time() * 1000),
    }
    modality_evidence = _modality_evidence_for(
        source=source,
        companion=companion,
        adapter_evidence=adapter_evidence,
        voice_normalization_payload=voice_normalization_payload,
    )
    adaptation = CognitiveInputAdaptation(
        turn_id=turn_id,
        source=source,
        raw_text=raw_text,
        normalized_text=normalized_text,
        session_id=session_id,
        channel=channel,
        confidence=confidence,
        changed=changed,
        desktop_companion_context=companion,
        modality_evidence=modality_evidence,
        adapter_evidence=adapter_evidence,
    )
    _append_input_adapter_event(adaptation)
    return adaptation


def _adaptation_from_context(
    *,
    turn_id: str,
    user_input: str,
    channel: str,
    session_id: str,
    companion: dict[str, Any],
) -> CognitiveInputAdaptation:
    source = _source_from_value(companion.get("input_adapter_source")) or _source_from_channel(channel, companion, None)
    raw_text = str(companion.get("input_adapter_raw_text") or _raw_voice_text(companion) or user_input or "").strip()
    normalized_text = str(companion.get("input_adapter_normalized_text") or user_input or raw_text or "").strip()
    changed = bool(companion.get("input_adapter_changed"))
    adapter_evidence = {
        "source": source.value,
        "raw_text": raw_text[:500],
        "normalized_text": normalized_text[:500],
        "changed": changed,
        "steps": list(companion.get("input_adapter_steps") or []),
        "channel": channel,
        "session_id": session_id,
        "reused": True,
    }
    return CognitiveInputAdaptation(
        turn_id=turn_id,
        source=source,
        raw_text=raw_text,
        normalized_text=normalized_text,
        session_id=session_id,
        channel=channel,
        confidence=_confidence_from_context(companion),
        changed=changed,
        desktop_companion_context=companion,
        modality_evidence=_modality_evidence_for(
            source=source,
            companion=companion,
            adapter_evidence=adapter_evidence,
            voice_normalization_payload=companion.get("voice_language_normalization") or {},
        ),
        adapter_evidence=adapter_evidence,
    )


def _source_from_value(value: Any) -> InputSource | None:
    try:
        return InputSource(str(value))
    except Exception:
        return None


def _source_from_channel(
    channel: str,
    companion: dict[str, Any],
    implicit_attribution: dict[str, Any] | None,
) -> InputSource:
    if _raw_voice_text(companion):
        return InputSource.VOICE
    source = str(companion.get("source") or companion.get("voice_stt_source") or "").lower()
    if "voice" in source or "stt" in source:
        return InputSource.VOICE
    attribution = implicit_attribution or {}
    if str(attribution.get("source") or "").lower() in {"voice", "stt"}:
        return InputSource.VOICE
    ch = (channel or str(attribution.get("channel") or "")).strip().lower()
    if "lark" in ch or "im" in ch:
        return InputSource.IM
    if "hotkey" in ch:
        return InputSource.HOTKEY
    if "watch" in ch or "scheduler" in ch:
        return InputSource.WATCHER
    if "api" in ch:
        return InputSource.API
    return InputSource.TEXT


def _raw_voice_text(companion: dict[str, Any]) -> str:
    for key in ("voice_raw_stt_text", "voice_asr_raw_text", "voice_final_text", "voice_routed_text"):
        text = str(companion.get(key) or "").strip()
        if text:
            return text
    return ""


def _confidence_from_context(companion: dict[str, Any]) -> float | None:
    for key in ("voice_confidence", "voice_stt_confidence", "confidence", "stt_confidence"):
        value = companion.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _write_voice_result_to_context(companion: dict[str, Any], voice_result: Any) -> None:
    companion["voice_language_raw_input"] = voice_result.input_text
    companion["voice_language_normalized_text"] = voice_result.normalized_text
    companion["voice_language_changed"] = voice_result.changed
    companion["voice_language_pending_confirmation_detected"] = voice_result.pending_confirmation_detected
    companion["voice_language_pending_cancellation_detected"] = voice_result.pending_cancellation_detected
    companion["voice_language_normalization"] = voice_result.to_dict()
    companion["voice_language_corrections"] = [
        {
            "kind": item.kind,
            "original": item.original,
            "canonical": item.canonical,
            "reason": item.reason,
            "confidence": item.confidence,
        }
        for item in voice_result.correction.corrections
    ]


def _modality_evidence_for(
    *,
    source: InputSource,
    companion: dict[str, Any],
    adapter_evidence: dict[str, Any],
    voice_normalization_payload: dict[str, Any],
) -> dict[str, Any]:
    evidence: dict[str, Any] = {"input_adapter": adapter_evidence}
    if companion:
        evidence["desktop_companion"] = dict(companion)
    if source == InputSource.VOICE:
        evidence["voice"] = dict(companion)
        if voice_normalization_payload:
            evidence["voice_language_normalization"] = voice_normalization_payload
    return evidence


def _append_input_adapter_event(adaptation: CognitiveInputAdaptation) -> None:
    try:
        payload = adaptation.to_dict()
        payload["raw_text"] = payload["raw_text"][:500]
        payload["normalized_text"] = payload["normalized_text"][:500]
        append_event("input_adapter_finished", adaptation.turn_id, payload)
    except Exception:
        pass
