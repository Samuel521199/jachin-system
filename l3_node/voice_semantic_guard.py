"""Chinese semantic safeguards for voice mission routing.

The guard is intentionally deterministic. It protects the router from common
STT/ASR semantic traps such as "give me" being misread as a recipient and
negated delivery/app commands being executed anyway.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from l3_node.mission_intent_schema import MissionIntent, MissionTaskType

_SELF_RECIPIENTS = {"我", "我这边", "自己", "me", "myself"}
_NEGATION_RE = re.compile(r"(?:不要|别|不用|不需要|无需|禁止|不是|先别|do\s*not|don't|dont|no)", re.I)
_SEND_RE = re.compile(r"(?:\u53d1\u6d88\u606f|\u53d1\u9001\u6d88\u606f|\u53d1\u4fe1\u606f|\u53d1\u9001\u4fe1\u606f|\u53d1\u7ed9|\u53d1\u9001\u7ed9|\u8f6c\u53d1|send|message)", re.I)
_APP_RE = re.compile(r"(?:打开|启动|切换到|运行|open|launch|start|switch\s+to)", re.I)


@dataclass(frozen=True)
class SemanticGuardDecision:
    blocked: bool
    reason: str = ""
    normalized_task_type: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def _has_negated_action(text: str) -> bool:
    raw = str(text or "")
    return bool(_NEGATION_RE.search(raw) and (_SEND_RE.search(raw) or _APP_RE.search(raw)))


def _self_recipient_only(intent: MissionIntent) -> bool:
    recipients = [str(r).strip().lower() for r in intent.slots.recipients if str(r).strip()]
    return bool(recipients) and all(r in _SELF_RECIPIENTS for r in recipients)


def apply_voice_semantic_guard(intent: MissionIntent, *, raw_text: str = "") -> MissionIntent:
    """Return a guarded intent, blocking unsafe semantic fallthroughs.

    The parser may still extract slots from a noisy transcript. This function
    does not try to parse new tasks; it only downgrades unsafe parses to
    UNKNOWN so the clarification path can take over.
    """
    text = raw_text or intent.raw_text or ""
    reasons: list[str] = []
    if intent.task_type in {MissionTaskType.LARK_MESSAGE_SEND, MissionTaskType.PROJECT_BRIEFING_DELIVERY, MissionTaskType.CODEX_ASK_LARK_SEND}:
        if _self_recipient_only(intent) and not re.search(r"(?:发给我自己|发送给我自己|send\s+to\s+myself)", text, re.I):
            reasons.append("self_recipient_not_delivery_target")
        if _has_negated_action(text):
            reasons.append("negated_send_or_delivery")
    elif intent.task_type == MissionTaskType.APP_CONTROL and _has_negated_action(text):
        reasons.append("negated_app_control")

    compact = _compact(text)
    if intent.task_type == MissionTaskType.LARK_MESSAGE_SEND and re.search(r"(?:给我|帮我|麻烦我?)发(?:一条)?消息", compact):
        reasons.append("geiwo_send_message_is_self_request")

    if not reasons:
        return intent

    guarded = MissionIntent(
        task_type=MissionTaskType.UNKNOWN,
        confidence=min(intent.confidence, 0.25),
        slots=intent.slots,
        missing_slots=list(dict.fromkeys([*intent.missing_slots, "clarification"])),
        risk_level=intent.risk_level,
        reasoning=[*intent.reasoning, *reasons],
        raw_text=intent.raw_text or text,
    )
    return guarded


def semantic_guard_payload(intent: MissionIntent, *, raw_text: str = "") -> dict[str, Any]:
    guarded = apply_voice_semantic_guard(intent, raw_text=raw_text)
    return SemanticGuardDecision(
        blocked=guarded.task_type == MissionTaskType.UNKNOWN and intent.task_type != MissionTaskType.UNKNOWN,
        reason=",".join(r for r in guarded.reasoning if r not in intent.reasoning),
        normalized_task_type=guarded.task_type.value,
    ).to_dict()
