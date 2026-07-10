"""Generic Skill/MCP adapter for the Memory-first mainline.

This adapter is the bridge between dynamic capability metadata and the
WorkOrder Dispatcher.  It deliberately does not know business domains such as
PMO, English learning, HR, or BI.  It selects an installed capability from
metadata, shapes a schema-aware tool input, and lets RoleExecutor plus
Verification/Recovery own the actual execution lifecycle.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
from typing import Any, Callable, Optional

from l3_node.capability_matcher import CapabilityMatchResult, match_task_to_capability

from .capability_hook_bridge import extract_work_order_suggestion
from .closure_memory import execute_turn_closure_memory_writes
from .dispatcher import dispatch_tool_work_order
from .runtime import close_turn

logger = logging.getLogger(__name__)

RunToolFunc = Callable[[str, str, Optional[list[str]]], Any]

_MIN_SEMANTIC_CONFIDENCE = 0.58
_SCHEMA_TEXT_FIELDS = (
    "input",
    "query",
    "text",
    "prompt",
    "user_input",
    "instruction",
    "question",
    "content",
    "message",
    "expression",
    "word",
    "term",
    "topic",
)
_RECIPIENT_FIELDS = ("recipient", "recipients", "recipients_json", "to", "target", "chat", "chat_id", "chat_ids")
_PATH_FIELDS = ("path", "file_path", "directory_path", "project_path", "target_path")
_APP_FIELDS = ("app", "app_name", "application", "window", "keywords")
_WINDOWS_APP_CONTROL_TOOLS = {
    "mcp:windows_open_app",
    "mcp:windows_window_switch",
    "mcp:windows_window_close",
}


async def try_execute_capability_work_order(
    *,
    user_input: str,
    tools: list[dict[str, Any]],
    allowed_skills: Optional[list[str]],
    run_tool_func: RunToolFunc,
    run_id: str,
    intent_decision: Any | None = None,
) -> str | None:
    """Try handling this turn with a dynamic Skill/MCP WorkOrder.

    Returns ``None`` when the capability match is too weak or when no installed
    tool is available.  Returning a string means the turn has fully executed
    through DecisionContract -> WorkOrder -> RoleExecutor -> Verification.
    """

    text = str(user_input or "").strip()
    if not text or not tools:
        return None
    if _looks_like_chitchat(text):
        return None

    suggestion = extract_work_order_suggestion(text)
    suggested_payload: dict[str, Any] | None = None
    if suggestion is not None:
        selected = _select_suggested_capability(
            suggestion=suggestion,
            user_input=text,
            tools=tools,
        )
        suggested_payload = _payload_from_suggestion(suggestion)
    else:
        selected = _select_capability(
            user_input=text,
            tools=tools,
            allowed=allowed_skills,
            intent_decision=intent_decision,
        )
    if selected is None:
        return None
    tool_id, tool_meta, match = selected
    if not tool_id:
        return None

    action_payload = suggested_payload or _build_action_payload(
        user_input=text,
        tool_meta=tool_meta,
        match=match,
        intent_decision=intent_decision,
    )
    work_order_input = json.dumps(action_payload, ensure_ascii=False, default=str)

    _log_capability_adapter(
        stage="selected",
        tool_id=tool_id,
        user_input=text,
        match=match,
        action_payload=action_payload,
        intent_decision=intent_decision,
    )

    async def _executor(_work_order):
        current_tool = str(_work_order.inputs.get("tool") or tool_id).strip()
        raw_action = str(_work_order.inputs.get("work_order_input") or work_order_input)
        if current_tool in _WINDOWS_APP_CONTROL_TOOLS:
            return await _call_local_windows_app_control(current_tool, raw_action)
        return await _call_tool_runner(run_tool_func, current_tool, raw_action, allowed_skills)

    result = await dispatch_tool_work_order(
        turn_id=run_id,
        goal=text,
        tool=tool_id,
        work_order_input=work_order_input,
        executor=_executor,
    )
    final_text = _final_reply(tool_id=tool_id, ok=bool(result.verification.ok), observation=result.observation)
    closure = close_turn(
        turn_id=result.contract.turn_id,
        final_text=final_text,
        executed_work_orders=[result.work_order.work_order_id],
        verification_reports=[result.verification],
        aborted=not bool(result.verification.ok),
    )
    _log_capability_adapter(
        stage="executed",
        tool_id=tool_id,
        user_input=text,
        match=match,
        dispatch_result=result,
        closure=closure,
        final_text=final_text,
    )
    await execute_turn_closure_memory_writes(closure)
    logger.info(
        "[CognitiveKernel] capability WorkOrder executed turn=%s tool=%s ok=%s role=%s",
        run_id[:12],
        tool_id,
        result.verification.ok,
        result.work_order.role_agent,
    )
    return final_text


def _select_suggested_capability(
    *,
    suggestion: dict[str, Any],
    user_input: str,
    tools: list[dict[str, Any]],
) -> tuple[str, dict[str, Any], CapabilityMatchResult | None] | None:
    tool_by_id = _tool_index(tools)
    tid = _normalize_tool_id(str(suggestion.get("tool") or ""))
    meta = tool_by_id.get(tid.lower()) or tool_by_id.get(tid.removeprefix("mcp:").lower())
    if meta is None:
        _log_capability_adapter(
            stage="skipped",
            tool_id=tid,
            user_input=user_input,
            reason="suggested_tool_unavailable",
            action_payload={"suggestion": suggestion},
        )
        return None
    return tid, meta, None


def _select_capability(
    *,
    user_input: str,
    tools: list[dict[str, Any]],
    allowed: Optional[list[str]],
    intent_decision: Any | None,
) -> tuple[str, dict[str, Any], CapabilityMatchResult | None] | None:
    tool_by_id = _tool_index(tools)
    chosen_id = _chosen_tool_id(intent_decision)
    if chosen_id and _chosen_is_executable(intent_decision) and chosen_id.lower() in tool_by_id:
        return chosen_id, tool_by_id[chosen_id.lower()], None

    try:
        match = match_task_to_capability(user_input, tools, allowed, limit=6)
    except Exception as exc:
        logger.warning("[CognitiveKernel] capability semantic match failed: %s", exc)
        return None
    if match.selected is None:
        _log_capability_adapter(stage="skipped", tool_id="", user_input=user_input, match=match, reason="no_match")
        return None
    if float(match.confidence or 0.0) < _MIN_SEMANTIC_CONFIDENCE:
        _log_capability_adapter(
            stage="skipped",
            tool_id=match.selected.id,
            user_input=user_input,
            match=match,
            reason=f"low_confidence:{match.confidence}",
        )
        return None
    tid = match.selected.id
    meta = tool_by_id.get(tid.lower()) or tool_by_id.get(tid.removeprefix("mcp:").lower())
    if meta is None:
        _log_capability_adapter(stage="skipped", tool_id=tid, user_input=user_input, match=match, reason="tool_unavailable")
        return None
    return tid, meta, match


def _tool_index(tools: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in tools or []:
        if not isinstance(item, dict):
            continue
        tid = _tool_id(item)
        if not tid:
            continue
        out[tid.lower()] = item
        out[tid.removeprefix("mcp:").lower()] = item
    return out


def _tool_id(tool: dict[str, Any]) -> str:
    fn = tool.get("function") if isinstance(tool.get("function"), dict) else {}
    raw = str(
        tool.get("id")
        or tool.get("tool_id")
        or tool.get("name")
        or fn.get("name")
        or ""
    ).strip()
    if not raw:
        return ""
    if raw.startswith(("mcp:", "core:", "util:", "jpp:", "delegate", "coordinate")):
        return raw
    return f"mcp:{raw}"


def _normalize_tool_id(raw: str) -> str:
    tid = str(raw or "").strip()
    if not tid:
        return ""
    if tid.startswith(("mcp:", "core:", "util:", "jpp:", "delegate", "coordinate")):
        return tid
    return f"mcp:{tid}"


def _json_obj(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(str(text or "{}"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _chosen_tool_id(intent_decision: Any | None) -> str:
    chosen = getattr(intent_decision, "chosen", None)
    if not isinstance(chosen, dict):
        return ""
    tid = str(chosen.get("tool_id") or "").strip()
    if not tid:
        return ""
    if tid.startswith(("mcp:", "core:", "util:", "jpp:", "delegate", "coordinate")):
        return tid
    return f"mcp:{tid}"


def _chosen_is_executable(intent_decision: Any | None) -> bool:
    chosen = getattr(intent_decision, "chosen", None)
    if not isinstance(chosen, dict):
        return False
    return (
        str(chosen.get("route_policy") or "").strip() == "execute"
        and str(chosen.get("consistency") or "").strip().upper() == "PASS"
    )


def _payload_from_suggestion(suggestion: dict[str, Any]) -> dict[str, Any]:
    raw = suggestion.get("work_order_input")
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        return {"input": text}
    return {}


def _build_action_payload(
    *,
    user_input: str,
    tool_meta: dict[str, Any],
    match: CapabilityMatchResult | None,
    intent_decision: Any | None,
) -> dict[str, Any]:
    slots = _intent_inputs(intent_decision)
    schema_props = _schema_properties(tool_meta)
    if schema_props:
        payload: dict[str, Any] = {}
        for prop in schema_props:
            lname = prop.lower()
            if lname in slots and slots[lname] not in (None, ""):
                payload[prop] = slots[lname]
            elif prop in slots and slots[prop] not in (None, ""):
                payload[prop] = slots[prop]
            elif lname in _SCHEMA_TEXT_FIELDS:
                if lname in {"word", "term"}:
                    payload[prop] = _extract_likely_word(user_input) or user_input
                elif lname == "expression":
                    payload[prop] = _extract_expression(user_input) or user_input
                else:
                    payload[prop] = user_input
            elif lname in _RECIPIENT_FIELDS:
                payload[prop] = _recipient_slot_value(slots, prop)
            elif lname in _PATH_FIELDS:
                payload[prop] = slots.get("project_path") or slots.get("file_path") or slots.get("path") or ""
            elif lname in _APP_FIELDS:
                payload[prop] = slots.get("app_name") or slots.get("app") or slots.get("target") or ""
        if payload:
            return payload
    return {
        "input": user_input,
        "user_input": user_input,
        "query": user_input,
        "slots": slots,
        "intent_frame": _safe_to_dict(getattr(intent_decision, "intent_frame", None)),
        "capability_match": match.to_dict() if match is not None else None,
    }


def _recipient_slot_value(slots: dict[str, Any], field_name: str) -> Any:
    value = (
        slots.get("recipients")
        or slots.get("recipient")
        or slots.get("to")
        or slots.get("chat_id")
        or slots.get("chat")
        or ""
    )
    if str(field_name or "").lower().endswith("_json"):
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return ""
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return text
            except Exception:
                pass
            return json.dumps([text], ensure_ascii=False)
        if isinstance(value, list):
            return json.dumps([str(v) for v in value if str(v).strip()], ensure_ascii=False)
        if value:
            return json.dumps([str(value)], ensure_ascii=False)
        return ""
    return value


def _schema_properties(tool_meta: dict[str, Any]) -> list[str]:
    candidates: list[Any] = []
    for key in ("parameters", "input_schema", "inputSchema", "schema"):
        candidates.append(tool_meta.get(key))
    fn = tool_meta.get("function") if isinstance(tool_meta.get("function"), dict) else {}
    candidates.append(fn.get("parameters"))
    schema = tool_meta.get("schema") if isinstance(tool_meta.get("schema"), dict) else {}
    candidates.append(schema.get("input"))
    for schema_obj in candidates:
        props = _props_from_schema(schema_obj)
        if props:
            return props
    params = tool_meta.get("params")
    if isinstance(params, dict):
        return [str(k) for k in params.keys() if str(k).strip()]
    if isinstance(params, list):
        out: list[str] = []
        for item in params:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
            else:
                name = str(item or "").strip()
            if name:
                out.append(name)
        return out
    return []


def _props_from_schema(schema: Any) -> list[str]:
    if not isinstance(schema, dict):
        return []
    props = schema.get("properties")
    if isinstance(props, dict) and props:
        return [str(k) for k in props.keys() if str(k).strip()]
    required = schema.get("required")
    if isinstance(required, list):
        return [str(x) for x in required if str(x).strip()]
    return []


def _intent_inputs(intent_decision: Any | None) -> dict[str, Any]:
    frame = getattr(intent_decision, "intent_frame", None)
    raw = getattr(frame, "inputs", None)
    out: dict[str, Any] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            out[str(key)] = value
            out[str(key).lower()] = value
    target = getattr(frame, "target", "") if frame is not None else ""
    if target:
        out.setdefault("target", target)
    return out


def _extract_likely_word(text: str) -> str:
    quoted = re.findall(r"[`'\"]([A-Za-z][A-Za-z'\-]{1,40})[`'\"]", text or "")
    if quoted:
        return quoted[0]
    words = re.findall(r"\b[A-Za-z][A-Za-z'\-]{1,40}\b", text or "")
    stop = {
        "english",
        "word",
        "lookup",
        "explain",
        "meaning",
        "translate",
        "please",
        "tell",
        "about",
    }
    for word in reversed(words):
        if word.lower() not in stop:
            return word
    return words[-1] if words else ""


def _extract_expression(text: str) -> str:
    match = re.search(r"[\d\.\s\+\-\*/xX\(\)]{3,}", text or "")
    return match.group(0).strip().replace("x", "*").replace("X", "*") if match else ""


async def _call_tool_runner(
    run_tool_func: RunToolFunc,
    tool_id: str,
    work_order_input: str,
    allowed_skills: Optional[list[str]],
) -> str:
    if inspect.iscoroutinefunction(run_tool_func):
        result = await run_tool_func(tool_id, work_order_input, allowed_skills)
    else:
        result = await asyncio.to_thread(run_tool_func, tool_id, work_order_input, allowed_skills)
    if inspect.isawaitable(result):
        result = await result
    return str(result or "")


async def _call_local_windows_app_control(tool_id: str, work_order_input: str) -> str:
    payload = _json_obj(work_order_input)
    args = payload if isinstance(payload, dict) else {}
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server

    if tool_id == "mcp:windows_open_app":
        app_name = str(args.get("app_name") or args.get("name") or args.get("app") or args.get("target") or "").strip()
        return await asyncio.to_thread(
            windows_uia_server.windows_open_app,
            app_name,
            str(args.get("args_json") or "[]"),
            str(args.get("out_dir") or ""),
        )
    if tool_id == "mcp:windows_window_switch":
        return await asyncio.to_thread(
            windows_uia_server.windows_window_switch,
            str(args.get("keywords") or args.get("keyword") or args.get("window_title") or args.get("app_name") or ""),
            str(args.get("exclude_keywords") or ""),
            float(args.get("timeout") or 5.0),
            str(args.get("out_dir") or ""),
        )
    if tool_id == "mcp:windows_window_close":
        return await asyncio.to_thread(
            windows_uia_server.windows_window_close,
            str(args.get("keywords") or args.get("keyword") or args.get("window_title") or args.get("app_name") or ""),
            str(args.get("exclude_keywords") or ""),
            float(args.get("timeout") or 5.0),
            str(args.get("out_dir") or ""),
        )
    return json.dumps({"ok": False, "error": f"unsupported_windows_app_control_tool:{tool_id}"}, ensure_ascii=False)


def _final_reply(*, tool_id: str, ok: bool, observation: str) -> str:
    if ok:
        return f"已通过 {tool_id} 完成该能力调用。"
    preview = str(observation or "").strip()[:500]
    if preview:
        return f"{tool_id} 的能力调用没有通过验证。{preview}"
    return f"{tool_id} 的能力调用没有通过验证。"


def _looks_like_chitchat(text: str) -> bool:
    s = str(text or "").strip().lower()
    return s in {"hi", "hello", "你好", "在吗", "谢谢", "ok", "好的"} or len(s) <= 1


def _safe_to_dict(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "to_dict"):
        try:
            return value.to_dict()
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        try:
            return dict(value.__dict__)
        except Exception:
            pass
    return str(value)


def _log_capability_adapter(
    *,
    stage: str,
    tool_id: str,
    user_input: str,
    match: CapabilityMatchResult | None = None,
    action_payload: dict[str, Any] | None = None,
    intent_decision: Any | None = None,
    dispatch_result: Any = None,
    closure: Any = None,
    final_text: str = "",
    reason: str = "",
) -> None:
    try:
        from l3_node.terminal_turn_debug_log import append_section

        payload = {
            "stage": stage,
            "reason": reason,
            "tool_id": tool_id,
            "user_input": user_input,
            "semantic_match": match.to_dict() if match is not None else None,
            "intent_decision": _safe_to_dict(intent_decision),
            "action_payload": action_payload,
            "verification": _safe_to_dict(getattr(dispatch_result, "verification", None)) if dispatch_result else None,
            "recovery_plan": _safe_to_dict(getattr(dispatch_result, "recovery_plan", None)) if dispatch_result else None,
            "turn_closure": _safe_to_dict(closure),
            "final_text": final_text,
        }
        append_section("[Capability WorkOrder Adapter] Skill/MCP 适配层", json.dumps(payload, ensure_ascii=False, indent=2))
    except Exception:
        pass





