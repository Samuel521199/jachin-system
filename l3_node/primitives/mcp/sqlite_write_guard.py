"""
SQLite 类 MCP 写操作签批门控：防止「统帅让我用工具」时模型绕过 System Prompt 直接 write_query。

- 默认开启；设置环境变量 JACHIN_MCP_SQLITE_WRITE_GUARD=0 可关闭。
- 批准后由模型在 Action Input 中附带 jachin_mcp_write_ack: true（该键在发往 MCP 子进程前会被剔除）。
"""
from __future__ import annotations

import os
import re
from typing import Any

WRITE_ACK_KEY = "jachin_mcp_write_ack"


def sqlite_write_guard_enabled() -> bool:
    v = (os.environ.get("JACHIN_MCP_SQLITE_WRITE_GUARD") or "1").strip().lower()
    return v not in ("0", "false", "no", "off", "")


def is_sqlite_family_tool(tool_id: str, raw_name: str) -> bool:
    tid = (tool_id or "").lower()
    rn = (raw_name or "").lower()
    if "sqlite" in tid:
        return True
    # 官方 read/write_query；npm「mcp-sqlite」等社区实现的工具名
    if rn in ("read_query", "write_query"):
        return True
    try:
        from l3_node.primitives.tools.loader import MCP_SQLITE_COMMUNITY_TOOL_RAW

        if rn in MCP_SQLITE_COMMUNITY_TOOL_RAW:
            return True
    except ImportError:
        pass
    return False


_MUTATING_SQL = re.compile(
    r"\b(INSERT\s+INTO|UPDATE\s+|DELETE\s+FROM|DROP\s+|ALTER\s+TABLE|TRUNCATE\s+|REPLACE\s+INTO)\b",
    re.IGNORECASE | re.DOTALL,
)


def _sql_from_args(args: dict[str, Any]) -> str:
    if not isinstance(args, dict):
        return ""
    for k in ("query", "sql", "statement", "command"):
        v = args.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def looks_mutating_sql(sql: str) -> bool:
    if not (sql or "").strip():
        return False
    return bool(_MUTATING_SQL.search(sql))


def write_ack_present(args: dict[str, Any]) -> bool:
    if not isinstance(args, dict):
        return False
    v = args.get(WRITE_ACK_KEY)
    if v is True:
        return True
    if isinstance(v, str) and v.strip().lower() in ("1", "true", "yes", "批准", "同意", "确认"):
        return True
    return False


def strip_write_ack_for_mcp(args: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(args, dict) or WRITE_ACK_KEY not in args:
        return args
    out = {k: v for k, v in args.items() if k != WRITE_ACK_KEY}
    return out


_ACK_USER_RE = re.compile(
    r"jachin_mcp_write_ack\s*[:：=]\s*(?:true|1|yes|on)\b",
    re.IGNORECASE,
)


def user_text_grants_mcp_write_ack(text: str) -> bool:
    """
    检测用户是否在聊天中明确授权 SQLite 写签批（与 Action Input 内带键等价）。
    匹配示例：jachin_mcp_write_ack: true、「添加 jachin_mcp_write_ack: true」等。
    """
    s = (text or "").strip()
    if not s:
        return False
    low = s.lower()
    if "jachin_mcp_write_ack" not in low:
        return False
    if _ACK_USER_RE.search(s):
        return True
    compact = re.sub(r"[\s\"'`]+", "", low)
    return "jachin_mcp_write_ack:true" in compact or "jachin_mcp_write_ack:1" in compact


def user_message_is_only_sqlite_write_ack(text: str) -> bool:
    """是否整段用户输入仅为（或多行重复的）写库签批声明，无其它业务语句。"""
    if not user_text_grants_mcp_write_ack(text):
        return False
    leftover: list[str] = []
    for line in (text or "").splitlines():
        seg = line.strip()
        if not seg:
            continue
        if user_text_grants_mcp_write_ack(seg):
            continue
        leftover.append(seg)
    return len(leftover) == 0


def messages_history_has_write_ack_grant(messages: list[Any] | None, *, max_scan: int = 48) -> bool:
    """自末尾向前扫描最近若干条 user 消息，任一条含授权语则视为本会话已获准写签批。"""
    if not messages:
        return False
    n = 0
    for m in reversed(messages):
        if n >= max_scan:
            break
        if isinstance(m, dict) and m.get("role") == "user":
            n += 1
            if user_text_grants_mcp_write_ack(str(m.get("content") or "")):
                return True
    return False


def maybe_inject_user_write_ack(
    tool_id: str,
    raw_name: str,
    args: dict[str, Any],
    *,
    user_granted: bool,
) -> dict[str, Any]:
    """
    若统帅已在聊天中授权，且当前调用需要签批但未带键，则自动补上 jachin_mcp_write_ack（仍会在发往 MCP 前 strip）。
    """
    if not user_granted or not isinstance(args, dict):
        return args
    if not sqlite_write_guard_enabled():
        return args
    if not is_sqlite_family_tool(tool_id, raw_name):
        return args
    if write_ack_present(args):
        return args

    rn = (raw_name or "").strip().lower()
    sql = _sql_from_args(args)

    if rn == "write_query":
        return {**args, WRITE_ACK_KEY: True}

    if rn == "read_query" and sql and looks_mutating_sql(sql):
        return args

    if rn not in ("read_query", "write_query") and sql and looks_mutating_sql(sql):
        return {**args, WRITE_ACK_KEY: True}

    if rn in ("update_records", "delete_records", "create_record"):
        return {**args, WRITE_ACK_KEY: True}

    return args


def check_sqlite_mcp_blocked(
    tool_id: str,
    raw_name: str,
    args: dict[str, Any],
) -> tuple[bool, str]:
    """
    若应拦截返回 (True, 人类可读原因)；否则 (False, "")。
    """
    if not sqlite_write_guard_enabled():
        return False, ""
    if not is_sqlite_family_tool(tool_id, raw_name):
        return False, ""

    rn = (raw_name or "").lower()
    sql = _sql_from_args(args)
    ack = write_ack_present(args)

    if rn == "write_query":
        if ack:
            return False, ""
        return True, (
            "【签批门禁】已拦截 MCP SQLite 的 write_query：须先向统帅展示风险与待执行 SQL，"
            "待统帅明确同意后在**同一条** Action Input JSON 内加入 "
            f'"{WRITE_ACK_KEY}": true 再调用（该字段不会传给数据库），'
            "或在对话中发送「jachin_mcp_write_ack: true」后由系统自动注入。"
            "若需只读，请改用 read_query 且仅使用 SELECT。"
        )

    if rn == "read_query" and sql:
        if looks_mutating_sql(sql):
            return True, (
                "【签批门禁】read_query 中检测到疑似写操作关键字（UPDATE/DELETE/INSERT 等）。"
                "写操作须改用 write_query 并走统帅签批，或拆分为纯 SELECT。"
            )

    if rn not in ("read_query", "write_query") and sql:
        if looks_mutating_sql(sql) and not ack:
            return True, (
                "【签批门禁】检测到疑似数据变更 SQL，且未带签批字段。"
                f'请在 JSON 中加入 "{WRITE_ACK_KEY}": true，或由统帅在对话中发送「jachin_mcp_write_ack: true」。'
            )

    if rn in ("update_records", "delete_records", "create_record") and not ack:
        return True, (
            f"【签批门禁】已拦截 {rn}：请在 Action Input 中加入 \"{WRITE_ACK_KEY}\": true，"
            "或由统帅在聊天中发送「jachin_mcp_write_ack: true」以自动签批后续写操作。"
        )

    return False, ""
