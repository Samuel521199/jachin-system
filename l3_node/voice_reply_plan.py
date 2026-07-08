"""Structured reply plans for voice clarification and companion follow-ups.

Rules should not be the speaking persona. They create a ReplyPlan that tells a
reply composer what must be asked, confirmed, or clarified. A model can then
phrase the final spoken text naturally while staying inside the rule boundary.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


ReplyIntent = Literal[
    "none",
    "ask_missing_slot",
    "confirm_external_action",
    "clarify_ambiguous_entity",
    "clarify_ambiguous_intent",
    "companion_emotional_followup",
    "companion_preference_followup",
    "proactive_status_hint",
    "deny_or_degrade_safely",
]

ReplyRisk = Literal["none", "low", "medium", "high"]


@dataclass(frozen=True)
class VoiceReplyPlan:
    reply_intent: ReplyIntent
    reason: str
    target_model_task: str = "generate_followup_question"
    goal: str = ""
    risk: ReplyRisk = "low"
    known_context: dict[str, Any] = field(default_factory=dict)
    missing_slots: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    raw_text: str = ""
    corrected_text: str = ""
    fallback_template: str = ""
    reply_source: str = "reply_plan"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_SLOT_LABELS = {
    "app": "应用",
    "contact": "联系人",
    "recipient": "联系人",
    "recipients": "联系人",
    "message": "消息内容",
    "message_content": "消息内容",
    "project": "项目",
    "feature_query": "要查询的内容",
}


def _clean_list(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    out: list[str] = []
    for val in values:
        s = str(val or "").strip()
        if s and s not in out:
            out.append(s)
    return out


def _slot_label(slot: str) -> str:
    return _SLOT_LABELS.get(str(slot or "").strip(), str(slot or "").strip() or "信息")


def fallback_reply_from_plan(plan: VoiceReplyPlan) -> str:
    """Last-resort text when no model composer is available."""
    if plan.fallback_template:
        return plan.fallback_template
    if plan.reply_intent == "ask_missing_slot":
        if "message_content" in plan.missing_slots or "message" in plan.missing_slots:
            return "我还需要知道你想发送的具体内容。"
        if "contact" in plan.missing_slots or "recipient" in plan.missing_slots:
            return "我还需要知道你想发给谁。"
        labels = "、".join(_slot_label(x) for x in plan.missing_slots[:2]) or "必要信息"
        return f"我还需要你补充{labels}。"
    if plan.reply_intent == "confirm_external_action":
        return "这个操作会发到外部，我需要你确认后再执行。"
    if plan.reply_intent == "clarify_ambiguous_entity":
        if plan.candidates:
            return f"我听到的对象有点不确定，是{ '，还是'.join(plan.candidates[:3]) }？"
        return "我听到的对象有点不确定，你再说一下是哪一个？"
    if plan.reply_intent == "clarify_ambiguous_intent":
        return "这句我还没理解清楚，你想让我具体做什么？"
    if plan.reply_intent == "deny_or_degrade_safely":
        return "这个我现在不能直接执行，需要你换个更明确的说法。"
    return ""


def _constraints_for_missing_slot(intent: str, missing_slots: list[str]) -> list[str]:
    constraints = [
        "只追问缺失的信息，不要执行任务。",
        "不要编造用户没有提供的对象、内容或参数。",
        "语气温和自然，适合实时 TTS 播报。",
        "最多一句话，不要输出 Markdown。",
    ]
    if intent == "send_message" or "message_content" in missing_slots:
        constraints.append("如果缺少消息正文，只询问要发送什么内容。")
    if "contact" in missing_slots:
        constraints.append("如果缺少联系人，只询问发给谁。")
    return constraints


def reply_plan_from_voice_selection(
    *,
    selected: dict[str, Any],
    raw_text: str,
    corrected_text: str = "",
) -> VoiceReplyPlan | None:
    selected_type = str(selected.get("type") or "").strip()
    intent = str(selected.get("intent") or "").strip()
    slots = selected.get("slots") if isinstance(selected.get("slots"), dict) else {}
    missing_slots = _clean_list(selected.get("missing_slots"))
    uncertain_slots = selected.get("uncertain_slots")
    candidates: list[str] = []
    if isinstance(uncertain_slots, list):
        for item in uncertain_slots:
            if isinstance(item, dict):
                val = str(item.get("value") or "").strip()
                if val and val not in candidates:
                    candidates.append(val)

    known_context = {
        "intent": intent,
        "slots": slots,
        "selected_type": selected_type,
    }

    if selected_type == "clarification_required":
        if candidates and not missing_slots:
            plan = VoiceReplyPlan(
                reply_intent="clarify_ambiguous_entity",
                reason=str(selected.get("clarification_reason") or "ambiguous_entity"),
                goal="确认用户实际指的是哪个候选实体。",
                risk="medium",
                known_context=known_context,
                candidates=candidates,
                constraints=[
                    "只让用户在候选实体之间确认，不要新增候选。",
                    "不要执行任务。",
                    "一句话以内，适合 TTS。",
                ],
                raw_text=raw_text,
                corrected_text=corrected_text or str(selected.get("corrected_text") or raw_text),
            )
        else:
            slot_labels = "、".join(_slot_label(x) for x in missing_slots[:2])
            plan = VoiceReplyPlan(
                reply_intent="ask_missing_slot",
                reason=str(selected.get("clarification_reason") or "missing_required_slot"),
                goal=f"追问用户补充{slot_labels or '缺失信息'}。",
                risk="medium",
                known_context=known_context,
                missing_slots=missing_slots,
                candidates=candidates,
                constraints=_constraints_for_missing_slot(intent, missing_slots),
                raw_text=raw_text,
                corrected_text=corrected_text or str(selected.get("corrected_text") or raw_text),
            )
        return _with_fallback(plan)

    if selected_type == "task_requires_confirmation":
        plan = VoiceReplyPlan(
            reply_intent="confirm_external_action",
            reason="task_requires_confirmation",
            target_model_task="generate_confirmation_question",
            goal="确认最终执行对象和动作，得到用户明确同意。",
            risk="high" if intent in {"send_message", "contact_interaction"} else "medium",
            known_context=known_context,
            constraints=[
                "必须明确即将执行的对象和内容。",
                "不能替用户确认，不能直接执行。",
                "不要新增规则层没有给出的对象或内容。",
                "一句话以内，适合 TTS。",
            ],
            raw_text=raw_text,
            corrected_text=corrected_text or str(selected.get("corrected_text") or raw_text),
        )
        return _with_fallback(plan)

    return None


def _with_fallback(plan: VoiceReplyPlan) -> VoiceReplyPlan:
    fallback = fallback_reply_from_plan(plan)
    return VoiceReplyPlan(
        **{
            **plan.to_dict(),
            "fallback_template": fallback,
            "reply_source": "fallback_template_available",
        }
    )


def build_reply_composer_prompt(plan: VoiceReplyPlan | dict[str, Any], *, user_text: str = "") -> str:
    """Build the prompt sent to L3 when it should compose the spoken follow-up."""
    data = plan.to_dict() if isinstance(plan, VoiceReplyPlan) else dict(plan or {})
    return (
        "【语音追问生成任务】\n"
        "你不是在执行用户原始任务，而是在根据规则层给出的 ReplyPlan 生成一句自然追问/确认话术。\n"
        "规则层只负责边界，最终对用户说的话由你来写。\n"
        "禁止调用工具，禁止声称已经执行，禁止补全用户没说的信息。\n"
        "请只输出最终要对用户说的一句话，不要 Markdown。\n\n"
        f"用户原始语音文本：{user_text[:500]}\n"
        "ReplyPlan JSON：\n"
        f"{json.dumps(data, ensure_ascii=False, indent=2)[:2500]}"
    )


def build_reply_composer_prompt_from_payload(payload: dict[str, Any], *, user_text: str = "") -> str:
    return build_reply_composer_prompt(payload, user_text=user_text)
