"""PMO 调试日志：function calling 目的推断 + tool_calls Thought 合成."""

import json

from l3_node.llm_client import tool_calls_to_react_text
from l3_node.pmo_copilot_debug_file import infer_tool_purpose_from_input


def test_infer_db_query_personnel_step3() -> None:
    sql = (
        'SELECT json_extract(value, \'$.en_name\') AS person, COUNT(*) AS cnt '
        'FROM pmo_raw_records, json_each(json_extract(fields, \'$."Person in charge/Participant"\')) '
        "WHERE source_view = 'vewCz1FFJi' GROUP BY person"
    )
    purpose = infer_tool_purpose_from_input("core:db_query", json.dumps({"sql": sql}, ensure_ascii=False))
    assert "Step3" in purpose or "人员" in purpose


def test_tool_calls_to_react_uses_sql_inference() -> None:
    sql = "SELECT view_id, record_count FROM pmo_views_meta"
    calls = [{"function": {"name": "core_db_query", "arguments": json.dumps({"sql": sql})}}]
    out = tool_calls_to_react_text(calls, openapi_fname_to_tool_id={"core_db_query": "core:db_query"})
    assert "（API function calling）" not in out
    assert "Thought:" in out
    assert "地图" in out or "pmo_views_meta" in out.lower() or "SQLite" in out


def test_tool_calls_use_assistant_content_thought() -> None:
    calls = [{"function": {"name": "core_db_query", "arguments": json.dumps({"sql": "SELECT 1"})}}]
    out = tool_calls_to_react_text(
        calls,
        openapi_fname_to_tool_id={"core_db_query": "core:db_query"},
        assistant_content="Thought: Step1·先读视图地图确认列名",
    )
    assert "Step1" in out
