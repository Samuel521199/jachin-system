"""[遗留] 原 L2 记忆同步守护用的 urgent 代数；L3 记忆已闭环于 Memory Nexus，bump 恒为 no-op。"""
from __future__ import annotations

import time
import threading

_urgent_generation = 0
_urgent_lock = threading.Lock()
_last_emit_ts = 0.0


def bump_urgent_l3_local_sync() -> int:
    """兼容占位：已不再向 L2 同步记忆，恒不递增。"""
    with _urgent_lock:
        return _urgent_generation


def get_urgent_sync_generation() -> int:
    with _urgent_lock:
        return _urgent_generation


def seconds_since_last_urgent() -> float:
    with _urgent_lock:
        return max(0.0, time.time() - _last_emit_ts)
