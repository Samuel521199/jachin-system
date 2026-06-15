"""战报 current_sprint：开发表 SSOT，人员看板滞后时不覆盖。"""
from __future__ import annotations

from l3_node.tools import pmo_sprint_query as sq


def test_resolve_war_report_prefers_dev_view_over_stale_personnel_board():
    worker_b = {
        "current_sprint": "2026/06/08-Sprint",
        "current_sprint_date": "2026-06-08",
        "recent_sprints": [{"sprint": "2026/06/08-Sprint", "sprint_date": "2026-06-08", "cnt": 124}],
    }
    worker_c = {
        "current_sprint": "2026/06/15-Sprint",
        "current_sprint_date": "2026-06-15",
        "recent_sprints": [
            {"sprint": "2026/06/15-Sprint", "sprint_date": "2026-06-15", "cnt": 40},
            {"sprint": "2026/06/08-Sprint", "sprint_date": "2026-06-08", "cnt": 100},
        ],
    }
    cs, cs_date, meta = sq.resolve_war_report_current_sprint(
        worker_b,
        worker_c,
        today="2026-06-15",
        refresh_from_db=False,
    )
    assert cs == "2026/06/15-Sprint"
    assert cs_date == "2026-06-15"
    assert meta.get("personnel_board_sprint") == "2026/06/08-Sprint"
    assert meta.get("resolved_from") == "worker_c_recent_sprints"


def test_apply_war_report_aligns_b_and_c():
    worker_b = {"current_sprint": "2026/06/08-Sprint"}
    worker_c = {
        "current_sprint": "2026/06/15-Sprint",
        "current_sprint_date": "2026-06-15",
        "recent_sprints": [{"sprint": "2026/06/15-Sprint", "sprint_date": "2026-06-15", "cnt": 1}],
    }
    cs, _ = sq.apply_war_report_current_sprint(
        worker_b,
        worker_c,
        today="2026-06-15",
        refresh_from_db=False,
    )
    assert cs == "2026/06/15-Sprint"
    assert worker_b["current_sprint"] == "2026/06/15-Sprint"
    assert worker_c["current_sprint"] == "2026/06/15-Sprint"


def test_resolve_war_report_db_refresh_uses_list_recent_sprints(monkeypatch):
    worker_b = {"current_sprint": "2026/06/08-Sprint"}
    worker_c = {}
    monkeypatch.setattr(sq, "pmo_mirror_db_ready", lambda: True)
    monkeypatch.setattr(
        sq,
        "list_recent_sprints",
        lambda **_: [
            {"sprint": "2026/06/15-Sprint", "sprint_date": "2026-06-15", "cnt": 38},
            {"sprint": "2026/06/08-Sprint", "sprint_date": "2026-06-08", "cnt": 124},
        ],
    )
    cs, cs_date, meta = sq.resolve_war_report_current_sprint(
        worker_b,
        worker_c,
        today="2026-06-15",
        refresh_from_db=True,
    )
    assert cs == "2026/06/15-Sprint"
    assert cs_date == "2026-06-15"
    assert meta.get("resolved_from") == "dev_view_db_c1"
