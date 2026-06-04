"""飞书 PMO native_table 列宽 SSOT。"""
from __future__ import annotations

from l3_node.channels.lark.md_native_table_card import _table_element
from l3_node.pmo_report_format import (
    PMO_DEMAND_TABLE_COLUMN_WIDTHS_PCT,
    detect_pmo_native_table_profile,
    format_priority_cell,
    pmo_native_table_column_widths,
    polish_demand_table_in_markdown,
)


def test_detect_demand_profile():
    assert (
        detect_pmo_native_table_profile(
            ["优先级", "需求名称", "时间跨度", "参与人", "完成度", "状态"]
        )
        == "demand"
    )


def test_demand_priority_column_not_widest():
    widths = pmo_native_table_column_widths("demand", 6)
    assert widths[0] == PMO_DEMAND_TABLE_COLUMN_WIDTHS_PCT[0]
    assert int(widths[0].rstrip("%")) < int(widths[-1].rstrip("%"))


def test_table_element_applies_pmo_widths():
    matrix = [
        ["优先级", "需求名称", "时间跨度", "参与人", "完成度", "状态"],
        ["**P0**", "FB外跳", "06/01→06/02", "Gavin", "[▓▓░░] 51%", "🔵 开发中"],
    ]
    el = _table_element(matrix, element_id="t0")
    assert el["columns"][0]["width"] == "10%"
    assert el.get("row_height") == "low"


def test_format_priority_cell_compact():
    assert format_priority_cell("P0") == "**P0**"
    assert "【" not in format_priority_cell("P1")


def test_polish_upgrades_five_column_demand_table():
    mc = """**📊 需求进度全览**
| 需求名称 | 时间跨度 | 参与人 | 完成度 | 状态 |
| --- | --- | --- | --- | --- |
| **【P0】** FB外跳 | 06/01→06/02 | Gavin | [▓▓░░] 51% | 🔵 开发中 |
"""
    out = polish_demand_table_in_markdown(mc)
    assert "| 优先级 | 需求名称 |" in out
    assert "P0" in out
    assert "**【P0】**" not in out
    assert "FB外跳" in out
