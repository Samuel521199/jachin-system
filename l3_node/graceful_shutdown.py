"""L3 process-level graceful shutdown hooks."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, List

logger = logging.getLogger(__name__)

_hooks: List[Callable[[], Awaitable[None]]] = []


def register_shutdown_hook(coro_factory: Callable[[], Awaitable[None]]) -> None:
    """注册异步停机钩子（在事件循环仍运行时调用）。"""
    _hooks.append(coro_factory)


async def run_shutdown_hooks(*, timeout_sec: float = 5.0) -> None:
    for h in list(_hooks):
        try:
            await asyncio.wait_for(h(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            logger.warning("[GracefulShutdown] 钩子超时 %.1fs: %s", timeout_sec, h)
        except Exception as e:
            logger.warning("[GracefulShutdown] 钩子异常: %s", e)
