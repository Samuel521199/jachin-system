"""Trust-state utilities for Memory-first recall.

The trust layer is intentionally horizontal: memory producers can set explicit
metadata, while recall/ranking/evidence can still infer sane defaults for older
records. This keeps trust semantics out of individual business skills.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any


TRUST_CONFIRMED = "confirmed"
TRUST_FLOATING = "floating"
TRUST_REJECTED = "rejected"
TRUST_CONFLICTED = "conflicted"
TRUST_EXPIRED = "expired"

VALID_TRUST_STATES = {
    TRUST_CONFIRMED,
    TRUST_FLOATING,
    TRUST_REJECTED,
    TRUST_CONFLICTED,
    TRUST_EXPIRED,
}

_REJECT_MARKERS = (
    "user_rejected",
    "explicitly_rejected",
    "explicitly_denied",
    "negative_feedback",
    "do_not_use",
    "do-not-use",
    "wrong_memory",
    "memory_rejected",
    "denied_by_user",
)

_CONFIRM_MARKERS = (
    "user_confirmed",
    "explicitly_confirmed",
    "confirmed_by_user",
    "accepted_by_user",
    "memory_confirmed",
)

_CONFLICT_MARKERS = (
    "conflict",
    "conflicted",
    "contradiction",
    "needs_confirmation",
    "requires_user_confirmation",
)


def normalize_trust_state(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "user_confirmed": TRUST_CONFIRMED,
        "confirmed_by_user": TRUST_CONFIRMED,
        "accepted": TRUST_CONFIRMED,
        "trusted": TRUST_CONFIRMED,
        "system_inferred": TRUST_FLOATING,
        "inferred": TRUST_FLOATING,
        "unverified": TRUST_FLOATING,
        "pending": TRUST_FLOATING,
        "user_rejected": TRUST_REJECTED,
        "denied": TRUST_REJECTED,
        "wrong": TRUST_REJECTED,
        "invalid": TRUST_REJECTED,
        "needs_confirmation": TRUST_CONFLICTED,
        "conflict": TRUST_CONFLICTED,
        "stale": TRUST_EXPIRED,
    }
    raw = aliases.get(raw, raw)
    return raw if raw in VALID_TRUST_STATES else ""


def infer_memory_trust(value: Any, *, now_ms: int | None = None) -> tuple[str, str]:
    explicit = _first_non_empty(
        _get(value, "trust_state"),
        _get(value, "memory_trust_state"),
        _get(value, "user_attitude"),
    )
    explicit_state = normalize_trust_state(explicit)
    if explicit_state and explicit_state != TRUST_FLOATING:
        return explicit_state, f"explicit:{explicit_state}"

    text = _trust_search_text(value)
    if any(marker in text for marker in _REJECT_MARKERS):
        return TRUST_REJECTED, "inferred:user_rejected_marker"

    status = str(_get(value, "status") or "").strip().lower()
    expires_at = _safe_int(_get(value, "expires_at_ms"))
    if status == "expired" or (expires_at and expires_at < (now_ms or int(time.time() * 1000))):
        return TRUST_EXPIRED, "inferred:expired"

    if bool(_get(value, "confirmed_by_user")) or any(marker in text for marker in _CONFIRM_MARKERS):
        return TRUST_CONFIRMED, "inferred:user_confirmed"

    if bool(_get(value, "review_required")) or bool(_get(value, "requires_user_confirmation")):
        if any(marker in text for marker in _CONFLICT_MARKERS):
            return TRUST_CONFLICTED, "inferred:conflict_or_confirmation_required"
        return TRUST_FLOATING, "inferred:needs_user_confirmation"

    if any(marker in text for marker in _CONFLICT_MARKERS):
        return TRUST_CONFLICTED, "inferred:conflict_marker"

    if explicit_state == TRUST_FLOATING:
        return TRUST_FLOATING, "explicit:floating"

    return TRUST_FLOATING, "default:system_inferred"


def trust_weight(trust_state: str) -> float:
    state = normalize_trust_state(trust_state) or TRUST_FLOATING
    return {
        TRUST_CONFIRMED: 1.18,
        TRUST_FLOATING: 1.0,
        TRUST_CONFLICTED: 0.55,
        TRUST_EXPIRED: 0.35,
        TRUST_REJECTED: 0.03,
    }[state]


def should_recall_memory(value: Any, *, include_rejected: bool = False) -> bool:
    state, _reason = infer_memory_trust(value)
    if state == TRUST_REJECTED and not include_rejected:
        return False
    recall_allowed = _get(value, "recall_allowed")
    if recall_allowed is False and not include_rejected:
        return False
    return True


def decorate_memory_evidence(item: Any, *, reason_prefix: str = "memory trust") -> Any:
    state, reason = infer_memory_trust(item)
    weight = trust_weight(state)
    confidence = max(0.0, min(1.0, float(_get(item, "confidence") or 0.0) * weight))
    relevance = str(_get(item, "relevance_reason") or "").strip()
    trust_note = f"{reason_prefix}: state={state} weight={weight:.2f} reason={reason}"
    if trust_note not in relevance:
        relevance = f"{relevance}; {trust_note}" if relevance else trust_note
    try:
        return replace(
            item,
            confidence=confidence,
            confirmed_by_user=bool(_get(item, "confirmed_by_user")) or state == TRUST_CONFIRMED,
            trust_state=state,
            trust_reason=reason,
            user_attitude=state,
            recall_allowed=state != TRUST_REJECTED,
            relevance_reason=relevance,
        )
    except Exception:
        for name, value in {
            "confidence": confidence,
            "confirmed_by_user": bool(_get(item, "confirmed_by_user")) or state == TRUST_CONFIRMED,
            "trust_state": state,
            "trust_reason": reason,
            "user_attitude": state,
            "recall_allowed": state != TRUST_REJECTED,
            "relevance_reason": relevance,
        }.items():
            try:
                setattr(item, name, value)
            except Exception:
                pass
        return item


def trust_score_detail(item: Any) -> dict[str, Any]:
    state, reason = infer_memory_trust(item)
    return {
        "memory_trust_state": state,
        "memory_trust_weight": round(trust_weight(state), 3),
        "memory_trust_reason": reason,
        "memory_recall_allowed": should_recall_memory(item),
    }


def lifecycle_record_trust_defaults(value: Any) -> dict[str, Any]:
    state, reason = infer_memory_trust(value)
    return {
        "trust_state": state,
        "trust_reason": reason,
        "user_attitude": state,
        "recall_allowed": state != TRUST_REJECTED,
    }


def memory_requires_confirmation(value: Any) -> bool:
    state, _reason = infer_memory_trust(value)
    return state == TRUST_CONFLICTED


def _get(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if str(value or "").strip():
            return value
    return ""


def _trust_search_text(value: Any) -> str:
    chunks: list[str] = []
    if isinstance(value, dict):
        chunks.extend(str(v) for v in value.values())
    else:
        for name in (
            "source_event",
            "memory_type",
            "content",
            "review_reason",
            "trust_reason",
            "relevance_reason",
            "tags",
            "evidence",
        ):
            chunks.append(str(getattr(value, name, "") or ""))
    return " ".join(chunks).lower()


def _safe_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except Exception:
        return 0
