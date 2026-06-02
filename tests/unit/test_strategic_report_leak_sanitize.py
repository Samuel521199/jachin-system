"""战略战报 LLM 泄漏/占位符检测与截断。"""
from __future__ import annotations

import unittest

from l3_node.primitives.skills.bi.bi_daily_report.strategic_report import (
    _ensure_deliverable_strategic_report,
    _finalize_strategic_report_output,
    _is_report_corrupted,
    _polish_strategic_report_prose,
    _smart_truncate_corrupted_report,
    _truncate_at_first_corrupted_section,
)

_DNU_LOOP_SUFFIX = """
### 🟡异动二:`meta_ads06`渠道质量存疑 (DNU占比过高)

*   **观测现象**:该渠道贡献了当日近八成新增 (`DNU=`, DNU=), DNU=), DNU=), DNU=), DNU=), DNU=), DNU=), DNU=), DNU=), DNU=) 。DNU/D AU比例异常高 (>57%) 。D NU/D AU比例异常高 (>57%) 。D NU/D AU比例异常高 (>57%) 。
"""

_GOOD_PREFIX = """# 📊 BI 战略分析（2026-05-30）

## 一、执行摘要
- DAU 正常

## 二、红榜与黑榜
- 红：留存稳定

## 三、分维度数据解读
- 用户侧无异常

## 四、重点异动归因树
- 归因 A

## 五、跨部门行动清单
- [🟡高优 - Haku] 风控扫描
"""

_CORRUPT_SUFFIX = """
---

## ---
## ❓ ##六、待澄清与数据需求

| # | 需求事项 | 责任方 | 优先级 | 说明 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|--- | --- | --- | --- | --- |
| 1 | IAA 指标 | Ethan | 高 | 补齐 eCPM **(截断)** ... **(续)** ... **(完整)** ...

*(Note: The table above is a placeholder for the actual table structure required by the prompt's formatting rules)*

*(Self-Correction during thought process: The prompt requires a specific table format)*

*(Final Check on Constraints)*:

*(Ready to Output)*
"""


class TestStrategicReportLeakSanitize(unittest.TestCase):
    def test_detects_user_sample_corruption(self) -> None:
        raw = _GOOD_PREFIX + _CORRUPT_SUFFIX
        self.assertTrue(_is_report_corrupted(raw))

    def test_truncates_at_section6(self) -> None:
        raw = _GOOD_PREFIX + _CORRUPT_SUFFIX
        trimmed = _truncate_at_first_corrupted_section(raw)
        self.assertIn("## 五、跨部门行动清单", trimmed)
        self.assertNotIn("##六、待澄清", trimmed.replace(" ", ""))
        self.assertNotIn("Self-Correction", trimmed)

    def test_finalize_strips_and_adds_notice(self) -> None:
        raw = _GOOD_PREFIX + _CORRUPT_SUFFIX
        text, was_bad = _finalize_strategic_report_output(raw)
        self.assertTrue(was_bad)
        self.assertNotIn("Self-Correction", text)
        self.assertIn("已自动截断", text)

    def test_ensure_deliverable_clean_report_unchanged(self) -> None:
        good = _GOOD_PREFIX + "\n## 六、待澄清与数据需求\n| # | 事项 | 责任方 | 说明 |\n|---|---|---|---|\n| 1 | IAA | Ethan | 未提供 |\n"
        out = _ensure_deliverable_strategic_report(good)
        self.assertNotIn("已自动截断", out)
        self.assertIn("## 六、待澄清", out)

    def test_detects_dnu_loop_corruption(self) -> None:
        raw = _GOOD_PREFIX + _DNU_LOOP_SUFFIX
        self.assertTrue(_is_report_corrupted(raw))

    def test_smart_truncate_dnu_loop_keeps_section4_head(self) -> None:
        raw = _GOOD_PREFIX + _DNU_LOOP_SUFFIX
        out = _smart_truncate_corrupted_report(raw)
        self.assertIn("## 四、重点异动归因树", out)
        self.assertNotIn("DNU=), DNU=)", out)
        self.assertTrue(
            "截断" in out or "### 🟡异动二" not in out or "渠道 DNU 枚举异常截断" in out
        )

    def test_polish_preserves_visual_hierarchy(self) -> None:
        raw = """# 📊 BI 增长战报（数据日：2026-06-01）

---

## 一、大盘晴雨表 —— 今天我们真的增长了吗？

定调：今天大盘呈现虚胖特征。

### 人话结论
- **DAU** 4,652（环比+7.16%），渠道 meta_ads06。

**数据源**：stats_user_dau.
"""
        out = _polish_strategic_report_prose(raw)
        self.assertNotIn("**", out)
        self.assertIn("DAU", out)
        self.assertIn("🎯 【定调】", out)
        self.assertIn("📊 【结论】", out)
        self.assertNotIn("---", out)
        self.assertNotIn("`", out)
        self.assertIn("一、大盘晴雨表", out)
        self.assertNotIn("stats_user_dau", out)


if __name__ == "__main__":
    unittest.main()
