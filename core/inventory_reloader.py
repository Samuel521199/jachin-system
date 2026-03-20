"""
Jachin Nexus V2 - Inventory 重载器（单 Task 串行化）

解决 MCP Python SDK stdio_client 的 anyio cancel scope 跨 task 退出问题：
https://github.com/modelcontextprotocol/python-sdk/issues/79
https://github.com/modelcontextprotocol/python-sdk/issues/521

所有 MCP 的创建（add_server）与关闭（stop）必须在同一 asyncio task 内完成。
本模块提供队列 + 专用 task，确保 reload_inventory 与 mcp_manager.stop() 始终在同一 task 执行。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# 队列项: ("initial", future) | ("reload", future) | ("shutdown",)
_reload_queue: asyncio.Queue[tuple[str, asyncio.Future[Any] | None]] | None = None
_reloader_task: asyncio.Task[None] | None = None


async def _reloader_loop() -> None:
    """
    专用 task：串行处理 MCP 创建与关闭。
    所有 add_server 与 stop 均在此 task 内执行，避免 anyio cancel scope 跨 task 退出。
    """
    global _reload_queue
    if not _reload_queue:
        return
    first = True
    try:
        from core.mcp_client import get_mcp_manager
        from core.inventory_scanner import reload_inventory, ensure_inventory_dirs

        ensure_inventory_dirs()
        manager = get_mcp_manager()

        while True:
            item = await _reload_queue.get()
            kind = item[0]
            future = item[1] if len(item) > 1 else None

            if kind == "shutdown":
                logger.debug("[InventoryReloader] 收到 shutdown，关闭 MCP 管理器")
                await manager.stop()
                if future:
                    try:
                        future.set_result(None)
                    except asyncio.InvalidStateError:
                        pass
                break

            if kind == "initial" or kind == "reload":
                try:
                    if first:
                        first = False
                        await manager.start()
                        logger.info(
                            "[InventoryReloader] MCP 管理器已启动 servers=%d tools=%d",
                            manager.server_count,
                            manager.tool_count,
                        )
                    result = await reload_inventory()
                    if future:
                        try:
                            future.set_result(result)
                        except asyncio.InvalidStateError:
                            pass
                except Exception as e:
                    logger.warning("[InventoryReloader] 重载失败: %s", e, exc_info=True)
                    if future:
                        try:
                            future.set_exception(e)
                        except asyncio.InvalidStateError:
                            pass
    except asyncio.CancelledError:
        logger.debug("[InventoryReloader] 任务已取消")
        raise
    except Exception as e:
        logger.error("[InventoryReloader] 循环异常: %s", e, exc_info=True)


def _ensure_reloader() -> asyncio.Queue[tuple[str, asyncio.Future[Any] | None]]:
    """确保队列与 reloader task 已创建。"""
    global _reload_queue, _reloader_task
    if _reload_queue is None:
        _reload_queue = asyncio.Queue()
        _reloader_task = asyncio.create_task(_reloader_loop())
    return _reload_queue


async def request_initial_reload() -> dict[str, Any]:
    """
    请求首次初始化并重载，等待完成。
    由 lifespan 在启动时调用，必须在 start_cloud_sync_background 之前完成。
    """
    queue = _ensure_reloader()
    future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
    queue.put_nowait(("initial", future))
    return await future


def request_reload() -> asyncio.Future[dict[str, Any]]:
    """
    请求热重载（不等待）。
    由 sync_daemon、API 等调用，返回 Future 供可选 await。
    """
    queue = _ensure_reloader()
    future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
    queue.put_nowait(("reload", future))
    return future


async def request_shutdown_and_wait() -> None:
    """
    请求关闭并等待 reloader 完成。
    由 lifespan 在 shutdown 时调用。
    """
    global _reload_queue, _reloader_task
    if _reload_queue is None or _reloader_task is None:
        return
    future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    _reload_queue.put_nowait(("shutdown", future))
    try:
        await asyncio.wait_for(asyncio.shield(_reloader_task), timeout=10.0)
    except asyncio.TimeoutError:
        logger.warning("[InventoryReloader] 关闭超时，强制取消")
        _reloader_task.cancel()
        try:
            await _reloader_task
        except asyncio.CancelledError:
            pass
    except asyncio.CancelledError:
        _reloader_task.cancel()
        try:
            await _reloader_task
        except asyncio.CancelledError:
            pass
    _reload_queue = None
    _reloader_task = None
