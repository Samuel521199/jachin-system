"""
L1-L3 控制台输出：统一添加 UTC 时间前缀，便于跨时区排查
"""
from __future__ import annotations

from datetime import datetime, timezone
import sys


def utc_prefix() -> str:
    """返回当前 UTC 时间戳，格式 ISO8601"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def log_utc(msg: str, file=sys.stderr, **kwargs) -> None:
    """带 UTC 前缀的 print"""
    print(f"{utc_prefix()} {msg}", file=file, flush=True, **kwargs)
