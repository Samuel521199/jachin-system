"""
同会话在用户消息「排队等待锁 / 飞书同 chat 已有在途工单」时，将新进线原文暂存，
供**当前** ``run_agent`` ReAct 在**下一轮 LLM 调用前**以 user 消息并入上下文（路线图 **P** 场景四 · 中段热注入 · 单机版）。

- HTTP：在 ``/api/v3/agent/run`` 尝试获取同 ``chat_id``/``session_id`` 锁前，若锁已被占用则 ``record_pending``。
- 飞书：可选 ``JACHIN_IM_SESSION_HOT_INJECT=1`` 时在 ``prior>0`` 同时入账（默认关闭，避免与 **X** rollup 简单重复；需时可开）。

关闭：``JACHIN_SESSION_HOT_USER_INJECT_DISABLE=1``
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Any


_LOCK = threading.RLock()
# session_key -> deque of (ts, text)
_BUFFERS: dict[str, deque[tuple[float, str]]] = {}

_MAX_LINES = 12
_MAX_CHARS_LINE = 500


def _disabled() -> bool:
    return (os.environ.get("JACHIN_SESSION_HOT_USER_INJECT_DISABLE") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def record_pending_session_user_text(session_key: str, text: str) -> None:
    sk = (session_key or "").strip()
    if _disabled() or not sk:
        return
    t = (text or "").strip()
    if not t:
        return
    if len(t) > _MAX_CHARS_LINE:
        t = t[:_MAX_CHARS_LINE] + "…"
    now = time.time()
    with _LOCK:
        dq = _BUFFERS.setdefault(sk, deque(maxlen=_MAX_LINES * 2))
        if dq and dq[-1][1] == t:
            return
        dq.append((now, t))


def peek_pending_session_user(session_key: str) -> dict[str, Any] | None:
    """只读探针（如 **R** runtime-snapshot）：不消费。"""
    sk = (session_key or "").strip()
    if not sk:
        return None
    with _LOCK:
        dq = _BUFFERS.get(sk)
        if not dq:
            return None
        texts = [x[1] for x in list(dq)[-6:]]
        return {
            "pending_count": len(dq),
            "since_ts": dq[0][0],
            "previews": texts,
        }


def drain_pending_session_user_texts(session_key: str, *, max_items: int = 6) -> list[str]:
    """供 ``agent_core`` 每轮 LLM 前调用：取出并清空该会话暂存（FIFO 批量）。"""
    sk = (session_key or "").strip()
    if _disabled() or not sk:
        return []
    cap = max(1, min(int(max_items), 24))
    with _LOCK:
        dq = _BUFFERS.get(sk)
        if not dq:
            return []
        out: list[str] = []
        while dq and len(out) < cap:
            _, tx = dq.popleft()
            out.append(tx)
        if not dq:
            _BUFFERS.pop(sk, None)
        return out
