"""
Jachin Nexus V2 - L2 记忆同步与检索 API

POST /api/v2/memory/sync: L3 上报本地记忆，L2 向量梦境引擎语义消解后写入 LanceDB。
GET /api/v2/memory/search: L3 检索记忆，LanceDB 向量相似度搜索，权限隔离（仅本子账号）。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Query, Request

from core.db import get_connection
from core.db.dream_weaver import weave_dreams_for_sub_account
from core.db.l2_memory_lancedb import search_memories_vector, sync_memories_to_lancedb
from core.errors import (
    ERR_AUTH_001,
    ERR_AUTH_003,
    ERR_BAD_REQUEST_001,
    ERR_BAD_REQUEST_002,
    ERR_NOT_FOUND_001,
    ERR_QUOTA_001,
    api_error,
)
from core.permissions import (
    ACTION_MEMORY_READ,
    ACTION_MEMORY_WRITE,
    get_effective_search_namespaces,
    get_permissions,
    verify_memory_namespace,
    verify_permissions,
)
from core.resource_quota import check_memory_quota

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2", tags=["v2-memory"])


def _get_sub_account_from_request(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization") or request.headers.get("X-Sub-Account-Id")
    if auth and auth.startswith("Bearer "):
        return auth[7:].strip() or None
    if auth:
        return auth.strip() or None
    return request.headers.get("X-Sub-Account-Id")


@router.post("/memory/sync")
async def memory_sync(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """
    L3 将本地记忆同步至 L2。
    body: { node_id, namespace?, local_memory: { entries: [...] } }
    namespace 可选，默认 default。L2 向量梦境引擎：语义消解后写入 LanceDB，返回 optimized_memory。
    """
    sub_account_id = _get_sub_account_from_request(request)
    if not sub_account_id:
        raise api_error(401, ERR_AUTH_001, "X-Sub-Account-Id required")

    try:
        body = await request.json()
    except Exception:
        raise api_error(400, ERR_BAD_REQUEST_001, "Invalid JSON")

    node_id = body.get("node_id") or ""
    local_memory = body.get("local_memory") or {}
    entries = local_memory.get("entries") or []
    namespace = (body.get("namespace") or "default").strip() or "default"

    if not node_id:
        raise api_error(400, ERR_BAD_REQUEST_002, "node_id required")

    conn = get_connection()
    try:
        perm_row = conn.execute(
            "SELECT id FROM sub_accounts WHERE id = ?",
            (sub_account_id,),
        ).fetchone()
        if not perm_row:
            raise api_error(404, ERR_NOT_FOUND_001, "Sub-account not found")
        perms = get_permissions(conn, sub_account_id)
        allowed, msg = verify_permissions(perms, ACTION_MEMORY_WRITE)
        if not allowed:
            raise api_error(403, ERR_AUTH_003, msg or "无记忆写入权限")
        allowed, msg = verify_memory_namespace(perms, namespace, write=True)
        if not allowed:
            raise api_error(403, ERR_AUTH_003, msg or f"无命名空间 {namespace} 的写入权限")
        add_mb = sum(len(str(e.get("content", ""))) for e in entries) / (1024 * 1024)
        allowed_quota, quota_msg = check_memory_quota(conn, sub_account_id, additional_mb=add_mb)
        if not allowed_quota:
            raise api_error(402, ERR_QUOTA_001, quota_msg or "存储配额超限")
    finally:
        conn.close()

    optimized = sync_memories_to_lancedb(sub_account_id, node_id, entries, namespace=namespace)

    # 异步触发梦境优化：聚类、LLM 融合、冲突消解、记忆升维
    background_tasks.add_task(weave_dreams_for_sub_account, sub_account_id)

    return {
        "ok": True,
        "optimized_memory": {
            "entries": optimized,
            "optimized_at": time.time(),
        },
        "message": "记忆已同步，向量梦境消解完成",
    }


@router.get("/memory/search")
async def memory_search(
    request: Request,
    q: str = Query(..., min_length=1),
    node_id: Optional[str] = Query(None),
    limit: int = Query(10, ge=1, le=50),
    namespaces: Optional[str] = Query(
        None,
        description="逗号分隔的命名空间列表，如 customer_service_kb,default。不传则使用子账号允许的全部命名空间",
    ),
) -> dict[str, Any]:
    """
    L3 检索记忆。必须携带 X-Sub-Account-Id，仅返回该子账号下的记忆。
    node_id 可选：若提供则仅搜该节点；否则搜子账号下全部节点。
    namespaces 可选：仅在允许的命名空间内检索；若子账号配置了 allowed_memory_namespaces，
    请求的 namespaces 必须为其子集，否则 403。
    LanceDB 向量相似度搜索，返回语义最相关的 Top-K 条记忆。
    """
    sub_account_id = _get_sub_account_from_request(request)
    if not sub_account_id:
        raise api_error(401, ERR_AUTH_001, "X-Sub-Account-Id required")

    conn = get_connection()
    try:
        perm_row = conn.execute(
            "SELECT id FROM sub_accounts WHERE id = ?",
            (sub_account_id,),
        ).fetchone()
        if not perm_row:
            raise api_error(404, ERR_NOT_FOUND_001, "Sub-account not found")
        perms = get_permissions(conn, sub_account_id)
        allowed, msg = verify_permissions(perms, ACTION_MEMORY_READ)
        if not allowed:
            raise api_error(403, ERR_AUTH_003, msg or "无记忆读取权限")
        ns_list: list[str] | None = None
        if namespaces:
            ns_list = [n.strip() for n in namespaces.split(",") if n.strip()]
        allowed, msg = verify_memory_namespace(perms, ns_list, write=False)
        if not allowed:
            raise api_error(403, ERR_AUTH_003, msg or "命名空间权限校验失败")
        effective_ns = get_effective_search_namespaces(perms, ns_list)
    finally:
        conn.close()

    results = search_memories_vector(sub_account_id, q, node_id, limit, namespaces=effective_ns)

    return {"results": results, "count": len(results)}
