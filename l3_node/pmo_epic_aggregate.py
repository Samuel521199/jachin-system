"""
PMO 大需求行聚合 SSOT：子任务 → epic_name → 参与人/完成度/时间跨度。

供 Publisher、宏观看板脚本、后续 notifier 组装复用。
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Iterable


def _person_names_from_task(t: dict[str, Any]) -> list[str]:
    raw = t.get("person")
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        parts = re.split(r"[,;，、]", raw)
        return [p.strip() for p in parts if p.strip()]
    return []


def merge_personnel_progress_into_children(
    children: list[dict[str, Any]],
    epic_name: str,
    personnel_tasks: list[dict[str, Any]],
    current_sprint: str,
) -> list[dict[str, Any]]:
    """
    人员看板 (B) 的 Progress/状态 常比开发计划表 (C) 新；按任务名合并到子任务副本供 📊 推断。
    原地修改并返回 children。
    """
    epic_name = str(epic_name or "").strip()
    cs = str(current_sprint or "").strip()
    if not epic_name or not personnel_tasks:
        return children
    by_task: dict[str, dict[str, Any]] = {}
    for t in personnel_tasks:
        if cs and str(t.get("sprint") or "") != cs:
            continue
        tn = str(t.get("task") or "").strip()
        if not tn or epic_name not in tn:
            continue
        by_task[tn] = t
    for c in children:
        tn = str(c.get("task") or "").strip()
        b = by_task.get(tn)
        if not b:
            continue
        for key in ("progress", "status", "status_text"):
            bv = b.get(key)
            if bv is not None and str(bv).strip() not in ("", "null", "—"):
                if not str(c.get(key) or "").strip():
                    c[key] = bv
    return children


def group_children_by_epic(
    children: Iterable[dict[str, Any]],
    *,
    current_sprint: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """按 parent_epic（大需求名）分组；仅保留 current_sprint 内行（若指定）。"""
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    cs = str(current_sprint or "").strip()
    for c in children:
        if cs and str(c.get("sprint") or "") != cs:
            continue
        pe = str(c.get("parent_epic") or "").strip()
        if pe:
            out[pe].append(c)
    return dict(out)


def epic_participants(
    epic: dict[str, Any],
    children: list[dict[str, Any]],
    *,
    personnel_tasks: list[dict[str, Any]] | None = None,
    max_names: int = 8,
) -> str:
    """
    汇总大需求参与人：子任务执行人优先；无子任务时从人员看板任务名匹配 epic 名兜底。
    """
    names: list[str] = []
    epic_person = str(epic.get("person") or "").strip()
    if epic_person and epic_person != "—":
        names.extend(_person_names_from_task({"person": epic_person}))

    for c in children:
        for n in _person_names_from_task(c):
            if n not in names:
                names.append(n)

    if not names and personnel_tasks:
        epic_name = str(epic.get("epic_name") or "").strip()
        prefixes: set[str] = set()
        if epic_name:
            prefixes.add(epic_name)
        for c in children:
            tn = str(c.get("task") or "").strip()
            if tn:
                prefixes.add(tn)
                if "-" in tn:
                    prefixes.add(tn.split("-", 1)[0])
        for t in personnel_tasks:
            tn = str(t.get("task") or "")
            if any(p and len(p) >= 4 and (p in tn or tn.startswith(p)) for p in prefixes):
                for n in _person_names_from_task(t):
                    if n not in names:
                        names.append(n)

    if not names:
        return "—"
    if len(names) <= max_names:
        return "; ".join(names)
    return "; ".join(names[:max_names]) + f" 等{len(names)}人"


def epic_completion_pct(epic: dict[str, Any], children: list[dict[str, Any]]) -> int:
    """📊 完成度 %：泳道流程 rank 汇总（见 pmo_workflow_stage，禁止子任务条数占比）。"""
    preset = epic.get("workflow_completion_pct")
    if preset is not None and str(preset).strip() != "":
        try:
            return max(0, min(100, int(preset)))
        except (TypeError, ValueError):
            pass
    from l3_node.pmo_workflow_stage import infer_epic_workflow_completion_pct

    return infer_epic_workflow_completion_pct(epic, children)


def enrich_epics_workflow_status(
    epics: list[dict[str, Any]],
    children: Iterable[dict[str, Any]],
    *,
    current_sprint: str | None = None,
) -> None:
    """为 epics[] 写入 workflow_status（流程 SSOT，供 Publisher / 脚本复用）。"""
    from l3_node.pmo_workflow_stage import (
        infer_epic_workflow_completion_pct,
        infer_epic_workflow_status,
    )

    by_epic = group_children_by_epic(children, current_sprint=current_sprint)
    for epic in epics:
        name = str(epic.get("epic_name") or "").strip()
        kids = by_epic.get(name, [])
        from l3_node.pmo_workflow_stage import _filter_children_for_workflow_infer

        infer_kids = _filter_children_for_workflow_infer(kids) or kids
        pct = infer_epic_workflow_completion_pct(epic, infer_kids)
        epic["workflow_completion_pct"] = pct
        epic["workflow_status"] = infer_epic_workflow_status(
            epic, infer_kids, completion_pct=pct
        )
