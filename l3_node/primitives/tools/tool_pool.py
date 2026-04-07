"""
L3 工具池：内置（Native + jpp）与 MCP 合并，供 run_agent 等单点调用。

规范见 docs/architecture/L3_TOOL_POOL_AND_MCP_ASSEMBLY.md。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from l3_node.primitives.tools.loader import is_tool_allowed, load_tools

if TYPE_CHECKING:
    from l3_node.primitives.mcp.registry import MCPToolRegistry


async def assemble_tool_pool(
    *,
    allowed_skills: list[str] | None,
    gateway_bundle: Any = None,
    bg_channel: str | None = None,
    mcp_registry: MCPToolRegistry | None = None,
    logger: logging.Logger | None = None,
) -> list[dict[str, Any]]:
    """
    阶段 A: load_tools；阶段 B: fetch_tools_from_l2（可因 RBAC 跳过）；阶段 C: 白名单过滤 MCP、追加、通道剔除。
    """
    log = logger or logging.getLogger(__name__)
    tools = load_tools(allowed_skills=allowed_skills)
    skip_mcp_for_rbac = False
    if gateway_bundle is not None:
        try:
            from l3_node.intent_gateway.rbac_precheck import precheck_l2_subintent_allowed

            loc = "prefer_l2" if gateway_bundle.extra.get("attachment_forced_l2_routing") else "local_only"
            ok_rbac, rbac_reason = precheck_l2_subintent_allowed(gateway_bundle, locality=loc)
            if not ok_rbac:
                skip_mcp_for_rbac = True
                log.warning(
                    "[L3 Agent] RBAC 预检拒绝合并 L2 MCP locality=%s reason=%s",
                    loc,
                    rbac_reason,
                )
        except Exception as e:
            log.debug("[L3 Agent] RBAC MCP 预检跳过: %s", e)

    try:
        if not skip_mcp_for_rbac:
            from l3_node.primitives.mcp.registry import get_mcp_registry

            reg = mcp_registry if mcp_registry is not None else get_mcp_registry()
            mcp_tools = await reg.fetch_tools_from_l2()
            if mcp_tools:
                if allowed_skills is not None:
                    mcp_tools = [t for t in mcp_tools if is_tool_allowed(t["id"], allowed_skills)]
                tools = list(tools) + mcp_tools
                log.info("[L3 Agent] 已合并 %d 个 MCP 工具，总计 %d", len(mcp_tools), len(tools))
    except Exception as e:
        log.debug("[L3 Agent] MCP 工具拉取跳过（L2 可能未启动）: %s", e)

    if bg_channel == "background_task":
        tools = [t for t in tools if (t.get("id") or "").strip().lower() != "core:submit_background_task"]
    return tools
