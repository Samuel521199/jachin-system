"""Shared confidence and governance rules for Cognitive Kernel memories.

The memory system has a fast runtime index and a durable lifecycle index. This
module keeps the quality policy in one place so aliases, corrections, task
state, skill memory, and future MCP memories do not each invent their own
confidence math.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class MemoryFeedbackUpdate:
    confidence: float
    review_required: bool
    review_reason: str
    last_verified_at_ms: int


def clamp_confidence(value: float) -> float:
    return max(0.0, min(0.99, float(value or 0.0)))


def classify_memory_layer(memory_type: str, ttl: str = "") -> str:
    memory_type = str(memory_type or "").strip()
    ttl = str(ttl or "").strip().lower()
    if ttl in {"turn"}:
        return "turn"
    if ttl in {"session"}:
        return "session"
    if memory_type in {"recent_work_order", "short_term_action", "conversation_short_term", "state_task", "state_resource"}:
        return "working"
    if memory_type in {"task_state", "failure_hint", "historical_task_summary"}:
        return "working"
    if memory_type in {"user_preference", "safety_preference", "alias", "correction", "project_fact", "tool_habit", "capability_usage"}:
        return "long_term"
    if ttl in {"permanent", "forever", "long_term"}:
        return "long_term"
    return "working"


def extract_memory_scope(evidence: list[dict[str, Any]] | None) -> dict[str, str]:
    scope = {"domain": "global", "owner": "user", "skill_id": ""}
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        for key in ("domain", "owner", "skill_id"):
            value = str(item.get(key) or "").strip()
            if value:
                scope[key] = value
    return scope


def initial_confidence(*, requested: float, memory_type: str, evidence: list[dict[str, Any]] | None, requires_user_confirmation: bool) -> float:
    ok_count = sum(1 for item in evidence or [] if isinstance(item, dict) and item.get("ok") is True)
    fail_count = sum(1 for item in evidence or [] if isinstance(item, dict) and item.get("ok") is False)
    base = clamp_confidence(requested)
    if base <= 0:
        base = {
            "user_preference": 0.82,
            "safety_preference": 0.86,
            "alias": 0.78,
            "correction": 0.78,
            "project_fact": 0.72,
            "tool_habit": 0.68,
            "historical_task_summary": 0.62,
            "failure_hint": 0.66,
            "task_state": 0.62,
            "short_term_action": 0.58,
        }.get(str(memory_type or ""), 0.55)
    if ok_count:
        base += min(0.12, ok_count * 0.04)
    if fail_count:
        base -= min(0.24, fail_count * 0.08)
    if requires_user_confirmation:
        base = min(base, 0.62)
    return clamp_confidence(base)


def apply_feedback(
    *,
    confidence: float,
    success_count: int,
    failure_count: int,
    ok: bool,
    now_ms: int,
    failure_reason: str = "",
) -> MemoryFeedbackUpdate:
    confidence = clamp_confidence(confidence)
    if ok:
        next_confidence = clamp_confidence(max(confidence, 0.82) + min(0.04, 0.015 + success_count * 0.002))
        review_required = False if failure_count <= 0 else failure_count > success_count + 2
        review_reason = "" if not review_required else "historical_failures_exceed_successes"
        return MemoryFeedbackUpdate(
            confidence=next_confidence,
            review_required=review_required,
            review_reason=review_reason,
            last_verified_at_ms=now_ms,
        )
    next_confidence = clamp_confidence(confidence - min(0.35, 0.12 + failure_count * 0.04))
    review_required = failure_count >= 2 or failure_count > success_count or next_confidence < 0.5
    return MemoryFeedbackUpdate(
        confidence=next_confidence,
        review_required=review_required,
        review_reason=(failure_reason or "memory_feedback_failure") if review_required else "",
        last_verified_at_ms=0,
    )


def recall_score(
    *,
    text_hits: int,
    confidence: float,
    hit_count: int,
    success_count: int,
    failure_count: int,
    review_required: bool,
    layer: str,
    recency_hint: float = 0.0,
) -> float:
    if text_hits <= 0:
        return 0.0
    success_total = max(1, success_count + failure_count)
    reliability = (success_count - failure_count) / success_total
    layer_boost = {
        "turn": 0.8,
        "session": 0.6,
        "working": 0.35,
        "long_term": 0.2,
    }.get(str(layer or ""), 0.2)
    review_penalty = 3.0 if review_required else 0.0
    return (
        text_hits * 10
        + clamp_confidence(confidence) * 2.0
        + min(hit_count, 8) * 0.12
        + reliability
        + layer_boost
        + recency_hint
        - review_penalty
    )
