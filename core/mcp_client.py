"""
Jachin Nexus V2 - L2 MCP 客户端代理引擎（四大原语 · MCP）

连接 MCP 服务器、发现工具、执行工具调用，供 L3 通过 HTTP 代理调用。
使用官方 mcp Python SDK，全异步实现。
本地 RPA 已迁至 L3 伴生 MCP，L2 仅代理外部 MCP Server。

stdio 噪声过滤（npx/dotenv 等非 JSON 行）在 import 本模块前由 ``core.mcp_stdio_noise_filter`` 安装；
MCP 路径预检（filesystem 根目录、git 仓库）见 ``core/inventory_scanner.py``，架构索引见
``docs/architecture/CURRENT_SYSTEM_ARCHITECTURE.md`` §4。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Optional

from core.mcp_stdio_noise_filter import apply_stdio_stdout_noise_filter

# 须在首次 stdio 连接前安装：过滤子进程 stdout 中非 JSON-RPC 的噪声行（见 mcp_stdio_noise_filter 模块说明）
apply_stdio_stdout_noise_filter()

import mcp.types as mcp_types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)


def _log_tavily_before_stdio_connect(server_id: str, args: Any, env: Optional[dict[str, Any]]) -> None:
    sid = (server_id or "").lower()
    if "tavily" not in sid and not (
        isinstance(args, list) and any(isinstance(a, str) and "tavily" in a.lower() for a in args)
    ):
        return
    from core.mcp_embedded_runtime import log_tavily_mcp_chain

    log_tavily_mcp_chain("stdio_connect_before", server_id, env if isinstance(env, dict) else None, log_parent_os=True)


def format_mcp_tool_args_for_log(tool_name: str, arguments: dict[str, Any] | None) -> str:
    """
    编程/排障：单行摘要，避免把整段 content 打进日志。
    """
    tn = (tool_name or "").strip().lower()
    if tn.startswith("mcp:"):
        tn = tn[4:]
    args = arguments if isinstance(arguments, dict) else {}
    keys = list(args.keys())

    def _pv(key: str) -> str:
        v = args.get(key)
        if v is None:
            return "missing"
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return "empty_str"
            return f"len={len(s)} preview={s[:100]!r}{'…' if len(s) > 100 else ''}"
        return f"type={type(v).__name__!r} repr={repr(v)[:120]}"

    if tn in ("write_file", "edit_file", "create_file"):
        return (
            f"keys={keys} path={_pv('path')} content={_pv('content')} "
            f"file_path_present={('file_path' in args)}"
        )
    if tn == "read_file":
        return f"keys={keys} path={_pv('path')}"
    if tn in ("delete_file", "move_file", "copy_file", "get_file_info"):
        return f"keys={keys} path={_pv('path')}"
    return f"keys={keys} n={len(keys)}"


def normalize_mcp_schema_aliases(tool_name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    """
    模型常按 core:fs_write 习惯传 file_path；官方 MCP server-filesystem 的 write_file/read_file 等要求 path。
    在 call_tool / L2 invoke 前统一映射，避免 -32602 path undefined。
    """
    if not isinstance(arguments, dict):
        return {}
    out = dict(arguments)
    tn = (tool_name or "").strip().lower()
    if tn.startswith("mcp:"):
        tn = tn[4:]
    if tn not in (
        "write_file",
        "read_file",
        "edit_file",
        "create_file",
        "delete_file",
        "move_file",
        "copy_file",
        "get_file_info",
        "list_directory",
        "search_files",
    ):
        return out
    path_ok = out.get("path")
    if path_ok is not None and str(path_ok).strip():
        return out
    for k in ("file_path", "filepath", "target_path", "file", "uri"):
        v = out.get(k)
        if v is not None and str(v).strip():
            out["path"] = str(v).strip()
            if k != "path":
                out.pop(k, None)
            break
    return out


# 与仓库 plugin.json / mcp_json_repair 对齐；无版本号的 npx 会拉到最新，常与 Python mcp 1.26+ 在 tools/list 上不兼容
_SERVER_FILESYSTEM_NPM_PIN = "@modelcontextprotocol/server-filesystem@0.6.3"


def _is_npx_server_filesystem_args(args: list[Any]) -> bool:
    return any(isinstance(a, str) and "@modelcontextprotocol/server-filesystem" in a for a in args)


def _npx_filesystem_connect_max_attempts() -> int:
    """
    默认 **1 次**（失败即放弃，不阻塞 L3 主流程）。

    若需对 ``Connection closed`` 自动重试，设置 ``JACHIN_MCP_NPX_FILESYSTEM_CONNECT_ATTEMPTS=2..10``。
    """
    raw = (os.environ.get("JACHIN_MCP_NPX_FILESYSTEM_CONNECT_ATTEMPTS") or "").strip()
    if raw.isdigit():
        return max(1, min(10, int(raw)))
    return 1


def _npx_filesystem_initialize_failure_retriable(err: BaseException) -> bool:
    """MCP SDK 在子进程过早退出时多为 McpError: Connection closed。"""
    msg = f"{type(err).__name__} {err}".lower()
    if "connection closed" in msg:
        return True
    if "mcperror" in msg and "closed" in msg:
        return True
    if "eof" in msg and "stdio" in msg:
        return True
    return False


def _pin_server_filesystem_npm_version(args: list[Any]) -> list[Any]:
    """
    将 ``@modelcontextprotocol/server-filesystem`` 固定为与当前 MCP Python SDK 对齐的 npm 版本。

    - 无版本号（仅 ``@scope/pkg``）会拉到 registry 最新，常与 mcp 1.26+ 不兼容；
    - 旧 pin（如 ``@0.6.2``）在部分环境下会在 ``initialize`` 阶段即 Connection closed。
    因此凡在 args 中出现该包名，一律规范为 ``_SERVER_FILESYSTEM_NPM_PIN``（当前 0.6.3）。
    """
    out: list[Any] = []
    changed = False
    for a in args:
        if not isinstance(a, str):
            out.append(a)
            continue
        s = a.strip()
        if "@modelcontextprotocol/server-filesystem" in s:
            if s != _SERVER_FILESYSTEM_NPM_PIN:
                out.append(_SERVER_FILESYSTEM_NPM_PIN)
                changed = True
                continue
        out.append(a)
    if changed:
        logger.info(
            "[MCP] 已将 npx 参数固定为 %s（统一 server-filesystem 版本，避免与 MCP SDK 不兼容）",
            _SERVER_FILESYSTEM_NPM_PIN,
        )
    return out


def ensure_mcp_server_filesystem_root_directories(args: list[Any]) -> None:
    """
    官方 ``@modelcontextprotocol/server-filesystem``（npm）启动时对每个 CLI 允许路径 ``fs.stat``，
    不存在或不可访问的目录会被丢弃；若最终 **没有任何** 可访问目录则 ``process.exit(1)``，
    父进程侧表现为 ``initialize`` 阶段 ``Connection closed``。

    Python ``mcp`` 客户端默认 **不** 声明 MCP ``roots`` 能力（见 ``mcp.client.session.ClientSession``），
    无法仅靠协议向服务端补根目录，因此必须在拉起 npx 子进程前保证每个 CLI 根路径为**已存在目录**
    （``mkdir(parents=True, exist_ok=True)``）。

    与 npm 包版本（当前 pin ``0.6.3``）无关；属上游服务器契约。
    """
    if not isinstance(args, list):
        return
    try:
        pkg_idx = next(
            i
            for i, a in enumerate(args)
            if isinstance(a, str) and "server-filesystem" in a
        )
    except StopIteration:
        return
    for a in args[pkg_idx + 1 :]:
        if not isinstance(a, str):
            continue
        raw = a.strip()
        if not raw or raw.startswith("-"):
            continue
        try:
            p = Path(raw).expanduser()
            p.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning(
                "[MCP] server-filesystem 根目录无法预创建（服务端可能直接退出）path=%s err=%s",
                raw,
                e,
            )


def _collapse_redundant_server_filesystem_roots(args: list[Any]) -> list[Any]:
    """
    若同时传入「项目根」与「其下的 data/hr_resumes」，只保留项目根（子路径仍可在工具内访问）。
    多根目录在部分 Node 版上易触发异常退出。
    """
    try:
        pkg_idx = next(
            i
            for i, a in enumerate(args)
            if isinstance(a, str) and "server-filesystem" in a
        )
    except StopIteration:
        return args
    tail = [args[i] for i in range(pkg_idx + 1, len(args)) if isinstance(args[i], str) and not str(args[i]).startswith("-")]
    if len(tail) != 2:
        return args
    try:
        root = Path(tail[0]).expanduser().resolve()
        sub = Path(tail[1]).expanduser().resolve()
        sub.relative_to(root)
    except (ValueError, OSError):
        return args
    logger.info(
        "[MCP] server-filesystem 省略冗余子目录参数，仅保留根: %s",
        root,
    )
    return list(args[: pkg_idx + 1]) + [str(root)]


def stdio_official_filesystem_workspace_cwd(args: Any) -> Optional[str]:
    """
    ``@modelcontextprotocol/server-filesystem`` 将相对 ``path``（如 list_directory 的 ``.``）
    按 **子进程当前工作目录** 解析。若 stdio 的 cwd 继承为 L3 仓库根（常见），则 ``.`` 会落在
    允许目录之外，触发 ``Access denied - path outside allowed directories``。

    返回 MCP 命令行里、包名之后 **第一个已存在且为目录** 的允许根，供 ``StdioServerParameters.cwd`` 使用。
    """
    if not isinstance(args, list):
        return None
    pkg_idx = -1
    for i, a in enumerate(args):
        if isinstance(a, str) and "server-filesystem" in a:
            pkg_idx = i
            break
    if pkg_idx < 0:
        return None
    for j in range(pkg_idx + 1, len(args)):
        a = args[j]
        if not isinstance(a, str) or not a.strip():
            continue
        s = a.strip()
        if s.startswith("-"):
            continue
        try:
            p = Path(s).expanduser()
            if p.is_dir():
                return str(p.resolve())
        except OSError:
            continue
    return None


def _stdio_missing_dash_m_module(args: Any) -> Optional[str]:
    """
    stdio 配置为 ``python -m some.module`` 时，若该模块未安装则子进程会立刻失败。
    返回缺失的模块名，若无 ``-m`` 或已安装则返回 None。
    """
    if not isinstance(args, list):
        return None
    for i in range(len(args) - 1):
        if args[i] != "-m":
            continue
        mod = args[i + 1]
        if not isinstance(mod, str) or not mod.strip():
            continue
        name = mod.strip()
        try:
            import importlib.util

            if importlib.util.find_spec(name) is None:
                return name
        except (ValueError, AttributeError, ModuleNotFoundError):
            return name
    return None


def _stdio_args_reference_missing_py_file(args: Any) -> Optional[str]:
    """
    stdio MCP 的 args 里若出现 .py 路径且文件不存在，子进程会立即退出并表现为 Connection closed。
    提前跳过并打日志，避免拖住其它 MCP 的排障。
    """
    if not isinstance(args, list):
        return None
    for a in args:
        if not isinstance(a, str):
            continue
        s = a.strip()
        if len(s) < 4 or not s.lower().endswith(".py"):
            continue
        try:
            p = Path(s)
            if not p.is_file():
                return s
        except OSError:
            return s
    return None


def _stdio_command_is_bare_executable_name(command: str) -> bool:
    """
    裸命令名（如 python、uvx）须交给 PATH / CreateProcess 解析。
    若误用 Path(command).is_file()，在 L3 cwd=仓库根时会把根目录下同名空文件/占位文件当成解释器，Windows 上触发 WinError 193。
    """
    s = (command or "").strip()
    if not s:
        return False
    if sys.platform == "win32":
        if s.startswith("\\\\"):
            return False
        if len(s) > 1 and s[1] == ":":
            return False
        return "\\" not in s and "/" not in s
    return "/" not in s and not s.startswith(("./", "../"))


def _resolve_stdio_command(command: str) -> str:
    """
    Windows 下 npx/npm 常为 .cmd，部分环境下需绝对路径才能稳定拉起 stdio 子进程。
    已由 ``resolve_mcp_stdio_command`` 解析为嵌入式路径时，此处若已是存在的 .exe/.cmd 则原样返回。
    """
    if not command:
        return command
    try:
        p = Path(command)
        # 仅对显式路径（绝对路径或含路径分隔符）做「已是文件则定型为绝对路径」
        if not _stdio_command_is_bare_executable_name(command) and p.is_file():
            return str(p.resolve())
    except OSError:
        pass
    if sys.platform != "win32":
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

    async def _connect_stdio_once(self) -> None:
        """单次 stdio 启动并完成 MCP initialize（成功则 self._session 可用）。"""
        from core.mcp_embedded_runtime import (
            effective_stdio_env_for_sdk,
            expand_stdio_env_windows_npx_google_maps,
            expand_stdio_env_windows_npx_tavily,
            is_tavily_stdio_server,
            log_tavily_mcp_chain,
            log_tavily_stdio_cwd_choice,
            log_tavily_stdio_merged_spawn,
            resolve_tavily_stdio_cwd,
        )

        eff_env = effective_stdio_env_for_sdk(self.server_id, self.args, self.env)
        eff_env = expand_stdio_env_windows_npx_tavily(self.server_id, self.args, eff_env)
        eff_env = expand_stdio_env_windows_npx_google_maps(self.server_id, self.args, eff_env)
        if _is_npx_server_filesystem_args(self.args):
            ensure_mcp_server_filesystem_root_directories(self.args)
        # server-filesystem 与 @modelcontextprotocol/sdk 1.26+ 需与 Python mcp 默认协议一致；
        # 勿强行降级 protocolVersion（曾导致 initialize 阶段即 Connection closed）。
        # 若全局安装了 dotenvx 等 npx 包装，可向 stdout 注入非 JSON 行；合并 CI 以降低啰嗦输出（与 mcp_stdio_noise_filter 互补）。
        if _is_npx_server_filesystem_args(self.args):
            merged = dict(eff_env) if isinstance(eff_env, dict) else {}
            merged.setdefault("CI", "true")
            # 降低 npx/npm 交互式提示与进度条污染 stderr，减少 Windows 上子进程握手竞态
            merged.setdefault("NPM_CONFIG_UPDATE_NOTIFIER", "false")
            merged.setdefault("npm_config_fund", "false")
            merged.setdefault("npm_config_progress", "false")
            eff_env = merged
        tavily_cwd: str | None = None
        if is_tavily_stdio_server(self.server_id, self.args):
            tavily_cwd = resolve_tavily_stdio_cwd()
            log_tavily_stdio_cwd_choice(self.server_id, tavily_cwd)
            log_tavily_mcp_chain(
                "stdio_explicit_env",
                self.server_id,
                eff_env,
                log_parent_os=True,
            )
            log_tavily_stdio_merged_spawn(self.server_id, eff_env)
        fs_cwd = stdio_official_filesystem_workspace_cwd(self.args)
        stdio_cwd = tavily_cwd or fs_cwd
        if fs_cwd and not tavily_cwd:
            logger.debug("[MCP] server-filesystem stdio cwd=%s server_id=%s", fs_cwd, self.server_id)
        server_params = StdioServerParameters(
            command=self.command,
            args=self.args,
            env=eff_env,
            cwd=stdio_cwd,
        )
        stdio_transport = await self._exit_stack.enter_async_context(stdio_client(server_params))
        stdio, write = stdio_transport
        self._session = await self._exit_stack.enter_async_context(ClientSession(stdio, write))
        await self._session.initialize()
        if is_tavily_stdio_server(self.server_id, self.args):
            log_tavily_mcp_chain(
                "stdio_session_ready",
                self.server_id,
                eff_env,
                log_parent_os=True,
            )

    async def connect(self) -> None:
        """
        通过 stdio 启动并连接底层 MCP 进程。
        使用 AsyncExitStack 管理资源生命周期。

        ``npx @modelcontextprotocol/server-filesystem`` 默认只尝试一次；失败由上层跳过该 Server，
        不长时间重试以免拖慢启动。需要重试时设置 ``JACHIN_MCP_NPX_FILESYSTEM_CONNECT_ATTEMPTS``。
        """
        self.args = _collapse_redundant_server_filesystem_roots(
            _pin_server_filesystem_npm_version(list(self.args))
        )
        logger.info("[MCP] 正在拉起 Server server_id=%s command=%s args=%s", self.server_id, self.command, self.args)

        fs_npx = _is_npx_server_filesystem_args(self.args)
        max_att = _npx_filesystem_connect_max_attempts() if fs_npx else 1
        last_err: Optional[BaseException] = None

        for attempt in range(max_att):
            try:
                await self._connect_stdio_once()
                logger.info("[MCP] Server 已连接 server_id=%s", self.server_id)
                return
            except Exception as e:
                last_err = e
                await self.close()
                self._exit_stack = AsyncExitStack()
                retriable = (
                    fs_npx
                    and attempt + 1 < max_att
                    and _npx_filesystem_initialize_failure_retriable(e)
                )
                if retriable:
                    delay = min(4.0, 0.55 * (2**attempt))
                    logger.warning(
                        "[MCP] server-filesystem 握手失败，%.1fs 后重试 (%d/%d) server_id=%s err=%s",
                        delay,
                        attempt + 1,
                        max_att,
                        self.server_id,
                        e,
                    )
                    await asyncio.sleep(delay)
                    continue
                if fs_npx:
                    logger.warning(
                        "[MCP] Server 连接失败（server-filesystem 不重试或已达上限）server_id=%s err=%s",
                        self.server_id,
                        e,
                    )
                else:
                    logger.exception("[MCP] Server 连接失败 server_id=%s err=%s", self.server_id, e)
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
            # 显式传空分页参数，避免 mcp SDK 序列化时省略 params 导致部分 Node 版
            # @modelcontextprotocol/server-filesystem 在 tools/list 阶段异常退出（Connection closed）。
            response = await self._session.list_tools(params=mcp_types.PaginatedRequestParams())
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
        arguments = normalize_mcp_schema_aliases(name, arguments or {})
        _diag = format_mcp_tool_args_for_log(name, arguments)
        _ct = f"stdio-{time.time_ns():x}"
        logger.info(
            "[MCP] call_tool trace=%s server_id=%s name=%s diag=%s",
            _ct,
            self.server_id,
            name,
            _diag,
        )
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
            # 禁止向上抛出：海外网络/站点封禁时子进程或传输层异常会击穿 ReAct，导致进程静默退出
            return (
                f"[MCP 工具错误] {type(e).__name__}: {e}\n"
                "（若访问境内站点，海外 IP 可能被拒绝、重置连接或长时间挂起；请换网络或稍后重试。）"
            )

    async def close(self) -> None:
        """优雅关闭进程与连接。"""
        logger.debug("[MCP] 关闭 Server server_id=%s", self.server_id)
        try:
            await self._exit_stack.aclose()
        except asyncio.CancelledError:
            raise
        except BaseException as e:
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            msg = str(e).lower()
            et = type(e).__name__
            # MCP SDK：stdio_client 在 Ctrl+C / 事件循环收尾时异步生成器清理易触发竞态
            # 见 https://github.com/modelcontextprotocol/python-sdk/issues/521
            if (
                "cancel scope" in msg
                or "asynchronous generator" in msg
                or "closing of asynchronous" in msg
            ):
                logger.debug("[MCP] SDK 关闭竞态（可忽略）server_id=%s %s", self.server_id, et)
            elif isinstance(e, RuntimeError):
                logger.debug("[MCP] 关闭 RuntimeError server_id=%s err=%s", self.server_id, e)
            else:
                logger.warning("[MCP] 关闭 Server 异常 server_id=%s err=%s", self.server_id, e)
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
        # mcp_servers.json 热更新：mtime 变化时重读；已连接的 server_id 跳过，避免重复握手
        self._mcp_cfg_loaded_mtime: Optional[float] = None
        self._cached_mcp_servers: list[dict[str, Any]] = []

    def _load_config(self) -> list[dict[str, Any]]:
        """读取 mcp_servers.json 配置。"""
        if not self._config_path.exists():
            logger.info("[MCP] 配置文件不存在 path=%s，跳过 MCP 初始化", self._config_path)
            return []
        try:
            # utf-8-sig：兼容 Windows 记事本等保存的带 BOM 文件，避免整表解析失败
            raw = self._config_path.read_text(encoding="utf-8-sig")
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

    def _config_file_mtime(self) -> float:
        try:
            return self._config_path.stat().st_mtime
        except OSError:
            return -1.0

    async def _connect_single_stdio_server(self, cfg: dict[str, Any]) -> None:
        """
        连接单个 stdio MCP（已由调用方去重 server_id）。
        """
        server_id = str(cfg.get("id") or cfg.get("name") or "unknown").strip()
        if server_id in self._instances:
            return
        command = cfg.get("command", "")
        args = cfg.get("args") or []
        env = cfg.get("env")
        if not command:
            logger.warning("[MCP] 跳过无效配置 server_id=%s 缺少 command", server_id)
            return
        try:
            from core.mcp_embedded_runtime import preflight_mcp_stdio_command, resolve_mcp_stdio_command

            command = resolve_mcp_stdio_command(str(command).strip())
            ok_pf, pf_msg = preflight_mcp_stdio_command(command, server_id)
            if not ok_pf:
                if server_id == "sqlite_manager" and "uvx" in (command or "").lower():
                    logger.warning("[MCP] 跳过 sqlite_manager（无 uvx）：%s", pf_msg)
                else:
                    logger.error("[MCP] %s", pf_msg)
                return
        except Exception as e:
            logger.warning("[MCP] 运行时解析/预检异常 server_id=%s err=%s，跳过该 Server", server_id, e)
            return
        args_list = args if isinstance(args, list) else []
        try:
            from core.inventory_scanner import _prune_mcp_filesystem_roots, _prune_mcp_git_repository_args

            pruned = _prune_mcp_filesystem_roots(list(args_list))
            if pruned is None:
                logger.warning(
                    "[MCP] 跳过 server_id=%s：server-filesystem 无有效根目录（路径须存在且为目录）",
                    server_id,
                )
                return
            gpr = _prune_mcp_git_repository_args(pruned)
            if gpr is None:
                logger.warning(
                    "[MCP] 跳过 server_id=%s：mcp_server_git 的 --repository 不是有效 Git 工作区"
                    "（该路径下需存在 .git；或把仓库改为已 git init / git clone 的目录，例如项目根）",
                    server_id,
                )
                return
            args_list = gpr
        except Exception:
            pass
        miss_py = _stdio_args_reference_missing_py_file(args_list)
        if miss_py:
            logger.warning(
                "[MCP] 跳过 server_id=%s：入口脚本不存在 path=%s（请修正 ~/.jachin/mcp_servers.json 或运行 scripts/repair-mcp-servers.ps1）",
                server_id,
                miss_py,
            )
            return
        miss_mod = _stdio_missing_dash_m_module(args_list)
        if miss_mod:
            logger.error(
                "[MCP] 跳过 server_id=%s：未安装 Python 模块 %r（例：mcp_server_fetch → pip install mcp-server-fetch）",
                server_id,
                miss_mod,
            )
            return
        instance = MCPServerInstance(
            server_id=server_id,
            command=command,
            args=args_list,
            env=env,
        )
        try:
            _log_tavily_before_stdio_connect(server_id, args_list, env)
            await instance.connect()
            tools = await instance.list_tools()
        except MCPConnectionError as e:
            logger.warning("[MCP] Server 启动失败 server_id=%s err=%s", server_id, e)
            try:
                await instance.close()
            except Exception:
                pass
            return
        except Exception as e:
            logger.exception("[MCP] Server 启动异常 server_id=%s err=%s", server_id, e)
            try:
                await instance.close()
            except Exception:
                pass
            return
        if server_id in self._instances:
            try:
                await instance.close()
            except Exception:
                pass
            return
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
                logger.debug(
                    "[MCP] 工具名冲突 name=%s 已由 %s 提供，跳过 %s",
                    name,
                    self._tool_route[name].server_id,
                    server_id,
                )

    async def start(self) -> None:
        """
        读取配置，并发拉起所有 MCP Server，构建工具路由表。
        内置工具优先注册，外部 Server 工具不覆盖同名内置工具。

        可安全多次调用：仅在 mcp_servers.json 变更时重读磁盘；已连接的 server_id 会跳过。
        解决「先起 L3、后写 mcp_servers.json」时首包 start 早退导致永不加载 stdio MCP 的问题。
        """
        try:
            from core.l3_dotenv_merge import merge_l3_dotenv_into_os

            merge_l3_dotenv_into_os()
        except Exception as e:
            logger.debug("[MCP] dotenv merge at start: %s", e)
        self._register_builtin_tools()
        try:
            from l3_node.paths import get_app_root

            from core.mcp_json_repair import (
                repair_hr_atomic_tools_path,
                repair_official_fetch_ignore_robots_arg,
            )

            _cfg_repaired = repair_hr_atomic_tools_path(get_app_root())
            _cfg_repaired = repair_official_fetch_ignore_robots_arg() or _cfg_repaired
            if _cfg_repaired:
                self._mcp_cfg_loaded_mtime = None
                logger.info("[MCP] mcp_json_repair 已更新 ~/.jachin/mcp_servers.json，将重载 stdio 配置")
        except Exception as e:
            logger.debug("[MCP] mcp_json_repair at start: %s", e)
        mt = self._config_file_mtime()
        if self._mcp_cfg_loaded_mtime != mt:
            self._mcp_cfg_loaded_mtime = mt
            self._cached_mcp_servers = self._load_config()
        servers = self._cached_mcp_servers
        if not servers:
            return
        pending = [
            c
            for c in servers
            if isinstance(c, dict)
            and (c.get("id") or c.get("name") or "unknown") not in self._instances
        ]
        if pending:
            # 失败重试时可能每轮 ReAct 都进入；详情见下方 warning；此处用 debug 降噪
            logger.debug("[MCP] 尝试连接 %d 个尚未就绪的 stdio Server（配置共 %d 条）", len(pending), len(servers))
        _instances_before = len(self._instances)
        # 按 server_id 去重后并发连接，避免多个 npx stdio 顺序阻塞（总墙钟≈最慢一个）
        id_to_cfg: dict[str, dict[str, Any]] = {}
        for cfg in servers:
            if not isinstance(cfg, dict):
                continue
            try:
                from core.mcp_embedded_runtime import resolve_mcp_cfg_placeholders

                rcfg = resolve_mcp_cfg_placeholders(dict(cfg))
            except Exception:
                rcfg = dict(cfg)
            sid = str(rcfg.get("id") or rcfg.get("name") or "unknown").strip()
            if not sid or sid == "unknown":
                continue
            if sid in self._instances:
                continue
            if sid in id_to_cfg:
                continue
            id_to_cfg[sid] = rcfg
        if id_to_cfg:
            # 串行连接：多路 npx @modelcontextprotocol/server-filesystem 并发时，Windows 上 npm 缓存/
            # 子进程握手易竞态，表现为 initialize 阶段 Connection closed（见 inventory 与默认 workspace FS）。
            for c in id_to_cfg.values():
                try:
                    await self._connect_single_stdio_server(c)
                except Exception as e:
                    logger.debug("[MCP] 单 Server 连接链异常（已忽略）server_id=%s err=%s", c.get("id"), e)
        if len(self._instances) > _instances_before:
            logger.info(
                "[MCP] stdio Server 已连接，当前 instances=%d tools=%d",
                len(self._instances),
                len(self._tool_route),
            )
        elif pending:
            logger.debug(
                "[MCP] 本轮 stdio 连接尝试结束 instances=%d tools=%d",
                len(self._instances),
                len(self._tool_route),
            )

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
        self._mcp_cfg_loaded_mtime = None
        self._cached_mcp_servers = []
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

    def can_invoke_stdio_tool(self, tool_name: str) -> bool:
        """本进程是否已挂载该工具（内置或 stdio Server）。"""
        n = (tool_name or "").strip()
        return bool(n) and (n in self._builtin_tool_names or n in self._tool_route)

    async def _reconnect_stdio_server_by_id(self, server_id: str) -> bool:
        """
        关闭并重新连接指定 stdio Server。
        用于：首次连接时父进程尚无 TAVILY_API_KEY，子进程已以空环境启动；合并 .env 后需换新子进程。
        """
        sid = (server_id or "").strip()
        if not sid or sid == "unknown":
            return False
        old = self._instances.pop(sid, None)
        if old:
            try:
                await old.close()
            except Exception as e:
                logger.debug("[MCP] reconnect close server_id=%s err=%s", sid, e)
        for tn in list(self._tool_route.keys()):
            inst = self._tool_route.get(tn)
            if inst is not None and getattr(inst, "server_id", None) == sid:
                del self._tool_route[tn]
                if tn in self._tools_cache and tn not in self._builtin_tool_names:
                    del self._tools_cache[tn]
        servers = self._cached_mcp_servers or self._load_config()
        for cfg in servers:
            if not isinstance(cfg, dict):
                continue
            try:
                from core.mcp_embedded_runtime import resolve_mcp_cfg_placeholders

                rc = resolve_mcp_cfg_placeholders(dict(cfg))
            except Exception:
                rc = dict(cfg)
            rsid = str(rc.get("id") or rc.get("name") or "").strip()
            if rsid == sid:
                await self._connect_single_stdio_server(rc)
                return sid in self._instances
        logger.warning("[MCP] reconnect: 配置中无 server_id=%s", sid)
        return False

    async def invoke_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """
        根据路由表将工具调用转发到对应 Server 执行。
        内置工具优先本地执行，无需外部 MCP Server。
        """
        args = normalize_mcp_schema_aliases(tool_name, arguments or {})
        _mt = f"mgr-{time.time_ns():x}"
        logger.info(
            "[MCP] invoke_tool trace=%s route name=%s builtin=%s diag=%s",
            _mt,
            tool_name,
            tool_name in self._builtin_tool_names,
            format_mcp_tool_args_for_log(tool_name, args),
        )
        if tool_name in self._builtin_tool_names:
            from core.mcp_builtin_tools import invoke_builtin_tool
            return await invoke_builtin_tool(tool_name, args)
        instance = self._tool_route.get(tool_name)
        if not instance:
            raise MCPToolNotFoundError(f"工具 '{tool_name}' 未找到或未挂载")
        if "tavily" in (tool_name or "").lower():
            from core.mcp_embedded_runtime import log_tavily_mcp_chain

            log_tavily_mcp_chain(
                "invoke_tool_before_call",
                getattr(instance, "server_id", "?"),
                instance.env if isinstance(getattr(instance, "env", None), dict) else None,
                log_parent_os=True,
            )
        result = await instance.call_tool(tool_name, args)
        if "tavily" in (tool_name or "").lower():
            from core.mcp_embedded_runtime import log_tavily_invoke_outcome

            log_tavily_invoke_outcome(tool_name, result if isinstance(result, str) else str(result))
        if (
            isinstance(result, str)
            and "TAVILY_API_KEY" in result
            and ("-32600" in result or "environment variable is required" in result.lower())
            and "tavily" in (tool_name or "").lower()
        ):
            try:
                from core.l3_dotenv_merge import merge_l3_dotenv_into_os

                merge_l3_dotenv_into_os()
                if (os.environ.get("TAVILY_API_KEY") or "").strip():
                    _sid = getattr(instance, "server_id", "") or ""
                    logger.warning(
                        "[MCP] Tavily 报缺 Key，已合并 .env，尝试重连 stdio server_id=%s 并重试一次",
                        _sid,
                    )
                    if await self._reconnect_stdio_server_by_id(_sid):
                        inst2 = self._tool_route.get(tool_name)
                        if inst2:
                            return await inst2.call_tool(tool_name, args)
            except Exception as e:
                logger.debug("[MCP] Tavily 重连重试跳过: %s", e)
        return result

    async def add_server(self, cfg: dict[str, Any]) -> bool:
        """
        动态注入单个 MCP Server 配置（供 Inventory 侧载使用）。
        创建实例、连接、注册工具。返回 True 表示成功。
        """
        try:
            from core.mcp_embedded_runtime import resolve_mcp_cfg_placeholders

            cfg = resolve_mcp_cfg_placeholders(dict(cfg))
        except Exception:
            cfg = dict(cfg)
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
        try:
            from core.mcp_embedded_runtime import preflight_mcp_stdio_command, resolve_mcp_stdio_command

            command = resolve_mcp_stdio_command(str(command).strip())
            ok_pf, pf_msg = preflight_mcp_stdio_command(command, server_id)
            if not ok_pf:
                logger.error("[MCP] add_server 预检失败: %s", pf_msg)
                return False
        except Exception as e:
            logger.warning("[MCP] add_server 运行时解析/预检异常 server_id=%s err=%s", server_id, e)
            return False
        args_list = args if isinstance(args, list) else []
        try:
            from core.inventory_scanner import _prune_mcp_filesystem_roots, _prune_mcp_git_repository_args

            pruned = _prune_mcp_filesystem_roots(list(args_list))
            if pruned is None:
                logger.warning(
                    "[MCP] add_server 跳过 server_id=%s：server-filesystem 无有效根目录",
                    server_id,
                )
                return False
            gpr = _prune_mcp_git_repository_args(pruned)
            if gpr is None:
                logger.warning(
                    "[MCP] add_server 跳过 server_id=%s：mcp_server_git 的 --repository 不是有效 Git 工作区",
                    server_id,
                )
                return False
            args_list = gpr
        except Exception:
            pass
        miss_py = _stdio_args_reference_missing_py_file(args_list)
        if miss_py:
            logger.warning(
                "[MCP] add_server 跳过 server_id=%s：入口脚本不存在 path=%s",
                server_id,
                miss_py,
            )
            return False
        miss_mod = _stdio_missing_dash_m_module(args_list)
        if miss_mod:
            logger.error(
                "[MCP] add_server 跳过 server_id=%s：未安装 Python 模块 %r（例：pip install mcp-server-fetch）",
                server_id,
                miss_mod,
            )
            return False
        instance = MCPServerInstance(
            server_id=server_id,
            command=command,
            args=args_list,
            env=env,
        )
        try:
            _log_tavily_before_stdio_connect(server_id, args_list, env)
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
