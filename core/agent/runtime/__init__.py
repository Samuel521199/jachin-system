"""
Agent Runtime - L3 技能加载与执行

智能加载路径：Standalone 零拷贝 vs Distributed HTTP 拉取
"""

from core.agent.runtime.loader import SkillLoader, load_skill_path

__all__ = ["SkillLoader", "load_skill_path"]
