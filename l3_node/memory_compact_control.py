"""
L5 记忆坍缩（梦境合并）运行期控制：协作式取消、与双缓冲配合。

用于在用户点击「停止」或前端发送取消帧时，避免在 LLM 完成后仍执行原子覆写。
"""
from __future__ import annotations

import threading

_cancel_ev = threading.Event()


def reset_memory_compact_cancel() -> None:
    """新一轮 compact 开始时清除取消标记。"""
    _cancel_ev.clear()


def request_memory_compact_cancel() -> None:
    """请求取消当前或下一次 compact 写入阶段（在原子替换主库前生效）。"""
    _cancel_ev.set()


def is_memory_compact_cancel_requested() -> bool:
    return _cancel_ev.is_set()
