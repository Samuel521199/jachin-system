"""Two-layer semantic intent engine.

Layer 1 is the deterministic parser in semantic_slot_parser.py.
Layer 2 is an optional LLM parser that returns the same MissionIntent schema.
The LLM layer is a semantic candidate generator only; tool execution still goes
through deterministic capability routing, risk gates, and consistency checks.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from l3_node.mission_intent_schema import MissionIntent, MissionRiskLevel, MissionSlots, MissionTaskType
from l3_node.semantic_slot_parser import parse_mission_intent
from l3_node.task_understanding_engine import infer_task_understanding

logger = logging.getLogger(__name__)

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


@dataclass
class SemanticIntentResult:
    intent: MissionIntent
    meta: dict[str, Any] = field(default_factory=dict)


def _llm_parser_enabled() -> bool:
    raw = os.environ.get("JACHIN_ENABLE_LLM_INTENT_PARSER", "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    try:
        from l3_node.intent_gateway.config import get_intent_gateway_config

        return bool(get_intent_gateway_config().get("os_mission_llm_intent_enabled", True))
    except Exception:
        return True


def _try_llm_parse(user_input: str) -> tuple[MissionIntent | None, dict[str, Any]]:
    if not _llm_parser_enabled():
        return None, {"enabled": False, "status": "disabled"}
    return None, {"enabled": True, "status": "engine_required_for_async_parse"}


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


def _coerce_task_type(raw: Any) -> MissionTaskType:
    val = str(raw or "").strip().lower()
    for item in MissionTaskType:
        if val == item.value:
            return item
    return MissionTaskType.UNKNOWN


def _coerce_risk_level(raw: Any) -> MissionRiskLevel:
    val = str(raw or "").strip().lower()
    for item in MissionRiskLevel:
        if val == item.value:
            return item
    return MissionRiskLevel.LOW


def _clean_str(raw: Any, max_len: int = 2000) -> str:
    return str(raw or "").strip()[:max_len]


def _clean_str_list(raw: Any, max_n: int = 8) -> list[str]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw[:max_n]:
        s = _clean_str(item, 200)
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


def _intent_from_llm_dict(data: dict[str, Any], raw_text: str) -> MissionIntent | None:
    task_type = _coerce_task_type(data.get("task_type"))
    if task_type == MissionTaskType.UNKNOWN:
        return None
    slots_raw = data.get("slots") if isinstance(data.get("slots"), dict) else {}
    slots = MissionSlots(
        project_name=_clean_str(slots_raw.get("project_name"), 200),
        project_path=_clean_str(slots_raw.get("project_path"), 1000),
        directory_path=_clean_str(slots_raw.get("directory_path"), 1000),
        feature_query=_clean_str(slots_raw.get("feature_query"), 2000),
        bug_query=_clean_str(slots_raw.get("bug_query"), 2000),
        recipients=_clean_str_list(slots_raw.get("recipients")),
        message=_clean_str(slots_raw.get("message"), 4000),
        file_path=_clean_str(slots_raw.get("file_path"), 1000),
        app_name=_clean_str(slots_raw.get("app_name"), 200),
        output_format=_clean_str(slots_raw.get("output_format"), 200),
        expression=_clean_str(slots_raw.get("expression"), 500),
    )
    try:
        slots.since_days = max(1, min(30, int(slots_raw.get("since_days") or 3)))
    except (TypeError, ValueError):
        slots.since_days = 3
    try:
        confidence = float(data.get("confidence"))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(0.98, confidence))
    missing = _clean_str_list(data.get("missing_slots"), 12)
    reasoning = _clean_str_list(data.get("reasoning"), 10)
    if data.get("goal"):
        reasoning.append("llm_goal=" + _clean_str(data.get("goal"), 300))
    if data.get("success_condition"):
        reasoning.append("llm_success_condition=" + _clean_str(data.get("success_condition"), 300))
    return MissionIntent(
        task_type=task_type,
        confidence=confidence,
        slots=slots,
        missing_slots=missing,
        risk_level=_coerce_risk_level(data.get("risk_level")),
        reasoning=reasoning or ["llm semantic intent candidate"],
        raw_text=raw_text,
    )


async def _try_llm_parse_async(user_input: str, engine: Any | None) -> tuple[MissionIntent | None, dict[str, Any]]:
    if not _llm_parser_enabled():
        return None, {"enabled": False, "status": "disabled"}
    if engine is None:
        return None, {"enabled": True, "status": "no_engine"}
    text = str(user_input or "").strip()
    if not text:
        return None, {"enabled": True, "status": "empty_input"}
    try:
        from l3_node.intent_gateway.config import get_intent_gateway_config
        from l3_node.intent_gateway.model_resolve import get_classification_model_litellm_id

        cfg = get_intent_gateway_config()
        timeout_sec = float(cfg.get("os_mission_llm_intent_timeout_sec", 4.0))
        max_tokens = int(cfg.get("os_mission_llm_intent_max_tokens", 600))
        model = get_classification_model_litellm_id()
    except Exception:
        timeout_sec = 4.0
        max_tokens = 600
        model = None
    timeout_sec = max(0.5, min(timeout_sec, 12.0))
    max_tokens = max(160, min(max_tokens, 1200))
    system_prompt = (
        "You are Jachin's OS mission intent parser. Output exactly one JSON object and no prose. "
        "Your job is semantic understanding, not tool execution. Distinguish the user's final goal from a mentioned method/tool. "
        "Valid task_type values: unknown, project_briefing_delivery, codex_ask_lark_send, project_memory_update, lark_message_send, "
        "calculator_calculate, file_to_app, app_control, system_status_report. "
        "If the user asks to use/open an app in order to get a result, choose the task that satisfies the result, not app_control. "
        "For requests like ask Codex a question then send its reply through Lark/Feishu, choose codex_ask_lark_send, put the question in slots.feature_query, and recipients in slots.recipients. "
        "For spoken arithmetic such as Chinese numbers or words like plus/minus/times/divide, normalize slots.expression to ASCII digits/operators. "
        "Use app_control only when opening/focusing the app is itself the final goal. "
        "For external communication, require explicit recipient and message. "
        "Return keys: task_type, confidence, slots, missing_slots, risk_level, reasoning, goal, success_condition. "
        "slots keys: project_name, project_path, directory_path, feature_query, bug_query, recipients, message, file_path, app_name, since_days, output_format, expression."
    )
    user_prompt = f"User utterance:\n{text[:4000]}"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    async def _call() -> str:
        kwargs = {
            "temperature": 0.0,
            "max_tokens": max_tokens,
            "l3_call_purpose": "os_mission_intent_parser",
        }
        if model:
            kwargs["l3_override_model"] = model
        raw = await engine.generate_response(messages, tools=None, **kwargs)
        if isinstance(raw, dict):
            return str(raw.get("content") or "")
        return str(raw or "")

    try:
        raw_text = await asyncio.wait_for(_call(), timeout=timeout_sec)
    except asyncio.TimeoutError:
        logger.info("[SemanticIntent] LLM intent parser timeout %.1fs", timeout_sec)
        return None, {"enabled": True, "status": "timeout", "timeout_sec": timeout_sec}
    except Exception as exc:
        logger.info("[SemanticIntent] LLM intent parser failed: %s", str(exc)[:200])
        return None, {"enabled": True, "status": "error", "error": str(exc)[:300]}
    data = _parse_json_loose(raw_text)
    if not data:
        return None, {"enabled": True, "status": "invalid_json", "raw_preview": raw_text[:500]}
    intent = _intent_from_llm_dict(data, text)
    if intent is None:
        return None, {"enabled": True, "status": "unknown_or_invalid_task", "raw": data}
    return intent, {
        "enabled": True,
        "status": "parsed",
        "task_type": intent.task_type.value,
        "confidence": intent.confidence,
        "goal": _clean_str(data.get("goal"), 500),
        "success_condition": _clean_str(data.get("success_condition"), 500),
    }


def _specificity(intent: MissionIntent) -> int:
    if intent.task_type == MissionTaskType.UNKNOWN:
        return 0
    if intent.task_type == MissionTaskType.APP_CONTROL:
        return 1
    if intent.task_type in {MissionTaskType.SYSTEM_STATUS_REPORT, MissionTaskType.PROJECT_MEMORY_UPDATE}:
        return 2
    if intent.task_type in {MissionTaskType.FILE_TO_APP, MissionTaskType.LARK_MESSAGE_SEND, MissionTaskType.PROJECT_BRIEFING_DELIVERY}:
        return 3
    if intent.task_type == MissionTaskType.CODEX_ASK_LARK_SEND:
        return 4
    if intent.task_type == MissionTaskType.CALCULATOR_CALCULATE:
        return 4
    return 2


_ACTION_WORD_RE = re.compile(
    r"(\u6253\u5f00|\u542f\u52a8|\u8fd0\u884c|\u5207\u6362|\u8ba1\u7b97|\u6d4f\u89c8\u5668|\u8ba1\u7b97\u5668|windows|codex|lark|feishu|"
    r"\u98de\u4e66|\u53d1\u9001|\u7ed9\u6211|open|launch|run|switch|calculate|calculator|browser|message)",
    re.I,
)


def _recipient_looks_dirty(value: str) -> bool:
    s = str(value or "").strip()
    if not s:
        return True
    if len(s) > 80:
        return True
    return bool(_ACTION_WORD_RE.search(s))


def _message_is_placeholder(value: str) -> bool:
    s = re.sub(r"\s+", "", str(value or "").strip().lower())
    return s in {
        "",
        "\u6d88\u606f",
        "\u4e00\u6761\u6d88\u606f",
        "\u53d1\u4e00\u6761\u6d88\u606f",
        "\u53d1\u9001\u4e00\u6761\u6d88\u606f",
        "message",
        "a message",
        "amessage",
        "one-message",
    }

def _merge_lark_slots(primary: MissionIntent, secondary: MissionIntent) -> MissionIntent:
    """Keep LLM semantic slots as primary, only borrowing clean deterministic slots."""
    slots = primary.slots
    secondary_slots = secondary.slots

    primary_recipients = [r for r in slots.recipients if not _recipient_looks_dirty(r)]
    secondary_recipients = [r for r in secondary_slots.recipients if not _recipient_looks_dirty(r)]
    if primary_recipients:
        slots.recipients = primary_recipients
    elif secondary_recipients:
        slots.recipients = secondary_recipients
    else:
        slots.recipients = []

    primary_message = "" if _message_is_placeholder(slots.message) else str(slots.message or "").strip()
    secondary_message = "" if _message_is_placeholder(secondary_slots.message) else str(secondary_slots.message or "").strip()
    slots.message = primary_message or secondary_message

    missing: list[str] = []
    if not slots.recipients:
        missing.append("recipients")
    if not slots.message:
        missing.append("message")
    primary.missing_slots = missing
    if "slot_merge:lark_message_send" not in primary.reasoning:
        primary.reasoning.append("slot_merge:lark_message_send")
    return primary


def _repair_same_task_slots(primary: MissionIntent, secondary: MissionIntent) -> MissionIntent:
    if primary.task_type == MissionTaskType.LARK_MESSAGE_SEND:
        return _merge_lark_slots(primary, secondary)
    return primary


def _rule_has_bad_lark_slots(rule_intent: MissionIntent) -> bool:
    if rule_intent.task_type != MissionTaskType.LARK_MESSAGE_SEND:
        return False
    if any(_recipient_looks_dirty(r) for r in rule_intent.slots.recipients):
        return True
    return _message_is_placeholder(rule_intent.slots.message)

def _choose_between_rule_and_llm(
    rule_intent: MissionIntent,
    llm_intent: MissionIntent | None,
) -> tuple[MissionIntent, str, dict[str, Any]]:
    if not llm_intent or llm_intent.task_type == MissionTaskType.UNKNOWN:
        return rule_intent, "rule_only", {}
    if rule_intent.task_type == MissionTaskType.UNKNOWN:
        return llm_intent, "llm_preferred_rule_unknown", {}
    if llm_intent.task_type == rule_intent.task_type:
        if (
            llm_intent.confidence >= 0.70
            and rule_intent.task_type == MissionTaskType.LARK_MESSAGE_SEND
            and _rule_has_bad_lark_slots(rule_intent)
        ):
            return _repair_same_task_slots(llm_intent, rule_intent), "llm_preferred_same_task_cleaner_slots", {}
        if (
            rule_intent.task_type == MissionTaskType.CALCULATOR_CALCULATE
            and llm_intent.confidence >= 0.70
            and not llm_intent.missing_slots
            and len(str(llm_intent.slots.expression or "")) > len(str(rule_intent.slots.expression or ""))
        ):
            return llm_intent, "llm_preferred_more_specific_goal", {}
        if llm_intent.confidence >= rule_intent.confidence and not llm_intent.missing_slots:
            return _repair_same_task_slots(llm_intent, rule_intent), "llm_preferred_same_task_higher_confidence", {}
        return rule_intent, "rule_preferred_same_task", {}

    disagreement = {
        "rule_task_type": rule_intent.task_type.value,
        "llm_task_type": llm_intent.task_type.value,
        "rule_confidence": rule_intent.confidence,
        "llm_confidence": llm_intent.confidence,
        "rule_specificity": _specificity(rule_intent),
        "llm_specificity": _specificity(llm_intent),
    }
    if rule_intent.task_type == MissionTaskType.CODEX_ASK_LARK_SEND and llm_intent.task_type in {
        MissionTaskType.APP_CONTROL,
        MissionTaskType.LARK_MESSAGE_SEND,
    }:
        return rule_intent, "rule_preferred_composite_mission", disagreement
    if (
        _specificity(llm_intent) > _specificity(rule_intent)
        and llm_intent.confidence >= 0.65
        and len(llm_intent.missing_slots) <= len(rule_intent.missing_slots)
    ):
        return llm_intent, "llm_preferred_more_specific_goal", disagreement
    if llm_intent.confidence > rule_intent.confidence + 0.1 and not llm_intent.missing_slots:
        return llm_intent, "llm_preferred_higher_confidence", disagreement
    return rule_intent, "rule_preferred_due_to_disagreement", disagreement


def parse_semantic_intent(user_input: str) -> SemanticIntentResult:
    rule_intent = parse_mission_intent(user_input)
    understanding = infer_task_understanding(user_input)
    llm_intent, llm_meta = _try_llm_parse(user_input)
    chosen, decision, disagreement = _choose_between_rule_and_llm(rule_intent, llm_intent)

    meta = {
        "parser_layers": ["rules", "llm"],
        "decision": decision,
        "rule": {"task_type": rule_intent.task_type.value, "confidence": rule_intent.confidence},
        "task_understanding": understanding.to_dict(),
        "llm": llm_meta,
        "llm_intent": llm_intent.to_dict() if llm_intent is not None else None,
        "disagreement": disagreement,
    }
    return SemanticIntentResult(chosen, meta)


async def parse_semantic_intent_async(user_input: str, *, engine: Any | None = None) -> SemanticIntentResult:
    rule_intent = parse_mission_intent(user_input)
    understanding = infer_task_understanding(user_input)
    llm_intent, llm_meta = await _try_llm_parse_async(user_input, engine)
    chosen, decision, disagreement = _choose_between_rule_and_llm(rule_intent, llm_intent)
    meta = {
        "parser_layers": ["rules", "llm"],
        "decision": decision,
        "rule": {"task_type": rule_intent.task_type.value, "confidence": rule_intent.confidence},
        "task_understanding": understanding.to_dict(),
        "llm": llm_meta,
        "llm_intent": llm_intent.to_dict() if llm_intent is not None else None,
        "disagreement": disagreement,
    }
    return SemanticIntentResult(chosen, meta)

