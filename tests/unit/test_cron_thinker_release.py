# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
import unittest

from unittest.mock import patch

from core.cron_thinker import (
    RELEASE_TITLE_NEEDLE,
    compute_smoke_run_at_beijing,
    job_id_for_maintenance_day,
    parse_release_maintenance_date,
    release_title_present,
)


class CronThinkerReleaseParseTests(unittest.TestCase):
    def test_parse_sample_email_body(self) -> None:
        raw = f"""标题 {RELEASE_TITLE_NEEDLE}

【维护时间】2026-5-5 07:00 —— 2026-5-5 08:00
"""
        d = parse_release_maintenance_date(raw)
        self.assertEqual(d, dt.date(2026, 5, 5))

    def test_parse_slash_date(self) -> None:
        raw = f"{RELEASE_TITLE_NEEDLE}\n【维护时间】 2026/05/06 隔夜"
        d = parse_release_maintenance_date(raw)
        self.assertEqual(d, dt.date(2026, 5, 6))

    def test_ignore_without_title(self) -> None:
        self.assertIsNone(parse_release_maintenance_date("【维护时间】2026-5-5"))

    def test_ignore_without_maintenance_line(self) -> None:
        self.assertIsNone(parse_release_maintenance_date(RELEASE_TITLE_NEEDLE + " 无时间"))

    def test_flexible_title_short_form(self) -> None:
        """人工漏写「发版」：【生产环境维护公告】"""
        raw = """【生产环境维护公告】
【维护类型】日常
【维护时间】2026-5-8 07:00 —— 2026-5-5 08:00
"""
        d = parse_release_maintenance_date(raw)
        self.assertEqual(d, dt.date(2026, 5, 8))

    def test_release_title_present(self) -> None:
        self.assertTrue(release_title_present("prefix " + RELEASE_TITLE_NEEDLE))
        self.assertTrue(release_title_present("【生产环境维护公告】"))
        self.assertFalse(release_title_present("【生产环境公告】"))
        self.assertFalse(release_title_present("普通通知"))

    def test_title_without_maintenance_keyword_rejected(self) -> None:
        raw = "【生产环境公告】\n【维护时间】2026-5-8"
        self.assertIsNone(parse_release_maintenance_date(raw))

    @patch(
        "core.cron_thinker.load_bios_settings",
        return_value={
            "enabled": True,
            "day_offset": 1,
            "hour_beijing": 10,
            "minute_beijing": 0,
        },
    )
    def test_next_day_10am_beijing(self, _mock_load) -> None:
        run_at = compute_smoke_run_at_beijing(dt.date(2026, 5, 5))
        self.assertEqual(run_at.date(), dt.date(2026, 5, 6))
        self.assertEqual((run_at.hour, run_at.minute), (10, 0))
        self.assertEqual(str(run_at.tzinfo), "Asia/Shanghai")

    def test_job_id(self) -> None:
        self.assertEqual(job_id_for_maintenance_day(dt.date(2026, 5, 5)), "smoke_test_2026-05-05")


if __name__ == "__main__":
    unittest.main()
