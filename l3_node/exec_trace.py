"""执行里程碑：同步打印到 stderr（PowerShell/终端立即可见），并可选写入 logger。"""
from __future__ import annotations

import logging
import os
import sys


def exec_trace_stderr_enabled() -> bool:
    v = (os.environ.get("JACHIN_EXEC_TRACE_STDERR") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def exec_trace(
    msg: str,
    *args: object,
    logger: logging.Logger | None = None,
    level: int = logging.INFO,
) -> None:
    if args:
        try:
            text = msg % tuple(args)
        except (TypeError, ValueError):
            text = f"{msg} | args={args!r}"
    else:
        text = msg
    _pfx = "[JachinExec]"
    if logger is not None:
        logger.log(level, "%s %s", _pfx, text)
    if exec_trace_stderr_enabled():
        print(f"{_pfx} {text}", file=sys.stderr, flush=True)
