"""
跨请求持久化槽位追问轮次（bundle 每轮可能重建）。键：session_id|correlation 优先 + skill_id。
"""
from __future__ import annotations

import threading
from typing import Any

_lock = threading.Lock()
_store: dict[str, dict[str, Any]] = {}


def _session_key(bundle: Any) -> str:
    s = str(getattr(bundle, "session_id", "") or "").strip()
    c = str(getattr(bundle, "correlation_id", "") or "").strip()
    return (s or c or "global").strip() or "global"


def _compound_key(bundle: Any, skill_id: str) -> str:
    return f"{_session_key(bundle)}::{skill_id.strip()}"


def get_rounds(bundle: Any, skill_id: str) -> int:
    k = _compound_key(bundle, skill_id)
    with _lock:
        try:
            return max(0, int(_store.get(k, {}).get("rounds", 0)))
        except (TypeError, ValueError):
            return 0


def set_rounds(bundle: Any, skill_id: str, n: int) -> None:
    k = _compound_key(bundle, skill_id)
    with _lock:
        ent = _store.get(k) or {}
        ent["rounds"] = max(0, int(n))
        _store[k] = ent


def bump_rounds(bundle: Any, skill_id: str) -> int:
    n = get_rounds(bundle, skill_id) + 1
    set_rounds(bundle, skill_id, n)
    return n


def clear_skill(bundle: Any, skill_id: str) -> None:
    k = _compound_key(bundle, skill_id)
    with _lock:
        _store.pop(k, None)


def clear_all_for_bundle(bundle: Any) -> None:
    """逃生舱或 Abort 时清空本会话键下所有 skill 条目（前缀匹配）。"""
    prefix = _session_key(bundle) + "::"
    with _lock:
        dead = [k for k in list(_store.keys()) if k.startswith(prefix) or k == _session_key(bundle)]
        for k in dead:
            _store.pop(k, None)
