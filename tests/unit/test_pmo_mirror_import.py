"""PMO v7 mirror import 单元测试。"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from l3_node.tools.pmo_db_tools import dispatch_pmo_db_tool, init_pmo_database, run_db_query
from l3_node.tools.pmo_mirror_import import (
    extract_view_id_from_filename,
    parse_md_content,
    resolve_md_files,
    run_mirror_import,
)


SAMPLE_MD = """## 同步元数据
```json
{"title": "K11 测试", "view_id_hint": "vewTEST001"}
```

### 层级视图

- **`recvjtUS3szDkf…`** · Requirement: 游戏加载-BatoSpine优化 · priority: P1 · Sprint: 2026/05/18-Sprint · 状态: 🔵 按时完成

| Requirement | Sprint | 状态 |
| --- | --- | --- |
| Bingo Flash | 2026/05/18-Sprint | 已完成 |
| vi重构 | 2026/05/18-Sprint | 开发中 |
"""


class TestPmoMirrorImport(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._root = Path(self._tmpdir.name)
        self._db = self._root / "pmo_test.sqlite"
        os.environ["JACHIN_PMO_DB_PATH"] = str(self._db)

        self._pull = self._root / "pull"
        self._pull.mkdir()
        md_name = "01_test_vewTEST001.md"
        (self._pull / md_name).write_text(SAMPLE_MD, encoding="utf-8")
        manifest = {
            "output_dir": str(self._pull),
            "files": [md_name],
        }
        (self._pull / "00_SYNC_MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        os.environ.pop("JACHIN_PMO_DB_PATH", None)
        self._tmpdir.cleanup()

    def test_extract_view_id(self) -> None:
        self.assertEqual(extract_view_id_from_filename("03_foo_vewAb12Xy.md"), "vewAb12Xy")

    def test_parse_md_content(self) -> None:
        rows = parse_md_content(SAMPLE_MD)
        self.assertGreaterEqual(len(rows), 3)
        bullet = next(r for r in rows if "BatoSpine" in r.raw_text)
        self.assertEqual(bullet.fields.get("Requirement"), "游戏加载-BatoSpine优化")
        table = next(r for r in rows if r.fields.get("Requirement") == "Bingo Flash")
        self.assertEqual(table.fields.get("状态"), "已完成")

    def test_resolve_md_files(self) -> None:
        _, files = resolve_md_files(manifest_path=self._pull / "00_SYNC_MANIFEST.json")
        self.assertEqual(len(files), 1)

    def test_run_mirror_import(self) -> None:
        init_pmo_database()
        out = run_mirror_import(manifest_path=str(self._pull / "00_SYNC_MANIFEST.json"))
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["total_records"] >= 3)

        q = run_db_query(
            sql="SELECT COUNT(*) AS n FROM pmo_raw_records WHERE source_view = :v",
            params={"v": "vewTEST001"},
        )
        self.assertEqual(q["rows"][0]["n"], out["total_records"])

        meta = run_db_query(
            sql="SELECT record_count, columns_json FROM pmo_views_meta WHERE view_id = :v",
            params={"v": "vewTEST001"},
        )
        self.assertEqual(meta["row_count"], 1)
        cols = json.loads(meta["rows"][0]["columns_json"])
        self.assertIn("Requirement", cols)

    def test_dispatch_mirror_tool(self) -> None:
        init_pmo_database()
        out = dispatch_pmo_db_tool(
            "core:pmo_mirror_import",
            input={"manifest_path": str(self._pull / "00_SYNC_MANIFEST.json")},
        )
        self.assertEqual(out["status"], "ok")
        self.assertGreater(out["total_records"], 0)


if __name__ == "__main__":
    unittest.main()
