"""Text-transport compatibility helpers.

The Memory-first mainline executes through DecisionContract -> WorkOrder ->
Dispatcher -> RoleExecutor. These helpers keep parser-era input normalization
quirks outside ``agent_core.py`` without granting direct tool execution rights.
"""

from __future__ import annotations

import json
import os
from logging import Logger
from typing import Any

from l3_node.primitives.tools.loader import tool_entry_looks_like_sqlite_family


def log_sqlite_tool_input(
    *,
    logger: Logger,
    trace: str,
    run_id: str,
    tool: str,
    action_input: str,
) -> None:
    if not tool_entry_looks_like_sqlite_family({"id": tool}):
        return
    try:
        payload = json.loads(action_input or "")
        if isinstance(payload, dict):
            for key in ("sql", "query", "statement", "command"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    sql = value.strip()
                    limit = 12000
                    logger.info(
                        "[L3 Agent][SQLite SQL] trace=%s run_id=%s tool=%s json_key=%s sql_len=%d sql=%s%s",
                        trace,
                        run_id,
                        tool,
                        key,
                        len(sql),
                        sql[:limit],
                        "...(truncated)" if len(sql) > limit else "",
                    )
                    return
            logger.info(
                "[L3 Agent][SQLite args] trace=%s run_id=%s tool=%s args=%s",
                trace,
                run_id,
                tool,
                json.dumps(payload, ensure_ascii=False)[:4000],
            )
    except json.JSONDecodeError:
        logger.info(
            "[L3 Agent][SQLite input] trace=%s run_id=%s tool=%s json_parse_fail preview=%r%s",
            trace,
            run_id,
            tool,
            (action_input or "")[:800],
            "...(truncated)" if len(action_input or "") > 800 else "",
        )


def maybe_inject_sqlite_write_ack(
    *,
    logger: Logger,
    tool: str,
    action_input: str,
    metadata: dict[str, Any],
) -> str:
    if not metadata.get("_user_granted_mcp_sqlite_write_ack"):
        return action_input
    if not tool_entry_looks_like_sqlite_family({"id": tool}):
        return action_input
    try:
        from l3_node.primitives.mcp.sqlite_write_guard import maybe_inject_user_write_ack

        payload = json.loads(action_input or "")
        if not isinstance(payload, dict):
            return action_input
        normalized = (tool or "").strip().lower()
        if normalized.startswith("mcp:"):
            normalized = normalized[4:].strip().lower()
        merged = maybe_inject_user_write_ack(tool, normalized, payload, user_granted=True)
        if merged != payload:
            logger.info("[L3 Agent] SQLite write ack injected by dialog grant tool=%s", tool)
            return json.dumps(merged, ensure_ascii=False)
    except json.JSONDecodeError:
        return action_input
    except Exception as exc:
        logger.debug("[L3 Agent] SQLite write ack injection skipped: %s", exc)
    return action_input


def normalize_openapi_tool_id(*, logger: Logger, tool: str) -> str:
    raw = (tool or "").strip()
    if not raw or ":" in raw:
        return tool
    try:
        from l3_node.primitives.mcp.registry import openapi_safe_function_name
        from l3_node.primitives.tools.core_util_tools import util_tool_ids

        for util_tool in util_tool_ids():
            if openapi_safe_function_name(util_tool) == raw:
                logger.info("[L3 Agent] OpenAPI tool name normalized %s -> %s", raw, util_tool)
                return util_tool
        for sys_tool in ("sys:health_stats", "sys:list_env_safe"):
            if openapi_safe_function_name(sys_tool) == raw:
                logger.info("[L3 Agent] OpenAPI tool name normalized %s -> %s", raw, sys_tool)
                return sys_tool
    except Exception as exc:
        logger.debug("[L3 Agent] OpenAPI tool-name normalization skipped: %s", exc)
    return tool


def prepare_lark_send_text_input(
    *,
    logger: Logger,
    tool: str,
    action_input: str,
    metadata: dict[str, Any],
) -> tuple[str, str]:
    if (tool or "").strip() != "util:lark_send_text":
        return action_input, str(metadata.get("_lark_chat_id") or "")
    lark_bind = str(metadata.get("_lark_chat_id") or "")
    try:
        try:
            from l3_node.channels.lark.client import _ensure_dotenv_loaded

            _ensure_dotenv_loaded()
        except Exception:
            pass
        env_lark = (
            os.environ.get("LARK_CHAT_ID")
            or os.environ.get("LARK_DEFAULT_CHAT_ID")
            or os.environ.get("FEISHU_CHAT_ID")
            or ""
        ).strip()
        p2p_open = (
            os.environ.get("LARK_USER_OPEN_ID")
            or os.environ.get("LARK_DM_OPEN_ID")
            or ""
        ).strip()
        payload = json.loads(action_input or "")
        if isinstance(payload, dict):
            existing = str(payload.get("chat_id") or payload.get("receive_id") or "").strip()
            if not existing and not p2p_open:
                if env_lark:
                    payload["chat_id"] = env_lark
                    action_input = json.dumps(payload, ensure_ascii=False)
                    lark_bind = env_lark
                    logger.info("[L3 Agent] util:lark_send_text uses env LARK_CHAT_ID len=%d", len(env_lark))
                elif lark_bind:
                    payload["chat_id"] = lark_bind
                    action_input = json.dumps(payload, ensure_ascii=False)
                    logger.info("[L3 Agent] util:lark_send_text injected mirrored chat_id len=%d", len(lark_bind))
            elif existing:
                lark_bind = existing
            elif p2p_open:
                logger.info("[L3 Agent] util:lark_send_text skips mirrored chat_id because P2P open_id is configured")
    except json.JSONDecodeError:
        pass
    except Exception as exc:
        logger.debug("[L3 Agent] lark_send_text chat_id preparation skipped: %s", exc)
    return action_input, lark_bind


def bind_lark_context(lark_chat_id: str) -> Any:
    try:
        from l3_node.channels.lark.turn_chat_context import bind_lark_chat_id_for_tools

        return bind_lark_chat_id_for_tools(lark_chat_id)
    except Exception:
        return None


def reset_lark_context(token: Any) -> None:
    if token is None:
        return
    try:
        from l3_node.channels.lark.turn_chat_context import reset_lark_chat_id_for_tools

        reset_lark_chat_id_for_tools(token)
    except Exception:
        pass
