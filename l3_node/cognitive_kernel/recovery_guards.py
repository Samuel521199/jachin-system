"""Recovery guards for final-answer failure modes.

These guards are policy decisions owned by the Cognitive Kernel / RecoveryAgent
layer. They return structured WorkOrder suggestions instead of text tool
protocol instructions.
"""

from __future__ import annotations

import json

from .capability_hook_bridge import build_work_order_suggestion


def is_hallucinated_final_mcp_error_json(text: str) -> bool:
    """Detect a model-final JSON that pretends to be an MCP validation error."""

    value = _parse_final_json(text, max_len=8000)
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

    value = _parse_final_json(text, max_len=4000)
    if not isinstance(value, dict) or "ok" in value:
        return False
    if str(value.get("status") or "").lower() != "error":
        return False
    message = str(value.get("message") or "")
    suggestion = str(value.get("suggestion") or "")
    return bool(
        re_contains_weather_denial(message)
        or ("wttr" in suggestion.lower() and "curl" in suggestion.lower())
    )


def build_fake_mcp_error_recovery_prompt() -> str:
    """Recovery suggestion used when the model emitted a fake MCP/API JSON error."""

    return build_work_order_suggestion(
        tool="core:fs_write",
        work_order_input={
            "path": "$requested_workspace_path",
            "content": "$verified_file_content",
        },
        reason="fake_mcp_error_json_recovery",
        role_agent="FileExecutorAgent",
        visible_message="检测到伪造的 MCP/API 错误 JSON；已生成文件写入 WorkOrder 恢复建议。",
    )


def build_fake_weather_error_recovery_prompt() -> str:
    """Recovery suggestion used when weather lookup was skipped and a fake error was emitted."""

    return build_work_order_suggestion(
        tool="util:get_weather_lite",
        work_order_input={"city": "$city_or_location_from_user_input"},
        reason="fake_weather_error_json_recovery",
        role_agent="OSExecutorAgent",
        visible_message="检测到未查询天气却返回错误 JSON；已生成天气查询 WorkOrder 恢复建议。",
    )


def _parse_final_json(text: str, *, max_len: int) -> object | None:
    raw = (text or "").strip()
    if len(raw) > max_len or not (raw.startswith("{") and raw.endswith("}")):
        return None
    if "32602" not in raw and "MCP error" not in raw and "weather" not in raw.lower() and "天气" not in raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def re_contains_weather_denial(message: str) -> bool:
    text = message or ""
    return ("天气" in text or "weather" in text.lower()) and any(
        marker in text for marker in ("不可用", "暂时", "无法获取", "无法查询", "unavailable", "failed")
    )
