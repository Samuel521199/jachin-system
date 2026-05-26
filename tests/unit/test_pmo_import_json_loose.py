"""pmo_import_json_loose 单元测试。"""
from __future__ import annotations

import unittest

from l3_node.tools.pmo_import_json_loose import (
    extract_balanced_json_objects,
    loads_json_loose,
    salvage_bundle_tables,
)


def _coerce_records(raw):
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    return []


_WRITABLE = frozenset(
    {
        "pmo_people",
        "pmo_product_requirements",
        "pmo_dev_requirements",
        "pmo_design_requirements",
        "pmo_personnel_task_progress",
    }
)


class TestPmoImportJsonLoose(unittest.TestCase):
    def test_extract_balanced_objects(self) -> None:
        text = 'prefix {"id":"a","requirement_name":"x"} junk {"id":"b","requirement_name":"y"}'
        objs = extract_balanced_json_objects(text)
        self.assertEqual(len(objs), 2)
        self.assertEqual(objs[0]["id"], "a")

    def test_salvage_bundle_tables(self) -> None:
        broken = (
            '{"source_file":"01.md","source_view":"vew1","tables":{"pmo_product_requirements":['
            '{"id":"r1","requirement_name":"One"}'
            '{"id":"r2","requirement_name":"Two"}]}}'
        )
        _, _, batches, warnings = salvage_bundle_tables(
            broken,
            writable_tables=_WRITABLE,
            coerce_records=_coerce_records,
        )
        self.assertTrue(batches)
        self.assertTrue(warnings)
        tables = {b[0] for b in batches}
        self.assertIn("pmo_product_requirements", tables)

    def test_loads_json_loose_trailing_comma(self) -> None:
        text = '{"a": 1, "b": 2,}'
        obj, mode = loads_json_loose(text)
        if mode == "repaired":
            self.assertIsInstance(obj, dict)
        else:
            # 无 json_repair 时 strict 会失败
            self.assertIsNone(obj)


if __name__ == "__main__":
    unittest.main()
