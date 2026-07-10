"""👥 人员矩阵「负责需求」列排版 SSOT。"""
from __future__ import annotations

from l3_node.pmo_report_format import (
    format_personnel_matrix_tasks_cell,
    normalize_personnel_task_cell_text,
    polish_personnel_matrix_in_markdown,
)


def test_format_tasks_cell_uses_br_not_semicolon_or_bold():
    cell = format_personnel_matrix_tasks_cell(
        [
            {
                "priority": "P1",
                "task": "在线奖励-弹窗 UI",
                "progress": "开发中",
            },
            {
                "priority": "P0",
                "task": "Laro GO 游戏加载优化-进度条",
                "status": "🔵 按时完成",
            },
        ]
    )
    assert "**" not in cell
    assert "；" not in cell
    assert "<br>" in cell
    assert "【P1】" in cell
    assert "开发中" in cell


def test_format_tasks_cell_no_etc_when_many_tasks():
    tasks = [
        {"priority": "P0", "task": f"任务-{i}", "status": "开发中"}
        for i in range(6)
    ]
    cell = format_personnel_matrix_tasks_cell(tasks)
    assert "等6项" not in cell
    assert "等" not in cell.split("项")[0][-3:] if "项" in cell else True
    assert cell.count("<br>") == 5


def test_normalize_archived_semicolon_cell_multiline():
    raw = (
        "【P1】**在线奖励-弹窗** · 开发中；"
        "【P0】**Laro GO** · 🔵 按时完成"
    )
    out = normalize_personnel_task_cell_text(raw)
    assert "**" not in out
    assert "<br>" in out.lower()
    assert "；" not in out
    assert "等" not in out


def test_polish_personnel_matrix_normalizes_task_column():
    mc = """**👥 人员任务矩阵**
| 人员 | 负责需求（含优先级） | 状态预警 |
| --- | --- | --- |
| **Baojing** | 【P1】**任务A** · 开发中；【P0】**任务B** · 完成 | ✅ 正常（本周计划 2/完成 1） |
"""
    out = polish_personnel_matrix_in_markdown(mc, sort_rows=False)
    row = [ln for ln in out.splitlines() if "Baojing" in ln][0]
    task_cell = row.split("|")[2] if row.count("|") >= 2 else row
    assert "**" not in task_cell
    assert "<br>" in task_cell.lower()
    assert "；" not in task_cell
    assert "等" not in task_cell
