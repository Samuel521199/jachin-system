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
_shutdown_hook_registered: bool = False


async def start_l3_stdio_mcp_host() -> None:
    """
    启动本进程 MCPManager + ``scan_local_mcps(for_l2_host=False)``（仅首轮）。

    ``await mgr.start()`` **每次**调用：依赖 MCPManager 内 mtime 缓存与已连接 server 跳过，
    以便在 L3 已运行后再写入 ``mcp_servers.json`` 时仍能拉起 official-sqlite-npx 等 stdio 服务。
    """
    global _started, _shutdown_hook_registered
    try:
        from l3_node.nexus_config import sync_merge_sqlite_read_from_env_to_nexus_config

        sync_merge_sqlite_read_from_env_to_nexus_config()
    except Exception as e:
        logger.debug("[L3 MCP Host] nexus merge_sqlite 持久化跳过: %s", e)
    try:
        from core.mcp_client import get_mcp_manager
        from core.inventory_scanner import scan_local_mcps, ensure_inventory_dirs

        ensure_inventory_dirs()
        try:
            try:
                from core.l3_dotenv_merge import merge_l3_dotenv_into_os

                merge_l3_dotenv_into_os()
            except Exception as _de:
                logger.debug("[L3 MCP Host] dotenv merge（MCP 修补前）跳过: %s", _de)
            from l3_node.paths import get_app_root as _gar
            from core.mcp_embedded_runtime import ensure_jachin_workspace_my_life_sqlite_db
            from core.mcp_json_repair import (
                ensure_default_official_fetch_mcp,
                ensure_default_official_filesystem_mcp,
                ensure_sqlite_manager_life_db_mcp,
                repair_hr_atomic_tools_path,
                repair_official_fetch_ignore_robots_arg,
            )

            ensure_jachin_workspace_my_life_sqlite_db()
            ensure_default_official_filesystem_mcp()
            ensure_default_official_fetch_mcp()
            ensure_sqlite_manager_life_db_mcp()
            repair_official_fetch_ignore_robots_arg()
            repair_hr_atomic_tools_path(_gar())
        except Exception as e:
            logger.debug("[L3 MCP Host] mcp_json_repair 跳过: %s", e)
        mgr = get_mcp_manager()
        await mgr.start()

        if not _shutdown_hook_registered:
            _shutdown_hook_registered = True

            async def _shutdown_stop_mcp() -> None:
                try:
                    from core.mcp_client import get_mcp_manager as _gm

                    await _gm().stop()
                except Exception as e:
                    logger.debug("[L3 MCP Host] shutdown hook stop: %s", e)

            try:
                from l3_node.graceful_shutdown import register_shutdown_hook

                register_shutdown_hook(_shutdown_stop_mcp)
            except Exception as e:
                logger.debug("[L3 MCP Host] register_shutdown_hook 跳过: %s", e)

        if not _started:
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
        else:
            logger.debug(
                "[L3 MCP Host] MCPManager.start() 已重入（配置/补连）servers=%d tools=%d",
                mgr.server_count,
                mgr.tool_count,
            )
    except asyncio.CancelledError:
        logger.debug("[L3 MCP Host] 启动过程被取消（常见于 Windows stdio 子进程竞态），已中止本次引导")
        raise
    except Exception as e:
        logger.warning("[L3 MCP Host] 启动失败（可稍后重试）: %s", e, exc_info=True)


def reset_for_tests() -> None:
    global _started
    _started = False
