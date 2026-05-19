"""
ProactiveReporter — 主动汇报系统（§5 Layer 4）

无人值守期间按计划主动汇报：
- 日终总结（每日 23:55，包含今日执行统计、新学经验、明日预计任务）
- 重要任务完成通知
- 资源/异常告警（已在 awareness_loop 中触发）

环境变量
----------
JACHIN_DAILY_SUMMARY_DISABLE=1   关闭日终总结生成
"""
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("autonomy.proactive_reporter")


class ProactiveReporter:
    """无人值守期间的主动汇报生成器。"""

    async def generate_daily_summary(self, date: str = "") -> str:
        """
        生成日终总结，包含：
        - 今日执行任务统计（成功/失败/跳过）
        - Token 消耗统计
        - 新学到的经验（Experience JSONL 今日条目）
        - 明日预计执行任务（PersistedIntent 列表）
        - 需要用户关注的问题（连续失败意图）
        """
        import datetime
        import os
        if (os.environ.get("JACHIN_DAILY_SUMMARY_DISABLE") or "").strip().lower() in ("1", "true"):
            return ""

        if not date:
            date = datetime.datetime.now().strftime("%Y-%m-%d")

        lines: list[str] = [
            f"📊 **Jachin 日终总结 · {date}**",
            "",
        ]

        # 今日意图执行统计
        lines += self._build_intent_stats(date)

        # Token 消耗
        lines += self._build_token_stats()

        # 新学经验（Experience JSONL 今日条目数）
        lines += self._build_experience_stats(date)

        # 明日预计任务
        lines += self._build_upcoming_tasks()

        # 需要关注的问题
        lines += self._build_attention_items()

        return "\n".join(lines)

    def _build_intent_stats(self, date: str) -> list[str]:
        try:
            from l3_node.autonomy.intent_persister import get_intent_persister
            intents = get_intent_persister().list_all()
            today_ts_start = _date_to_ts(date)
            today_ts_end = today_ts_start + 86400.0
            executed_today = [
                i for i in intents
                if i.last_executed_at and today_ts_start <= i.last_executed_at < today_ts_end
            ]
            succeeded = sum(1 for i in executed_today if i.status != "failed")
            failed = sum(1 for i in executed_today if i.status == "failed")
            return [
                "**今日任务执行**",
                f"- 已执行：{len(executed_today)} 个意图",
                f"- 成功：{succeeded}，失败：{failed}",
                "",
            ]
        except Exception as e:
            logger.debug("[ProactiveReporter] intent stats error: %s", e)
            return []

    def _build_token_stats(self) -> list[str]:
        try:
            from l3_node.llm_budget import get_today_token_usage, get_token_day_budget
            used = get_today_token_usage()
            budget = get_token_day_budget()
            pct = int(used / budget * 100) if budget > 0 else 0
            return [
                "**Token 消耗**",
                f"- 今日用量：{used:,} tokens（{pct}% of {budget:,}）",
                "",
            ]
        except Exception:
            return []

    def _build_experience_stats(self, date: str) -> list[str]:
        try:
            import json
            import os
            from pathlib import Path
            home = Path(os.environ.get("JACHIN_HOME", "~/.jachin")).expanduser()
            exp_file = home / "workspace" / ".jachin_experience.jsonl"
            if not exp_file.exists():
                return []
            today_ts_start = _date_to_ts(date)
            today_ts_end = today_ts_start + 86400.0
            count = 0
            with exp_file.open(encoding="utf-8") as f:
                for line in f:
                    try:
                        obj = json.loads(line.strip())
                        ts = float(obj.get("created_at") or obj.get("timestamp") or 0)
                        if today_ts_start <= ts < today_ts_end:
                            count += 1
                    except Exception:
                        pass
            if count == 0:
                return []
            return [
                "**今日新经验**",
                f"- Experience RAG 新增：{count} 条程序记忆",
                "",
            ]
        except Exception:
            return []

    def _build_upcoming_tasks(self) -> list[str]:
        try:
            from l3_node.autonomy.intent_persister import get_intent_persister
            intents = get_intent_persister().list_all(enabled_only=True)
            upcoming = [i for i in intents if i.trigger.type in ("cron", "interval") and i.status == "active"]
            if not upcoming:
                return []
            lines = ["**明日预计执行**"]
            for i in upcoming[:5]:
                trigger_desc = ""
                if i.trigger.cron:
                    trigger_desc = f"cron: {i.trigger.cron}"
                elif i.trigger.interval_sec:
                    trigger_desc = f"每 {i.trigger.interval_sec}s"
                lines.append(f"- {i.description[:60]}（{trigger_desc}）")
            lines.append("")
            return lines
        except Exception:
            return []

    def _build_attention_items(self) -> list[str]:
        try:
            from l3_node.autonomy.intent_persister import get_intent_persister
            intents = get_intent_persister().list_all()
            problems = [i for i in intents if i.consecutive_failures >= 2]
            if not problems:
                return []
            lines = ["**⚠️ 需要关注**"]
            for i in problems:
                lines.append(
                    f"- 「{i.description[:40]}」已连续失败 {i.consecutive_failures} 次"
                )
            lines.append("")
            return lines
        except Exception:
            return []


def _date_to_ts(date_str: str) -> float:
    import datetime
    try:
        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        return dt.timestamp()
    except ValueError:
        return 0.0
