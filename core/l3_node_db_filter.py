"""L2：将 Redis 在线节点与 SQLite l3_nodes 分配关系求交，防止伪造 Redis 的跨租户委托。"""
from __future__ import annotations

from typing import Any

from core.db import get_connection


def filter_l3_nodes_assigned_in_db(sub_account_id: str, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    仅保留 ``l3_nodes`` 表中 ``sub_account_id`` 已绑定且 ``id`` 匹配的节点。
    """
    if not sub_account_id or not nodes:
        return []
    ids = [str(n.get("node_id") or "").strip() for n in nodes]
    ids = [i for i in ids if i]
    if not ids:
        return []
    conn = get_connection()
    try:
        ph = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT id FROM l3_nodes WHERE sub_account_id = ? AND id IN ({ph})",
            (sub_account_id, *ids),
        ).fetchall()
        allowed = {str(r[0]) for r in rows}
    finally:
        conn.close()
    return [n for n in nodes if str(n.get("node_id") or "").strip() in allowed]
