"""native_tool_json 单元测试。"""
from __future__ import annotations

import unittest

from l3_node.primitives.native_tool_json import (
    coerce_file_path_from_tool_input,
    parse_fs_write_tool_input,
)


class TestNativeToolJson(unittest.TestCase):
    def test_coerce_plain_path(self) -> None:
        self.assertEqual(
            coerce_file_path_from_tool_input("pmo_lark_pull/00_SYNC_MANIFEST.json"),
            "pmo_lark_pull/00_SYNC_MANIFEST.json",
        )

    def test_coerce_json_object(self) -> None:
        inp = '{"file_path": "C:\\\\Users\\\\x\\\\.jachin\\\\workspace\\\\pmo_lark_pull\\\\00_SYNC_MANIFEST.json"}'
        self.assertIn("00_SYNC_MANIFEST.json", coerce_file_path_from_tool_input(inp))

    def test_coerce_truncated_json_regex(self) -> None:
        inp = '{"file_path": "pmo_lark_pull/00_SYNC_MANIFEST.json"'
        self.assertEqual(
            coerce_file_path_from_tool_input(inp),
            "pmo_lark_pull/00_SYNC_MANIFEST.json",
        )

    def test_coerce_json_not_whole_path(self) -> None:
        self.assertEqual(coerce_file_path_from_tool_input('{"file_path":'), "")

    def test_parse_fs_write_json(self) -> None:
        inp = '{"file_path":"pmo_staging/a.json","content":"{\\"x\\":1}"}'
        p = parse_fs_write_tool_input(inp)
        self.assertEqual(p["file_path"], "pmo_staging/a.json")
        self.assertIn("x", p["content"])


if __name__ == "__main__":
    unittest.main()
