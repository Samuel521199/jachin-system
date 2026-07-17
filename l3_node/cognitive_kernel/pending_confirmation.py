"""Pending DecisionContract store for confirmation-resume turns."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import DecisionContract, RiskLevel, ToolPolicy, WorkOrder
from .ledger import append_event
from .paths import state_dir


@dataclass(slots=True)
class PendingConfirmation:
    session_key: str
    contract: DecisionContract
    work_order: WorkOrder
    saved_at_ms: int
    expires_at_ms: int


_TRAILING_CONFIRMATION_PUNCTUATION = " .!?,;:\t\r\n。！？、，；：…"


def confirmation_session_key(*, session_id: str = "", channel: str = "") -> str:
    sid = str(session_id or "").strip()
    if sid:
        return sid
    ch = str(channel or "").strip()
    if ch:
        return f"channel:{ch}"
    return "default"


def is_confirmation_text(text: str) -> bool:
    normalized = _normalize_confirmation_reply(text)
    return normalized in {
        "confirm",
        "confirmed",
        "yes",
        "ok",
        "okay",
        "approve",
        "approved",
        "go ahead",
        "continue",
        "execute",
        "\u786e\u8ba4",
        "\u786e\u5b9a",
        "\u662f",
        "\u662f\u7684",
        "\u5bf9",
        "\u5bf9\u7684",
        "\u6ca1\u9519",
        "\u5c31\u662f",
        "\u6279\u51c6",
        "\u540c\u610f",
        "\u7ee7\u7eed",
        "\u6267\u884c",
        "\u786e\u8ba4\u6267\u884c",
        "\u53ef\u4ee5\u6267\u884c",
        "\u5c31\u8fd9\u4e2a",
        "\u5c31\u662f\u8fd9\u4e2a",
        "\u5bf9\u5c31\u662f\u8fd9\u4e2a",
        "\u662f\u8fd9\u4e2a",
        "\u6253\u5f00\u5427",
        "\u53d1\u5427",
        "\u53d1\u9001\u5427",
        "\u6267\u884c\u5427",
        "\u7ee7\u7eed\u5427",
    }


def is_cancellation_text(text: str) -> bool:
    normalized = _normalize_confirmation_reply(text)
    return normalized in {
        "cancel",
        "cancelled",
        "abort",
        "stop",
        "no",
        "do not execute",
        "\u5426",
        "\u4e0d",
        "\u4e0d\u662f",
        "\u4e0d\u5bf9",
        "\u53d6\u6d88",
        "\u505c\u6b62",
        "\u4e0d\u6267\u884c",
        "\u4e0d\u8981\u6267\u884c",
        "\u4e0d\u7528\u4e86",
        "\u7b97\u4e86",
        "\u522b\u6267\u884c",
        "\u505c\u4e0b",
    }


def _normalize_confirmation_reply(text: str) -> str:
    normalized = str(text or "").strip().lower()
    normalized = normalized.strip(_TRAILING_CONFIRMATION_PUNCTUATION)
    normalized = normalized.replace("\u3000", " ")
    normalized = " ".join(normalized.split())
    normalized = normalized.replace(",", "").replace("\uff0c", "").replace("\u3001", "")
    return normalized


def save_pending_confirmation(
    *,
    contract: DecisionContract,
    work_order: WorkOrder,
    session_id: str = "",
    channel: str = "",
) -> Path:
    key = confirmation_session_key(session_id=session_id, channel=channel)
    saved_at_ms = int(time.time() * 1000)
    expires_at_ms = saved_at_ms + pending_confirmation_ttl_ms()
    payload = {
        "session_key": key,
        "saved_at_ms": saved_at_ms,
        "expires_at_ms": expires_at_ms,
        "contract": contract.to_dict(),
        "work_order": work_order.to_dict(),
    }
    path = _pending_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    append_event(
        "confirmation_pending_saved",
        contract.turn_id,
        {
            "session_key": key,
            "decision_id": contract.decision_id,
            "work_order_id": work_order.work_order_id,
            "tool": work_order.inputs.get("tool"),
            "risk_level": contract.risk_level.value,
            "confirmation_reason": contract.tool_policy.confirmation_reason or contract.clarification_question,
            "expires_at_ms": expires_at_ms,
        },
    )
    return path


def load_pending_confirmation(*, session_id: str = "", channel: str = "") -> PendingConfirmation | None:
    key = confirmation_session_key(session_id=session_id, channel=channel)
    path = _pending_path(key)
    if path.exists():
        return _load_pending_path(path, expected_key=key)
    return _load_single_recent_pending_fallback(missing_key=key)


def clear_pending_confirmation(*, session_id: str = "", channel: str = "") -> None:
    key = confirmation_session_key(session_id=session_id, channel=channel)
    clear_pending_confirmation_by_key(key)


def clear_pending_confirmation_by_key(key: str) -> None:
    try:
        _pending_path(key).unlink(missing_ok=True)
    except Exception:
        pass


def cancel_pending_confirmation(*, session_id: str = "", channel: str = "", reason: str = "user_cancelled") -> PendingConfirmation | None:
    pending = load_pending_confirmation(session_id=session_id, channel=channel)
    if pending is None:
        return None
    append_event(
        "confirmation_cancelled",
        pending.contract.turn_id,
        {
            "session_key": pending.session_key,
            "reason": reason,
            "decision_id": pending.contract.decision_id,
            "work_order_id": pending.work_order.work_order_id,
            "tool": pending.work_order.inputs.get("tool"),
        },
    )
    clear_pending_confirmation_by_key(pending.session_key)
    return pending


def mark_pending_as_confirmed(pending: PendingConfirmation, *, confirmation_turn_id: str = "") -> None:
    contract = pending.contract
    contract.execution_allowed = True
    contract.tool_policy.requires_confirmation = False
    contract.tool_policy.confirmation_reason = ""
    contract.clarification_question = ""
    append_event(
        "confirmation_resumed",
        contract.turn_id,
        {
            "session_key": pending.session_key,
            "confirmation_turn_id": confirmation_turn_id,
            "decision_id": contract.decision_id,
            "work_order_id": pending.work_order.work_order_id,
            "tool": pending.work_order.inputs.get("tool"),
        },
    )


def pending_confirmation_ttl_ms() -> int:
    import os

    raw = os.environ.get("JACHIN_PENDING_CONFIRMATION_TTL_SECONDS", "900").strip()
    try:
        seconds = float(raw)
    except ValueError:
        seconds = 900.0
    return int(max(30.0, min(seconds, 24 * 60 * 60.0)) * 1000)


def _pending_path(key: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in key)[:120] or "default"
    return state_dir() / "pending_confirmations" / f"{safe}.json"


def _load_pending_path(path: Path, *, expected_key: str = "") -> PendingConfirmation | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        key = str(payload.get("session_key") or expected_key or path.stem)
        contract = _contract_from_dict(payload.get("contract") or {})
        work_order = _work_order_from_dict(payload.get("work_order") or {})
        saved_at_ms = int(payload.get("saved_at_ms") or 0)
        expires_at_ms = int(payload.get("expires_at_ms") or (saved_at_ms + pending_confirmation_ttl_ms()))
        if expires_at_ms and int(time.time() * 1000) > expires_at_ms:
            append_event(
                "confirmation_expired",
                contract.turn_id,
                {
                    "session_key": key,
                    "decision_id": contract.decision_id,
                    "work_order_id": work_order.work_order_id,
                    "expires_at_ms": expires_at_ms,
                },
            )
            clear_pending_confirmation_by_key(key)
            return None
        return PendingConfirmation(
            session_key=key,
            contract=contract,
            work_order=work_order,
            saved_at_ms=saved_at_ms,
            expires_at_ms=expires_at_ms,
        )
    except Exception:
        return None


def _load_single_recent_pending_fallback(*, missing_key: str) -> PendingConfirmation | None:
    pending_dir = state_dir() / "pending_confirmations"
    if not pending_dir.exists():
        return None
    candidates: list[PendingConfirmation] = []
    for path in pending_dir.glob("*.json"):
        pending = _load_pending_path(path)
        if pending is not None:
            candidates.append(pending)
    if len(candidates) != 1:
        return None
    pending = candidates[0]
    append_event(
        "confirmation_pending_session_fallback",
        pending.contract.turn_id,
        {
            "missing_session_key": missing_key,
            "fallback_session_key": pending.session_key,
            "decision_id": pending.contract.decision_id,
            "work_order_id": pending.work_order.work_order_id,
            "tool": pending.work_order.inputs.get("tool"),
        },
    )
    return pending


def _risk(value: Any) -> RiskLevel:
    try:
        return RiskLevel(str(value or RiskLevel.LOW.value))
    except Exception:
        return RiskLevel.LOW


def _tool_policy_from_dict(data: dict[str, Any]) -> ToolPolicy:
    return ToolPolicy(
        allowed_tools=[str(x) for x in data.get("allowed_tools") or []],
        denied_tools=[str(x) for x in data.get("denied_tools") or []],
        risk_level=_risk(data.get("risk_level")),
        requires_confirmation=bool(data.get("requires_confirmation")),
        confirmation_reason=str(data.get("confirmation_reason") or ""),
        verification_required=bool(data.get("verification_required", True)),
    )


def _contract_from_dict(data: dict[str, Any]) -> DecisionContract:
    return DecisionContract(
        decision_id=str(data.get("decision_id") or ""),
        turn_id=str(data.get("turn_id") or ""),
        task_type=str(data.get("task_type") or ""),
        goal=str(data.get("goal") or ""),
        selected_workflow=str(data.get("selected_workflow") or ""),
        selected_roles=[str(x) for x in data.get("selected_roles") or []],
        risk_level=_risk(data.get("risk_level")),
        tool_policy=_tool_policy_from_dict(data.get("tool_policy") or {}),
        execution_allowed=bool(data.get("execution_allowed")),
        clarification_question=str(data.get("clarification_question") or ""),
        verification_criteria=[str(x) for x in data.get("verification_criteria") or []],
        rationale=[str(x) for x in data.get("rationale") or []],
        memory_context_refs=[x for x in data.get("memory_context_refs") or [] if isinstance(x, dict)],
    )


def _work_order_from_dict(data: dict[str, Any]) -> WorkOrder:
    return WorkOrder(
        work_order_id=str(data.get("work_order_id") or ""),
        decision_id=str(data.get("decision_id") or ""),
        role_agent=str(data.get("role_agent") or "ToolExecutionAgent"),
        task=str(data.get("task") or ""),
        inputs=dict(data.get("inputs") or {}),
        tool_policy=_tool_policy_from_dict(data.get("tool_policy") or {}),
        expected_outputs=[str(x) for x in data.get("expected_outputs") or []],
        verification_criteria=[str(x) for x in data.get("verification_criteria") or []],
        status=str(data.get("status") or "pending"),  # type: ignore[arg-type]
    )
