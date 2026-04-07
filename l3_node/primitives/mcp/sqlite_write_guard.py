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
    # @modelcontextprotocol/server-sqlite 常见工具名
    if rn in ("read_query", "write_query"):
        return True
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
            f'"{WRITE_ACK_KEY}": true 再调用（该字段不会传给数据库）。'
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
                f'请先请示统帅，获准后在 JSON 中加入 "{WRITE_ACK_KEY}": true。'
            )

    return False, ""
