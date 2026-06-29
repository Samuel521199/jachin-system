"""
MCP stdio：官方 SDK 将子进程 stdout 按行解析为 JSON-RPC。
部分环境（npx 包装、安全/代理软件）会在 MCP 协议输出前向 stdout 打印非 JSON 行
（例如「◇ injected env …」），导致 ValidationError 与 ERROR 级堆栈。

在首次连接 stdio MCP 前替换 ``mcp.client.stdio.stdio_client``，对**明显非 JSON** 的行仅记录
warning 并跳过，不中断会话。

与 mcp 包版本耦合：循环逻辑同步自 ``mcp.client.stdio`` 的 ``stdio_client``。

架构说明见 ``docs/architecture/CURRENT_SYSTEM_ARCHITECTURE.md`` §4。
"""
from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from importlib import import_module
from pathlib import Path
from typing import TextIO

import anyio
import anyio.lowlevel
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from anyio.streams.text import TextReceiveStream


def _import_official_mcp_modules():
    """Import the PyPI ``mcp`` SDK even if ``l3_node/primitives`` shadows it.

    Some desktop/dev launch paths put ``l3_node/primitives`` on ``sys.path``.
    That makes ``import mcp`` resolve to Jachin's internal
    ``l3_node/primitives/mcp`` package, which has no ``types`` module and
    breaks the stdio host at startup.
    """
    repo_root = Path(__file__).resolve().parent.parent
    local_primitives = (repo_root / "l3_node" / "primitives").resolve()
    local_mcp = (local_primitives / "mcp").resolve()

    def _is_local_mcp_module(mod: object) -> bool:
        raw = getattr(mod, "__file__", "") or ""
        if not raw:
            return False
        try:
            Path(raw).resolve().relative_to(local_mcp)
            return True
        except (OSError, ValueError):
            return False

    removed_paths: list[tuple[int, str]] = []
    for i, raw in reversed(list(enumerate(sys.path))):
        try:
            if Path(raw or ".").resolve() == local_primitives:
                removed_paths.append((i, raw))
                sys.path.pop(i)
        except OSError:
            continue

    if _is_local_mcp_module(sys.modules.get("mcp")):
        for name in [k for k in sys.modules if k == "mcp" or k.startswith("mcp.")]:
            sys.modules.pop(name, None)

    try:
        types_mod = import_module("mcp.types")
        stdio_mod = import_module("mcp.client.stdio")
        message_mod = import_module("mcp.shared.message")
    finally:
        for i, raw in sorted(removed_paths):
            if raw not in sys.path:
                sys.path.insert(min(i, len(sys.path)), raw)

    return types_mod, stdio_mod, message_mod


types, _mcp_stdio_mod, _mcp_message_mod = _import_official_mcp_modules()
PROCESS_TERMINATION_TIMEOUT = _mcp_stdio_mod.PROCESS_TERMINATION_TIMEOUT
StdioServerParameters = _mcp_stdio_mod.StdioServerParameters
_create_platform_compatible_process = _mcp_stdio_mod._create_platform_compatible_process
_get_executable_command = _mcp_stdio_mod._get_executable_command
_terminate_process_tree = _mcp_stdio_mod._terminate_process_tree
get_default_environment = _mcp_stdio_mod.get_default_environment
SessionMessage = _mcp_message_mod.SessionMessage

logger = logging.getLogger(__name__)

_PATCH_ATTR = "_jachin_stdio_noise_filter_applied"


def _line_looks_like_json_rpc(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    return s[0] in "{["


@asynccontextmanager
async def _stdio_client_skip_noise(server: StdioServerParameters, errlog: TextIO = sys.stderr):
    read_stream: MemoryObjectReceiveStream[SessionMessage | Exception]
    read_stream_writer: MemoryObjectSendStream[SessionMessage | Exception]

    write_stream: MemoryObjectSendStream[SessionMessage]
    write_stream_reader: MemoryObjectReceiveStream[SessionMessage]

    read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
    write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

    try:
        command = _get_executable_command(server.command)

        process = await _create_platform_compatible_process(
            command=command,
            args=server.args,
            env=({**get_default_environment(), **server.env} if server.env is not None else get_default_environment()),
            errlog=errlog,
            cwd=server.cwd,
        )
    except OSError:
        await read_stream.aclose()
        await write_stream.aclose()
        await read_stream_writer.aclose()
        await write_stream_reader.aclose()
        raise

    async def stdout_reader():
        assert process.stdout, "Opened process is missing stdout"

        try:
            async with read_stream_writer:
                buffer = ""
                async for chunk in TextReceiveStream(
                    process.stdout,
                    encoding=server.encoding,
                    errors=server.encoding_error_handler,
                ):
                    lines = (buffer + chunk).split("\n")
                    buffer = lines.pop()

                    for line in lines:
                        if not _line_looks_like_json_rpc(line):
                            preview = line.strip()[:200]
                            if preview:
                                logger.warning(
                                    "[MCP] 忽略子进程 stdout 非 JSON-RPC 行（可能被 npx/环境注入），前 200 字: %s",
                                    preview,
                                )
                            continue
                        try:
                            message = types.JSONRPCMessage.model_validate_json(line)
                        except Exception as exc:
                            logger.exception("Failed to parse JSONRPC message from server")
                            await read_stream_writer.send(exc)
                            continue

                        session_message = SessionMessage(message)
                        await read_stream_writer.send(session_message)
        except anyio.ClosedResourceError:
            await anyio.lowlevel.checkpoint()

    async def stdin_writer():
        assert process.stdin, "Opened process is missing stdin"

        try:
            async with write_stream_reader:
                async for session_message in write_stream_reader:
                    json = session_message.message.model_dump_json(by_alias=True, exclude_none=True)
                    await process.stdin.send(
                        (json + "\n").encode(
                            encoding=server.encoding,
                            errors=server.encoding_error_handler,
                        )
                    )
        except anyio.ClosedResourceError:
            await anyio.lowlevel.checkpoint()

    async with (
        anyio.create_task_group() as tg,
        process,
    ):
        tg.start_soon(stdout_reader)
        tg.start_soon(stdin_writer)
        try:
            yield read_stream, write_stream
        finally:
            if process.stdin:
                try:
                    await process.stdin.aclose()
                except Exception:
                    pass

            try:
                with anyio.fail_after(PROCESS_TERMINATION_TIMEOUT):
                    await process.wait()
            except TimeoutError:
                await _terminate_process_tree(process)
            except ProcessLookupError:
                pass
            await read_stream.aclose()
            await write_stream.aclose()
            await read_stream_writer.aclose()
            await write_stream_reader.aclose()


def apply_stdio_stdout_noise_filter() -> None:
    """幂等：替换 ``mcp.client.stdio.stdio_client``。"""
    import mcp.client.stdio as mcp_stdio

    if getattr(mcp_stdio, _PATCH_ATTR, False):
        return
    mcp_stdio.stdio_client = _stdio_client_skip_noise
    setattr(mcp_stdio, _PATCH_ATTR, True)
    logger.debug("[MCP] 已启用 stdio stdout 非 JSON 行过滤（Jachin）")
