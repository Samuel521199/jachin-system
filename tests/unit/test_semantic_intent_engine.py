from __future__ import annotations

import json


class FakeIntentEngine:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls: list[dict] = []

    async def generate_response(self, messages, tools=None, **kwargs):
        self.calls.append({"messages": messages, "tools": tools, "kwargs": kwargs})
        return json.dumps(self.payload, ensure_ascii=False)


def test_semantic_intent_llm_candidate_overrides_tool_only_app_control():
    import asyncio

    from l3_node.semantic_intent_engine import parse_semantic_intent_async

    engine = FakeIntentEngine(
        {
            "task_type": "calculator_calculate",
            "confidence": 0.92,
            "slots": {"app_name": "calculator", "expression": "40*50+100"},
            "missing_slots": [],
            "risk_level": "low",
            "reasoning": ["final goal is to return a numeric result"],
            "goal": "calculate the requested arithmetic expression",
            "success_condition": "return_numeric_result",
        }
    )

    result = asyncio.run(
        parse_semantic_intent_async(
            "\u7ed9\u6211\u6253\u5f00 windows \u7684\u8ba1\u7b97\u5668\u7b97\u4e00\u4e0b"
            "\u56db\u5341\u4e58\u4ee5\u4e94\u5341\u52a0\u4e00\u767e\u7b49\u4e8e\u51e0",
            engine=engine,
        )
    )

    assert result.meta["rule"]["task_type"] == "app_control"
    assert result.meta["llm"]["status"] == "parsed"
    assert result.meta["decision"] == "llm_preferred_more_specific_goal"
    assert result.intent.task_type.value == "calculator_calculate"
    assert result.intent.slots.expression == "40*50+100"
    assert engine.calls

def test_semantic_intent_prefers_llm_slots_when_rule_recipient_is_dirty():
    from l3_node.mission_intent_schema import MissionIntent, MissionSlots, MissionTaskType
    from l3_node.semantic_intent_engine import _choose_between_rule_and_llm

    rule_intent = MissionIntent(
        task_type=MissionTaskType.LARK_MESSAGE_SEND,
        confidence=0.78,
        slots=MissionSlots(recipients=["\u6211\u6253\u5f00lark\u7ed9vivian"], message="\u4e00\u6761\u6d88\u606f"),
        missing_slots=[],
        reasoning=["send+recipient signals"],
        raw_text="\u7ed9\u6211\u6253\u5f00lark\u7ed9vivian\u53d1\u9001\u4e00\u6761\u6d88\u606f",
    )
    llm_intent = MissionIntent(
        task_type=MissionTaskType.LARK_MESSAGE_SEND,
        confidence=0.95,
        slots=MissionSlots(recipients=["vivian"], message=""),
        missing_slots=["message"],
        reasoning=["llm semantic parse"],
        raw_text=rule_intent.raw_text,
    )

    chosen, decision, disagreement = _choose_between_rule_and_llm(rule_intent, llm_intent)

    assert decision == "llm_preferred_same_task_cleaner_slots"
    assert disagreement == {}
    assert chosen.slots.recipients == ["vivian"]
    assert chosen.slots.message == ""
    assert chosen.missing_slots == ["message"]


def test_lark_route_allows_open_lark_prefix_after_semantic_parse():
    from l3_node.capability_router import choose_capability_route
    from l3_node.mission_intent_schema import MissionIntent, MissionSlots, MissionTaskType

    intent = MissionIntent(
        task_type=MissionTaskType.LARK_MESSAGE_SEND,
        confidence=0.95,
        slots=MissionSlots(recipients=["vivian"], message="\u4f60\u597d"),
        raw_text="\u7ed9\u6211\u6253\u5f00lark\u7ed9vivian\u53d1\u9001\u4f60\u597d",
    )

    route = choose_capability_route(intent, [{"id": "mcp:windows_lark_send_message"}])

    assert route.ok is True
    assert route.tool_id == "mcp:windows_lark_send_message"
    assert route.reason == "Lark message should use Windows UI verified send workflow"
