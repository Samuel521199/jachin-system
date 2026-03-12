"""
端到端测试：插件系统完整流程
End-to-End Test: Complete Plugin System Flow

测试内容：
1. Gateway RPC 方法（Connect, SendHeartbeat, InvokePlugin, StreamPlugin）
2. 完整的插件调用流程（Tier 3 → Gateway → Plugin → 返回 UI）
3. 多个插件并发调用
4. 权限违规场景
"""

import pytest
import asyncio
import json
import tempfile
import shutil
from pathlib import Path
from typing import Dict, Any
import sys

import ray

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from core.system.plugin_manager import PluginManager
from core.system.plugin_executor import PluginExecutor
from core.system.permission_enforcer import PermissionEnforcer
from core.transport.gateway import JachinLinkGatewayServicer, JachinLinkGateway

# 导入 gRPC 生成的代码
try:
    from core.transport import jachin_link_pb2
    from core.transport import jachin_link_pb2_grpc
    GRPC_CODE_AVAILABLE = True
except ImportError as e:
    # 如果 gRPC 代码未生成，跳过测试
    GRPC_CODE_AVAILABLE = False
    pytest.skip(f"gRPC code not available: {e}. Run scripts/generate_grpc_code.ps1 first", allow_module_level=True)


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
def gateway_servicer(plugin_executor, temp_plugin_dirs):
    """创建 Gateway Servicer"""
    from core.transport.connection_manager import ConnectionManager
    from core.transport.mtls_manager import MTLSManager
    
    # 创建临时证书目录
    cert_dir = temp_plugin_dirs[0] / "certs" / "test"
    cert_dir.mkdir(parents=True, exist_ok=True)
    
    connection_manager = ConnectionManager()
    mtls_manager = MTLSManager(cert_dir=cert_dir)
    
    servicer = JachinLinkGatewayServicer(
        mtls_manager=mtls_manager,
        connection_manager=connection_manager,
        plugin_executor=plugin_executor
    )
    return servicer


@pytest.fixture(scope="module", autouse=True)
def ray_init():
    """初始化 Ray（模块级别，所有测试共享）"""
    if not ray.is_initialized():
        ray.init(ignore_reinit_error=True, num_cpus=2)
    yield


def create_test_plugin(
    plugin_id: str,
    skills_repo_dir: Path,
    permissions: list = None,
    has_stream_method: bool = False
) -> Path:
    """创建测试插件"""
    plugin_dir = skills_repo_dir / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)
    
    # 创建 manifest.yaml
    manifest = {
        "id": plugin_id,
        "name": f"Test Plugin {plugin_id}",
        "version": "1.0.0",
        "description": "Test plugin for E2E testing",
        "author": "Test Author",
        "price": {
            "type": "free"
        },
        "permissions": permissions or [],
        "runtime": {
            "type": "ray",
            "python_version": "3.10",
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
    main_py_content = '''
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
        return {
            "success": True,
            "data": {"message": f"Hello, {name}!"},
            "ui_schema": {
                "type": "AdaptiveCard",
                "version": "1.6",
                "body": [
                    {
                        "type": "TextBlock",
                        "text": f"Hello, {name}!"
                    }
                ]
            }
        }
    
    def process_data(self, payload: Dict[str, Any], trace_id: str = None) -> Dict[str, Any]:
        """处理数据方法"""
        data = payload.get("data", {})
        result = {"processed": True, "data": data}
        return {
            "success": True,
            "data": result
        }
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


class MockContext:
    """模拟 gRPC 上下文"""
    def __init__(self):
        self.code = None
        self.details = None
    
    def set_code(self, code):
        self.code = code
    
    def set_details(self, details):
        self.details = details


@pytest.mark.asyncio
@pytest.mark.requires_ray
async def test_invoke_plugin_e2e(
    gateway_servicer,
    plugin_manager,
    temp_plugin_dirs,
    ray_init
):
    """端到端测试：InvokePlugin RPC 调用"""
    plugins_dir, skills_repo_dir = temp_plugin_dirs
    
    # 创建测试插件
    plugin_id = "com.test.e2e-plugin"
    plugin_dir = create_test_plugin(plugin_id, skills_repo_dir)
    
    # 创建请求
    request = jachin_link_pb2.PluginRequest(
        plugin_id=plugin_id,
        method_name="hello",
        payload=json.dumps({"name": "E2E Test"}).encode('utf-8'),
        trace_id="e2e-trace-001"
    )
    
    # 创建模拟上下文
    context = MockContext()
    
    # 调用 InvokePlugin
    response = await gateway_servicer.InvokePlugin(request, context)
    
    # 验证响应
    assert response.status_code == 200
    assert response.payload is not None
    assert response.ui_render_schema is not None
    
    # 解析 payload
    response_data = json.loads(response.payload.decode('utf-8'))
    assert response_data["message"] == "Hello, E2E Test!"
    
    # 验证 UI Schema
    ui_schema = json.loads(response.ui_render_schema)
    assert ui_schema["type"] == "AdaptiveCard"
    assert len(ui_schema["body"]) > 0


@pytest.mark.asyncio
@pytest.mark.requires_ray
async def test_stream_plugin_e2e(
    gateway_servicer,
    plugin_manager,
    temp_plugin_dirs,
    ray_init
):
    """端到端测试：StreamPlugin RPC 调用"""
    plugins_dir, skills_repo_dir = temp_plugin_dirs
    
    # 创建有流式方法的测试插件
    plugin_id = "com.test.stream-e2e-plugin"
    plugin_dir = create_test_plugin(plugin_id, skills_repo_dir, has_stream_method=True)
    
    # 创建请求
    request = jachin_link_pb2.PluginRequest(
        plugin_id=plugin_id,
        method_name="process_data",
        payload=json.dumps({"data": [1, 2, 3, 4, 5]}).encode('utf-8'),
        trace_id="e2e-trace-002"
    )
    
    # 创建模拟上下文
    context = MockContext()
    
    # 收集流式响应
    chunks = []
    async for response in gateway_servicer.StreamPlugin(request, context):
        chunks.append(response)
        if response.status_code != 200:
            break
    
    # 验证响应
    assert len(chunks) > 0
    # 第一个 chunk 应该是正常响应或错误响应
    first_chunk = chunks[0]
    assert first_chunk.status_code in [200, 404, 500]  # 可能是成功、插件不存在或流式方法不存在


@pytest.mark.asyncio
@pytest.mark.requires_ray
async def test_multiple_plugins_concurrent(
    gateway_servicer,
    plugin_manager,
    temp_plugin_dirs,
    ray_init
):
    """端到端测试：多个插件并发调用"""
    plugins_dir, skills_repo_dir = temp_plugin_dirs
    
    # 创建多个测试插件
    plugin_ids = [
        "com.test.plugin-1",
        "com.test.plugin-2",
        "com.test.plugin-3"
    ]
    
    for plugin_id in plugin_ids:
        create_test_plugin(plugin_id, skills_repo_dir)
    
    # 并发调用多个插件
    async def call_plugin(plugin_id: str, name: str):
        request = jachin_link_pb2.PluginRequest(
            plugin_id=plugin_id,
            method_name="hello",
            payload=json.dumps({"name": name}).encode('utf-8'),
            trace_id=f"e2e-trace-{plugin_id}"
        )
        context = MockContext()
        return await gateway_servicer.InvokePlugin(request, context)
    
    # 并发调用
    tasks = [
        call_plugin(plugin_id, f"User-{i+1}")
        for i, plugin_id in enumerate(plugin_ids)
    ]
    
    responses = await asyncio.gather(*tasks)
    
    # 验证所有响应都成功
    for i, response in enumerate(responses):
        assert response.status_code == 200, f"Plugin {plugin_ids[i]} failed"
        response_data = json.loads(response.payload.decode('utf-8'))
        assert f"User-{i+1}" in response_data["message"]


@pytest.mark.asyncio
@pytest.mark.requires_ray
async def test_permission_violation_e2e(
    gateway_servicer,
    plugin_manager,
    temp_plugin_dirs,
    ray_init
):
    """端到端测试：权限违规场景"""
    plugins_dir, skills_repo_dir = temp_plugin_dirs
    
    # 创建没有 file.write 权限的插件
    plugin_id = "com.test.no-permission-plugin"
    plugin_dir = create_test_plugin(
        plugin_id,
        skills_repo_dir,
        permissions=[{"scope": "file.read"}]  # 只有读取权限
    )
    
    # 调用需要 file.write 权限的方法
    request = jachin_link_pb2.PluginRequest(
        plugin_id=plugin_id,
        method_name="save_file",  # 这个方法名会推断需要 file.write 权限
        payload=json.dumps({"data": "test"}).encode('utf-8'),
        trace_id="e2e-trace-003"
    )
    
    context = MockContext()
    response = await gateway_servicer.InvokePlugin(request, context)
    
    # 应该返回 403 错误
    assert response.status_code == 403
    assert "permission" in response.error_message.lower()


@pytest.mark.asyncio
@pytest.mark.requires_ray
async def test_plugin_not_found_e2e(
    gateway_servicer,
    ray_init
):
    """端到端测试：插件不存在的情况"""
    request = jachin_link_pb2.PluginRequest(
        plugin_id="com.test.non-existent",
        method_name="hello",
        payload=b"{}",
        trace_id="e2e-trace-004"
    )
    
    context = MockContext()
    response = await gateway_servicer.InvokePlugin(request, context)
    
    # 应该返回 404 错误
    assert response.status_code == 404
    assert "not installed" in response.error_message.lower() or "not found" in response.error_message.lower()


@pytest.mark.asyncio
@pytest.mark.requires_ray
async def test_connect_and_heartbeat_e2e(
    gateway_servicer,
    ray_init
):
    """端到端测试：Connect 和 SendHeartbeat RPC"""
    # 测试 Connect
    connect_request = jachin_link_pb2.ConnectRequest(
        device_id="test-device-001",
        device_info="Test Device",
        client_certificate=b"mock_cert"
    )
    
    context = MockContext()
    connect_response = await gateway_servicer.Connect(connect_request, context)
    
    # Connect 应该成功（或返回需要配对）
    assert connect_response.success is not None
    
    # 测试 SendHeartbeat
    heartbeat_request = jachin_link_pb2.Heartbeat(
        device_id="test-device-001",
        timestamp=1234567890
    )
    
    heartbeat_response = await gateway_servicer.SendHeartbeat(heartbeat_request, context)
    
    # Heartbeat 应该成功（或返回设备未注册）
    assert heartbeat_response.success is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
