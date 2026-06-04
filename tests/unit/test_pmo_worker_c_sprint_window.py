"""Worker C C-1 Sprint 时间窗 SQL（按日期，非 row_index）。"""
from __future__ import annotations

import os
import re
from pathlib import Path

from l3_node.pmo_multi_agent_queries import WORKER_C_TASK
from l3_node.tools.pmo_db_tools import _db_query_hints, run_db_query


def _extract_c1_sql() -> str:
    m = re.search(
        r"\*\*C-1 ·[\s\S]*?\n(SELECT[\s\S]*?LIMIT 3;)",
        WORKER_C_TASK,
    )
    assert m, "C-1 SQL block not found"
    return m.group(1)


def test_c1_sql_uses_sprint_date_not_latest_row():
    c1 = _extract_c1_sql()
    assert "sprint_date" in c1
    assert "-21 days" in c1
    assert "latest_row" not in c1


def test_c1_sql_uses_replace_for_sprint_date():
    c1 = _extract_c1_sql()
    assert (
        "replace(substr(json_extract(fields, '$.Sprint'), 1, 10), '/', '-')"
        in c1
    )


def test_c1_sql_runs_on_local_db_if_present():
    db = Path(os.environ.get("JACHIN_PMO_DB_PATH", Path.home() / ".jachin/workspace/pmo_db.sqlite"))
    if not db.exists():
        return
    out = run_db_query(sql=_extract_c1_sql(), max_rows=10)
    assert out.get("status") == "ok", out
    assert (out.get("row_count") or 0) > 0, (
        "C-1 应返回近三周 Sprint；若仍为 0 请检查镜像是否含 2026 Sprint 或 -21 days 窗口"
    )
    for row in out.get("rows") or []:
        sd = str(row.get("sprint_date") or "")
        assert sd, f"sprint_date 不应为空: {row}"


def test_c1_bad_sql_blocked_or_hinted():
    from l3_node.tools.pmo_db_tools import (
        pmo_sql_has_sprint_date_without_replace,
        run_db_query,
    )

    bad = (
        "SELECT json_extract(fields, '$.Sprint') AS sprint,\n"
        "       date(substr(json_extract(fields, '$.Sprint'), 1, 10)) AS sprint_date,\n"
        "       COUNT(*) AS cnt\n"
        "FROM pmo_raw_records WHERE source_view = 'vewpI8lyYw'\n"
        "GROUP BY json_extract(fields, '$.Sprint')\n"
        "HAVING sprint_date IS NOT NULL AND sprint_date >= date('now', '-21 days')\n"
        "ORDER BY sprint_date DESC LIMIT 3;"
    )
    assert pmo_sql_has_sprint_date_without_replace(bad)
    out = run_db_query(sql=bad)
    assert out.get("error") == "pmo_sql_antipattern"

    hints = _db_query_hints(bad, row_count=0)
    assert any("replace" in str(h) for h in hints)


def test_hints_on_latest_row_sprint_selection():
    hints = _db_query_hints(
        "SELECT sprint, MAX(row_index) AS latest_row FROM pmo_raw_records "
        "WHERE source_view='vewpI8lyYw' GROUP BY sprint ORDER BY latest_row DESC LIMIT 4"
    )
    assert any("latest_row" in str(h) for h in hints)


def _extract_sql_block(marker: str) -> str:
    m = re.search(
        rf"\*\*{re.escape(marker)}[\s\S]*?\n(SELECT[\s\S]*?;)",
        WORKER_C_TASK,
    )
    assert m, f"{marker} SQL block not found"
    return m.group(1)


def test_c2_epic_sql_parent_dual_form_and_task_no():
    c2 = _extract_sql_block("C-2 · 近三周大需求")
    assert "父记录" in c2
    assert "任务编号" in c2
    assert "NOT IN" in c2
    assert "GLOB '[0-9]*'" not in c2
    assert "Participant\"'), '$[0].text')" not in c2
    assert "状态\"'), '$[0].text')" not in c2
    assert "trim(json_extract(fields, '$." in c2


def test_c2_nested_person_blocked():
    from l3_node.tools.pmo_db_tools import (
        pmo_sql_has_vewp_person_or_status_nested_extract,
        run_db_query,
    )

    bad = (
        'SELECT json_extract(fields, \'$.Requirement\') AS epic_name, '
        'json_extract(json_extract(fields, \'$."Person in charge/Participant"\'), '
        "'$[0].text') AS person "
        "FROM pmo_raw_records WHERE source_view = 'vewpI8lyYw' LIMIT 5"
    )
    assert pmo_sql_has_vewp_person_or_status_nested_extract(bad)
    out = run_db_query(sql=bad)
    assert out.get("error") == "pmo_sql_antipattern"


def test_c2_runs_on_local_db_if_present():
    import os
    from pathlib import Path

    from l3_node.tools.pmo_db_tools import run_db_query

    db = Path(os.environ.get("JACHIN_PMO_DB_PATH", Path.home() / ".jachin/workspace/pmo_db.sqlite"))
    if not db.exists():
        return
    c1 = _extract_sql_block("C-1 · 当前 Sprint")
    out1 = run_db_query(sql=c1, max_rows=5)
    assert out1.get("status") == "ok", out1
    sprints = [str(r.get("sprint") or "") for r in out1.get("rows") or [] if r.get("sprint")]
    if not sprints:
        return
    in_list = ",".join(f"'{s}'" for s in sprints[:3])
    c2 = _extract_sql_block("C-2 · 近三周大需求").replace(
        "('<s1>','<s2>','<s3>')", f"({in_list})"
    )
    out2 = run_db_query(sql=c2, max_rows=100)
    assert out2.get("status") == "ok", out2


def test_c3_child_sql_coalesce_parent_and_json_each():
    c3 = _extract_sql_block("C-3 · 子任务全量")
    assert "COALESCE" in c3
    assert "json_each" in c3
    assert "en_name" in c3
    assert "parent_epic IS NOT NULL" not in c3


def test_c6_fallback_row_index_probe():
    assert "**C-6" in WORKER_C_TASK
    c6 = _extract_sql_block("C-6 · 层级探针")
    assert "row_index" in c6
