"""
Lark 机器人交互调试日志：按「本地时钟整点小时」分文件，写入
``~/.jachin/jachin_debug/健康skill/lark_interaction_YYYYMMDD_HH.log``。

关闭：环境变量 ``JACHIN_LARK_INTERACTION_DEBUG=0``（或 false/no/off）。
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_DEBUG_SUBDIR = "健康skill"
_FILE_PREFIX = "lark_interaction"
_MAX_BODY = 16000


def _is_enabled() -> bool:
    v = (os.environ.get("JACHIN_LARK_INTERACTION_DEBUG") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _jachin_home() -> Path:
    return Path(os.environ.get("JACHIN_HOME", str(Path.home() / ".jachin"))).expanduser().resolve()


def log_dir() -> Path:
    return _jachin_home() / "jachin_debug" / _DEBUG_SUBDIR


def _hour_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H")


def _truncate(s: str, max_len: int = _MAX_BODY) -> str:
    t = (s or "").strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 32] + "\n… [truncated, total_len=" + str(len(t)) + "] …\n"


def append_lark_interaction_record(
    event: str,
    *,
    chat_id: str = "",
    user_id: str = "",
    user_text: str = "",
    reply: str = "",
    route: str = "",
    status: str = "",
    error: str = "",
    error_trace: str = "",
    send_ok: bool | None = None,
    extra: str = "",
) -> None:
    """
    追加一条可读记录到当前小时的日志文件（线程安全）。
    """
    if not _is_enabled():
        return
    try:
        d = log_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{_FILE_PREFIX}_{_hour_stamp()}.log"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines: list[str] = [
            "",
            "=" * 72,
            f"[{ts}] event={event}",
        ]
        if chat_id:
            lines.append(f"chat_id={chat_id}")
        if user_id:
            lines.append(f"user_id={user_id}")
        if route:
            lines.append(f"route={route}")
        if status:
            lines.append(f"status={status}")
        if send_ok is not None:
            lines.append(f"send_ok={send_ok}")
        if user_text:
            lines.append("--- user_text ---")
            lines.append(_truncate(user_text))
        if reply:
            lines.append("--- assistant_reply ---")
            lines.append(_truncate(reply))
        if error:
            lines.append("--- error ---")
            lines.append(_truncate(error, 8000))
        if error_trace:
            lines.append("--- traceback ---")
            lines.append(_truncate(error_trace, 12000))
        if extra:
            lines.append("--- extra ---")
            lines.append(_truncate(extra, 4000))
        lines.append("")
        block = "\n".join(lines)
        with _lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(block)
    except Exception as e:
        logger.debug("[LarkInteractionLog] 写入失败: %s", e, exc_info=True)


def append_from_callback_exception(exc: BaseException, *, where: str, **ctx: Any) -> None:
    """回调链路上的未捕获异常，便于与长连接线程对照。"""
    import traceback

    append_lark_interaction_record(
        "callback_exception",
        route=where,
        status="error",
        error=str(exc),
        error_trace=traceback.format_exc(),
        extra=str(ctx) if ctx else "",
    )
