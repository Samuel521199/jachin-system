"""
性能测试：插件执行性能
Performance Test: Plugin Execution Performance

测试内容：
1. 插件执行延迟
2. 并发执行性能
3. Actor 创建和销毁性能
4. 内存使用情况
"""

import pytest
import asyncio
import time
import tempfile
import shutil
import yaml
from pathlib import Path
from typing import List, Dict, Any
import sys

import ray

project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from core.system.plugin_manager import PluginManager
from core.system.plugin_executor import PluginExecutor


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
                    }
                ]
            }
        }
    return {"success": False, "error": f"Unknown capability: {capability}"}
'''
    with open(main_file, "w", encoding="utf-8") as f:
        f.write(main_content)
    
    return skill_dir


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
def plugin_executor(temp_plugin_dirs):
    """创建插件执行器"""
    plugins_dir, skills_repo_dir = temp_plugin_dirs
    plugin_manager = PluginManager(plugins_dir, skills_repo_dir)
    return PluginExecutor(plugin_manager)


@pytest.mark.asyncio
@pytest.mark.requires_ray
async def test_plugin_execution_latency(plugin_executor, temp_plugin_dirs, ray_init):
    """测试插件执行延迟"""
    plugins_dir, skills_repo_dir = temp_plugin_dirs
    
    # 创建测试技能
    plugin_id = "com.jachin.sys-monitor"
    create_test_skill(plugin_id, skills_repo_dir)
    
    # 测量执行时间
    start_time = time.time()
    
    result = await plugin_executor.invoke_plugin(
        plugin_id=plugin_id,
        method_name="get_performance_snapshot",
        payload=b'{}',
        trace_id="perf-test-001"
    )
    
    end_time = time.time()
    execution_time = end_time - start_time
    
    # 验证结果
    assert result["status_code"] == 200
    
    # 性能断言（应该在 5 秒内完成）
    assert execution_time < 5.0, f"Execution took {execution_time:.2f}s, expected < 5.0s"
    
    print(f"Plugin execution latency: {execution_time:.2f}s")


@pytest.mark.asyncio
@pytest.mark.requires_ray
async def test_concurrent_plugin_execution(plugin_executor, temp_plugin_dirs, ray_init):
    """测试并发插件执行性能"""
    plugins_dir, skills_repo_dir = temp_plugin_dirs

    # 创建测试技能
    plugin_id = "com.jachin.sys-monitor"
    create_test_skill(plugin_id, skills_repo_dir)
    num_concurrent = 10
    
    # 并发执行
    start_time = time.time()
    
    tasks = [
        plugin_executor.invoke_plugin(
            plugin_id=plugin_id,
            method_name="get_performance_snapshot",
            payload=b'{}',
            trace_id=f"concurrent-test-{i}"
        )
        for i in range(num_concurrent)
    ]
    
    results = await asyncio.gather(*tasks)
    
    end_time = time.time()
    total_time = end_time - start_time
    avg_time = total_time / num_concurrent
    
    # 验证结果
    assert all(r["status_code"] == 200 for r in results)
    
    # 性能断言（并发应该比串行快）
    assert total_time < num_concurrent * 2.0, f"Concurrent execution took {total_time:.2f}s, expected < {num_concurrent * 2.0}s"
    
    print(f"Concurrent execution ({num_concurrent} requests):")
    print(f"  Total time: {total_time:.2f}s")
    print(f"  Average time: {avg_time:.2f}s")
    print(f"  Throughput: {num_concurrent / total_time:.2f} req/s")


@pytest.mark.asyncio
@pytest.mark.requires_ray
async def test_actor_creation_performance(plugin_executor, temp_plugin_dirs, ray_init):
    """测试 Actor 创建性能"""
    plugins_dir, skills_repo_dir = temp_plugin_dirs

    # 创建测试技能
    plugin_id = "com.jachin.sys-monitor"
    create_test_skill(plugin_id, skills_repo_dir)
    
    # 清除现有 Actor
    if plugin_id in plugin_executor.plugin_actors:
        del plugin_executor.plugin_actors[plugin_id]
    
    # 测量创建时间
    start_time = time.time()
    
    # 第一次调用会创建 Actor
    result = await plugin_executor.invoke_plugin(
        plugin_id=plugin_id,
        method_name="get_performance_snapshot",
        payload=b'{}',
        trace_id="actor-creation-test"
    )
    
    end_time = time.time()
    creation_time = end_time - start_time
    
    # 验证结果
    assert result["status_code"] == 200
    assert plugin_id in plugin_executor.plugin_actors
    
    # 性能断言（Actor 创建应该在 3 秒内完成）
    assert creation_time < 3.0, f"Actor creation took {creation_time:.2f}s, expected < 3.0s"
    
    print(f"Actor creation time: {creation_time:.2f}s")
    
    # 第二次调用应该更快（使用现有 Actor）
    start_time = time.time()
    result2 = await plugin_executor.invoke_plugin(
        plugin_id=plugin_id,
        method_name="get_performance_snapshot",
        payload=b'{}',
        trace_id="actor-reuse-test"
    )
    end_time = time.time()
    reuse_time = end_time - start_time
    
    assert result2["status_code"] == 200
    assert reuse_time < creation_time, "Reusing actor should be faster"
    
    print(f"Actor reuse time: {reuse_time:.2f}s")
    print(f"Speedup: {creation_time / reuse_time:.2f}x")


@pytest.mark.asyncio
@pytest.mark.requires_ray
async def test_memory_usage(plugin_executor, temp_plugin_dirs, ray_init):
    """测试内存使用情况"""
    import psutil
    import os

    plugins_dir, skills_repo_dir = temp_plugin_dirs
    
    # 创建测试技能
    plugin_id = "com.jachin.sys-monitor"
    create_test_skill(plugin_id, skills_repo_dir)

    process = psutil.Process(os.getpid())
    initial_memory = process.memory_info().rss / 1024 / 1024  # MB
    
    # 执行多次调用
    for i in range(10):
        await plugin_executor.invoke_plugin(
            plugin_id=plugin_id,
            method_name="get_performance_snapshot",
            payload=b'{}',
            trace_id=f"memory-test-{i}"
        )
    
    final_memory = process.memory_info().rss / 1024 / 1024  # MB
    memory_increase = final_memory - initial_memory
    
    print(f"Memory usage:")
    print(f"  Initial: {initial_memory:.2f} MB")
    print(f"  Final: {final_memory:.2f} MB")
    print(f"  Increase: {memory_increase:.2f} MB")
    
    # 内存增长应该在合理范围内（< 100MB）
    assert memory_increase < 100, f"Memory increased by {memory_increase:.2f} MB, expected < 100 MB"


@pytest.mark.asyncio
@pytest.mark.requires_ray
async def test_large_payload_performance(plugin_executor, temp_plugin_dirs, ray_init):
    """测试大 payload 性能"""
    import json

    plugins_dir, skills_repo_dir = temp_plugin_dirs
    
    # 创建测试技能
    plugin_id = "com.jachin.sys-monitor"
    create_test_skill(plugin_id, skills_repo_dir)
    
    # 创建大 payload（1MB）
    large_data = {"data": "x" * (1024 * 1024)}
    large_payload = json.dumps(large_data).encode('utf-8')
    
    start_time = time.time()
    
    result = await plugin_executor.invoke_plugin(
        plugin_id=plugin_id,
        method_name="get_performance_snapshot",
        payload=large_payload,
        trace_id="large-payload-test"
    )
    
    end_time = time.time()
    execution_time = end_time - start_time
    
    # 验证结果
    assert result["status_code"] == 200
    
    # 大 payload 应该在合理时间内完成（< 10 秒）
    assert execution_time < 10.0, f"Large payload execution took {execution_time:.2f}s, expected < 10.0s"
    
    print(f"Large payload (1MB) execution time: {execution_time:.2f}s")
