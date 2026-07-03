from __future__ import annotations

import json

from l3_node.intent_orchestrator import (
    HIDCA_OS_CONTROL,
    HIDCA_WORKSPACE_LARK,
    analyze_intent,
    check_tool_consistency,
    prune_tools_for_hidca,
    sandbox_implicit_attribution,
    write_router_evidence,
)


def _tools() -> list[dict]:
    return [
        {"id": "mcp:windows_calculator_calculate", "description": "Calculate with Windows Calculator"},
        {"id": "mcp:windows_open_app", "description": "Open a local Windows app"},
        {"id": "mcp:uia_snapshot", "description": "Read local UIA tree"},
        {"id": "core:fs_read", "description": "Read local files"},
        {"id": "mcp:windows_lark_send_message", "description": "Send Lark message"},
        {"id": "mcp:windows_codex_lark_workflow_template", "description": "Codex to Lark workflow"},
        {"id": "mcp:windows_codex_ask_lark_send", "description": "Ask Codex then send reply through Lark"},
    ]


def test_io_routes_windows_calculator_with_lark_negation_to_os_control(tmp_path):
    utterance = (
        "\u7ed9\u6211\u6253\u5f00 windows \u4e0a\u539f\u751f\u7684\u8ba1\u7b97\u5668"
        "\u7ed9\u6211\u7b97 20+70 \u7b49\u4e8e\u51e0\uff0c\u6ce8\u610f\u662f\u7528 windows "
        "\u90a3\u4e2a mcp \u6765\u6253\u5f00\u7535\u8111\u539f\u672c\u7684\u8ba1\u7b97\u5668"
        "\u8ba1\u7b97\u54e6\uff0c\u4e0d\u9700\u8981\u6253\u5f00 lark"
    )

    decision = analyze_intent(utterance, tools=_tools())

    assert decision.intent.task_type.value == "calculator_calculate"
    assert decision.route.tool_id == "mcp:windows_calculator_calculate"
    assert decision.hidca["semantic_router_domain"] == HIDCA_OS_CONTROL
    assert decision.intent_frame.constraints["require_domains"] == ["os_assistant"]
    assert any(item["entity"] == "lark" for item in decision.intent_frame.forbidden)
    assert any("user_negation(lark)" in c.counter for c in decision.candidates if c.target == "lark")

    pruned, meta = prune_tools_for_hidca(_tools(), decision)
    ids = {tool["id"] for tool in pruned}
    assert "mcp:windows_calculator_calculate" in ids
    assert "mcp:windows_lark_send_message" not in ids
    assert "mcp:windows_codex_lark_workflow_template" not in ids
    assert meta["tools_after_prune"] <= 4

    evidence_path = write_router_evidence(decision, output_dir=tmp_path)
    evidence = json.loads(tmp_path.joinpath(evidence_path.split("\\")[-1]).read_text(encoding="utf-8"))
    assert evidence["chosen"]["tool_id"] == "mcp:windows_calculator_calculate"
    assert evidence["hidca"]["semantic_router_domain"] == HIDCA_OS_CONTROL


def test_io_sandbox_strips_lark_context_for_os_control():
    utterance = (
        "\u7528 windows mcp \u6253\u5f00\u8ba1\u7b97\u5668\u7b97 20+70\uff0c"
        "\u522b\u6253\u5f00\u98de\u4e66"
    )
    implicit = {
        "channel": "websocket_lark",
        "lark_chat_id": "oc_123",
        "originating_lark_chat_id": "oc_456",
        "safe_key": "kept",
    }

    decision = analyze_intent(utterance, tools=_tools(), implicit_attribution=implicit)
    sanitized, stripped = sandbox_implicit_attribution(implicit, decision)

    assert decision.hidca["semantic_router_domain"] == HIDCA_OS_CONTROL
    assert sanitized == {"channel": "websocket_lark", "safe_key": "kept"}
    assert set(stripped) == {"lark_chat_id", "originating_lark_chat_id"}


def test_io_keeps_lark_tools_for_explicit_lark_send():
    utterance = "\u7ed9 Vivian \u53d1 \u4f60\u597d"

    decision = analyze_intent(utterance, tools=_tools())
    pruned, _meta = prune_tools_for_hidca(_tools(), decision)
    ids = {tool["id"] for tool in pruned}

    assert decision.intent.task_type.value == "lark_message_send"
    assert decision.hidca["semantic_router_domain"] == HIDCA_WORKSPACE_LARK
    assert "mcp:windows_lark_send_message" in ids
    assert "mcp:windows_calculator_calculate" not in ids

def test_consistency_check_blocks_generic_open_app_lark_when_forbidden():
    utterance = (
        "\u7528 windows mcp \u6253\u5f00\u8ba1\u7b97\u5668\u7b97 20+70\uff0c"
        "\u4e0d\u9700\u8981\u6253\u5f00 lark"
    )

    decision = analyze_intent(utterance, tools=_tools())
    violation = check_tool_consistency(
        "mcp:windows_open_app",
        '{"app_name":"lark"}',
        decision,
    )

    assert violation is not None
    assert violation["error"] == "routing_violation"

def test_io_async_uses_llm_candidate_for_spoken_chinese_arithmetic():
    import asyncio

    from l3_node.intent_orchestrator import analyze_intent_async

    class FakeIntentEngine:
        async def generate_response(self, messages, tools=None, **kwargs):
            return json.dumps(
                {
                    "task_type": "calculator_calculate",
                    "confidence": 0.92,
                    "slots": {"app_name": "calculator", "expression": "40*50+100"},
                    "missing_slots": [],
                    "risk_level": "low",
                    "reasoning": ["method is calculator, final goal is result"],
                    "goal": "calculate spoken arithmetic",
                    "success_condition": "return_numeric_result",
                },
                ensure_ascii=False,
            )

    decision = asyncio.run(
        analyze_intent_async(
            "\u7ed9\u6211\u6253\u5f00 windows \u7684\u8ba1\u7b97\u5668\u7b97\u4e00\u4e0b"
            "\u56db\u5341\u4e58\u4ee5\u4e94\u5341\u52a0\u4e00\u767e\u7b49\u4e8e\u51e0",
            tools=_tools(),
            engine=FakeIntentEngine(),
        )
    )

    assert decision.intent.task_type.value == "calculator_calculate"
    assert decision.intent.slots.expression == "40*50+100"
    assert decision.route.tool_id == "mcp:windows_calculator_calculate"
    assert decision.hidca["semantic_intent"]["decision"] == "llm_preferred_more_specific_goal"



def test_io_async_keeps_composite_codex_to_lark_when_llm_says_open_codex() -> None:
    import asyncio

    from l3_node.intent_orchestrator import analyze_intent_async

    class FakeIntentEngine:
        async def generate_response(self, messages, tools=None, **kwargs):
            return json.dumps(
                {
                    "task_type": "app_control",
                    "confidence": 0.86,
                    "slots": {"app_name": "codex"},
                    "missing_slots": [],
                    "risk_level": "low",
                    "reasoning": ["surface verb is open Codex"],
                },
                ensure_ascii=False,
            )

    utterance = (
        "\u8bf7\u4f60\u5728 codex \u91cc\u9762\u6253\u5f00\u4e00\u4e2a\u4f1a\u8bdd\u6846\uff0c"
        "\u95ee\u4ed6\u8fd9\u5468\u7684AI\u5927\u4e8b\u6709\u4ec0\u4e48\uff0c"
        "\u7136\u540e\u628a\u4ed6\u56de\u590d\u7684\u5185\u5bb9\u901a\u8fc7lark\u53d1\u9001\u7ed9vivian"
    )

    decision = asyncio.run(analyze_intent_async(utterance, tools=_tools(), engine=FakeIntentEngine()))
    pruned, _meta = prune_tools_for_hidca(_tools(), decision)
    ids = {tool["id"] for tool in pruned}

    assert decision.intent.task_type.value == "codex_ask_lark_send"
    assert decision.route.tool_id == "mcp:windows_codex_ask_lark_send"
    assert decision.hidca["semantic_router_domain"] == HIDCA_WORKSPACE_LARK
    assert decision.hidca["semantic_intent"]["decision"] == "rule_preferred_composite_mission"
    assert "mcp:windows_codex_ask_lark_send" in ids
    assert "mcp:windows_open_app" not in ids
