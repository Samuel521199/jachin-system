"""按飞书 chat_id 跟踪当前前台 run_agent 的 run_id，供第二条进线消息触发 request_cancel_run。"""
from __future__ import annotations

import threading
from typing import Optional

_lock = threading.Lock()
_by_chat: dict[str, str] = {}


def register_foreground_run(chat_id: str, run_id: str) -> None:
    cid = (chat_id or "").strip()
    rid = (run_id or "").strip()
    if not cid or not rid:
        return
    with _lock:
        _by_chat[cid] = rid


def unregister_foreground_run(chat_id: str, run_id: str) -> None:
    cid = (chat_id or "").strip()
    rid = (run_id or "").strip()
    if not cid:
        return
    with _lock:
        if _by_chat.get(cid) == rid:
            _by_chat.pop(cid, None)


def get_active_run_id(chat_id: str) -> Optional[str]:
    cid = (chat_id or "").strip()
    if not cid:
        return None
    with _lock:
        r = _by_chat.get(cid)
        return str(r) if r else None
