"""
BI 每日战报 Skill — 技能大脑

设计规范: docs/bi_daily_report/
主入口: main_skill.run_bi_daily_report(config)
Lark 对话触发: main_skill.is_bi_analysis_intent(text) 供 agent 预检
"""
from l3_node.primitives.skills.bi.bi_daily_report.main_skill import is_bi_analysis_intent, run_bi_daily_report

__all__ = ["is_bi_analysis_intent", "run_bi_daily_report"]
