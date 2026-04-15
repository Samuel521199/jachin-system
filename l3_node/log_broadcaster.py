"""
L3 全息监控 - 全局实时日志广播中心

将调度器、锁竞争、任务成败等状态实时打印到 PowerShell，并广播到前端 SSE 订阅。
便携包模式下（JACHIN_LOG_DIR 已设置）同时写入 logs/l3_broadcast.log 便于排查。
"""

from __future__ import annotations

import logging
import os
import queue
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.utils.log_utils import truncate_jsonish_text_for_ws_or_log

logger = logging.getLogger("l3_node")

# 全息广播单条消息上限（先字段级截断再入队，避免 SSE/终端同步写卡死）
_BROADCAST_MESSAGE_MAX_TOTAL = 48_000

# 线程安全队列，供 SSE 生成器消费（加大容量以承载深度执行日志分片）
_log_queue: queue.Queue[tuple[str, str, float]] = queue.Queue(maxsize=4000)
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
    _msg_len = len(message)
    if _msg_len > _BROADCAST_MESSAGE_MAX_TOTAL:
        try:
            message = truncate_jsonish_text_for_ws_or_log(
                message,
                max_field_len=500,
                max_total=_BROADCAST_MESSAGE_MAX_TOTAL,
            )
        except Exception:
            message = message[:_BROADCAST_MESSAGE_MAX_TOTAL] + f"... [已截断，原长度: {_msg_len} 字符]"
    ts = datetime.now().timestamp()
    # 1. 打印到 PowerShell（带颜色 + UTC 时间），仅当 console=True
    if console:
        utc = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        color = _COLORS.get(level, _COLORS["INFO"])
        reset = _COLORS["RESET"]
        try:
            print(f"{utc} {color}[{level}] {message}{reset}", file=sys.stderr, flush=True)
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

    # 3. 便携包模式：写入 logs/l3_broadcast.log
    log_dir = os.environ.get("JACHIN_LOG_DIR")
    if log_dir:
        try:
            p = Path(log_dir) / "l3_broadcast.log"
            p.parent.mkdir(parents=True, exist_ok=True)
            utc = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            with open(p, "a", encoding="utf-8") as f:
                f.write(f"{utc} [{level}] {message}\n")
        except OSError:
            pass


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


_DEEP_BROADCAST_CHUNK = 7500


class DeepLogBroadcastHandler(logging.Handler):
    """将 jachin.deep 大块日志切片送入全息队列（避免单条 SSE 过大）。"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            level = record.levelname.upper()
            if level == "DEBUG":
                return
            if level == "CRITICAL":
                level = "ERROR"
            if len(msg) <= _DEEP_BROADCAST_CHUNK:
                broadcast_log(msg, level, console=False)
                return
            total = (len(msg) + _DEEP_BROADCAST_CHUNK - 1) // _DEEP_BROADCAST_CHUNK
            for i in range(0, len(msg), _DEEP_BROADCAST_CHUNK):
                part = msg[i : i + _DEEP_BROADCAST_CHUNK]
                idx = i // _DEEP_BROADCAST_CHUNK + 1
                broadcast_log(f"[Deep {idx}/{total}] {part}", level, console=False)
        except Exception:
            self.handleError(record)


def install_deep_log_handlers(*, stream_handler: logging.Handler | None = None) -> None:
    """
    配置 logger「jachin.deep」：控制台 + l3_debug.log（与 early_log 共用 FileHandler）+ 全息 SSE。
    若已与 l3_node/core 共用 StreamHandler，传入同一实例可避免重复配置。
    """
    try:
        from core.deep_execution_log import deep_log_enabled
    except ImportError:
        return
    if not deep_log_enabled():
        return
    dlg = logging.getLogger("jachin.deep")
    dlg.setLevel(logging.INFO)
    dlg.propagate = False
    for h in dlg.handlers[:]:
        dlg.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    fmt_deep = logging.Formatter("%(message)s")
    if stream_handler is not None:
        sh = stream_handler
        if sh.formatter is None:
            sh.setFormatter(fmt)
        dlg.addHandler(sh)
    else:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        sh.setLevel(logging.INFO)
        dlg.addHandler(sh)
    try:
        from l3_node.early_log import attach_file_handler_to_logger

        attach_file_handler_to_logger(dlg)
        try:
            from l3_node.async_file_log import get_batched_file_handler

            _bfh = get_batched_file_handler()
            if _bfh is not None:
                _bfh.setFormatter(fmt_deep)
            else:
                _fh = next((h for h in dlg.handlers if isinstance(h, logging.FileHandler)), None)
                if _fh is not None and _fh.formatter is None:
                    _fh.setFormatter(fmt_deep)
        except ImportError:
            _fh = next((h for h in dlg.handlers if isinstance(h, logging.FileHandler)), None)
            if _fh is not None and _fh.formatter is None:
                _fh.setFormatter(fmt_deep)
    except Exception:
        pass
    dbh = DeepLogBroadcastHandler()
    dbh.setFormatter(fmt_deep)
    dbh.setLevel(logging.INFO)
    dlg.addHandler(dbh)


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
    for name in ("l3_node.agent_core", "l3_node.primitives.mcp.registry", "l3_node.bootstrap", "l3_node.llm_client"):
        logging.getLogger(name).setLevel(logging.INFO)


__all__ = [
    "broadcast_log",
    "consume_logs",
    "format_sse_event",
    "install_broadcast_handler",
    "install_deep_log_handlers",
    "BroadcastLogHandler",
    "DeepLogBroadcastHandler",
]
