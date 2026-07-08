"""Policy layer for desktop companion follow-up questions.

This module decides whether the companion should ask a follow-up question.
It does not generate final prose for the assistant. Instead it exposes a
small, testable contract: rules set the boundary, the LLM may phrase the
question naturally inside that boundary.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


FollowupType = Literal[
    "none",
    "task_clarification",
    "safety_confirmation",
    "companion_emotional",
    "companion_preference",
    "companion_context",
    "proactive_status",
]

FollowupMode = Literal["no_followup", "rule_boundary", "model_expression", "strategy_prompt"]
FollowupRisk = Literal["none", "low", "medium", "high"]


@dataclass(frozen=True)
class VoiceFollowupDecision:
    should_ask: bool
    followup_type: FollowupType = "none"
    mode: FollowupMode = "no_followup"
    risk: FollowupRisk = "none"
    max_rounds: int = 0
    question_goal: str = ""
    suggested_question: str = ""
    constraints: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_SEND_OR_EXTERNAL_RE = re.compile(
    r"(\u53d1\u6d88\u606f|\u53d1\u9001|\u53d1\u7ed9|\u8f6c\u53d1|\u53d1\u90ae\u4ef6|"
    r"\u5220\u9664|\u4fee\u6539|\u63d0\u4ea4|\u53d1\u5e03|send|email|delete|publish)",
    re.I,
)
_TASK_ACTION_RE = re.compile(
    r"(\u6253\u5f00|\u67e5\u627e|\u641c\u7d22|\u8fd0\u884c|\u6267\u884c|\u751f\u6210|"
    r"\u603b\u7ed3|\u5206\u6790|\u6574\u7406|\u53d1\u6d88\u606f|\u63d0\u9192|open|run|search|send)",
    re.I,
)
_EMOTION_RE = re.compile(
    r"(\u7d2f|\u56f0|\u96be\u53d7|\u70e6|\u7126\u8651|\u5d29\u6e83|\u6491\u4e0d\u4f4f|"
    r"\u4e0d\u5f00\u5fc3|\u5fc3\u60c5\u4e0d\u597d|\u538b\u529b|\u59d4\u5c48|\u60f3\u54ed|"
    r"tired|sad|anxious|stress|stressed|exhausted)",
    re.I,
)
_ADVICE_RE = re.compile(
    r"(\u600e\u4e48\u529e|\u600e\u4e48\u5904\u7406|\u5e2e\u6211\u60f3|\u7ed9\u6211\u5efa\u8bae|"
    r"\u4f60\u89c9\u5f97|\u8981\u4e0d\u8981|what should i do|advice)",
    re.I,
)
_AMBIGUOUS_REFERENCE_RE = re.compile(
    r"(\u90a3\u4e2a|\u8fd9\u4e2a|\u521a\u624d\u90a3\u4e2a|\u4e0a\u4e00\u4e2a|\u5b83|"
    r"\u4ed6|\u5979|\u90a3\u4ef6\u4e8b|\u8fd9\u4ef6\u4e8b|that one|this one)",
    re.I,
)
_CANCEL_RE = re.compile(r"(\u7b97\u4e86|\u4e0d\u7528|\u6ca1\u4e8b|\u53d6\u6d88|forget it|cancel)", re.I)


def _ctx_bool(ctx: dict[str, Any], *keys: str) -> bool:
    return any(bool(ctx.get(key)) for key in keys)


def _ctx_text(ctx: dict[str, Any], *keys: str) -> str:
    for key in keys:
        val = ctx.get(key)
        if val is not None:
            s = str(val).strip()
            if s:
                return s
    return ""


def _followup_rounds(ctx: dict[str, Any]) -> int:
    for key in ("voice_followup_rounds", "companion_followup_rounds", "slot_clarification_rounds"):
        try:
            return max(0, int(ctx.get(key) or 0))
        except (TypeError, ValueError):
            continue
    return 0


def _has_active_task(ctx: dict[str, Any]) -> bool:
    ids = ctx.get("active_task_ids")
    if isinstance(ids, (list, tuple)) and ids:
        return True
    return bool(_ctx_text(ctx, "target_task_id", "task_context_summary"))


def _question_for_task_clarification(ctx: dict[str, Any]) -> str:
    q = _ctx_text(ctx, "voice_stt_user_message", "clarification_question")
    if q:
        return q
    return "\u8fd9\u53e5\u6211\u6ca1\u5b8c\u5168\u542c\u6e05\uff0c\u4f60\u8981\u6211\u8865\u54ea\u4e2a\u4fe1\u606f\uff1f"


def decide_voice_followup_policy(user_text: str, context: dict[str, Any] | None = None) -> VoiceFollowupDecision:
    """Return the follow-up policy for the current companion turn.

    The ordering is intentional: hard execution safety beats natural companion
    curiosity. Companion follow-ups only run for low-risk, non-task utterances.
    """
    ctx = context if isinstance(context, dict) else {}
    text = str(user_text or "").strip()
    routed_text = _ctx_text(ctx, "voice_routed_text", "voice_corrected_text", "voice_final_text")
    probe = "\n".join(x for x in (text, routed_text) if x)
    rounds = _followup_rounds(ctx)

    if not text and not routed_text:
        return VoiceFollowupDecision(False, reasons=["empty_text"])

    if bool(ctx.get("voice_reply_composer")):
        return VoiceFollowupDecision(False, reasons=["reply_composer_turn"])

    if _CANCEL_RE.search(probe):
        return VoiceFollowupDecision(False, reasons=["user_cancel_or_dismiss"])

    if rounds >= 2:
        return VoiceFollowupDecision(
            False,
            reasons=["followup_round_budget_exhausted"],
            constraints=["Do not ask again for this same turn; answer or gracefully park it."],
        )

    if _ctx_bool(ctx, "clarification_pending") or _ctx_text(ctx, "voice_stt_user_message", "clarification_question"):
        return VoiceFollowupDecision(
            True,
            followup_type="task_clarification",
            mode="rule_boundary",
            risk="medium",
            max_rounds=2,
            question_goal="Collect the missing slot or clarify the ambiguous execution target.",
            suggested_question=_question_for_task_clarification(ctx),
            constraints=[
                "Ask only for the missing slot; do not execute yet.",
                "Keep the spoken question under one short sentence.",
            ],
            reasons=["pending_or_stt_clarification"],
        )

    intent_class = _ctx_text(ctx, "voice_intent_class").upper()
    lane = _ctx_text(ctx, "voice_dispatch_lane").lower()
    if (
        _ctx_bool(ctx, "awaiting_confirmation")
        or _SEND_OR_EXTERNAL_RE.search(probe)
        or intent_class in {"TASK_SYNC", "TASK_ASYNC", "CONTROL"}
        or lane in {"foreground", "background_submit", "background_control"}
    ):
        if _SEND_OR_EXTERNAL_RE.search(probe) or _ctx_bool(ctx, "awaiting_confirmation"):
            return VoiceFollowupDecision(
                True,
                followup_type="safety_confirmation",
                mode="rule_boundary",
                risk="high",
                max_rounds=1,
                question_goal="Confirm the final target and content before any external or irreversible action.",
                suggested_question="\u6211\u5148\u786e\u8ba4\u4e00\u4e0b\u5bf9\u8c61\u548c\u5185\u5bb9\uff0c\u518d\u6267\u884c\u3002\u5bf9\u5417\uff1f",
                constraints=[
                    "Never silently perform external side effects.",
                    "Mention the final object and content if known.",
                    "Do not ask broad companion questions in this branch.",
                ],
                reasons=["external_or_confirmation_required"],
            )
        return VoiceFollowupDecision(False, reasons=["task_route_without_missing_slot"])

    if _TASK_ACTION_RE.search(probe):
        return VoiceFollowupDecision(False, reasons=["explicit_task_action_no_companion_followup"])

    if _EMOTION_RE.search(probe):
        return VoiceFollowupDecision(
            True,
            followup_type="companion_emotional",
            mode="model_expression",
            risk="low",
            max_rounds=1,
            question_goal="Understand whether the user wants listening, practical help, or a lighter topic.",
            suggested_question="\u542c\u8d77\u6765\u4f60\u6709\u70b9\u7d2f\u4e86\uff0c\u60f3\u8ba9\u6211\u542c\u4f60\u8bf4\uff0c\u8fd8\u662f\u5e2e\u4f60\u628a\u4e8b\u60c5\u7406\u4e00\u7406\uff1f",
            constraints=[
                "Validate the emotion first.",
                "Ask at most one gentle question.",
                "Do not turn it into a task unless the user chooses that.",
            ],
            reasons=["emotion_signal"],
        )

    if _ADVICE_RE.search(probe):
        return VoiceFollowupDecision(
            True,
            followup_type="companion_preference",
            mode="model_expression",
            risk="low",
            max_rounds=1,
            question_goal="Learn whether the user wants direct advice or reflective sorting.",
            suggested_question="\u4f60\u60f3\u8981\u6211\u76f4\u63a5\u7ed9\u5efa\u8bae\uff0c\u8fd8\u662f\u5148\u966a\u4f60\u628a\u601d\u8def\u7406\u6e05\uff1f",
            constraints=[
                "Offer two clear response styles.",
                "Keep it short enough for TTS.",
            ],
            reasons=["advice_preference_signal"],
        )

    if _AMBIGUOUS_REFERENCE_RE.search(probe):
        return VoiceFollowupDecision(
            True,
            followup_type="companion_context",
            mode="model_expression",
            risk="low",
            max_rounds=1,
            question_goal="Resolve the ambiguous reference without guessing.",
            suggested_question="\u4f60\u8bf4\u7684\u201c\u90a3\u4e2a\u201d\uff0c\u662f\u6307\u521a\u624d\u7684\u4efb\u52a1\uff0c\u8fd8\u662f\u53e6\u4e00\u4ef6\u4e8b\uff1f",
            constraints=[
                "Ask only about the reference.",
                "Do not infer a specific object when multiple are possible.",
            ],
            reasons=["ambiguous_reference"],
        )

    if _has_active_task(ctx) and intent_class == "CHITCHAT":
        return VoiceFollowupDecision(
            False,
            followup_type="proactive_status",
            mode="strategy_prompt",
            risk="low",
            max_rounds=0,
            question_goal="Optionally mention background task status if natural; do not force a question.",
            constraints=[
                "Do not end every chitchat turn with a question.",
                "Mention task status only if it fits naturally.",
            ],
            reasons=["active_task_context_available"],
        )

    return VoiceFollowupDecision(False, reasons=["no_followup_needed"])


def build_voice_followup_prompt_block(decision: VoiceFollowupDecision) -> str:
    """Render a compact prompt block for L3 companion mode."""
    if not decision.should_ask and decision.followup_type != "proactive_status":
        return ""

    data = decision.to_dict()
    return (
        "\n[Voice Follow-up Policy]\n"
        "Rules set the boundary; phrase naturally but do not exceed the boundary.\n"
        f"should_ask={str(data['should_ask']).lower()}; "
        f"type={data['followup_type']}; mode={data['mode']}; risk={data['risk']}; "
        f"max_rounds={data['max_rounds']}.\n"
        f"goal={data['question_goal']}\n"
        f"suggested_question={data['suggested_question']}\n"
        "constraints=" + json_like_list(data["constraints"]) + "\n"
    )


def json_like_list(items: list[str]) -> str:
    escaped = [item.replace("\\", "\\\\").replace('"', '\\"') for item in items]
    return "[" + ", ".join(f'"{item}"' for item in escaped) + "]"
