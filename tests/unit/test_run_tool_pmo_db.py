"""run_tool 对 core:db_query / core:db_write 的路由测试。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from l3_node.primitives.tools.loader import run_tool


class TestRunToolPmoDb(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db = Path(self._tmpdir.name) / "pmo_test.sqlite"
        os.environ["JACHIN_PMO_DB_PATH"] = str(self._db)

    def tearDown(self) -> None:
        os.environ.pop("JACHIN_PMO_DB_PATH", None)
        self._tmpdir.cleanup()

    def test_run_tool_db_write_not_unknown(self) -> None:
        allow = ["core:db_write", "core:db_query", "core:fs_read"]
        out = run_tool(
            "core:db_write",
            '{"table":"pmo_dev_requirements","operation":"upsert","records":[{"id":"t1","requirement_name":"Test"}]}',
            allowed_skills=allow,
        )
        self.assertNotIn("[未知工具", out)
        self.assertIn('"status": "ok"', out)

    def test_run_tool_db_query_not_unknown(self) -> None:
        allow = ["core:db_write", "core:db_query"]
        run_tool(
            "core:db_write",
            '{"table":"pmo_dev_requirements","operation":"upsert","records":[{"id":"t2","requirement_name":"Q"}]}',
            allowed_skills=allow,
        )
        out = run_tool(
            "core:db_query",
            '{"sql":"SELECT requirement_name FROM pmo_dev_requirements WHERE id = :id","params":{"id":"t2"}}',
            allowed_skills=allow,
        )
        self.assertNotIn("[未知工具", out)
        self.assertIn("Q", out)


if __name__ == "__main__":
    unittest.main()
