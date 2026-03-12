"""
技能执行集成测试
Skill Execution Integration Tests
"""

import pytest
from core.runtime.skill_registry import SkillRegistry
from core.runtime.skill_runner import SkillRunner


@pytest.mark.asyncio
async def test_skill_registry_load():
    """测试技能注册表加载"""
    registry = SkillRegistry()
    
    # 加载所有技能（如果没有技能，应该返回0）
    count = await registry.load_all_skills()
    
    assert isinstance(count, int)
    assert count >= 0


@pytest.mark.asyncio
async def test_list_skills():
    """测试列出技能"""
    registry = SkillRegistry()
    
    skills = await registry.list_skills()
    
    assert isinstance(skills, list)
    # 如果没有技能，应该是空列表
    # 如果有技能，应该包含技能信息


@pytest.mark.asyncio
async def test_skill_runner_init():
    """测试技能运行器初始化"""
    registry = SkillRegistry()
    runner = SkillRunner(registry)
    
    assert runner.registry is not None
    assert runner.sandboxes is not None
