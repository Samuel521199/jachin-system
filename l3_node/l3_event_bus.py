"""
L3 轻量事件总线：后台任务状态 → 已订阅 WebSocket 客户端广播。

与 ws_server 解耦，避免 background_task_service ↔ ws_server 循环依赖。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_subscribers: set[Any] = set()
_lock = asyncio.Lock()


async def register_background_task_subscriber(ws: Any) -> None:
    async with _lock:
        _subscribers.add(ws)
    logger.debug("[L3EventBus] background_task subscriber +1 total=%d", len(_subscribers))


async def unregister_background_task_subscriber(ws: Any) -> None:
    async with _lock:
        _subscribers.discard(ws)


async def broadcast_background_task_event(payload: dict[str, Any]) -> None:
    """向所有订阅者发送 JSON；失败连接自动移除。"""
    async with _lock:
        subs = list(_subscribers)
    if not subs:
        return
    try:
        msg = json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        logger.warning("[L3EventBus] 序列化失败: %s", e)
        return
    dead: list[Any] = []
    for ws in subs:
        try:
            await ws.send(msg)
        except Exception:
            dead.append(ws)
    if dead:
        async with _lock:
            for ws in dead:
                _subscribers.discard(ws)
