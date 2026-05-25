"""
task_plan.md ↔ active.json 双向镜像（路线图 TaskDAG · task_plan 全量迁移 · 单机初版）

在 TaskDAG 写盘或 active.json 更新后，将结构化节点同步为 workspace 根目录 task_plan.md；
也可从 task_plan.md 勾选行解析回 active.json（保留已完成节点状态）。

环境变量
--------
JACHIN_TASK_PLAN_DAG_SYNC=1          开启双向镜像（默认关）
JACHIN_TASK_PLAN_DAG_SYNC_MD=1       Planner 写 active 后生成/更新 task_plan.md（默认随总开关）
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

_DONE_STATUS = frozenset({"done", "completed", "success", "skipped"})


def sync_enabled() -> bool:
    return (os.environ.get("JACHIN_TASK_PLAN_DAG_SYNC") or "").strip().lower() in (
        "1", "true", "yes",
    )


def sync_md_enabled() -> bool:
    if not sync_enabled():
        return False
    return (os.environ.get("JACHIN_TASK_PLAN_DAG_SYNC_MD") or "1").strip().lower() not in (
        "0", "false", "no",
    )


def active_json_to_task_plan_markdown(data: dict[str, Any]) -> str:
    title = str(data.get("title") or data.get("dag_title") or "TaskDAG").strip() or "TaskDAG"
    nodes = data.get("nodes") if isinstance(data.get("nodes"), list) else []
    lines = [
        f"# {title}",
        "",
        "<!-- JACHIN_TASK_PLAN_DAG: auto-generated from workspace/task_dags/active.json -->",
        "",
        "## 步骤",
        "",
    ]
    for n in nodes:
        if not isinstance(n, dict):
            continue
        nid = str(n.get("node_id") or n.get("id") or "").strip()
        tl = str(n.get("title") or n.get("description") or "").strip()
        st = str(n.get("status") or "pending").strip().lower()
        mark = "x" if st in _DONE_STATUS else " "
        label = f"{nid}: {tl}".strip(": ") if nid else tl
        lines.append(f"- [{mark}] {label}")
    if len(lines) <= 6:
        lines.append("- [ ] （待规划）")
    lines.append("")
    lines.append("## 备注")
    lines.append("")
    lines.append("_由 TaskDAG 自动同步；手工修改勾选行后可用 `mirror_task_plan_md_to_active_json` 回写。_")
    lines.append("")
    return "\n".join(lines)


def mirror_active_json_to_task_plan_md() -> bool:
    """将 active.json 镜像到 workspace/task_plan.md。"""
    if not sync_md_enabled():
        return False
    try:
        from l3_node.task_engine.task_dag import load_task_dag_dict
        from l3_node.task_planning import write_task_plan

        data = load_task_dag_dict()
        if not data:
            return False
        md = active_json_to_task_plan_markdown(data)
        ok = write_task_plan(md)
        if ok:
            logger.info("[TaskPlanDagBridge] mirrored active.json -> task_plan.md")
        return ok
    except Exception as e:
        logger.debug("[TaskPlanDagBridge] mirror to md failed: %s", e)
        return False


_CHECKBOX_RE = re.compile(
    r"^\s*-\s*\[([ xX])\]\s*(.+?)\s*$",
    re.MULTILINE,
)


def parse_task_plan_md_nodes(md: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for m in _CHECKBOX_RE.finditer(md or ""):
        done = m.group(1).strip().lower() == "x"
        body = (m.group(2) or "").strip()
        nid = ""
        title = body
        if ":" in body[:48]:
            head, _, rest = body.partition(":")
            if head.strip() and len(head.strip()) <= 32:
                nid = head.strip()
                title = rest.strip() or head.strip()
        out.append({
            "node_id": nid or str(len(out) + 1),
            "title": title[:120],
            "status": "done" if done else "pending",
        })
    return out


def mirror_task_plan_md_to_active_json() -> bool:
    """从 task_plan.md 勾选行回写 active.json（合并保留未知 node 的 done 状态）。"""
    if not sync_enabled():
        return False
    try:
        from l3_node.task_engine.task_dag import load_task_dag_dict, save_active_task_dag_dict
        from l3_node.task_planning import read_task_plan

        md = read_task_plan()
        if not md.strip():
            return False
        parsed = parse_task_plan_md_nodes(md)
        if not parsed:
            return False
        existing = load_task_dag_dict() or {}
        old_nodes = existing.get("nodes") if isinstance(existing.get("nodes"), list) else []
        old_by_id = {
            str(n.get("node_id") or n.get("id") or ""): n
            for n in old_nodes
            if isinstance(n, dict) and str(n.get("node_id") or n.get("id") or "")
        }
        merged: list[dict[str, Any]] = []
        for p in parsed:
            nid = p["node_id"]
            prev = old_by_id.get(nid) if isinstance(old_by_id.get(nid), dict) else {}
            merged.append({
                "node_id": nid,
                "title": p["title"],
                "status": p["status"],
                "description": str(prev.get("description") or "")[:200],
                "depends_on": prev.get("depends_on") if isinstance(prev.get("depends_on"), list) else [],
            })
        title = existing.get("title") or existing.get("dag_title") or "TaskDAG"
        if md.startswith("#"):
            first = md.split("\n", 1)[0].lstrip("#").strip()
            if first:
                title = first[:120]
        payload = {
            **existing,
            "title": title,
            "nodes": merged,
            "synced_from": "task_plan.md",
        }
        ok = save_active_task_dag_dict(payload)
        if ok:
            logger.info("[TaskPlanDagBridge] mirrored task_plan.md -> active.json nodes=%d", len(merged))
        return ok
    except Exception as e:
        logger.debug("[TaskPlanDagBridge] mirror from md failed: %s", e)
        return False
