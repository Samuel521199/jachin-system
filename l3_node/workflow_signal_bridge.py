"""
进程内工作流信号桥：Lark/HTTP 注入的信号由正在执行的 HarvestLoop 等节点在循环内拉取。

与 local_memory 持久化互补：inject_signal 会同时写入持久化 state._workflow_signals。
"""
from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_pending: dict[str, list[str]] = {}


def push_signal(workflow_id: str, signal: str) -> None:
    wid = (workflow_id or "").strip()
    if not wid:
        return
    sig = str(signal).strip()
    if not sig:
        return
    with _lock:
        _pending.setdefault(wid, []).append(sig)
    logger.info("[WorkflowSignalBridge] 入队 workflow=%s signal=%s", wid, sig)


def purge_stop_harvest_signals(workflow_id: str) -> int:
    """
    从进程内信号队列中移除所有 STOP_HARVEST（飞书「继续」时清理未消费的停止信号）。
    返回移除条数。
    """
    wid = (workflow_id or "").strip()
    if not wid:
        return 0
    stop = "STOP_HARVEST"
    with _lock:
        lst = _pending.get(wid, [])
        if not lst:
            return 0
        kept = [x for x in lst if str(x).strip() != stop]
        removed = len(lst) - len(kept)
        if kept:
            _pending[wid] = kept
        else:
            _pending.pop(wid, None)
    if removed:
        logger.info("[WorkflowSignalBridge] 已清除 workflow=%s 的 STOP_HARVEST x%d", wid, removed)
    return removed


def drain_merge_into_context(context: dict[str, Any], workflow_id: str) -> None:
    """将本进程内待处理信号合并进 context 的 _workflow_signals（FIFO 追加到队尾）。"""
    wid = (workflow_id or "").strip()
    if not wid:
        return
    with _lock:
        batch = _pending.pop(wid, [])
    if not batch:
        return
    if hasattr(context, "push_signal"):
        for s in batch:
            context.push_signal(s)
    else:
        q = context.setdefault("_workflow_signals", [])
        if not isinstance(q, list):
            q = []
            context["_workflow_signals"] = q
        q.extend(batch)
    logger.debug("[WorkflowSignalBridge] 已合并 %d 条信号到 context workflow=%s", len(batch), wid)
