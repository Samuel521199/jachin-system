"""Two-layer semantic intent engine.

Layer 1 is the deterministic parser in semantic_slot_parser.py.
Layer 2 is an optional LLM parser hook. It is intentionally disabled unless an
environment flag is set, and it must return the same MissionIntent schema.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from l3_node.mission_intent_schema import MissionIntent, MissionTaskType
from l3_node.semantic_slot_parser import parse_mission_intent
from l3_node.task_understanding_engine import infer_task_understanding


@dataclass
class SemanticIntentResult:
    intent: MissionIntent
    meta: dict[str, Any] = field(default_factory=dict)


def _llm_parser_enabled() -> bool:
    return os.environ.get("JACHIN_ENABLE_LLM_INTENT_PARSER", "").strip().lower() in {"1", "true", "yes", "on"}


def _try_llm_parse(user_input: str) -> tuple[MissionIntent | None, dict[str, Any]]:
    # The hook is deliberately conservative. A real provider can be connected
    # behind this function, but the router contract already records whether the
    # LLM layer was attempted and why rules won.
    if not _llm_parser_enabled():
        return None, {"enabled": False, "status": "disabled"}
    return None, {"enabled": True, "status": "provider_not_configured"}


def parse_semantic_intent(user_input: str) -> SemanticIntentResult:
    rule_intent = parse_mission_intent(user_input)
    understanding = infer_task_understanding(user_input)
    llm_intent, llm_meta = _try_llm_parse(user_input)

    chosen = rule_intent
    decision = "rule_only"
    disagreement: dict[str, Any] = {}
    if llm_intent and llm_intent.task_type != MissionTaskType.UNKNOWN:
        if rule_intent.task_type == MissionTaskType.UNKNOWN or llm_intent.confidence > rule_intent.confidence + 0.1:
            chosen = llm_intent
            decision = "llm_preferred"
        elif llm_intent.task_type != rule_intent.task_type:
            disagreement = {
                "rule_task_type": rule_intent.task_type.value,
                "llm_task_type": llm_intent.task_type.value,
            }
            decision = "rule_preferred_due_to_disagreement"
        else:
            decision = "rule_preferred_same_task"

    meta = {
        "parser_layers": ["rules", "llm"],
        "decision": decision,
        "rule": {"task_type": rule_intent.task_type.value, "confidence": rule_intent.confidence},
        "task_understanding": understanding.to_dict(),
        "llm": llm_meta,
        "disagreement": disagreement,
    }
    return SemanticIntentResult(chosen, meta)
