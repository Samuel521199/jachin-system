"""PMO Worker 结果宿主回填单元测试。"""
from __future__ import annotations

import json
from unittest.mock import patch

from l3_node.pmo_worker_result_backfill import (
    backfill_worker_b,
    backfill_worker_c,
    merge_worker_b_result,
    parse_worker_final_json,
    run_worker_b_host_bootstrap,
)


def test_parse_worker_final_json_from_fence() -> None:
    raw = '说明\n```json\n{"personnel_tasks": []}\n```\n'
    obj = parse_worker_final_json(raw)
    assert obj == {"personnel_tasks": []}


def test_backfill_worker_b_injects_personnel_tasks() -> None:
    empty_b = json.dumps(
        {
            "product_tasks": {"vew8TxMcSh": []},
            "development_tasks": {"vewpI8lyYw": []},
        },
        ensure_ascii=False,
    )
    fake_s1 = [{"sprint": "2026/06/01-Sprint", "sprint_date": "2026-06-01", "cnt": 6}]
    fake_b4 = [
        {
            "person": "Buck",
            "task": "游戏加载-优化",
            "priority": "P0",
            "sprint": "2026/06/01-Sprint",
        }
    ]

    def _fake_run(sql: str, *, max_rows: int = 500):
        if "GROUP BY json_extract(fields, '$.Sprint')" in sql and "vewCz1FFJi" in sql:
            return {"status": "ok", "rows": fake_s1}
        if "UNION ALL" in sql and "vewCz1FFJi" in sql:
            return {"status": "ok", "rows": fake_b4}
        return {"status": "ok", "rows": []}

    with patch("l3_node.tools.pmo_db_tools.run_db_query", side_effect=_fake_run):
        out = backfill_worker_b(empty_b)
    data = json.loads(out)
    assert len(data["personnel_tasks"]) == 1
    assert data["personnel_tasks"][0]["person"] == "Buck"
    assert "_host_backfill" in data


def test_merge_worker_b_preserves_host_personnel() -> None:
    host = {
        "recent_sprints": [{"sprint": "2026/06/01-Sprint"}],
        "personnel_tasks": [{"person": "Buck", "task": "t1"}],
        "sprint_names_for_in": ["2026/06/01-Sprint"],
        "completed_sql_ids": ["B-S1", "B-4"],
    }
    agent = json.dumps({"requirement_context": [{"requirement": "Epic A"}]})
    out = json.loads(merge_worker_b_result(host, agent))
    assert out["personnel_tasks"][0]["person"] == "Buck"
    assert out["requirement_context"][0]["requirement"] == "Epic A"
    assert "B-SUP" in out["completed_sql_ids"]


def test_backfill_worker_c_injects_epics() -> None:
    empty_c = json.dumps({"c1_recent_sprints": []})
    fake_c1 = [{"sprint": "2026/06/01-Sprint", "sprint_date": "2026-06-01", "cnt": 38}]
    fake_c2 = [{"epic_name": "Epic A", "sprint": "2026/06/01-Sprint", "priority": "P0"}]

    def _fake_run(sql: str, *, max_rows: int = 500):
        if "vewpI8lyYw" in sql and "GROUP BY" in sql:
            return {"status": "ok", "rows": fake_c1}
        if "epic_name" in sql:
            return {"status": "ok", "rows": fake_c2}
        return {"status": "ok", "rows": []}

    with patch("l3_node.tools.pmo_db_tools.run_db_query", side_effect=_fake_run):
        out = backfill_worker_c(empty_c)
    data = json.loads(out)
    assert data["current_sprint"] == "2026/06/01-Sprint"
    assert data["epics"][0]["epic_name"] == "Epic A"
