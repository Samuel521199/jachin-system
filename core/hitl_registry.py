"""
HITL (Human-in-the-Loop) 授权注册表

当 Agent 需要人工授权时（如 core:shell_exec），注册 task_id，
等待 Layer 3 精灵的 HITL_APPROVE/HITL_REJECT 后释放挂起。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# task_id -> {"event": asyncio.Event, "approved": bool | None}
_pending: dict[str, dict[str, Any]] = {}


def register(task_id: str) -> asyncio.Event:
    """注册 HITL 挂起，返回 Event，resolve 时 set"""
    ev = asyncio.Event()
    _pending[task_id] = {"event": ev, "approved": None}
    return ev


def resolve(task_id: str, approved: bool) -> None:
    """解析 HITL：设置结果并释放挂起"""
    if task_id not in _pending:
        logger.warning("[HITL] 未知 task_id: %s", task_id)
        return
    _pending[task_id]["approved"] = approved
    _pending[task_id]["event"].set()


async def await_response(task_id: str, timeout: float = 300.0) -> bool:
    """
    等待 HITL 响应，返回是否授权。
    超时视为拒绝。
    """
    if task_id not in _pending:
        return False
    ev = _pending[task_id]["event"]
    try:
        await asyncio.wait_for(ev.wait(), timeout=timeout)
        return _pending[task_id].get("approved", False)
    except asyncio.TimeoutError:
        _pending[task_id]["approved"] = False
        return False
    finally:
        _pending.pop(task_id, None)


def cleanup(task_id: str) -> None:
    """清理已完成的挂起"""
    _pending.pop(task_id, None)
