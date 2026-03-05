"""
Jachin Nexus V2 - L2 记忆同步 API

POST /api/v2/memory/sync: L3 上报本地记忆，L2 梦境优化后回传。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2", tags=["v2-memory"])

# L2 梦境优化后的记忆存储（简化：单节点内存，生产可接入 SQLite/LanceDB）
_MEMORY_STORE: dict[str, dict[str, Any]] = {}


def _get_sub_account_from_request(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization") or request.headers.get("X-Sub-Account-Id")
    if auth and auth.startswith("Bearer "):
        return auth[7:].strip() or None
    if auth:
        return auth.strip() or None
    return request.headers.get("X-Sub-Account-Id")


def _dream_optimize(local_memory: dict[str, Any]) -> dict[str, Any]:
    """
    梦境优化：聚类、去重、融合。
    简化实现：直接合并 entries，去重 by content hash。
    """
    entries = local_memory.get("entries") or []
    seen = set()
    optimized = []
    for e in entries:
        content = e.get("content", "") or str(e)
        h = hash(content[:200])
        if h not in seen:
            seen.add(h)
            optimized.append(e)
    return {
        "entries": optimized[:100],  # 限制条数
        "optimized_at": __import__("time").time(),
    }


@router.post("/memory/sync")
async def memory_sync(request: Request) -> dict[str, Any]:
    """
    L3 将本地记忆同步至 L2。
    body: { node_id, local_memory: { entries: [...] } }
    L2 梦境优化后返回 optimized_memory，L3 覆盖本地。
    """
    sub_account_id = _get_sub_account_from_request(request)
    if not sub_account_id:
        raise HTTPException(status_code=401, detail="X-Sub-Account-Id required")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    node_id = body.get("node_id") or ""
    local_memory = body.get("local_memory") or {}

    if not node_id:
        raise HTTPException(status_code=400, detail="node_id required")

    key = f"{sub_account_id}:{node_id}"
    _MEMORY_STORE[key] = local_memory
    optimized = _dream_optimize(local_memory)

    return {
        "ok": True,
        "optimized_memory": optimized,
        "message": "记忆已同步，梦境优化完成",
    }
