"""
L3 全息监控 - 全局实时日志广播中心

将调度器、锁竞争、任务成败等状态实时打印到 PowerShell，并广播到前端 SSE 订阅。
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
from datetime import datetime
from typing import Optional

logger = logging.getLogger("l3_node")

# 线程安全队列，供 SSE 生成器消费（最大 500 条，防止内存溢出）
_log_queue: queue.Queue[tuple[str, str, float]] = queue.Queue(maxsize=500)
_queue_lock = threading.Lock()

# ANSI 颜色（PowerShell / Windows Terminal 支持）
_COLORS = {
    "INFO": "\033[90m",      # 灰色
    "SUCCESS": "\033[92m",   # 绿色
    "WARNING": "\033[93m",   # 黄色
    "ERROR": "\033[91m",     # 红色
    "RESET": "\033[0m",
}


def broadcast_log(message: str, level: str = "INFO", *, console: bool = True) -> None:
    """
    广播日志：打印到终端 + 放入队列供 SSE 订阅。

    Args:
        message: 日志内容
        level: INFO | SUCCESS | WARNING | ERROR
        console: 是否同时打印到终端（Handler 转发时设为 False 避免重复）
    """
    level = (level or "INFO").upper()
    if level not in _COLORS:
        level = "INFO"
    ts = datetime.now().timestamp()
    # 1. 打印到 PowerShell（带颜色），仅当 console=True
    if console:
        color = _COLORS.get(level, _COLORS["INFO"])
        reset = _COLORS["RESET"]
        try:
            print(f"{color}[{level}] {message}{reset}", file=sys.stderr, flush=True)
        except Exception:
            pass
    # 2. 放入队列（非阻塞，满则丢弃最旧）
    try:
        with _queue_lock:
            if _log_queue.full():
                try:
                    _log_queue.get_nowait()
                except queue.Empty:
                    pass
            _log_queue.put_nowait((message, level, ts))
    except Exception as e:
        logger.debug("[LogBroadcaster] 入队失败: %s", e)


def consume_logs() -> Optional[tuple[str, str, float]]:
    """从队列取出一条日志，超时 1 秒返回 None（供 SSE 生成器轮询）"""
    try:
        return _log_queue.get(timeout=1.0)
    except queue.Empty:
        return None


def format_sse_event(message: str, level: str, ts: float) -> str:
    """格式化为 SSE data 行（JSON）"""
    import json
    payload = {"message": message, "level": level, "ts": ts}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


class BroadcastLogHandler(logging.Handler):
    """将 l3_node 日志转发到 broadcast_log，供全息监控 SSE 订阅"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            level = record.levelname.upper()
            if level == "DEBUG":
                return  # 不广播 DEBUG，减少噪音
            if level == "CRITICAL":
                level = "ERROR"
            # console=False 避免与 root logger 重复输出
            broadcast_log(msg, level, console=False)
        except Exception:
            self.handleError(record)


def install_broadcast_handler() -> None:
    """为 l3_node 及其子模块安装 BroadcastLogHandler，使全息监控能收到详细日志"""
    root = logging.getLogger("l3_node")
    for h in root.handlers[:]:
        if isinstance(h, BroadcastLogHandler):
            return  # 已安装
    handler = BroadcastLogHandler()
    handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    handler.setLevel(logging.INFO)
    root.addHandler(handler)
    # 确保 l3_node 子模块的日志能传播上来
    root.setLevel(logging.INFO)
    for name in ("l3_node.agent_core", "l3_node.skills.mcp_registry", "l3_node.bootstrap", "l3_node.llm_client"):
        logging.getLogger(name).setLevel(logging.INFO)


__all__ = ["broadcast_log", "consume_logs", "format_sse_event", "install_broadcast_handler", "BroadcastLogHandler"]
