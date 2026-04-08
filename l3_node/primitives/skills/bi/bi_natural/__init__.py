"""
自然周/自然月留存对比 Skill — 抓取 raw_natural、推送两条 Lark 卡片（用户 / 付费）。
主入口: main_skill.run_bi_natural_retention(mode=...)
"""
from l3_node.primitives.skills.bi.bi_natural.main_skill import run_bi_natural_retention

__all__ = ["run_bi_natural_retention"]
