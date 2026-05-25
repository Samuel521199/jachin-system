"""
TaskDAG 节点状态同步（路线图 · 单体 L3 轻量调度）

在 delegate 子任务 `HOOK_ON_TASK_NODE_DONE` 时，将节点状态写回 `active.json`；
可选在工具 `core:task_dag_update` 中手工更新。

环境变量
--------
JACHIN_DAG_NODE_SYNC=1          开启 Hook 自动同步（默认关）
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def dag_node_sync_enabled() -> bool:
    return (os.environ.get("JACHIN_DAG_NODE_SYNC") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _resolve_node_id(meta: dict[str, Any]) -> str:
    for key in ("task_dag_node_id", "dag_node_id", "node_id"):
        v = str(meta.get(key) or "").strip()
        if v:
            return v[:64]
    idx = meta.get("delegate_sub_task_index")
    if idx is not None:
        try:
            return f"sub-{int(idx) + 1}"
        except (TypeError, ValueError):
            pass
    role = str(meta.get("delegate_sub_task_role") or "").strip()
    if role:
        return role[:64]
    return ""


def mark_dag_node_status(
    node_id: str,
    status: str,
    *,
    error: str | None = None,
) -> dict[str, Any]:
    """
    更新 active.json 中匹配 node_id / id 的节点 status。
    返回 {"ok": bool, "node_id": str, "status": str, "matched": int}。
    """
    nid = (node_id or "").strip()
    st = (status or "pending").strip()[:24] or "pending"
    if not nid:
        return {"ok": False, "error": "node_id required", "matched": 0}
    try:
        from l3_node.task_engine.task_dag import load_task_dag_dict, save_active_task_dag_dict
    except ImportError as e:
        return {"ok": False, "error": str(e), "matched": 0}

    data = load_task_dag_dict()
    if not data or not isinstance(data.get("nodes"), list):
        return {"ok": False, "error": "no active.json or nodes missing", "matched": 0}

    matched = 0
    for n in data["nodes"]:
        if not isinstance(n, dict):
            continue
        key = str(n.get("node_id") or n.get("id") or "").strip()
        if key != nid:
            continue
        n["status"] = st
        if error:
            n["last_error"] = str(error)[:500]
        elif st in ("completed", "done", "success"):
            n.pop("last_error", None)
        matched += 1

    if matched == 0:
        return {"ok": False, "error": f"node_id not found: {nid}", "matched": 0}

    ok = save_active_task_dag_dict(data)
    return {"ok": ok, "node_id": nid, "status": st, "matched": matched}


def get_next_pending_dag_node() -> dict[str, Any] | None:
    """返回第一个 status 为 pending 的节点摘要，无则 None。"""
    try:
        from l3_node.task_engine.task_dag import load_task_dag_dict
    except ImportError:
        return None
    data = load_task_dag_dict()
    if not data:
        return None
    nodes = data.get("nodes")
    if not isinstance(nodes, list):
        return None
    for n in nodes:
        if not isinstance(n, dict):
            continue
        st = str(n.get("status") or "pending").lower()
        if st in ("pending", "todo", "open", ""):
            return {
                "node_id": str(n.get("node_id") or n.get("id") or ""),
                "title": str(n.get("title") or n.get("description") or "")[:200],
                "status": st,
            }
    return None


def sync_from_task_node_ctx(ctx: Any) -> None:
    """由 HOOK_ON_TASK_NODE_DONE 调用：根据 metadata 更新 active.json。"""
    if not dag_node_sync_enabled():
        return
    md = getattr(ctx, "metadata", None) or {}
    if not isinstance(md, dict):
        return
    nid = _resolve_node_id(md)
    if not nid:
        return
    err = str(md.get("task_node_error") or "").strip()
    st = "failed" if err else "completed"
    res = mark_dag_node_status(nid, st, error=err or None)
    if res.get("ok"):
        logger.info(
            "[TaskDAG] node sync node_id=%s status=%s run_id=%s",
            nid,
            st,
            str(getattr(ctx, "run_id", "") or "")[:12],
        )
    else:
        logger.debug("[TaskDAG] node sync skip: %s", res.get("error"))


async def _on_task_node_done(ctx: Any) -> None:
    try:
        sync_from_task_node_ctx(ctx)
    except Exception as e:
        logger.debug("[TaskDAG] sync hook failed: %s", e)


def register_dag_node_sync_hooks() -> None:
    if not dag_node_sync_enabled():
        return
    try:
        from l3_node.engine.hooks_pipeline import HOOK_ON_TASK_NODE_DONE, global_hooks
    except ImportError:
        return
    global_hooks.register(HOOK_ON_TASK_NODE_DONE, _on_task_node_done)
    logger.debug("[TaskDAG] dag_node_sync hook registered")


try:
    register_dag_node_sync_hooks()
except Exception:
    pass
