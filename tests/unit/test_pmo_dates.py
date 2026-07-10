"""PMO 日期：飞书毫秒时间戳须按 Asia/Shanghai 解析，避免 UTC 少一天。"""
from __future__ import annotations

from datetime import datetime

import pytest

from l3_node.tools.pmo_dates import pmo_china_tzinfo, pmo_ms_to_iso_date, pmo_today_iso


def test_ms_midnight_shanghai_not_utc_minus_one_day():
    """2026-06-08 00:00 +08:00 在 UTC 为 2026-06-07 16:00，旧逻辑会错成 6/7。"""
    tz = pmo_china_tzinfo()
    ms = int(datetime(2026, 6, 8, 0, 0, 0, tzinfo=tz).timestamp() * 1000)
    assert pmo_ms_to_iso_date(ms) == "2026-06-08"


def test_ms_jack_looi_review_scenario():
    """Review 6/2、Start 6/8 类字段：当地午夜毫秒须保持日历日。"""
    tz = pmo_china_tzinfo()
    start_ms = int(datetime(2026, 6, 8, 0, 0, 0, tzinfo=tz).timestamp() * 1000)
    review_ms = int(datetime(2026, 6, 2, 0, 0, 0, tzinfo=tz).timestamp() * 1000)
    assert pmo_ms_to_iso_date(start_ms) == "2026-06-08"
    assert pmo_ms_to_iso_date(review_ms) == "2026-06-02"


def test_pmo_reference_date_pin(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JACHIN_PMO_REFERENCE_DATE", "2026-06-09")
    assert pmo_today_iso() == "2026-06-09"
    monkeypatch.delenv("JACHIN_PMO_REFERENCE_DATE", raising=False)
