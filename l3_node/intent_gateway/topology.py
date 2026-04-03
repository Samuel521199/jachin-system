"""
§11.1 拓扑排序校验器：纯代码检环，环图禁止入队。
边语义：若 B.depends_on 含 A，则 A 必须先于 B，即有向边 A → B。
"""
from __future__ import annotations

import json
import logging
from collections import deque
from typing import TYPE_CHECKING, Callable, Optional, Sequence

if TYPE_CHECKING:
    from l3_node.intent_gateway.envelope import SubIntentNode

logger = logging.getLogger(__name__)


def _emit_topology_status(
    on_step: Optional[Callable[[str, str, str], None]],
    run_id: str,
    message: str,
) -> None:
    if not on_step:
        return
    try:
        on_step(
            "system_status",
            json.dumps({"status": message}, ensure_ascii=False),
            run_id or "",
        )
    except Exception as e:
        logger.debug("[Topology] on_step 失败: %s", e)


def _validate_subintent_dag_core(nodes: Sequence["SubIntentNode"]) -> tuple[bool, Optional[list[str]]]:
    ids = {n.id for n in nodes}
    for n in nodes:
        for d in n.depends_on or []:
            if d not in ids:
                return False, [f"missing_dep:{n.id}->{d}"]

    # A -> B 表示 A 先于 B（B 依赖 A）
    adj: dict[str, list[str]] = {n.id: [] for n in nodes}
    for n in nodes:
        for d in n.depends_on or []:
            adj[d].append(n.id)

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {n.id: WHITE for n in nodes}
    stack_path: list[str] = []
    cycle: list[str] | None = None

    def dfs(u: str) -> bool:
        nonlocal cycle
        color[u] = GRAY
        stack_path.append(u)
        for v in adj.get(u, []):
            c = color.get(v, WHITE)
            if c == WHITE:
                if not dfs(v):
                    return False
            elif c == GRAY:
                cycle = stack_path[stack_path.index(v) :] + [v]
                return False
        stack_path.pop()
        color[u] = BLACK
        return True

    for n in nodes:
        if color[n.id] == WHITE:
            if not dfs(n.id):
                return False, cycle
    return True, None


def validate_subintent_dag(
    nodes: Sequence["SubIntentNode"],
    *,
    on_step: Optional[Callable[[str, str, str], None]] = None,
    run_id: str = "",
) -> tuple[bool, Optional[list[str]]]:
    """
    校验子意图 DAG；可选通过 on_step 推送 system_status（与 ws_server 协议一致）。
    """
    if not nodes:
        _emit_topology_status(on_step, run_id, "✓ 无子意图 DAG，跳过拓扑校验。")
        return True, None
    _emit_topology_status(on_step, run_id, "⏳ 正在校验子意图依赖拓扑（无环）…")
    ok, detail = _validate_subintent_dag_core(nodes)
    if ok:
        _emit_topology_status(on_step, run_id, "✓ 拓扑无环，依赖边合法。")
    else:
        _emit_topology_status(
            on_step,
            run_id,
            "✗ 子意图依赖存在环或非法依赖，已禁止按 DAG 自动串行。",
        )
    try:
        from l3_node.intent_gateway.config import get_intent_gateway_config

        if bool(get_intent_gateway_config().get("dag_topology_tracker_enabled", True)):
            from l3_node.intent_gateway.intent_tracker import emit_intent_tracker_event

            _dh = ""
            if isinstance(detail, list) and detail:
                _dh = str(detail[0])[:120]
            elif detail is not None:
                _dh = str(detail)[:120]
            emit_intent_tracker_event(
                "dag_topology_validated",
                {
                    "run_id": (run_id or "")[:32],
                    "ok": ok,
                    "node_count": len(nodes),
                    "detail_head": _dh,
                },
            )
    except Exception:
        pass
    return ok, detail


def topological_order(nodes: Sequence["SubIntentNode"]) -> list[str] | None:
    ok, _ = validate_subintent_dag(nodes, on_step=None, run_id="")
    if not ok:
        return None
    ids = {n.id for n in nodes}
    in_deg: dict[str, int] = {n.id: 0 for n in nodes}
    children: dict[str, list[str]] = {n.id: [] for n in nodes}
    for n in nodes:
        in_deg[n.id] = len(n.depends_on or [])
        for d in n.depends_on or []:
            if d in children:
                children[d].append(n.id)
    q = deque(sorted(i for i in ids if in_deg[i] == 0))
    out: list[str] = []
    while q:
        u = q.popleft()
        out.append(u)
        for v in children.get(u, []):
            in_deg[v] -= 1
            if in_deg[v] == 0:
                q.append(v)
    if len(out) != len(ids):
        return None
    return out
