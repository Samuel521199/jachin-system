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
