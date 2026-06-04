"""pmo_personnel_format 可读输出（GFM 表 + 【姓名】）。"""
from __future__ import annotations

from l3_node.tools.pmo_personnel_format import format_personnel_report_text


def test_format_personnel_gfm_no_hash_headings():
    payload = {
        "current_sprint": "2026/06/01-Sprint",
        "current_sprint_date": "2026-06-01",
        "summary": {
            "person_count": 1,
            "current_week_task_count": 2,
            "unassigned_count": 0,
        },
        "by_person": {
            "Buck": [
                {
                    "task": "游戏加载-新版Tongits",
                    "task_no": "K11-03083",
                    "priority": "P0",
                    "sprint": "2026/06/01-Sprint",
                    "is_current_week": True,
                    "department": "开发",
                    "start_date_iso": "2026-06-01",
                    "progress": "提交测试环境",
                },
                {
                    "task": "游戏BUG-Pusoy",
                    "task_no": "K11-03126",
                    "priority": "P2",
                    "sprint": "2026/06/01-Sprint",
                    "is_current_week": True,
                    "department": "开发",
                    "status_text": "🔵 按时完成",
                },
            ],
        },
    }
    text = format_personnel_report_text(payload)
    assert "####" not in text
    assert "【Buck】" in text
    assert "| 序 |" in text
    assert "| 1 |" in text or "| 1 | 游戏加载" in text
    assert "K11-03083" in text
    assert "| --- |" in text
