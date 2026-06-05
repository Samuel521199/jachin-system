"""PMO 变更预警三轴分析（无网络 / 可无镜像库）。"""
from __future__ import annotations

from l3_node.tools.pmo_change_alert import (
    _analyze_schedule,
    _extract_semantic_fields,
    _parse_assignees,
    _route_axes,
    _should_push,
    analyze_change_events,
    format_change_alert_markdown,
    format_change_alert_narrative_markdown,
    human_change_alert_title,
)


def test_parse_assignees_team_skipped() -> None:
    persons, warnings = _parse_assignees("游戏组")
    assert persons == []
    assert "assignee_is_team" in warnings


def test_schedule_zero_buffer() -> None:
    sem = {
        "start_date": "2026-06-05",
        "expected_due": "2026-06-05",
        "acceptable_due": "2026-06-06",
        "sprint": "2026/06/01-Sprint",
    }
    out = _analyze_schedule(
        sem,
        current_sprint="2026/06/01-Sprint",
        today="2026-06-05",
        mirror_in_mirror=False,
    )
    assert "zero_buffer" in out["risks"]
    assert out["verdict"] == "warning"


def test_incomplete_change_no_assignee() -> None:
    evt = {
        "change_type": "created",
        "label": "麻将大厅重做",
        "after": {"Requirement": "麻将大厅重做", "Sprint": "2026/06/01-Sprint"},
        "changed_fields": {},
    }
    sem = _extract_semantic_fields(evt["after"])
    persons, w = _parse_assignees(sem.get("assignee_raw") or "")
    route = _route_axes(sem, persons, w)
    assert route["personnel"] is False
    assert route["schedule"] is True

    fact = analyze_change_events(
        [evt],
        personnel_seed={
            "current_sprint": "2026/06/01-Sprint",
            "personnel_tasks": [],
            "_host_bootstrap": ["test"],
        },
        today="2026-06-05",
    )
    assert fact["should_push"] is True
    assert fact["change_alert_result"] == "alert_sent"
    pers = fact["analyzed_events"][0]["personnel_axis"]
    assert pers["status"] == "skipped"
    md = format_change_alert_markdown(fact)
    assert pers.get("message") or "assignee" in str(pers).lower() or len(md) > 50


def test_mahjong_demo_personnel_overdue() -> None:
    evt = {
        "change_type": "created",
        "label": "麻将花色增加开发",
        "after": {
            "Requirement": "麻将花色增加开发",
            "Person in charge/Participant": "Gavin",
            "Sprint": "2026/06/01-Sprint",
            "Start Date": "2026-06-05",
            "Expected Delivery Date": "2026-06-05",
        },
        "changed_fields": {},
    }
    seed = {
        "current_sprint": "2026/06/01-Sprint",
        "personnel_tasks": [
            {
                "person": "Gavin",
                "task": "FB外跳-程序开发",
                "sprint": "2026/06/01-Sprint",
                "is_current_week": True,
                "expected_delivery_date_iso": "2026-06-02",
                "progress": "",
                "status_text": "",
            }
        ],
    }
    fact = analyze_change_events([evt], personnel_seed=seed, today="2026-06-05")
    assert fact["should_push"] is True
    gavin = fact["analyzed_events"][0]["personnel_axis"]["people"][0]
    assert gavin["symbol"] == "🚨"


def test_narrative_markdown_no_technical_codes() -> None:
    evt = {
        "change_type": "created",
        "label": "麻将花色增加开发",
        "after": {
            "Requirement": "麻将花色增加开发",
            "Person in charge/Participant": "Gavin",
            "Sprint": "2026/06/01-Sprint",
            "Start Date": "2026-06-05",
            "Expected Delivery Date": "2026-06-05",
        },
        "changed_fields": {},
    }
    seed = {
        "current_sprint": "2026/06/01-Sprint",
        "personnel_tasks": [
            {
                "person": "Gavin",
                "task": "FB外跳-程序开发",
                "sprint": "2026/06/01-Sprint",
                "is_current_week": True,
                "expected_delivery_date_iso": "2026-06-02",
            }
        ],
    }
    fact = analyze_change_events([evt], personnel_seed=seed, today="2026-06-05")
    md = format_change_alert_narrative_markdown(fact)
    assert "zero_buffer" not in md
    assert "mid_sprint_change" not in md
    assert "Gavin" in md
    assert "FB外跳" in md
    assert "| :--- |" not in md
    title = human_change_alert_title(fact)
    assert "Gavin" in title
    assert "麻将" in title


def test_should_push_all_clear() -> None:
    schedule = {"verdict": "ok", "risks": []}
    personnel = {"verdict": "ok", "people": []}
    project = {"verdict": "ok", "risks": []}
    assert _should_push(schedule, personnel, project, severity_score=10) is False
