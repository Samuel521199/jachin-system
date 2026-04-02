"""
Jachin Nexus V2 - L2 MCP 代理 API

暴露给 L3 的 HTTP：若 ``JACHIN_L2_STDIO_MCP=1`` 可先走 L2 本机 MCPManager；否则工具不在 L2 本机时：

1. **Pull 队列（优先）**：Redis ``l3_mcp_delegate_queue:{node_id}`` + L3 轮询
   ``GET /api/v2/mcp/delegate/poll``，结果经 ``POST .../delegate/result`` 回写（NAT 友好）。
   载荷含 L2 签发的 **Task Token**（``core/mcp_task_token.py``），执行端校验 task/tool/node/sub。
2. **HTTP POST 兼容（NAT 降级）**：对带 ``l3_http_url`` 的节点 ``POST /api/v3/mcp/execute``，同样携带 ``task_id`` + ``task_token``。

委托目标须同时在 Redis 心跳与 SQLite ``l3_nodes`` 中归属当前子账号（``core/l3_node_db_filter.py``）。
标记 **LOCAL_PINNED** 的工具（``core/mcp_tool_locality.py``）禁止跨节点委托。

见 docs/ARCHITECTURE_L3_MCP_HOST_AND_L2_TASK_MANAGER.md。``JACHIN_MCP_DELEGATE_PULL=0`` 关闭 Pull 优先。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from core.db import get_connection
from core.errors import (
    ERR_AUTH_001,
    ERR_AUTH_004,
    ERR_BAD_REQUEST_001,
    ERR_MCP_001,
    ERR_MCP_002,
    api_error,
)
from core.usage_telemetry import record_usage_async
from core.l2_stdio_mcp_flag import l2_stdio_mcp_enabled
from core.l3_node_db_filter import filter_l3_nodes_assigned_in_db
from core.mcp_client import (
    MCPConnectionError,
    MCPToolNotFoundError,
    get_mcp_manager,
)
from core.l3_redis_state import (
    aggregate_mcp_tools_catalog_from_redis,
    delete_mcp_delegate_lease,
    get_l3_nodes_with_mcp_tool,
    get_mcp_delegate_lease,
    get_mcp_delegate_result,
    pop_mcp_delegate_tasks,
    push_mcp_delegate_task,
    set_mcp_delegate_lease,
    set_mcp_delegate_result,
)
from core.mcp_task_token import mint_mcp_delegate_task_token
from core.mcp_tool_locality import is_tool_local_pinned
from core.redis_manager import get_redis_client

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


class McpDelegateResultBody(BaseModel):
    """POST /delegate/result — 执行节点回写 MCP 代跑结果"""

    task_id: str = Field(..., min_length=8)
    node_id: str = Field(..., min_length=1)
    ok: bool
    result: Optional[str] = None
    error: Optional[str] = None
    error_class: Optional[str] = None


def _mcp_pull_delegate_enabled() -> bool:
    return os.environ.get("JACHIN_MCP_DELEGATE_PULL", "1").strip().lower() not in ("0", "false", "no")


async def _wait_mcp_delegate_result(task_id: str, deadline_monotonic: float) -> Optional[dict[str, Any]]:
    while time.monotonic() < deadline_monotonic:
        res = get_mcp_delegate_result(task_id)
        if res is not None:
            return res
        await asyncio.sleep(0.2)
    return None


async def _delegate_mcp_via_pull_queue_first(
    *,
    sub_account_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    eff_sub: str,
    item_id: str,
    timeout_sec: float,
    t0: float,
) -> Optional[dict[str, Any]]:
    """
    返回 None 表示未走通 Pull，应回退 HTTP。
    返回 dict 则直接作为 invoke 响应（或已在内联 raise）。
    """
    if not _mcp_pull_delegate_enabled():
        return None
    if not get_redis_client():
        return None
    nodes = get_l3_nodes_with_mcp_tool(sub_account_id, tool_name, require_l3_http_url=False)
    nodes = filter_l3_nodes_assigned_in_db(sub_account_id, nodes)
    if not nodes:
        return None
    wait_budget = min(max(timeout_sec - 4.0, 8.0), 56.0)
    for node in nodes:
        nid = (node.get("node_id") or "").strip()
        if not nid:
            continue
        task_id = str(uuid.uuid4())
        task_token = mint_mcp_delegate_task_token(
            task_id=task_id,
            tool_name=tool_name,
            executor_node_id=nid,
            sub_account_id=sub_account_id,
        )
        payload: dict[str, Any] = {
            "kind": "mcp_delegate",
            "task_id": task_id,
            "tool_name": tool_name,
            "arguments": arguments,
            "sub_account_id": sub_account_id,
            "task_token": task_token,
            "created_at": time.time(),
        }
        if not push_mcp_delegate_task(nid, payload):
            continue
        logger.info("[MCP API] Pull 队列已投递 tool=%s target_node=%s task_id=%s", tool_name, nid, task_id)
        deadline = time.monotonic() + wait_budget
        res = await _wait_mcp_delegate_result(task_id, deadline)
        if res is None:
            logger.warning("[MCP API] Pull 队列等待超时 tool=%s node=%s task_id=%s", tool_name, nid, task_id)
            continue
        if res.get("ok"):
            result = res.get("result", "")
            latency_ms = (time.perf_counter() - t0) * 1000
            record_usage_async(eff_sub, f"mcp:pull:{nid}", tool_name, "success", latency_ms)
            return {
                "ok": True,
                "tool_name": tool_name,
                "result": result,
                "delegated": True,
                "via": "pull_queue",
                "executor_node_id": nid,
            }
        err_cls = (res.get("error_class") or "").strip()
        if err_cls == "ResourceExhausted":
            logger.info("[MCP API] 执行节点 ResourceExhausted，尝试下一候选 tool=%s node=%s", tool_name, nid)
            continue
        latency_ms = (time.perf_counter() - t0) * 1000
        record_usage_async(eff_sub, item_id, tool_name, "failure", latency_ms)
        msg = res.get("error") or res.get("message") or "MCP 代跑失败"
        raise api_error(500, ERR_MCP_002, str(msg))
    return None


def _merge_tool_dicts_by_name(primary: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for src in (primary, extra):
        for t in src:
            if not isinstance(t, dict):
                continue
            n = (t.get("name") or "").strip()
            if not n or n in seen:
                continue
            seen.add(n)
            out.append(t)
    return out


@router.get("/tools")
async def list_mcp_tools() -> dict[str, Any]:
    """
    获取 MCP 工具目录。
    - 默认（L2 无 stdio）：合并 **Redis 中各 L3 节点上报** 的工具名。
    - ``JACHIN_L2_STDIO_MCP=1``：额外合并本机 MCPManager（兼容旧部署）。
    """
    edge_tools = aggregate_mcp_tools_catalog_from_redis()
    tools: list[dict[str, Any]] = list(edge_tools)
    servers = 0
    try:
        if l2_stdio_mcp_enabled():
            manager = get_mcp_manager()
            local = manager.get_all_tools()
            if not local:
                local = await manager.list_tools_async()
            servers = manager.server_count
            tools = _merge_tool_dicts_by_name(local, tools)
        logger.info("[MCP API] GET /tools 返回 count=%d (edge=%d)", len(tools), len(edge_tools))
        return {
            "tools": tools,
            "count": len(tools),
            "servers": servers,
        }
    except Exception as e:
        logger.exception("[MCP API] GET /tools 异常 err=%s", e)
        raise api_error(500, ERR_MCP_002, "获取 MCP 工具列表失败", detail=str(e))


@router.post("/invoke")
async def invoke_mcp_tool(request: Request, body: InvokeRequest) -> dict[str, Any]:
    """
    执行 MCP 工具。
    - **默认（长期）**：L2 不跑 stdio MCP；仅 **委托 L3**（Pull 队列或 HTTP）。建议携带 ``X-Sub-Account-Id``。
    - **兼容**：``JACHIN_L2_STDIO_MCP=1`` 时先尝试 L2 本机 MCPManager。
    """
    tool_name = (body.tool_name or "").strip()
    if not tool_name:
        raise api_error(400, ERR_BAD_REQUEST_001, "tool_name 不能为空")

    sub_account_id = _get_sub_account_id(request)

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

    manager = get_mcp_manager() if l2_stdio_mcp_enabled() else None
    server_id = manager.get_server_id_for_tool(tool_name) if manager else None
    item_id = f"mcp:{server_id}" if server_id else f"mcp:unknown"

    arguments = body.arguments or {}
    eff_sub = sub_account_id or "anon"
    logger.info("[MCP API] POST /invoke tool_name=%s sub=%s item_id=%s", tool_name, eff_sub[:16], item_id)

    t0 = time.perf_counter()
    timeout_sec = 60.0

    if manager and manager.can_invoke_stdio_tool(tool_name):
        try:
            result = await asyncio.wait_for(
                manager.invoke_tool(tool_name, arguments),
                timeout=timeout_sec,
            )
            latency_ms = (time.perf_counter() - t0) * 1000
            record_usage_async(eff_sub, item_id, tool_name, "success", latency_ms)
            return {
                "ok": True,
                "tool_name": tool_name,
                "result": result,
            }
        except MCPToolNotFoundError:
            pass
        except asyncio.TimeoutError:
            latency_ms = (time.perf_counter() - t0) * 1000
            record_usage_async(eff_sub, item_id, tool_name, "failure", latency_ms)
            raise api_error(500, ERR_MCP_002, "MCP 工具调用超时")
        except MCPConnectionError as e:
            latency_ms = (time.perf_counter() - t0) * 1000
            record_usage_async(eff_sub, item_id, tool_name, "failure", latency_ms)
            raise api_error(500, ERR_MCP_002, str(e))
        except Exception as e:
            latency_ms = (time.perf_counter() - t0) * 1000
            record_usage_async(eff_sub, item_id, tool_name, "failure", latency_ms)
            logger.exception("[MCP API] invoke 异常 tool_name=%s err=%s", tool_name, e)
            raise api_error(500, ERR_MCP_002, f"MCP 工具执行失败: {e}")

    if sub_account_id:
        if is_tool_local_pinned(tool_name):
            raise api_error(
                400,
                ERR_BAD_REQUEST_001,
                "该工具标记为 LOCAL_PINNED（绑定本机文件/浏览器/调度上下文），禁止跨节点委托。请在具备该工具的 L3 本机执行。",
            )
        pulled = await _delegate_mcp_via_pull_queue_first(
            sub_account_id=sub_account_id,
            tool_name=tool_name,
            arguments=arguments,
            eff_sub=eff_sub,
            item_id=item_id,
            timeout_sec=timeout_sec,
            t0=t0,
        )
        if pulled is not None:
            return pulled
        nodes = get_l3_nodes_with_mcp_tool(sub_account_id, tool_name, require_l3_http_url=True)
        nodes = filter_l3_nodes_assigned_in_db(sub_account_id, nodes)
        if nodes:
            l3_url = (nodes[0].get("l3_http_url") or "").strip().rstrip("/")
            nid0 = (nodes[0].get("node_id") or "").strip()
            if l3_url and nid0:
                http_task_id = str(uuid.uuid4())
                http_token = mint_mcp_delegate_task_token(
                    task_id=http_task_id,
                    tool_name=tool_name,
                    executor_node_id=nid0,
                    sub_account_id=sub_account_id,
                )
                execute_url = f"{l3_url}/api/v3/mcp/execute"
                logger.info("[MCP API] Pull 未就绪，HTTP 委托 L3 tool=%s url=%s", tool_name, execute_url)
                try:
                    async with httpx.AsyncClient(timeout=timeout_sec) as client:
                        r = await client.post(
                            execute_url,
                            json={
                                "tool_name": tool_name,
                                "arguments": arguments,
                                "task_id": http_task_id,
                                "task_token": http_token,
                            },
                        )
                        r.raise_for_status()
                        data = r.json()
                        if data.get("ok"):
                            result = data.get("result", "")
                            latency_ms = (time.perf_counter() - t0) * 1000
                            record_usage_async(
                                eff_sub,
                                f"mcp:delegate:{nodes[0].get('node_id', '')}",
                                tool_name,
                                "success",
                                latency_ms,
                            )
                            return {
                                "ok": True,
                                "tool_name": tool_name,
                                "result": result,
                                "delegated": True,
                                "via": "http_push",
                            }
                except Exception as delegate_e:
                    logger.warning("[MCP API] 委托 L3 失败 tool=%s err=%s", tool_name, delegate_e)

    msg = (
        f"工具 '{tool_name}' 无法在 L2 本机执行且未成功委托 L3。"
        " 请确认边绦在线、已上报 mcp_tools、Redis 可用，并携带 X-Sub-Account-Id。"
    )
    if not get_redis_client() or not _mcp_pull_delegate_enabled():
        msg += (
            " 【降级说明】当前无 Redis 或已关闭 Pull（JACHIN_MCP_DELEGATE_PULL=0）；"
            "仅依赖对 L3 的 HTTP 入站委托，在 NAT/防火墙后节点上**不可靠**。"
            "生产环境请启用 Redis + Pull，见 docs/MCP_EXECUTION_MODEL.md。"
        )
    if not l2_stdio_mcp_enabled():
        msg += " 或在 L2 设置 JACHIN_L2_STDIO_MCP=1 恢复本机 stdio MCP。"
    latency_ms = (time.perf_counter() - t0) * 1000
    record_usage_async(eff_sub, item_id, tool_name, "failure", latency_ms)
    logger.warning("[MCP API] 工具未找到或无法委托 tool_name=%s", tool_name)
    raise api_error(404, ERR_MCP_001, msg)


@router.get("/delegate/poll")
async def mcp_delegate_poll(
    request: Request,
    node_id: str = Query(..., min_length=1),
    limit: int = Query(2, ge=1, le=10),
) -> dict[str, Any]:
    """
    L3 轮询拉取本节点 MCP 代跑任务（RPOP Redis 下行队列）。
    需 X-Sub-Account-Id / Bearer，且 node_id 必须属于该子账号。
    """
    sub_account_id = _get_sub_account_id(request)
    if not sub_account_id:
        raise api_error(401, ERR_AUTH_001, "需要 X-Sub-Account-Id 或 Authorization Bearer")

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM l3_nodes WHERE id = ? AND sub_account_id = ?",
            (node_id, sub_account_id),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise api_error(403, ERR_AUTH_004, "node_id 不属于当前子账号或未分配")

    tasks = pop_mcp_delegate_tasks(node_id, limit=limit)
    for t in tasks:
        tid = t.get("task_id")
        if tid:
            set_mcp_delegate_lease(str(tid), node_id, sub_account_id, ttl_sec=90)
    return {"tasks": tasks, "count": len(tasks)}


@router.post("/delegate/result")
async def mcp_delegate_result(request: Request, body: McpDelegateResultBody) -> dict[str, Any]:
    """执行节点回写 ``mcp_delegate`` 任务结果，供 L2 /invoke 轮询读取。"""
    sub_account_id = _get_sub_account_id(request)
    if not sub_account_id:
        raise api_error(401, ERR_AUTH_001, "需要 X-Sub-Account-Id 或 Authorization Bearer")

    lease = get_mcp_delegate_lease(body.task_id)
    if not lease:
        raise api_error(400, ERR_BAD_REQUEST_001, "无效、过期或已提交的 task_id")

    if lease.get("node_id") != body.node_id or lease.get("sub_account_id") != sub_account_id:
        raise api_error(403, ERR_AUTH_004, "租约与 node_id / 子账号不匹配")

    wr: dict[str, Any] = {
        "ok": body.ok,
        "result": body.result if body.ok else None,
        "error": (body.error or "") if not body.ok else "",
        "error_class": (body.error_class or "") if not body.ok else "",
    }
    set_mcp_delegate_result(body.task_id, wr, ttl_sec=120)
    delete_mcp_delegate_lease(body.task_id)
    return {"ok": True}
