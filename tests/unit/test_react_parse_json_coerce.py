"""ReAct：裸 JSON 工具意图须归一为 native，禁止当 answer 泄漏 thought。"""

from l3_node.agent_core import _parse_action, _try_coerce_json_tool_intent_to_native


def test_try_coerce_json_tool_intent_basic() -> None:
    s = (
        '{"thought": "推理", "action": "core:shell_exec", '
        '"action_input": {"command": "echo ok"}}'
    )
    r = _try_coerce_json_tool_intent_to_native(s)
    assert r is not None
    assert r["type"] == "native"
    assert r["tool"] == "core:shell_exec"
    assert '"command"' in r["input"]


def test_parse_action_prefers_json_tool_over_naked_answer() -> None:
    s = (
        '{"thought": "x", "action": "util:get_weather_lite", "action_input": {"location": "杭州"}}'
    )
    r = _parse_action(s, skills=[], pure_json_contract=False)
    assert r is not None
    assert r.get("type") == "native"
    assert r.get("tool") == "util:get_weather_lite"


def test_parse_action_plain_json_answer_without_tool_keys() -> None:
    s = '{"summary": "hello", "items": [1, 2]}'
    r = _parse_action(s, skills=[], pure_json_contract=False)
    # 无 action/action_input，不归一；可能走裸文本 answer 分支
    assert r is None or r.get("type") == "answer"


def test_parse_action_json_tool_under_pure_json_contract() -> None:
    """pure_json_contract（终端 JSON 契约）下也必须识别裸 JSON 工具意图，否则会 parsed=None 且从不执行工具。"""
    s = (
        '{"thought": "x", "action": "util:generate_office_doc", '
        '"action_input": {"file_format": "docx", "file_path": "C:/w/x.docx", '
        '"content_json": {"blocks": [{"type": "p", "data": "hi"}]}}}'
    )
    r = _parse_action(s, skills=[], pure_json_contract=True)
    assert r is not None
    assert r.get("type") == "native"
    assert r.get("tool") == "util:generate_office_doc"
    assert "docx" in str(r.get("input"))


def test_try_coerce_ignores_action_without_colon() -> None:
    """业务 JSON 的 action 字段勿误当工具 id。"""
    s = '{"status": "ok", "action": "completed", "action_input": {}}'
    assert _try_coerce_json_tool_intent_to_native(s) is None
