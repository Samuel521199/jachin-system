"""L3 run cancellation token and streaming LLM task cancellation helpers."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_registry: dict[str, asyncio.Event] = {}
_stream_tasks: dict[str, asyncio.Task] = {}


def register_stream_task(run_id: str, task: asyncio.Task) -> None:
    if run_id and task:
        _stream_tasks[str(run_id)] = task


def unregister_stream_task(run_id: str) -> None:
    _stream_tasks.pop(str(run_id), None)


def register_cancel_event(run_id: str, ev: asyncio.Event) -> None:
    if run_id:
        _registry[str(run_id)] = ev


def unregister_cancel_event(run_id: str) -> None:
    _registry.pop(str(run_id), None)


def request_cancel_run(run_id: str) -> bool:
    """请求取消指定 run_id 的 Agent 循环（设置 Event + 取消流式 LLM Task）。返回是否找到任一路径。"""
    rid = str(run_id)
    ev = _registry.get(rid)
    st = _stream_tasks.get(rid)
    if ev is None and st is None:
        return False
    if ev is not None:
        ev.set()
    if st is not None and not st.done():
        st.cancel()
    logger.info("[AgentCancel] 已请求取消 run_id=%s", rid[:16] if len(rid) > 16 else rid)
    return True


def get_cancel_event(run_id: str) -> Optional[asyncio.Event]:
    return _registry.get(str(run_id))
