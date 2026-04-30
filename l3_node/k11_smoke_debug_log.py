"""
K11 冒烟/定时控制台调试日志（落盘，便于打包版 L3 与桌面控制台排障）。

路径：``%USERPROFILE%/.jachin/jachin_debug/冒烟/`` 下按日
``k11_smoke_YYYYMMDD.log``。专记「冒烟 REST / 定时调度 / 到点子进程」；与 l3_debug.log 区分。
"""
from __future__ import annotations

import os
import threading
import time
import traceback
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_lock = threading.RLock()


def k11_smoke_debug_dir() -> Path:
    return Path.home() / ".jachin" / "jachin_debug" / "冒烟"


def _log_path_today() -> Path:
    k11_smoke_debug_dir().mkdir(parents=True, exist_ok=True)
    return k11_smoke_debug_dir() / f"k11_smoke_{time.strftime('%Y%m%d', time.localtime())}.log"


def k11_smoke_debug_line(msg: str, *args: Any) -> None:
    """写一行（UTF-8 追加，线程安全）。"""
    try:
        if args:
            try:
                msg = msg % args
            except Exception:
                msg = f"{msg} {args!r}"
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())} {msg}\n"
        p = _log_path_today()
        with _lock:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.open("a", encoding="utf-8").write(line)
    except Exception:
        pass


_K11_DEBUG_SESSION_LOGGED = False


def k11_smoke_debug_init_once() -> None:
    """进程内首次打点时调用，记 exe/pid/目录。"""
    global _K11_DEBUG_SESSION_LOGGED
    if _K11_DEBUG_SESSION_LOGGED:
        return
    _K11_DEBUG_SESSION_LOGGED = True
    import sys

    k11_smoke_debug_line(
        "=== k11 冒烟调试会话开始 | pid=%s | frozen=%s | executable=%s | JACHIN_APP_ROOT=%s ===",
        str(os.getpid()),
        str(bool(getattr(sys, "frozen", False))),
        os.path.normcase(sys.executable or "?"),
        (os.environ.get("JACHIN_APP_ROOT") or "(unset)")[:200],
    )


def k11_smoke_debug_exc(where: str, exc: BaseException | None = None, extra: str = "") -> None:
    k11_smoke_debug_line("EXCEPTION at %s%s", where, f" | {extra}" if extra else "")
    if exc is None:
        return
    try:
        tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
        for block in tb:
            for s in block.rstrip().splitlines():
                if s:
                    k11_smoke_debug_line("  %s", s)
    except Exception:
        k11_smoke_debug_line("  (traceback 格式化失败) | type=%s msg=%s", type(exc).__name__, exc)


def k11_smoke_debug_mapping(prefix: str, m: Mapping[str, Any] | None) -> None:
    if m is None:
        k11_smoke_debug_line("%s: <empty>", prefix)
        return
    out: list[str] = []
    for k, v in sorted(m.items(), key=lambda x: str(x[0])):
        if any(x in k.lower() for x in ("secret", "key", "token", "password")):
            s = "<redacted>" if v else "∅"
        else:
            s = repr(v)
            if len(s) > 200:
                s = s[:200] + "…"
        out.append(f"{k}={s}")
    k11_smoke_debug_line("%s: %s", prefix, " | ".join(out))
