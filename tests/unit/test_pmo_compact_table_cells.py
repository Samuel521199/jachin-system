"""PMO 战报紧凑单元格 SSOT。"""
from __future__ import annotations

from l3_node.channels.lark.md_native_table_card import _table_element
from l3_node.pmo_report_format import (
    format_compact_completion_cell,
    format_compact_time_span_cell,
    format_personnel_matrix_tasks_cell_compact,
    format_workflow_status_cell,
    polish_demand_table_in_markdown,
)


def test_compact_time_span_strips_year():
    assert format_compact_time_span_cell("2026/06/01→2026/06/07") == "06/01→06/07"


def test_compact_completion_five_blocks():
    assert format_compact_completion_cell("[▓▓▓▓▓░░░░░] 51%") == "▓▓▓░░ 51%"


def test_workflow_status_keeps_parenthetical():
    s = "🔵 立项/评审 · 需求评审（美术 1/1 · 产品 3/4）"
    out = format_workflow_status_cell(s)
    assert "（" in out
    assert "美术 1/1" in out
    assert "需求评审" in out


def test_personnel_tasks_compact_no_br():
    cell = "【P0】A · 开发<br>【P1】B · 完成<br>【P2】C · 待开始"
    out = format_personnel_matrix_tasks_cell_compact(cell)
    assert "<br>" not in out.lower()
    assert " · " in out


def test_polish_demand_compact_time():
    mc = """**📊 需求进度全览**
| 优先级 | 需求名称 | 时间跨度 | 参与人 | 完成度 | 状态 |
| --- | --- | --- | --- | --- | --- |
| **P0** | FB外跳 | 2026/06/01→2026/06/07 | Gavin | [▓▓▓░░░░░░░] 51% | 🔵 开发/验收 · 技术开发（技术 0/1） |
"""
    out = polish_demand_table_in_markdown(mc)
    assert "2026/" not in out
    assert "06/01→06/07" in out
    assert "（技术" in out


def test_table_element_row_height_auto():
    matrix = [
        ["优先级", "需求名称", "时间跨度", "参与人", "完成度", "状态"],
        ["P0", "FB外跳", "06/01→06/02", "Gavin", "▓▓▓░░ 51%", "🔵 开发中"],
    ]
    el = _table_element(matrix, element_id="t0")
    assert el["row_height"] == "auto"


def test_table_element_personnel_row_height_auto_with_multiline_tasks():
    matrix = [
        ["人员", "负责需求（含优先级）", "状态预警"],
        ["Gavin", "【P0】A · 开发<br>【P1】B · 完成", "🚨 延期"],
    ]
    el = _table_element(matrix, element_id="t1")
    assert el["row_height"] == "auto"
    task_col = el["columns"][1]
    assert task_col["data_type"] == "lark_md"
    row_val = list(el["rows"][0].values())[1]
    assert "\n" in row_val
    assert "【P0】A" in row_val and "【P1】B" in row_val


def test_personnel_tasks_cell_full_no_etc():
    from l3_node.pmo_report_format import format_personnel_matrix_tasks_cell

    tasks = [{"priority": f"P{i}", "task": f"任务{i}", "status": "开发中"} for i in range(5)]
    cell = format_personnel_matrix_tasks_cell(tasks, compact_for_feishu=False)
    assert "等" not in cell or "等5项" not in cell
    assert cell.count("<br>") == 4
    assert "任务4" in cell


def test_personnel_compact_matrix_preserves_all_tasks():
    from l3_node.pmo_report_format import compact_pmo_table_matrix_for_native_table

    matrix = [
        ["人员", "负责需求（含优先级）", "状态预警"],
        ["Akie", "【P1】任务A · 开发<br>【P1】任务B · 完成", "🚨 延期"],
    ]
    out = compact_pmo_table_matrix_for_native_table(matrix)
    task = out[1][1]
    assert "等" not in task
    assert "<br>" in task.lower()
    assert "任务A" in task and "任务B" in task
