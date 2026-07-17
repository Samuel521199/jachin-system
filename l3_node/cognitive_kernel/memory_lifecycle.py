"""Memory lifecycle store for Cognitive Kernel recall/write-back.

This is a local lifecycle index in front of Memory Nexus. It handles the parts
the kernel needs deterministically: classification, dedupe, TTL expiry, merge
metadata, and conflict-friendly reads. The semantic Memory Nexus can still be
used for vector search; this store keeps the operational memory lifecycle sane.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .contracts import MemoryEvidence, MemoryWriteRequest
from .ledger import append_event
from .memory_confidence import apply_feedback, classify_memory_layer, extract_memory_scope, initial_confidence, recall_score
from .memory_trust import (
    TRUST_CONFIRMED,
    TRUST_CONFLICTED,
    TRUST_FLOATING,
    TRUST_REJECTED,
    decorate_memory_evidence,
    infer_memory_trust,
    lifecycle_record_trust_defaults,
    should_recall_memory,
    trust_weight,
)
from .paths import kernel_home


def _now_ms() -> int:
    return int(time.time() * 1000)


def _store_path() -> Path:
    path = kernel_home() / "memory" / "memory_lifecycle.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _index_path() -> Path:
    path = kernel_home() / "memory" / "memory_lifecycle_index.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _governance_index_path() -> Path:
    path = kernel_home() / "memory" / "memory_lifecycle_governance.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


REVIEWABLE_MEMORY_TYPES = {
    "alias",
    "correction",
    "user_preference",
    "safety_preference",
    "project_fact",
    "tool_habit",
    "capability_usage",
    "failure_hint",
}


_RECORD_CACHE: dict[str, Any] = {
    "path": "",
    "mtime_ns": -1,
    "size": -1,
    "records": [],
    "search_index": None,
    "rerank_vectors": {},
}

_COMMON_RECALL_TERMS = {
    "and",
    "but",
    "content",
    "domain",
    "memory",
    "noise",
    "not",
    "owner",
    "recall",
    "similar",
    "stress",
    "target",
    "the",
    "this",
    "token",
    "wording",
}
_MAX_INDEX_POSTINGS_FOR_CANDIDATE = 250_000
_RERANK_WINDOW = 64
_RERANK_HASH_DIM = 384
_RERANK_DOT_WEIGHT = 3.0


@dataclass(slots=True)
class LifecycleMemoryRecord:
    memory_id: str
    memory_type: str
    content: str
    source_event: str = ""
    confidence: float = 0.0
    ttl: str = ""
    expires_at_ms: int = 0
    created_at_ms: int = 0
    updated_at_ms: int = 0
    hit_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    last_verified_at_ms: int = 0
    review_required: bool = False
    review_reason: str = ""
    layer: str = ""
    domain: str = "global"
    owner: str = "user"
    skill_id: str = ""
    content_hash: str = ""
    tags: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    status: str = "active"
    merge_policy: str = "dedupe_and_merge"
    trust_state: str = "floating"
    trust_reason: str = ""
    user_attitude: str = "floating"
    recall_allowed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "memory_type": self.memory_type,
            "content": self.content,
            "source_event": self.source_event,
            "confidence": self.confidence,
            "ttl": self.ttl,
            "expires_at_ms": self.expires_at_ms,
            "created_at_ms": self.created_at_ms,
            "updated_at_ms": self.updated_at_ms,
            "hit_count": self.hit_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "last_verified_at_ms": self.last_verified_at_ms,
            "review_required": self.review_required,
            "review_reason": self.review_reason,
            "layer": self.layer,
            "domain": self.domain,
            "owner": self.owner,
            "skill_id": self.skill_id,
            "content_hash": self.content_hash,
            "tags": list(self.tags),
            "evidence": list(self.evidence),
            "status": self.status,
            "merge_policy": self.merge_policy,
            "trust_state": self.trust_state,
            "trust_reason": self.trust_reason,
            "user_attitude": self.user_attitude,
            "recall_allowed": self.recall_allowed,
        }

    def to_evidence(self, reason: str = "memory lifecycle recall") -> MemoryEvidence:
        evidence = MemoryEvidence(
            memory_id=self.memory_id,
            memory_type=self.memory_type,
            content=self.content,
            source="MemoryLifecycle",
            created_at=str(self.created_at_ms),
            updated_at=str(self.updated_at_ms),
            confidence=self.confidence,
            confirmed_by_user=self.memory_type in {"user_preference", "alias", "correction"},
            ttl=self.ttl,
            relevance_reason=reason,
            trust_state=self.trust_state,
            trust_reason=self.trust_reason,
            user_attitude=self.user_attitude,
            recall_allowed=self.recall_allowed,
        )
        return decorate_memory_evidence(evidence, reason_prefix="memory lifecycle trust")


def classify_memory_type(content: str, requested: str = "") -> str:
    requested = (requested or "").strip()
    if requested:
        return requested
    hay = (content or "").lower()
    if any(x in hay for x in ("prefers", "偏好", "习惯", "以后都", "默认")):
        return "user_preference"
    if any(x in hay for x in ("alias", "别名", "也叫", "项目路径", "=", "就是")):
        return "alias"
    if any(x in hay for x in ("correction", "纠错", "不是", "应该是", "不要再")):
        return "correction"
    if any(x in hay for x in ("failed", "failure", "失败", "timeout", "recovery")):
        return "failure_hint"
    if any(x in hay for x in ("task", "progress", "后台", "阶段", "继续")):
        return "task_state"
    return "short_term_action"


def ttl_to_expiry_ms(ttl: str, memory_type: str) -> int:
    ttl = (ttl or "").strip().lower()
    now = _now_ms()
    if ttl in {"", "default"}:
        ttl = {
            "short_term_action": "7d",
            "task_state": "30d",
            "failure_hint": "14d",
            "user_preference": "permanent",
            "alias": "permanent",
            "correction": "permanent",
        }.get(memory_type, "14d")
    if ttl in {"permanent", "forever", "long_term"}:
        return 0
    if ttl in {"turn", "session"}:
        return now + 24 * 3600 * 1000
    units = {"ms": 1, "s": 1000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}
    for suffix, scale in units.items():
        if ttl.endswith(suffix):
            try:
                return now + int(float(ttl[: -len(suffix)]) * scale)
            except ValueError:
                break
    return now + 14 * 86_400_000


def write_lifecycle_memory(request: MemoryWriteRequest) -> LifecycleMemoryRecord:
    memory_type = classify_memory_type(request.content, request.memory_type)
    now = _now_ms()
    content_hash = _content_hash(memory_type, request.content)
    records = _load_records(include_expired=True)
    existing = next((r for r in records if r.content_hash == content_hash and r.status == "active"), None)
    scope = extract_memory_scope(request.evidence)
    layer = classify_memory_layer(memory_type, request.ttl)
    confidence = initial_confidence(
        requested=float(request.confidence or 0.0),
        memory_type=memory_type,
        evidence=request.evidence,
        requires_user_confirmation=bool(request.requires_user_confirmation),
    )
    trust_defaults = lifecycle_record_trust_defaults(request)
    request_trust_state = request.trust_state or trust_defaults["trust_state"]
    request_trust_reason = request.trust_reason or trust_defaults["trust_reason"]
    request_user_attitude = request.user_attitude or trust_defaults["user_attitude"]
    request_recall_allowed = bool(trust_defaults["recall_allowed"])
    trust_prior = _trust_prior_for_write(
        records=records,
        memory_type=memory_type,
        content=request.content,
        scope=scope,
        evidence=request.evidence,
    )
    request_review_required = bool(request.requires_user_confirmation)
    request_review_reason = "requires_user_confirmation" if request.requires_user_confirmation else ""
    if trust_prior["matched_count"]:
        confidence = _confidence_with_trust_prior(confidence, trust_prior)
    if (
        trust_prior["recommended_state"] == TRUST_CONFLICTED
        and request_trust_state != TRUST_CONFIRMED
    ):
        request_trust_state = TRUST_CONFLICTED
        request_trust_reason = trust_prior["reason"]
        request_user_attitude = TRUST_CONFLICTED
        request_recall_allowed = True
        request_review_required = True
        request_review_reason = "similar_memory_trust_governance_requires_confirmation"
    elif (
        trust_prior["recommended_state"] == TRUST_CONFIRMED
        and request_trust_state == TRUST_FLOATING
    ):
        request_trust_state = TRUST_CONFIRMED
        request_trust_reason = trust_prior["reason"]
        request_user_attitude = TRUST_CONFIRMED
        request_recall_allowed = True
    prior_evidence = _trust_prior_evidence(trust_prior)
    request_evidence = list(request.evidence or [])
    if prior_evidence:
        request_evidence.append(prior_evidence)
    if existing and request.merge_policy in {"dedupe_and_merge", "append_action_chain"}:
        existing_state, _existing_reason = infer_memory_trust(existing)
        if existing_state == TRUST_REJECTED and request_trust_state != "confirmed":
            existing.review_required = True
            existing.review_reason = "new_memory_conflicts_with_user_rejected_memory"
            existing.trust_state = "conflicted"
            existing.trust_reason = "existing_rejected_memory_requires_user_confirmation"
            existing.user_attitude = "conflicted"
            existing.recall_allowed = True
        existing.updated_at_ms = now
        existing.hit_count += 1
        existing.confidence = max(existing.confidence, confidence)
        existing.evidence.extend(request_evidence)
        existing.ttl = request.ttl or existing.ttl
        existing.expires_at_ms = ttl_to_expiry_ms(existing.ttl, memory_type)
        existing.layer = existing.layer or layer
        existing.domain = existing.domain or scope["domain"]
        existing.owner = existing.owner or scope["owner"]
        existing.skill_id = existing.skill_id or scope["skill_id"]
        if request_trust_state == "confirmed" or not existing.trust_state:
            existing.trust_state = request_trust_state
            existing.trust_reason = request_trust_reason
            existing.user_attitude = request_user_attitude
            existing.recall_allowed = request_recall_allowed
        if request_review_required and existing.trust_state != TRUST_CONFIRMED:
            existing.review_required = True
            existing.review_reason = request_review_reason
        record = existing
        action = "merged"
    else:
        record = LifecycleMemoryRecord(
            memory_id=f"mem_{uuid.uuid4().hex[:16]}",
            memory_type=memory_type,
            content=request.content,
            source_event=request.source_event,
            confidence=confidence,
            ttl=request.ttl or "default",
            expires_at_ms=ttl_to_expiry_ms(request.ttl, memory_type),
            created_at_ms=now,
            updated_at_ms=now,
            hit_count=1,
            success_count=_evidence_success_count(request_evidence),
            failure_count=_evidence_failure_count(request_evidence),
            last_verified_at_ms=now if _evidence_success_count(request_evidence) else 0,
            review_required=request_review_required,
            review_reason=request_review_reason,
            layer=layer,
            domain=scope["domain"],
            owner=scope["owner"],
            skill_id=scope["skill_id"],
            content_hash=content_hash,
            tags=[memory_type, request.source_event],
            evidence=request_evidence,
            merge_policy=request.merge_policy,
            trust_state=request_trust_state,
            trust_reason=request_trust_reason,
            user_attitude=request_user_attitude,
            recall_allowed=request_recall_allowed,
        )
        records.append(record)
        action = "created"
    _rewrite_records(records)
    append_event(
        "memory_lifecycle_write",
        request.turn_id,
        {
            "action": action,
            "memory_id": record.memory_id,
            "memory_type": record.memory_type,
            "ttl": record.ttl,
            "expires_at_ms": record.expires_at_ms,
            "content_hash": record.content_hash,
            "trust_state": record.trust_state,
            "trust_reason": record.trust_reason,
            "recall_allowed": record.recall_allowed,
            "trust_prior": trust_prior,
        },
    )
    return record


def recall_lifecycle_memories(query: str = "", *, memory_types: list[str] | None = None, limit: int = 8) -> list[MemoryEvidence]:
    query_terms = _query_terms(query)
    wanted = set(memory_types or [])
    now = _now_ms()
    all_records = _load_records(include_expired=True)
    records = _candidate_records(all_records, query_terms, wanted)
    active_records = []
    for record in records:
        if record.status != "active":
            continue
        if not should_recall_memory(record):
            continue
        if record.expires_at_ms and record.expires_at_ms < now:
            record.status = "expired"
            record.trust_state = "expired"
            record.trust_reason = "expired_during_recall"
            continue
        active_records.append(record)
    rule_scored = [
        (record, score)
        for record in active_records
        if (score := _score_record(record, query_terms)) > 0
    ]
    rule_scored.sort(key=lambda item: (item[1], item[0].updated_at_ms), reverse=True)
    reranked = _normalized_dot_rerank(query, rule_scored[: max(limit, _RERANK_WINDOW)])
    out = [
        item.to_evidence("memory lifecycle recall: inverted-index -> rule-score -> normalized-dot-rerank")
        for item, _final_score, _rule_score, _dot_score in reranked[: max(0, limit)]
    ]
    append_event(
        "memory_lifecycle_recall",
        "memory-recall",
        {
            "query": query[:300],
            "memory_types": list(wanted),
            "layer_1": "inverted_index_keyword_candidate_recall",
            "layer_2": "rule_score_coarse_rank",
            "layer_3": "normalized_dot_product_rerank",
            "candidate_count": len(records),
            "active_candidate_count": len(active_records),
            "rule_scored_count": len(rule_scored),
            "rerank_window": min(len(rule_scored), max(limit, _RERANK_WINDOW)),
            "hit_count": len(out),
            "trust_filter": "rejected memories excluded by default",
        },
    )
    return out


def warm_lifecycle_memory_index() -> dict[str, Any]:
    started = time.perf_counter()
    records = _load_records(include_expired=True)
    index = _search_index(records)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    term_count = sum(len(term_index) for term_index in index.values())
    posting_count = sum(len(postings) for term_index in index.values() for postings in term_index.values())
    summary = {
        "record_count": len(records),
        "memory_type_count": len(index),
        "term_count": term_count,
        "posting_count": posting_count,
        "elapsed_ms": elapsed_ms,
    }
    append_event("memory_lifecycle_index_warm", "memory-lifecycle", summary)
    return summary


def record_lifecycle_memory_feedback(
    *,
    memory_type: str,
    content: str,
    ok: bool,
    turn_id: str = "",
    failure_reason: str = "",
) -> LifecycleMemoryRecord | None:
    content_hash = _content_hash(memory_type, content)
    records = _load_records(include_expired=True)
    record = next((r for r in records if r.content_hash == content_hash and r.status == "active"), None)
    if record is None:
        return None
    now = _now_ms()
    record.updated_at_ms = now
    record.hit_count += 1
    if ok:
        record.success_count += 1
        update = apply_feedback(
            confidence=record.confidence,
            success_count=record.success_count,
            failure_count=record.failure_count,
            ok=True,
            now_ms=now,
            failure_reason=failure_reason,
        )
        record.last_verified_at_ms = update.last_verified_at_ms
        record.confidence = update.confidence
        record.review_required = update.review_required
        record.review_reason = update.review_reason
    else:
        record.failure_count += 1
        update = apply_feedback(
            confidence=record.confidence,
            success_count=record.success_count,
            failure_count=record.failure_count,
            ok=False,
            now_ms=now,
            failure_reason=failure_reason,
        )
        record.confidence = update.confidence
        record.review_required = update.review_required
        record.review_reason = update.review_reason
    _rewrite_records(records)
    append_event(
        "memory_lifecycle_feedback",
        turn_id or "memory-lifecycle",
        {
            "memory_id": record.memory_id,
            "memory_type": record.memory_type,
            "content_hash": record.content_hash,
            "ok": bool(ok),
            "success_count": record.success_count,
            "failure_count": record.failure_count,
            "confidence": record.confidence,
            "review_required": record.review_required,
            "review_reason": record.review_reason,
        },
    )
    return record


def govern_lifecycle_memory(
    *,
    memory_id: str,
    action: str,
    note: str = "",
    corrected_content: str = "",
) -> dict[str, Any]:
    """Apply explicit human governance to one lifecycle memory.

    This is the write side of Memory Trust Layer. It keeps rejected memories in
    the ledger for auditability, but marks them as non-recallable by default.
    """

    memory_id = (memory_id or "").strip()
    action = (action or "").strip()
    if not memory_id:
        raise ValueError("memory governance requires memory_id")
    if action not in {"confirm", "reject", "mark_conflicted", "correct"}:
        raise ValueError(f"unsupported memory governance action: {action}")

    records = _load_records(include_expired=True)
    record = next((item for item in records if item.memory_id == memory_id), None)
    if record is None:
        raise ValueError(f"memory not found: {memory_id}")

    now = _now_ms()
    before = record.to_dict()
    record.updated_at_ms = now
    governance_evidence = {
        "type": "memory_trust_governance",
        "action": action,
        "note": note,
        "ts_ms": now,
    }

    if action == "confirm":
        record.trust_state = TRUST_CONFIRMED
        record.trust_reason = "user_confirmed_from_console"
        record.user_attitude = TRUST_CONFIRMED
        record.recall_allowed = True
        record.review_required = False
        record.review_reason = ""
        record.last_verified_at_ms = now
        record.success_count += 1
        record.confidence = max(record.confidence, 0.82)
    elif action == "reject":
        record.trust_state = TRUST_REJECTED
        record.trust_reason = "user_rejected_from_console"
        record.user_attitude = TRUST_REJECTED
        record.recall_allowed = False
        record.review_required = False
        record.review_reason = ""
        record.failure_count += 1
        record.confidence = min(record.confidence, 0.08)
    elif action == "mark_conflicted":
        record.trust_state = TRUST_CONFLICTED
        record.trust_reason = "user_marked_conflicted_from_console"
        record.user_attitude = TRUST_CONFLICTED
        record.recall_allowed = True
        record.review_required = True
        record.review_reason = "memory_trust_conflict_needs_user_confirmation"
        record.confidence = min(record.confidence, 0.5)
    elif action == "correct":
        corrected = (corrected_content or "").strip()
        if not corrected:
            raise ValueError("correct memory governance requires corrected_content")
        record.content = corrected
        record.content_hash = _content_hash(record.memory_type, corrected)
        record.trust_state = TRUST_CONFIRMED
        record.trust_reason = "user_corrected_from_console"
        record.user_attitude = TRUST_CONFIRMED
        record.recall_allowed = True
        record.review_required = False
        record.review_reason = ""
        record.last_verified_at_ms = now
        record.success_count += 1
        record.confidence = max(record.confidence, 0.86)
        governance_evidence["corrected_content_hash"] = record.content_hash

    if not record.trust_state:
        record.trust_state = TRUST_FLOATING
    record.evidence.append(governance_evidence)
    _rewrite_records(records)
    after = record.to_dict()
    append_event(
        "memory_lifecycle_trust_governance",
        "memory-governance",
        {
            "memory_id": memory_id,
            "memory_type": record.memory_type,
            "action": action,
            "trust_state": record.trust_state,
            "trust_reason": record.trust_reason,
            "recall_allowed": record.recall_allowed,
            "note": note,
        },
    )
    return {"before": before, "after": after}


def expire_lifecycle_memories() -> int:
    now = _now_ms()
    records = _load_records(include_expired=True)
    n = 0
    for record in records:
        if record.status == "active" and record.expires_at_ms and record.expires_at_ms < now:
            record.status = "expired"
            record.updated_at_ms = now
            n += 1
    if n:
        _rewrite_records(records)
        append_event("memory_lifecycle_expired", "memory-lifecycle", {"expired_count": n})
    return n


def govern_lifecycle_memories(
    *,
    low_confidence_threshold: float = 0.45,
    stale_after_days: int = 30,
    failure_review_threshold: int = 2,
) -> dict[str, Any]:
    """Run deterministic quality governance over the lifecycle memory index.

    The runtime writes memories fast; this pass is the slower curator that marks
    suspicious items for review without deleting useful context. It is designed
    to be safe under duplicate storms and corrupt raw lines.
    """

    now = _now_ms()
    stale_after_ms = max(1, stale_after_days) * 86_400_000
    records = _load_records(include_expired=True)
    invalid_raw_line_count = _invalid_raw_line_count()
    summary: dict[str, Any] = {
        "updated_at_ms": now,
        "total_count": len(records),
        "active_count": sum(1 for r in records if r.status == "active"),
        "invalid_raw_line_count": invalid_raw_line_count,
        "low_confidence_count": 0,
        "failure_pressure_count": 0,
        "stale_unverified_count": 0,
        "conflict_count": 0,
        "expired_count": 0,
        "review_required_count": 0,
        "review_reasons": {},
        "types": {},
    }

    changed = False
    conflict_ids = _detect_conflict_memory_ids(records)
    for record in records:
        summary["types"][record.memory_type] = int(summary["types"].get(record.memory_type, 0)) + 1
        if record.status == "active" and record.expires_at_ms and record.expires_at_ms < now:
            record.status = "expired"
            record.updated_at_ms = now
            summary["expired_count"] += 1
            changed = True
            continue
        if record.status != "active":
            continue
        reason = ""
        if record.memory_id in conflict_ids:
            reason = "memory_conflict"
            summary["conflict_count"] += 1
        elif record.confidence < low_confidence_threshold:
            reason = f"low_confidence:{record.confidence:.2f}"
            summary["low_confidence_count"] += 1
        elif record.failure_count >= failure_review_threshold and record.failure_count >= record.success_count:
            reason = "failure_pressure"
            summary["failure_pressure_count"] += 1
        elif _is_stale_unverified(record, now=now, stale_after_ms=stale_after_ms):
            reason = "stale_unverified"
            summary["stale_unverified_count"] += 1
        if reason:
            if not record.review_required or record.review_reason != reason:
                record.review_required = True
                record.review_reason = reason
                record.updated_at_ms = now
                changed = True
        if record.review_required:
            summary["review_required_count"] += 1
            summary["review_reasons"][record.review_reason or "review_required"] = int(
                summary["review_reasons"].get(record.review_reason or "review_required", 0)
            ) + 1

    if changed:
        _rewrite_records(records)
    _governance_index_path().write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    append_event("memory_lifecycle_governance", "memory-lifecycle", summary)
    return summary


def pending_lifecycle_review_items(*, limit: int = 50) -> list[dict[str, Any]]:
    records = [
        record
        for record in _load_records(include_expired=False)
        if record.review_required and record.status == "active"
    ]
    records.sort(key=lambda r: (r.confidence, -r.failure_count, r.updated_at_ms))
    items = []
    for record in records[: max(0, limit)]:
        items.append(
            {
                "memory_id": record.memory_id,
                "memory_type": record.memory_type,
                "content": record.content,
                "confidence": record.confidence,
                "review_reason": record.review_reason,
                "success_count": record.success_count,
                "failure_count": record.failure_count,
                "layer": record.layer,
                "domain": record.domain,
                "owner": record.owner,
                "skill_id": record.skill_id,
                "updated_at_ms": record.updated_at_ms,
            }
        )
    return items


def memory_quality_snapshot() -> dict[str, Any]:
    records = _load_records(include_expired=True)
    governance = {}
    if _governance_index_path().exists():
        try:
            governance = json.loads(_governance_index_path().read_text(encoding="utf-8"))
        except Exception:
            governance = {"error": "governance_index_unreadable"}
    return {
        "updated_at_ms": _now_ms(),
        "total_count": len(records),
        "active_count": sum(1 for r in records if r.status == "active"),
        "expired_count": sum(1 for r in records if r.status == "expired"),
        "review_required_count": sum(1 for r in records if r.status == "active" and r.review_required),
        "invalid_raw_line_count": _invalid_raw_line_count(),
        "pending_review": pending_lifecycle_review_items(limit=20),
        "governance": governance,
    }


def _trust_prior_for_write(
    *,
    records: list[LifecycleMemoryRecord],
    memory_type: str,
    content: str,
    scope: dict[str, str],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    query_terms = set(_query_terms(content))
    governance_key = _governance_key_from_evidence(memory_type, scope, evidence)
    confirmed_strength = 0.0
    rejected_strength = 0.0
    conflicted_strength = 0.0
    matched: list[dict[str, Any]] = []
    for record in records:
        if record.status != "active" or record.memory_type != memory_type:
            continue
        if record.domain and scope.get("domain") and record.domain != scope.get("domain"):
            continue
        similarity = 0.0
        match_reason = ""
        if governance_key and _governance_key(record) == governance_key:
            similarity = 1.0
            match_reason = "governance_key"
        else:
            record_terms = set(_query_terms(record.content))
            if query_terms and record_terms:
                overlap = len(query_terms & record_terms)
                similarity = overlap / max(1, min(len(query_terms), len(record_terms)))
                match_reason = "term_overlap"
        if similarity < 0.42:
            continue
        state, reason = infer_memory_trust(record)
        strength = similarity * max(1.0, float(record.success_count + record.failure_count + 1))
        if state == TRUST_CONFIRMED:
            confirmed_strength += strength
        elif state == TRUST_REJECTED:
            rejected_strength += strength
        elif state == TRUST_CONFLICTED:
            conflicted_strength += strength
        matched.append(
            {
                "memory_id": record.memory_id,
                "trust_state": state,
                "trust_reason": reason,
                "similarity": round(similarity, 3),
                "match_reason": match_reason,
                "strength": round(strength, 3),
            }
        )
    recommended_state = ""
    reason = ""
    requires_confirmation = False
    confidence_delta = 0.0
    if rejected_strength >= 1.5 and rejected_strength >= confirmed_strength + 0.75:
        recommended_state = TRUST_CONFLICTED
        requires_confirmation = True
        confidence_delta = -0.22
        reason = "trust_prior:similar_memory_repeatedly_rejected"
    elif conflicted_strength >= 1.5 and conflicted_strength >= confirmed_strength:
        recommended_state = TRUST_CONFLICTED
        requires_confirmation = True
        confidence_delta = -0.12
        reason = "trust_prior:similar_memory_conflicted"
    elif confirmed_strength >= 2.0 and confirmed_strength >= rejected_strength + 0.75:
        recommended_state = TRUST_CONFIRMED
        confidence_delta = 0.12
        reason = "trust_prior:similar_memory_confirmed_by_user"
    return {
        "matched_count": len(matched),
        "confirmed_strength": round(confirmed_strength, 3),
        "rejected_strength": round(rejected_strength, 3),
        "conflicted_strength": round(conflicted_strength, 3),
        "recommended_state": recommended_state,
        "requires_confirmation": requires_confirmation,
        "confidence_delta": confidence_delta,
        "reason": reason,
        "matches": matched[:8],
    }


def _confidence_with_trust_prior(confidence: float, prior: dict[str, Any]) -> float:
    try:
        delta = float(prior.get("confidence_delta") or 0.0)
    except Exception:
        delta = 0.0
    return max(0.01, min(0.99, float(confidence or 0.0) + delta))


def _trust_prior_evidence(prior: dict[str, Any]) -> dict[str, Any]:
    if not int(prior.get("matched_count") or 0):
        return {}
    return {
        "type": "memory_trust_prior",
        "recommended_state": prior.get("recommended_state") or "",
        "requires_confirmation": bool(prior.get("requires_confirmation")),
        "confidence_delta": prior.get("confidence_delta") or 0.0,
        "reason": prior.get("reason") or "trust_prior:similar_memory_seen",
        "confirmed_strength": prior.get("confirmed_strength") or 0.0,
        "rejected_strength": prior.get("rejected_strength") or 0.0,
        "conflicted_strength": prior.get("conflicted_strength") or 0.0,
        "matched_count": prior.get("matched_count") or 0,
        "matches": prior.get("matches") or [],
    }


def _score_record(record: LifecycleMemoryRecord, query_terms: list[str]) -> float:
    if not query_terms:
        hits = 1
    else:
        hay = _record_search_text(record)
        hits = sum(1 for term in query_terms if term in hay)
        if hits <= 0:
            return 0.0
    recency = min(1.0, max(0.0, record.updated_at_ms / max(1, _now_ms())))
    base = recall_score(
        text_hits=hits,
        confidence=record.confidence,
        hit_count=record.hit_count,
        success_count=record.success_count,
        failure_count=record.failure_count,
        review_required=record.review_required,
        layer=record.layer,
        recency_hint=recency,
    )
    return base * trust_weight(record.trust_state)


def _normalized_dot_rerank(
    query: str,
    scored_records: list[tuple[LifecycleMemoryRecord, float]],
) -> list[tuple[LifecycleMemoryRecord, float, float, float]]:
    """Rerank the coarse candidates with normalized dot product.

    Layer 1 and 2 deliberately keep recall cheap. This layer only sees a small
    coarse-ranked window, so we get a semantic-ish tie breaker without turning
    million-scale recall into a full vector scan.
    """

    if not scored_records:
        return []
    query_vector = _normalized_hash_vector(query)
    if not query_vector:
        return [(record, rule_score, rule_score, 0.0) for record, rule_score in scored_records]
    reranked: list[tuple[LifecycleMemoryRecord, float, float, float]] = []
    for record, rule_score in scored_records:
        dot_score = _dot_product(query_vector, _record_rerank_vector(record))
        final_score = rule_score + _RERANK_DOT_WEIGHT * dot_score
        reranked.append((record, final_score, rule_score, dot_score))
    reranked.sort(key=lambda item: (item[1], item[2], item[0].updated_at_ms), reverse=True)
    return reranked


def _record_rerank_vector(record: LifecycleMemoryRecord) -> list[float]:
    cache: dict[str, list[float]] = _RECORD_CACHE.setdefault("rerank_vectors", {})
    cache_key = f"{record.content_hash}:{record.updated_at_ms}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    vector = _normalized_hash_vector(_record_search_text(record))
    cache[cache_key] = vector
    return vector


def _normalized_hash_vector(text: Any, *, dim: int = _RERANK_HASH_DIM) -> list[float]:
    terms = _rerank_terms_from_text(text)
    if not terms:
        return []
    vector = [0.0] * dim
    for term in terms:
        digest = hashlib.blake2b(term.encode("utf-8", errors="ignore"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "little") % dim
        vector[index] += 1.0
    return _normalize_vector(vector)


def _rerank_terms_from_text(text: Any) -> list[str]:
    raw = str(text or "").strip().lower()
    if not raw:
        return []
    terms = set(_index_terms_from_text(raw))
    compact = _compact_search_text(raw)
    # Character n-grams make Chinese compact queries and technical identifiers
    # less brittle while keeping the vector local and deterministic.
    if len(compact) >= 3:
        max_len = min(len(compact), 256)
        for size in (3, 4):
            for start in range(0, max_len - size + 1):
                piece = compact[start : start + size]
                if _indexable_term(piece):
                    terms.add(piece)
    return sorted(terms)[:512]


def _normalize_vector(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        return []
    return [value / norm for value in vector]


def _dot_product(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def _candidate_records(
    records: list[LifecycleMemoryRecord],
    query_terms: list[str],
    wanted: set[str],
) -> list[LifecycleMemoryRecord]:
    if not query_terms:
        return [record for record in records if not wanted or record.memory_type in wanted]
    index = _search_index(records)
    type_keys = wanted or set(index.keys())
    candidate_indices: set[int] = set()
    skipped_common_postings = 0
    for memory_type in type_keys:
        term_index = index.get(memory_type)
        if not term_index:
            continue
        for term in query_terms:
            postings = term_index.get(term)
            if not postings:
                continue
            if len(postings) > _MAX_INDEX_POSTINGS_FOR_CANDIDATE:
                skipped_common_postings += 1
                continue
            candidate_indices.update(postings)
    if not candidate_indices:
        return [record for record in records if not wanted or record.memory_type in wanted]
    return [records[index] for index in candidate_indices]


def _search_index(records: list[LifecycleMemoryRecord]) -> dict[str, dict[str, list[int]]]:
    cached = _RECORD_CACHE.get("search_index")
    if cached is not None:
        return cached
    index: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for record_index, record in enumerate(records):
        if record.status != "active":
            continue
        for term in _index_terms_for_record(record):
            index[record.memory_type][term].append(record_index)
    materialized = {memory_type: dict(term_index) for memory_type, term_index in index.items()}
    _RECORD_CACHE["search_index"] = materialized
    return materialized


def _index_terms_for_record(record: LifecycleMemoryRecord) -> set[str]:
    terms: set[str] = set()
    for text in (
        record.memory_type,
        record.content,
        " ".join(record.tags or []),
        record.domain,
        record.owner,
        record.skill_id,
    ):
        terms.update(_index_terms_from_text(text))
    for item in record.evidence or []:
        if not isinstance(item, dict):
            continue
        for name in (
            "governance_key",
            "entity_key",
            "target_key",
            "alias_key",
            "subject_key",
            "app_key",
            "project_key",
            "target_id",
            "domain",
            "type",
        ):
            terms.update(_index_terms_from_text(item.get(name)))
    return terms


def _index_terms_from_text(text: Any) -> set[str]:
    raw = str(text or "").strip().lower()
    if not raw:
        return set()
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", " ", raw.replace("_", " ").replace("-", " "), flags=re.UNICODE)
    terms: set[str] = set()
    for part in normalized.split():
        part = part.strip().lower()
        if not _indexable_term(part):
            continue
        terms.add(part)
        if _has_cjk(part) and len(part) >= 4:
            for size in (2, 3, 4):
                for start in range(0, max(0, len(part) - size + 1)):
                    piece = part[start : start + size]
                    if _indexable_term(piece):
                        terms.add(piece)
    compact = _compact_search_text(raw)
    if _indexable_term(compact):
        terms.add(compact)
    return terms


def _indexable_term(term: str) -> bool:
    if not term or len(term) < 2 or len(term) > 64:
        return False
    if term in _COMMON_RECALL_TERMS:
        return False
    if term.isdigit():
        return False
    return True


def _query_terms(query: str) -> list[str]:
    text = str(query or "").strip().lower()
    if not text:
        return []
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", " ", text.replace("_", " ").replace("-", " "), flags=re.UNICODE)
    compact = _compact_search_text(text)
    terms: list[str] = []
    for part in normalized.split():
        part = part.strip().lower()
        if not part:
            continue
        terms.append(part)
        if _has_cjk(part) and len(part) >= 4:
            for size in (2, 3, 4):
                for start in range(0, max(0, len(part) - size + 1)):
                    terms.append(part[start : start + size])
    if compact:
        terms.append(compact)
    seen: set[str] = set()
    out: list[str] = []
    for term in terms:
        if len(term) < 2 or term in seen:
            continue
        seen.add(term)
        out.append(term)
    return out[:80]


def _record_search_text(record: LifecycleMemoryRecord) -> str:
    try:
        evidence_text = json.dumps(record.evidence or [], ensure_ascii=False, sort_keys=True)
    except Exception:
        evidence_text = str(record.evidence or "")
    raw = (
        f"{record.memory_type} {record.content} {' '.join(record.tags)} "
        f"{record.domain} {record.owner} {record.skill_id} {evidence_text}"
    ).lower()
    return f"{raw} {_compact_search_text(raw)}"


def _compact_search_text(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", str(text or "").lower(), flags=re.UNICODE)


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in str(text or ""))


def _load_records(*, include_expired: bool = False) -> list[LifecycleMemoryRecord]:
    path = _store_path()
    if not path.exists():
        _refresh_record_cache(path, [], mtime_ns=-1, size=-1)
        return []
    stat = path.stat()
    cached_records = _cached_records(path, mtime_ns=stat.st_mtime_ns, size=stat.st_size)
    if cached_records is not None:
        if include_expired:
            return list(cached_records)
        return [record for record in cached_records if record.status == "active"]
    out: list[LifecycleMemoryRecord] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            obj = _record_defaults(obj)
            record = LifecycleMemoryRecord(**obj)
            if include_expired or record.status == "active":
                out.append(record)
        except Exception:
            continue
    _refresh_record_cache(path, out, mtime_ns=stat.st_mtime_ns, size=stat.st_size)
    if include_expired:
        return list(out)
    return [record for record in out if record.status == "active"]


def _cached_records(path: Path, *, mtime_ns: int, size: int) -> list[LifecycleMemoryRecord] | None:
    if (
        _RECORD_CACHE.get("path") == str(path)
        and _RECORD_CACHE.get("mtime_ns") == mtime_ns
        and _RECORD_CACHE.get("size") == size
    ):
        return _RECORD_CACHE.get("records") or []
    return None


def _refresh_record_cache(path: Path, records: list[LifecycleMemoryRecord], *, mtime_ns: int, size: int) -> None:
    _RECORD_CACHE["path"] = str(path)
    _RECORD_CACHE["mtime_ns"] = mtime_ns
    _RECORD_CACHE["size"] = size
    _RECORD_CACHE["records"] = list(records)
    _RECORD_CACHE["search_index"] = None
    _RECORD_CACHE["rerank_vectors"] = {}


def _invalid_raw_line_count() -> int:
    path = _store_path()
    if not path.exists():
        return 0
    invalid = 0
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            json.loads(line)
        except Exception:
            invalid += 1
    return invalid


def _is_stale_unverified(record: LifecycleMemoryRecord, *, now: int, stale_after_ms: int) -> bool:
    if record.memory_type not in REVIEWABLE_MEMORY_TYPES:
        return False
    anchor = record.last_verified_at_ms or record.created_at_ms or record.updated_at_ms
    if anchor <= 0:
        return True
    return now - anchor >= stale_after_ms


def _detect_conflict_memory_ids(records: list[LifecycleMemoryRecord]) -> set[str]:
    grouped: dict[str, list[LifecycleMemoryRecord]] = defaultdict(list)
    for record in records:
        if record.status != "active" or record.memory_type not in REVIEWABLE_MEMORY_TYPES:
            continue
        key = _governance_key(record)
        if key:
            grouped[key].append(record)
    conflict_ids: set[str] = set()
    for items in grouped.values():
        active_hashes = {item.content_hash for item in items}
        if len(active_hashes) <= 1:
            continue
        conflict_ids.update(item.memory_id for item in items)
    return conflict_ids


def _governance_key(record: LifecycleMemoryRecord) -> str:
    keys = []
    for item in record.evidence or []:
        if not isinstance(item, dict):
            continue
        for name in ("governance_key", "entity_key", "target_key", "alias_key", "subject_key", "app_key", "project_key"):
            value = str(item.get(name) or "").strip().lower()
            if value:
                keys.append(value)
    if keys:
            return f"{record.memory_type}:{record.domain}:{record.owner}:{keys[0]}"
    return ""


def _governance_key_from_evidence(memory_type: str, scope: dict[str, str], evidence: list[dict[str, Any]]) -> str:
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        for name in ("governance_key", "entity_key", "target_key", "alias_key", "subject_key", "app_key", "project_key"):
            value = str(item.get(name) or "").strip().lower()
            if value:
                return f"{memory_type}:{scope.get('domain') or 'global'}:{scope.get('owner') or 'user'}:{value}"
    return ""


def _record_defaults(obj: dict[str, Any]) -> dict[str, Any]:
    obj.setdefault("success_count", 0)
    obj.setdefault("failure_count", 0)
    obj.setdefault("last_verified_at_ms", 0)
    obj.setdefault("review_required", False)
    obj.setdefault("review_reason", "")
    obj.setdefault("layer", classify_memory_layer(str(obj.get("memory_type") or ""), str(obj.get("ttl") or "")))
    obj.setdefault("domain", "global")
    obj.setdefault("owner", "user")
    obj.setdefault("skill_id", "")
    defaults = lifecycle_record_trust_defaults(obj)
    obj.setdefault("trust_state", defaults["trust_state"])
    obj.setdefault("trust_reason", defaults["trust_reason"])
    obj.setdefault("user_attitude", defaults["user_attitude"])
    obj.setdefault("recall_allowed", defaults["recall_allowed"])
    return obj


def _rewrite_records(records: list[LifecycleMemoryRecord]) -> None:
    path = _store_path()
    path.write_text("\n".join(json.dumps(r.to_dict(), ensure_ascii=False) for r in records) + ("\n" if records else ""), encoding="utf-8")
    stat = path.stat()
    _refresh_record_cache(path, records, mtime_ns=stat.st_mtime_ns, size=stat.st_size)
    index = {
        "updated_at_ms": _now_ms(),
        "count": len(records),
        "active_count": sum(1 for r in records if r.status == "active"),
        "types": {},
    }
    for record in records:
        index["types"][record.memory_type] = int(index["types"].get(record.memory_type, 0)) + 1
    _index_path().write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def _evidence_success_count(evidence: list[dict[str, Any]]) -> int:
    return sum(1 for item in evidence or [] if isinstance(item, dict) and item.get("ok") is True)


def _evidence_failure_count(evidence: list[dict[str, Any]]) -> int:
    return sum(1 for item in evidence or [] if isinstance(item, dict) and item.get("ok") is False)


def _content_hash(memory_type: str, content: str) -> str:
    normalized = " ".join((content or "").strip().lower().split())
    return hashlib.sha256(f"{memory_type}:{normalized}".encode("utf-8")).hexdigest()[:24]
