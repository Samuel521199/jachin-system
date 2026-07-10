"""
PMO Sprint 大需求 + 开发/产品/美术子任务查询（案例 SSOT：PMO_DB_QUERY_CASE_STUDY_0511_SPRINT §5）。

Python 解析 ``pmo_raw_records.fields``，避免 ``json_extract`` 在父记录混 string/array 时 malformed JSON。
子任务：① `父记录` ∈ {开发, 产品, 美术}，按 row_index 归并大需求；② `父记录` 为 Epic/中间层链接名（非部门名）且有任务编号，同样按 row_index 归并（修复「技术优化」类漏采）。
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import timedelta
from typing import Any

from l3_node.pmo_parent_record import parent_text_from_fields
from l3_node.tools.pmo_dates import pmo_ms_to_iso_date, pmo_today_date
from l3_node.tools.pmo_db_tools import _DEPT_PLACEHOLDER_ROW_NAMES, _connect, get_pmo_db_path, pmo_mirror_db_ready

_ms_to_iso_date = pmo_ms_to_iso_date

_DEFAULT_SOURCE_VIEW = "vewpI8lyYw"
_DEPT = _DEPT_PLACEHOLDER_ROW_NAMES
_DEVELOPMENT_PARENT = "开发"
_PRODUCT_PARENT = "产品"
_ART_PARENT = "美术"
_CHILD_DEPT_PARENTS: tuple[str, str, str] = (_DEVELOPMENT_PARENT, _PRODUCT_PARENT, _ART_PARENT)
_SPRINT_GLOB_RE = re.compile(r"^(\d{4})/(\d{2})/(\d{2})-Sprint$")


def _resolve_child_dept_parents(department: str) -> tuple[str, ...]:
    """解析 department 参数 → 要采集的「父记录」部门名列表。"""
    d = (department or "").strip().lower()
    if not d or d in ("all", "any", "*", "全部"):
        return _CHILD_DEPT_PARENTS
    if d in ("development", "dev", "开发"):
        return (_DEVELOPMENT_PARENT,)
    if d in ("product", "prod", "产品"):
        return (_PRODUCT_PARENT,)
    if d in ("art", "美术", "design"):
        return (_ART_PARENT,)
    # 允许直接传中文部门名
    if department.strip() in _CHILD_DEPT_PARENTS:
        return (department.strip(),)
    return _CHILD_DEPT_PARENTS


def _safe_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        return {}


def parent_text(fields: dict[str, Any]) -> str | None:
    return parent_text_from_fields(fields)


def _status_text(fields: dict[str, Any]) -> str | None:
    st = fields.get("状态")
    if isinstance(st, list) and st and isinstance(st[0], dict):
        return str(st[0].get("text") or "").strip() or None
    if isinstance(st, str):
        s = st.strip()
        return s or None
    return None


def _person_display(fields: dict[str, Any]) -> str | None:
    pip = fields.get("Person in charge/Participant")
    if isinstance(pip, str):
        s = pip.strip()
        return s or None
    if isinstance(pip, list):
        names: list[str] = []
        for p in pip:
            if isinstance(p, dict):
                n = str(p.get("en_name") or p.get("text") or "").strip()
                if n:
                    names.append(n)
        return ", ".join(dict.fromkeys(names)) if names else None
    return None


def _is_big_epic(fields: dict[str, Any]) -> bool:
    req = str(fields.get("Requirement") or "").strip()
    if not req or req in _DEPT:
        return False
    if parent_text(fields) is not None:
        return False
    if not fields.get("任务编号"):
        return False
    return True


def _pack_epic_row(fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "epic_name": str(fields.get("Requirement") or "").strip(),
        "sprint": fields.get("Sprint"),
        "priority": fields.get("priority"),
        "version_goal": fields.get("Version Goal"),
        "expectation_purpose": fields.get("Expectation/Purpose"),
        "progress": fields.get("Progress"),
        "person": _person_display(fields),
        "status": _status_text(fields),
        "start_date": _ms_to_iso_date(fields.get("Start Date")),
        "review_date": _ms_to_iso_date(fields.get("Review Date")),
        "acceptance_date": _ms_to_iso_date(fields.get("Acceptance Date")),
        "expected_delivery_date": _ms_to_iso_date(fields.get("Expected Delivery Date")),
        "actual_delivery_date": _ms_to_iso_date(fields.get("Actual Delivery Date")),
        "task_no": fields.get("任务编号"),
    }


def _pack_child_row(
    fields: dict[str, Any],
    parent_epic: str | None,
    *,
    department: str,
) -> dict[str, Any]:
    return {
        "department": department,
        "parent_epic": parent_epic,
        "task": str(fields.get("Requirement") or "").strip(),
        "priority": fields.get("priority"),
        "sprint": fields.get("Sprint"),
        "version_goal": fields.get("Version Goal"),
        "expectation_purpose": fields.get("Expectation/Purpose"),
        "progress": fields.get("Progress"),
        "status": _status_text(fields),
        "person": _person_display(fields),
        "start_date": _ms_to_iso_date(fields.get("Start Date")),
        "review_date": _ms_to_iso_date(fields.get("Review Date")),
        "acceptance_date": _ms_to_iso_date(fields.get("Acceptance Date")),
        "expected_delivery_date": _ms_to_iso_date(fields.get("Expected Delivery Date")),
        "actual_delivery_date": _ms_to_iso_date(fields.get("Actual Delivery Date")),
        "task_no": fields.get("任务编号"),
    }


def _pack_dev_row(fields: dict[str, Any], parent_epic: str | None) -> dict[str, Any]:
    return _pack_child_row(fields, parent_epic, department=_DEVELOPMENT_PARENT)


def _epic_child_from_task(task: dict[str, Any]) -> dict[str, Any]:
    """Worker C User-facing result 使用 epic_children[] 时的字段形状。"""
    return {
        "department": task.get("department"),
        "parent_epic": task.get("parent_epic"),
        "task": task.get("task"),
        "priority": task.get("priority"),
        "sprint": task.get("sprint"),
        "version_goal": task.get("version_goal"),
        "expectation_purpose": task.get("expectation_purpose"),
        "progress": task.get("progress"),
        "status": task.get("status"),
        "person": task.get("person"),
        "start_date": task.get("start_date"),
        "review_date": task.get("review_date"),
        "acceptance_date": task.get("acceptance_date"),
        "expected_delivery_date": task.get("expected_delivery_date"),
        "actual_delivery_date": task.get("actual_delivery_date"),
        "task_no": task.get("task_no"),
    }


def _epic_indices_from_rows(rows: list[tuple[int, dict[str, Any]]]) -> list[tuple[int, str]]:
    _, indices = _sorted_big_epics(rows)
    return indices


def _sorted_big_epics(
    rows: list[tuple[int, dict[str, Any]]],
) -> tuple[list[tuple[int, dict[str, Any]]], list[tuple[int, str]]]:
    epics_raw: list[tuple[int, dict[str, Any]]] = []
    for idx, f in rows:
        if _is_big_epic(f):
            epics_raw.append((idx, f))
    by_name: dict[str, tuple[int, dict[str, Any]]] = {}
    for idx, f in epics_raw:
        name = str(f.get("Requirement") or "").strip()
        if name not in by_name or idx < by_name[name][0]:
            by_name[name] = (idx, f)
    epic_sorted = sorted(by_name.values(), key=lambda x: x[0])
    indices = [(idx, str(f.get("Requirement") or "").strip()) for idx, f in epic_sorted]
    return epic_sorted, indices


def _epic_for_row(row_index: int, epic_indices: list[tuple[int, str]]) -> str | None:
    best: str | None = None
    for eidx, name in epic_indices:
        if eidx < row_index:
            best = name
        else:
            break
    return best


def _dept_lane_for_row(
    row_index: int,
    rows: list[tuple[int, dict[str, Any]]],
) -> str:
    """向上扫描：当前行所属部门泳道（开发/产品/美术），默认开发。"""
    lane = _DEVELOPMENT_PARENT
    for idx, f in rows:
        if idx >= row_index:
            break
        pt = parent_text(f)
        if pt in _CHILD_DEPT_PARENTS:
            lane = pt
        req = str(f.get("Requirement") or "").strip()
        if req in _CHILD_DEPT_PARENTS:
            lane = req
    return lane


def _dedupe_child_tasks(
    raw: list[tuple[int, dict[str, Any], str | None, str]],
) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    seen_task_no: dict[str, int] = {}
    seen_fallback: set[tuple[str, str, str, str]] = set()
    for idx, f, pe, dept_parent in sorted(raw, key=lambda x: x[0]):
        row = _pack_child_row(f, pe, department=dept_parent)
        tno = row.get("task_no")
        if tno:
            key = str(tno)
            if key in seen_task_no and seen_task_no[key] <= idx:
                continue
            seen_task_no[key] = idx
        else:
            fb = (
                str(pe or ""),
                str(row.get("task") or ""),
                str(row.get("department") or ""),
                str(row.get("sprint") or ""),
            )
            if fb in seen_fallback:
                continue
            seen_fallback.add(fb)
        tasks.append(row)
    return tasks


def _collect_dept_tasks(
    rows: list[tuple[int, dict[str, Any]]],
    epic_indices: list[tuple[int, str]],
    dept_parent: str,
) -> list[dict[str, Any]]:
    raw: list[tuple[int, dict[str, Any], str | None, str]] = []
    for idx, f in rows:
        if parent_text(f) != dept_parent:
            continue
        req = str(f.get("Requirement") or "").strip()
        if not req or req in _DEPT or req == dept_parent:
            continue
        raw.append((idx, f, _epic_for_row(idx, epic_indices), dept_parent))
    return _dedupe_child_tasks(raw)


def _collect_epic_chain_tasks(
    rows: list[tuple[int, dict[str, Any]]],
    epic_indices: list[tuple[int, str]],
) -> list[dict[str, Any]]:
    """
    父记录为 Epic 名或中间层（如 技术优化 → 中台技术优化），非 开发/产品/美术 部门占位。
    仍须有任务编号；parent_epic 由 row_index 归并到最近大需求。
    """
    raw: list[tuple[int, dict[str, Any], str | None, str]] = []
    for idx, f in rows:
        if _is_big_epic(f):
            continue
        pt = parent_text(f)
        if pt is None or pt in _CHILD_DEPT_PARENTS:
            continue
        req = str(f.get("Requirement") or "").strip()
        if not req or req in _DEPT:
            continue
        if not f.get("任务编号"):
            continue
        lane = _dept_lane_for_row(idx, rows)
        raw.append((idx, f, _epic_for_row(idx, epic_indices), lane))
    return _dedupe_child_tasks(raw)


def _merge_child_task_lists(*parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 task_no 去重合并多路子任务列表（部门占位 + Epic 链接链）。"""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tasks in parts:
        for row in tasks:
            tno = row.get("task_no")
            if tno:
                key = str(tno)
                if key in seen:
                    continue
                seen.add(key)
            out.append(row)
    return out


def _epics_with_children(tasks: list[dict[str, Any]]) -> int:
    return len({t["parent_epic"] for t in tasks if t.get("parent_epic")})


def _fetch_view_rows(
    conn: sqlite3.Connection,
    *,
    source_view: str,
    sprint: str | None = None,
    sprints: list[str] | None = None,
) -> list[tuple[int, dict[str, Any]]]:
    sql = (
        "SELECT row_index, fields FROM pmo_raw_records "
        "WHERE source_view = ? ORDER BY row_index"
    )
    params: list[Any] = [source_view]
    out: list[tuple[int, dict[str, Any]]] = []
    for row_index, raw in conn.execute(sql, params):
        f = _safe_json(raw)
        sp = str(f.get("Sprint") or "").strip()
        if sprint and sp != sprint:
            continue
        if sprints is not None and sp not in sprints:
            continue
        out.append((int(row_index), f))
    return out


def list_recent_sprints(
    *,
    days: int = 21,
    limit: int = 3,
    source_view: str = _DEFAULT_SOURCE_VIEW,
) -> list[dict[str, Any]]:
    """等同 C-1：近 N 天内最多 limit 个 Sprint（按 sprint_date 降序）。"""
    cutoff = (pmo_today_date() - timedelta(days=int(days))).isoformat()
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT json_extract(fields, '$.Sprint') AS sprint,
                   date(replace(substr(json_extract(fields, '$.Sprint'), 1, 10), '/', '-')) AS sprint_date,
                   COUNT(*) AS cnt
            FROM pmo_raw_records
            WHERE source_view = ?
              AND json_extract(fields, '$.Sprint') IS NOT NULL
              AND json_extract(fields, '$.Sprint') != ''
              AND json_extract(fields, '$.Sprint') GLOB '????/??/??-Sprint'
            GROUP BY json_extract(fields, '$.Sprint')
            HAVING sprint_date IS NOT NULL
               AND sprint_date >= ?
            ORDER BY sprint_date DESC
            LIMIT ?
            """,
            (source_view, cutoff, int(limit)),
        ).fetchall()
        return [
            {
                "sprint": str(r["sprint"] or "").strip(),
                "sprint_date": r["sprint_date"],
                "cnt": r["cnt"],
            }
            for r in rows
            if str(r["sprint"] or "").strip()
        ]
    finally:
        conn.close()


def run_sprint_epic_report(
    *,
    sprint: str,
    source_view: str = _DEFAULT_SOURCE_VIEW,
    department: str = "all",
) -> dict[str, Any]:
    """
    单 Sprint 大需求 + 开发/产品/美术子任务（父记录=部门名，row_index 归并 parent_epic）。

    ``department`` 默认 ``all``（三者都采集）；可传 development / product / art 或中文部门名。
    """
    sprint_s = (sprint or "").strip()
    if not sprint_s:
        return {
            "status": "error",
            "error_class": "config",
            "message": "sprint 参数必填",
        }
    if not pmo_mirror_db_ready():
        return {
            "status": "error",
            "error_class": "config",
            "message": "pmo_raw_records 为空；请先 INIT（core:pmo_mirror_import）",
            "db_path": str(get_pmo_db_path()),
        }

    dept_parents = _resolve_child_dept_parents(department)

    conn = _connect()
    try:
        rows = _fetch_view_rows(conn, source_view=source_view, sprint=sprint_s)
    finally:
        conn.close()

    epic_sorted, epic_indices = _sorted_big_epics(rows)

    epic_chain = _collect_epic_chain_tasks(rows, epic_indices)

    dev_tasks: list[dict[str, Any]] = []
    product_tasks: list[dict[str, Any]] = []
    art_tasks: list[dict[str, Any]] = []
    for dept_parent in dept_parents:
        collected = _collect_dept_tasks(rows, epic_indices, dept_parent)
        chain_slice = [t for t in epic_chain if t.get("department") == dept_parent]
        merged = _merge_child_task_lists(collected, chain_slice)
        if dept_parent == _DEVELOPMENT_PARENT:
            dev_tasks = merged
        elif dept_parent == _PRODUCT_PARENT:
            product_tasks = merged
        elif dept_parent == _ART_PARENT:
            art_tasks = merged

    epics = [_pack_epic_row(f) for _, f in epic_sorted]
    all_children = dev_tasks + product_tasks + art_tasks
    from l3_node.pmo_epic_aggregate import enrich_epics_workflow_status

    enrich_epics_workflow_status(epics, all_children, current_sprint=sprint_s)

    return {
        "status": "ok",
        "sprint": sprint_s,
        "source_view": source_view,
        "department": department,
        "departments_collected": list(dept_parents),
        "epics": epics,
        "dev_tasks": dev_tasks,
        "product_tasks": product_tasks,
        "art_tasks": art_tasks,
        "epic_children": [_epic_child_from_task(t) for t in all_children],
        "summary": {
            "sprint": sprint_s,
            "epic_count": len(epics),
            "dev_task_count": len(dev_tasks),
            "product_task_count": len(product_tasks),
            "art_task_count": len(art_tasks),
            "child_task_count": len(all_children),
            "epics_with_dev": _epics_with_children(dev_tasks),
            "epics_with_product": _epics_with_children(product_tasks),
            "epics_with_art": _epics_with_children(art_tasks),
        },
    }


def run_sprint_epic_report_for_recent(
    *,
    days: int = 21,
    limit: int = 3,
    source_view: str = _DEFAULT_SOURCE_VIEW,
    department: str = "all",
) -> dict[str, Any]:
    """近三周多 Sprint 合并（Worker C 战报采集）。"""
    recent = list_recent_sprints(days=days, limit=limit, source_view=source_view)
    sprint_names = [str(r["sprint"]) for r in recent if r.get("sprint")]
    empty_summary = {
        "epic_count": 0,
        "dev_task_count": 0,
        "product_task_count": 0,
        "art_task_count": 0,
        "child_task_count": 0,
        "epics_with_dev": 0,
        "epics_with_product": 0,
        "epics_with_art": 0,
    }
    if not sprint_names:
        return {
            "status": "ok",
            "current_sprint": None,
            "recent_sprints": [],
            "epics": [],
            "dev_tasks": [],
            "product_tasks": [],
            "art_tasks": [],
            "epic_children": [],
            "summary": empty_summary,
            "sprint_window_empty": True,
        }

    from l3_node.tools.pmo_personnel_query import resolve_current_sprint

    current_sprint, _cs_date, _ = resolve_current_sprint(recent)
    if not current_sprint:
        current_sprint = sprint_names[0]
    all_epics: list[dict[str, Any]] = []
    all_dev: list[dict[str, Any]] = []
    all_product: list[dict[str, Any]] = []
    all_art: list[dict[str, Any]] = []
    for sp in sprint_names:
        rep = run_sprint_epic_report(sprint=sp, source_view=source_view, department=department)
        if str(rep.get("status") or "").lower() != "ok":
            continue
        all_epics.extend(rep.get("epics") or [])
        all_dev.extend(rep.get("dev_tasks") or [])
        all_product.extend(rep.get("product_tasks") or [])
        all_art.extend(rep.get("art_tasks") or [])

    all_children = all_dev + all_product + all_art
    return {
        "status": "ok",
        "current_sprint": current_sprint,
        "recent_sprints": recent,
        "epics": all_epics,
        "dev_tasks": all_dev,
        "product_tasks": all_product,
        "art_tasks": all_art,
        "epic_children": [_epic_child_from_task(t) for t in all_children],
        "completed_sql_ids": ["C-TOOL"],
        "summary": {
            "current_sprint": current_sprint,
            "epic_count": len(all_epics),
            "dev_task_count": len(all_dev),
            "product_task_count": len(all_product),
            "art_task_count": len(all_art),
            "child_task_count": len(all_children),
            "epics_with_dev": _epics_with_children(all_dev),
            "epics_with_product": _epics_with_children(all_product),
            "epics_with_art": _epics_with_children(all_art),
        },
    }


def _distinct_sprints(source_view: str = _DEFAULT_SOURCE_VIEW) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        cur = conn.execute(
            """
            SELECT DISTINCT json_extract(fields, '$.Sprint') AS sprint
            FROM pmo_raw_records
            WHERE source_view = ?
              AND json_extract(fields, '$.Sprint') IS NOT NULL
              AND json_extract(fields, '$.Sprint') != ''
            """,
            (source_view,),
        )
        out = []
        for row in cur:
            sp = str(row["sprint"] or "").strip()
            if not sp:
                continue
            m = _SPRINT_GLOB_RE.match(sp)
            sd = None
            if m:
                sd = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            out.append({"sprint": sp, "sprint_date": sd, "row_count": 0})
        return out
    finally:
        conn.close()


def run_resolve_sprint(
    *,
    sprint: str | None = None,
    sprint_date: str | None = None,
    label: str | None = None,
    year: int | None = None,
    source_view: str = _DEFAULT_SOURCE_VIEW,
) -> dict[str, Any]:
    if not pmo_mirror_db_ready():
        return {
            "status": "error",
            "error_class": "config",
            "message": "pmo_raw_records 为空；请先 INIT",
        }

    sprint_s = (sprint or "").strip()
    if sprint_s:
        conn = _connect()
        try:
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM pmo_raw_records WHERE source_view = ? "
                "AND json_extract(fields, '$.Sprint') = ?",
                (source_view, sprint_s),
            ).fetchone()
            if n and int(n["n"]) > 0:
                return {
                    "status": "ok",
                    "resolved_sprint": sprint_s,
                    "ambiguous": False,
                    "candidates": [{"sprint": sprint_s, "sprint_date": None, "row_count": int(n["n"])}],
                }
        finally:
            conn.close()

    all_sp = _distinct_sprints(source_view)
    if not all_sp and not sprint_s:
        return {"status": "error", "error_class": "config", "message": "库内无 Sprint 数据"}

    def _year_of(sp: str) -> int | None:
        m = _SPRINT_GLOB_RE.match(sp)
        return int(m.group(1)) if m else None

    default_year = year
    if default_year is None:
        years = [_year_of(x["sprint"]) for x in all_sp]
        years = [y for y in years if y is not None]
        default_year = max(years) if years else datetime.now().year

    candidates: list[dict[str, Any]] = []
    sd_norm = (sprint_date or "").strip()
    label_s = (label or "").strip()

    for item in all_sp:
        sp = item["sprint"]
        m = _SPRINT_GLOB_RE.match(sp)
        if not m:
            continue
        y, mo, da = int(m.group(1)), int(m.group(2)), int(m.group(3))
        match = False
        if sd_norm:
            try:
                parts = sd_norm.replace("/", "-").split("-")[:3]
                if len(parts) == 3:
                    match = (int(parts[0]), int(parts[1]), int(parts[2])) == (y, mo, da)
            except ValueError:
                pass
        if label_s and not match:
            # 5月11 / 05/11 / 5.11
            mo_da = f"{mo}/{da}"
            mo_da2 = f"{mo:02d}/{da:02d}"
            if label_s in (mo_da, mo_da2, f"{mo}月{da}", f"{mo}月{da}日", f"{mo}.{da}"):
                if year is None or y == default_year:
                    match = True
        if sprint_s and sp == sprint_s:
            match = True
        if match:
            conn = _connect()
            try:
                cnt = conn.execute(
                    "SELECT COUNT(*) AS n FROM pmo_raw_records WHERE source_view = ? "
                    "AND json_extract(fields, '$.Sprint') = ?",
                    (source_view, sp),
                ).fetchone()
                rc = int(cnt["n"]) if cnt else 0
            finally:
                conn.close()
            candidates.append(
                {"sprint": sp, "sprint_date": item.get("sprint_date"), "row_count": rc}
            )

    if not label_s and not sd_norm and not sprint_s:
        recent = list_recent_sprints(source_view=source_view)
        return {
            "status": "ok",
            "resolved_sprint": recent[0]["sprint"] if len(recent) == 1 else None,
            "ambiguous": len(recent) > 1,
            "candidates": [
                {"sprint": r["sprint"], "sprint_date": r.get("sprint_date"), "row_count": r.get("cnt", 0)}
                for r in recent
            ],
        }

    if len(candidates) == 1:
        return {
            "status": "ok",
            "resolved_sprint": candidates[0]["sprint"],
            "ambiguous": False,
            "candidates": candidates,
        }
    if len(candidates) > 1:
        return {
            "status": "ok",
            "resolved_sprint": None,
            "ambiguous": True,
            "candidates": candidates,
        }
    return {
        "status": "ok",
        "resolved_sprint": None,
        "ambiguous": False,
        "candidates": [],
        "message": "无匹配 Sprint；请检查 label/sprint_date",
    }


def resolve_war_report_current_sprint(
    worker_b: dict[str, Any] | None = None,
    worker_c: dict[str, Any] | None = None,
    *,
    today: str | None = None,
    refresh_from_db: bool = True,
    source_view: str = _DEFAULT_SOURCE_VIEW,
) -> tuple[str | None, str | None, dict[str, Any]]:
    """
    战报「本周 Sprint」：以 **开发 Epic 表 (vewpI8lyYw · Worker C)** 为 SSOT，
    按 ``sprint_date <= today`` 取最大一行；**不**以可能滞后的人员看板 (Worker B) 为准。
    """
    from l3_node.tools.pmo_personnel_query import resolve_current_sprint

    worker_b = worker_b or {}
    worker_c = worker_c or {}
    meta: dict[str, Any] = {"ssot_view": source_view}

    if refresh_from_db:
        try:
            if pmo_mirror_db_ready():
                rows = list_recent_sprints(source_view=source_view)
                cs, cs_date, rmeta = resolve_current_sprint(rows, today=today)
                meta.update(rmeta)
                if cs:
                    meta["resolved_from"] = "dev_view_db_c1"
                    if worker_b.get("current_sprint") and worker_b.get("current_sprint") != cs:
                        meta["personnel_board_sprint"] = worker_b.get("current_sprint")
                    return cs, cs_date, meta
        except Exception as exc:
            meta["db_refresh_error"] = str(exc)

    c_rows = worker_c.get("recent_sprints") or []
    cs, cs_date, rmeta = resolve_current_sprint(c_rows, today=today)
    meta.update(rmeta)
    if cs:
        meta["resolved_from"] = "worker_c_recent_sprints"
        if worker_b.get("current_sprint") and worker_b.get("current_sprint") != cs:
            meta["personnel_board_sprint"] = worker_b.get("current_sprint")
        return cs, cs_date, meta

    cs = worker_c.get("current_sprint")
    cs_date = worker_c.get("current_sprint_date")
    if cs:
        meta["resolved_from"] = "worker_c_explicit"
        if worker_b.get("current_sprint") and worker_b.get("current_sprint") != cs:
            meta["personnel_board_sprint"] = worker_b.get("current_sprint")
        return str(cs).strip() or None, (str(cs_date).strip()[:10] if cs_date else None), meta

    b_rows = worker_b.get("recent_sprints") or []
    cs, cs_date, rmeta = resolve_current_sprint(b_rows, today=today)
    meta.update(rmeta)
    if cs:
        meta["resolved_from"] = "worker_b_recent_sprints_fallback"
        return cs, cs_date, meta

    cs = worker_b.get("current_sprint")
    cs_date = worker_b.get("current_sprint_date")
    meta["resolved_from"] = "worker_b_explicit_fallback"
    return (str(cs).strip() or None if cs else None), (str(cs_date).strip()[:10] if cs_date else None), meta


def apply_war_report_current_sprint(
    worker_b: dict[str, Any],
    worker_c: dict[str, Any],
    *,
    today: str | None = None,
    refresh_from_db: bool = True,
) -> tuple[str | None, str | None]:
    """将战报用 current_sprint 写入 B/C 字典（二者对齐为开发表 SSOT）。"""
    cs, cs_date, meta = resolve_war_report_current_sprint(
        worker_b,
        worker_c,
        today=today,
        refresh_from_db=refresh_from_db,
    )
    if cs:
        worker_b["current_sprint"] = cs
        worker_c["current_sprint"] = cs
    if cs_date:
        worker_b["current_sprint_date"] = cs_date
        worker_c["current_sprint_date"] = cs_date
    worker_b["_war_report_sprint_meta"] = meta
    worker_c["_war_report_sprint_meta"] = meta
    return cs, cs_date
