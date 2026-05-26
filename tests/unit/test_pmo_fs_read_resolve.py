"""PMO fs_read 路径回退（pmo_lark_pull + _vew 后缀）。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import core.native_tools as nt
from core.native_tools import core_fs_read, try_resolve_pmo_lark_md_if_missing


class TestPmoFsReadResolve(unittest.TestCase):
    def test_resolve_by_view_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pull = root / "pmo_lark_pull"
            pull.mkdir()
            real = pull / "02_K11 需求池_ZItbw4om_产品端人员任务看板_按人员分组_vewL9Mofgd.md"
            real.write_text("| 任务 | 状态 |\n| --- | --- |\n| A | 进行中 |", encoding="utf-8")
            wrong = pull / "02_K11 需求池_ZItbw4om_产品任务需求完成度与人员分配_vewL9Mofgd.md"
            with mock.patch.object(nt, "_PROJ_ROOT", root):
                resolved = try_resolve_pmo_lark_md_if_missing(wrong)
                self.assertIsNotNone(resolved)
                self.assertEqual(resolved.name, real.name)
                content = core_fs_read(str(wrong))
            self.assertIn("进行中", content)


if __name__ == "__main__":
    unittest.main()
