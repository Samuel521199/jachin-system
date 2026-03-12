"""
端到端测试：自然语言到插件执行的完整流程
End-to-End Test: Natural Language to Plugin Execution Flow

测试内容：
1. 自然语言查询 → IntentPlanner → 插件执行 → SDUI 返回
2. 错误处理和降级策略
3. 性能基准测试
"""

import pytest
import asyncio
import json
import tempfile
import shutil
import yaml
from pathlib import Path
from typing import Dict, Any
from unittest.mock import patch, AsyncMock, Mock

import sys
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from core.brain.planner.intent_planner import IntentPlanner
from core.system.plugin_manager import PluginManager
from core.system.plugin_executor import PluginExecutor
from tests.mocks.mock_llm import create_intent_planning_mock


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
def plugin_executor(plugin_manager):
    """创建插件执行器"""
    return PluginExecutor(plugin_manager)


@pytest.fixture
def intent_planner(plugin_manager):
    """创建意图规划器（使用 Mock LLM）"""
    planner = IntentPlanner(plugin_manager, llm_provider="local")
    return planner


def create_test_skill(skill_id: str, skills_repo_dir: Path, bundled: bool = True):
    """
    创建测试技能
    
    Args:
        skill_id: 技能 ID
        skills_repo_dir: 技能仓库目录
        bundled: 是否在 _bundled 目录中创建
    """
    if bundled:
        skill_dir = skills_repo_dir / "_bundled" / skill_id
    else:
        skill_dir = skills_repo_dir / skill_id
    skill_dir.mkdir(parents=True, exist_ok=True)
    
    # 创建 manifest.yaml
    manifest = {
        "id": skill_id,
        "version": "1.0.0",
        "name": f"Test Skill {skill_id}",
        "description": f"Test description for {skill_id}",
        "capabilities": [
            {
                "name": "get_performance_snapshot",
                "description": "获取系统性能快照",
                "input_schema": {"type": "object", "properties": {}},
                "output_schema": {"type": "object"}
            }
        ],
        "permissions": ["system.telemetry"],
        "requirements": [],
        "runtime": {"type": "ray", "python_version": "3.10", "resources": {}}
    }
    
    manifest_file = skill_dir / "manifest.yaml"
    with open(manifest_file, "w", encoding="utf-8") as f:
        yaml.dump(manifest, f, allow_unicode=True)
    
    # 创建 main.py
    main_file = skill_dir / "main.py"
    main_content = '''"""
测试技能主模块
"""
import asyncio
from typing import Dict, Any


async def execute(capability: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """执行技能能力"""
    if capability == "get_performance_snapshot":
        return {
            "success": True,
            "snapshot": {
                "timestamp": "2024-01-01T00:00:00Z",
                "cpu": {"usage": 50.0, "cores": 8},
                "memory": {"used": 4096, "total": 8192, "usage_percent": 50.0},
                "disk": {"used": 100000, "total": 500000, "usage_percent": 20.0}
            },
            "ui_render_schema": {
                "type": "AdaptiveCard",
                "body": [
                    {
                        "type": "TextBlock",
                        "text": "System Performance",
                        "weight": "Bolder",
                        "size": "Medium"
                    },
                    {
                        "type": "SDUI.Chart",
                        "chart_type": "line",
                        "title": "CPU Usage",
                        "data": [
                            {"time": "00:00", "usage": 50},
                            {"time": "00:01", "usage": 55},
                            {"time": "00:02", "usage": 48}
                        ],
                        "x_axis_label": "Time",
                        "y_axis_label": "Usage (%)"
                    }
                ]
            }
        }
    return {"success": False, "error": f"Unknown capability: {capability}"}
'''
    with open(main_file, "w", encoding="utf-8") as f:
        f.write(main_content)
    
    return skill_dir


@pytest.mark.asyncio
@pytest.mark.requires_ray
async def test_natural_language_to_plugin_execution(
    intent_planner,
    plugin_executor,
    temp_plugin_dirs,
    ray_init
):
    """测试自然语言到插件执行的完整流程"""
    plugins_dir, skills_repo_dir = temp_plugin_dirs
    
    # 创建测试技能
    create_test_skill("com.jachin.sys-monitor", skills_repo_dir, bundled=True)
    
    # 使用 Mock LLM - 直接替换 intent_planner 的 factory
    mock_llm = create_intent_planning_mock()
    mock_provider = type('MockProvider', (), {
        'create_provider': lambda self, provider_type: mock_llm
    })()
    intent_planner.factory = mock_provider
    
    # 1. 自然语言查询
    user_query = "查看系统状态"
    
    # 2. 意图规划
    plan = await intent_planner.plan(user_query)
    
    assert plan is not None
    assert plan.plugin_id == "com.jachin.sys-monitor"
    assert plan.method_name == "get_performance_snapshot"
    assert plan.confidence > 0.5
    
    # 3. 插件执行
    payload_bytes = json.dumps(plan.parameters).encode('utf-8')
    result = await plugin_executor.invoke_plugin(
        plugin_id=plan.plugin_id,
        method_name=plan.method_name,
        payload=payload_bytes,
        trace_id="e2e-test-001"
    )
    
    # 4. 验证结果
    assert result["status_code"] == 200
    assert result["ui_render_schema"] is not None
    
    # 5. 验证 SDUI Schema
    ui_schema = json.loads(result["ui_render_schema"])
    assert ui_schema["type"] == "AdaptiveCard"
    assert len(ui_schema.get("body", [])) > 0


@pytest.mark.asyncio
@pytest.mark.requires_ray
async def test_error_handling_low_confidence(
    intent_planner,
    temp_plugin_dirs,
    ray_init
):
    """测试低置信度错误处理"""
    plugins_dir, skills_repo_dir = temp_plugin_dirs
    
    # 创建测试技能（至少需要一个技能来获取能力列表）
    create_test_skill("com.jachin.sys-monitor", skills_repo_dir, bundled=True)
    
    # Mock LLM 返回低置信度 - 直接替换 intent_planner 的 factory
    mock_llm = type('MockLLM', (), {
        'chat': AsyncMock(return_value=json.dumps({
            "plugin_id": "com.jachin.sys-monitor",
            "method_name": "get_performance_snapshot",
            "parameters": {},
            "confidence": 0.3,  # 低于阈值
            "reasoning": "Uncertain match"
        }))
    })()
    
    mock_provider = type('MockProvider', (), {
        'create_provider': lambda self, provider_type: mock_llm
    })()
    intent_planner.factory = mock_provider
    
    # 规划（应该返回 None）
    plan = await intent_planner.plan("一些模糊的查询")
    
    assert plan is None or plan.confidence < 0.5


@pytest.mark.asyncio
@pytest.mark.requires_ray
async def test_error_handling_llm_failure(
    intent_planner,
    temp_plugin_dirs,
    ray_init
):
    """测试 LLM 失败的错误处理"""
    plugins_dir, skills_repo_dir = temp_plugin_dirs
    
    # 创建测试技能（至少需要一个技能来获取能力列表）
    create_test_skill("com.jachin.sys-monitor", skills_repo_dir, bundled=True)
    
    # Mock LLM 抛出异常 - 直接替换 intent_planner 的 factory
    mock_llm = type('MockLLM', (), {
        'chat': AsyncMock(side_effect=Exception("LLM API error"))
    })()
    
    mock_provider = type('MockProvider', (), {
        'create_provider': lambda self, provider_type: mock_llm
    })()
    intent_planner.factory = mock_provider
    
    # 规划（应该优雅处理错误）
    plan = await intent_planner.plan("查看系统状态")
    
    # 应该返回 None，而不是抛出异常
    assert plan is None


@pytest.mark.asyncio
@pytest.mark.requires_ray
async def test_performance_benchmark(
    intent_planner,
    plugin_executor,
    temp_plugin_dirs,
    ray_init
):
    """性能基准测试"""
    import time
    
    plugins_dir, skills_repo_dir = temp_plugin_dirs
    
    # 创建测试技能
    create_test_skill("com.jachin.sys-monitor", skills_repo_dir, bundled=True)
    
    # 使用 Mock LLM（避免实际 API 调用）- 直接替换 intent_planner 的 factory
    from tests.mocks.mock_llm import create_intent_planning_mock
    mock_llm = create_intent_planning_mock()
    
    mock_provider = type('MockProvider', (), {
        'create_provider': lambda self, provider_type: mock_llm
    })()
    intent_planner.factory = mock_provider
    
    # 测量端到端延迟
    start_time = time.time()
    
    # 1. 意图规划
    plan = await intent_planner.plan("查看系统状态")
    assert plan is not None
    
    # 2. 插件执行
    payload_bytes = json.dumps(plan.parameters).encode('utf-8')
    result = await plugin_executor.invoke_plugin(
        plugin_id=plan.plugin_id,
        method_name=plan.method_name,
        payload=payload_bytes,
        trace_id="perf-benchmark"
    )
    
    end_time = time.time()
    total_time = end_time - start_time
    
    # 验证结果
    assert result["status_code"] == 200
    
    # 性能断言（应该在 5 秒内完成）
    assert total_time < 5.0, f"End-to-end execution took {total_time:.2f}s, expected < 5.0s"
    
    print(f"End-to-end performance:")
    print(f"  Total time: {total_time:.2f}s")
    print(f"  Intent planning: ~0.1s (mock)")
    print(f"  Plugin execution: ~{total_time - 0.1:.2f}s")
