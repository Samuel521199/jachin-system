"""Voice language normalization before Cognitive Kernel planning.

This module is the boundary between noisy STT text and the deterministic
mission router. It keeps the policy narrow: normalize spoken entities, preserve
message bodies, and convert short spoken confirmations only when there is an
active pending DecisionContract.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from l3_node.voice_entity_correction import VoiceCorrectionResult, correct_voice_entities


@dataclass(slots=True)
class VoiceLanguageNormalization:
    raw_text: str
    input_text: str
    normalized_text: str
    is_voice: bool
    correction: VoiceCorrectionResult
    pending_confirmation_detected: bool = False
    pending_cancellation_detected: bool = False
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return self.normalized_text != self.input_text or self.correction.changed

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_text": self.raw_text,
            "input_text": self.input_text,
            "normalized_text": self.normalized_text,
            "is_voice": self.is_voice,
            "pending_confirmation_detected": self.pending_confirmation_detected,
            "pending_cancellation_detected": self.pending_cancellation_detected,
            "correction": {
                "raw_text": self.correction.raw_text,
                "cleaned_text": self.correction.cleaned_text,
                "corrected_text": self.correction.corrected_text,
                "corrections": [asdict(item) for item in self.correction.corrections],
                "suspect_tokens": [asdict(item) for item in self.correction.suspect_tokens],
            },
            "evidence": dict(self.evidence),
        }


def normalize_voice_language_input(
    user_input: str,
    *,
    session_id: str = "",
    channel: str = "",
    voice_context: dict[str, Any] | None = None,
) -> VoiceLanguageNormalization:
    """Return the text that should enter the Cognitive Kernel.

    For text turns this still runs entity normalization, but confirmation
    rewrites are enabled only for voice turns with an active pending task.
    """

    ctx = voice_context or {}
    is_voice = _is_voice_turn(ctx)
    raw_text = _first_non_empty(
        ctx.get("voice_raw_stt_text"),
        ctx.get("voice_asr_raw_text"),
        ctx.get("voice_final_text"),
        ctx.get("voice_routed_text"),
        user_input,
    )
    input_text = str(user_input or raw_text or "").strip()
    correction = correct_voice_entities(input_text)
    normalized = correction.corrected_text.strip()
    pending = _has_pending_confirmation(session_id=session_id, channel=channel)
    pending_confirm = False
    pending_cancel = False
    if is_voice and pending:
        if _looks_like_spoken_confirmation(normalized):
            normalized = "确认执行"
            pending_confirm = True
        elif _looks_like_spoken_cancellation(normalized):
            normalized = "取消"
            pending_cancel = True
    result = VoiceLanguageNormalization(
        raw_text=str(raw_text or ""),
        input_text=input_text,
        normalized_text=normalized,
        is_voice=is_voice,
        correction=correction,
        pending_confirmation_detected=pending_confirm,
        pending_cancellation_detected=pending_cancel,
        evidence={
            "session_id": session_id,
            "channel": channel,
            "pending_confirmation_present": pending,
            "voice_source": ctx.get("voice_stt_source") or ctx.get("source") or "",
            "voice_confidence": ctx.get("voice_stt_confidence") or ctx.get("voice_confidence"),
        },
    )
    _append_normalization_event(result)
    return result


def _is_voice_turn(ctx: dict[str, Any]) -> bool:
    if not ctx:
        return False
    if any(ctx.get(k) for k in ("voice_raw_stt_text", "voice_asr_raw_text", "voice_final_text", "voice_routed_text")):
        return True
    source = str(ctx.get("source") or ctx.get("voice_stt_source") or "").lower()
    return "voice" in source or "stt" in source


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _has_pending_confirmation(*, session_id: str = "", channel: str = "") -> bool:
    try:
        from l3_node.cognitive_kernel.pending_confirmation import load_pending_confirmation

        return load_pending_confirmation(session_id=session_id, channel=channel) is not None
    except Exception:
        return False


def _compact_spoken_reply(text: str) -> str:
    t = str(text or "").strip().lower()
    t = re.sub(r"[\s,，。.!！?？;；:：、…]+", "", t)
    return t


def _looks_like_spoken_confirmation(text: str) -> bool:
    compact = _compact_spoken_reply(text)
    if compact in {
        "是",
        "是的",
        "对",
        "对的",
        "没错",
        "确认",
        "确认执行",
        "可以",
        "可以执行",
        "继续",
        "继续执行",
        "就这个",
        "就是这个",
        "对就是这个",
        "是这个",
        "打开吧",
        "发吧",
        "发送吧",
        "执行吧",
        "继续吧",
        "goahead",
        "yes",
        "ok",
        "okay",
    }:
        return True
    return bool(re.fullmatch(r"(对|是|确认|可以|没错).{0,6}(这个|执行|打开|发送|发吧|继续)?", compact))


def _looks_like_spoken_cancellation(text: str) -> bool:
    compact = _compact_spoken_reply(text)
    if compact in {
        "不",
        "不是",
        "不对",
        "否",
        "取消",
        "别执行",
        "不要执行",
        "不用了",
        "算了",
        "停下",
        "停止",
        "no",
        "cancel",
        "stop",
    }:
        return True
    return bool(re.fullmatch(r"(不|不是|不对|取消|算了).{0,8}", compact))


def _append_normalization_event(result: VoiceLanguageNormalization) -> None:
    try:
        from l3_node.cognitive_kernel.ledger import append_event

        append_event(
            "voice_language_normalized",
            "voice-language-normalizer",
            {
                "input_text": result.input_text[:300],
                "normalized_text": result.normalized_text[:300],
                "is_voice": result.is_voice,
                "changed": result.changed,
                "corrections": [asdict(item) for item in result.correction.corrections],
                "suspect_tokens": [asdict(item) for item in result.correction.suspect_tokens],
                "pending_confirmation_detected": result.pending_confirmation_detected,
                "pending_cancellation_detected": result.pending_cancellation_detected,
                "evidence": result.evidence,
            },
        )
    except Exception:
        pass
