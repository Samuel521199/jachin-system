"""core:fs_read 仓库 docs/ 相对路径解析。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import core.native_tools as nt
from core.native_tools import core_fs_read, _dedupe_pmo_lark_pull_segments


class TestFsReadProjectDocs(unittest.TestCase):
    def test_relative_docs_path_resolves_to_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            doc = root / "docs" / "pmo_bmo_plugin" / "项目开发全流程说明.md"
            doc.parent.mkdir(parents=True)
            doc.write_text("# 流程说明\n立项/评审", encoding="utf-8")
            with mock.patch.object(nt, "_PROJ_ROOT", root):
                out = core_fs_read("docs/pmo_bmo_plugin/项目开发全流程说明.md")
            self.assertIn("立项", out)

    def test_dedupe_double_pmo_lark_pull(self) -> None:
        raw = r"D:/proj/pmo_lark_pull/pmo_lark_pull/01_a.md"
        self.assertEqual(
            _dedupe_pmo_lark_pull_segments(raw).lower(),
            "d:/proj/pmo_lark_pull/01_a.md",
        )


if __name__ == "__main__":
    unittest.main()
