"""resolve_release_window 口径：since = 最近一封发版公告（非第二新）。"""
from __future__ import annotations

from datetime import datetime, timezone

from l3_node.tools.pmo_release_epic_mapping import resolve_release_window


def _mail(mid: str, maint: str, internal: datetime) -> dict:
    return {
        "message_id": mid,
        "subject": "生产环境维护公告",
        "maintenance_date": maint,
        "internal_dt": internal,
        "internal_date": str(int(internal.timestamp() * 1000)),
    }


def test_resolve_release_window_uses_latest_mail_not_previous():
    june5 = _mail("m1", "2026-06-05", datetime(2026, 6, 4, 10, 5, tzinfo=timezone.utc))
    may22 = _mail("m2", "2026-05-22", datetime(2026, 5, 21, 13, 51, tzinfo=timezone.utc))
    now = datetime(2026, 6, 8, 2, 0, tzinfo=timezone.utc)
    win = resolve_release_window([june5, may22], now=now)
    assert win["ok"] is True
    assert win["since"] == june5["internal_dt"]
    assert win["since_mail"]["maintenance_date"] == "2026-06-05"
    assert win["latest_mail"]["maintenance_date"] == "2026-06-05"


def test_resolve_release_window_single_mail():
    only = _mail("m1", "2026-06-05", datetime(2026, 6, 4, 10, 5, tzinfo=timezone.utc))
    win = resolve_release_window([only])
    assert win["since_mail"]["message_id"] == "m1"
