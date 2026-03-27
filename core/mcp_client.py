"""
Jachin Nexus V2 - L2 MCP 客户端代理引擎 (轨道 A)

连接 MCP 服务器、发现工具、执行工具调用，供 L3 通过 HTTP 代理调用。
使用官方 mcp Python SDK，全异步实现。
本地 RPA 已迁至 L3 伴生 MCP，L2 仅代理外部 MCP Server。
"""
from __future__ import annotations

import json
import logging
import shutil
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)


def _resolve_stdio_command(command: str) -> str:
    """
    Windows 下 npx/npm 常为 .cmd，部分环境下需绝对路径才能稳定拉起 stdio 子进程。
    """
    if not command or sys.platform != "win32":
        return command
    low = command.lower()
    if low not in ("npx", "npm", "node"):
        return command
    found = shutil.which(command)
    if found:
        return found
    found_cmd = shutil.which(f"{command}.cmd")
    if found_cmd:
        return found_cmd
    return command


# 内置工具 server_id（用于 IAM item_id）
BUILTIN_SERVER_ID = "l2-builtin"

# 默认配置路径
DEFAULT_MCP_CONFIG_PATH = Path.home() / ".jachin" / "mcp_servers.json"


class MCPConnectionError(Exception):
    """MCP 连接或初始化失败"""


class MCPToolNotFoundError(Exception):
    """工具未找到"""


class MCPServerInstance:
    """
    单个 MCP 服务器实例。管理进程生命周期与工具调用。
    """

    def __init__(
        self,
        server_id: str,
        *,
        command: str,
        args: list[str],
        env: Optional[dict[str, str]] = None,
    ) -> None:
        self.server_id = server_id
        self.command = _resolve_stdio_command(command)
        self.args = args or []
        self.env = env
        self._exit_stack = AsyncExitStack()
        self._session: Optional[ClientSession] = None
        self._tool_names: list[str] = []

    async def connect(self) -> None:
        """
        通过 stdio 启动并连接底层 MCP 进程。
        使用 AsyncExitStack 管理资源生命周期。
        """
        logger.info("[MCP] 正在拉起 Server server_id=%s command=%s args=%s", self.server_id, self.command, self.args)
        try:
            server_params = StdioServerParameters(
                command=self.command,
                args=self.args,
                env=self.env,
            )
            stdio_transport = await self._exit_stack.enter_async_context(stdio_client(server_params))
            stdio, write = stdio_transport
            self._session = await self._exit_stack.enter_async_context(ClientSession(stdio, write))
            await self._session.initialize()
            logger.info("[MCP] Server 已连接 server_id=%s", self.server_id)
        except Exception as e:
            logger.exception("[MCP] Server 连接失败 server_id=%s err=%s", self.server_id, e)
            await self.close()
            raise MCPConnectionError(f"MCP server {self.server_id} 连接失败: {e}") from e

    async def list_tools(self) -> list[dict[str, Any]]:
        """
        获取该服务提供的工具列表。
        Returns:
            [{"name": str, "description": str, "inputSchema": dict}, ...]
        """
        if not self._session:
            raise MCPConnectionError(f"Server {self.server_id} 未连接")
        try:
            response = await self._session.list_tools()
            tools = []
            self._tool_names = []
            for t in response.tools:
                tool_info = {
                    "name": t.name,
                    "description": t.description or "",
                    "inputSchema": t.inputSchema if hasattr(t, "inputSchema") else {},
                }
                tools.append(tool_info)
                self._tool_names.append(t.name)
            logger.debug("[MCP] list_tools server_id=%s count=%d names=%s", self.server_id, len(tools), [x["name"] for x in tools])
            return tools
        except Exception as e:
            logger.warning("[MCP] list_tools 失败 server_id=%s err=%s", self.server_id, e)
            raise

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """
        执行指定工具，返回可读结果字符串。
        """
        if not self._session:
            raise MCPConnectionError(f"Server {self.server_id} 未连接")
        logger.info("[MCP] call_tool server_id=%s name=%s args_keys=%s", self.server_id, name, list(arguments.keys()) if arguments else [])
        try:
            result = await self._session.call_tool(name, arguments)
            if result.isError:
                err_msg = str(result.content) if result.content else "工具执行返回错误"
                logger.warning("[MCP] call_tool 工具返回错误 server_id=%s name=%s content=%s", self.server_id, name, err_msg)
                return f"[MCP 工具错误] {err_msg}"
            # 解析 content：可能是 TextContent 列表
            if result.content:
                parts = []
                for block in result.content:
                    if hasattr(block, "text") and block.text:
                        parts.append(block.text)
                    elif hasattr(block, "type") and block.type == "text" and hasattr(block, "text"):
                        parts.append(block.text)
                if parts:
                    return "\n".join(parts)
            return "[无输出]"
        except Exception as e:
            logger.exception("[MCP] call_tool 异常 server_id=%s name=%s err=%s", self.server_id, name, e)
            raise

    async def close(self) -> None:
        """优雅关闭进程与连接。"""
        logger.debug("[MCP] 关闭 Server server_id=%s", self.server_id)
        try:
            await self._exit_stack.aclose()
        except RuntimeError as e:
            # MCP SDK 已知问题：stdio_client 的 anyio cancel scope 在跨 task 退出时抛错
            # 见 https://github.com/modelcontextprotocol/python-sdk/issues/521
            if "cancel scope" in str(e).lower():
                logger.debug("[MCP] 关闭时 anyio 跨 task 退出（已知问题，可忽略）server_id=%s", self.server_id)
            else:
                raise
        finally:
            self._session = None
            self._tool_names = []

    def has_tool(self, name: str) -> bool:
        """检查该 Server 是否提供指定工具。"""
        return name in self._tool_names


class MCPManager:
    """
    MCP 管理器：读取配置、并发拉起所有 Server、维护工具路由表。
    """

    def __init__(self, config_path: Optional[Path] = None) -> None:
        self._config_path = config_path or DEFAULT_MCP_CONFIG_PATH
        self._instances: dict[str, MCPServerInstance] = {}
        self._tool_route: dict[str, MCPServerInstance] = {}  # tool_name -> instance
        self._tools_cache: dict[str, dict[str, Any]] = {}  # tool_name -> {name, description, inputSchema}
        self._builtin_tool_names: set[str] = set()  # 内置工具名，优先路由

    def _load_config(self) -> list[dict[str, Any]]:
        """读取 mcp_servers.json 配置。"""
        if not self._config_path.exists():
            logger.info("[MCP] 配置文件不存在 path=%s，跳过 MCP 初始化", self._config_path)
            return []
        try:
            raw = self._config_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            servers = data.get("mcp_servers", data) if isinstance(data, dict) else data
            if not isinstance(servers, list):
                return []
            return servers
        except json.JSONDecodeError as e:
            logger.warning("[MCP] 配置文件 JSON 解析失败 path=%s err=%s", self._config_path, e)
            return []
        except Exception as e:
            logger.warning("[MCP] 读取配置失败 path=%s err=%s", self._config_path, e)
            return []

    def _register_builtin_tools(self) -> None:
        """注册内置 MCP 工具（HR 招聘等）。"""
        try:
            from core.mcp_builtin_tools import get_builtin_tools
            for t in get_builtin_tools():
                name = t.get("name", "")
                if name:
                    self._tools_cache[name] = t
                    self._builtin_tool_names.add(name)
                    logger.info("[MCP] 内置工具已注册 name=%s", name)
        except ImportError as e:
            logger.debug("[MCP] 内置工具未加载: %s", e)

    async def start(self) -> None:
        """
        读取配置，并发拉起所有 MCP Server，构建工具路由表。
        内置工具优先注册，外部 Server 工具不覆盖同名内置工具。
        """
        self._register_builtin_tools()
        servers = self._load_config()
        if not servers:
            return
        logger.info("[MCP] 开始并发拉起 %d 个 Server", len(servers))
        for cfg in servers:
            server_id = cfg.get("id") or cfg.get("name") or "unknown"
            command = cfg.get("command", "")
            args = cfg.get("args") or []
            env = cfg.get("env")
            if not command:
                logger.warning("[MCP] 跳过无效配置 server_id=%s 缺少 command", server_id)
                continue
            instance = MCPServerInstance(
                server_id=server_id,
                command=command,
                args=args if isinstance(args, list) else [],
                env=env,
            )
            try:
                await instance.connect()
                tools = await instance.list_tools()
                self._instances[server_id] = instance
                for t in tools:
                    name = t.get("name", "")
                    if not name:
                        continue
                    if name in self._builtin_tool_names:
                        logger.debug("[MCP] 工具名与内置冲突 name=%s，保留内置实现", name)
                        continue
                    if name not in self._tool_route:
                        self._tool_route[name] = instance
                        self._tools_cache[name] = t
                    else:
                        logger.debug("[MCP] 工具名冲突 name=%s 已由 %s 提供，跳过 %s", name, self._tool_route[name].server_id, server_id)
            except MCPConnectionError as e:
                logger.warning("[MCP] Server 启动失败 server_id=%s err=%s", server_id, e)
            except Exception as e:
                logger.exception("[MCP] Server 启动异常 server_id=%s err=%s", server_id, e)
        logger.info("[MCP] 启动完成 instances=%d tools=%d", len(self._instances), len(self._tool_route))

    async def stop(self) -> None:
        """关闭所有 MCP Server 实例。"""
        for server_id, instance in list(self._instances.items()):
            try:
                await instance.close()
            except Exception as e:
                logger.warning("[MCP] 关闭 Server 异常 server_id=%s err=%s", server_id, e)
        self._instances.clear()
        self._tool_route.clear()
        # 保留内置工具缓存，stop 后 get_all_tools 仍可返回内置工具
        for name in list(self._tools_cache.keys()):
            if name not in self._builtin_tool_names:
                del self._tools_cache[name]
        logger.info("[MCP] 已关闭所有 Server")

    def get_all_tools(self) -> list[dict[str, Any]]:
        """
        获取所有已挂载的工具列表（从缓存返回，同步方法）。
        """
        return list(self._tools_cache.values())

    async def list_tools_async(self) -> list[dict[str, Any]]:
        """
        异步获取所有工具列表（重新从各 Server 拉取，保证最新）。
        内置工具始终包含在列表中。
        """
        tools: list[dict[str, Any]] = []
        seen: set[str] = set(self._builtin_tool_names)
        for name in self._builtin_tool_names:
            if name in self._tools_cache:
                tools.append(self._tools_cache[name])
        for instance in self._instances.values():
            try:
                lst = await instance.list_tools()
                for t in lst:
                    name = t.get("name", "")
                    if name and name not in seen:
                        seen.add(name)
                        tools.append(t)
            except Exception as e:
                logger.warning("[MCP] list_tools 失败 server_id=%s err=%s", instance.server_id, e)
        return tools

    async def invoke_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """
        根据路由表将工具调用转发到对应 Server 执行。
        内置工具优先本地执行，无需外部 MCP Server。
        """
        if tool_name in self._builtin_tool_names:
            from core.mcp_builtin_tools import invoke_builtin_tool
            return await invoke_builtin_tool(tool_name, arguments or {})
        instance = self._tool_route.get(tool_name)
        if not instance:
            raise MCPToolNotFoundError(f"工具 '{tool_name}' 未找到或未挂载")
        return await instance.call_tool(tool_name, arguments or {})

    async def add_server(self, cfg: dict[str, Any]) -> bool:
        """
        动态注入单个 MCP Server 配置（供 Inventory 侧载使用）。
        创建实例、连接、注册工具。返回 True 表示成功。
        """
        server_id = cfg.get("id") or cfg.get("name") or "unknown"
        command = cfg.get("command", "")
        args = cfg.get("args") or []
        env = cfg.get("env")
        if not command:
            logger.warning("[MCP] add_server 跳过 server_id=%s 缺少 command", server_id)
            return False
        if server_id in self._instances:
            logger.debug("[MCP] add_server 跳过 server_id=%s 已存在", server_id)
            return True
        instance = MCPServerInstance(
            server_id=server_id,
            command=command,
            args=args if isinstance(args, list) else [],
            env=env,
        )
        try:
            await instance.connect()
            tools = await instance.list_tools()
            self._instances[server_id] = instance
            for t in tools:
                name = t.get("name", "")
                if name and name not in self._builtin_tool_names and name not in self._tool_route:
                    self._tool_route[name] = instance
                    self._tools_cache[name] = t
            logger.info("[MCP] 侧载 Server 已注入 server_id=%s tools=%d", server_id, len(tools))
            return True
        except MCPConnectionError as e:
            logger.warning("[MCP] add_server 连接失败 server_id=%s err=%s", server_id, e)
            return False
        except Exception as e:
            logger.exception("[MCP] add_server 异常 server_id=%s err=%s", server_id, e)
            return False

    def get_server_id_for_tool(self, tool_name: str) -> Optional[str]:
        """获取工具所属的 MCP server_id，用于 IAM 鉴权 item_id = mcp:{server_id}"""
        if tool_name in self._builtin_tool_names:
            return BUILTIN_SERVER_ID
        instance = self._tool_route.get(tool_name)
        return instance.server_id if instance else None

    @property
    def tool_count(self) -> int:
        return len(self._tools_cache)

    @property
    def server_count(self) -> int:
        return len(self._instances)


# 全局单例，由 FastAPI lifespan 管理
_manager: Optional[MCPManager] = None


def get_mcp_manager() -> MCPManager:
    """获取 MCP 管理器单例。"""
    global _manager
    if _manager is None:
        _manager = MCPManager()
    return _manager


def set_mcp_manager(manager: Optional[MCPManager]) -> None:
    """设置 MCP 管理器（用于测试或注入）。"""
    global _manager
    _manager = manager
