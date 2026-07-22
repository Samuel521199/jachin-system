"""Session-level endpointing for always-on voice input.

Low-level VAD/STT can tell us that an audio segment ended, but the assistant
still needs to decide whether the user's *task sentence* ended.  This layer
keeps a short pending fragment per voice session and returns one of:

- wait: the phrase is likely incomplete; do not plan a task yet.
- ready: the phrase is complete enough for GoalInterpreter/Dispatcher.
- merged: a previous pending fragment plus the current utterance forms one task.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from l3_node.cognitive_kernel.ledger import append_event
from l3_node.cognitive_kernel.paths import state_dir


EndpointAction = Literal["ready", "wait", "merged"]


@dataclass(slots=True)
class VoiceSessionEndpointDecision:
    action: EndpointAction
    raw_text: str
    effective_text: str
    session_key: str
    reason_code: str
    should_continue_planning: bool
    user_visible_reply: str = ""
    pending_text: str = ""
    merged_from_pending: bool = False
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_voice_session_endpoint(
    text: str,
    *,
    voice_context: dict[str, Any] | None = None,
    run_id: str = "",
    session_id: str = "",
    channel: str = "",
) -> VoiceSessionEndpointDecision:
    ctx = voice_context or {}
    raw = str(text or "").strip()
    key = _session_key(ctx, session_id=session_id, channel=channel)
    pending = _load_pending(key)
    compact = _compact(raw)
    reasons: list[str] = []
    finalized = _bool_or_none(ctx.get("voice_stt_finalized"))
    provisional = bool(ctx.get("voice_stt_provisional"))
    mode = str(ctx.get("voice_interaction_mode") or "").lower()
    confidence = _float(ctx.get("voice_stt_confidence") or ctx.get("voice_confidence")) or 0.0

    if mode not in {"continuous_listen", "wake_conversation"}:
        decision = _decision(
            action="ready",
            raw=raw,
            effective=raw,
            key=key,
            reason_code="non_continuous_mode",
            confidence=0.92,
            reasons=["push_to_talk_or_non_voice_endpointing_bypass"],
            pending=pending,
            run_id=run_id,
            ctx=ctx,
        )
        _append_endpoint_event(decision)
        return decision

    if provisional or finalized is False:
        merged_pending = _merge_text(pending.get("text", ""), raw)
        _save_pending(key, merged_pending or raw, reason="stt_not_finalized", run_id=run_id)
        decision = _decision(
            action="wait",
            raw=raw,
            effective=merged_pending or raw,
            key=key,
            reason_code="stt_not_finalized",
            confidence=0.88,
            reasons=["stt_segment_is_not_final"],
            pending=pending,
            run_id=run_id,
            ctx=ctx,
        )
        _append_endpoint_event(decision)
        return decision

    merged = _merge_text(pending.get("text", ""), raw)
    endpoint_text = merged or raw
    incomplete_reason = _incomplete_reason(endpoint_text)
    if incomplete_reason:
        _save_pending(key, endpoint_text, reason=incomplete_reason, run_id=run_id)
        decision = _decision(
            action="wait",
            raw=raw,
            effective=endpoint_text,
            key=key,
            reason_code=incomplete_reason,
            confidence=0.78,
            reasons=["semantic_endpoint_not_complete", incomplete_reason],
            pending=pending,
            run_id=run_id,
            ctx=ctx,
        )
        _append_endpoint_event(decision)
        return decision

    if pending.get("text") and endpoint_text != raw:
        _clear_pending(key)
        decision = _decision(
            action="merged",
            raw=raw,
            effective=endpoint_text,
            key=key,
            reason_code="merged_pending_voice_fragments",
            confidence=0.86,
            reasons=["previous_fragment_completed_by_current_utterance"],
            pending=pending,
            run_id=run_id,
            ctx=ctx,
        )
        _append_endpoint_event(decision)
        return decision

    _clear_pending(key)
    decision = _decision(
        action="ready",
        raw=raw,
        effective=raw,
        key=key,
        reason_code="voice_sentence_complete",
        confidence=0.9,
        reasons=["semantic_endpoint_complete"],
        pending=pending,
        run_id=run_id,
        ctx=ctx,
    )
    _append_endpoint_event(decision)
    return decision


def _decision(
    *,
    action: EndpointAction,
    raw: str,
    effective: str,
    key: str,
    reason_code: str,
    confidence: float,
    reasons: list[str],
    pending: dict[str, Any],
    run_id: str,
    ctx: dict[str, Any],
) -> VoiceSessionEndpointDecision:
    return VoiceSessionEndpointDecision(
        action=action,
        raw_text=raw,
        effective_text=effective,
        session_key=key,
        reason_code=reason_code,
        should_continue_planning=action != "wait",
        user_visible_reply=_reply(reason_code) if action == "wait" else "",
        pending_text=str(pending.get("text") or ""),
        merged_from_pending=bool(action == "merged"),
        confidence=confidence,
        reasons=reasons,
        evidence={
            "run_id": run_id,
            "created_at_ms": int(time.time() * 1000),
            "voice_interaction_mode": ctx.get("voice_interaction_mode") or "",
            "stt_finalized": ctx.get("voice_stt_finalized"),
            "stt_provisional": ctx.get("voice_stt_provisional"),
            "stt_confidence": ctx.get("voice_stt_confidence") or ctx.get("voice_confidence"),
            "pending_age_ms": _pending_age_ms(pending),
        },
    )


def _incomplete_reason(text: str) -> str:
    compact = _compact(text)
    if not compact:
        return "empty_or_too_short"
    if compact in {"打开", "关闭", "发送", "发给", "计算", "搜索", "查找", "总结", "帮我", "open", "close", "send", "search"}:
        return "bare_action_without_target"
    if re.search(r"(然后|接着|再|and|then)$", text.strip(), re.I):
        return "ends_with_continuation_marker"
    if re.search(r"(发给|发送给|给|打开|关闭|搜索|查找|总结|计算)\s*$", text.strip(), re.I):
        return "ends_with_missing_slot"
    if len(compact) <= 2 and re.search(r"(打开|关闭|发|搜|查)", compact):
        return "short_action_fragment"
    return ""


def _merge_text(left: str, right: str) -> str:
    left = str(left or "").strip()
    right = str(right or "").strip()
    if not left:
        return right
    if not right:
        return left
    if right in left:
        return left
    if left in right:
        return right
    return f"{left}，{right}"


def _session_key(ctx: dict[str, Any], *, session_id: str, channel: str) -> str:
    raw = (
        ctx.get("voice_session_id")
        or ctx.get("voice_trace_id")
        or session_id
        or channel
        or ctx.get("source")
        or "default"
    )
    return _safe_key(str(raw))


def _pending_path(key: str) -> Path:
    return state_dir() / "voice_sessions" / f"{_safe_key(key)}.json"


def _load_pending(key: str) -> dict[str, Any]:
    path = _pending_path(key)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        expires_at_ms = int(payload.get("expires_at_ms") or 0)
        if expires_at_ms and int(time.time() * 1000) > expires_at_ms:
            _clear_pending(key)
            return {}
        return payload if isinstance(payload, dict) else {}
    except Exception:
        _clear_pending(key)
        return {}


def _save_pending(key: str, text: str, *, reason: str, run_id: str) -> None:
    path = _pending_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = int(time.time() * 1000)
    payload = {
        "session_key": key,
        "text": str(text or "")[:2000],
        "reason": reason,
        "run_id": run_id,
        "saved_at_ms": now,
        "expires_at_ms": now + 45_000,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _clear_pending(key: str) -> None:
    try:
        _pending_path(key).unlink(missing_ok=True)
    except Exception:
        pass


def _pending_age_ms(pending: dict[str, Any]) -> int:
    try:
        saved = int(pending.get("saved_at_ms") or 0)
    except Exception:
        return 0
    return max(0, int(time.time() * 1000) - saved) if saved else 0


def _reply(reason_code: str) -> str:
    if reason_code == "stt_not_finalized":
        return "我还在等你把这句话说完，暂不执行。"
    if reason_code in {"bare_action_without_target", "ends_with_missing_slot", "short_action_fragment"}:
        return "我听到的是半句任务，先等你补全目标或内容。"
    if reason_code == "ends_with_continuation_marker":
        return "我听到你后面还有内容，先等你继续说。"
    return "我还不确定这句话已经说完，先不执行。"


def _compact(text: str) -> str:
    return re.sub(r"[\s,，。.!?？；;：:\"'“”‘’（）()\[\]{}<>、]+", "", str(text or "").strip().lower())


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_key(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in str(value or "default"))[:120]


def _append_endpoint_event(decision: VoiceSessionEndpointDecision) -> None:
    try:
        append_event("voice_session_endpoint_decision", decision.evidence.get("run_id") or "voice", decision.to_dict())
    except Exception:
        pass
