"""output_format_signals：直连绕过与工具意图启发式。"""

from l3_node.routing.output_format_signals import heuristic_tool_need, should_use_direct_llm_bypass


def test_heuristic_tool_need_weather_disables_direct_bypass() -> None:
    assert heuristic_tool_need("杭州今天天气怎么样") is True
    use_direct, _ = should_use_direct_llm_bypass("杭州今天天气怎么样")
    assert use_direct is False


def test_plain_chitchat_not_flagged_as_tool_need() -> None:
    assert heuristic_tool_need("你好") is False
