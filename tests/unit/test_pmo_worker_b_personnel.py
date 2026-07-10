"""Worker B B-S1 / B-4 vewCz1FFJi 人员 SSOT SQL。"""
from __future__ import annotations

import os
import re
from pathlib import Path

from l3_node.pmo_multi_agent_queries import WORKER_B_TASK
from l3_node.tools.pmo_db_tools import (
    pmo_sql_has_vewcz1_personnel_without_json_each,
    pmo_sql_is_worker_b4_personnel_union,
    run_db_query,
)


def _extract_sql(marker: str) -> str:
    m = re.search(
        rf"\*\*{re.escape(marker)}[\s\S]*?\n(SELECT[\s\S]*?;)",
        WORKER_B_TASK,
    )
    assert m, f"{marker} not found"
    return m.group(1)


def test_bs1_in_task_body():
    assert "**B-S1" in WORKER_B_TASK
    bs1 = _extract_sql("B-S1 · 近三周 Sprint")
    assert "-21 days" in bs1
    assert "vewCz1FFJi" in bs1
    assert "replace(substr" in bs1


def test_b4_union_person_plain_and_array():
    b4 = _extract_sql("B-4 · 👥 人员安排 SSOT")
    assert "UNION ALL" in b4
    assert "typeof" in b4
    assert "NOT GLOB" in b4
    assert "json_each" in b4
    assert "任务编号" in b4
    assert pmo_sql_is_worker_b4_personnel_union(b4)
    assert not pmo_sql_has_vewcz1_personnel_without_json_each(b4)


def test_bare_json_each_on_vewcz1_blocked():
    bad = (
        "SELECT json_extract(value, '$.en_name') AS person FROM pmo_raw_records, "
        "json_each(json_extract(fields, '$.\"Person in charge/Participant\"')) "
        "WHERE source_view = 'vewCz1FFJi'"
    )
    assert pmo_sql_has_vewcz1_personnel_without_json_each(bad)


def test_b4_runs_on_local_db_if_present():
    db = Path(os.environ.get("JACHIN_PMO_DB_PATH", Path.home() / ".jachin/workspace/pmo_db.sqlite"))
    if not db.exists():
        return
    bs1 = _extract_sql("B-S1 · 近三周 Sprint")
    out1 = run_db_query(sql=bs1, max_rows=5)
    assert out1.get("status") == "ok", out1
    sprints = [str(r.get("sprint") or "") for r in out1.get("rows") or []]
    assert sprints, "B-S1 should return at least one sprint"
    in_list = ",".join(f"'{s}'" for s in sprints[:3])
    b4 = _extract_sql("B-4 · 👥 人员安排 SSOT").replace(
        "('<s1>','<s2>','<s3>')", f"({in_list})"
    )
    out4 = run_db_query(sql=b4, max_rows=50)
    assert out4.get("status") == "ok", out4
    assert (out4.get("row_count") or 0) > 0, out4
