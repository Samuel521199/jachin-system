"""
Event Bus - 轻量异步事件总线

边缘智能体神经反射弧：当 core-vad-audio 监听到声音、core-cron-trigger 触发时，
主动向总线抛出事件；WorkflowRunner 订阅后瞬间唤醒，将数据沿 DAG 传递。

支持多上游 Join 节点：asyncio.gather 等待所有上游事件后执行下游。
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


@dataclass
class BusEvent:
    """总线事件"""
    type: str  # audio.input, cron.trigger, ...
    payload: dict[str, Any]
    source_plugin_id: str | None = None


# 事件类型 → 工作流 ID 列表 → handler
_subscriptions: dict[str, dict[str, Callable[..., Coroutine[Any, Any, None]]]] = defaultdict(dict)
_queue: asyncio.Queue[BusEvent] = asyncio.Queue()
_consumer_task: asyncio.Task | None = None


def emit(event_type: str, payload: dict[str, Any], source_plugin_id: str | None = None) -> None:
    """
    同步发射事件（非阻塞入队）
    插件调用示例：emit("audio.input", {"text": "用户说的话"}, "core-vad-audio")
    """
    ev = BusEvent(type=event_type, payload=payload, source_plugin_id=source_plugin_id)
    try:
        _queue.put_nowait(ev)
    except asyncio.QueueFull:
        logger.warning("Event bus queue full, dropping event %s", event_type)


async def emit_async(event_type: str, payload: dict[str, Any], source_plugin_id: str | None = None) -> None:
    """异步发射事件"""
    ev = BusEvent(type=event_type, payload=payload, source_plugin_id=source_plugin_id)
    await _queue.put(ev)


def subscribe(event_type: str, workflow_id: str, handler: Callable[..., Coroutine[Any, Any, None]]) -> None:
    """
    订阅事件类型，当事件到达时调用 handler(workflow_id, event)
    """
    _subscriptions[event_type][workflow_id] = handler
    logger.debug("Subscribed workflow %s to event %s", workflow_id, event_type)


def unsubscribe(event_type: str, workflow_id: str) -> None:
    """取消订阅"""
    _subscriptions[event_type].pop(workflow_id, None)


async def _consume_loop() -> None:
    """消费循环：从队列取事件，分发给订阅者"""
    while True:
        try:
            ev = await _queue.get()
            handlers = _subscriptions.get(ev.type, {})
            for wf_id, handler in list(handlers.items()):
                try:
                    await handler(ev)
                except Exception as e:
                    logger.exception("Event handler error workflow=%s: %s", wf_id, e)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception("Event consume error: %s", e)


def start_consumer() -> None:
    """启动事件消费循环（在 asyncio 事件循环中调用）"""
    global _consumer_task
    if _consumer_task is None or _consumer_task.done():
        _consumer_task = asyncio.create_task(_consume_loop())
        logger.info("Event bus consumer started")


def stop_consumer() -> None:
    """停止消费循环"""
    global _consumer_task
    if _consumer_task and not _consumer_task.done():
        _consumer_task.cancel()
        _consumer_task = None
