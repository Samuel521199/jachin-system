"""LLM-assisted resolver for pending OS mission follow-ups.

The router owns mission safety and execution.  This module only answers one
question: does the latest user utterance complete or modify the pending intent,
and if so which fixed schema slots should change?
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from l3_node.mission_intent_schema import MissionIntent, MissionSlots, MissionTaskType

logger = logging.getLogger(__name__)

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)
_ALLOWED_SLOTS = set(MissionSlots.__dataclass_fields__)
_EXTERNAL_EFFECT_TASKS = {
    MissionTaskType.LARK_MESSAGE_SEND,
    MissionTaskType.PROJECT_BRIEFING_DELIVERY,
    MissionTaskType.FILE_TO_APP,
}


@dataclass
class PendingResolverResult:
    used: bool = False
    source: str = "none"
    continue_pending: bool = True
    operation: str = ""
    filled_slots: dict[str, Any] = field(default_factory=dict)
    slot_confidence: dict[str, float] = field(default_factory=dict)
    ambiguous_slots: list[str] = field(default_factory=list)
    needs_clarification: bool = False
    clarification_question: str = ""
    reasoning_summary: str = ""
    raw_response: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "used": self.used,
            "source": self.source,
            "continue_pending": self.continue_pending,
            "operation": self.operation,
            "filled_slots": self.filled_slots,
            "slot_confidence": self.slot_confidence,
            "ambiguous_slots": self.ambiguous_slots,
            "needs_clarification": self.needs_clarification,
            "clarification_question": self.clarification_question,
            "reasoning_summary": self.reasoning_summary,
            "raw_response_preview": self.raw_response[:800],
            "error": self.error,
        }


def external_effect_intent(intent: MissionIntent) -> bool:
    return intent.task_type in _EXTERNAL_EFFECT_TASKS


def _parse_json_loose(raw: str) -> dict[str, Any] | None:
    s = str(raw or "").strip()
    if not s:
        return None
    m = _JSON_FENCE.search(s)
    if m:
        s = m.group(1).strip()
    try:
        data = json.loads(s)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        start = s.find("{")
        end = s.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(s[start : end + 1])
                return data if isinstance(data, dict) else None
            except json.JSONDecodeError:
                return None
    return None


def _clean_str(raw: Any, max_len: int = 4000) -> str:
    return str(raw or "").strip()[:max_len]


def _clean_str_list(raw: Any, max_n: int = 12) -> list[str]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw[:max_n]:
        s = _clean_str(item, 500)
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


def _clean_slots(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in raw.items():
        slot = str(key or "").strip()
        if slot not in _ALLOWED_SLOTS:
            continue
        if slot == "recipients":
            recipients = _clean_str_list(value, 8)
            if recipients:
                out[slot] = recipients
        elif slot == "since_days":
            try:
                out[slot] = max(1, min(30, int(value)))
            except (TypeError, ValueError):
                continue
        else:
            text = _clean_str(value, 4000)
            if text:
                out[slot] = text
    return out


def _clean_confidence(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in raw.items():
        slot = str(key or "").strip()
        if slot not in _ALLOWED_SLOTS:
            continue
        try:
            out[slot] = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            continue
    return out


def _result_from_dict(data: dict[str, Any], raw_response: str) -> PendingResolverResult:
    return PendingResolverResult(
        used=True,
        source="llm",
        continue_pending=bool(data.get("continue_pending", True)),
        operation=_clean_str(data.get("operation"), 80),
        filled_slots=_clean_slots(data.get("filled_slots")),
        slot_confidence=_clean_confidence(data.get("slot_confidence")),
        ambiguous_slots=_clean_str_list(data.get("ambiguous_slots"), 12),
        needs_clarification=bool(data.get("needs_clarification")),
        clarification_question=_clean_str(data.get("clarification_question"), 500),
        reasoning_summary=_clean_str(data.get("reasoning_summary"), 500),
        raw_response=raw_response,
    )


def _resolver_enabled() -> bool:
    raw = os.environ.get("JACHIN_ENABLE_LLM_PENDING_RESOLVER", "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    try:
        from l3_node.intent_gateway.config import get_intent_gateway_config

        return bool(get_intent_gateway_config().get("os_mission_llm_pending_resolver_enabled", True))
    except Exception:
        return True


def _resolver_config() -> tuple[float, int, str | None]:
    try:
        from l3_node.intent_gateway.config import get_intent_gateway_config
        from l3_node.intent_gateway.model_resolve import get_classification_model_litellm_id

        cfg = get_intent_gateway_config()
        timeout_sec = float(cfg.get("os_mission_llm_pending_resolver_timeout_sec", 4.0))
        max_tokens = int(cfg.get("os_mission_llm_pending_resolver_max_tokens", 700))
        model = get_classification_model_litellm_id()
    except Exception:
        timeout_sec = 4.0
        max_tokens = 700
        model = None
    return max(0.5, min(timeout_sec, 12.0)), max(180, min(max_tokens, 1400)), model


def _context_for_prompt(pending: dict[str, Any], user_input: str) -> str:
    context = {
        "pending_intent": pending.get("intent") if isinstance(pending.get("intent"), dict) else {},
        "pending_route": pending.get("route") if isinstance(pending.get("route"), dict) else {},
        "pending_reason": pending.get("pending_reason"),
        "initial_user_input": pending.get("initial_user_input"),
        "history": pending.get("history") if isinstance(pending.get("history"), list) else [],
        "latest_user_input": str(user_input or ""),
    }
    return json.dumps(context, ensure_ascii=False, indent=2)[:8000]


async def resolve_pending_intent_async(
    pending: dict[str, Any],
    user_input: str,
    *,
    engine: Any | None = None,
) -> PendingResolverResult:
    if not _resolver_enabled():
        return PendingResolverResult(used=False, source="llm", error="disabled")
    if engine is None:
        return PendingResolverResult(used=False, source="llm", error="no_engine")
    timeout_sec, max_tokens, model = _resolver_config()
    system_prompt = (
        "You are Jachin's pending OS mission resolver. Output exactly one JSON object and no prose. "
        "Resolve the latest user utterance against the existing pending mission, not as an isolated request. "
        "Use only this fixed schema: continue_pending, operation, filled_slots, slot_confidence, ambiguous_slots, "
        "needs_clarification, clarification_question, reasoning_summary. "
        "operation must be one of: fill_slots, ask_clarification, new_task, cancel. "
        "filled_slots may only contain: project_name, project_path, directory_path, feature_query, bug_query, "
        "recipients, message, file_path, app_name, since_days, output_format, expression. "
        "For external-effect tasks such as sending messages or files, never guess a recipient or content. "
        "Pronouns such as he/she/him/her/it/that person/ta are only valid when the pending context uniquely identifies them. "
        "Separate command words from payload: if the user says to send a test message, message should be the payload, not the whole command. "
        "If any external-effect slot is ambiguous or low confidence, set needs_clarification=true and ask one short question."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Pending context and latest utterance:\n" + _context_for_prompt(pending, user_input)},
    ]

    async def _call() -> str:
        kwargs = {
            "temperature": 0.0,
            "max_tokens": max_tokens,
            "l3_call_purpose": "os_mission_pending_resolver",
        }
        if model:
            kwargs["l3_override_model"] = model
        raw = await engine.generate_response(messages, tools=None, **kwargs)
        if isinstance(raw, dict):
            return str(raw.get("content") or "")
        return str(raw or "")

    try:
        raw_response = await asyncio.wait_for(_call(), timeout=timeout_sec)
    except asyncio.TimeoutError:
        logger.info("[PendingResolver] LLM resolver timeout %.1fs", timeout_sec)
        return PendingResolverResult(used=False, source="llm", error="timeout")
    except Exception as exc:
        logger.info("[PendingResolver] LLM resolver failed: %s", str(exc)[:200])
        return PendingResolverResult(used=False, source="llm", error=str(exc)[:300])
    data = _parse_json_loose(raw_response)
    if not data:
        return PendingResolverResult(used=False, source="llm", raw_response=raw_response, error="invalid_json")
    return _result_from_dict(data, raw_response)


def apply_resolver_slots(intent: MissionIntent, result: PendingResolverResult) -> tuple[MissionIntent, list[dict[str, Any]]]:
    changes: list[dict[str, Any]] = []
    for slot, value in result.filled_slots.items():
        if slot not in _ALLOWED_SLOTS:
            continue
        before = getattr(intent.slots, slot)
        if before == value:
            continue
        setattr(intent.slots, slot, value)
        intent.missing_slots = [item for item in intent.missing_slots if item != slot]
        changes.append(
            {
                "slot": slot,
                "before": before,
                "after": value,
                "source": "llm_pending_resolver",
                "confidence": result.slot_confidence.get(slot),
            }
        )
    if changes and "pending_slot_fill:llm_resolver" not in intent.reasoning:
        intent.reasoning.append("pending_slot_fill:llm_resolver")
    return intent, changes


def resolver_needs_clarification(
    intent: MissionIntent,
    result: PendingResolverResult,
    *,
    min_external_confidence: float = 0.80,
) -> bool:
    if result.needs_clarification or result.ambiguous_slots or result.operation == "ask_clarification":
        return True
    if not external_effect_intent(intent):
        return False
    for slot, value in result.filled_slots.items():
        if not value:
            return True
        confidence = result.slot_confidence.get(slot)
        if confidence is not None and confidence < min_external_confidence:
            return True
    return False
