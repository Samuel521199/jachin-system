"""高价值本地记忆写入 bump 代数，供 MemorySyncDaemon 分片睡眠内提前唤醒同步。"""
from __future__ import annotations

import time
import threading

_urgent_generation = 0
_urgent_lock = threading.Lock()
_last_emit_ts = 0.0


def bump_urgent_l3_local_sync() -> int:
    """返回单调代数；守护进程可对比是否需提前唤醒。"""
    global _urgent_generation, _last_emit_ts
    with _urgent_lock:
        _urgent_generation += 1
        _last_emit_ts = time.time()
        return _urgent_generation


def get_urgent_sync_generation() -> int:
    with _urgent_lock:
        return _urgent_generation


def seconds_since_last_urgent() -> float:
    with _urgent_lock:
        return max(0.0, time.time() - _last_emit_ts)
