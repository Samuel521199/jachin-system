"""Memory lifecycle store for Cognitive Kernel recall/write-back.

This is a local lifecycle index in front of Memory Nexus. It handles the parts
the kernel needs deterministically: classification, dedupe, TTL expiry, merge
metadata, and conflict-friendly reads. The semantic Memory Nexus can still be
used for vector search; this store keeps the operational memory lifecycle sane.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .contracts import MemoryEvidence, MemoryWriteRequest
from .ledger import append_event
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
    content_hash: str = ""
    tags: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    status: str = "active"
    merge_policy: str = "dedupe_and_merge"

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
            "content_hash": self.content_hash,
            "tags": list(self.tags),
            "evidence": list(self.evidence),
            "status": self.status,
            "merge_policy": self.merge_policy,
        }

    def to_evidence(self, reason: str = "memory lifecycle recall") -> MemoryEvidence:
        return MemoryEvidence(
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
        )


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
    if existing and request.merge_policy in {"dedupe_and_merge", "append_action_chain"}:
        existing.updated_at_ms = now
        existing.hit_count += 1
        existing.confidence = max(existing.confidence, float(request.confidence or 0.0))
        existing.evidence.extend(request.evidence or [])
        existing.ttl = request.ttl or existing.ttl
        existing.expires_at_ms = ttl_to_expiry_ms(existing.ttl, memory_type)
        record = existing
        action = "merged"
    else:
        record = LifecycleMemoryRecord(
            memory_id=f"mem_{uuid.uuid4().hex[:16]}",
            memory_type=memory_type,
            content=request.content,
            source_event=request.source_event,
            confidence=float(request.confidence or 0.0),
            ttl=request.ttl or "default",
            expires_at_ms=ttl_to_expiry_ms(request.ttl, memory_type),
            created_at_ms=now,
            updated_at_ms=now,
            hit_count=1,
            content_hash=content_hash,
            tags=[memory_type, request.source_event],
            evidence=list(request.evidence or []),
            merge_policy=request.merge_policy,
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
        },
    )
    return record


def recall_lifecycle_memories(query: str = "", *, memory_types: list[str] | None = None, limit: int = 8) -> list[MemoryEvidence]:
    query_terms = [x for x in (query or "").lower().replace("：", " ").replace(":", " ").split() if x]
    wanted = set(memory_types or [])
    now = _now_ms()
    all_records = _load_records(include_expired=True)
    records = []
    changed = False
    for record in all_records:
        if record.status != "active":
            continue
        if record.expires_at_ms and record.expires_at_ms < now:
            record.status = "expired"
            changed = True
            continue
        if wanted and record.memory_type not in wanted:
            continue
        records.append(record)
    if changed:
        _rewrite_records(all_records)
    scored = sorted(
        records,
        key=lambda r: (_score_record(r, query_terms), r.updated_at_ms),
        reverse=True,
    )
    out = [item.to_evidence("memory lifecycle ranked recall") for item in scored[: max(0, limit)] if _score_record(item, query_terms) > 0]
    append_event(
        "memory_lifecycle_recall",
        "memory-recall",
        {
            "query": query[:300],
            "memory_types": list(wanted),
            "hit_count": len(out),
        },
    )
    return out


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


def _score_record(record: LifecycleMemoryRecord, query_terms: list[str]) -> float:
    if not query_terms:
        return 1.0
    hay = f"{record.memory_type} {record.content} {' '.join(record.tags)}".lower()
    hits = sum(1 for term in query_terms if term in hay)
    if hits <= 0:
        return 0.0
    recency = min(1.0, max(0.0, record.updated_at_ms / max(1, _now_ms())))
    return hits * 10 + record.confidence + recency + min(record.hit_count, 5) * 0.1


def _load_records(*, include_expired: bool = False) -> list[LifecycleMemoryRecord]:
    path = _store_path()
    if not path.exists():
        return []
    out: list[LifecycleMemoryRecord] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            record = LifecycleMemoryRecord(**obj)
            if include_expired or record.status == "active":
                out.append(record)
        except Exception:
            continue
    return out


def _rewrite_records(records: list[LifecycleMemoryRecord]) -> None:
    path = _store_path()
    path.write_text("\n".join(json.dumps(r.to_dict(), ensure_ascii=False) for r in records) + ("\n" if records else ""), encoding="utf-8")
    index = {
        "updated_at_ms": _now_ms(),
        "count": len(records),
        "active_count": sum(1 for r in records if r.status == "active"),
        "types": {},
    }
    for record in records:
        index["types"][record.memory_type] = int(index["types"].get(record.memory_type, 0)) + 1
    _index_path().write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def _content_hash(memory_type: str, content: str) -> str:
    normalized = " ".join((content or "").strip().lower().split())
    return hashlib.sha256(f"{memory_type}:{normalized}".encode("utf-8")).hexdigest()[:24]
