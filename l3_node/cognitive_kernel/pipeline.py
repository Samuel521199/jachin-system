"""Top-level context builder for the Memory-first Cognitive Kernel."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from .contracts import AgentInputEnvelope, InputSource, MemoryEvidence, RelevantMemoryBundle, StateSnapshot, TaskLedgerEntry
from .kernel_prompts import build_cognitive_kernel_system_prompt
from .ledger import record_turn_started
from .memory_recall_agent import recall_relevant_memory
from .state_fabric import build_state_snapshot
from .task_guardian import start_task_guardian


@dataclass(slots=True)
class CognitiveTurnContext:
    envelope: AgentInputEnvelope
    state_snapshot: StateSnapshot
    memory_bundle: RelevantMemoryBundle
    ledger_entry: TaskLedgerEntry

    def prompt_block(self, max_chars: int = 6000) -> str:
        payload = {
            "architecture": "memory_first_cognitive_kernel",
            "contract_version": "2026-07-08",
            "system_prompt": build_cognitive_kernel_system_prompt(),
            "input_envelope": self.envelope.to_dict(),
            "state_snapshot": self.state_snapshot.to_dict(),
            "relevant_memory": _prompt_ready_memory_bundle(self.memory_bundle),
            "kernel_rules": [
                "All inputs must be judged by the Cognitive Kernel before action.",
                "Use state snapshot and memory recall to resolve short references.",
                "Do not directly mutate the external world from the kernel.",
                "External actions require DecisionContract and WorkOrder.",
                "VerificationAgent verifies observable effects; RecoveryAgent proposes repair.",
                "Every turn should end with TurnClosure and memory write requests when useful.",
            ],
        }
        text = "[Cognitive Kernel Context]\n" + json.dumps(payload, ensure_ascii=False, default=str)
        return text[:max_chars]


def _prompt_ready_memory_bundle(bundle: RelevantMemoryBundle) -> dict[str, Any]:
    """Build the Section 6 memory package for the kernel prompt.

    The full recall bundle stays in the ledger. The prompt receives a smaller,
    structured package split into summary, short-term context, long-term
    context, conflicts, gaps, and ranking evidence.
    """

    return {
        "retrieval_summary": _clip(bundle.retrieval_summary, 1200),
        "candidate_intents": list(bundle.candidate_intents),
        "candidate_task_domains": list(bundle.candidate_task_domains),
        "multi_queries": {k: _clip(v, 500) for k, v in (bundle.multi_queries or {}).items()},
        "resolved_references": bundle.resolved_references[:8],
        "short_term_context": {
            "recent_actions": _evidence_for_prompt(bundle.recent_actions, limit=8),
            "active_tasks": _evidence_for_prompt(bundle.active_tasks, limit=6),
        },
        "long_term_context": {
            "user_preferences": _evidence_for_prompt(bundle.user_preferences, limit=5),
            "safety_preferences": _evidence_for_prompt(bundle.safety_preferences, limit=5),
            "aliases": _evidence_for_prompt(bundle.aliases, limit=6),
            "corrections": _evidence_for_prompt(bundle.corrections, limit=6),
            "entity_matches": _evidence_for_prompt(bundle.entity_matches, limit=6),
            "contact_matches": _evidence_for_prompt(bundle.contact_matches, limit=6),
            "project_facts": _evidence_for_prompt(bundle.project_facts, limit=6),
            "tool_habits": _evidence_for_prompt(bundle.tool_habits, limit=5),
            "failure_hints": _evidence_for_prompt(bundle.failure_hints, limit=5),
            "historical_task_summaries": _evidence_for_prompt(bundle.historical_task_summaries, limit=5),
        },
        "conflicts": bundle.conflicts[:8],
        "memory_gaps": list(bundle.memory_gaps[:8]),
        "ranking_evidence": bundle.ranking_evidence[:12],
        "confidence": bundle.confidence,
    }


def _evidence_for_prompt(items: list[MemoryEvidence], *, limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items[: max(0, limit)]:
        out.append(
            {
                "memory_id": item.memory_id,
                "memory_type": item.memory_type,
                "content": _clip(item.content, 500),
                "source": item.source,
                "confidence": item.confidence,
                "confirmed_by_user": item.confirmed_by_user,
                "ttl": item.ttl,
                "relevance_reason": _clip(item.relevance_reason, 300),
                "created_at": item.created_at,
                "updated_at": item.updated_at,
            }
        )
    return out


def _clip(value: Any, max_len: int) -> str:
    text = str(value or "")
    if len(text) <= max_len:
        return text
    return text[: max(0, max_len - 3)] + "..."


def _source_from_channel(channel: str, companion: dict[str, Any] | None) -> InputSource:
    ch = (channel or "").strip().lower()
    if companion and (
        companion.get("voice_raw_stt_text")
        or companion.get("voice_asr_raw_text")
        or companion.get("voice_final_text")
        or companion.get("voice_routed_text")
    ):
        return InputSource.VOICE
    if "lark" in ch or "im" in ch:
        return InputSource.IM
    if "hotkey" in ch:
        return InputSource.HOTKEY
    if "watch" in ch or "scheduler" in ch:
        return InputSource.WATCHER
    if "api" in ch:
        return InputSource.API
    return InputSource.TEXT


def _voice_confidence(companion: dict[str, Any]) -> float | None:
    for key in ("voice_confidence", "voice_stt_confidence", "confidence", "stt_confidence"):
        value = companion.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


async def build_cognitive_turn_context(
    *,
    run_id: str,
    user_input: str,
    channel: str = "",
    session_id: str = "",
    prior_messages: list[dict[str, Any]] | None = None,
    attachments_metadata: list[dict[str, Any]] | None = None,
    implicit_attribution: dict[str, Any] | None = None,
    desktop_companion_context: dict[str, Any] | None = None,
    gateway_system_state: str | None = None,
) -> CognitiveTurnContext:
    start_task_guardian()
    now_ms = int(time.time() * 1000)
    companion = desktop_companion_context or {}
    raw_voice = (
        companion.get("voice_raw_stt_text")
        or companion.get("voice_asr_raw_text")
        or companion.get("voice_final_text")
        or companion.get("voice_routed_text")
        or ""
    )
    source = _source_from_channel(channel, companion)
    envelope = AgentInputEnvelope(
        turn_id=run_id,
        source=source,
        raw_text=str(raw_voice or user_input or ""),
        normalized_text=str(user_input or "").strip(),
        session_id=session_id or "",
        channel=channel or "",
        attachments=list(attachments_metadata or []),
        confidence=_voice_confidence(companion),
        modality_evidence={"voice": companion} if companion else {},
        implicit_attribution=implicit_attribution or {},
        created_at_ms=now_ms,
    )
    state_snapshot = build_state_snapshot(
        run_id=run_id,
        channel=channel,
        implicit_attribution=implicit_attribution,
        desktop_companion_context=desktop_companion_context,
        gateway_system_state=gateway_system_state,
    )
    memory_bundle = await recall_relevant_memory(
        envelope=envelope,
        state_snapshot=state_snapshot,
        prior_messages=prior_messages or [],
    )
    ledger_entry = TaskLedgerEntry(
        turn_id=run_id,
        input_envelope=envelope,
        state_snapshot=state_snapshot,
        memory_bundle=memory_bundle,
    )
    record_turn_started(ledger_entry)
    return CognitiveTurnContext(
        envelope=envelope,
        state_snapshot=state_snapshot,
        memory_bundle=memory_bundle,
        ledger_entry=ledger_entry,
    )
