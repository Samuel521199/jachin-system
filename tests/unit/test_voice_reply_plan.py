from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VOICE_SERVER = ROOT / "voice_server"
if str(VOICE_SERVER) not in sys.path:
    sys.path.insert(0, str(VOICE_SERVER))

from l3_node.voice_followup_policy import decide_voice_followup_policy
from l3_node.voice_reply_plan import build_reply_composer_prompt, reply_plan_from_voice_selection
from services.voice_understanding import VoiceUnderstandingCorrector


def test_reply_plan_from_missing_message_content_selection() -> None:
    selected = {
        "type": "clarification_required",
        "intent": "send_message",
        "slots": {"app": "Lark", "contact": "Neil"},
        "missing_slots": ["message_content"],
        "corrected_text": "\u5728 Lark \u7ed9 Neil \u53d1\u6d88\u606f",
        "clarification_reason": "send_message_missing_or_weak_required_slot",
    }

    plan = reply_plan_from_voice_selection(
        selected=selected,
        raw_text="\u5728 Lark \u7ed9 Neil \u53d1\u6d88\u606f",
    )

    assert plan is not None
    assert plan.reply_intent == "ask_missing_slot"
    assert plan.missing_slots == ["message_content"]
    assert plan.known_context["slots"]["contact"] == "Neil"
    assert plan.reply_source == "fallback_template_available"
    assert "\u5177\u4f53\u5185\u5bb9" in plan.fallback_template


def test_voice_understanding_exposes_reply_plan_instead_of_only_question_text() -> None:
    result = VoiceUnderstandingCorrector().correct("\u5728 LARK \u7ed9Neil\u53d1\u6d88\u606f")

    selected = result["understanding"]["selected"]
    reply_plan = result["reply_plan"]

    assert selected["type"] == "clarification_required"
    assert reply_plan["reply_intent"] == "ask_missing_slot"
    assert reply_plan["missing_slots"] == ["message_content"]
    assert result["user_message_source"] == "fallback_template"


def test_reply_composer_prompt_contains_plan_and_no_execution_boundary() -> None:
    selected = {
        "type": "task_requires_confirmation",
        "intent": "send_message",
        "slots": {"contact": "Vivian"},
        "corrected_text": "\u7ed9 Vivian \u53d1 hello",
    }
    plan = reply_plan_from_voice_selection(selected=selected, raw_text="\u7ed9 Vivian \u53d1 hello")
    assert plan is not None

    prompt = build_reply_composer_prompt(plan, user_text="\u7ed9 Vivian \u53d1 hello")

    assert "\u8bed\u97f3\u8ffd\u95ee\u751f\u6210\u4efb\u52a1" in prompt
    assert "\u7981\u6b62\u8c03\u7528\u5de5\u5177" in prompt
    assert "confirm_external_action" in prompt


def test_followup_policy_skips_reply_composer_turns() -> None:
    decision = decide_voice_followup_policy(
        "\u3010\u8bed\u97f3\u8ffd\u95ee\u751f\u6210\u4efb\u52a1\u3011",
        {"voice_reply_composer": True, "voice_reply_plan": {"reply_intent": "ask_missing_slot"}},
    )

    assert decision.should_ask is False
    assert "reply_composer_turn" in decision.reasons
