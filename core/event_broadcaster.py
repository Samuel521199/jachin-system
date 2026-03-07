"""
Jachin Nexus V2 - 全局事件广播器

使用 asyncio.Queue 订阅者列表模式管理 SSE 连接的客户端。
当 Inventory 热重载、技能更新等事件发生时，通过 broadcast_event 推送给所有 L3 客户端。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# 全局订阅者队列集合：每个 SSE 连接一个 Queue
_subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
_lock = asyncio.Lock()


def broadcast_event(event: dict[str, Any]) -> None:
    """
    向所有已连接的 L3 客户端广播事件。

    支持两种格式（兼容不同前端）：
    - 扁平格式: {"type": "INVENTORY_UPDATED", "message": "...", "timestamp": "..."}
    - 规范格式: {"event": "INVENTORY_UPDATED", "data": {"message": "...", "timestamp": "..."}}
    """
    dead: set[asyncio.Queue[dict[str, Any]]] = set()
    for q in _subscribers:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            dead.add(q)
        except Exception as e:
            logger.debug("[EventBroadcaster] broadcast failed: %s", e)
            dead.add(q)
    for q in dead:
        _subscribers.discard(q)
    if _subscribers:
        ev_type = event.get("event") or event.get("type", "?")
        logger.info("[EventBroadcaster] 已广播 %s 至 %d 个客户端", ev_type, len(_subscribers))


def create_subscriber_queue(maxsize: int = 64) -> asyncio.Queue[dict[str, Any]]:
    """创建并返回一个新的订阅者队列（由路由在连接建立时调用）"""
    return asyncio.Queue(maxsize=maxsize)


async def register_subscriber(queue: asyncio.Queue[dict[str, Any]]) -> None:
    """注册订阅者"""
    async with _lock:
        _subscribers.add(queue)
    logger.debug("[EventBroadcaster] 客户端已连接，当前订阅者=%d", len(_subscribers))


async def unregister_subscriber(queue: asyncio.Queue[dict[str, Any]]) -> None:
    """注销订阅者"""
    async with _lock:
        _subscribers.discard(queue)
    logger.debug("[EventBroadcaster] 客户端已断开，当前订阅者=%d", len(_subscribers))


def build_inventory_updated_event(message: str, **extra: Any) -> dict[str, Any]:
    """
    构建 INVENTORY_UPDATED 事件，同时支持 type 和 event/data 两种格式。
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    data = {"message": message, "timestamp": timestamp, **extra}
    return {
        "event": "INVENTORY_UPDATED",
        "type": "INVENTORY_UPDATED",
        "message": message,
        "timestamp": timestamp,
        "data": data,
        **extra,
    }
