"""Worker C C-2 大需求 WHERE 拦截（禁止 Sprint IN 全表冒充 Epic）。"""
from __future__ import annotations

from l3_node.tools.pmo_db_tools import (
    pmo_sql_has_c2_epic_filters,
    pmo_sql_missing_worker_c2_epic_filters,
    run_db_query,
)

USER_ROUND3_SQL = """
SELECT json_extract(fields, '$.Requirement') AS requirement,
       json_extract(fields, '$."父记录"[0].text') AS parent_task,
       COALESCE(json_extract(fields, '$."父记录"[0].text'), 'NULL') AS task_level,
       json_extract(fields, '$.Sprint') AS sprint,
       json_extract(fields, '$.priority') AS priority,
       json_extract(fields, '$."任务编号"') AS task_no,
       trim(json_extract(fields, '$."Person in charge/Participant"')) AS person,
       json_extract(fields, '$.Progress') AS progress,
       json_extract(fields, '$."状态"') AS status_text
FROM pmo_raw_records WHERE source_view = 'vewpI8lyYw'
  AND json_extract(fields, '$.Sprint') IN ('2026/06/08-Sprint','2026/06/01-Sprint','2026/05/25-Sprint')
ORDER BY sprint, task_no LIMIT 200;
"""


def test_user_round3_sql_blocked_as_not_c2():
    assert not pmo_sql_has_c2_epic_filters(USER_ROUND3_SQL)
    assert pmo_sql_missing_worker_c2_epic_filters(USER_ROUND3_SQL)
    out = run_db_query(sql=USER_ROUND3_SQL)
    assert out.get("error") == "pmo_sql_antipattern"


def test_c2_ssot_has_epic_filters():
    import re
    from l3_node.pmo_multi_agent_queries import WORKER_C_TASK

    m = re.search(r"\*\*C-2 ·[\s\S]*?\n(SELECT[\s\S]*?;)", WORKER_C_TASK)
    c2 = m.group(1)
    assert pmo_sql_has_c2_epic_filters(c2)
    assert not pmo_sql_missing_worker_c2_epic_filters(c2)
