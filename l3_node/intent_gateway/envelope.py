"""§5.3.1 IntentEnvelope / SubIntentNode（结构化复合意图，可选）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


LocalityHint = Literal["local_only", "prefer_l2", "require_l2_task_manager", "edge_sensor", "unspecified"]


@dataclass
class SubIntentNode:
    id: str
    text_span: str = ""
    rewritten_text: str = ""
    what: str = ""
    locality: LocalityHint = "unspecified"
    depends_on: list[str] = field(default_factory=list)
    planning_requirement: str = "none"  # none | optional | mandatory
    rbac_scope_hint: str = ""
    is_compensable: bool = False
    compensation_action_id: str = ""  # §11.3 Registry 枚举，禁止 LLM 自由文本
    # LLM 拆分：参数/产物绑定，用于依赖边后修复（from_sub_intent → depends_on）
    preconditions: list[dict[str, Any]] = field(default_factory=list)
    # §7.1：子意图级槽位模式（与 IntentRegistry.required_slots 同形：name、pattern、prompt_template…）
    slot_schema: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class IntentEnvelope:
    correlation_id: str = ""
    session_id: str = ""
    raw_user_input: str = ""
    routing_utterance: str = ""
    sub_intents: list[SubIntentNode] = field(default_factory=list)
    edges: list[tuple[str, str]] | None = None
    payload_redaction: dict[str, Any] = field(default_factory=dict)
