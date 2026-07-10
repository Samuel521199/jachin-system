"""Worker B B-SUP 与 Worker C C-2 拦截分流。"""
from __future__ import annotations

import re

from l3_node.pmo_multi_agent_queries import WORKER_B_TASK
from l3_node.tools.pmo_db_tools import (
    pmo_sql_has_invented_chinese_task_fields,
    pmo_sql_is_worker_b_sup_vewp_context,
    pmo_sql_missing_worker_c2_epic_filters,
    run_db_query,
)

USER_ROUND5_WRONG = """
SELECT json_extract(fields, '$.任务标题') AS task_title,
       json_extract(fields, '$.关联需求') AS related_requirement,
       json_extract(fields, '$.负责人') AS assignee,
       json_extract(fields, '$.Sprint') AS sprint
FROM pmo_raw_records
WHERE source_view = 'vewpI8lyYw'
  AND json_extract(fields, '$.Sprint') IN ('2026/06/08-Sprint','2026/06/01-Sprint')
LIMIT 200;
"""


def test_user_round5_blocked_as_b_sup_invented_not_c2():
    assert pmo_sql_has_invented_chinese_task_fields(USER_ROUND5_WRONG)
    assert not pmo_sql_is_worker_b_sup_vewp_context(USER_ROUND5_WRONG)
    assert not pmo_sql_missing_worker_c2_epic_filters(USER_ROUND5_WRONG)
    out = run_db_query(sql=USER_ROUND5_WRONG)
    assert out.get("error") == "pmo_sql_antipattern"
    assert "B-SUP" in (out.get("message") or "")
    assert "C-2" not in (out.get("message") or "")


def test_b_sup_ssot_runs_without_c2_block():
    m = re.search(r"\*\*B-SUP ·[\s\S]*?\n(SELECT[\s\S]*?;)", WORKER_B_TASK)
    b_sup = m.group(1)
    assert pmo_sql_is_worker_b_sup_vewp_context(b_sup)
    assert not pmo_sql_missing_worker_c2_epic_filters(b_sup)
    assert not pmo_sql_has_invented_chinese_task_fields(b_sup)
    out = run_db_query(sql=b_sup, max_rows=5)
    assert out.get("error") != "pmo_sql_antipattern"
