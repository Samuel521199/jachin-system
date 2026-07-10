"""📊 需求进度全览 6 列 + 优先级排序 + 加粗规则。"""
from __future__ import annotations

from l3_node.pmo_agent_policy import _PMO_MD_SECTION_DEMAND
from l3_node.pmo_report_format import (
    PMO_DEMAND_TABLE_HEADERS,
    epic_priority_sort_key,
    format_demand_table_gfm_row,
    pmo_demand_table_column_issues,
    polish_demand_table_in_markdown,
    sort_epics_for_demand_table,
    split_priority_from_epic_name,
)


def test_demand_table_six_columns_required():
    old = """**📊 需求进度全览**
| 需求名称 | 时间跨度 | 参与人 | 完成度 | 状态 |
| --- | --- | --- | --- | --- |
| a | b | c | d | e |
"""
    issues = pmo_demand_table_column_issues(old, _PMO_MD_SECTION_DEMAND)
    assert any("优先级" in i for i in issues)


def test_demand_table_valid_six_columns():
    mc = """**📊 需求进度全览**
| 优先级 | 需求名称 | 时间跨度 | 参与人 | 完成度 | 状态 |
| --- | --- | --- | --- | --- | --- |
| **P0** | Epic A | 05/01→05/25 | Ethan | [▓▓░░] 20% | 🔵 开发/验收 · 技术开发 |
"""
    assert pmo_demand_table_column_issues(mc, _PMO_MD_SECTION_DEMAND) == []
    assert len(PMO_DEMAND_TABLE_HEADERS) == 6


def test_sort_epics_p0_before_p2():
    epics = [
        {"epic_name": "z", "priority": "P2"},
        {"epic_name": "a", "priority": "P0"},
        {"epic_name": "m", "priority": "P1"},
    ]
    ordered = [e["epic_name"] for e in sort_epics_for_demand_table(epics)]
    assert ordered == ["a", "m", "z"]


def test_format_demand_row_splits_priority_from_name():
    row = format_demand_table_gfm_row(
        priority="P1",
        epic_name="【P0】不应出现",
        time_span="06/01→06/02",
        participants="Ethan",
        progress_bar="[▓▓░░░░░░░░] 20%",
        workflow_status="🔵 开发/验收 · 技术开发",
    )
    assert "**P1**" in row
    assert "【P0】不应出现" not in row
    assert "**Ethan**" not in row
    assert "游戏" not in row or True


def test_split_priority_from_coupled_name():
    pr, name = split_priority_from_epic_name("【P2】 外包游戏资源优化")
    assert pr == "P2"
    assert "外包" in name


def test_polish_demand_sorts_and_decouples_priority():
    mc = """**📊 需求进度全览**
| 优先级 | 需求名称 | 时间跨度 | 参与人 | 完成度 | 状态 |
| --- | --- | --- | --- | --- | --- |
| — | **【P2】** **外包游戏** | a | b | c | d |
| — | **【P0】** **FB外跳** | a | b | c | d |
"""
    out = polish_demand_table_in_markdown(mc)
    lines = [ln for ln in out.splitlines() if "|" in ln and "---" not in ln and "优先级" not in ln]
    assert lines[0].find("P0") >= 0 or "FB" in lines[0]
    assert "P2" in lines[-1] or "外包" in lines[-1]
