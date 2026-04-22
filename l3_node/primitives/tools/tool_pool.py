"""
L3 工具池：内置（Native + jpp）与 MCP 合并，供 run_agent 等单点调用。

规范见 docs/architecture/L3_TOOL_POOL_AND_MCP_ASSEMBLY.md。

SQLite：stdio 的 mcp:read_query 或 npm「mcp-sqlite」的 mcp:query 等常因 allowed_skills 未列名被合并阶段剔除。
- 官方 @modelcontextprotocol/server-sqlite 在 npm 上不存在（404），请使用 npx -y mcp-sqlite <db路径>。
- 修复：JACHIN_MERGE_SQLITE_READ_INTO_TOOL_POOL=1 或 nexus agent.merge_sqlite_read_into_tool_pool=true。

本地 MCP（Puppeteer / browser-use 等）：配对 L2 且 permissions_snapshot.allowed_skills 非空时，
未写入白名单的 mcp: 工具会在合并阶段被剔除（常见症状：Agent 只见 PowerPoint 等少数 MCP）。
- 修复：JACHIN_MERGE_LOCAL_MCP_INTO_TOOL_POOL=1 或 nexus agent.merge_local_mcp_into_tool_pool=true（须设置在 **L3 进程** 环境并重启）。
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from l3_node.primitives.tools.loader import (
    MCP_SQLITE_MERGE_READISH_IDS,
    is_tool_allowed,
    load_tools,
    tool_entry_looks_like_sqlite_family,
)

if TYPE_CHECKING:
    from l3_node.primitives.mcp.registry import MCPToolRegistry

logger = logging.getLogger(__name__)


def implicit_local_mcp_merge_enabled() -> bool:
    """与 SQLite 同理：把 mcp:* 并入白名单，放行本地 mcp_servers 已注册的全部 MCP 工具 id。"""
    v = (os.environ.get("JACHIN_MERGE_LOCAL_MCP_INTO_TOOL_POOL") or "").strip().lower()
    if v in ("1", "true", "yes"):
        return True
    try:
        from l3_node.nexus_config import get_nexus_config

        cfg = get_nexus_config() or {}
        agent = cfg.get("agent") if isinstance(cfg.get("agent"), dict) else {}
        return bool(agent.get("merge_local_mcp_into_tool_pool", False))
    except Exception:
        return False


def expand_allowed_skills_with_local_mcp(allowed: list[str] | None) -> list[str] | None:
    """
    将 ``mcp:*`` 并入 allowed_skills，使 is_tool_allowed 放行任意 ``mcp:…`` 工具（仍须 MCP 子进程启动成功）。
    allowed 为 None（未配对/全开）时不修改。
    """
    if allowed is None:
        return None
    if not implicit_local_mcp_merge_enabled():
        return allowed
    seen = {str(x).strip().lower() for x in allowed if str(x).strip()}
    if "mcp:*" in seen:
        return allowed
    out = list(allowed) + ["mcp:*"]
    logger.info(
        "[L3 Agent] merge_local_mcp：已将 mcp:* 并入本 run 白名单 "
        "（JACHIN_MERGE_LOCAL_MCP_INTO_TOOL_POOL 或 nexus agent.merge_local_mcp_into_tool_pool）"
    )
    return out


def implicit_sqlite_read_merge_enabled() -> bool:
    v = (os.environ.get("JACHIN_MERGE_SQLITE_READ_INTO_TOOL_POOL") or "").strip().lower()
    if v in ("1", "true", "yes"):
        return True
    try:
        from l3_node.nexus_config import get_nexus_config

        cfg = get_nexus_config() or {}
        agent = cfg.get("agent") if isinstance(cfg.get("agent"), dict) else {}
        return bool(agent.get("merge_sqlite_read_into_tool_pool", False))
    except Exception:
        return False


def expand_allowed_skills_with_implicit_sqlite_read(allowed: list[str] | None) -> list[str] | None:
    """
    将 SQLite 只读相关工具 id 并入白名单（含 npm mcp-sqlite 的 mcp:query 等）。
    allowed 为 None（未配对全开）时不修改。
    """
    if allowed is None:
        return None
    if not implicit_sqlite_read_merge_enabled():
        return allowed
    seen: set[str] = {str(x).strip().lower() for x in allowed if str(x).strip()}
    out = list(allowed)
    added = False
    for e in MCP_SQLITE_MERGE_READISH_IDS:
        el = e.lower()
        if el not in seen:
            out.append(e)
            seen.add(el)
            added = True
    if added:
        logger.info(
            "[L3 Agent] merge_sqlite_read：已把 SQLite 只读相关工具 id 并入本 run 白名单 "
            "（含 mcp:read_query / mcp:query 等；JACHIN_MERGE_SQLITE_READ_INTO_TOOL_POOL 或 nexus agent.merge_sqlite_read_into_tool_pool）"
        )
    return out


async def assemble_tool_pool(
    *,
    allowed_skills: list[str] | None,
    gateway_bundle: Any = None,
    bg_channel: str | None = None,
    mcp_registry: MCPToolRegistry | None = None,
    logger: logging.Logger | None = None,
    allowlist_diag_source: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    阶段 A: load_tools；阶段 B: fetch_tools_from_l2（可因 RBAC 跳过）；阶段 C: 白名单过滤 MCP、追加、通道剔除。

    allowlist_diag_source: L2 原始白名单（read_query 隐式合并前）。默认 None 表示用 allowed_skills 做诊断；
        run_agent 应传入扩展前的列表以准确打出「被白名单剔除的 SQLite id」。
    """
    log = logger or logging.getLogger(__name__)
    _diag_src = allowlist_diag_source if allowlist_diag_source is not None else allowed_skills
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
                _pre_sqlite = {str(t.get("id") or "") for t in mcp_tools if tool_entry_looks_like_sqlite_family(t)}
                if _diag_src is not None and _pre_sqlite:
                    _pass_diag = {
                        str(t.get("id") or "")
                        for t in mcp_tools
                        if tool_entry_looks_like_sqlite_family(t)
                        and is_tool_allowed(str(t.get("id") or ""), _diag_src)
                    }
                    _dropped_sql = _pre_sqlite - _pass_diag
                    if _dropped_sql:
                        log.warning(
                            "[L3 Agent] MCP 注册表已暴露 SQLite 族 id=%s，但当前 L2 技能白名单未放行，合并时会被剔除。"
                            "请在 allowed_skills 中加入 mcp:read_query / mcp:query 等，或设置 "
                            "JACHIN_MERGE_SQLITE_READ_INTO_TOOL_POOL=1 / nexus agent.merge_sqlite_read_into_tool_pool。",
                            sorted(_dropped_sql),
                        )
                elif not _pre_sqlite:
                    log.debug(
                        "[L3 Agent] MCP 注册表当前无 SQLite 族（read_query / mcp:query 等）。"
                        "请检查 ~/.jachin/mcp_servers.json 是否使用 npx -y mcp-sqlite <db路径>（勿用已 404 的 @modelcontextprotocol/server-sqlite），"
                        "并确认 Node/npx 可用。"
                    )
                if allowed_skills is not None:
                    mcp_tools = [t for t in mcp_tools if is_tool_allowed(t["id"], allowed_skills)]
                tools = list(tools) + mcp_tools
                log.info("[L3 Agent] 已合并 %d 个 MCP 工具，总计 %d", len(mcp_tools), len(tools))
    except Exception as e:
        log.debug("[L3 Agent] MCP 工具拉取跳过（L2 可能未启动）: %s", e)

    _sqlite_ids = [str(t.get("id") or "") for t in tools if tool_entry_looks_like_sqlite_family(t)]
    if _sqlite_ids:
        log.info("[L3 Agent] 工具池已含 SQLite 族 MCP: %s", _sqlite_ids)
    else:
        log.info(
            "[L3 Agent] 工具池未含 SQLite 族（read_query、mcp:query 等）。"
            "常见原因：mcp_servers.json 仍指向已下架的 @modelcontextprotocol/server-sqlite（npm 404），"
            "应改为 npx -y mcp-sqlite <db>；或 stdio 启动失败；或有 allowed_skills 白名单未放行（可开 merge_sqlite_read_into_tool_pool）。"
        )

    if bg_channel == "background_task":
        tools = [t for t in tools if (t.get("id") or "").strip().lower() != "core:submit_background_task"]
    return tools
