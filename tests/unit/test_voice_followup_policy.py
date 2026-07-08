from __future__ import annotations

from l3_node.voice_followup_policy import (
    build_voice_followup_prompt_block,
    decide_voice_followup_policy,
)


def test_followup_policy_uses_rule_boundary_for_pending_clarification() -> None:
    decision = decide_voice_followup_policy(
        "\u597d\u7684",
        {
            "clarification_pending": True,
            "voice_stt_user_message": "\u8981\u53d1\u7ed9\u8c01\uff1f",
        },
    )

    assert decision.should_ask is True
    assert decision.followup_type == "task_clarification"
    assert decision.mode == "rule_boundary"
    assert decision.suggested_question == "\u8981\u53d1\u7ed9\u8c01\uff1f"


def test_followup_policy_requires_confirmation_for_external_action() -> None:
    decision = decide_voice_followup_policy(
        "\u7ed9 Vivian \u53d1\u6d88\u606f\uff0c\u5185\u5bb9\u662f hello",
        {"voice_intent_class": "CHITCHAT", "voice_dispatch_lane": "direct_llm"},
    )

    assert decision.should_ask is True
    assert decision.followup_type == "safety_confirmation"
    assert decision.risk == "high"
    assert "Never silently perform external side effects." in decision.constraints


def test_followup_policy_does_not_add_companion_question_to_plain_task() -> None:
    decision = decide_voice_followup_policy(
        "\u6253\u5f00 Chrome",
        {"voice_intent_class": "TASK_SYNC", "voice_dispatch_lane": "foreground"},
    )

    assert decision.should_ask is False
    assert decision.followup_type == "none"
    assert "task_route_without_missing_slot" in decision.reasons


def test_followup_policy_allows_one_gentle_emotional_question() -> None:
    decision = decide_voice_followup_policy(
        "\u6211\u4eca\u5929\u597d\u7d2f\uff0c\u6709\u70b9\u6491\u4e0d\u4f4f",
        {"voice_intent_class": "CHITCHAT", "voice_dispatch_lane": "direct_llm"},
    )

    assert decision.should_ask is True
    assert decision.followup_type == "companion_emotional"
    assert decision.mode == "model_expression"
    assert decision.max_rounds == 1


def test_followup_policy_stops_after_round_budget() -> None:
    decision = decide_voice_followup_policy(
        "\u6211\u4eca\u5929\u597d\u7d2f",
        {
            "voice_intent_class": "CHITCHAT",
            "voice_dispatch_lane": "direct_llm",
            "voice_followup_rounds": 2,
        },
    )

    assert decision.should_ask is False
    assert "followup_round_budget_exhausted" in decision.reasons


def test_followup_prompt_block_renders_policy_for_llm() -> None:
    decision = decide_voice_followup_policy(
        "\u4f60\u89c9\u5f97\u6211\u8be5\u600e\u4e48\u529e",
        {"voice_intent_class": "CHITCHAT", "voice_dispatch_lane": "direct_llm"},
    )

    block = build_voice_followup_prompt_block(decision)

    assert "Voice Follow-up Policy" in block
    assert "type=companion_preference" in block
    assert "Rules set the boundary" in block
