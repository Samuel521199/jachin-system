"""PMO 父记录空链接 SSOT 单元测试。"""
from __future__ import annotations

from l3_node.pmo_parent_record import (
    parent_record_is_empty_link,
    parent_text_from_fields,
    sql_parent_epic_null_clause,
)


def test_parent_record_is_empty_link_variants():
    assert parent_record_is_empty_link(None)
    assert parent_record_is_empty_link("")
    assert parent_record_is_empty_link(
        '{"table_id": "t", "text_arr": [], "type": "text"}'
    )
    assert parent_record_is_empty_link({"text_arr": [], "type": "text"})
    assert not parent_record_is_empty_link("开发")
    assert not parent_record_is_empty_link([{"text": "EpicA"}])


def test_parent_text_from_fields():
    assert parent_text_from_fields({"父记录": "开发"}) == "开发"
    assert parent_text_from_fields(
        {"父记录": '{"text_arr": [], "type": "text"}'}
    ) is None


def test_sql_parent_epic_null_clause_includes_empty_link():
    sql = sql_parent_epic_null_clause("fields")
    assert "text_arr" in sql
    assert "父记录" in sql
