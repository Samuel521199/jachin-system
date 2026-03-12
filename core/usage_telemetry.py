"""
Jachin L2 - 本地审计与用量记录

AOP 埋点：在 MCP call_tool、Wasm 执行等入口完成后，异步写入 usage_telemetry 表。
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


def record_usage(
    sub_account_id: str,
    item_id: str,
    action_name: str,
    status: str,
    latency_ms: Optional[float] = None,
) -> None:
    """
    同步写入用量记录到 usage_telemetry 表。
    供 MCP invoke、Skill execute 等入口调用。
    为避免阻塞主流程，建议在独立线程/协程中调用。
    """
    try:
        from core.db import get_connection

        conn = get_connection()
        try:
            row_id = f"ut-{secrets.token_hex(8)}"
            conn.execute(
                """
                INSERT INTO usage_telemetry (id, sub_account_id, item_id, action_name, status, latency_ms, reported)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                """,
                (row_id, sub_account_id, item_id, action_name, status, latency_ms),
            )
            conn.commit()
            logger.debug(
                "[Telemetry] 已记录 sub=%s item=%s action=%s status=%s latency=%.0fms",
                sub_account_id[:12],
                item_id,
                action_name,
                status,
                latency_ms or 0,
            )
        finally:
            conn.close()
    except Exception as e:
        logger.warning("[Telemetry] 写入失败: %s", e, exc_info=False)


def record_usage_async(
    sub_account_id: str,
    item_id: str,
    action_name: str,
    status: str,
    latency_ms: Optional[float] = None,
) -> None:
    """
    异步写入用量记录（fire-and-forget）。
    在事件循环中运行时，将同步写入放入 executor 避免阻塞。
    """
    try:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(
            None,
            lambda: record_usage(sub_account_id, item_id, action_name, status, latency_ms),
        )
    except RuntimeError:
        record_usage(sub_account_id, item_id, action_name, status, latency_ms)
