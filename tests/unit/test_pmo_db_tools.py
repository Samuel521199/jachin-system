"""PMO core:db_query / core:db_write 单元测试。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from l3_node.tools.pmo_db_tools import (
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
