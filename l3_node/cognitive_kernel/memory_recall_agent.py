"""Memory recall packaging for the Memory-first Cognitive Kernel."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from .contracts import (
    AgentInputEnvelope,
    MemoryEvidence,
    MemoryRecallRequest,
    RelevantMemoryBundle,
    StateSnapshot,
)
from .ledger import current_ledger_path
from .memory_lifecycle import expire_lifecycle_memories, recall_lifecycle_memories


def _history_evidence(prior_messages: list[dict[str, Any]], limit: int = 6) -> list[MemoryEvidence]:
    items: list[MemoryEvidence] = []
    for idx, msg in enumerate(prior_messages[-limit:]):
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        content = msg.get("content")
        if not isinstance(content, str):
            content = str(content or "")
        content = content.strip()
        if not content:
            continue
        items.append(
            MemoryEvidence(
                memory_id=f"conversation_short_term:{idx}:{uuid.uuid4().hex[:6]}",
                memory_type="conversation_short_term",
                content=f"{role}: {content[:500]}",
                source="prior_messages",
                confidence=0.62,
                relevance_reason="recent conversation context",
                ttl="session",
            )
        )
    return items


def _task_state_evidence(state_snapshot: StateSnapshot) -> list[MemoryEvidence]:
    items: list[MemoryEvidence] = []
    task_state = state_snapshot.task_state or {}
    resource_state = state_snapshot.resource_state or {}
    if task_state:
        items.append(
            MemoryEvidence(
                memory_id=f"state_task:{state_snapshot.snapshot_id}",
                memory_type="state_task",
                content=json.dumps(task_state, ensure_ascii=False, default=str)[:800],
                source="StateWatcher",
                confidence=0.7,
                relevance_reason="current task/channel state",
                ttl="turn",
            )
        )
    if resource_state:
        items.append(
            MemoryEvidence(
                memory_id=f"state_resource:{state_snapshot.snapshot_id}",
                memory_type="state_resource",
                content=json.dumps(resource_state, ensure_ascii=False, default=str)[:800],
                source="StateWatcher",
                confidence=0.66,
                relevance_reason="current resource state",
                ttl="turn",
            )
        )
    return items


def _recent_ledger_evidence(limit: int = 8) -> tuple[list[MemoryEvidence], list[MemoryEvidence]]:
    recent_actions: list[MemoryEvidence] = []
    failure_hints: list[MemoryEvidence] = []
    path: Path = current_ledger_path()
    if not path.exists():
        return recent_actions, failure_hints
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-200:]
    except Exception:
        return recent_actions, failure_hints
    for line in reversed(lines):
        if len(recent_actions) >= limit and len(failure_hints) >= limit:
            break
        try:
            event = json.loads(line)
        except Exception:
            continue
        et = str(event.get("event_type") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if et == "work_order" and len(recent_actions) < limit:
            recent_actions.append(
                MemoryEvidence(
                    memory_id=str(payload.get("work_order_id") or uuid.uuid4().hex[:8]),
                    memory_type="recent_work_order",
                    content=json.dumps(payload, ensure_ascii=False, default=str)[:900],
                    source="CognitiveKernelLedger",
                    confidence=0.72,
                    relevance_reason="recent execution chain",
                    ttl="recent",
                )
            )
        if et in {"verification_report", "recovery_plan"} and len(failure_hints) < limit:
            failed = payload.get("ok") is False or et == "recovery_plan"
            if failed:
                failure_hints.append(
                    MemoryEvidence(
                        memory_id=str(payload.get("verification_id") or payload.get("recovery_id") or uuid.uuid4().hex[:8]),
                        memory_type=et,
                        content=json.dumps(payload, ensure_ascii=False, default=str)[:900],
                        source="CognitiveKernelLedger",
                        confidence=0.78,
                        relevance_reason="recent failure/recovery hint",
                        ttl="recent",
                    )
                )
    return recent_actions, failure_hints


async def recall_relevant_memory(
    *,
    envelope: AgentInputEnvelope,
    state_snapshot: StateSnapshot,
    prior_messages: list[dict[str, Any]] | None = None,
    max_results_per_channel: int = 5,
) -> RelevantMemoryBundle:
    request = MemoryRecallRequest(
        turn_id=envelope.turn_id,
        input_envelope=envelope,
        state_snapshot_summary=state_snapshot.to_dict(),
        retrieval_purpose=[
            "resolve_reference",
            "load_preferences",
            "continue_task",
            "check_recent_actions",
            "find_corrections",
            "enrich_context",
        ],
        max_results_per_channel=max_results_per_channel,
    )

    evidence = _history_evidence(prior_messages or [], max_results_per_channel)
    state_evidence = _task_state_evidence(state_snapshot)
    memory_gaps: list[str] = []

    entity_matches: list[MemoryEvidence] = []
    user_preferences: list[MemoryEvidence] = []
    aliases: list[MemoryEvidence] = []
    corrections: list[MemoryEvidence] = []
    try:
        expire_lifecycle_memories()
        lifecycle_hits = recall_lifecycle_memories(
            envelope.normalized_text or envelope.raw_text,
            limit=max_results_per_channel * 2,
        )
        for item in lifecycle_hits:
            bucket = _classify_recalled_memory(item)
            if bucket == "user_preferences":
                user_preferences.append(item)
            elif bucket == "aliases":
                aliases.append(item)
            elif bucket == "corrections":
                corrections.append(item)
            elif item.memory_type == "failure_hint":
                # failure_hints is defined below; temporarily stash as entity and move later.
                entity_matches.append(item)
            else:
                entity_matches.append(item)
    except Exception as exc:
        memory_gaps.append(f"memory_lifecycle_unavailable:{exc.__class__.__name__}")
    try:
        from l3_node.local_memory_search import search_local_memories  # type: ignore

        maybe = search_local_memories(envelope.normalized_text or envelope.raw_text, top_k=max_results_per_channel)
        rows = []
        if isinstance(maybe, dict):
            raw_rows = maybe.get("results") or maybe.get("memories") or maybe.get("items") or []
            rows = raw_rows if isinstance(raw_rows, list) else []
        elif isinstance(maybe, list):
            rows = maybe
        if rows:
            for i, row in enumerate(rows[:max_results_per_channel]):
                evidence_item = _memory_evidence_from_row(row, i)
                bucket = _classify_recalled_memory(evidence_item)
                if bucket == "user_preferences":
                    user_preferences.append(evidence_item)
                elif bucket == "aliases":
                    aliases.append(evidence_item)
                elif bucket == "corrections":
                    corrections.append(evidence_item)
                else:
                    entity_matches.append(evidence_item)
    except Exception as exc:
        memory_gaps.append(f"memory_nexus_unavailable:{exc.__class__.__name__}")

    recent_actions, failure_hints = _recent_ledger_evidence(max_results_per_channel)
    lifecycle_failures = [item for item in entity_matches if item.memory_type == "failure_hint"]
    if lifecycle_failures:
        failure_hints = [*lifecycle_failures, *failure_hints]
        entity_matches = [item for item in entity_matches if item.memory_type != "failure_hint"]
    conflicts = _detect_memory_state_conflicts(
        state_snapshot=state_snapshot,
        recent_actions=recent_actions,
        aliases=aliases,
        corrections=corrections,
    )

    confidence = 0.55
    if evidence or entity_matches or recent_actions or state_evidence or user_preferences or aliases or corrections:
        confidence = 0.75

    return RelevantMemoryBundle(
        turn_id=request.turn_id,
        retrieval_summary=(
            f"Memory recall packaged at {int(time.time())}; "
            f"short_term={len(evidence)} state={len(state_evidence)} "
            f"entity_matches={len(entity_matches)} recent_actions={len(recent_actions)} "
            f"failure_hints={len(failure_hints)} gaps={len(memory_gaps)}"
        ),
        recent_actions=[*evidence, *recent_actions],
        active_tasks=state_evidence,
        user_preferences=_dedupe_evidence(user_preferences),
        aliases=_dedupe_evidence(aliases),
        corrections=_dedupe_evidence(corrections),
        entity_matches=entity_matches,
        failure_hints=failure_hints,
        conflicts=conflicts,
        confidence=confidence,
        memory_gaps=memory_gaps,
    )


def _memory_evidence_from_row(row: Any, index: int) -> MemoryEvidence:
    if isinstance(row, dict):
        content = str(row.get("content") or row.get("text") or row.get("body") or row)[:1200]
        memory_type = str(row.get("type") or row.get("memory_type") or row.get("category") or "memory_nexus")
        confidence = row.get("score")
        try:
            confidence_value = float(confidence if confidence is not None else 0.7)
        except Exception:
            confidence_value = 0.7
        return MemoryEvidence(
            memory_id=str(row.get("id") or row.get("memory_id") or f"memory_nexus:{index}"),
            memory_type=memory_type,
            content=content,
            source="Memory Nexus",
            confidence=confidence_value,
            relevance_reason="semantic recall before kernel prompt",
            ttl=str(row.get("ttl") or ""),
            created_at=str(row.get("created_at") or row.get("created") or ""),
            updated_at=str(row.get("updated_at") or row.get("updated") or ""),
        )
    return MemoryEvidence(
        memory_id=f"memory_nexus:{index}",
        memory_type="memory_nexus",
        content=str(row)[:1200],
        source="Memory Nexus",
        confidence=0.7,
        relevance_reason="semantic recall before kernel prompt",
    )


def _classify_recalled_memory(item: MemoryEvidence) -> str:
    hay = f"{item.memory_type} {item.content}".lower()
    if any(k in hay for k in ("preference", "偏好", "喜欢", "习惯", "user_preference")):
        return "user_preferences"
    if any(k in hay for k in ("alias", "别名", "代号", "项目路径", "path memory")):
        return "aliases"
    if any(k in hay for k in ("correction", "纠错", "不要", "以后", "failure", "recovery")):
        return "corrections"
    return "entity_matches"


def _dedupe_evidence(items: list[MemoryEvidence]) -> list[MemoryEvidence]:
    seen: set[str] = set()
    out: list[MemoryEvidence] = []
    for item in items:
        key = f"{item.memory_type}:{item.content[:180]}"
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _detect_memory_state_conflicts(
    *,
    state_snapshot: StateSnapshot,
    recent_actions: list[MemoryEvidence],
    aliases: list[MemoryEvidence],
    corrections: list[MemoryEvidence],
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    active = state_snapshot.active_window or {}
    active_app = str(active.get("app_name") or active.get("process_name") or "").strip()
    if active_app:
        for item in recent_actions[:5]:
            content = (item.content or "").lower()
            if "last_opened_app" in content and active_app.lower() not in content:
                conflicts.append(
                    {
                        "type": "state_vs_action_memory",
                        "state_app": active_app,
                        "memory_id": item.memory_id,
                        "memory_preview": item.content[:300],
                        "resolution_policy": "prefer_current_state_for_app_control",
                    }
                )
                break
    for item in corrections[:5]:
        conflicts.append(
            {
                "type": "correction_memory_present",
                "memory_id": item.memory_id,
                "memory_preview": item.content[:300],
                "resolution_policy": "correction_memory_has_high_weight",
            }
        )
    return conflicts
