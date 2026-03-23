"""
Jachin Nexus V2 - L2 记忆同步与检索 API

POST /api/v2/memory/sync: L3 上报本地记忆，L2 向量梦境引擎语义消解后写入 LanceDB。
GET /api/v2/memory/search: L3 检索记忆，LanceDB 向量相似度搜索，权限隔离（仅本子账号）。
POST /api/v2/memory/reinforce: P2-9 对记忆 id 累加强化分（混合检索加权）。
POST /api/v2/memory/feedback: UI 点赞/点踩（vote + 可选 delta），并写 intelligence_events。
GET /api/v2/memory/search?explain=true: 结果含可解释排序分量（MEMORY_SCORING）。
POST /api/v2/intelligence/implicit-signal: §4.3 客户端埋点（skip/dwell/repeat_*）。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Query, Request

from core.db import get_connection
from core.db.dream_weaver import weave_dreams_for_sub_account
from core.db.l2_memory_lancedb import search_memories_vector, sync_memories_to_lancedb
from core.db.memory_reinforcement import add_reinforce_delta
from core.intelligence_implicit import (
    SIGNAL_DWELL,
    SIGNAL_REPEAT_FOLLOWUP,
    SIGNAL_REPEAT_INTENT,
    SIGNAL_SKIP,
    emit_implicit_signal,
)
from core.intelligence_workspace import emit_intelligence_event
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
    hybrid: bool = Query(
        True,
        description="混合检索（向量+BM25），专有名词更准；false 时仅向量",
    ),
    explain: bool = Query(
        False,
        description="为每条结果附加 explain 分解释（vec/bm25/reinforce/total），见 docs/MEMORY_SCORING.md",
    ),
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

    results = search_memories_vector(
        sub_account_id,
        q,
        node_id,
        limit,
        namespaces=effective_ns,
        hybrid=hybrid,
        explain=explain,
    )

    return {"results": results, "count": len(results), "explain": bool(explain)}


@router.post("/memory/reinforce")
async def memory_reinforce(request: Request) -> dict[str, Any]:
    """
    P2-9：对某条记忆 id 增加强化分（写入侧车 JSON，混合检索时加权）。
    body: { "memory_id": "...", "delta": 1.0 }  delta 可为负，不低于 0 总分裁剪在服务端。
    """
    sub_account_id = _get_sub_account_from_request(request)
    if not sub_account_id:
        raise api_error(401, ERR_AUTH_001, "X-Sub-Account-Id required")

    try:
        body = await request.json()
    except Exception:
        raise api_error(400, ERR_BAD_REQUEST_001, "Invalid JSON")

    memory_id = (body.get("memory_id") or body.get("id") or "").strip()
    if not memory_id:
        raise api_error(400, ERR_BAD_REQUEST_002, "memory_id required")
    try:
        delta = float(body.get("delta", 1.0))
    except (TypeError, ValueError):
        raise api_error(400, ERR_BAD_REQUEST_002, "delta must be number")

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
    finally:
        conn.close()

    max_per = 8.0
    try:
        nexus = Path.home() / ".jachin" / "nexus_config.json"
        if nexus.exists():
            cfg = json.loads(nexus.read_text(encoding="utf-8"))
            sec = cfg.get("intelligence_p2")
            if isinstance(sec, dict) and sec.get("reinforce_max_boost") is not None:
                max_per = float(sec["reinforce_max_boost"])
    except Exception:
        pass

    new_v = add_reinforce_delta(memory_id, delta, max_per_id=max(0.5, min(20.0, max_per)))
    return {"ok": True, "memory_id": memory_id, "reinforce_score": new_v}


@router.post("/memory/feedback")
async def memory_feedback(request: Request) -> dict[str, Any]:
    """
    UI 闭环：点赞/点踩 → 侧车 reinforce + intelligence_events（intelligence_e 可选聚合）。
    body: { "memory_id": "...", "vote": "up" | "down" } 可选 "delta" 覆盖默认增量。
    """
    sub_account_id = _get_sub_account_from_request(request)
    if not sub_account_id:
        raise api_error(401, ERR_AUTH_001, "X-Sub-Account-Id required")

    try:
        body = await request.json()
    except Exception:
        raise api_error(400, ERR_BAD_REQUEST_001, "Invalid JSON")

    memory_id = (body.get("memory_id") or body.get("id") or "").strip()
    if not memory_id:
        raise api_error(400, ERR_BAD_REQUEST_002, "memory_id required")
    vote = str(body.get("vote") or "").strip().lower()
    if vote not in ("up", "down", "1", "-1", "positive", "negative"):
        raise api_error(400, ERR_BAD_REQUEST_002, 'vote must be "up" or "down"')
    if vote in ("1", "positive"):
        vote = "up"
    if vote in ("-1", "negative"):
        vote = "down"

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
    finally:
        conn.close()

    default_up = 0.5
    default_down = -0.35
    try:
        nexus = Path.home() / ".jachin" / "nexus_config.json"
        if nexus.exists():
            cfg = json.loads(nexus.read_text(encoding="utf-8"))
            ui = cfg.get("intelligence_ui")
            if isinstance(ui, dict):
                if ui.get("memory_feedback_up") is not None:
                    default_up = float(ui["memory_feedback_up"])
                if ui.get("memory_feedback_down") is not None:
                    default_down = float(ui["memory_feedback_down"])
    except Exception:
        pass

    try:
        delta = float(body.get("delta"))
    except (TypeError, ValueError):
        delta = default_up if vote == "up" else default_down

    max_per = 8.0
    try:
        nexus = Path.home() / ".jachin" / "nexus_config.json"
        if nexus.exists():
            cfg = json.loads(nexus.read_text(encoding="utf-8"))
            sec = cfg.get("intelligence_p2")
            if isinstance(sec, dict) and sec.get("reinforce_max_boost") is not None:
                max_per = float(sec["reinforce_max_boost"])
    except Exception:
        pass

    new_v = add_reinforce_delta(memory_id, delta, max_per_id=max(0.5, min(20.0, max_per)))
    ev = "ui_memory_thumbs_up" if vote == "up" else "ui_memory_thumbs_down"
    emit_intelligence_event(ev, {"memory_id": memory_id, "delta": delta, "sub_account_id": sub_account_id})
    return {"ok": True, "memory_id": memory_id, "vote": vote, "reinforce_score": new_v}


_IMPLICIT_HTTP_MAP = {
    "skip": SIGNAL_SKIP,
    "dwell": SIGNAL_DWELL,
    "repeat_followup": SIGNAL_REPEAT_FOLLOWUP,
    "repeat_intent": SIGNAL_REPEAT_INTENT,
}


@router.post("/intelligence/implicit-signal")
async def post_intelligence_implicit_signal(request: Request) -> dict[str, Any]:
    """
    §4.3 全客户端埋点：跳过 / 停留 / 复述与追问等。
    body: {
      "signal": "skip" | "dwell" | "repeat_followup" | "repeat_intent" | "assistant_echo",
      "source": "lark" | "console" | ...,
      "payload": { ... }  // 可选：dwell_ms、dwell_sec、ratio、snippet、session_id、memory_id 等
    }
    事件写入 ~/.jachin/logs/intelligence_events.jsonl；intelligence_e 可按类型聚合。
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
    finally:
        conn.close()

    try:
        body = await request.json()
    except Exception:
        raise api_error(400, ERR_BAD_REQUEST_001, "Invalid JSON")

    sig = str(body.get("signal") or body.get("type") or "").strip().lower()
    src = str(body.get("source", "http_client") or "http_client")
    raw_pl = body.get("payload")
    payload: dict[str, Any] = dict(raw_pl) if isinstance(raw_pl, dict) else {}

    if sig == "assistant_echo":
        payload.setdefault("source", src)
        emit_intelligence_event("user_rephrased_assistant", payload)
        return {"ok": True, "emitted": "user_rephrased_assistant"}

    mid = _IMPLICIT_HTTP_MAP.get(sig)
    if not mid:
        raise api_error(
            400,
            ERR_BAD_REQUEST_002,
            f"unknown signal: {sig!r}; use skip|dwell|repeat_followup|repeat_intent|assistant_echo",
        )

    if sig == "dwell":
        if "dwell_ms" not in payload and "dwell_sec" not in payload and "seconds" not in payload:
            raise api_error(400, ERR_BAD_REQUEST_002, "dwell requires payload.dwell_ms or dwell_sec or seconds")

    payload.setdefault("sub_account_id", sub_account_id)
    emit_implicit_signal(mid, payload, source=src)
    return {"ok": True, "emitted": mid}
