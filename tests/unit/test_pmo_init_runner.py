"""PMO INIT 确定性路径单元测试。"""
from __future__ import annotations

from unittest.mock import MagicMock

from l3_node.pmo_init_runner import PMO_INIT_WIKI_URLS, format_pmo_init_direct_summary
from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _render_bitable_markdown


def test_pmo_init_wiki_urls_count() -> None:
    assert len(PMO_INIT_WIKI_URLS) == 12


def test_render_bitable_skips_list_tables_when_table_id_known() -> None:
    client = MagicMock()
    client.bitable_list_fields.return_value = [{"field_id": "f1", "field_name": "标题"}]
    client.bitable_list_records.return_value = [
        {"record_id": "r1", "fields": {"f1": "任务A"}},
    ]
    md, export = _render_bitable_markdown(
        client,
        "app_token_xyz",
        "tblABC",
        "测试表",
        50000,
        view_id="vew123",
        emit_hierarchy=False,
    )
    client.bitable_list_tables.assert_not_called()
    assert "tblABC" in md or "测试表" in md
    client.bitable_list_fields.assert_called_once_with("app_token_xyz", "tblABC")
    client.bitable_list_records.assert_called_once_with(
        "app_token_xyz", "tblABC", 50000, view_id="vew123"
    )
    assert export is not None
    assert export.get("view_id") == "vew123"
    assert export["tables"][0]["records"][0]["record_id"] == "r1"


def test_format_pmo_init_direct_summary_ok() -> None:
    text = format_pmo_init_direct_summary(
        {
            "status": "ok",
            "message": "INIT 完成",
            "pull": {"files": ["a.md", "b.md"], "output_dir": "/tmp/pmo_lark_pull"},
            "import": {"total_records": 100, "views": [{"view_id": "vewpI8lyYw"}]},
        }
    )
    assert "拉表: 2 个 md" in text
    assert "total_records=100" in text
    assert "状态: ok" in text
