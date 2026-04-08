"""执行里程碑：同步打印到 stderr（PowerShell/终端立即可见），并可选写入 logger。"""
from __future__ import annotations

import logging
import os
import sys


def exec_trace_stderr_enabled() -> bool:
    v = (os.environ.get("JACHIN_EXEC_TRACE_STDERR") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def exec_trace(
    msg_or_logger: str | logging.Logger,
    *args: object,
    logger: logging.Logger | None = None,
    level: int = logging.INFO,
) -> None:
    """
    支持两种写法：
    - exec_trace("fmt %s", x, logger=logger)
    - exec_trace(logger, "fmt %s", x)   # 与全仓库现有调用一致
    """
    if isinstance(msg_or_logger, logging.Logger):
        _log = msg_or_logger
        if not args:
            return
        msg = str(args[0])
        fmt_args = tuple(args[1:])
    else:
        _log = logger
        msg = str(msg_or_logger)
        fmt_args = args
    if fmt_args:
        try:
            text = msg % fmt_args
        except (TypeError, ValueError):
            text = f"{msg} | args={fmt_args!r}"
    else:
        text = msg
    _pfx = "[JachinExec]"
    if _log is not None:
        _log.log(level, "%s %s", _pfx, text)
    if exec_trace_stderr_enabled():
        print(f"{_pfx} {text}", file=sys.stderr, flush=True)
