"""
Plugin Executor - 插件执行器
负责动态加载和执行插件（Ray Actor）

职责：
- 根据 plugin_id 查找已安装的插件
- 检查 License 有效性
- 获取或创建插件对应的 Ray Actor
- 调用插件方法并处理返回结果
- 生成 Server-Driven UI (SDUI) 响应
"""

import logging
import json
import importlib.util
import asyncio
import time
from pathlib import Path
from typing import Optional, Dict, Any, Union
import ray

from core.system.plugin_manager import PluginManager
from core.system.permission_enforcer import PermissionEnforcer, get_permission_enforcer
from common.schemas.manifest import PluginManifest
from core.monitoring import get_performance_monitor, PerformanceContext

logger = logging.getLogger(__name__)


class PluginExecutor:
    """
    插件执行器
    
    负责：
    - 插件 Actor 的生命周期管理
    - 动态方法调用
    - 结果序列化和 SDUI 生成
    """
    
    def __init__(self, plugin_manager: PluginManager, permission_enforcer: Optional[PermissionEnforcer] = None):
        """
        初始化插件执行器
        
        Args:
            plugin_manager: 插件管理器实例
            permission_enforcer: 权限执行器（可选，如果为 None 则使用全局实例）
        """
        self.plugin_manager = plugin_manager
        self.permission_enforcer = permission_enforcer or get_permission_enforcer()
        # 存储 plugin_id -> Ray Actor Handle 的映射
        self.plugin_actors: Dict[str, ray.actor.ActorHandle] = {}
        
    async def invoke_plugin(
        self,
        plugin_id: str,
        method_name: str,
        payload: bytes,
        trace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        调用插件方法（带性能监控）
        
        Args:
            plugin_id: 插件 ID（如 "com.developer.deep-research"）
            method_name: 方法名（插件内部的方法，如 "buy", "sell"）
            payload: 载荷数据（bytes，通常是 JSON 字符串）
            trace_id: 链路追踪 ID
            
        Returns:
            Dict 包含：
            - status_code: HTTP 状态码
            - payload: 返回数据（bytes）
            - ui_render_schema: SDUI JSON 字符串
            - error_message: 错误信息（如果有）
        """
        monitor = get_performance_monitor()
        start_time = time.time()
        
        try:
            # 1. 检查插件是否已安装（.jsp 插件）或 bundled skill
            manifest = self.plugin_manager.get_plugin_manifest(plugin_id)
            is_bundled_skill = False
            
            if not manifest:
                # 尝试作为 bundled skill 加载
                # 先检查是否已经有缓存的 actor
                if plugin_id in self.plugin_actors:
                    actor_handle = self.plugin_actors[plugin_id]
                    is_bundled_skill = True
                    logger.info(f"Reusing cached bundled skill actor: {plugin_id}")
                else:
                    logger.info(f"Plugin '{plugin_id}' not found as .jsp, trying as bundled skill...")
                    actor_handle = self.plugin_manager.load_skill(plugin_id)
                    if actor_handle:
                        is_bundled_skill = True
                        logger.info(f"Loaded bundled skill: {plugin_id}")
                        # 存储 bundled skill 的 actor 到 plugin_actors（用于后续重用）
                        self.plugin_actors[plugin_id] = actor_handle
                    else:
                        logger.error(f"Plugin/Skill '{plugin_id}' not found")
                        return {
                            "status_code": 404,
                            "error_message": f"Plugin/Skill '{plugin_id}' not found",
                            "payload": b"{}",
                            "ui_render_schema": None
                        }
            else:
                # 2. 检查 License（仅对 .jsp 插件）
                if not self.plugin_manager.check_plugin_license(plugin_id):
                    logger.error(f"Plugin '{plugin_id}' license invalid or expired")
                    return {
                        "status_code": 403,
                        "error_message": f"Plugin '{plugin_id}' license invalid or expired",
                        "payload": b"{}",
                        "ui_render_schema": None
                    }
                
                # 3. 注册插件权限（如果尚未注册）
                if plugin_id not in self.permission_enforcer.plugin_permissions:
                    self.permission_enforcer.register_plugin_permissions(plugin_id, manifest)
                
                # 4. 获取或创建插件 Actor
                actor_handle = await self._get_or_create_plugin_actor(plugin_id, manifest)
                if not actor_handle:
                    logger.error(f"Failed to create actor for plugin '{plugin_id}'")
                    return {
                        "status_code": 500,
                        "error_message": f"Failed to initialize plugin '{plugin_id}'",
                        "payload": b"{}",
                        "ui_render_schema": None
                    }
            
            # 5. 解析 payload（假设是 JSON）
            try:
                payload_dict = json.loads(payload.decode('utf-8')) if payload else {}
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.warning(f"Failed to parse payload as JSON: {e}, treating as raw bytes")
                payload_dict = {"raw": payload.hex()}
            
            # 6. 检查权限（根据方法名推断需要的权限）
            # 注意：这里可以根据 method_name 或 payload 中的操作类型来检查权限
            # 例如：如果 method_name 是 "download_file"，需要 "file.write" 权限
            # 如果 method_name 是 "query_database"，需要 "database.query" 权限
            # 这是一个简化的实现，实际可以根据插件的方法签名或元数据来确定
            required_permission = self._infer_required_permission(method_name, payload_dict)
            if required_permission:
                try:
                    self.permission_enforcer.require_permission(plugin_id, required_permission)
                except PermissionError as e:
                    logger.error(f"Permission denied: {e}")
                    return {
                        "status_code": 403,
                        "error_message": str(e),
                        "payload": b"{}",
                        "ui_render_schema": None
                    }
            
            # 7. 调用插件方法
            # 注意：Ray Actor 方法调用是异步的
            try:
                if is_bundled_skill:
                    # Bundled skill 使用 execute 方法
                    if not hasattr(actor_handle, "execute"):
                        logger.error(f"Bundled skill '{plugin_id}' does not have 'execute' method")
                        return {
                            "status_code": 400,
                            "error_message": "Bundled skill must implement 'execute' method",
                            "payload": b"{}",
                            "ui_render_schema": None
                        }
                    
                    # 调用 execute(capability, params)
                    execute_ref = actor_handle.execute.remote(method_name, payload_dict)
                    result = await asyncio.wait_for(
                        asyncio.to_thread(ray.get, execute_ref),
                        timeout=30.0  # 30 秒超时
                    )
                else:
                    # .jsp 插件直接调用方法
                    if not hasattr(actor_handle, method_name):
                        logger.error(f"Method '{method_name}' not found in plugin '{plugin_id}'")
                        return {
                            "status_code": 400,
                            "error_message": f"Method '{method_name}' not found",
                            "payload": b"{}",
                            "ui_render_schema": None
                        }
                    
                    # 调用方法（Ray Actor 方法返回的是 ObjectRef，需要 await）
                    method = getattr(actor_handle, method_name)
                    result_ref = method.remote(payload_dict, trace_id=trace_id)
                    
                    # 等待结果（设置超时）
                    # ray.get() 是同步阻塞的，需要在线程池中运行以避免阻塞事件循环
                    result = await asyncio.wait_for(
                        asyncio.to_thread(ray.get, result_ref),
                        timeout=30.0  # 30 秒超时
                    )
                
            except asyncio.TimeoutError:
                logger.error(f"Plugin '{plugin_id}' method '{method_name}' timeout")
                return {
                    "status_code": 504,
                    "error_message": "Plugin execution timeout",
                    "payload": b"{}",
                    "ui_render_schema": None
                }
            except Exception as e:
                logger.error(f"Failed to invoke plugin method: {e}", exc_info=True)
                return {
                    "status_code": 500,
                    "error_message": str(e),
                    "payload": b"{}",
                    "ui_render_schema": None
                }
            
            # 8. 处理返回结果
            # 插件应该返回一个字典，包含：
            # - "success": bool
            # - "data": Any (业务数据)
            # - "ui_schema": Optional[Dict] (SDUI JSON，可选)
            # - "ui_render_schema": Optional[str] (SDUI JSON 字符串，可选，优先级高于 ui_schema)
            # - "data_payload": Optional[bytes] (原始数据，可选)
            if not isinstance(result, dict):
                result = {"success": True, "data": result}
            
            # 序列化返回数据
            response_payload = json.dumps(result.get("data", result)).encode('utf-8')
            
            # 处理 SDUI Schema（优先使用 ui_render_schema，兼容 ui_schema）
            ui_render_schema = result.get("ui_render_schema")
            if ui_render_schema:
                # 如果 ui_render_schema 是字典，序列化为 JSON 字符串
                if isinstance(ui_render_schema, dict):
                    ui_render_schema = json.dumps(ui_render_schema)
                elif not isinstance(ui_render_schema, str):
                    # 如果不是字符串也不是字典，尝试序列化
                    ui_render_schema = json.dumps(ui_render_schema)
            else:
                ui_schema = result.get("ui_schema")
                if ui_schema:
                    # 如果已经是字符串，直接使用；否则序列化为 JSON
                    if isinstance(ui_schema, str):
                        ui_render_schema = ui_schema
                    else:
                        ui_render_schema = json.dumps(ui_schema)
                else:
                    # 如果没有提供 UI Schema，生成一个简单的默认 UI
                    ui_render_schema = self._generate_default_ui(result)
            
            # 处理 data_payload（如果有）
            data_payload = result.get("data_payload")
            if data_payload and isinstance(data_payload, bytes):
                pass  # 保持为 bytes
            elif data_payload:
                # 如果不是 bytes，尝试编码
                data_payload = json.dumps(data_payload).encode('utf-8')
            else:
                data_payload = None
            
            duration = time.time() - start_time
            success = result.get("success", True) and result.get("status_code", 200) == 200
            
            # 记录性能指标
            monitor.record(
                "plugin.execution",
                duration,
                success=success,
                tags={"plugin_id": plugin_id, "method_name": method_name, "trace_id": trace_id or ""}
            )
            
            return {
                "status_code": 200 if success else 500,
                "payload": response_payload,
                "ui_render_schema": ui_render_schema,
                "data_payload": data_payload,
                "error_message": result.get("error"),
                "trace_id": trace_id
            }
            
        except Exception as e:
            logger.error(f"Unexpected error in plugin execution: {e}", exc_info=True)
            
            # 记录性能指标（失败）
            monitor.record(
                "plugin.execution",
                time.time() - start_time if 'start_time' in locals() else 0.0,
                success=False,
                tags={"plugin_id": plugin_id, "method_name": method_name, "error": str(e)}
            )
            
            return {
                "status_code": 500,
                "error_message": str(e),
                "payload": b"{}",
                "ui_render_schema": None
            }
    
    async def _get_or_create_plugin_actor(
        self,
        plugin_id: str,
        manifest: PluginManifest
    ) -> Optional[ray.actor.ActorHandle]:
        """
        获取或创建插件 Actor
        
        Args:
            plugin_id: 插件 ID
            manifest: 插件清单
            
        Returns:
            Ray Actor Handle，如果失败则返回 None
        """
        # 如果 Actor 已存在，检查是否还活着
        if plugin_id in self.plugin_actors:
            try:
                actor_handle = self.plugin_actors[plugin_id]
                # 尝试调用一个简单的方法来检查 Actor 是否可用
                # 这里假设插件 Actor 有一个 health_check 方法
                # 如果没有，可以跳过检查
                # 注意：这里不实际调用，只是检查 Actor 对象是否有效
                # 实际调用可能会失败，但会在 invoke_plugin 中处理
                return actor_handle
            except Exception as e:
                logger.warning(f"Actor for plugin '{plugin_id}' may be dead: {e}, recreating...")
                del self.plugin_actors[plugin_id]
                # 如果 Actor 已死，也需要重新注册权限
                if plugin_id in self.permission_enforcer.plugin_permissions:
                    self.permission_enforcer.unregister_plugin(plugin_id)
        
        # 创建新的 Actor
        try:
            # 1. 加载插件模块
            plugin_dir = self.plugin_manager.skills_repo_dir / plugin_id
            main_py = plugin_dir / "main.py"
            
            if not main_py.exists():
                logger.error(f"Plugin main.py not found: {main_py}")
                return None
            
            # 2. 动态导入插件模块
            spec = importlib.util.spec_from_file_location(
                f"plugin_{plugin_id.replace('.', '_')}",
                main_py
            )
            if not spec or not spec.loader:
                logger.error(f"Failed to load plugin module: {main_py}")
                return None
            
            plugin_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(plugin_module)
            
            # 3. 查找插件 Actor 类（约定：插件必须有一个 PluginActor 类）
            if not hasattr(plugin_module, "PluginActor"):
                logger.error(f"Plugin '{plugin_id}' does not define 'PluginActor' class")
                return None
            
            PluginActorClass = getattr(plugin_module, "PluginActor")
            
            # 4. 创建 Ray Actor（使用插件的运行时配置）
            runtime_config = manifest.runtime
            num_cpus = runtime_config.resources.get("cpu", 1)
            num_gpus = 1 if runtime_config.resources.get("gpu", False) else 0
            
            # 构建 runtime_env（包含插件的依赖）
            # 添加权限拦截器初始化脚本
            from core.system.runtime_permission_interceptor import create_permission_interceptor_init_script
            
            # 创建初始化脚本
            init_script = create_permission_interceptor_init_script(plugin_id)
            
            runtime_env = {
                "pip": manifest.requirements,
                "env_vars": {
                    "PLUGIN_ID": plugin_id,
                    "PLUGIN_VERSION": manifest.version,
                    "JACHIN_PROJECT_ROOT": str(Path(__file__).parent.parent.parent)  # 项目根目录
                },
                # 使用 Ray 的 setup 脚本在 Actor 启动时执行
                # 注意：Ray 的 runtime_env 不支持直接执行脚本，我们需要在 Actor 初始化时手动调用
                # 这里我们通过环境变量传递信息，在 Actor 的 __init__ 中安装拦截器
            }
            
            # 创建 Actor（使用 @ray.remote 装饰器）
            # 注意：插件类必须已经用 @ray.remote 装饰
            # 如果没有，我们需要动态创建
            if not hasattr(PluginActorClass, "_remote"):
                # 动态创建 Ray Actor 类
                PluginActorClass = ray.remote(
                    num_cpus=num_cpus,
                    num_gpus=num_gpus,
                    runtime_env=runtime_env
                )(PluginActorClass)
            
            # 实例化 Actor
            actor_handle = PluginActorClass.remote(
                plugin_id=plugin_id,
                manifest=manifest.model_dump()
            )
            
            # 存储 Actor Handle
            self.plugin_actors[plugin_id] = actor_handle
            
            # 注册插件权限
            self.permission_enforcer.register_plugin_permissions(plugin_id, manifest)
            
            # 安装运行时权限拦截器
            # 注意：这需要在 Actor 内部执行，我们通过调用一个初始化方法来实现
            # 如果 Actor 有 _install_permission_interceptor 方法，调用它
            try:
                # 尝试调用 Actor 的初始化方法（如果存在）
                if hasattr(actor_handle, '_install_permission_interceptor'):
                    init_ref = actor_handle._install_permission_interceptor.remote()
                    # 等待初始化完成（不阻塞，使用后台任务）
                    asyncio.create_task(self._wait_for_interceptor_init(init_ref, plugin_id))
                else:
                    # 如果没有初始化方法，记录日志
                    # 拦截器可以通过环境变量在 Actor 初始化时自动安装
                    logger.debug(f"Plugin '{plugin_id}' Actor does not have _install_permission_interceptor method, "
                               f"interceptor should be installed in Actor.__init__")
            except Exception as e:
                logger.warning(f"Failed to install permission interceptor for plugin '{plugin_id}': {e}")
                # 不阻止 Actor 创建，权限检查仍会在调用前进行
            
            logger.info(f"Created actor for plugin '{plugin_id}'")
            return actor_handle
            
        except Exception as e:
            logger.error(f"Failed to create actor for plugin '{plugin_id}': {e}", exc_info=True)
            return None
    
    def _generate_default_ui(self, result: Dict[str, Any]) -> str:
        """
        生成默认 UI Schema（当插件没有提供时）
        
        Args:
            result: 插件返回的结果
            
        Returns:
            Adaptive Cards JSON 字符串
        """
        from common.schemas.sdui import create_simple_card
        
        # 提取主要信息
        message = "操作完成"
        if "error" in result:
            message = f"错误: {result['error']}"
        elif "message" in result:
            message = result["message"]
        
        # 创建简单的卡片
        card = create_simple_card(
            title="插件执行结果",
            text=message,
            subtitle=f"数据: {json.dumps(result.get('data', {}), ensure_ascii=False)[:100]}..."
        )
        
        return card.to_json()
    
    def _infer_required_permission(self, method_name: str, payload: Dict[str, Any]) -> Optional[str]:
        """
        根据方法名和 payload 推断需要的权限
        
        Args:
            method_name: 方法名
            payload: 载荷数据
            
        Returns:
            需要的权限作用域，如果无法推断则返回 None
        """
        # 简单的启发式规则：根据方法名推断权限
        method_lower = method_name.lower()
        
        # 文件操作
        if any(keyword in method_lower for keyword in ["download", "save", "write", "create"]):
            return "file.write"
        if any(keyword in method_lower for keyword in ["read", "load", "open"]):
            return "file.read"
        if any(keyword in method_lower for keyword in ["delete", "remove"]):
            return "file.delete"
        
        # 网络操作
        if any(keyword in method_lower for keyword in ["fetch", "request", "http", "api", "download"]):
            return "internet.access"
        
        # 数据库操作
        if any(keyword in method_lower for keyword in ["query", "select", "search"]):
            return "database.query"
        if any(keyword in method_lower for keyword in ["insert", "update", "delete", "write"]):
            return "database.write"
        
        # LLM 操作
        if any(keyword in method_lower for keyword in ["llm", "ai", "generate", "complete"]):
            return "llm.call"
        
        # 设备操作
        if any(keyword in method_lower for keyword in ["control", "command", "execute"]):
            return "device.control"
        
        # 默认：不需要特殊权限
        return None
    
    async def _wait_for_interceptor_init(self, init_ref, plugin_id: str):
        """等待拦截器初始化完成（后台任务）"""
        try:
            await asyncio.wait_for(
                asyncio.to_thread(ray.get, init_ref),
                timeout=10.0
            )
            logger.debug(f"Permission interceptor initialized for plugin '{plugin_id}'")
        except Exception as e:
            logger.warning(f"Failed to wait for interceptor init for plugin '{plugin_id}': {e}")
    
    async def cleanup(self):
        """清理所有插件 Actor"""
        for plugin_id, actor_handle in list(self.plugin_actors.items()):
            try:
                # 尝试卸载拦截器（如果 Actor 有该方法）
                try:
                    if hasattr(actor_handle, '_uninstall_permission_interceptor'):
                        uninstall_ref = actor_handle._uninstall_permission_interceptor.remote()
                        await asyncio.wait_for(
                            asyncio.to_thread(ray.get, uninstall_ref),
                            timeout=5.0
                        )
                except Exception:
                    pass  # 忽略卸载失败
                
                ray.kill(actor_handle)
                logger.info(f"Killed actor for plugin '{plugin_id}'")
            except Exception as e:
                logger.warning(f"Failed to kill actor for plugin '{plugin_id}': {e}")
        
        self.plugin_actors.clear()
        
        # 清理权限注册
        for plugin_id in list(self.permission_enforcer.plugin_permissions.keys()):
            self.permission_enforcer.unregister_plugin(plugin_id)
