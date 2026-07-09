"""Recovery guards for legacy final-answer failure modes.

These guards are policy decisions owned by the Cognitive Kernel / RecoveryAgent
layer. The legacy text transport may still call them to decide whether to inject
a corrective turn, but the predicates must not live in ``agent_core.py``.
"""

from __future__ import annotations

import json


def is_hallucinated_final_mcp_error_json(text: str) -> bool:
    """Detect a model-final JSON that pretends to be an MCP validation error."""

    s = (text or "").strip()
    if len(s) > 8000 or not (s.startswith("{") and s.endswith("}")):
        return False
    if "32602" not in s and "MCP error" not in s:
        return False
    try:
        value = json.loads(s)
    except json.JSONDecodeError:
        return False
    if not isinstance(value, dict):
        return False
    status = str(value.get("status") or "").lower()
    error = str(value.get("error") or "")
    return status == "failed" and (
        "32602" in error
        or "write_file" in error.lower()
        or "invalid arguments" in error.lower()
        or "validation" in error.lower()
    )


def is_hallucinated_weather_service_error_json(text: str) -> bool:
    """Detect a fabricated weather-service JSON error emitted without tool use."""

    s = (text or "").strip()
    if len(s) > 4000 or not (s.startswith("{") and s.endswith("}")):
        return False
    try:
        value = json.loads(s)
    except json.JSONDecodeError:
        return False
    if not isinstance(value, dict) or "ok" in value:
        return False
    if str(value.get("status") or "").lower() != "error":
        return False
    message = str(value.get("message") or "")
    suggestion = str(value.get("suggestion") or "")
    if ("天气" in message or "weather" in message.lower()) and (
        "不可用" in message or "暂时" in message or "无法获取" in message or "无法查询" in message
    ):
        return True
    return "wttr" in suggestion.lower() and "curl" in suggestion.lower()


def build_fake_mcp_error_recovery_prompt() -> str:
    """Prompt used when the model emitted a fake MCP/API JSON error."""

    return (
        "【系统纠偏】你刚才的 Final Answer 是模仿 MCP/API 错误格式的 JSON，但本轮并未执行任何工具"
        "（不应出现 -32602 类真实返回）。\n"
        "请改用 ReAct 文本续写，**禁止**再用 Final Answer 提交伪错误 JSON：\n"
        "1) Thought: …\n"
        "2) Action: core:fs_write\n"
        "3) Action Input: "
        '{"path":"<相对工作区路径，如 scripts/xxx.py>","content":"<完整文件内容>"}\n'
        "path 须为非空相对路径；也可用 mcp:write_file + 同上 JSON。"
        "写盘成功后再 Final Answer 给出绝对路径。"
    )


def build_fake_weather_error_recovery_prompt() -> str:
    """Prompt used when weather lookup was skipped and a fake error was emitted."""

    return (
        "【系统纠偏】你刚才输出的是仿 API 的天气错误 JSON，但本轮并未执行 **util:get_weather_lite**，"
        "Observation 中也没有工具返回。\n"
        "请立即用 ReAct 续写（禁止再输出 Final Answer 或裸 JSON）：\n"
        "Thought: …\n"
        "Action: util:get_weather_lite\n"
        "Action Input: {\"city\":\"<从用户原话提取的城市或地区，如 杭州>\"}\n"
        "若用户未指定城市，可传 {\"location\":\"<合理默认或用户所在>\"} 或先一句追问。"
    )
