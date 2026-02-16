"""
Skills - 技能 Actor 与基类 (v4.0)

原 core/brain/ray_actors/ 迁移至此
- BaseSkill: 技能基类（非 Actor），技能实现继承此类
- BaseSkillActor: Ray Actor 基类，仅用于需要 Actor 的场景
"""

from core.skills.base_skill import BaseSkill, BaseSkillActor
from core.skills.sentinel import SentinelActor

__all__ = ["BaseSkill", "BaseSkillActor", "SentinelActor"]
