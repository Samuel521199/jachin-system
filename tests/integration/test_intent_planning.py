"""
集成测试：意图规划器（Intent Planner）
Integration Test: Intent Planner

测试内容：
1. 自然语言查询到插件匹配
2. 能力列表获取和缓存
3. LLM 响应解析
4. 置信度阈值过滤
5. 错误处理和降级策略
"""

import pytest
import asyncio
import json
import tempfile
import shutil
from pathlib import Path
from typing import Dict, Any
from unittest.mock import Mock, AsyncMock, patch

import sys
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from core.brain.planner.intent_planner import IntentPlanner, ExecutionPlan
from core.system.plugin_manager import PluginManager


@pytest.fixture
def temp_plugin_dirs():
    """创建临时插件目录"""
    plugins_dir = tempfile.mkdtemp(prefix="jachin_plugins_")
    skills_repo_dir = tempfile.mkdtemp(prefix="jachin_skills_")
    
    yield Path(plugins_dir), Path(skills_repo_dir)
    
    # 清理
    shutil.rmtree(plugins_dir, ignore_errors=True)
    shutil.rmtree(skills_repo_dir, ignore_errors=True)


@pytest.fixture
def plugin_manager(temp_plugin_dirs):
    """创建插件管理器"""
    plugins_dir, skills_repo_dir = temp_plugin_dirs
    return PluginManager(plugins_dir, skills_repo_dir)


@pytest.fixture
def intent_planner(plugin_manager):
    """创建意图规划器"""
    return IntentPlanner(plugin_manager, llm_provider="local")


def create_test_skill(skill_id: str, skills_repo_dir: Path):
    """创建测试技能"""
    skill_dir = skills_repo_dir / "_bundled" / skill_id
    skill_dir.mkdir(parents=True, exist_ok=True)
    
    # 创建 manifest.yaml
    manifest = {
        "id": skill_id,
        "version": "1.0.0",
        "name": f"Test Skill {skill_id}",
        "description": f"Test description for {skill_id}",
        "capabilities": [
            {
                "name": "get_status",
                "description": "获取系统状态",
                "input_schema": {"type": "object", "properties": {}},
                "output_schema": {"type": "object"}
            },
            {
                "name": "check_performance",
                "description": "检查系统性能",
                "input_schema": {"type": "object", "properties": {}},
                "output_schema": {"type": "object"}
            }
        ],
        "permissions": ["system.telemetry"],
        "requirements": [],
        "runtime": {"type": "ray", "python_version": "3.10", "resources": {}}
    }
    
    import yaml
    manifest_file = skill_dir / "manifest.yaml"
    with open(manifest_file, "w", encoding="utf-8") as f:
        yaml.dump(manifest, f, allow_unicode=True)
    
    return skill_dir


@pytest.mark.asyncio
async def test_get_capabilities(intent_planner, temp_plugin_dirs, plugin_manager):
    """测试获取能力列表"""
    plugins_dir, skills_repo_dir = temp_plugin_dirs
    
    # 创建测试技能
    skill_id = "com.test.skill1"
    create_test_skill(skill_id, skills_repo_dir)
    
    # 获取能力列表
    capabilities = await intent_planner._get_capabilities()
    
    # 验证
    assert len(capabilities) >= 2  # 至少有两个能力
    assert any(cap["plugin_id"] == skill_id for cap in capabilities)
    assert any(cap["method_name"] == "get_status" for cap in capabilities)
    assert any(cap["method_name"] == "check_performance" for cap in capabilities)


@pytest.mark.asyncio
async def test_capabilities_cache(intent_planner, temp_plugin_dirs, plugin_manager):
    """测试能力列表缓存"""
    plugins_dir, skills_repo_dir = temp_plugin_dirs
    
    # 创建测试技能
    skill_id = "com.test.skill1"
    create_test_skill(skill_id, skills_repo_dir)
    
    # 第一次获取（应该扫描）
    capabilities1 = await intent_planner._get_capabilities()
    
    # 第二次获取（应该使用缓存）
    capabilities2 = await intent_planner._get_capabilities()
    
    # 验证缓存
    assert capabilities1 == capabilities2
    assert intent_planner._capabilities_cache is not None
    
    # 清除缓存后应该重新扫描
    intent_planner.clear_cache()
    assert intent_planner._capabilities_cache is None


@pytest.mark.asyncio
async def test_format_capabilities(intent_planner, temp_plugin_dirs, plugin_manager):
    """测试能力列表格式化"""
    plugins_dir, skills_repo_dir = temp_plugin_dirs
    
    # 创建测试技能
    skill_id = "com.test.skill1"
    create_test_skill(skill_id, skills_repo_dir)
    
    # 获取能力列表
    capabilities = await intent_planner._get_capabilities()
    
    # 格式化
    formatted = intent_planner._format_capabilities(capabilities)
    
    # 验证格式
    assert skill_id in formatted
    assert "get_status" in formatted
    assert "check_performance" in formatted
    assert "获取系统状态" in formatted or "系统状态" in formatted


@pytest.mark.asyncio
async def test_parse_llm_response_valid(intent_planner):
    """测试解析有效的 LLM 响应"""
    llm_response = json.dumps({
        "plugin_id": "com.test.skill1",
        "method_name": "get_status",
        "parameters": {},
        "confidence": 0.95,
        "reasoning": "User wants to check system status"
    })
    
    plan = intent_planner._parse_llm_response(llm_response)
    
    assert plan is not None
    assert plan.plugin_id == "com.test.skill1"
    assert plan.method_name == "get_status"
    assert plan.confidence == 0.95
    assert plan.reasoning == "User wants to check system status"


@pytest.mark.asyncio
async def test_parse_llm_response_low_confidence(intent_planner):
    """测试低置信度响应"""
    llm_response = json.dumps({
        "plugin_id": "com.test.skill1",
        "method_name": "get_status",
        "parameters": {},
        "confidence": 0.3,  # 低置信度
        "reasoning": "Uncertain match"
    })
    
    plan = intent_planner._parse_llm_response(llm_response)
    
    # 低置信度应该返回 None（在 plan 方法中会被过滤）
    assert plan is not None
    assert plan.confidence == 0.3


@pytest.mark.asyncio
async def test_parse_llm_response_missing_fields(intent_planner):
    """测试缺少必需字段的响应"""
    llm_response = json.dumps({
        "confidence": 0.95
        # 缺少 plugin_id 和 method_name
    })
    
    plan = intent_planner._parse_llm_response(llm_response)
    
    # 应该返回 None（因为缺少必需字段）
    assert plan is None


@pytest.mark.asyncio
async def test_parse_llm_response_invalid_json(intent_planner):
    """测试无效的 JSON 响应"""
    llm_response = "这不是有效的 JSON {"
    
    plan = intent_planner._parse_llm_response(llm_response)
    
    # 应该返回 None
    assert plan is None


@pytest.mark.asyncio
async def test_plan_with_mock_llm(intent_planner, temp_plugin_dirs, plugin_manager):
    """测试使用 Mock LLM 的规划"""
    plugins_dir, skills_repo_dir = temp_plugin_dirs
    
    # 创建测试技能
    skill_id = "com.test.skill1"
    create_test_skill(skill_id, skills_repo_dir)
    
    # Mock LLM - 直接替换 intent_planner 的 factory
    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock(return_value=json.dumps({
        "plugin_id": skill_id,
        "method_name": "get_status",
        "parameters": {},
        "confidence": 0.95,
        "reasoning": "User wants to check system status"
    }))
    
    mock_provider = Mock()
    mock_provider.create_provider = Mock(return_value=mock_llm)
    intent_planner.factory = mock_provider
    
    # 规划
    plan = await intent_planner.plan("查看系统状态")
    
    # 验证
    assert plan is not None
    assert plan.plugin_id == skill_id
    assert plan.method_name == "get_status"
    assert plan.confidence == 0.95


@pytest.mark.asyncio
async def test_plan_low_confidence_filter(intent_planner, temp_plugin_dirs, plugin_manager):
    """测试低置信度过滤"""
    plugins_dir, skills_repo_dir = temp_plugin_dirs
    
    # 创建测试技能
    skill_id = "com.test.skill1"
    create_test_skill(skill_id, skills_repo_dir)
    
    # Mock LLM 返回低置信度 - 直接替换 intent_planner 的 factory
    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock(return_value=json.dumps({
        "plugin_id": skill_id,
        "method_name": "get_status",
        "parameters": {},
        "confidence": 0.3,  # 低于阈值 0.5
        "reasoning": "Uncertain match"
    }))
    
    mock_provider = Mock()
    mock_provider.create_provider = Mock(return_value=mock_llm)
    intent_planner.factory = mock_provider
    
    # 规划
    plan = await intent_planner.plan("一些不相关的查询")
    
    # 验证（应该返回 None，因为置信度低于阈值）
    assert plan is None


@pytest.mark.asyncio
async def test_plan_llm_error_handling(intent_planner, temp_plugin_dirs, plugin_manager):
    """测试 LLM 错误处理"""
    plugins_dir, skills_repo_dir = temp_plugin_dirs
    
    # 创建测试技能
    skill_id = "com.test.skill1"
    create_test_skill(skill_id, skills_repo_dir)
    
    # Mock LLM 抛出异常 - 直接替换 intent_planner 的 factory
    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock(side_effect=Exception("LLM API error"))
    
    mock_provider = Mock()
    mock_provider.create_provider = Mock(return_value=mock_llm)
    intent_planner.factory = mock_provider
    
    # 规划（应该优雅处理错误）
    plan = await intent_planner.plan("查看系统状态")
    
    # 验证（应该返回 None，而不是抛出异常）
    assert plan is None


@pytest.mark.asyncio
async def test_plan_no_capabilities(intent_planner, temp_plugin_dirs, plugin_manager):
    """测试没有可用能力的情况"""
    plugins_dir, skills_repo_dir = temp_plugin_dirs
    
    # 不创建任何技能
    
    # 规划
    plan = await intent_planner.plan("查看系统状态")
    
    # 验证（应该返回 None，因为没有可用能力）
    assert plan is None
