"""core:db_query tool input 解析（裸 SQL vs 畸形 JSON 包装）。"""
from __future__ import annotations

from l3_node.tools.pmo_db_tools import parse_db_query_work_order_input, run_db_query


def test_plain_sql_input():
    sql = "SELECT 1 AS x FROM pmo_raw_records LIMIT 1;"
    p = parse_db_query_work_order_input(sql)
    assert p.get("sql") == sql


def test_valid_json_wrapper():
    inp = '{"sql": "SELECT 1 AS x LIMIT 1;"}'
    assert parse_db_query_work_order_input(inp).get("sql") == "SELECT 1 AS x LIMIT 1;"


def test_malformed_json_with_embedded_quotes():
    """模拟 Worker C：JSON 包装 + SQL 内未转义路径引号 → json.loads 失败。"""
    inp = (
        '{"sql": "SELECT json_extract(fields, \'$.Requirement\') AS epic_name, '
        "json_extract(fields, '$.\"任务编号\"') AS task_no "
        "FROM pmo_raw_records WHERE source_view = 'vewpI8lyYw' LIMIT 5;\"}"
    )
    sql = parse_db_query_work_order_input(inp).get("sql") or ""
    assert sql.lower().startswith("select")
    assert "vewpI8lyYw" in sql
    assert "任务编号" in sql


def test_malformed_json_not_missing_sql_on_run():
    inp = (
        '{"sql": "SELECT COUNT(*) AS cnt FROM pmo_raw_records '
        "WHERE source_view = 'vewpI8lyYw';\"}"
    )
    out = run_db_query(sql=parse_db_query_work_order_input(inp).get("sql", ""))
    assert out.get("status") == "ok", out
