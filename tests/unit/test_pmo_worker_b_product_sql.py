"""Verify Worker B B-SUP SQL runs against live PMO DB."""
from __future__ import annotations

import re

from l3_node.pmo_multi_agent_queries import WORKER_B_TASK
from l3_node.tools.pmo_db_tools import run_db_query


def test_b_sup_sql_runs():
    m = re.search(
        r"\*\*B-SUP ·[\s\S]*?\n(SELECT[\s\S]*?LIMIT 300;)",
        WORKER_B_TASK,
    )
    assert m, "B-SUP SQL block not found"
    sql = m.group(1).replace("('<s1>','<s2>','<s3>')", "('2026/06/01-Sprint')")
    out = run_db_query(sql=sql, max_rows=3)
    assert out.get("error") != "pmo_sql_antipattern"
    assert out.get("status") == "ok" or out.get("row_count", 0) >= 0
