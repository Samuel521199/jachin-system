"""
Jachin Nexus v8.0 — Session Manager (会话隔离器)

按 session_id 动态分配独立的异步 Actor 协程，实现高并发下的记忆隔离。
张三问天气、李四问代码，互不干扰。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

Processor = Callable[[dict[str, Any]], Awaitable[None]]

SESSION_IDLE_TIMEOUT = 300.0  # 5 分钟无任务则回收 Actor


class SessionActor:
    """单会话 Actor：串行处理该 session 的任务，与其他 session 并行"""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._processor: Callable[[dict[str, Any]], Awaitable[None]] | None = None

    def start(self, processor: Processor) -> None:
        """启动 Actor 循环"""
        if self._task is not None and not self._task.done():
            return
        self._processor = processor
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        if self._processor is None:
            return
        while True:
            try:
                task_data = await asyncio.wait_for(
                    self._queue.get(),
                    timeout=SESSION_IDLE_TIMEOUT,
                )
                await self._processor(task_data)
            except asyncio.TimeoutError:
                logger.debug("[SessionActor] session=%s 空闲超时，回收", self.session_id[:8])
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("[SessionActor] session=%s 处理异常: %s", self.session_id[:8], e)

    async def submit(self, task_data: dict[str, Any]) -> None:
        """提交任务到该 session 队列"""
        await self._queue.put(task_data)

    def cancel(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()


class SessionManager:
    """v8.0 会话管理器：session_id -> SessionActor"""

    def __init__(self, processor: Processor) -> None:
        self._actors: dict[str, SessionActor] = {}
        self._lock = asyncio.Lock()
        self._processor = processor

    def _make_session_id(self, source: str, metadata: dict[str, Any]) -> str:
        """从 metadata 提取或生成 session_id"""
        sid = (metadata or {}).get("session_id")
        if sid and str(sid).strip():
            return str(sid).strip()
        # 兼容：Telegram chat_id、设备 UUID 等
        chat_id = (metadata or {}).get("chat_id")
        if chat_id is not None:
            return f"{source}:{chat_id}"
        return f"{source}:{uuid.uuid4().hex[:12]}"

    async def submit(
        self,
        ev_id: int,
        source: str,
        intent: str,
        metadata: dict[str, Any],
    ) -> None:
        """提交任务，按 session_id 路由到对应 Actor"""
        session_id = self._make_session_id(source, metadata)
        task_data = {
            "ev_id": ev_id,
            "source": source,
            "intent": intent,
            "metadata": metadata,
            "session_id": session_id,
        }
        async with self._lock:
            actor = self._actors.get(session_id)
            if actor is None:
                actor = SessionActor(session_id)
                actor.start(self._processor)
                self._actors[session_id] = actor
        await actor.submit(task_data)

    async def remove(self, session_id: str) -> None:
        """移除并取消指定 session 的 Actor"""
        async with self._lock:
            actor = self._actors.pop(session_id, None)
        if actor:
            actor.cancel()
