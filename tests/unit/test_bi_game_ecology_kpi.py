"""游戏与生态 KPI：stats_game_daily 平台汇总行提取。"""
from __future__ import annotations

import unittest

from l3_node.primitives.skills.bi.bi_daily_report.main_skill import (
    _extract_stats_game_daily_platform_row,
    _normalize_win_rate_pct,
)


class TestStatsGameDailyPlatformRow(unittest.TestCase):
    def test_extract_daily_total_row(self) -> None:
        rows = [
            {
                "业务日期": "2026-05-30",
                "统计范围": "当日总计",
                "完成局数": "30,377",
                "获胜次数": 12544,
                "胜率": 0.4078,
            },
            {
                "业务日期": "2026-05-30",
                "统计范围": "Tongits King",
                "完成局数": 22648,
                "获胜次数": 8947,
                "胜率": 0.3903,
            },
        ]
        got = _extract_stats_game_daily_platform_row(rows)
        self.assertIsNotNone(got)
        assert got is not None
        self.assertEqual(got["完成游戏局数"], 30377)
        self.assertEqual(got["用户获胜次数"], 12544)
        self.assertEqual(got["胜率（%）"], 40.78)

    def test_normalize_win_rate_pct(self) -> None:
        self.assertEqual(_normalize_win_rate_pct(0.4078), 40.78)
        self.assertEqual(_normalize_win_rate_pct(40.78), 40.78)


if __name__ == "__main__":
    unittest.main()
