"""
Ray Actors - 已迁移至 core/skills/，此处保留向后兼容
"""

from core.skills.base_skill import BaseSkillActor
from core.skills.sentinel import SentinelActor

__all__ = ["BaseSkillActor", "SentinelActor"]
