"""PMO bitable watch：diff 与防抖状态机（无网络）。"""
from __future__ import annotations

from l3_node.tools.pmo_bitable_watch import (
    diff_record_maps,
    format_change_summary_markdown,
    run_change_diff,
    _merge_session_events,
)


def test_diff_created_updated_deleted() -> None:
    before = {
        "r1": {"Requirement": "任务A", "Sprint": "S1"},
        "r2": {"Requirement": "待删"},
    }
    after = {
        "r1": {"Requirement": "任务A", "Sprint": "S2"},
        "r3": {"Requirement": "新任务"},
    }
    events = diff_record_maps(before, after, view_id="vewpI8lyYw", table_id="tblX")
    types = {e["change_type"] for e in events}
    assert types == {"updated", "deleted", "created"}
    out = run_change_diff(before_records=before, after_records=after)
    assert out["summary"] == {"created": 1, "updated": 1, "deleted": 1}


def test_merge_session_events_same_record() -> None:
    e1 = {
        "record_id": "r1",
        "change_type": "updated",
        "changed_fields": {"Sprint": {"before": "S1", "after": "S2"}},
        "after": {"Sprint": "S2"},
        "label": "A",
    }
    e2 = {
        "record_id": "r1",
        "change_type": "updated",
        "changed_fields": {"priority": {"before": "P2", "after": "P0"}},
        "after": {"Sprint": "S2", "priority": "P0"},
        "label": "A",
    }
    merged = _merge_session_events([e1], [e2])
    assert len(merged) == 1
    assert "Sprint" in merged[0]["changed_fields"]
    assert "priority" in merged[0]["changed_fields"]
    assert merged[0]["changed_fields"]["Sprint"]["before"] == "S1"


def test_format_change_summary_markdown() -> None:
    md = format_change_summary_markdown(
        [
            {
                "change_type": "created",
                "record_id": "r9",
                "label": "测试需求",
                "changed_fields": {"Requirement": {"before": "", "after": "测试需求"}},
            }
        ],
        table_id="tblfK9gk6vTQpJtB",
        view_id="vewpI8lyYw",
    )
    assert "多维表变更回调" in md
    assert "tblfK9gk6vTQpJtB" in md
    assert "新增" in md
