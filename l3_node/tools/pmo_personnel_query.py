"""
PMO 人员任务矩阵查询（案例 SSOT：PMO_PERSONNEL_QUERY_CASE_STUDY_0601_SPRINT §5～§11）。

Python 解析 ``pmo_raw_records.fields``：两视图合并、Person 双形态、current_sprint（sd≤today）。
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import timedelta
from typing import Any

from l3_node.tools.pmo_dates import pmo_ms_to_iso_date, pmo_today_date, pmo_today_iso
from l3_node.tools.pmo_db_tools import _connect, get_pmo_db_path, pmo_mirror_db_ready
from l3_node.tools.pmo_sprint_query import parent_text, _status_text

logger = logging.getLogger(__name__)

_PERSON_VIEW = "vewCz1FFJi"
_DEV_VIEW = "vewpI8lyYw"


def resolve_current_sprint(
    sprint_rows: list[dict[str, Any]],
    *,
    today: str | None = None,
) -> tuple[str | None, str | None, dict[str, Any]]:
    """
    从 B-S1/C-1 行（含 sprint, sprint_date）解析 current_sprint。
    规则：sprint_date <= today 中取 sprint_date 最大的一行。
    """
    today_s = (today or pmo_today_iso()).strip()[:10]
    eligible: list[tuple[str, str]] = []
    for r in sprint_rows:
        sp = str(r.get("sprint") or "").strip()
        sd = str(r.get("sprint_date") or "").strip()[:10]
        if not sp or not sd:
            continue
        if sd <= today_s:
            eligible.append((sd, sp))
    meta: dict[str, Any] = {"eligible_count": len(eligible), "today": today_s}
    if eligible:
        sd_max, sp_max = max(eligible)
        meta["resolved_by"] = "sd_lte_today_max"
        logger.info(
            "[PMO personnel] current_sprint=%s (sd=%s, eligible=%d)",
            sp_max,
            sd_max,
            len(eligible),
        )
        return sp_max, sd_max, meta
    meta["resolved_by"] = "none_eligible"
    meta["_current_sprint_fallback"] = "all_future_in_window"
    logger.warning(
        "[PMO personnel] no eligible current_sprint (all future in window? rows=%d)",
        len(sprint_rows),
    )
    return None, None, meta


_ms_to_iso_date = pmo_ms_to_iso_date


def person_keys_from_task(task: dict[str, Any]) -> list[str]:
    """
    👥 人员矩阵按「单人」分行：多人任务归入每一位负责人，禁止 ``A; B`` 合成键重复占行。
    """
    persons = task.get("persons")
    if isinstance(persons, list) and persons:
        out: list[str] = []
        for p in persons:
            s = str(p).strip()
            if s and s not in out:
                out.append(s)
        if out:
            return out
    raw = str(task.get("person") or "").strip()
    if not raw:
        return []
    if ";" in raw or "；" in raw:
        parts = [x.strip() for x in re.split(r"[;；]", raw) if x.strip()]
        return parts if parts else [raw]
    return [raw]


def _person_from_fields(fields: dict[str, Any]) -> list[str]:
    pip = fields.get("Person in charge/Participant")
    if isinstance(pip, str):
        s = pip.strip()
        return [s] if s else []
    if isinstance(pip, list):
        names: list[str] = []
        for p in pip:
            if isinstance(p, dict):
                n = str(p.get("en_name") or p.get("text") or "").strip()
                if n:
                    names.append(n)
        return names
    return []


def list_recent_sprints_personnel(
    *,
    days: int = 21,
    limit: int = 3,
    source_view: str = _PERSON_VIEW,
) -> list[dict[str, Any]]:
    """B-S1：近 N 天内最多 limit 个 Sprint（人员看板视图）。"""
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


def _fetch_rows(conn: sqlite3.Connection, *, source_view: str, sprints: list[str]) -> list[tuple[int, dict[str, Any]]]:
    if not sprints:
        return []
    placeholders = ",".join("?" for _ in sprints)
    sql = f"""
        SELECT row_index, fields
        FROM pmo_raw_records
        WHERE source_view = ?
          AND json_extract(fields, '$.Sprint') IN ({placeholders})
        ORDER BY row_index
    """
    out: list[tuple[int, dict[str, Any]]] = []
    for row in conn.execute(sql, (source_view, *sprints)):
        raw = row["fields"]
        try:
            f = json.loads(raw) if isinstance(raw, str) else {}
        except json.JSONDecodeError:
            f = {}
        if isinstance(f, dict):
            out.append((int(row["row_index"]), f))
    return out


def _fields_to_task(
    fields: dict[str, Any],
    *,
    source_view: str,
    current_sprint: str | None,
) -> dict[str, Any] | None:
    sprint = str(fields.get("Sprint") or "").strip()
    task = str(fields.get("Requirement") or "").strip()
    task_no = str(fields.get("任务编号") or "").strip()
    if not sprint:
        return None
    persons = _person_from_fields(fields)
    person = persons[0] if len(persons) == 1 else ("; ".join(persons) if persons else "")
    progress = fields.get("Progress")
    if progress is not None and not isinstance(progress, str):
        progress = str(progress)
    return {
        "source_view": source_view,
        "person": person or None,
        "persons": persons,
        "task": task or None,
        "priority": fields.get("priority"),
        "sprint": sprint,
        "department": parent_text(fields),
        "status_text": _status_text(fields),
        "progress": progress,
        "start_date": fields.get("Start Date"),
        "review_date": fields.get("Review Date"),
        "acceptance_date": fields.get("Acceptance Date"),
        "expected_delivery_date": fields.get("Expected Delivery Date"),
        "actual_delivery_date": fields.get("Actual Delivery Date"),
        "start_date_iso": _ms_to_iso_date(fields.get("Start Date")),
        "review_date_iso": _ms_to_iso_date(fields.get("Review Date")),
        "acceptance_date_iso": _ms_to_iso_date(fields.get("Acceptance Date")),
        "expected_delivery_date_iso": _ms_to_iso_date(fields.get("Expected Delivery Date")),
        "actual_delivery_date_iso": _ms_to_iso_date(fields.get("Actual Delivery Date")),
        "task_no": task_no or None,
        "is_current_week": bool(current_sprint and sprint == current_sprint),
    }


def _merge_key(task: dict[str, Any]) -> tuple[str, str, str]:
    req = str(task.get("task") or "")[:100]
    return (
        str(task.get("task_no") or ""),
        str(task.get("sprint") or ""),
        req,
    )


def _merge_task_fields(base: dict[str, Any], other: dict[str, Any]) -> None:
    """Person 优先 base（人员看板先扫）；空字段从 other 互补。"""
    fill_keys = (
        "person",
        "priority",
        "department",
        "status_text",
        "progress",
        "start_date",
        "review_date",
        "acceptance_date",
        "expected_delivery_date",
        "actual_delivery_date",
        "start_date_iso",
        "review_date_iso",
        "acceptance_date_iso",
        "expected_delivery_date_iso",
        "actual_delivery_date_iso",
    )
    for k in fill_keys:
        if base.get(k) in (None, "", "null") and other.get(k) not in (None, "", "null"):
            base[k] = other[k]
    if not base.get("persons") and other.get("persons"):
        base["persons"] = other["persons"]
    if base.get("source_view") != other.get("source_view"):
        base["source_merged"] = True


def _merge_tasks(
    person_rows: list[dict[str, Any]],
    dev_rows: list[dict[str, Any]],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for t in person_rows:
        if not t:
            continue
        k = _merge_key(t)
        if k not in by_key:
            by_key[k] = dict(t)
        else:
            _merge_task_fields(by_key[k], t)
    for t in dev_rows:
        if not t:
            continue
        k = _merge_key(t)
        if k not in by_key:
            by_key[k] = dict(t)
        else:
            _merge_task_fields(by_key[k], t)
    return by_key


def _requirement_context_row(fields: dict[str, Any], source_view: str) -> dict[str, Any]:
    person = _person_from_fields(fields)
    pip = person[0] if len(person) == 1 else ("; ".join(person) if person else None)
    progress = fields.get("Progress")
    if progress is not None and not isinstance(progress, str):
        progress = str(progress)
    return {
        "source_view": source_view,
        "requirement": fields.get("Requirement"),
        "priority": fields.get("priority"),
        "sprint": fields.get("Sprint"),
        "person": pip,
        "status_text": _status_text(fields),
        "progress": progress,
        "task_no": fields.get("任务编号"),
    }


def _dash(v: Any) -> str:
    if v is None or v == "" or v == "null":
        return "—"
    return str(v).strip()


def enrich_personnel_task_row(task: dict[str, Any]) -> dict[str, Any]:
    """
    规范化 personnel_tasks 行：日期转 ISO、status/progress 可读化。
    保留 B-4 兼容字段名；日期展示优先 *_iso。
    """
    row = {k: v for k, v in task.items() if k != "persons"}
    date_pairs = (
        ("start_date", "start_date_iso"),
        ("review_date", "review_date_iso"),
        ("acceptance_date", "acceptance_date_iso"),
        ("expected_delivery_date", "expected_delivery_date_iso"),
        ("actual_delivery_date", "actual_delivery_date_iso"),
    )
    for raw_key, iso_key in date_pairs:
        raw = row.get(raw_key)
        iso = row.get(iso_key) or _ms_to_iso_date(raw)
        if iso:
            row[iso_key] = iso
        if raw not in (None, "", "null") and iso != raw:
            row[f"{raw_key}_raw"] = raw
    row["status"] = row.get("status_text") or row.get("status")
    prog = row.get("progress")
    if prog is not None and not isinstance(prog, str):
        row["progress"] = str(prog)
    return row


def _finalize_merged_tasks(
    merged: dict[tuple[str, str, str], dict[str, Any]],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    return {k: enrich_personnel_task_row(v) for k, v in merged.items()}


def _build_personnel_buckets(
    merged: dict[tuple[str, str, str], dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, list[dict[str, Any]]],
]:
    personnel_tasks = _personnel_flat_rows(merged)
    unassigned: list[dict[str, Any]] = []
    cross_week: list[dict[str, Any]] = []
    current_week: list[dict[str, Any]] = []
    by_person: dict[str, list[dict[str, Any]]] = {}

    for t in merged.values():
        if not t.get("task_no"):
            continue
        if not t.get("person"):
            unassigned.append(t)
            continue
        if t.get("is_current_week"):
            current_week.append(t)
        else:
            cross_week.append(t)
        for pk in person_keys_from_task(t):
            by_person.setdefault(pk, []).append(t)

    return personnel_tasks, unassigned, cross_week, current_week, by_person


def _personnel_flat_rows(merged: dict[tuple[str, str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    """B-4 兼容扁平行：有 task_no + person 的任务（已 enrich）。"""
    out: list[dict[str, Any]] = []
    for t in merged.values():
        if not t.get("task_no"):
            continue
        if not t.get("person"):
            continue
        out.append(t)
    out.sort(key=lambda r: (str(r.get("person") or ""), str(r.get("sprint") or ""), str(r.get("task_no") or "")))
    return out


def run_personnel_report_for_recent(
    *,
    days: int = 21,
    limit: int = 3,
    person_view: str = _PERSON_VIEW,
    cross_view: str = _DEV_VIEW,
) -> dict[str, Any]:
    """近三周人员战报采集（Worker B 宿主预取 / core:pmo_personnel_report recent_window）。"""
    if not pmo_mirror_db_ready():
        return {
            "status": "error",
            "error_class": "config",
            "message": "pmo_raw_records 为空；请先 INIT（core:pmo_mirror_import）",
            "db_path": str(get_pmo_db_path()),
        }

    recent = list_recent_sprints_personnel(days=days, limit=limit, source_view=person_view)
    sprint_names = [str(r["sprint"]) for r in recent if r.get("sprint")]
    current_sprint, current_sprint_date, cs_meta = resolve_current_sprint(recent)

    if not sprint_names:
        return {
            "status": "ok",
            "current_sprint": current_sprint,
            "current_sprint_date": current_sprint_date,
            "recent_sprints": [],
            "personnel_tasks": [],
            "requirement_context": [],
            "unassigned_tasks": [],
            "cross_week_tasks": [],
            "by_person": {},
            "summary": {
                "personnel_row_count": 0,
                "current_week_task_count": 0,
                "person_count": 0,
                "unassigned_count": 0,
                "cross_week_count": 0,
            },
            "sprint_window_empty": True,
            "_current_sprint_meta": cs_meta,
        }

    conn = _connect()
    try:
        person_parsed: list[dict[str, Any]] = []
        dev_parsed: list[dict[str, Any]] = []
        req_ctx: list[dict[str, Any]] = []
        for _idx, f in _fetch_rows(conn, source_view=person_view, sprints=sprint_names):
            t = _fields_to_task(f, source_view=person_view, current_sprint=current_sprint)
            if t and t.get("task_no"):
                person_parsed.append(t)
        for _idx, f in _fetch_rows(conn, source_view=cross_view, sprints=sprint_names):
            t = _fields_to_task(f, source_view=cross_view, current_sprint=current_sprint)
            if t and (t.get("task_no") or t.get("task")):
                dev_parsed.append(t)
            req_ctx.append(_requirement_context_row(f, cross_view))
    finally:
        conn.close()

    merged = _finalize_merged_tasks(_merge_tasks(person_parsed, dev_parsed))
    personnel_tasks, unassigned, cross_week, current_week, by_person = _build_personnel_buckets(merged)

    summary = {
        "personnel_row_count": len(personnel_tasks),
        "current_week_task_count": len(current_week),
        "person_count": len(by_person),
        "unassigned_count": len(unassigned),
        "cross_week_count": len(cross_week),
    }

    return {
        "status": "ok",
        "current_sprint": current_sprint,
        "current_sprint_date": current_sprint_date,
        "recent_sprints": recent,
        "personnel_tasks": personnel_tasks,
        "requirement_context": req_ctx,
        "unassigned_tasks": unassigned,
        "cross_week_tasks": cross_week,
        "by_person": by_person,
        "summary": summary,
        "_current_sprint_meta": cs_meta,
    }


def run_personnel_report(
    *,
    sprint: str | None = None,
    recent_window: bool = False,
    person_view: str = _PERSON_VIEW,
    cross_view: str = _DEV_VIEW,
) -> dict[str, Any]:
    """core:pmo_personnel_report 入口。"""
    if recent_window or not (sprint or "").strip():
        rep = run_personnel_report_for_recent(person_view=person_view, cross_view=cross_view)
    else:
        sprint_s = sprint.strip()
        recent = list_recent_sprints_personnel(limit=10)
        if not any(str(r.get("sprint")) == sprint_s for r in recent):
            recent.insert(0, {"sprint": sprint_s, "sprint_date": None, "cnt": 0})
        cs, cs_date, cs_meta = resolve_current_sprint(recent)
        if sprint_s:
            cs = sprint_s
            for r in recent:
                if str(r.get("sprint")) == sprint_s and r.get("sprint_date"):
                    cs_date = str(r["sprint_date"])[:10]
                    break
        conn = _connect()
        try:
            person_parsed = []
            dev_parsed = []
            req_ctx = []
            for _idx, f in _fetch_rows(conn, source_view=person_view, sprints=[sprint_s]):
                t = _fields_to_task(f, source_view=person_view, current_sprint=cs)
                if t and t.get("task_no"):
                    person_parsed.append(t)
            for _idx, f in _fetch_rows(conn, source_view=cross_view, sprints=[sprint_s]):
                t = _fields_to_task(f, source_view=cross_view, current_sprint=cs)
                if t and (t.get("task_no") or t.get("task")):
                    dev_parsed.append(t)
                req_ctx.append(_requirement_context_row(f, cross_view))
        finally:
            conn.close()
        merged = _finalize_merged_tasks(_merge_tasks(person_parsed, dev_parsed))
        personnel_tasks, unassigned, cross_week, current_week, by_person = _build_personnel_buckets(merged)
        rep = {
            "status": "ok",
            "current_sprint": cs,
            "current_sprint_date": cs_date,
            "recent_sprints": recent[:3],
            "personnel_tasks": personnel_tasks,
            "requirement_context": req_ctx,
            "unassigned_tasks": unassigned,
            "cross_week_tasks": cross_week,
            "by_person": by_person,
            "summary": {
                "personnel_row_count": len(personnel_tasks),
                "current_week_task_count": len(current_week),
                "person_count": len(by_person),
                "unassigned_count": len(unassigned),
                "cross_week_count": len(cross_week),
            },
            "_current_sprint_meta": cs_meta,
        }
    if str(rep.get("status") or "").lower() == "ok":
        rep["completed_sql_ids"] = ["B-TOOL"]
        from l3_node.tools.pmo_personnel_format import format_personnel_report_text

        rep["formatted_text"] = format_personnel_report_text(rep)
    return rep
