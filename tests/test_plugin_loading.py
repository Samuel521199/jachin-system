"""
测试插件加载功能
验证预装技能能否正确加载并作为 Ray Actor 运行
"""

import pytest
import ray
import asyncio
from pathlib import Path
from typing import Dict, Any

from core.system.plugin_manager import PluginManager
from core.system.kernel import initialize_kernel, shutdown_kernel
from core.config import settings


@pytest.fixture(scope="module")
def ray_cluster():
    """初始化 Ray 集群（模块级别，所有测试共享）"""
    # 初始化 Ray（如果未初始化）
    if not ray.is_initialized():
        ray.init(ignore_reinit_error=True)

    yield

    # 清理（可选，因为测试结束后 Ray 会自动关闭）
    # ray.shutdown()


@pytest.fixture
def plugin_manager():
    """创建插件管理器实例"""
    plugins_dir = Path(settings.SKILLS_REPO_PATH).parent / "plugins"
    skills_repo_dir = Path(settings.SKILLS_REPO_PATH)

    return PluginManager(
        plugins_dir=plugins_dir,
        skills_repo_dir=skills_repo_dir
    )


@pytest.mark.asyncio
async def test_scan_bundled_skills(plugin_manager):
    """测试扫描预装技能"""
    skill_ids = plugin_manager.scan_bundled_skills()

    assert len(skill_ids) > 0, "应该发现至少一个预装技能"
    assert "com.jachin.sys-monitor" in skill_ids, "应该发现 sys-monitor 技能"
    assert "com.jachin.os-mate" in skill_ids, "应该发现 os-mate 技能"
    assert "com.jachin.files" in skill_ids, "应该发现 files 技能"

    print(f"✓ Found {len(skill_ids)} bundled skills: {skill_ids}")


@pytest.mark.asyncio
async def test_load_sys_monitor_skill(plugin_manager, ray_cluster):
    """测试加载 sys-monitor 技能并创建 Ray Actor"""
    skill_id = "com.jachin.sys-monitor"

    # 加载技能
    actor_handle = plugin_manager.load_skill(skill_id)

    assert actor_handle is not None, f"应该成功加载技能: {skill_id}"
    print(f"✓ Successfully loaded skill: {skill_id}")

    # 检查 Actor 状态
    status = await actor_handle.get_status.remote()
    assert status["skill_id"] == skill_id
    assert status["initialized"] is True
    assert "get_performance_snapshot" in status["capabilities"]
    print(f"✓ Actor status: {status}")

    # 健康检查
    health = await actor_handle.health_check.remote()
    assert health["status"] == "healthy"
    print(f"✓ Health check passed: {health}")


@pytest.mark.asyncio
async def test_execute_sys_monitor_capability(plugin_manager, ray_cluster):
    """测试执行 sys-monitor 的 get_performance_snapshot 能力"""
    skill_id = "com.jachin.sys-monitor"

    # 加载技能
    actor_handle = plugin_manager.load_skill(skill_id)
    assert actor_handle is not None

    # 执行能力
    result = await actor_handle.execute.remote(
        capability="get_performance_snapshot",
        params={}
    )

    assert result is not None, "应该返回结果"
    assert result.get("success") is True, f"执行应该成功: {result}"

    snapshot = result.get("snapshot")
    assert snapshot is not None, "应该包含性能快照数据"
    assert "timestamp" in snapshot, "快照应该包含时间戳"
    assert "cpu" in snapshot, "快照应该包含 CPU 信息"
    assert "memory" in snapshot, "快照应该包含内存信息"

    print(f"✓ Successfully executed capability:")
    print(f"  - CPU: {snapshot.get('cpu', {}).get('percent', 'N/A')}%")
    print(f"  - Memory: {snapshot.get('memory', {}).get('percent', 'N/A')}%")
    print(f"  - Timestamp: {snapshot.get('timestamp', 'N/A')}")


@pytest.mark.asyncio
async def test_load_all_bundled_skills(plugin_manager, ray_cluster):
    """测试加载所有预装技能"""
    skill_ids = plugin_manager.scan_bundled_skills()

    loaded_actors = {}
    for skill_id in skill_ids:
        actor_handle = plugin_manager.load_skill(skill_id)
        if actor_handle is not None:
            loaded_actors[skill_id] = actor_handle
            print(f"✓ Loaded: {skill_id}")
        else:
            print(f"✗ Failed to load: {skill_id}")

    assert len(loaded_actors) == len(skill_ids), "所有技能都应该成功加载"

    # 验证每个 Actor 都能响应
    for skill_id, actor_handle in loaded_actors.items():
        health = await actor_handle.health_check.remote()
        assert health["status"] == "healthy", f"{skill_id} should be healthy"
        print(f"✓ {skill_id} health check passed")


@pytest.mark.asyncio
async def test_skill_capability_validation(plugin_manager, ray_cluster):
    """测试技能能力验证"""
    skill_id = "com.jachin.sys-monitor"

    actor_handle = plugin_manager.load_skill(skill_id)
    assert actor_handle is not None

    # 检查能力列表
    capabilities = await actor_handle.get_capabilities.remote()
    assert len(capabilities) > 0, "应该至少有一个能力"

    # 检查是否支持特定能力
    has_capability = await actor_handle.has_capability.remote("get_performance_snapshot")
    assert has_capability is True, "应该支持 get_performance_snapshot"

    # 检查不支持的能力
    has_unknown = await actor_handle.has_capability.remote("unknown_capability")
    assert has_unknown is False, "不应该支持未知能力"

    print(f"✓ Capability validation passed")


if __name__ == "__main__":
    """直接运行测试（不通过 pytest）"""
    import sys

    # 初始化 Ray
    if not ray.is_initialized():
        ray.init(ignore_reinit_error=True)

    # 创建插件管理器
    from core.config import settings
    plugins_dir = Path(settings.SKILLS_REPO_PATH).parent / "plugins"
    skills_repo_dir = Path(settings.SKILLS_REPO_PATH)
    plugin_manager = PluginManager(
        plugins_dir=plugins_dir,
        skills_repo_dir=skills_repo_dir
    )

    async def run_tests():
        """运行测试"""
        print("=" * 60)
        print("Testing Plugin Loading")
        print("=" * 60)

        # 测试 1: 扫描预装技能
        print("\n[Test 1] Scanning bundled skills...")
        skill_ids = plugin_manager.scan_bundled_skills()
        print(f"Found {len(skill_ids)} skills: {skill_ids}")

        # 测试 2: 加载 sys-monitor
        print("\n[Test 2] Loading sys-monitor skill...")
        actor_handle = plugin_manager.load_skill("com.jachin.sys-monitor")
        if actor_handle:
            print("✓ Skill loaded successfully")

            # 测试 3: 执行能力
            print("\n[Test 3] Executing get_performance_snapshot...")
            result = await actor_handle.execute.remote(
                capability="get_performance_snapshot",
                params={}
            )

            if result.get("success"):
                snapshot = result.get("snapshot", {})
                print(f"✓ Execution successful")
                print(f"  CPU: {snapshot.get('cpu', {}).get('percent', 'N/A')}%")
                print(f"  Memory: {snapshot.get('memory', {}).get('percent', 'N/A')}%")
            else:
                print(f"✗ Execution failed: {result.get('error')}")
        else:
            print("✗ Failed to load skill")

        print("\n" + "=" * 60)
        print("Tests completed")
        print("=" * 60)

    # 运行异步测试
    asyncio.run(run_tests())

    # 关闭 Ray
    ray.shutdown()
