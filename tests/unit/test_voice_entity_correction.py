from __future__ import annotations

from l3_node.intent_orchestrator import HIDCA_WORKSPACE_LARK, analyze_intent
from l3_node.mission_intent_schema import MissionTaskType
from l3_node.semantic_slot_parser import parse_mission_intent
from l3_node.voice_entity_correction import correct_voice_entities, export_hotwords


def _tools() -> list[dict]:
    return [
        {"id": "mcp:windows_open_app"},
        {"id": "mcp:windows_lark_send_message"},
    ]


def test_voice_correction_canonicalizes_app_and_contact_slots() -> None:
    utterance = "\u5e2e\u6211\u6253\u5f00 luck \u7ed9 viian \u53d1\u4e00\u6761\u6d88\u606f\u5185\u5bb9\u662f\u6211\u4eca\u5929\u8981\u7761\u89c9"

    correction = correct_voice_entities(utterance)
    intent = parse_mission_intent(utterance)
    decision = analyze_intent(utterance, tools=_tools())

    assert correction.corrected_text == "\u5e2e\u6211\u6253\u5f00 Lark \u7ed9 Vivian \u53d1\u4e00\u6761\u6d88\u606f\u5185\u5bb9\u662f\u6211\u4eca\u5929\u8981\u7761\u89c9"
    assert [(c.kind, c.original, c.canonical) for c in correction.corrections] == [
        ("app", "luck", "Lark"),
        ("contact", "viian", "Vivian"),
    ]
    assert intent.task_type == MissionTaskType.LARK_MESSAGE_SEND
    assert intent.slots.recipients == ["Vivian"]
    assert intent.slots.message == "\u6211\u4eca\u5929\u8981\u7761\u89c9"
    assert intent.missing_slots == []
    assert decision.route.tool_id == "mcp:windows_lark_send_message"
    assert decision.hidca["semantic_router_domain"] == HIDCA_WORKSPACE_LARK


def test_voice_correction_does_not_rewrite_message_body() -> None:
    utterance = "\u7ed9 Vivian \u53d1\u6d88\u606f\uff0c\u5185\u5bb9\u662f luck \u5f88\u597d\u7b11"

    correction = correct_voice_entities(utterance)
    intent = parse_mission_intent(utterance)

    assert correction.corrected_text == utterance
    assert correction.corrections == []
    assert intent.task_type == MissionTaskType.LARK_MESSAGE_SEND
    assert intent.slots.recipients == ["Vivian"]
    assert intent.slots.message == "luck \u5f88\u597d\u7b11"


def test_voice_correction_handles_mixed_latin_cjk_contact_noise() -> None:
    utterance = "\u6253\u5f00 lock \u7ed9 v \u8587 m \u53d1\u6d88\u606f \u5185\u5bb9\u662f hi"

    correction = correct_voice_entities(utterance)
    intent = parse_mission_intent(utterance)

    assert correction.corrected_text == "\u6253\u5f00 Lark \u7ed9 Vivian \u53d1\u6d88\u606f \u5185\u5bb9\u662f hi"
    assert intent.task_type == MissionTaskType.LARK_MESSAGE_SEND
    assert intent.slots.recipients == ["Vivian"]
    assert intent.slots.message == "hi"


def test_voice_correction_app_aliases_feed_app_control() -> None:
    chrome = parse_mission_intent("\u6253\u5f00 clone \u6d4f\u89c8\u5668")
    vscode = parse_mission_intent("\u6253\u5f00 WS Code")

    assert chrome.task_type == MissionTaskType.APP_CONTROL
    assert chrome.slots.app_name == "chrome"
    assert vscode.task_type == MissionTaskType.APP_CONTROL
    assert vscode.slots.app_name == "vscode"


def test_voice_correction_exports_hotwords() -> None:
    hotwords = export_hotwords()

    assert hotwords["Lark"] >= 20
    assert hotwords["Vivian"] >= 20
    assert hotwords["Jachin"] >= 15


def test_lark_message_plan_requires_confirmation() -> None:
    from l3_node.capability_router import choose_capability_route
    from l3_node.mission_runtime import build_plan_preview

    utterance = "\u5e2e\u6211\u6253\u5f00 luck \u7ed9 viian \u53d1\u4e00\u6761\u6d88\u606f\u5185\u5bb9\u662f\u6211\u4eca\u5929\u8981\u7761\u89c9"
    intent = parse_mission_intent(utterance)
    route = choose_capability_route(intent, _tools())
    plan = build_plan_preview(intent, route)

    assert plan.requires_confirmation is True
    assert plan.auto_execute is False
    assert plan.confirmation_reason == "external_side_effect"


def test_voice_correction_handles_single_lark_syllable_and_vivi_sticky_send() -> None:
    utterance = "\u5e2e\u6211\u6253\u5f00\u62c9\u7ed9 vivi\u53d1\u9001\u4e00\u6761\u6d88\u606f\u5185\u5bb9\u662f\u4eca\u665a\u5403\u4ec0\u4e48"

    correction = correct_voice_entities(utterance)
    intent = parse_mission_intent(utterance)

    assert correction.corrected_text == "\u5e2e\u6211\u6253\u5f00Lark\u7ed9 Vivian\u53d1\u9001\u4e00\u6761\u6d88\u606f\u5185\u5bb9\u662f\u4eca\u665a\u5403\u4ec0\u4e48"
    assert [(c.kind, c.original, c.canonical) for c in correction.corrections] == [
        ("app", "\u62c9", "Lark"),
        ("contact", "vivi", "Vivian"),
    ]
    assert intent.task_type == MissionTaskType.LARK_MESSAGE_SEND
    assert intent.slots.recipients == ["Vivian"]
    assert intent.slots.message == "\u4eca\u665a\u5403\u4ec0\u4e48"
    assert intent.missing_slots == []
