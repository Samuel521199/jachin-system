"""
L3 进程内启动官方/侧载 stdio MCP（复用 core.mcp_client.MCPManager）。

长期架构下 L2 默认不拉起 MCPManager；与 ``~/.jachin/mcp_servers.json`` 及
``~/.jachin/inventory/mcps`` 约定与原先 L2 一致。

须由 ``http_server.run_http_server`` 在 **后台 Task** 中调用本模块：在 Windows 打包环境下，
mcp/anyio 创建 stdio 子进程时可能抛出 ``asyncio.CancelledError``（非 ``Exception`` 子类）；
若在 ``asyncio.run(main)`` 的主协程链上 ``await``，会直接导致进程退出。
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("l3_node")

_started: bool = False


async def start_l3_stdio_mcp_host() -> None:
    """幂等：启动本进程 MCPManager + ``scan_local_mcps(for_l2_host=False)``。"""
    global _started
    if _started:
        return
    try:
        from core.mcp_client import get_mcp_manager
        from core.inventory_scanner import scan_local_mcps, ensure_inventory_dirs

        ensure_inventory_dirs()
        mgr = get_mcp_manager()
        await mgr.start()
        injected = await scan_local_mcps(for_l2_host=False)
        from l3_node.l3_packaged_stdio_mcp import register_l3_packaged_stdio_mcps

        packaged = await register_l3_packaged_stdio_mcps()
        _started = True
        logger.info(
            "[L3 MCP Host] stdio MCP 已就绪 servers=%d tools=%d inventory_injected=%d packaged_stdio=%d",
            mgr.server_count,
            mgr.tool_count,
            injected,
            packaged,
        )
    except asyncio.CancelledError:
        logger.debug("[L3 MCP Host] 启动过程被取消（常见于 Windows stdio 子进程竞态），已中止本次引导")
        raise
    except Exception as e:
        logger.warning("[L3 MCP Host] 启动失败（可稍后重试）: %s", e, exc_info=True)


def reset_for_tests() -> None:
    global _started
    _started = False
