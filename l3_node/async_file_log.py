"""
L3 异步落盘：QueueHandler + 后台 QueueListener + 批量写文件。

- 主线程仅 `queue.put(LogRecord)`，磁盘 I/O 与格式化后的字符串拼接在 listener 线程完成。
- BatchedFileHandler 在 listener 线程内按字符数/时间窗口合并 write，减少 syscall。
- 进程退出须调用 `shutdown_async_file_sink()`（已由 early_log 注册 atexit + graceful_shutdown）。

环境变量：
- JACHIN_L3_LOG_ASYNC=0/false/off：禁用，回退为同步 FileHandler（仅用于极端排障）。
- JACHIN_L3_LOG_ASYNC_BATCH_CHARS：批量写阈值（字符），默认 262144。
- JACHIN_L3_LOG_ASYNC_FLUSH_SEC：距上次 flush 超过该秒数则写出（即使未满批量），默认 0.25。
"""
from __future__ import annotations

import atexit
import logging
import logging.handlers
import os
import queue
import threading
import time
from typing import Any

_LOG = logging.getLogger("l3_node.async_file_log")

_listener: logging.handlers.QueueListener | None = None
_queue_handler: logging.handlers.QueueHandler | None = None
_batched_handler: logging.Handler | None = None
_log_queue: queue.Queue[Any] | None = None
_shutdown_registered = False


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.environ.get(name) or str(default)).strip())
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.environ.get(name) or str(default)).strip())
    except ValueError:
        return default


def async_file_log_enabled() -> bool:
    v = (os.environ.get("JACHIN_L3_LOG_ASYNC") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


class BatchedFileHandler(logging.Handler):
    """
    在单线程内缓冲已格式化的日志行，按批量或时间窗口 flush 到文件。
    仅应由 QueueListener 所在线程调用 emit。
    """

    terminator = "\n"

    def __init__(
        self,
        filename: str,
        *,
        mode: str = "a",
        encoding: str = "utf-8",
        batch_chars: int = 262_144,
        flush_interval_sec: float = 0.25,
    ) -> None:
        super().__init__(level=logging.DEBUG)
        self.baseFilename = os.path.abspath(filename)
        self.mode = mode
        self.encoding = encoding
        self.batch_chars = max(4096, batch_chars)
        self.flush_interval_sec = max(0.05, flush_interval_sec)
        self._stream = None
        self._lines: list[str] = []
        self._pending_chars = 0
        self._last_flush = time.monotonic()
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            line = msg + self.terminator
            with self._lock:
                self._lines.append(line)
                self._pending_chars += len(line)
                now = time.monotonic()
                if self._pending_chars >= self.batch_chars:
                    self._flush_unlocked()
                elif (now - self._last_flush) >= self.flush_interval_sec and self._lines:
                    self._flush_unlocked()
        except Exception:
            self.handleError(record)

    def _flush_unlocked(self) -> None:
        if not self._lines:
            return
        if self._stream is None:
            self._stream = open(self.baseFilename, self.mode, encoding=self.encoding)
        self._stream.write("".join(self._lines))
        self._stream.flush()
        self._lines.clear()
        self._pending_chars = 0
        self._last_flush = time.monotonic()

    def flush(self) -> None:
        with self._lock:
            self._flush_unlocked()
            if self._stream is not None:
                self._stream.flush()

    def close(self) -> None:
        with self._lock:
            self._flush_unlocked()
            if self._stream is not None:
                try:
                    self._stream.close()
                except OSError:
                    pass
                self._stream = None
        super().close()


def install_async_file_sink(
    path: str,
    *,
    fmt: logging.Formatter | None = None,
) -> logging.handlers.QueueHandler:
    """
    安装异步文件下沉：返回应挂到 root（及其它 logger）的 QueueHandler。
    真实写盘 handler 可通过 get_batched_file_handler() 设置第二套 formatter（如 jachin.deep 仅 message）。
    """
    global _listener, _queue_handler, _batched_handler, _log_queue

    if _queue_handler is not None:
        return _queue_handler

    batch_chars = _env_int("JACHIN_L3_LOG_ASYNC_BATCH_CHARS", 262_144)
    flush_sec = _env_float("JACHIN_L3_LOG_ASYNC_FLUSH_SEC", 0.25)

    _log_queue = queue.Queue(-1)
    _batched_handler = BatchedFileHandler(
        path,
        batch_chars=batch_chars,
        flush_interval_sec=flush_sec,
    )
    if fmt is not None:
        _batched_handler.setFormatter(fmt)

    _listener = logging.handlers.QueueListener(
        _log_queue,
        _batched_handler,
        respect_handler_level=True,
    )
    _listener.start()
    _queue_handler = logging.handlers.QueueHandler(_log_queue)
    _queue_handler.setLevel(logging.DEBUG)

    _register_shutdown_once()
    _LOG.debug(
        "[AsyncFileLog] started path=%s batch_chars=%s flush_sec=%s",
        path,
        batch_chars,
        flush_sec,
    )
    return _queue_handler


def get_batched_file_handler() -> "BatchedFileHandler | None":
    return _batched_handler if isinstance(_batched_handler, BatchedFileHandler) else None


def get_queue_handler() -> logging.handlers.QueueHandler | None:
    return _queue_handler


def shutdown_async_file_sink() -> None:
    """排空队列、停止 listener、关闭批量文件句柄。可重复调用。"""
    global _listener, _queue_handler, _batched_handler, _log_queue

    listener = _listener
    if listener is not None:
        try:
            listener.stop()
        except Exception as e:
            _LOG.debug("[AsyncFileLog] listener.stop: %s", e)
        _listener = None

    _queue_handler = None
    _batched_handler = None
    _log_queue = None


__all__ = [
    "async_file_log_enabled",
    "BatchedFileHandler",
    "install_async_file_sink",
    "get_batched_file_handler",
    "get_queue_handler",
    "shutdown_async_file_sink",
]


def _register_shutdown_once() -> None:
    global _shutdown_registered
    if _shutdown_registered:
        return
    _shutdown_registered = True
    atexit.register(shutdown_async_file_sink)
    try:
        from l3_node.graceful_shutdown import register_shutdown_hook

        async def _hook() -> None:
            shutdown_async_file_sink()

        register_shutdown_hook(lambda: _hook())
    except Exception:
        pass
