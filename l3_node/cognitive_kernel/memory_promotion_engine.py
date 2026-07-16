"""Memory promotion and retirement decisions.

This module separates memory lifecycle judgement from storage.  It decides
whether short-lived observations should become durable memory, stay in working
memory, be downranked, or be archived for review.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from .memory_lifecycle import LifecycleMemoryRecord


@dataclass(slots=True)
class MemoryPromotionDecision:
    memory_id: str
    memory_type: str
    action: str
    target_layer: str
    confidence: float
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MemoryPromotionSummary:
    total: int
    promote: int = 0
    keep: int = 0
    downrank: int = 0
    archive: int = 0
    needs_review: int = 0
    decisions: list[MemoryPromotionDecision] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


LONG_TERM_TYPES = {
    "alias",
    "correction",
    "user_preference",
    "safety_preference",
    "project_fact",
    "tool_habit",
    "capability_usage",
    "recovery_playbook_candidate",
}

WORKING_TYPES = {
    "short_term_action",
    "active_task",
    "failure_hint",
    "recent_app_context",
}


def _record_dict(record: LifecycleMemoryRecord | dict[str, Any]) -> dict[str, Any]:
    if isinstance(record, LifecycleMemoryRecord):
        return record.to_dict()
    return dict(record)


def _now_ms() -> int:
    return int(time.time() * 1000)


def decide_memory_promotion(record: LifecycleMemoryRecord | dict[str, Any]) -> MemoryPromotionDecision:
    data = _record_dict(record)
    memory_id = str(data.get("memory_id") or "")
    memory_type = str(data.get("memory_type") or "")
    confidence = float(data.get("confidence") or 0.0)
    success_count = int(data.get("success_count") or 0)
    failure_count = int(data.get("failure_count") or 0)
    hit_count = int(data.get("hit_count") or 0)
    expires_at_ms = int(data.get("expires_at_ms") or 0)
    review_required = bool(data.get("review_required"))
    status = str(data.get("status") or "active")
    layer = str(data.get("layer") or "")
    evidence = {
        "confidence": confidence,
        "success_count": success_count,
        "failure_count": failure_count,
        "hit_count": hit_count,
        "layer": layer,
        "status": status,
    }
    if status in {"archived", "deleted", "expired"}:
        return MemoryPromotionDecision(memory_id, memory_type, "archive", "archive", confidence, "record is no longer active", evidence)
    if expires_at_ms and expires_at_ms <= _now_ms():
        return MemoryPromotionDecision(memory_id, memory_type, "archive", "archive", confidence, "record ttl expired", evidence)
    if review_required:
        return MemoryPromotionDecision(memory_id, memory_type, "needs_review", "review_queue", confidence, "record requires user or governance review", evidence)
    if failure_count >= max(2, success_count + 2):
        return MemoryPromotionDecision(memory_id, memory_type, "downrank", "working_memory", confidence, "failure evidence outweighs success evidence", evidence)
    if confidence < 0.35 and hit_count == 0:
        return MemoryPromotionDecision(memory_id, memory_type, "archive", "archive", confidence, "low confidence and unused", evidence)
    if memory_type in LONG_TERM_TYPES and confidence >= 0.72 and (success_count >= 1 or hit_count >= 2):
        return MemoryPromotionDecision(memory_id, memory_type, "promote_to_long_term", "long_term", confidence, "stable memory type with enough positive evidence", evidence)
    if memory_type in WORKING_TYPES and confidence >= 0.68 and success_count >= 2:
        return MemoryPromotionDecision(memory_id, memory_type, "promote_to_long_term", "long_term", confidence, "repeated working memory has become stable", evidence)
    if memory_type == "failure_hint" and confidence >= 0.6 and failure_count >= 1:
        return MemoryPromotionDecision(memory_id, memory_type, "keep_working", "recovery_memory", confidence, "failure hint should remain available for recovery", evidence)
    if confidence < 0.5:
        return MemoryPromotionDecision(memory_id, memory_type, "downrank", "working_memory", confidence, "confidence below promotion threshold", evidence)
    return MemoryPromotionDecision(memory_id, memory_type, "keep_working", "working_memory", confidence, "not enough evidence for promotion or retirement", evidence)


def evaluate_memory_promotions(records: Iterable[LifecycleMemoryRecord | dict[str, Any]]) -> MemoryPromotionSummary:
    decisions = [decide_memory_promotion(record) for record in records]
    summary = MemoryPromotionSummary(total=len(decisions), decisions=decisions)
    for decision in decisions:
        if decision.action == "promote_to_long_term":
            summary.promote += 1
        elif decision.action == "keep_working":
            summary.keep += 1
        elif decision.action == "downrank":
            summary.downrank += 1
        elif decision.action == "needs_review":
            summary.needs_review += 1
        else:
            summary.archive += 1
    return summary

