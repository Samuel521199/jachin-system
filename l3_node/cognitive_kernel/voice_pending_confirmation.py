"""Pending store for voice false-trigger confirmation turns."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .ledger import append_event
from .paths import state_dir
from .pending_confirmation import confirmation_session_key, is_cancellation_text, is_confirmation_text


@dataclass(slots=True)
class PendingVoiceConfirmation:
    session_key: str
    original_user_input: str
    normalized_text: str
    guard: dict[str, Any]
    saved_at_ms: int
    expires_at_ms: int


def save_pending_voice_confirmation(
    *,
    original_user_input: str,
    normalized_text: str,
    guard: dict[str, Any],
    turn_id: str = "",
    session_id: str = "",
    channel: str = "",
) -> Path:
    key = confirmation_session_key(session_id=session_id, channel=channel)
    saved_at_ms = int(time.time() * 1000)
    expires_at_ms = saved_at_ms + pending_voice_confirmation_ttl_ms()
    payload = {
        "session_key": key,
        "original_user_input": str(original_user_input or ""),
        "normalized_text": str(normalized_text or original_user_input or ""),
        "guard": dict(guard or {}),
        "saved_at_ms": saved_at_ms,
        "expires_at_ms": expires_at_ms,
    }
    path = _pending_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    append_event(
        "voice_false_trigger_pending_saved",
        turn_id or "voice",
        {
            "session_key": key,
            "reason_code": guard.get("reason_code") if isinstance(guard, dict) else "",
            "expires_at_ms": expires_at_ms,
        },
    )
    return path


def resolve_pending_voice_confirmation(
    *,
    reply_text: str,
    session_id: str = "",
    channel: str = "",
    turn_id: str = "",
) -> dict[str, Any] | None:
    pending = load_pending_voice_confirmation(session_id=session_id, channel=channel)
    if pending is None:
        return None
    if is_cancellation_text(reply_text):
        clear_pending_voice_confirmation_by_key(pending.session_key)
        _record_pending_learning(pending, accepted=False, turn_id=turn_id)
        append_event(
            "voice_false_trigger_pending_cancelled",
            turn_id or "voice",
            {"session_key": pending.session_key, "reason": "user_cancelled"},
        )
        return {"action": "cancel", "pending": _pending_to_dict(pending)}
    if is_confirmation_text(reply_text):
        clear_pending_voice_confirmation_by_key(pending.session_key)
        _record_pending_learning(pending, accepted=True, turn_id=turn_id)
        append_event(
            "voice_false_trigger_pending_confirmed",
            turn_id or "voice",
            {"session_key": pending.session_key, "restored_input_preview": pending.normalized_text[:200]},
        )
        return {"action": "confirm", "pending": _pending_to_dict(pending)}
    updated = _try_update_pending_from_voice_reply(pending, reply_text=reply_text, turn_id=turn_id)
    if updated is not None:
        append_event(
            "voice_false_trigger_pending_updated",
            turn_id or "voice",
            {
                "session_key": updated.session_key,
                "normalized_text_preview": updated.normalized_text[:200],
                "slot_patch": updated.guard.get("slot_patch") if isinstance(updated.guard, dict) else {},
            },
        )
        return {
            "action": "update",
            "pending": _pending_to_dict(updated),
            "reply": _pending_update_reply(updated),
        }
    return None


def load_pending_voice_confirmation(*, session_id: str = "", channel: str = "") -> PendingVoiceConfirmation | None:
    key = confirmation_session_key(session_id=session_id, channel=channel)
    path = _pending_path(key)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        expires_at_ms = int(payload.get("expires_at_ms") or 0)
        if expires_at_ms and int(time.time() * 1000) > expires_at_ms:
            clear_pending_voice_confirmation_by_key(key)
            append_event("voice_false_trigger_pending_expired", "voice", {"session_key": key})
            return None
        return PendingVoiceConfirmation(
            session_key=str(payload.get("session_key") or key),
            original_user_input=str(payload.get("original_user_input") or ""),
            normalized_text=str(payload.get("normalized_text") or payload.get("original_user_input") or ""),
            guard=payload.get("guard") if isinstance(payload.get("guard"), dict) else {},
            saved_at_ms=int(payload.get("saved_at_ms") or 0),
            expires_at_ms=expires_at_ms,
        )
    except Exception:
        clear_pending_voice_confirmation_by_key(key)
        append_event("voice_false_trigger_pending_corrupt", "voice", {"session_key": key})
        return None


def clear_pending_voice_confirmation_by_key(key: str) -> None:
    try:
        _pending_path(key).unlink(missing_ok=True)
    except Exception:
        pass


def pending_voice_confirmation_ttl_ms() -> int:
    return 2 * 60 * 1000


def _pending_path(key: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in str(key or "default"))
    return state_dir() / "voice_pending_confirmations" / f"{safe}.json"


def _pending_to_dict(pending: PendingVoiceConfirmation) -> dict[str, Any]:
    return {
        "session_key": pending.session_key,
        "original_user_input": pending.original_user_input,
        "normalized_text": pending.normalized_text,
        "guard": pending.guard,
        "saved_at_ms": pending.saved_at_ms,
        "expires_at_ms": pending.expires_at_ms,
    }


def _try_update_pending_from_voice_reply(
    pending: PendingVoiceConfirmation,
    *,
    reply_text: str,
    turn_id: str = "",
) -> PendingVoiceConfirmation | None:
    patch = _extract_message_slot_patch(reply_text)
    if not patch:
        return None
    old_slots = _extract_send_slots(pending.normalized_text or pending.original_user_input)
    recipient = str(patch.get("recipient") or old_slots.get("recipient") or "").strip()
    message = str(patch.get("message") or old_slots.get("message") or "").strip()
    if not recipient and not message:
        return None
    normalized_text = _compose_send_text(message=message, recipient=recipient)
    guard = dict(pending.guard or {})
    prior_patch = guard.get("slot_patch_history")
    history = list(prior_patch) if isinstance(prior_patch, list) else []
    history.append(
        {
            "turn_id": turn_id,
            "heard_reply": str(reply_text or "")[:200],
            "patch": patch,
            "previous_normalized_text": pending.normalized_text[:200],
        }
    )
    guard["slot_patch"] = patch
    guard["slot_patch_history"] = history[-8:]
    payload = {
        "session_key": pending.session_key,
        "original_user_input": pending.original_user_input,
        "normalized_text": normalized_text,
        "guard": guard,
        "saved_at_ms": pending.saved_at_ms or int(time.time() * 1000),
        "expires_at_ms": int(time.time() * 1000) + pending_voice_confirmation_ttl_ms(),
    }
    path = _pending_path(pending.session_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return PendingVoiceConfirmation(
        session_key=pending.session_key,
        original_user_input=pending.original_user_input,
        normalized_text=normalized_text,
        guard=guard,
        saved_at_ms=int(payload["saved_at_ms"]),
        expires_at_ms=int(payload["expires_at_ms"]),
    )


def _extract_message_slot_patch(text: str) -> dict[str, str]:
    raw = _strip_voice_reply_prefix(text)
    if not raw:
        return {}
    recipient = ""
    message = ""
    recipient_patterns = (
        r"(?:发送给|发给|同步给|通知|告诉|给)\s*([A-Za-z][A-Za-z0-9_.-]{1,40}|[\u4e00-\u9fff]{2,12})",
        r"([A-Za-z][A-Za-z0-9_.-]{1,40}|[\u4e00-\u9fff]{2,12})\s*(?:发|发送|同步|通知)",
    )
    for pattern in recipient_patterns:
        matches = list(re.finditer(pattern, raw, flags=re.IGNORECASE))
        if matches:
            recipient = _clean_slot_value(matches[-1].group(1))
            break
    if recipient:
        pattern = r"(?:把|将)?(.+?)(?:发送给|发给|同步给|通知|告诉|给)\s*" + re.escape(recipient)
        match = re.search(pattern, raw, flags=re.IGNORECASE)
        if match:
            message = _clean_message_value(match.group(1))
    content_match = re.search(r"(?:内容|消息|正文)\s*(?:是|为|改成|改为|:|：)\s*(.+)$", raw, flags=re.IGNORECASE)
    if content_match:
        message = _clean_message_value(content_match.group(1))
    patch: dict[str, str] = {}
    if recipient:
        patch["recipient"] = recipient
    if message:
        patch["message"] = message
    return patch


def _extract_send_slots(text: str) -> dict[str, str]:
    patch = _extract_message_slot_patch(text)
    message = patch.get("message", "")
    recipient = patch.get("recipient", "")
    if not message:
        match = re.search(r"发送消息[，,\s]*(.+?)[，,\s]*(?:发送给|发给|给)\s*", text or "", flags=re.IGNORECASE)
        if match:
            message = _clean_message_value(match.group(1))
    return {"message": message, "recipient": recipient}


def _compose_send_text(*, message: str, recipient: str) -> str:
    if message and recipient:
        return f"发送消息，{message}，给 {recipient}"
    if recipient:
        return f"发送消息，给 {recipient}"
    return f"发送消息，{message}"


def _pending_update_reply(pending: PendingVoiceConfirmation) -> str:
    slots = _extract_send_slots(pending.normalized_text)
    recipient = slots.get("recipient") or "未确定收件人"
    message = slots.get("message") or "未确定内容"
    return f"我已更新待确认任务：发给 {recipient}，内容是“{message}”。请回复“确认执行”或“取消”。"


def _strip_voice_reply_prefix(text: str) -> str:
    value = str(text or "").strip()
    value = re.sub(r"^[\s,，。.!！]*(可以|好的|好|行|嗯|对|确认|可以的|没错)[\s,，。.!！]*", "", value)
    value = re.sub(r"^(你)?(把|帮我|帮忙|麻烦你)\s*", "", value)
    return value.strip()


def _clean_message_value(value: str) -> str:
    text = _clean_slot_value(value)
    text = re.sub(r"^(你)?(把|将)\s*", "", text)
    text = re.sub(r"^(这个|这条|这段)\s*", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip(" ，,。.!！")
    return text


def _clean_slot_value(value: str) -> str:
    return str(value or "").strip().strip(" ，,。.!！?？:：；;\"'“”‘’")


def _record_pending_learning(pending: PendingVoiceConfirmation, *, accepted: bool, turn_id: str = "") -> None:
    try:
        from l3_node.voice_false_trigger_learning import record_voice_false_trigger_learning

        record_voice_false_trigger_learning(
            pending.guard,
            turn_id=turn_id or pending.session_key,
            source="voice_pending_confirmation",
            accepted_override=accepted,
        )
    except Exception:
        pass
