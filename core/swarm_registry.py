"""
Jachin Nexus v8.0 — Edge Mesh Swarm 虫群任务注册表

参考 hitl_registry 设计：主脑挂起，向全网广播 task_offer，
节点竞标接单后执行，结果回传时 resolve 释放挂起。
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)

# task_id -> {event, result, worker_id, payload, ...}
_pending: dict[str, dict[str, Any]] = {}


def register_task(tool_name: str, payload: dict[str, Any] | None = None) -> str:
    """
    注册虫群任务，创建挂起锁。返回 task_id。
    """
    task_id = f"T-{uuid.uuid4().hex[:8].upper()}"
    ev = asyncio.Event()
    _pending[task_id] = {
        "event": ev,
        "result": None,
        "worker_id": None,
        "tool_name": tool_name,
        "payload": payload or {},
        "created_at": time.time(),
    }
    return task_id


def claim_task(task_id: str, worker_id: str) -> bool:
    """
    节点竞标接单。若任务存在且未被接单，返回 True；否则 False。
    """
    if task_id not in _pending:
        logger.warning("[Swarm] 未知 task_id: %s", task_id)
        return False
    rec = _pending[task_id]
    if rec.get("worker_id") is not None:
        logger.warning("[Swarm] 任务 %s 已被 %s 接单", task_id, rec["worker_id"])
        return False
    rec["worker_id"] = worker_id
    return True


def resolve_task(task_id: str, result_data: Any) -> None:
    """
    释放挂起锁，写入结果。
    """
    if task_id not in _pending:
        logger.warning("[Swarm] 未知 task_id: %s", task_id)
        return
    _pending[task_id]["result"] = result_data
    _pending[task_id]["event"].set()


async def await_task_result(task_id: str, timeout: float = 300.0) -> Any:
    """
    主脑挂起等待节点回传结果。超时返回 None。
    """
    if task_id not in _pending:
        return None
    ev = _pending[task_id]["event"]
    try:
        await asyncio.wait_for(ev.wait(), timeout=timeout)
        return _pending.get(task_id, {}).get("result")
    except asyncio.TimeoutError:
        logger.warning("[Swarm] 任务 %s 超时", task_id)
        return None
    finally:
        _pending.pop(task_id, None)


def get_task_payload(task_id: str) -> dict[str, Any] | None:
    """获取任务完整参数，供接单节点执行"""
    rec = _pending.get(task_id)
    return rec.get("payload") if rec else None


def get_task_info(task_id: str) -> dict[str, Any] | None:
    """获取任务信息（tool_name, payload, worker_id）"""
    return _pending.get(task_id)
