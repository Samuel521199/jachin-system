"""Memory recall packaging for the Memory-first Cognitive Kernel."""

from __future__ import annotations

import asyncio
import json
import os
import re
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
    evidence = _history_evidence(prior_messages or [], max_results_per_channel)
    state_evidence = _task_state_evidence(state_snapshot)
    recent_actions, ledger_failure_hints = _recent_ledger_evidence(max_results_per_channel)
    candidate_intents = _candidate_intents(envelope.normalized_text or envelope.raw_text)
    candidate_domains = _candidate_task_domains(candidate_intents, envelope, state_snapshot)
    multi_queries = _build_multi_queries(
        envelope=envelope,
        state_snapshot=state_snapshot,
        candidate_intents=candidate_intents,
        candidate_domains=candidate_domains,
        recent_actions=recent_actions,
    )
    request = MemoryRecallRequest(
        turn_id=envelope.turn_id,
        input_envelope=envelope,
        candidate_intents=candidate_intents,
        candidate_task_domains=candidate_domains,
        candidate_entities=_candidate_entities(envelope.normalized_text or envelope.raw_text),
        multi_queries=multi_queries,
        retrieval_channels=[
            "recent_action_chain",
            "conversation_short_term",
            "task_state_memory",
            "user_preference_memory",
            "safety_preference_memory",
            "alias_and_correction_memory",
            "contact_and_entity_memory",
            "project_fact_memory",
            "tool_habit_memory",
            "failure_memory",
            "historical_task_summary",
            "environment_event_memory",
            "passive_nexus_profile_memory",
            "experience_rag_memory",
        ],
        state_snapshot_summary=state_snapshot.to_dict(),
        active_task_stack_summary=state_snapshot.task_state or {},
        retrieval_purpose=[
            "resolve_reference",
            "load_preferences",
            "load_long_term_user_memory",
            "resolve_aliases_and_contacts",
            "load_project_facts",
            "load_tool_habits",
            "continue_task",
            "check_recent_actions",
            "find_corrections",
            "find_failure_experience",
            "load_safety_preferences",
            "enrich_context",
        ],
        max_results_per_channel=max_results_per_channel,
    )

    memory_gaps: list[str] = []
    buckets: dict[str, list[MemoryEvidence]] = {
        "user_preferences": [],
        "safety_preferences": [],
        "aliases": [],
        "corrections": [],
        "entity_matches": [],
        "contact_matches": [],
        "project_facts": [],
        "tool_habits": [],
        "failure_hints": list(ledger_failure_hints),
        "historical_task_summaries": [],
    }
    ranking_evidence: list[dict[str, Any]] = []

    recalled_items, search_gaps = _fanout_memory_search(
        request=request,
        max_results_per_channel=max_results_per_channel,
    )
    memory_gaps.extend(search_gaps)
    passive_items, passive_gaps = await _passive_nexus_memory_evidence(max_results_per_channel)
    recalled_items.extend(passive_items)
    memory_gaps.extend(passive_gaps)
    experience_items, experience_gaps = _experience_memory_evidence(
        request=request,
        max_results_per_channel=max_results_per_channel,
    )
    recalled_items.extend(experience_items)
    memory_gaps.extend(experience_gaps)
    for item in recalled_items:
        bucket = _classify_recalled_memory(item)
        if bucket not in buckets:
            bucket = "entity_matches"
        buckets[bucket].append(item)

    ranked_buckets: dict[str, list[MemoryEvidence]] = {}
    for bucket_name, items in buckets.items():
        ranked, scores = _rank_and_filter(
            items,
            request=request,
            state_snapshot=state_snapshot,
            limit=max_results_per_channel,
        )
        ranked_buckets[bucket_name] = ranked
        ranking_evidence.extend({"bucket": bucket_name, **score} for score in scores)

    user_preferences = ranked_buckets["user_preferences"]
    safety_preferences = ranked_buckets["safety_preferences"]
    aliases = ranked_buckets["aliases"]
    corrections = ranked_buckets["corrections"]
    entity_matches = ranked_buckets["entity_matches"]
    contact_matches = ranked_buckets["contact_matches"]
    project_facts = ranked_buckets["project_facts"]
    tool_habits = ranked_buckets["tool_habits"]
    failure_hints = ranked_buckets["failure_hints"]
    historical_task_summaries = ranked_buckets["historical_task_summaries"]
    conflicts = _detect_memory_state_conflicts(
        state_snapshot=state_snapshot,
        recent_actions=recent_actions,
        aliases=aliases,
        corrections=corrections,
    )

    confidence = 0.55
    if (
        evidence
        or entity_matches
        or recent_actions
        or state_evidence
        or user_preferences
        or safety_preferences
        or aliases
        or corrections
        or contact_matches
        or project_facts
        or tool_habits
        or failure_hints
        or historical_task_summaries
    ):
        confidence = 0.75

    return RelevantMemoryBundle(
        turn_id=request.turn_id,
        recall_request=request.to_dict(),
        candidate_intents=list(candidate_intents),
        candidate_task_domains=list(candidate_domains),
        multi_queries=dict(multi_queries),
        retrieval_summary=(
            f"Memory recall packaged at {int(time.time())}; "
            f"candidate_intents={candidate_intents} candidate_domains={candidate_domains} "
            f"queries={len(multi_queries)} "
            f"short_term={len(evidence)} state={len(state_evidence)} "
            f"entity_matches={len(entity_matches)} recent_actions={len(recent_actions)} "
            f"failure_hints={len(failure_hints)} gaps={len(memory_gaps)}"
        ),
        recent_actions=[*evidence, *recent_actions],
        active_tasks=state_evidence,
        user_preferences=_dedupe_evidence(user_preferences),
        safety_preferences=_dedupe_evidence(safety_preferences),
        aliases=_dedupe_evidence(aliases),
        corrections=_dedupe_evidence(corrections),
        entity_matches=_dedupe_evidence(entity_matches),
        contact_matches=_dedupe_evidence(contact_matches),
        project_facts=_dedupe_evidence(project_facts),
        tool_habits=_dedupe_evidence(tool_habits),
        failure_hints=_dedupe_evidence(failure_hints),
        historical_task_summaries=_dedupe_evidence(historical_task_summaries),
        ranking_evidence=ranking_evidence[: max_results_per_channel * 8],
        conflicts=conflicts,
        confidence=confidence,
        memory_gaps=memory_gaps,
    )


def _candidate_intents(text: str) -> list[str]:
    low = (text or "").lower().strip()
    intents: list[str] = []
    rules = [
        ("open_app", ("open ", "launch", "start ", "calculator", "chrome", "lark", "feishu", "打开", "启动", "运行")),
        ("close_app", ("close", "quit", "exit", "关闭", "关掉", "退出")),
        ("switch_app", ("switch", "切到", "切换", "回到")),
        ("send_message", ("send to", "message", "发给", "发送", "通知", "告诉")),
        ("continue_task", ("continue", "resume", "继续", "接着", "刚才", "昨天")),
        ("undo_or_revert", ("undo", "revert", "cancel", "算了", "撤回", "撤销", "别")),
        ("file_operation", ("file", "folder", "read ", "write ", "delete", "文件", "目录")),
    ]
    for intent, markers in rules:
        if any(marker in low for marker in markers):
            intents.append(intent)
    if not intents:
        intents.append("conversation")
    return intents[:4]


def _candidate_task_domains(
    candidate_intents: list[str],
    envelope: AgentInputEnvelope,
    state_snapshot: StateSnapshot,
) -> list[str]:
    domains: list[str] = []
    for intent in candidate_intents:
        if intent in {"open_app", "close_app", "switch_app"}:
            domains.append("desktop_app_control")
        elif intent == "send_message":
            domains.append("communication")
        elif intent == "file_operation":
            domains.append("file_operation")
        elif intent in {"continue_task", "undo_or_revert"}:
            domains.append("task_management")
        elif intent == "conversation":
            domains.append("conversation")
    source = envelope.source.value if hasattr(envelope.source, "value") else str(envelope.source)
    if source == "voice" and "voice" not in domains:
        domains.append("voice")
    active = " ".join(str(v) for v in (state_snapshot.active_window or {}).values()).lower()
    if any(x in active for x in ("lark", "feishu")) and "communication" not in domains:
        domains.append("communication")
    return _dedupe_strings(domains) or ["unknown"]


def _candidate_entities(text: str) -> list[str]:
    entities: list[str] = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_\-]{1,40}", text or ""):
        if token.lower() not in {"open", "close", "send", "message", "file", "read", "write"}:
            entities.append(token)
    for marker in ("Calculator", "Chrome", "Lark", "Vivian", "VS Code"):
        if marker.lower() in (text or "").lower():
            entities.append(marker)
    return _dedupe_strings(entities)[:8]


def _build_multi_queries(
    *,
    envelope: AgentInputEnvelope,
    state_snapshot: StateSnapshot,
    candidate_intents: list[str],
    candidate_domains: list[str],
    recent_actions: list[MemoryEvidence],
) -> dict[str, str]:
    raw = str(envelope.raw_text or envelope.normalized_text or "").strip()
    normalized = str(envelope.normalized_text or envelope.raw_text or "").strip()
    active = state_snapshot.active_window or {}
    recent_events = state_snapshot.recent_app_events or []
    source = envelope.source.value if hasattr(envelope.source, "value") else str(envelope.source)
    return {
        "query_1_user_utterance": raw or normalized,
        "query_2_candidate_intent": " ".join(candidate_intents),
        "query_3_source_domain": f"{source} {' '.join(candidate_domains)}".strip(),
        "query_4_recent_action_chain": " ".join(item.content[:180] for item in recent_actions[:4]) or "recent opened apps by Jachin",
        "query_5_state_snapshot": json.dumps(
            {
                "active_window": active,
                "recent_app_events": recent_events[:5],
            },
            ensure_ascii=False,
            default=str,
        )[:1000],
        "query_6_long_term_user_memory": (
            f"{normalized} preferences aliases corrections contacts project facts tool habits "
            "safety preferences communication style historical task summaries"
        ).strip(),
    }


def _fanout_memory_search(
    *,
    request: MemoryRecallRequest,
    max_results_per_channel: int,
) -> tuple[list[MemoryEvidence], list[str]]:
    items: list[MemoryEvidence] = []
    gaps: list[str] = []
    queries = [q for q in request.multi_queries.values() if str(q or "").strip()]
    try:
        expire_lifecycle_memories()
        for query in queries:
            items.extend(
                recall_lifecycle_memories(
                    query,
                    limit=max_results_per_channel,
                )
            )
    except Exception as exc:
        gaps.append(f"memory_lifecycle_unavailable:{exc.__class__.__name__}")
    try:
        from l3_node.local_memory_search import search_local_memories  # type: ignore

        for idx, query in enumerate(queries):
            maybe = search_local_memories(query, top_k=max_results_per_channel)
            rows = _rows_from_memory_search_result(maybe)
            for i, row in enumerate(rows[:max_results_per_channel]):
                item = _memory_evidence_from_row(row, idx * 100 + i)
                item.relevance_reason = f"multi-query recall: {query[:120]}"
                items.append(item)
    except Exception as exc:
        gaps.append(f"memory_nexus_unavailable:{exc.__class__.__name__}")
    return _dedupe_evidence(items), gaps


def _unified_memory_provider_timeout_sec() -> float:
    raw = (os.environ.get("JACHIN_UNIFIED_MEMORY_PROVIDER_TIMEOUT_SEC") or "2.0").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 2.0
    return max(0.25, min(value, 10.0))


async def _passive_nexus_memory_evidence(limit: int) -> tuple[list[MemoryEvidence], list[str]]:
    """Load passive Nexus prompt sources as structured evidence.

    L0/L1 are no longer allowed to inject directly into prompts. If available,
    they enter the same ranking/dedupe/conflict path as every other memory.
    """

    items: list[MemoryEvidence] = []
    gaps: list[str] = []
    timeout = _unified_memory_provider_timeout_sec()
    try:
        from l3_client.local_mcps.jachin_memory_nexus.memory_backend import recall_room  # type: ignore
        from l3_node.memory_nexus_bridge import async_build_l0_persona_block, async_build_l1_system_memory_block
    except Exception as exc:
        return [], [f"passive_nexus_provider_unavailable:{exc.__class__.__name__}"]

    async def _load_l0() -> str:
        return await asyncio.wait_for(async_build_l0_persona_block(), timeout=timeout)

    async def _load_l1() -> str:
        return await asyncio.wait_for(async_build_l1_system_memory_block(recall_room), timeout=timeout)

    results = await asyncio.gather(_load_l0(), _load_l1(), return_exceptions=True)
    l0, l1 = results
    if isinstance(l0, Exception):
        gaps.append(f"passive_nexus_l0_unavailable:{l0.__class__.__name__}")
    elif str(l0 or "").strip():
        items.append(
            MemoryEvidence(
                memory_id=f"passive_nexus:l0:{uuid.uuid4().hex[:8]}",
                memory_type="user_preference",
                content=str(l0).strip()[:1600],
                source="Memory Nexus passive profile",
                confidence=0.82,
                confirmed_by_user=True,
                ttl="long_term",
                relevance_reason="L0 persona source routed through unified MemoryRecallAgent",
            )
        )
    if isinstance(l1, Exception):
        gaps.append(f"passive_nexus_l1_unavailable:{l1.__class__.__name__}")
    elif str(l1 or "").strip():
        items.append(
            MemoryEvidence(
                memory_id=f"passive_nexus:l1:{uuid.uuid4().hex[:8]}",
                memory_type="historical_task_summary",
                content=str(l1).strip()[:2400],
                source="Memory Nexus passive system memory",
                confidence=0.74,
                ttl="long_term",
                relevance_reason="L1 system memory source routed through unified MemoryRecallAgent",
            )
        )
    return items[: max(0, limit)], gaps


def _experience_memory_evidence(
    *,
    request: MemoryRecallRequest,
    max_results_per_channel: int,
) -> tuple[list[MemoryEvidence], list[str]]:
    """Load experience RAG as evidence instead of a direct few-shot prompt block."""

    items: list[MemoryEvidence] = []
    gaps: list[str] = []
    query = (
        request.multi_queries.get("query_1_user_utterance")
        or request.multi_queries.get("query_2_candidate_intent")
        or ""
    )
    if not str(query or "").strip():
        return items, gaps
    try:
        from l3_node.experience_memory import retrieve_experience

        rows = retrieve_experience(str(query)[:8000], top_k=min(3, max_results_per_channel))
    except Exception as exc:
        return [], [f"experience_memory_unavailable:{exc.__class__.__name__}"]
    for idx, row in enumerate(rows or []):
        if not isinstance(row, dict):
            continue
        payload = row.get("action_payload")
        try:
            payload_s = json.dumps(payload if isinstance(payload, dict) else {}, ensure_ascii=False, default=str)
        except Exception:
            payload_s = str(payload or "")
        content = (
            f"experience memory: user_intent={str(row.get('user_intent') or '')[:600]} "
            f"executed_tool={str(row.get('executed_tool') or '')[:160]} "
            f"action_payload={payload_s[:1200]}"
        ).strip()
        try:
            confidence = float(row.get("score") or 0.72)
        except (TypeError, ValueError):
            confidence = 0.72
        items.append(
            MemoryEvidence(
                memory_id=str(row.get("id") or f"experience_rag:{idx}:{uuid.uuid4().hex[:6]}"),
                memory_type="tool_habit",
                content=content[:1800],
                source="Experience RAG",
                created_at=str(row.get("ts") or ""),
                updated_at=str(row.get("ts") or ""),
                confidence=confidence,
                ttl="long_term",
                relevance_reason="experience few-shot source routed through unified MemoryRecallAgent",
            )
        )
    return items, gaps


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


# Stable overrides for the Section 6 recall plan. These definitions are the
# effective runtime implementations after module load.
def _candidate_intents(text: str) -> list[str]:  # type: ignore[no-redef]
    low = (text or "").lower().strip()
    rules = [
        ("open_app", ("open ", "launch", "start ", "calculator", "chrome", "lark", "feishu", "\u6253\u5f00", "\u542f\u52a8", "\u8fd0\u884c")),
        ("close_app", ("close", "quit", "exit", "\u5173\u95ed", "\u5173\u6389", "\u9000\u51fa")),
        ("switch_app", ("switch", "\u5207\u5230", "\u5207\u6362", "\u56de\u5230")),
        ("send_message", ("send to", "message", "\u53d1\u7ed9", "\u53d1\u9001", "\u901a\u77e5", "\u544a\u8bc9")),
        ("continue_task", ("continue", "resume", "\u7ee7\u7eed", "\u63a5\u7740", "\u521a\u624d", "\u6628\u5929")),
        ("undo_or_revert", ("undo", "revert", "cancel", "\u7b97\u4e86", "\u64a4\u56de", "\u64a4\u9500", "\u522b")),
        ("file_operation", ("file", "folder", "read ", "write ", "delete", "\u6587\u4ef6", "\u76ee\u5f55")),
    ]
    out: list[str] = []
    for intent, markers in rules:
        if any(marker in low for marker in markers):
            out.append(intent)
    return _dedupe_strings(out)[:4] or ["conversation"]


def _rows_from_memory_search_result(result: Any) -> list[Any]:
    if isinstance(result, dict):
        for key in ("hits", "results", "memories", "items", "matches"):
            rows = result.get(key)
            if isinstance(rows, list):
                return rows
    if isinstance(result, list):
        return result
    return []


def _classify_recalled_memory(item: MemoryEvidence) -> str:  # type: ignore[no-redef]
    hay = f"{item.memory_type} {item.content}".lower()
    if any(k in hay for k in ("safety", "confirm before", "confirmation", "permission", "privacy", "\u786e\u8ba4", "\u5b89\u5168", "\u9690\u79c1")):
        return "safety_preferences"
    if any(k in hay for k in ("tool habit", "preferred tool", "mcp", "uia", "automation", "\u5de5\u5177\u4e60\u60ef", "\u5e38\u7528\u5de5\u5177")):
        return "tool_habits"
    if any(k in hay for k in ("project", "report", "workspace", "repo", "\u9879\u76ee", "\u62a5\u8868", "\u76ee\u5f55")):
        return "project_facts"
    if any(k in hay for k in ("contact", "recipient", "vivian", "neil", "\u8054\u7cfb\u4eba", "\u6536\u4ef6\u4eba")):
        return "contact_matches"
    if any(k in hay for k in ("historical task", "task summary", "paused", "resume", "\u8fdb\u5ea6", "\u5386\u53f2\u4efb\u52a1", "\u6682\u505c")):
        return "historical_task_summaries"
    if any(k in hay for k in ("failure", "failed", "recovery", "timeout", "\u5931\u8d25", "\u91cd\u8bd5")):
        return "failure_hints"
    if any(k in hay for k in ("preference", "preferred", "default", "style", "brief", "user_preference", "\u504f\u597d", "\u9ed8\u8ba4")):
        return "user_preferences"
    if any(k in hay for k in ("alias", "also called", "=", "path memory", "\u522b\u540d", "\u5c31\u662f")):
        return "aliases"
    if any(k in hay for k in ("correction", "not browser", "\u4e0d\u662f", "\u7ea0\u9519", "\u6539\u6210", "\u4ee5\u540e\u4e0d\u8981")):
        return "corrections"
    return "entity_matches"


def _rank_and_filter(
    items: list[MemoryEvidence],
    *,
    request: MemoryRecallRequest,
    state_snapshot: StateSnapshot,
    limit: int,
) -> tuple[list[MemoryEvidence], list[dict[str, Any]]]:
    scored: list[tuple[float, MemoryEvidence, dict[str, Any]]] = []
    for item in _dedupe_evidence(items):
        score, detail = _memory_score(item, request=request, state_snapshot=state_snapshot)
        if score > 0:
            scored.append((score, item, detail))
    scored.sort(key=lambda row: row[0], reverse=True)
    return [item for _, item, _ in scored[:limit]], [detail for _, _, detail in scored[:limit]]


def _memory_score(
    item: MemoryEvidence,
    *,
    request: MemoryRecallRequest,
    state_snapshot: StateSnapshot,
) -> tuple[float, dict[str, Any]]:
    semantic_similarity = max(0.0, min(1.0, float(item.confidence or 0.0)))
    recency_score = _recency_score(item)
    user_confirmed_score = 1.0 if item.confirmed_by_user else 0.35
    task_relevance_score = _task_relevance_score(item, request)
    state_alignment_score = _state_alignment_score(item, state_snapshot)
    conflict_penalty = _conflict_penalty(item, state_snapshot)
    score = (
        semantic_similarity * 0.30
        + recency_score * 0.25
        + user_confirmed_score * 0.20
        + task_relevance_score * 0.15
        + state_alignment_score * 0.10
        - conflict_penalty
    )
    return score, {
        "memory_id": item.memory_id,
        "memory_type": item.memory_type,
        "score": round(score, 4),
        "semantic_similarity": round(semantic_similarity, 4),
        "recency_score": round(recency_score, 4),
        "user_confirmed_score": round(user_confirmed_score, 4),
        "task_relevance_score": round(task_relevance_score, 4),
        "state_alignment_score": round(state_alignment_score, 4),
        "conflict_penalty": round(conflict_penalty, 4),
    }


def _recency_score(item: MemoryEvidence) -> float:
    raw = item.updated_at or item.created_at
    try:
        ts = float(raw)
        if ts > 10_000_000_000:
            age_days = max(0.0, (time.time() * 1000 - ts) / 86_400_000)
        else:
            age_days = max(0.0, (time.time() - ts) / 86_400)
        return max(0.1, 1.0 / (1.0 + age_days / 14.0))
    except Exception:
        if item.ttl in {"turn", "session", "recent"}:
            return 0.85
        if item.ttl in {"permanent", "forever", "long_term"}:
            return 0.65
        return 0.55


def _task_relevance_score(item: MemoryEvidence, request: MemoryRecallRequest) -> float:
    hay = f"{item.memory_type} {item.content}".lower()
    terms = [*request.candidate_intents, *request.candidate_task_domains]
    if not terms:
        return 0.4
    hits = 0
    for term in terms:
        value = str(term or "").lower()
        if value in hay or value.replace("_", " ") in hay:
            hits += 1
    return min(1.0, 0.35 + hits * 0.25)


def _state_alignment_score(item: MemoryEvidence, state_snapshot: StateSnapshot) -> float:
    active = state_snapshot.active_window or {}
    if not active:
        return 0.4
    hay = item.content.lower()
    for value in active.values():
        token = str(value or "").strip().lower()
        if token and token in hay:
            return 1.0
    return 0.45 if any(k in hay for k in ("active_window", "foreground", "window")) else 0.25


def _conflict_penalty(item: MemoryEvidence, state_snapshot: StateSnapshot) -> float:
    active = state_snapshot.active_window or {}
    active_app = str(active.get("app_name") or active.get("process_name") or "").strip().lower()
    if active_app and "last_opened_app" in item.content.lower() and active_app not in item.content.lower():
        return 0.25
    return 0.0


def _dedupe_strings(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        val = str(item or "").strip()
        key = val.lower()
        if not val or key in seen:
            continue
        seen.add(key)
        out.append(val)
    return out


def _classify_recalled_memory(item: MemoryEvidence) -> str:  # type: ignore[no-redef]
    hay = f"{item.memory_type} {item.content}".lower()
    if any(k in hay for k in ("safety", "confirm before", "confirmation", "permission", "privacy", "\u786e\u8ba4", "\u5b89\u5168", "\u9690\u79c1")):
        return "safety_preferences"
    if any(k in hay for k in ("contact", "recipient", "vivian", "neil", "\u8054\u7cfb\u4eba", "\u6536\u4ef6\u4eba")):
        return "contact_matches"
    if any(k in hay for k in ("historical task", "task summary", "paused", "resume", "\u8fdb\u5ea6", "\u5386\u53f2\u4efb\u52a1", "\u6682\u505c")):
        return "historical_task_summaries"
    if any(k in hay for k in ("tool habit", "preferred tool", "mcp", "uia", "automation", "\u5de5\u5177\u4e60\u60ef", "\u5e38\u7528\u5de5\u5177")):
        return "tool_habits"
    if any(k in hay for k in ("failure", "failed", "recovery", "timeout", "\u5931\u8d25", "\u91cd\u8bd5")):
        return "failure_hints"
    if any(k in hay for k in ("alias", "also called", "=", "path memory", "\u522b\u540d", "\u5c31\u662f")):
        return "aliases"
    if any(k in hay for k in ("correction", "not browser", "\u4e0d\u662f", "\u7ea0\u9519", "\u6539\u6210", "\u4ee5\u540e\u4e0d\u8981")):
        return "corrections"
    if any(k in hay for k in ("preference", "preferred", "default", "style", "brief", "user_preference", "\u504f\u597d", "\u9ed8\u8ba4")):
        return "user_preferences"
    if any(k in hay for k in ("project", "report", "workspace", "repo", "\u9879\u76ee", "\u62a5\u8868", "\u76ee\u5f55")):
        return "project_facts"
    return "entity_matches"
