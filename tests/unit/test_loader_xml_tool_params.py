"""RoleExecutor XML 风格 tool input 与 JSON 互补（util:/sys:）。"""
from __future__ import annotations

from l3_node.primitives.tools import loader


def test_parse_work_order_xml_tool_params_basic() -> None:
    s = """
<parameter=file_path>~/Desktop/AI_OS_白皮书.md</parameter>
<parameter=topic>2026年全球 AI OS 商业化白皮书</parameter>
"""
    d = loader._parse_work_order_xml_tool_params(s)
    assert d["file_path"] == "~/Desktop/AI_OS_白皮书.md"
    assert d["topic"] == "2026年全球 AI OS 商业化白皮书"


def test_parse_work_order_xml_outline_sections_json_array() -> None:
    s = r"""<parameter=outline_sections>["第一章", "第二章"]</parameter>"""
    d = loader._parse_work_order_xml_tool_params(s)
    assert d["outline_sections"] == ["第一章", "第二章"]


def test_merge_xml_fills_empty_topic_from_json() -> None:
    util: dict = {"topic": "", "file_path": "/tmp/x.md"}
    xml = loader._parse_work_order_xml_tool_params(
        "<parameter=topic>补全标题</parameter>",
    )
    loader._merge_xml_params_into_util(util, xml)
    assert util["topic"] == "补全标题"
    assert util["file_path"] == "/tmp/x.md"
