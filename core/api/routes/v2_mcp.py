"""
Jachin Nexus V2 - L2 MCP 代理 API

暴露给 L3 调用的 HTTP 接口，将 MCP 工具调用路由到 L2 本地 MCP 服务器。
本机无技能时委托其他 L3 执行（路径 3：L3_LOCAL MCP 委托）。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.errors import ERR_BAD_REQUEST_001, ERR_MCP_001, ERR_MCP_002, api_error
from core.usage_telemetry import record_usage_async
from core.mcp_client import (
    MCPConnectionError,
    MCPToolNotFoundError,
    get_mcp_manager,
)
from core.l3_redis_state import get_l3_nodes_with_mcp_tool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/mcp", tags=["mcp"])


def _get_sub_account_id(request: Request) -> Optional[str]:
    """从 X-Sub-Account-Id 或 Authorization: Bearer 提取子账号 ID"""
    sub = request.headers.get("X-Sub-Account-Id", "").strip()
    if sub:
        return sub
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip() or None
    return None


class InvokeRequest(BaseModel):
    """POST /invoke 请求体"""

    tool_name: str = Field(..., description="MCP 工具名称")
    arguments: dict[str, Any] = Field(default_factory=dict, description="工具参数")


@router.get("/tools")
async def list_mcp_tools() -> dict[str, Any]:
    """
    获取所有已挂载的 MCP 工具列表。
    调用 MCPManager 汇总各 Server 的工具。
    """
    manager = get_mcp_manager()
    try:
        tools = manager.get_all_tools()
        if not tools:
            tools = await manager.list_tools_async()
        logger.info("[MCP API] GET /tools 返回 count=%d", len(tools))
        return {
            "tools": tools,
            "count": len(tools),
            "servers": manager.server_count,
        }
    except Exception as e:
        logger.exception("[MCP API] GET /tools 异常 err=%s", e)
        raise api_error(500, ERR_MCP_002, "获取 MCP 工具列表失败", detail=str(e))


@router.post("/invoke")
async def invoke_mcp_tool(request: Request, body: InvokeRequest) -> dict[str, Any]:
    """
    执行 MCP 工具。
    MVP: X-Sub-Account-Id 可选；无身份时直接放行。
    """
    tool_name = (body.tool_name or "").strip()
    if not tool_name:
        raise api_error(400, ERR_BAD_REQUEST_001, "tool_name 不能为空")

    sub_account_id = _get_sub_account_id(request)

    # MVP: 权限分配已隐藏，有身份即放行，直接全量下发
    if sub_account_id:
        from core.db import get_connection
        try:
            conn = get_connection()
            try:
                row = conn.execute(
                    "SELECT id, is_active FROM sub_accounts WHERE id = ?",
                    (sub_account_id,),
                ).fetchone()
                if not row:
                    raise api_error(
                        401,
                        "ERR_SUB_ACCOUNT_NOT_FOUND",
                        "【安全拦截】未找到有效的子账号凭证，请重新登录或联系管理员。",
                    )
                if row[1] == 0:
                    raise api_error(403, "ERR_ACCOUNT_DISABLED", "【安全拦截】您的账号已被禁用，无权调用任何接口。")
            finally:
                conn.close()
        except HTTPException:
            raise
        except Exception as e:
            logger.warning("[MCP API] 查询子账号失败 sub_account_id=%s err=%s", sub_account_id[:16], e)

    manager = get_mcp_manager()
    server_id = manager.get_server_id_for_tool(tool_name)
    item_id = f"mcp:{server_id}" if server_id else f"mcp:unknown"

    arguments = body.arguments or {}
    eff_sub = sub_account_id or "anon"
    logger.info("[MCP API] POST /invoke tool_name=%s sub=%s item_id=%s", tool_name, eff_sub[:16], item_id)

    t0 = time.perf_counter()
    timeout_sec = 60.0
    try:
        result = await asyncio.wait_for(
            manager.invoke_tool(tool_name, arguments),
            timeout=timeout_sec,
        )
        latency_ms = (time.perf_counter() - t0) * 1000
        record_usage_async(eff_sub, item_id, tool_name, "success", latency_ms)
        logger.debug("[MCP API] invoke 成功 tool_name=%s result_len=%d", tool_name, len(result) if result else 0)
        return {
            "ok": True,
            "tool_name": tool_name,
            "result": result,
        }
    except MCPToolNotFoundError as e:
        delegate_ok = False
        if sub_account_id:
            nodes = get_l3_nodes_with_mcp_tool(sub_account_id, tool_name)
            if nodes:
                l3_url = (nodes[0].get("l3_http_url") or "").strip().rstrip("/")
                if l3_url:
                    execute_url = f"{l3_url}/api/v3/mcp/execute"
                    logger.info("[MCP API] 本机无工具，委托 L3 tool=%s url=%s", tool_name, execute_url)
                    try:
                        async with httpx.AsyncClient(timeout=timeout_sec) as client:
                            r = await client.post(
                                execute_url,
                                json={"tool_name": tool_name, "arguments": arguments},
                            )
                            r.raise_for_status()
                            data = r.json()
                            if data.get("ok"):
                                result = data.get("result", "")
                                latency_ms = (time.perf_counter() - t0) * 1000
                                record_usage_async(eff_sub, f"mcp:delegate:{nodes[0].get('node_id','')}", tool_name, "success", latency_ms)
                                return {"ok": True, "tool_name": tool_name, "result": result, "delegated": True}
                            delegate_ok = False
                    except Exception as delegate_e:
                        logger.warning("[MCP API] 委托 L3 失败 tool=%s err=%s", tool_name, delegate_e)
        if not delegate_ok:
            latency_ms = (time.perf_counter() - t0) * 1000
            record_usage_async(eff_sub, item_id, tool_name, "failure", latency_ms)
            logger.warning("[MCP API] 工具未找到 tool_name=%s", tool_name)
            raise api_error(404, ERR_MCP_001, str(e))
    except asyncio.TimeoutError:
        latency_ms = (time.perf_counter() - t0) * 1000
        record_usage_async(eff_sub, item_id, tool_name, "failure", latency_ms)
        logger.warning("[MCP API] 调用超时 tool_name=%s", tool_name)
        raise api_error(500, ERR_MCP_002, "MCP 工具调用超时")
    except MCPConnectionError as e:
        latency_ms = (time.perf_counter() - t0) * 1000
        record_usage_async(eff_sub, item_id, tool_name, "failure", latency_ms)
        logger.warning("[MCP API] MCP 连接错误 tool_name=%s err=%s", tool_name, e)
        raise api_error(500, ERR_MCP_002, str(e))
    except Exception as e:
        latency_ms = (time.perf_counter() - t0) * 1000
        record_usage_async(eff_sub, item_id, tool_name, "failure", latency_ms)
        logger.exception("[MCP API] invoke 异常 tool_name=%s err=%s", tool_name, e)
        raise api_error(500, ERR_MCP_002, f"MCP 工具执行失败: {e}")
