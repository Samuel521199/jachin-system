"""core:pmo_personnel_report 与 resolve_current_sprint 单元测试。"""
from __future__ import annotations

from l3_node.tools.pmo_personnel_query import (
    _build_personnel_buckets,
    person_keys_from_task,
    resolve_current_sprint,
)


def test_resolve_current_sprint_skips_future_sprint():
    rows = [
        {"sprint": "2026/06/08-Sprint", "sprint_date": "2026-06-08", "cnt": 6},
        {"sprint": "2026/06/01-Sprint", "sprint_date": "2026-06-01", "cnt": 50},
        {"sprint": "2026/05/25-Sprint", "sprint_date": "2026-05-25", "cnt": 40},
    ]
    cs, sd, meta = resolve_current_sprint(rows, today="2026-06-04")
    assert cs == "2026/06/01-Sprint"
    assert sd == "2026-06-01"
    assert meta["eligible_count"] == 2


def test_person_keys_from_task_splits_multi_owner():
    assert person_keys_from_task({"persons": ["Jack Looi", "Baojing"]}) == [
        "Jack Looi",
        "Baojing",
    ]
    assert person_keys_from_task({"person": "Jack Looi; Baojing"}) == [
        "Jack Looi",
        "Baojing",
    ]
    assert person_keys_from_task({"person": "Buck"}) == ["Buck"]


def test_by_person_no_composite_person_key():
    merged = {
        ("T1", "2026/06/01-Sprint", "协作任务"): {
            "task_no": "T1",
            "sprint": "2026/06/01-Sprint",
            "task": "协作任务",
            "person": "Jack Looi; Baojing",
            "persons": ["Jack Looi", "Baojing"],
            "is_current_week": True,
        },
        ("T2", "2026/06/01-Sprint", "单人"): {
            "task_no": "T2",
            "sprint": "2026/06/01-Sprint",
            "task": "单人",
            "person": "Buck",
            "persons": ["Buck"],
            "is_current_week": True,
        },
    }
    _, _, _, _, by_person = _build_personnel_buckets(merged)
    assert "Jack Looi; Baojing" not in by_person
    assert len(by_person["Jack Looi"]) == 1
    assert len(by_person["Baojing"]) == 1
    assert by_person["Jack Looi"][0]["task"] == "协作任务"
    assert len(by_person) == 3


def test_resolve_current_sprint_all_future_returns_none():
    rows = [
        {"sprint": "2026/06/08-Sprint", "sprint_date": "2026-06-08", "cnt": 6},
    ]
    cs, sd, meta = resolve_current_sprint(rows, today="2026-06-04")
    assert cs is None
    assert sd is None
    assert meta.get("_current_sprint_fallback") == "all_future_in_window"
