"""PMO core:pmo_import_json / gap_report 单元测试。"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from l3_node.tools.pmo_db_tools import (
    init_pmo_database,
    run_import_json,
    run_init_gap_report,
)


class TestPmoImportJson(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        base = Path(self._tmpdir.name)
        self._db = base / "pmo_test.sqlite"
        self._staging = base / "staging"
        self._staging.mkdir()
        self._manifest_dir = base / "pull"
        self._manifest_dir.mkdir()
        os.environ["JACHIN_PMO_DB_PATH"] = str(self._db)

    def tearDown(self) -> None:
        os.environ.pop("JACHIN_PMO_DB_PATH", None)
        self._tmpdir.cleanup()

    def test_bundle_json_import(self) -> None:
        init_pmo_database()
        src = "01_product.md"
        bundle = {
            "source_file": src,
            "source_view": "vew8TxMcSh",
            "tables": {
                "pmo_people": [{"id": "ethan_001", "name": "Ethan", "dept": "产品"}],
                "pmo_product_requirements": [
                    {
                        "id": "req_001",
                        "requirement_name": "平台重命名",
                        "work_cycle": "2026/05/11-Sprint",
                        "confidence": 0.9,
                    }
                ],
            },
        }
        jpath = self._staging / "vew8TxMcSh.json"
        jpath.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")

        out = run_import_json(file_path=str(jpath))
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["inserted"], 2)
        self.assertEqual(out["by_table"]["pmo_product_requirements"]["record_count"], 1)

        manifest = {
            "output_dir": str(self._manifest_dir),
            "files": [src, "02_other.md"],
        }
        mpath = self._manifest_dir / "00_SYNC_MANIFEST.json"
        mpath.write_text(json.dumps(manifest), encoding="utf-8")

        gap = run_init_gap_report(manifest_path=str(mpath))
        self.assertEqual(gap["status"], "ok")
        self.assertEqual(gap["missing_count"], 1)
        self.assertIn("02_other.md", gap["missing_files"])
        self.assertEqual(gap["files"][0]["row_count_total"], 1)

    def test_ndjson_import(self) -> None:
        init_pmo_database()
        lines = [
            json.dumps(
                {
                    "table": "pmo_dev_requirements",
                    "records": [
                        {
                            "id": "dev_1",
                            "requirement_name": "机器人优化",
                            "source_file": "03_dev.md",
                        }
                    ],
                },
                ensure_ascii=False,
            )
        ]
        jpath = self._staging / "batch.ndjson"
        jpath.write_text("\n".join(lines) + "\n", encoding="utf-8")
        out = run_import_json(file_path=str(jpath))
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["inserted"], 1)

    def test_malformed_bundle_salvage_records(self) -> None:
        init_pmo_database()
        src = "01_product.md"
        # 故意漏掉第二条 record 前的逗号
        broken = (
            '{"source_file":"01_product.md","source_view":"vew8TxMcSh","tables":{'
            '"pmo_product_requirements":[{"id":"req_001","requirement_name":"A","confidence":0.9}'
            '{"id":"req_002","requirement_name":"B","confidence":0.9}]}}'
        )
        jpath = self._staging / "broken.json"
        jpath.write_text(broken, encoding="utf-8")
        out = run_import_json(file_path=str(jpath))
        self.assertIn(out["status"], ("ok", "partial"))
        self.assertGreaterEqual(out["inserted"] + out["updated"], 1)
        self.assertTrue(out.get("parse_warnings"))

    def test_ndjson_skip_bad_line(self) -> None:
        init_pmo_database()
        good = json.dumps(
            {
                "table": "pmo_dev_requirements",
                "records": [{"id": "dev_2", "requirement_name": "ok row", "source_file": "03.md"}],
            },
            ensure_ascii=False,
        )
        lines = ["{this is not json", good]
        jpath = self._staging / "mixed.ndjson"
        jpath.write_text("\n".join(lines) + "\n", encoding="utf-8")
        out = run_import_json(file_path=str(jpath))
        self.assertIn(out["status"], ("ok", "partial"))
        self.assertEqual(out["inserted"], 1)
        warns = out.get("parse_warnings", [])
        self.assertTrue(
            any("skip_invalid" in w or "not_object" in w for w in warns),
            msg=f"expected skip/not_object in {warns}",
        )

    def test_import_personnel_resolves_person_name_to_people_id(self) -> None:
        init_pmo_database()
        run_import_json(
            file_path=str(
                self._write_json(
                    {
                        "source_file": "01.md",
                        "source_view": "vew8TxMcSh",
                        "tables": {
                            "pmo_people": [{"id": "haku_001", "name": "Haku", "dept": "产品"}],
                        },
                    },
                    "people.json",
                )
            )
        )
        lines = [
            json.dumps(
                {
                    "table": "pmo_personnel_task_progress",
                    "records": [
                        {
                            "id": "pt_1",
                            "person_id": "Haku",
                            "person_name": "Haku",
                            "task_name": "匹配机制优化",
                            "source_file": "03.md",
                        }
                    ],
                },
                ensure_ascii=False,
            )
        ]
        jpath = self._staging / "personnel.ndjson"
        jpath.write_text("\n".join(lines) + "\n", encoding="utf-8")
        out = run_import_json(file_path=str(jpath))
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["inserted"], 1)
        from l3_node.tools.pmo_db_tools import run_db_query

        q = run_db_query(
            sql="SELECT person_id FROM pmo_personnel_task_progress WHERE id = 'pt_1'",
        )
        self.assertEqual(q["rows"][0]["person_id"], "haku_001")

    def _write_json(self, data: dict, name: str) -> Path:
        p = self._staging / name
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return p


if __name__ == "__main__":
    unittest.main()
