"""PMO core:db_query / core:db_write 单元测试。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from l3_node.tools.pmo_db_tools import (
    _db_query_hints,
    _db_query_row_quality_hints,
    _db_query_wide_date_range_hints,
    dispatch_pmo_db_tool,
    init_pmo_database,
    run_db_query,
    run_db_write,
)


class TestPmoDbTools(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db = Path(self._tmpdir.name) / "pmo_test.sqlite"
        os.environ["JACHIN_PMO_DB_PATH"] = str(self._db)

    def tearDown(self) -> None:
        os.environ.pop("JACHIN_PMO_DB_PATH", None)
        self._tmpdir.cleanup()

    def test_init_schema(self) -> None:
        out = init_pmo_database()
        self.assertEqual(out["status"], "ok")
        self.assertTrue(self._db.is_file())

    def test_db_write_upsert_and_query(self) -> None:
        init_pmo_database()
        w = run_db_write(
            table="pmo_dev_requirements",
            operation="upsert",
            records=[
                {
                    "id": "rec_test_01",
                    "requirement_name": "Bingo Flash",
                    "work_cycle": "Sprint-23",
                    "execution_stage": "进行中",
                    "confidence": 0.95,
                }
            ],
        )
        self.assertEqual(w["status"], "ok")
        self.assertEqual(w["inserted"], 1)

        q = run_db_query(
            sql="SELECT requirement_name, work_cycle FROM pmo_dev_requirements WHERE id = :id",
            params={"id": "rec_test_01"},
        )
        self.assertEqual(q["status"], "ok")
        self.assertEqual(q["row_count"], 1)
        self.assertEqual(q["rows"][0]["requirement_name"], "Bingo Flash")

        w2 = run_db_write(
            table="pmo_dev_requirements",
            operation="upsert",
            records=[
                {
                    "id": "rec_test_01",
                    "requirement_name": "Bingo Flash",
                    "execution_stage": "待验收",
                }
            ],
        )
        self.assertEqual(w2["updated"], 1)

    def test_db_query_rejects_write_sql(self) -> None:
        init_pmo_database()
        bad = run_db_query(sql="DELETE FROM pmo_dev_requirements")
        self.assertEqual(bad["status"], "error")

    def test_db_query_allows_pragma_table_info(self) -> None:
        init_pmo_database()
        out = run_db_query(sql="PRAGMA table_info(pmo_raw_records)")
        self.assertEqual(out["status"], "ok")
        names = {r["name"] for r in out["rows"]}
        self.assertIn("source_view", names)
        self.assertNotIn("view_id", names)

    def test_db_query_hints_on_view_id_column_error(self) -> None:
        init_pmo_database()
        bad = run_db_query(sql="SELECT view_id FROM pmo_raw_records LIMIT 1")
        self.assertEqual(bad["status"], "error")
        hints = bad.get("hints") or []
        self.assertTrue(any("source_view" in str(h) for h in hints))

    def test_db_query_hints_on_zero_row_parent_null(self) -> None:
        init_pmo_database()
        conn_path = self._db
        import sqlite3

        conn = sqlite3.connect(conn_path)
        conn.execute(
            "INSERT INTO pmo_raw_records (id, source_view, source_file, row_index, raw_text, fields, synced_at) "
            "VALUES ('t1', 'vewpI8lyYw', 'f.md', 0, 'x', '{\"Requirement\":\"EpicA\",\"父记录\":[{\"text\":\"开发\"}]}', '2026')"
        )
        conn.commit()
        conn.close()
        out = run_db_query(
            sql=(
                "SELECT id FROM pmo_raw_records WHERE source_view='vewpI8lyYw' "
                "AND json_extract(fields, '$.\"父记录\"') IS NULL"
            )
        )
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["row_count"], 0)
        hints = out.get("hints") or []
        self.assertTrue(any("父记录" in str(h) for h in hints))

    def test_db_query_hints_on_alias_json_extract_syntax_error(self) -> None:
        init_pmo_database()
        bad = run_db_query(
            sql=(
                "SELECT r1.json_extract(fields, '$.Requirement') FROM pmo_raw_records r1 "
                "JOIN pmo_raw_records r2 ON r1.id = r2.id"
            )
        )
        self.assertEqual(bad["status"], "error")
        hints = bad.get("hints") or []
        joined = " ".join(str(h) for h in hints)
        self.assertIn("json_extract", joined)
        self.assertTrue("Step 6a" in joined or "JOIN" in joined)

    def test_db_query_hints_on_cross_view_join(self) -> None:
        init_pmo_database()
        out = run_db_query(
            sql=(
                "SELECT r1.id FROM pmo_raw_records r1 "
                "JOIN pmo_raw_records r2 ON r1.source_view = r2.source_view LIMIT 1"
            )
        )
        hints = out.get("hints") or []
        self.assertTrue(any("Step 6a" in str(h) or "JOIN" in str(h) for h in hints))

    def test_db_query_hints_on_person_first_only_en_name(self) -> None:
        init_pmo_database()
        out = run_db_query(
            sql=(
                "SELECT json_extract(fields, '$.\"Person in charge/Participant\"[0].en_name') AS person "
                "FROM pmo_raw_records WHERE source_view='vewCz1FFJi' LIMIT 1"
            )
        )
        hints = out.get("hints") or []
        self.assertTrue(any("json_each" in str(h) for h in hints))

    def test_db_query_hints_on_sprint_array_syntax(self) -> None:
        init_pmo_database()
        out = run_db_query(
            sql=(
                "SELECT json_extract(fields, '$.\"Sprint\"[0].text') AS sprint "
                "FROM pmo_raw_records WHERE source_view='vewpI8lyYw' GROUP BY sprint"
            )
        )
        hints = out.get("hints") or []
        self.assertTrue(any("$.Sprint" in str(h) or "纯字符串" in str(h) for h in hints))

    def test_db_query_hints_on_epic_zero_rows_extra_and(self) -> None:
        init_pmo_database()
        out = run_db_query(
            sql=(
                "SELECT id FROM pmo_raw_records WHERE source_view='vewpI8lyYw' "
                "AND json_extract(fields, '$.\"父记录\"[0].text') IS NULL "
                "AND json_extract(fields, '$.priority') = 'P0' "
                "AND json_extract(fields, '$.Sprint') IS NOT NULL "
                "AND json_extract(fields, '$.Requirement') NOT IN ('开发','美术','产品')"
            )
        )
        hints = out.get("hints") or []
        self.assertTrue(any("Step 4" in str(h) or "追加" in str(h) for h in hints))

    def test_db_query_hints_on_epic_filter_on_personnel_view(self) -> None:
        init_pmo_database()
        out = run_db_query(
            sql=(
                "SELECT id FROM pmo_raw_records WHERE source_view='vewCz1FFJi' "
                "AND json_extract(fields, '$.\"父记录\"[0].text') IS NULL"
            )
        )
        hints = out.get("hints") or []
        joined = " ".join(str(h) for h in hints)
        self.assertIn("vewpI8lyYw", joined)
        self.assertIn("vewCz1FFJi", joined)

    def test_db_query_hints_on_step3_en_name_only(self) -> None:
        init_pmo_database()
        out = run_db_query(
            sql=(
                "SELECT json_extract(value, '$.en_name') AS en_name "
                "FROM pmo_raw_records, "
                "json_each(json_extract(fields, '$.\"Person in charge/Participant\"')) "
                "WHERE source_view='vewCz1FFJi' LIMIT 5"
            )
        )
        hints = out.get("hints") or []
        self.assertTrue(any("不完整" in str(h) or "task/status" in str(h) for h in hints))

    def test_db_query_hints_on_wrong_chinese_field_names(self) -> None:
        hints = _db_query_hints(
            "SELECT json_extract(fields, '$.负责人') FROM pmo_raw_records "
            "WHERE source_view='vewpI8lyYw'",
            row_count=10,
        )
        self.assertTrue(any("Person in charge" in str(h) or "Requirement" in str(h) for h in hints))

    def test_db_query_hints_on_version_limit_one(self) -> None:
        hints = _db_query_hints(
            'SELECT json_extract(fields, \'$."Version Goal"\') FROM pmo_raw_records '
            "WHERE source_view='vew8TxMcSh' LIMIT 1",
            row_count=1,
        )
        self.assertTrue(any("Step7" in str(h) or "COUNT" in str(h) for h in hints))

    def test_db_query_row_quality_hints_on_all_null_column(self) -> None:
        rows = [{"person": None} for _ in range(10)]
        hints = _db_query_row_quality_hints(
            rows,
            "SELECT json_extract(fields, '$.负责人') AS person FROM pmo_raw_records "
            "WHERE source_view='vewpI8lyYw'",
        )
        self.assertTrue(any("非空率极低" in str(h) for h in hints))

    def test_db_query_row_quality_hints_on_person_json_array(self) -> None:
        rows = [{"person": '[{"en_name":"alvintan"}]'} for _ in range(3)]
        hints = _db_query_row_quality_hints(
            rows,
            "SELECT json_extract(fields, '$.\"Person in charge/Participant\"') AS person "
            "FROM pmo_raw_records WHERE source_view='vewCz1FFJi'",
        )
        self.assertTrue(any("json_each" in str(h) for h in hints))

    def test_db_query_wide_date_range_hints(self) -> None:
        hints = _db_query_wide_date_range_hints(
            "SELECT * FROM pmo_raw_records WHERE json_extract(fields, '$.\"Start Date\"') >= '2020-01-01'"
        )
        self.assertTrue(any("Sprint" in str(h) or "过宽" in str(h) for h in hints))

    def test_db_query_hints_on_step3_without_json_each(self) -> None:
        hints = _db_query_hints(
            "SELECT json_extract(fields, '$.\"Person in charge/Participant\"') AS person "
            "FROM pmo_raw_records WHERE source_view='vewCz1FFJi'",
            row_count=23,
        )
        self.assertTrue(any("json_each" in str(h) and "绝对禁忌" in str(h) for h in hints))

    def test_db_query_allows_sqlite_replace_function_in_select(self) -> None:
        sql = (
            "SELECT date(replace(substr(json_extract(fields, '$.Sprint'), 1, 10), '/', '-')) "
            "FROM pmo_raw_records WHERE source_view = 'vew8TxMcSh' LIMIT 1"
        )
        out = run_db_query(sql=sql)
        assert out.get("status") == "ok", out

    def test_db_query_blocks_product_status_nested_extract(self) -> None:
        from l3_node.tools.pmo_db_tools import pmo_sql_has_product_status_nested_extract, run_db_query

        bad_sql = (
            "SELECT json_extract(json_extract(fields, '$.\"需求状态\"'), '$[0].text') "
            "FROM pmo_raw_records WHERE source_view='vew8TxMcSh' LIMIT 10"
        )
        self.assertTrue(pmo_sql_has_product_status_nested_extract(bad_sql))
        out = run_db_query(sql=bad_sql)
        self.assertEqual(out.get("status"), "error")
        self.assertEqual(out.get("error"), "pmo_sql_antipattern")
        self.assertTrue(any("plain string" in str(h) for h in (out.get("hints") or [])))

    def test_db_query_hints_on_malformed_json_product_status(self) -> None:
        hints = _db_query_hints(
            "SELECT json_extract(json_extract(fields, '$.\"需求状态\"'), '$[0].text') "
            "FROM pmo_raw_records WHERE source_view='vew8TxMcSh'",
            message="malformed JSON",
        )
        self.assertTrue(any("需求状态" in str(h) for h in hints))

    def test_db_query_blocks_product_fields_on_dev_view(self) -> None:
        from l3_node.tools.pmo_db_tools import (
            pmo_sql_has_product_fields_on_dev_view,
            run_db_query,
        )

        bad_sql = (
            "SELECT json_extract(fields, '$.\"任务简述\"') AS task "
            "FROM pmo_raw_records WHERE source_view = 'vewpI8lyYw' LIMIT 300"
        )
        self.assertTrue(pmo_sql_has_product_fields_on_dev_view(bad_sql))
        out = run_db_query(sql=bad_sql)
        self.assertEqual(out.get("status"), "error")
        self.assertEqual(out.get("error"), "pmo_sql_antipattern")

    def test_db_query_blocks_vewcz1_without_json_each(self) -> None:
        from l3_node.tools.pmo_db_tools import (
            pmo_sql_has_vewcz1_personnel_without_json_each,
            run_db_query,
        )

        bad_sql = (
            "SELECT json_extract(fields, '$.Requirement') AS task, "
            "json_extract(fields, '$.\"Person in charge/Participant\"') AS person "
            "FROM pmo_raw_records WHERE source_view = 'vewCz1FFJi' LIMIT 700"
        )
        self.assertTrue(pmo_sql_has_vewcz1_personnel_without_json_each(bad_sql))
        out = run_db_query(sql=bad_sql)
        self.assertEqual(out.get("status"), "error")
        self.assertEqual(out.get("error"), "pmo_sql_antipattern")

    def test_worker_b_product_sql_uses_safe_status_extract(self) -> None:
        from l3_node.pmo_multi_agent_queries import WORKER_B_TASK

        self.assertIn("json_extract(fields, '$.\"需求状态\"') AS demand_status", WORKER_B_TASK)
        self.assertNotIn(
            "json_extract(json_extract(fields, '$.\"需求状态\"'), '$[0].text')",
            WORKER_B_TASK,
        )

        hints = _db_query_hints(
            "SELECT json_extract(fields, '$.Requirement') FROM pmo_raw_records "
            "WHERE source_view='vewpI8lyYw' AND json_extract(fields, '$.\"父记录\"') = '[]'",
            row_count=0,
        )
        self.assertTrue(any("父记录" in str(h) for h in hints))

    def test_db_query_hints_on_join_step6(self) -> None:
        hints = _db_query_hints(
            "SELECT r1.fields FROM pmo_raw_records r1 "
            "JOIN pmo_raw_records r2 ON r1.source_view = r2.source_view",
            row_count=0,
        )
        self.assertTrue(any("Step 6a" in str(h) or "JOIN" in str(h) for h in hints))

    def test_low_confidence_warning(self) -> None:
        init_pmo_database()
        w = run_db_write(
            table="pmo_product_requirements",
            operation="upsert",
            records=[
                {
                    "id": "rec_low_conf",
                    "requirement_name": "模糊需求",
                    "confidence": 0.55,
                }
            ],
        )
        self.assertEqual(w["status"], "ok")
        self.assertEqual(len(w["low_confidence_warnings"]), 1)
        self.assertEqual(w["low_confidence_warnings"][0]["id"], "rec_low_conf")

    def test_dispatch_native_bridge(self) -> None:
        init_pmo_database()
        out = dispatch_pmo_db_tool(
            "core:db_query",
            input={"sql": "SELECT COUNT(*) AS n FROM pmo_people"},
        )
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["rows"][0]["n"], 0)

    def test_personnel_resolves_person_id_by_name(self) -> None:
        init_pmo_database()
        run_db_write(
            table="pmo_people",
            operation="upsert",
            records=[{"id": "ethan_001", "name": "Ethan", "dept": "产品"}],
        )
        w = run_db_write(
            table="pmo_personnel_task_progress",
            operation="upsert",
            records=[
                {
                    "id": "task_001",
                    "person_id": "Ethan",
                    "person_name": "Ethan",
                    "task_name": "平台重命名",
                    "work_cycle": "2026/05/11-Sprint",
                }
            ],
        )
        self.assertEqual(w["status"], "ok")
        self.assertEqual(w["inserted"], 1)
        q = run_db_query(
            sql="SELECT person_id, person_name FROM pmo_personnel_task_progress WHERE id = :id",
            params={"id": "task_001"},
        )
        self.assertEqual(q["rows"][0]["person_id"], "ethan_001")
        self.assertEqual(q["rows"][0]["person_name"], "Ethan")

    def test_personnel_auto_creates_people_when_missing(self) -> None:
        init_pmo_database()
        w = run_db_write(
            table="pmo_personnel_task_progress",
            operation="upsert",
            records=[
                {
                    "id": "task_002",
                    "person_name": "koi.liu",
                    "task_name": "域名",
                    "dept": "产品",
                }
            ],
        )
        self.assertEqual(w["status"], "ok")
        self.assertEqual(w["inserted"], 1)
        q = run_db_query(sql="SELECT id, name FROM pmo_people WHERE name = 'koi.liu'")
        self.assertEqual(q["row_count"], 1)
        self.assertTrue(q["rows"][0]["id"])


if __name__ == "__main__":
    unittest.main()
