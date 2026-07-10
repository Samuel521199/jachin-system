"""PMO atom_bi_project_context 落盘路径 SSOT。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import (
    _default_wiki_urls,
    _filter_wiki_urls_for_pmo,
    _manifest_file_entry,
    _pmo_prune_stale_md,
    _pmo_skill_wiki_urls,
    _pmo_stable_md_basename,
    _resolve_output_dir,
)


class TestPmoBiProjectOutputDir(unittest.TestCase):
    def test_bare_pmo_lark_pull_resolves_to_workspace_not_repo(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "repo"
            root.mkdir()
            home = Path(td) / "home"
            ws_pull = home / ".jachin" / "workspace" / "pmo_lark_pull"
            with mock.patch.object(Path, "home", return_value=home):
                out = _resolve_output_dir({"output_dir_relative": "pmo_lark_pull"}, root)
            self.assertEqual(out, ws_pull.resolve())
            self.assertFalse(str(out).startswith(str(root)))

    def test_env_jachin_pmo_lark_pull_dir_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            custom = Path(td) / "custom_pull"
            root = Path(td) / "repo"
            root.mkdir()
            with mock.patch.dict(os.environ, {"JACHIN_PMO_LARK_PULL_DIR": str(custom)}, clear=False):
                out = _resolve_output_dir({"output_dir_relative": "pmo_lark_pull"}, root)
            self.assertEqual(out, custom.resolve())

    def test_manifest_entry_uses_basename_under_workspace_pull(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            ws_pull = home / ".jachin" / "workspace" / "pmo_lark_pull"
            ws_pull.mkdir(parents=True)
            md = ws_pull / "09_K11 项目进度_vewpYzbZ29.md"
            md.write_text("# test", encoding="utf-8")
            with mock.patch.object(Path, "home", return_value=home):
                entry = _manifest_file_entry(md, Path(td) / "repo", ws_pull.resolve())
            self.assertEqual(entry, md.name)
            self.assertNotIn("pmo_lark_pull", entry.replace("\\", "/").split("/")[-2:])

    def test_stable_basename_same_view_overwrites_same_name(self) -> None:
        slug = "K11 需求池_ZItbw4om_产品任务需求完成度与人员分配_vew8TxMcSh"
        meta = {"view_id_hint": "vew8TxMcSh", "node_token": "ZItbw4om"}
        a = _pmo_stable_md_basename(slug, meta)
        b = _pmo_stable_md_basename(slug, meta)
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("01_"))
        self.assertTrue(a.endswith("_vew8TxMcSh.md"))

    def test_prune_removes_stale_md(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            keep = d / "01_keep.md"
            stale = d / "99_stale_old_run.md"
            keep.write_text("k", encoding="utf-8")
            stale.write_text("s", encoding="utf-8")
            removed = _pmo_prune_stale_md(d, {"01_keep.md"})
            self.assertEqual(removed, ["99_stale_old_run.md"])
            self.assertTrue(keep.is_file())
            self.assertFalse(stale.exists())

    def test_pmo_skill_wiki_urls_has_twelve_views(self) -> None:
        self.assertEqual(len(_pmo_skill_wiki_urls()), 12)

    def test_filter_wiki_urls_strips_default_noise(self) -> None:
        defaults = _default_wiki_urls()
        self.assertGreater(len(defaults), 12)
        filtered = _filter_wiki_urls_for_pmo(defaults)
        self.assertEqual(len(filtered), 12)
        self.assertTrue(all("view=vew" in u for u in filtered))
        self.assertFalse(any("ldxeuHgiN5L2gXBH" in u for u in filtered))
        self.assertFalse(any("HS8qw9Xv" in u for u in filtered))


if __name__ == "__main__":
    unittest.main()
