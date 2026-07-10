"""
PMO 日历日 SSOT：飞书多维表毫秒时间戳与「今天」均按 Asia/Shanghai（UTC+8）。

飞书日期字段通常为当地日历日 00:00 的毫秒时间戳；若按 UTC 格式化会整体少一天（如 6/8→6/7）。
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[misc, assignment]


def pmo_china_tzinfo():
    if ZoneInfo is not None:
        try:
            return ZoneInfo("Asia/Shanghai")
        except Exception:
            pass
    return timezone(timedelta(hours=8))


def pmo_now_china() -> datetime:
    """当前时刻（北京时间）；测试可设 ``JACHIN_PMO_REFERENCE_DATE=YYYY-MM-DD`` 固定日历日。"""
    tz = pmo_china_tzinfo()
    pin = (os.environ.get("JACHIN_PMO_REFERENCE_DATE") or "").strip()
    if pin:
        try:
            raw = pin.replace("/", "-")
            y, m, d = (int(x) for x in raw.split("-")[:3])
            return datetime(y, m, d, 12, 0, 0, tzinfo=tz)
        except (TypeError, ValueError):
            pass
    return datetime.now(tz)


def pmo_today_date() -> date:
    return pmo_now_china().date()


def pmo_today_iso() -> str:
    return pmo_today_date().isoformat()


def pmo_ms_to_iso_date(v: Any) -> str | None:
    """飞书/Lark 毫秒时间戳或 ISO 字符串 → ``YYYY-MM-DD``（北京时间日历日）。"""
    if v is None or v == "":
        return None
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        if re.match(r"^\d{4}-\d{2}-\d{2}", s):
            return s[:10]
        if re.match(r"^\d{4}/\d{2}/\d{2}", s):
            return s[:10].replace("/", "-")
    try:
        ts = int(v)
        if abs(ts) < 1e11:
            ts *= 1000
        dt = datetime.fromtimestamp(ts / 1000, tz=pmo_china_tzinfo())
        return dt.strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError, OverflowError):
        if isinstance(v, str) and v.strip():
            return v.strip()[:10]
        return None
