"""👥 人员任务矩阵预警行序 SSOT。"""
from __future__ import annotations

from datetime import date

from l3_node.pmo_report_format import (
    build_person_rhythm_alert,
    classify_personnel_alert,
    personnel_matrix_entries_sorted,
    personnel_matrix_sort_key,
    reorder_personnel_matrix_in_markdown,
)


def test_classify_personnel_alert_buckets():
    assert classify_personnel_alert("🚨 进度落后（时间已过约 80%）") == "behind"
    assert classify_personnel_alert("🚨 延期 2 项") == "overdue"
    assert classify_personnel_alert("🟡 偏闲（本周计划 2/完成 2）") == "idle"
    assert classify_personnel_alert("✅ 正常（本周计划 4/完成 3）") == "normal"


def test_personnel_matrix_sort_key_overdue_before_behind():
    overdue = personnel_matrix_sort_key(
        person="Makoto", alert_text="🚨 延期 1 项（本周计划 3/完成 1）"
    )
    behind = personnel_matrix_sort_key(
        person="Gavin", alert_text="🚨 进度落后（时间已过约 80%，完成 0%）"
    )
    assert overdue < behind


def test_personnel_matrix_sort_key_behind_before_normal():
    behind = personnel_matrix_sort_key(
        person="Gavin", alert_text="🚨 进度落后（时间已过约 80%，完成 0%）"
    )
    normal = personnel_matrix_sort_key(
        person="Baojing", alert_text="✅ 正常（本周计划 4/完成 3）"
    )
    assert behind < normal


def test_reorder_personnel_matrix_in_markdown():
    mc = """**👥 人员任务矩阵**
| 人员 | 负责需求（含优先级） | 状态预警 |
| --- | --- | --- |
| **Baojing** | 【P1】任务 B | ✅ 正常（本周计划 4/完成 3） |
| **Makoto** | 任务 X | 🚨 延期 1 项（本周计划 3/完成 1） |
| **Gavin** | 【P0】任务 A | 🚨 进度落后（时间已过约 80%，完成 0%） |
| **Jack Looi** | 【P1】任务 D | 🚨 进度落后（时间已过约 70%，完成 10%） |

**📦 版本发布需求映射**
| v | n |
| --- | --- |
| x | y |
"""
    out = reorder_personnel_matrix_in_markdown(mc)
    makoto_pos = out.find("Makoto")
    gavin_pos = out.find("Gavin")
    jack_pos = out.find("Jack Looi")
    baojing_pos = out.find("Baojing")
    assert 0 <= makoto_pos < gavin_pos <= jack_pos < baojing_pos
    assert out.find("**📦") > baojing_pos


def test_personnel_matrix_entries_sorted_from_by_person():
    by_person = {
        "Baojing": [
            {
                "is_current_week": True,
                "sprint": "2026/06/01-Sprint",
                "start_date_iso": "2026-06-01",
                "status": "🟢 提前完成",
                "progress": "完成",
            }
        ],
        "Gavin": [
            {
                "is_current_week": True,
                "sprint": "2026/06/01-Sprint",
                "start_date_iso": "2026-06-01",
                "status": "🟡 待开始",
            },
        ],
    }
    today = date(2026, 6, 5)
    entries = personnel_matrix_entries_sorted(
        by_person, current_sprint="2026/06/01-Sprint", today=today
    )
    names = [e[0] for e in entries]
    assert names.index("Gavin") < names.index("Baojing")
    assert "进度落后" in entries[0][2] or "🚨" in entries[0][2]
    assert "偏闲" in build_person_rhythm_alert(by_person["Baojing"], today=today)
