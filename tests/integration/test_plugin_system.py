"""
集成测试：插件系统完整流程
Integration Test: Plugin System Complete Flow

测试内容：
1. 插件安装和管理
2. InvokePlugin RPC 调用
3. StreamPlugin RPC 调用
4. 权限验证
5. Actor 生命周期管理
"""

import pytest
import asyncio
import json
import tempfile
import shutil
from pathlib import Path
from typing import Dict, Any

import ray

# 添加项目根目录到 Python 路径
import sys
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from core.system.plugin_manager import PluginManager
from core.system.plugin_executor import PluginExecutor
from core.system.permission_enforcer import PermissionEnforcer
from core.transport.gateway import JachinLinkGatewayServicer
from common.schemas.manifest import PluginManifest, PriceInfo, PriceType, Permission


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
def permission_enforcer():
    """创建权限执行器"""
    return PermissionEnforcer()


@pytest.fixture
def plugin_executor(plugin_manager, permission_enforcer):
    """创建插件执行器"""
    return PluginExecutor(plugin_manager, permission_enforcer)


@pytest.fixture
def gateway_servicer(plugin_executor):
    """创建 Gateway Servicer"""
    return JachinLinkGatewayServicer(plugin_executor=plugin_executor)


@pytest.fixture(scope="module", autouse=True)
def ray_init():
    """初始化 Ray（模块级别，所有测试共享）"""
    if not ray.is_initialized():
        ray.init(ignore_reinit_error=True, num_cpus=2)
    yield
    # 注意：不在这里 shutdown，让 pytest 管理 Ray 生命周期


def create_test_plugin(
    plugin_id: str,
    skills_repo_dir: Path,
    permissions: list = None,
    has_stream_method: bool = False
) -> Path:
    """
    创建测试插件

    Args:
        plugin_id: 插件 ID
        skills_repo_dir: 技能仓库目录
        permissions: 权限列表
        has_stream_method: 是否包含流式方法

    Returns:
        插件目录路径
    """
    plugin_dir = skills_repo_dir / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)

    # 创建 manifest.yaml
    manifest = {
        "id": plugin_id,
        "name": f"Test Plugin {plugin_id}",
        "version": "1.0.0",
        "description": "Test plugin for integration testing",
        "author": "Test Author",
        "price": {
            "type": "free"
        },
        "permissions": permissions or [],
        "runtime": {
            "resources": {
                "cpu": 1,
                "gpu": False
            }
        },
        "requirements": []
    }

    manifest_file = plugin_dir / "manifest.yaml"
    import yaml
    with open(manifest_file, 'w', encoding='utf-8') as f:
        yaml.dump(manifest, f, allow_unicode=True)

    # 创建 main.py
    main_py_content = f'''
import ray
from typing import Dict, Any

@ray.remote
class PluginActor:
    def __init__(self, plugin_id: str, manifest: dict):
        self.plugin_id = plugin_id
        self.manifest = manifest

    def hello(self, payload: Dict[str, Any], trace_id: str = None) -> Dict[str, Any]:
        """简单的 hello 方法"""
        name = payload.get("name", "World")
        return {{
            "success": True,
            "data": {{"message": f"Hello, {{name}}!"}},
            "ui_schema": {{
                "type": "AdaptiveCard",
                "body": [
                    {{
                        "type": "TextBlock",
                        "text": f"Hello, {{name}}!"
                    }}
                ]
            }}
        }}

    def process_data(self, payload: Dict[str, Any], trace_id: str = None) -> Dict[str, Any]:
        """处理数据方法"""
        data = payload.get("data", {{}})
        result = {{"processed": True, "data": data}}
        return {{
            "success": True,
            "data": result
        }}
'''

    if has_stream_method:
        main_py_content += '''
    def process_data_stream(self, payload: Dict[str, Any], trace_id: str = None):
        """流式处理方法"""
        data = payload.get("data", [])
        for i, item in enumerate(data):
            yield {
                "chunk_index": i,
                "data": item,
                "done": i == len(data) - 1
            }
'''

    main_py_file = plugin_dir / "main.py"
    with open(main_py_file, 'w', encoding='utf-8') as f:
        f.write(main_py_content)

    return plugin_dir


@pytest.mark.asyncio
@pytest.mark.requires_ray
async def test_plugin_installation(plugin_manager, temp_plugin_dirs):
    """测试插件安装"""
    plugins_dir, skills_repo_dir = temp_plugin_dirs

    plugin_id = "com.test.simple-plugin"
    plugin_dir = create_test_plugin(plugin_id, skills_repo_dir)

    # 安装插件（简化：直接复制目录内容到插件目录）
    # 注意：实际安装需要创建 .jsp 文件并解压，这里简化测试
    installed_dir = plugins_dir / plugin_id
    installed_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(plugin_dir, installed_dir, dirs_exist_ok=True)

    # 验证插件已安装（通过直接读取 manifest.yaml）
    manifest_file = installed_dir / "manifest.yaml"
    assert manifest_file.exists()

    # 使用 PluginManager 读取 manifest
    # 注意：PluginManager.get_plugin_manifest 可能需要从 skills_repo 读取
    # 这里我们直接验证文件存在
    import yaml
    with open(manifest_file, 'r', encoding='utf-8') as f:
        manifest_data = yaml.safe_load(f)
        assert manifest_data["id"] == plugin_id


@pytest.mark.asyncio
@pytest.mark.requires_ray
async def test_invoke_plugin_success(
    plugin_executor,
    plugin_manager,
    temp_plugin_dirs,
    ray_init
):
    """测试成功调用插件"""
    plugins_dir, skills_repo_dir = temp_plugin_dirs

    plugin_id = "com.test.hello-plugin"
    plugin_dir = create_test_plugin(plugin_id, skills_repo_dir)

    # 注意：插件已经在 skills_repo_dir 中创建
    # PluginManager.get_plugin_manifest 现在会从文件系统读取 manifest.yaml

    # 调用插件
    payload = json.dumps({"name": "Test User"}).encode('utf-8')
    result = await plugin_executor.invoke_plugin(
        plugin_id=plugin_id,
        method_name="hello",
        payload=payload,
        trace_id="test-trace-001"
    )

    # 验证结果
    assert result["status_code"] == 200
    assert "payload" in result
    assert "ui_render_schema" in result

    # 解析 payload
    response_data = json.loads(result["payload"].decode('utf-8'))
    assert response_data["message"] == "Hello, Test User!"


@pytest.mark.asyncio
async def test_invoke_plugin_not_found(plugin_executor):
    """测试插件不存在的情况"""
    result = await plugin_executor.invoke_plugin(
        plugin_id="com.test.non-existent",
        method_name="hello",
        payload=b"{}",
        trace_id="test-trace-002"
    )

    assert result["status_code"] == 404
    assert "not found" in result["error_message"].lower()


@pytest.mark.asyncio
@pytest.mark.requires_ray
async def test_permission_enforcement(
    plugin_executor,
    plugin_manager,
    temp_plugin_dirs,
    ray_init
):
    """测试权限验证"""
    plugins_dir, skills_repo_dir = temp_plugin_dirs

    # 创建没有 file.write 权限的插件
    plugin_id = "com.test.no-file-write-plugin"
    plugin_dir = create_test_plugin(
        plugin_id,
        skills_repo_dir,
        permissions=[{"scope": "file.read"}]  # 只有读取权限
    )

    # 安装插件
    installed_dir = plugins_dir / plugin_id
    installed_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(plugin_dir, installed_dir, dirs_exist_ok=True)

    # 调用需要 file.write 权限的方法（根据方法名推断）
    payload = json.dumps({"data": "test"}).encode('utf-8')
    result = await plugin_executor.invoke_plugin(
        plugin_id=plugin_id,
        method_name="save_file",  # 这个方法名会推断需要 file.write 权限
        payload=payload,
        trace_id="test-trace-003"
    )

    # 应该返回 403 错误
    assert result["status_code"] == 403
    assert "permission" in result["error_message"].lower()


@pytest.mark.asyncio
@pytest.mark.requires_ray
async def test_stream_plugin(
    plugin_executor,
    plugin_manager,
    temp_plugin_dirs,
    ray_init
):
    """测试流式插件调用"""
    plugins_dir, skills_repo_dir = temp_plugin_dirs

    # 创建有流式方法的插件
    plugin_id = "com.test.stream-plugin"
    plugin_dir = create_test_plugin(
        plugin_id,
        skills_repo_dir,
        has_stream_method=True
    )

    # 安装插件
    installed_dir = plugins_dir / plugin_id
    installed_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(plugin_dir, installed_dir, dirs_exist_ok=True)

    # 调用流式方法
    payload = json.dumps({"data": [1, 2, 3, 4, 5]}).encode('utf-8')

    # 注意：这里我们直接测试 plugin_executor，而不是 gateway
    # Gateway 的 StreamPlugin 测试需要 gRPC 上下文，更复杂

    # 获取 Actor
    manifest = plugin_manager.get_plugin_manifest(plugin_id)
    actor_handle = await plugin_executor._get_or_create_plugin_actor(plugin_id, manifest)

    assert actor_handle is not None

    # 调用流式方法
    payload_dict = json.loads(payload.decode('utf-8'))
    stream_method = getattr(actor_handle, "process_data_stream")
    result_refs = stream_method.remote(payload_dict, trace_id="test-trace-004")

    # 收集流式结果
    chunks = []
    try:
        # 简化：直接获取结果（实际应该使用 _stream_from_actor）
        result = await asyncio.wait_for(
            asyncio.to_thread(ray.get, result_refs),
            timeout=10.0
        )
        # 如果返回的是生成器，需要迭代
        if hasattr(result, '__iter__'):
            for chunk in result:
                chunks.append(chunk)
        else:
            chunks.append(result)
    except Exception as e:
        pytest.fail(f"Stream plugin failed: {e}")

    # 验证结果
    assert len(chunks) > 0


@pytest.mark.asyncio
@pytest.mark.requires_ray
async def test_actor_lifecycle(
    plugin_executor,
    plugin_manager,
    temp_plugin_dirs,
    ray_init
):
    """测试 Actor 生命周期管理"""
    plugins_dir, skills_repo_dir = temp_plugin_dirs

    plugin_id = "com.test.lifecycle-plugin"
    plugin_dir = create_test_plugin(plugin_id, skills_repo_dir)

    # 安装插件
    installed_dir = plugins_dir / plugin_id
    installed_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(plugin_dir, installed_dir, dirs_exist_ok=True)

    # 第一次调用（创建 Actor）
    manifest = plugin_manager.get_plugin_manifest(plugin_id)
    actor1 = await plugin_executor._get_or_create_plugin_actor(plugin_id, manifest)
    assert actor1 is not None

    # 第二次调用（应该复用同一个 Actor）
    actor2 = await plugin_executor._get_or_create_plugin_actor(plugin_id, manifest)
    assert actor2 is actor1  # 应该是同一个 Actor

    # 清理
    await plugin_executor.cleanup()

    # 验证 Actor 已清理
    assert plugin_id not in plugin_executor.plugin_actors


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
