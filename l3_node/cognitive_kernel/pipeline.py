"""Top-level context builder for the Memory-first Cognitive Kernel."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from .contracts import AgentInputEnvelope, InputSource, RelevantMemoryBundle, StateSnapshot, TaskLedgerEntry
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
            "input_envelope": self.envelope.to_dict(),
            "state_snapshot": self.state_snapshot.to_dict(),
            "relevant_memory": self.memory_bundle.to_dict(),
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
