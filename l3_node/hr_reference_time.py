"""
HR 透析分析用的参考时间：中国标准时间 Asia/Shanghai（与 UTC 相差 UTC+8）。

注入到 Wasm stdin JSON，供模型判断应届生届别、工龄、空窗期等；JSON 使用 ensure_ascii=False 输出时为 UTF-8 文本。
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, MutableMapping

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[misc, assignment]


def _china_tzinfo():
    if ZoneInfo is not None:
        try:
            return ZoneInfo("Asia/Shanghai")
        except Exception:
            pass
    from datetime import timedelta, timezone

    return timezone(timedelta(hours=8))


def apply_hr_analysis_reference_time(stdin_json: MutableMapping[str, Any]) -> None:
    """
    写入 reference_date、reference_datetime_iso、reference_timezone、reference_timezone_note。
    测试可设环境变量 JACHIN_HR_REFERENCE_DATE=YYYY-MM-DD 固定「当前」日历日（仍带 +08:00 正午 ISO）。
    """
    tz = _china_tzinfo()
    pin = (os.environ.get("JACHIN_HR_REFERENCE_DATE") or "").strip()
    now: datetime
    if pin:
        try:
            raw = pin.replace("/", "-")
            y, m, d = (int(x) for x in raw.split("-")[:3])
            now = datetime(y, m, d, 12, 0, 0, tzinfo=tz)
        except Exception:
            now = datetime.now(tz)
    else:
        now = datetime.now(tz)
    stdin_json["reference_date"] = now.strftime("%Y-%m-%d")
    stdin_json["reference_datetime_iso"] = now.isoformat(timespec="seconds")
    stdin_json["reference_timezone"] = "Asia/Shanghai"
    stdin_json["reference_timezone_note"] = (
        "中国标准时间（Asia/Shanghai，UTC+8）。"
        "凡涉及「当前日期」「至今」「工作年限」「应届生/毕业届别」「证书是否在有效期内」等，均以上述时刻为准；"
        "不得使用模型训练截止日期、UTC「现在」或本机非中国时区臆测。"
    )
