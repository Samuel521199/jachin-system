"""
WebSocket 会话 SIQ（路线图 AU 全量队列化 · 桌面/终端通道）

当 JACHIN_WS_SIQ_ENABLE=1 时，同 session_key 的多条 WS 进线经 SessionInstructionQueue 调度，
替代默认「取消上一轮并替换」行为（可与 JACHIN_WS_SUPERSEDE_ACK 并存：SIQ 优先）。

环境变量
--------
JACHIN_WS_SIQ_ENABLE=1          开启 WS SIQ
JACHIN_SIQ_ENABLE=1             或与 HTTP/IM 共用总开关
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


def ws_siq_enabled() -> bool:
    if (os.environ.get("JACHIN_WS_SIQ_DISABLE") or "").strip().lower() in (
        "1", "true", "yes",
    ):
        return False
    ws = (os.environ.get("JACHIN_WS_SIQ_ENABLE") or "").strip().lower()
    if ws in ("1", "true", "yes"):
        return True
    try:
        from l3_node.session_instruction_queue import siq_enabled

        return siq_enabled()
    except ImportError:
        return False


async def schedule_ws_turn_via_siq(
    *,
    session_key: str,
    intent: str,
    execute_coro_factory: Callable[[str], Awaitable[Any]],
) -> str:
    """
    将一轮 WS intent 提交到 SIQ；execute_coro_factory 接收最终 intent 并执行整轮流式任务。
    返回 queued|parallel|rejected|disabled。
    """
    if not ws_siq_enabled() or not (session_key or "").strip():
        return "disabled"

    from l3_node.session_instruction_queue import SIQInstruction, submit_instruction

    done = asyncio.Event()
    holder: dict[str, str] = {"status": "queued"}

    async def execute_fn(instr: SIQInstruction) -> str:
        await execute_coro_factory(instr.intent)
        holder["status"] = "done"
        done.set()
        return "ok"

    status = await submit_instruction(
        session_key.strip(),
        intent,
        execute_fn,
        metadata={"channel": "ws_server"},
    )
    if status in ("queued", "parallel"):
        try:
            from l3_node.session_instruction_queue import _instruction_timeout

            await asyncio.wait_for(done.wait(), timeout=_instruction_timeout() + 10.0)
        except asyncio.TimeoutError:
            logger.warning("[WS-SIQ] turn timed out session=%s", session_key[:24])
    return status
