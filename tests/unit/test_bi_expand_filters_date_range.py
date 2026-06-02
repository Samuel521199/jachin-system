"""BI SPA filters → actions：单时间段 / 对比页日期展开。"""
from __future__ import annotations

import unittest

from l3_node.primitives.mcp.mcp_tools.bi.tool_web_scraper import _expand_filters_to_actions


class TestBiExpandFiltersDateRange(unittest.TestCase):
    def test_single_range_defaults_visual_and_verify(self) -> None:
        flt = {
            "date_range": ["2026-05-23", "2026-05-29"],
            "query_selector": "button:has-text('查询')",
        }
        actions = _expand_filters_to_actions(flt)
        fill = next(a for a in actions if a.get("type") == "fill_date_range")
        self.assertEqual(fill["start"], "2026-05-23")
        self.assertEqual(fill["end"], "2026-05-29")
        self.assertEqual(fill.get("date_editor_visual_index"), 0)
        self.assertTrue(fill.get("verify_date_range"))

    def test_single_range_form_label(self) -> None:
        flt = {
            "date_range": ["2026-05-23", "2026-05-27"],
            "date_range_form_label": "业务日期",
            "date_range_use_visual_order": False,
        }
        actions = _expand_filters_to_actions(flt)
        fill = next(a for a in actions if a.get("type") == "fill_date_range")
        self.assertEqual(fill.get("form_item_label"), "业务日期")
        self.assertNotIn("date_editor_visual_index", fill)
        self.assertTrue(fill.get("verify_date_range"))

    def test_compare_visual_order_two_periods(self) -> None:
        flt = {
            "date_range_compare": [
                ["2026-05-23", "2026-05-29"],
                ["2026-05-16", "2026-05-22"],
            ],
            "date_range_compare_use_visual_order": True,
            "date_range_compare_no_escape_after_fill": True,
        }
        actions = _expand_filters_to_actions(flt)
        fills = [a for a in actions if a.get("type") == "fill_date_range"]
        self.assertEqual(len(fills), 2)
        self.assertEqual(fills[0]["date_editor_visual_index"], 0)
        self.assertEqual(fills[1]["date_editor_visual_index"], 1)
        self.assertTrue(fills[0].get("no_escape_after_fill"))
        self.assertTrue(any(a.get("type") == "wait_ms" for a in actions))


if __name__ == "__main__":
    unittest.main()
