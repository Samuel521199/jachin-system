"""
PMO 变更预警：宿主 Python 三轴分析 + 决策门 + 可选 Lark 推送。

SSOT：skills_repo/pmo-copilot/SKILL.change-alert.md
      docs/architecture/PMO_CHANGE_ALERT_CASE_STUDY_0605_MAHJONG.md

查数、规则、推送均由本模块完成；Agent 仅可选 narrate，禁止自由 db_query。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEV_VIEW = "vewpI8lyYw"
_PERSON_VIEW = "vewCz1FFJi"
_PRODUCT_VIEWS = ("vew8TxMcSh", "vewL9Mofgd")
_DEFAULT_MONITOR_CHAT = None  # 运行时由 pmo_lark_env.pmo_change_alert_monitor_chat_id() 解析
_DEDUP_STORE_PATH = Path.home() / ".jachin" / "data" / "pmo_change_alert_dedup.json"
_DEFAULT_DEDUP_WINDOW_SEC = 3600

_CHANGE_TYPE_CN = {
    "created": "新增",
    "updated": "修改",
    "deleted": "删除",
}


def _dedup_window_seconds() -> int:
    raw = (os.environ.get("PMO_CHANGE_ALERT_DEDUP_SECONDS") or "").strip()
    if raw:
        try:
            return max(60, int(raw))
        except ValueError:
            pass
    return _DEFAULT_DEDUP_WINDOW_SEC


def _change_alert_dedup_fingerprint(events: list[dict[str, Any]], fact: dict[str, Any]) -> str:
    """同一批变更 + 同一分析结论 → 相同指纹（用于抑制重复推送）。"""
    parts: list[str] = []
    for e in sorted(events, key=lambda x: str(x.get("record_id") or x.get("label") or "")):
        parts.append(
            "|".join(
                [
                    str(e.get("record_id") or ""),
                    str(e.get("change_type") or ""),
                    str(e.get("label") or ""),
                    str((e.get("semantic") or {}).get("requirement") if isinstance(e.get("semantic"), dict) else ""),
                ]
            )
        )
    parts.append(str(fact.get("change_alert_result") or ""))
    parts.append(str(fact.get("should_push")))
    parts.append(str(fact.get("max_severity_score")))
    blob = "\n".join(parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _load_dedup_store() -> dict[str, Any]:
    if not _DEDUP_STORE_PATH.is_file():
        return {"pushes": {}}
    try:
        raw = json.loads(_DEDUP_STORE_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {"pushes": {}}
    except (json.JSONDecodeError, OSError):
        return {"pushes": {}}


def _save_dedup_store(store: dict[str, Any]) -> None:
    try:
        _DEDUP_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _DEDUP_STORE_PATH.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning("[PMO change_alert] dedup store write failed: %s", e)


def _dedup_recently_pushed(fingerprint: str) -> bool:
    store = _load_dedup_store()
    pushes = store.get("pushes") if isinstance(store.get("pushes"), dict) else {}
    ts = pushes.get(fingerprint)
    if not ts:
        return False
    try:
        from datetime import datetime as dt

        sent = dt.fromisoformat(str(ts).replace("Z", "+00:00"))
        if sent.tzinfo is None:
            sent = sent.replace(tzinfo=timezone.utc)
        age = (_utc_now() - sent.astimezone(timezone.utc)).total_seconds()
        return age < _dedup_window_seconds()
    except (TypeError, ValueError):
        return False


def _dedup_mark_pushed(fingerprint: str) -> None:
    store = _load_dedup_store()
    pushes = store.get("pushes") if isinstance(store.get("pushes"), dict) else {}
    pushes[fingerprint] = _utc_now().isoformat()
    #  prune old entries
    window = _dedup_window_seconds()
    from datetime import datetime as dt

    kept: dict[str, str] = {}
    for k, v in pushes.items():
        try:
            sent = dt.fromisoformat(str(v).replace("Z", "+00:00"))
            if sent.tzinfo is None:
                sent = sent.replace(tzinfo=timezone.utc)
            if (_utc_now() - sent.astimezone(timezone.utc)).total_seconds() < window * 2:
                kept[k] = str(v)
        except (TypeError, ValueError):
            continue
    store["pushes"] = kept
    _save_dedup_store(store)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


_FIELD_ALIASES = {
    "requirement": ("Requirement", "任务名称", "名称", "title", "标题"),
    "assignee": ("Person in charge/Participant", "负责人", "执行人"),
    "sprint": ("Sprint",),
    "start": ("Start Date", "开始日期"),
    "expected_due": ("Expected Delivery Date", "期待交付", "Expected Delivery"),
    "acceptable_due": ("Acceptable Delivery Date", "可接受交付"),
    "priority": ("priority", "Priority", "优先级"),
    "status": ("状态", "Status"),
}

_TEAM_MARKERS = ("组", "团队", "Team", "team", "部门", "工作室")


def _today_iso() -> str:
    from l3_node.tools.pmo_dates import pmo_today_iso

    return pmo_today_iso()


def _parse_date(val: Any) -> str | None:
    if val is None or val == "":
        return None
    s = str(val).strip()
    if not s:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    if re.match(r"^\d{4}/\d{2}/\d{2}", s):
        return s[:10].replace("/", "-")
    try:
        ts = int(float(s))
        if ts > 1e10:
            from l3_node.tools.pmo_dates import pmo_ms_to_iso_date

            return pmo_ms_to_iso_date(ts)
    except (TypeError, ValueError):
        pass
    return None


def _field_lookup(fields: dict[str, str], *keys: str) -> str:
    for k in keys:
        v = fields.get(k)
        if v not in (None, "", "null"):
            return str(v).strip()
    return ""


def _extract_semantic_fields(fields: dict[str, str]) -> dict[str, Any]:
    return {
        "requirement": _field_lookup(fields, *_FIELD_ALIASES["requirement"]),
        "assignee_raw": _field_lookup(fields, *_FIELD_ALIASES["assignee"]),
        "sprint": _field_lookup(fields, *_FIELD_ALIASES["sprint"]),
        "start_date": _parse_date(_field_lookup(fields, *_FIELD_ALIASES["start"])),
        "expected_due": _parse_date(_field_lookup(fields, *_FIELD_ALIASES["expected_due"])),
        "acceptable_due": _parse_date(_field_lookup(fields, *_FIELD_ALIASES["acceptable_due"])),
        "priority": _field_lookup(fields, *_FIELD_ALIASES["priority"]),
        "status": _field_lookup(fields, *_FIELD_ALIASES["status"]),
    }


def _names_from_lark_person_value(val: Any) -> list[str]:
    """从飞书人员字段 JSON（dict / list）提取 display name。"""
    names: list[str] = []
    if isinstance(val, list):
        for item in val:
            names.extend(_names_from_lark_person_value(item))
    elif isinstance(val, dict):
        for key in ("name", "en_name", "cn_name", "display_name"):
            n = str(val.get(key) or "").strip()
            if n and len(n) <= 40 and n not in names:
                names.append(n)
                break
    return names


def _parse_assignees(raw: str) -> tuple[list[str], list[str]]:
    """返回 (valid_persons, warnings)。"""
    warnings: list[str] = []
    text = (raw or "").strip()
    if not text:
        return [], warnings
    if any(m in text for m in _TEAM_MARKERS):
        warnings.append("assignee_is_team")
        return [], warnings
    if text.startswith("[") or text.startswith("{"):
        try:
            parsed = json.loads(text)
            from_json = _names_from_lark_person_value(parsed)
            if from_json:
                return from_json, warnings
            warnings.append("assignee_unparseable")
            return [], warnings
        except json.JSONDecodeError:
            pass
    parts = re.split(r"[;；]", text)
    persons: list[str] = []
    for p in parts:
        name = p.strip()
        if not name:
            continue
        if any(m in name for m in _TEAM_MARKERS):
            warnings.append("assignee_is_team")
            continue
        if len(name) > 40:
            warnings.append("assignee_unparseable")
            continue
        if name not in persons:
            persons.append(name)
    return persons, warnings


def _assignees_from_event(evt: dict[str, Any]) -> tuple[list[str], list[str]]:
    after = evt.get("after") if isinstance(evt.get("after"), dict) else {}
    before = evt.get("before") if isinstance(evt.get("before"), dict) else {}
    changed = evt.get("changed_fields") if isinstance(evt.get("changed_fields"), dict) else {}
    persons: list[str] = []
    warnings: list[str] = []

    for side in (after, before):
        sem = _extract_semantic_fields(side)
        p, w = _parse_assignees(sem.get("assignee_raw") or "")
        persons.extend(p)
        warnings.extend(w)

    for key, pair in changed.items():
        if not isinstance(pair, dict):
            continue
        k = str(key).lower()
        if "person" in k or "负责人" in k or "participant" in k:
            for side in ("before", "after"):
                p, w = _parse_assignees(str(pair.get(side) or ""))
                persons.extend(p)
                warnings.extend(w)

    deduped: list[str] = []
    for p in persons:
        if p and p not in deduped:
            deduped.append(p)
    dedup_warnings = list(dict.fromkeys(warnings))
    return deduped, dedup_warnings


def _infer_change_subtype(evt: dict[str, Any], sem: dict[str, Any]) -> str:
    ct = str(evt.get("change_type") or "updated")
    changed = evt.get("changed_fields") or {}
    if ct == "created":
        return "insert"
    if ct == "deleted":
        return "delete"
    keys_lower = {str(k).lower() for k in changed}
    if any("person" in k or "负责人" in k for k in keys_lower):
        return "assignee_changed"
    if any("delivery" in k or "交付" in k or "due" in k for k in keys_lower):
        return "due_changed"
    if any("priority" in k or "优先级" in k for k in keys_lower):
        return "priority_changed"
    if any("sprint" in k for k in keys_lower):
        return "sprint_changed"
    if ct == "created" or (not sem.get("assignee_raw") and sem.get("requirement")):
        return "insert"
    return "updated"


def _severity_score(evt: dict[str, Any], sem: dict[str, Any], *, current_sprint: str | None) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    changed = evt.get("changed_fields") or {}
    subtype = _infer_change_subtype(evt, sem)

    def _pair(field_keys: tuple[str, ...]) -> dict[str, str] | None:
        for k, v in changed.items():
            kl = str(k).lower()
            if any(fk.lower() in kl for fk in field_keys):
                return v if isinstance(v, dict) else None
        return None

    pr = _pair(("priority", "优先级"))
    if pr:
        after_p = str(pr.get("after") or "").upper()
        before_p = str(pr.get("before") or "").upper()
        if after_p == "P0" and before_p != "P0":
            score += 30
            reasons.append("P0_bump")

    if subtype == "assignee_changed":
        score += 15
        reasons.append("assignee_changed")

    if str(evt.get("change_type")) == "created" and current_sprint and sem.get("sprint") == current_sprint:
        score += 20
        reasons.append("mid_sprint_insert")

    due_pair = _pair(("Expected Delivery", "期待交付", "Acceptable Delivery"))
    if due_pair:
        after_d = _parse_date(due_pair.get("after"))
        before_d = _parse_date(due_pair.get("before"))
        if after_d and before_d and after_d < before_d:
            score += 15
            reasons.append("due_moved_earlier")
        if after_d and current_sprint and sem.get("sprint") == current_sprint:
            score += 25
            reasons.append("due_in_current_sprint")

    st = _pair(("状态", "status"))
    if st:
        regress = ("待开始", "pending", "待评审")
        if any(r in str(st.get("after") or "") for r in regress) and "开发" in str(st.get("before") or ""):
            score += 20
            reasons.append("status_regression")

    if sem.get("start_date") and sem.get("expected_due") and sem["start_date"] == sem["expected_due"]:
        score += 10
        reasons.append("zero_buffer")

    return min(score, 100), reasons


def _run_sql(sql: str, *, max_rows: int = 50) -> list[dict[str, Any]]:
    from l3_node.tools.pmo_db_tools import run_db_query

    res = run_db_query(sql=sql, max_rows=max_rows)
    if str(res.get("status") or "").lower() != "ok":
        logger.warning("[PMO change_alert] SQL failed: %s", res.get("message") or res.get("error"))
        return []
    rows = res.get("rows")
    return rows if isinstance(rows, list) else []


def _sql_escape(s: str) -> str:
    return s.replace("'", "''")


def _mirror_keyword_search(keyword: str) -> dict[str, Any]:
    if not keyword or len(keyword) < 2:
        return {"dev_hits": [], "person_hits": [], "product_hits": []}
    kw = _sql_escape(keyword[:80])
    dev = _run_sql(
        f"""
        SELECT source_view, row_index,
               json_extract(fields, '$.Requirement') AS requirement,
               json_extract(fields, '$.Sprint') AS sprint
        FROM pmo_raw_records
        WHERE source_view = '{_DEV_VIEW}'
          AND (fields LIKE '%{kw}%' OR raw_text LIKE '%{kw}%')
        LIMIT 15
        """
    )
    person = _run_sql(
        f"""
        SELECT source_view, row_index,
               json_extract(fields, '$.Requirement') AS requirement
        FROM pmo_raw_records
        WHERE source_view = '{_PERSON_VIEW}'
          AND (fields LIKE '%{kw}%' OR raw_text LIKE '%{kw}%')
        LIMIT 15
        """
    )
    prod_views = ", ".join(f"'{v}'" for v in _PRODUCT_VIEWS)
    product = _run_sql(
        f"""
        SELECT source_view, row_index,
               json_extract(fields, '$.Requirement') AS requirement
        FROM pmo_raw_records
        WHERE source_view IN ({prod_views})
          AND (fields LIKE '%{kw}%' OR raw_text LIKE '%{kw}%')
        LIMIT 15
        """
    )
    return {"dev_hits": dev, "person_hits": person, "product_hits": product}


def _host_personnel_bootstrap() -> dict[str, Any]:
    try:
        from l3_node.pmo_worker_result_backfill import run_worker_b_host_bootstrap

        return run_worker_b_host_bootstrap()
    except Exception as e:
        logger.warning("[PMO change_alert] B-TOOL bootstrap failed: %s", e)
        return {}


def _tasks_for_person(personnel_tasks: list[dict[str, Any]], person: str) -> list[dict[str, Any]]:
    p_norm = person.strip().lower()
    out: list[dict[str, Any]] = []
    for t in personnel_tasks:
        if not isinstance(t, dict):
            continue
        candidates = [str(t.get("person") or "")]
        if isinstance(t.get("persons"), list):
            candidates.extend(str(x) for x in t["persons"])
        if any(c.strip().lower() == p_norm for c in candidates if c):
            out.append(t)
    return out


def _task_terminal(task: dict[str, Any]) -> bool:
    if task.get("actual_delivery_date_iso"):
        return True
    progress = str(task.get("progress") or "")
    status = str(task.get("status_text") or "")
    if "完成" in progress or "100" in progress:
        return True
    for sym in ("🟢", "🔵", "✅"):
        if sym in status:
            return True
    return False


def _analyze_personnel(
    persons: list[str],
    personnel_tasks: list[dict[str, Any]],
    *,
    current_sprint: str | None,
    change_sem: dict[str, Any],
    today: str,
    assignee_warnings: list[str],
) -> dict[str, Any]:
    if assignee_warnings and not persons:
        return {
            "status": "skipped",
            "verdict": "warning",
            "symbol": "⚠️",
            "message": "责任人未分配或无法解析为个人，人员轴跳过",
            "warnings": assignee_warnings,
            "people": [],
        }

    if not persons:
        return {
            "status": "skipped",
            "verdict": "warning",
            "symbol": "⚠️",
            "message": "无有效负责人，无法评估人员负荷",
            "people": [],
        }

    people_out: list[dict[str, Any]] = []
    any_alert = False
    any_warning = False

    for person in persons:
        tasks = _tasks_for_person(personnel_tasks, person)
        sprint_tasks = [
            t
            for t in tasks
            if t.get("is_current_week")
            or (current_sprint and str(t.get("sprint") or "") == current_sprint)
        ]
        m = len(sprint_tasks)
        k = sum(1 for t in sprint_tasks if _task_terminal(t))
        overdue = []
        for t in sprint_tasks:
            due = t.get("expected_delivery_date_iso") or _parse_date(t.get("expected_delivery_date"))
            if due and due < today and not _task_terminal(t):
                overdue.append({"task": t.get("task"), "due": due})

        symbol = "✅"
        verdict = "ok"
        reasons: list[str] = []

        if overdue:
            symbol = "🚨"
            verdict = "alert"
            any_alert = True
            reasons.append(f"计划交付已过 {len(overdue)} 项仍无完成记录")

        new_due = change_sem.get("expected_due")
        new_start = change_sem.get("start_date")
        if new_due == today and new_start == today and person in persons:
            if verdict != "alert":
                symbol = "🚨"
                verdict = "alert"
            any_alert = True
            reasons.append("同日 Start=Due 插单，节奏恶化")

        task_sprint = change_sem.get("sprint")
        if m == 0 and not overdue:
            if current_sprint and task_sprint and task_sprint != current_sprint:
                reasons.append("任务在未来 Sprint，当前周期无负荷属正常")
            else:
                symbol = "⚠️"
                verdict = "warning"
                any_warning = True
                reasons.append("镜像中未找到该员本 Sprint 任务记录")

        people_out.append(
            {
                "person": person,
                "planned_m": m,
                "completed_k": k,
                "overdue_count": len(overdue),
                "overdue_tasks": overdue[:5],
                "symbol": symbol,
                "verdict": verdict,
                "reason": "；".join(reasons) if reasons else "本周期节奏正常",
            }
        )

    overall = "ok"
    sym = "✅"
    if any_alert:
        overall, sym = "alert", "🚨"
    elif any_warning:
        overall, sym = "warning", "⚠️"

    return {
        "status": "ok" if persons else "skipped",
        "verdict": overall,
        "symbol": sym,
        "people": people_out,
    }


def _analyze_schedule(
    sem: dict[str, Any],
    *,
    current_sprint: str | None,
    today: str,
    mirror_in_mirror: bool,
) -> dict[str, Any]:
    risks: list[str] = []
    warnings: list[str] = []
    symbol = "✅"
    verdict = "ok"

    start = sem.get("start_date")
    due = sem.get("expected_due")
    acc = sem.get("acceptable_due")
    sprint = sem.get("sprint")

    if not due and not start and not sprint:
        return {
            "status": "degraded",
            "verdict": "warning",
            "symbol": "⚠️",
            "risks": ["date_fields_missing"],
            "message": "缺少 Start/Due/Sprint，排期轴降级",
        }

    if start and due and start == due:
        risks.append("zero_buffer")
        symbol, verdict = "⚠️", "warning"

    if due and acc:
        try:
            from datetime import datetime as dt

            d0 = dt.strptime(due, "%Y-%m-%d")
            d1 = dt.strptime(acc, "%Y-%m-%d")
            if (d1 - d0).days <= 1:
                risks.append("buffer_very_short")
                symbol, verdict = "⚠️", "warning"
        except ValueError:
            pass

    if due and due < today:
        risks.append("due_already_passed")
        symbol, verdict = "🚨", "alert"

    if current_sprint and sprint == current_sprint:
        risks.append("mid_sprint_change")

    if not mirror_in_mirror and sem.get("requirement"):
        warnings.append("mirror_row_missing")

    return {
        "status": "ok",
        "verdict": verdict,
        "symbol": symbol,
        "risks": risks,
        "warnings": warnings,
        "fields": {
            "start": start,
            "expected_due": due,
            "acceptable_due": acc,
            "sprint": sprint,
        },
    }


def _analyze_project(
    sem: dict[str, Any],
    mirror: dict[str, Any],
) -> dict[str, Any]:
    risks: list[str] = []
    req = sem.get("requirement") or ""
    dev_n = len(mirror.get("dev_hits") or [])
    prod_n = len(mirror.get("product_hits") or [])
    person_n = len(mirror.get("person_hits") or [])

    if req and dev_n == 0 and person_n == 0:
        risks.append("mirror_row_missing")
    if req and prod_n == 0 and dev_n > 0:
        risks.append("cross_view_mismatch")
    if not req:
        risks.append("requirement_name_missing")

    symbol = "⚠️" if risks else "✅"
    verdict = "warning" if risks else "ok"
    return {
        "status": "ok",
        "verdict": verdict,
        "symbol": symbol,
        "risks": risks,
        "mirror_counts": {"dev": dev_n, "person": person_n, "product": prod_n},
    }


def _route_axes(sem: dict[str, Any], persons: list[str], assignee_warnings: list[str]) -> dict[str, bool]:
    has_person = bool(persons) and not assignee_warnings
    has_schedule = bool(sem.get("expected_due") or sem.get("start_date") or sem.get("sprint"))
    has_project = bool(sem.get("requirement") or sem.get("assignee_raw"))
    return {
        "schedule": has_schedule,
        "personnel": has_person,
        "project": has_project or True,
    }


def _should_push(
    schedule: dict[str, Any],
    personnel: dict[str, Any],
    project: dict[str, Any],
    *,
    severity_score: int,
) -> bool:
    if schedule.get("verdict") == "alert" or personnel.get("verdict") == "alert":
        return True

    sched_risks = set(str(x) for x in (schedule.get("risks") or []))
    proj_risks = set(str(x) for x in (project.get("risks") or []))

    meaningful_schedule = schedule.get("verdict") == "warning" and bool(
        sched_risks - {"mid_sprint_change"}
    )

    people = personnel.get("people") or []
    meaningful_personnel = False
    if personnel.get("verdict") == "warning":
        if personnel.get("status") == "skipped":
            meaningful_personnel = True
        elif any(
            p.get("verdict") in ("alert", "warning")
            and "未来 Sprint" not in (p.get("reason") or "")
            for p in people
        ):
            meaningful_personnel = True

    meaningful_project = bool(proj_risks - {"mirror_row_missing"})

    if meaningful_schedule or meaningful_personnel or meaningful_project:
        return True

    if severity_score >= 40 and (
        sched_risks - {"mid_sprint_change"}
        or proj_risks - {"mirror_row_missing"}
        or any(p.get("verdict") == "alert" for p in people)
    ):
        return True
    return False


def analyze_change_events(
    events: list[dict[str, Any]],
    *,
    personnel_seed: dict[str, Any] | None = None,
    today: str | None = None,
) -> dict[str, Any]:
    """对一批变更事件做三轴分析，返回 fact_pack。"""
    today_s = today or _today_iso()
    seed = personnel_seed if personnel_seed is not None else _host_personnel_bootstrap()
    personnel_tasks = list(seed.get("personnel_tasks") or [])
    current_sprint = seed.get("current_sprint")

    analyzed: list[dict[str, Any]] = []
    max_severity = 0
    push_any = False

    for evt in events:
        after = evt.get("after") if isinstance(evt.get("after"), dict) else {}
        sem = _extract_semantic_fields(after)
        if not sem.get("requirement"):
            sem["requirement"] = str(evt.get("label") or "").strip()

        persons, assignee_warnings = _assignees_from_event(evt)
        severity, sev_reasons = _severity_score(evt, sem, current_sprint=current_sprint)
        max_severity = max(max_severity, severity)

        keyword = sem.get("requirement") or str(evt.get("record_id") or "")
        mirror = _mirror_keyword_search(keyword) if keyword else {"dev_hits": [], "person_hits": [], "product_hits": []}
        in_mirror = bool(mirror.get("dev_hits") or mirror.get("person_hits"))

        axes_route = _route_axes(sem, persons, assignee_warnings)
        schedule = (
            _analyze_schedule(sem, current_sprint=current_sprint, today=today_s, mirror_in_mirror=in_mirror)
            if axes_route["schedule"]
            else {
                "status": "degraded",
                "verdict": "warning",
                "symbol": "⚠️",
                "message": "排期字段不足，轴一降级",
                "risks": ["schedule_axis_degraded"],
            }
        )
        personnel = _analyze_personnel(
            persons,
            personnel_tasks,
            current_sprint=current_sprint,
            change_sem=sem,
            today=today_s,
            assignee_warnings=assignee_warnings,
        )
        project = _analyze_project(sem, mirror)

        decision_push = _should_push(schedule, personnel, project, severity_score=severity)
        if decision_push:
            push_any = True

        analyzed.append(
            {
                "change_type": evt.get("change_type"),
                "change_subtype": _infer_change_subtype(evt, sem),
                "record_id": evt.get("record_id"),
                "label": sem.get("requirement") or evt.get("label"),
                "semantic": sem,
                "assignees": persons,
                "assignee_warnings": assignee_warnings,
                "severity_score": severity,
                "severity_reasons": sev_reasons,
                "axes_route": axes_route,
                "schedule_axis": schedule,
                "personnel_axis": personnel,
                "project_axis": project,
                "mirror": mirror,
                "decision_push": decision_push,
            }
        )

    result = "alert_sent" if push_any else "all_clear"
    return {
        "status": "ok",
        "change_alert_result": result,
        "today": today_s,
        "current_sprint": current_sprint,
        "event_count": len(analyzed),
        "max_severity_score": max_severity,
        "should_push": push_any,
        "analyzed_events": analyzed,
        "personnel_bootstrap": {
            "source": seed.get("_host_bootstrap") or seed.get("completed_sql_ids"),
            "personnel_rows": len(personnel_tasks),
        },
    }


def format_change_alert_markdown(fact_pack: dict[str, Any]) -> str:
    today_s = fact_pack.get("today") or _today_iso()
    events = fact_pack.get("analyzed_events") or []
    if not events:
        return f"⚠️ **变更预警** · {today_s}\n\n无有效变更事件。\n\n`change_alert_result: all_clear`"

    first_label = events[0].get("label") or "变更"
    title_hint = first_label if len(events) == 1 else f"{first_label} 等 {len(events)} 条"

    lines = [
        f"⚠️ **变更预警** · {today_s} · {title_hint}",
        "",
        "---",
        "",
    ]

    for idx, item in enumerate(events, 1):
        sem = item.get("semantic") or {}
        ct = _CHANGE_TYPE_CN.get(str(item.get("change_type")), str(item.get("change_type")))
        lines.append(f"### 📌 变更摘要 {idx} · {ct}")
        lines.append("")
        lines.append("| 项 | 内容 |")
        lines.append("| :--- | :--- |")
        lines.append(f"| 需求 | **{sem.get('requirement') or item.get('label') or '—'}** |")
        if sem.get("sprint"):
            lines.append(f"| Sprint | {sem.get('sprint')} |")
        if sem.get("start_date"):
            lines.append(f"| Start | {sem.get('start_date')} |")
        if sem.get("expected_due"):
            lines.append(f"| 期待交付 | {sem.get('expected_due')} |")
        if sem.get("acceptable_due"):
            lines.append(f"| 可接受交付 | {sem.get('acceptable_due')} |")
        assignees = item.get("assignees") or []
        if assignees:
            lines.append(f"| 负责人 | **{', '.join(assignees)}** |")
        elif item.get("assignee_warnings"):
            lines.append("| 负责人 | ⚠️ 未分配或无法解析 |")
        lines.append(f"| 严重度 | {item.get('severity_score')} |")
        lines.append("")

        sched = item.get("schedule_axis") or {}
        lines.append("**排期 / 变更**")
        lines.append(f"- 判定：{sched.get('symbol', '—')} {sched.get('verdict', '')}")
        if sched.get("risks"):
            lines.append(f"- 风险：{', '.join(sched['risks'])}")
        if sched.get("message"):
            lines.append(f"- {sched['message']}")
        lines.append("")

        pers = item.get("personnel_axis") or {}
        lines.append("**👥 人员影响（§1.4.1b）**")
        lines.append("")
        people = pers.get("people") or []
        if pers.get("status") == "skipped":
            lines.append(f"⚠️ {pers.get('message', '人员轴跳过')}")
        elif people:
            lines.append("| 人员 | 本 Sprint | 判定 | 依据 |")
            lines.append("| :--- | :--- | :--- | :--- |")
            for p in people:
                lines.append(
                    f"| **{p.get('person')}** | 计划 {p.get('planned_m')} / 完成 {p.get('completed_k')} | "
                    f"{p.get('symbol')} | {p.get('reason', '')[:120]} |"
                )
        else:
            lines.append("（无人员数据）")
        lines.append("")

        proj = item.get("project_axis") or {}
        lines.append("**⚠️ 项目 / 数据**")
        if proj.get("risks"):
            for r in proj["risks"]:
                if r == "mirror_row_missing":
                    lines.append("- ⚠️ 镜像库未检索到该变更行（可能 lag / 分页 / 视图未同步）")
                elif r == "cross_view_mismatch":
                    lines.append("- ⚠️ 产品侧无对齐记录 → 跨视图风险")
                elif r == "requirement_name_missing":
                    lines.append("- ⚠️ 需求名缺失 → 数据质量预警")
                else:
                    lines.append(f"- ⚠️ {r}")
        else:
            lines.append("- ✅ 未发现明显跨视图矛盾")
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.append("💡 **建议动作**（需 PM 确认，系统不自动改表）")
    lines.append("")
    lines.append("1. 核对变更字段是否完整（负责人 / 交付日）")
    lines.append("2. 延期项未闭环前谨慎插单或后移交付日")
    lines.append("3. 产品表与开发表对齐 Epic / 负责人")
    lines.append("")
    lines.append(f"`change_alert_result: {fact_pack.get('change_alert_result', 'all_clear')}`")
    return "\n".join(lines)


_SCHEDULE_RISK_CN: dict[str, str] = {
    "zero_buffer": "开工日与期待交付同一天，几乎没有缓冲",
    "buffer_very_short": "可接受交付日只比期待日晚 1 天，缓冲偏紧",
    "mid_sprint_change": "落在当前 Sprint 中途插入",
    "due_past": "期待交付日已早于今天",
    "mid_sprint_insert": "当前 Sprint 内新增",
}


def _event_change_sentence(item: dict[str, Any]) -> str:
    """单条变更的自然语言摘要（用于【变更】段）。"""
    sem = item.get("semantic") or {}
    name = sem.get("requirement") or item.get("label") or "未命名需求"
    ct = _CHANGE_TYPE_CN.get(str(item.get("change_type")), "变更")
    assignees = item.get("assignees") or []
    sprint = sem.get("sprint") or ""
    start = sem.get("start_date") or ""
    due = sem.get("expected_due") or ""

    who = f"指派给 **{', '.join(assignees)}**" if assignees else "尚未指定负责人"
    when = ""
    if sprint:
        when = f"纳入 **{sprint}**"
    if start and due:
        when = (when + "，" if when else "") + (
            f"**{start}** 开工、**{due}** 交付"
            if start != due
            else f"**{start}** 当天开工且当天交付"
        )
    elif due:
        when = (when + "，" if when else "") + f"期待 **{due}** 交付"

    tail = f"，{when}" if when else ""
    return f"{ct}「**{name}**」{who}{tail}"


def _event_impact_sentences(item: dict[str, Any]) -> list[str]:
    """单条变更的交叉影响句（用于【影响】段；无实质风险则返回空）。"""
    sem = item.get("semantic") or {}
    name = sem.get("requirement") or item.get("label") or "该需求"
    sched = item.get("schedule_axis") or {}
    pers = item.get("personnel_axis") or {}
    proj = item.get("project_axis") or {}
    risks = set(str(x) for x in (sched.get("risks") or []))
    out: list[str] = []

    if "due_already_passed" in risks or "due_past" in risks:
        out.append(f"「{name}」期待交付日已早于今天。")
    elif "zero_buffer" in risks:
        out.append(f"「{name}」开工与交付同一天，几乎没有缓冲。")
    elif "buffer_very_short" in risks:
        out.append(f"「{name}」可接受交付日仅比期待日晚约 1 天，缓冲偏紧。")
    elif "mid_sprint_change" in risks or "mid_sprint_insert" in risks:
        out.append(f"「{name}」落在当前 Sprint 中途变更，需关注对既有承诺的冲击。")

    if pers.get("status") == "skipped":
        msg = pers.get("message") or ""
        if "无有效负责人" in msg or "未分配" in msg:
            out.append(f"「{name}」Epic/父需求层尚未指定 Owner，只有子任务有人时容易扯皮。")
    else:
        for p in pers.get("people") or []:
            person = p.get("person") or ""
            sym = p.get("symbol") or ""
            if sym != "🚨" and sym != "⚠️":
                continue
            if "未来 Sprint" in (p.get("reason") or ""):
                continue
            overdue = p.get("overdue_tasks") or []
            if overdue and sym == "🚨":
                tasks = "、".join(
                    f"「{t.get('task')}」（计划 {t.get('due')}）"
                    for t in overdue[:3]
                    if t.get("task")
                )
                out.append(
                    f"**{person}** 本 Sprint 仍有 {tasks} 尚未关闭，"
                    f"此时再动「{name}」的节奏风险偏高。"
                )
            elif "今天又插了一条" in _humanize_person_reason(p.get("reason") or ""):
                out.append(
                    f"**{person}** 今天又插了一条当天就要交的任务（「{name}」），"
                    "与手头未闭环项叠加后节奏会恶化。"
                )
            elif sym == "⚠️" and p.get("verdict") == "warning":
                out.append(f"**{person}** 在镜像库里暂无本 Sprint 负荷记录，建议 PM 核对是否刚改表。")

    proj_risks = set(str(x) for x in (proj.get("risks") or []))
    if "cross_view_mismatch" in proj_risks:
        out.append(f"「{name}」在开发表有记录但产品侧未对齐，Epic/负责人可能对不上。")

    return out


def _event_action_hint(item: dict[str, Any]) -> str | None:
    """单条变更的可执行建议（去重后合并进【建议】）。"""
    sem = item.get("semantic") or {}
    name = sem.get("requirement") or item.get("label") or "该需求"
    pers = item.get("personnel_axis") or {}
    hints: list[str] = []

    if pers.get("status") == "skipped":
        hints.append(f"给「{name}」补 Owner，避免只有子任务有人扛。")

    alert_people = [
        p for p in (pers.get("people") or [])
        if p.get("symbol") == "🚨" and p.get("person")
    ]
    if alert_people:
        who = alert_people[0].get("person")
        hints.append(f"先和 **{who}** 对齐手头延期项，再定「{name}」是否维持当前交付日。")

    sched = item.get("schedule_axis") or {}
    if "zero_buffer" in (sched.get("risks") or []) and not alert_people:
        hints.append(f"评估「{name}」是否能把交付日后移 1～2 天，或砍 scope。")

    return hints[0] if hints else None


def build_change_alert_prose_brief(fact_pack: dict[str, Any]) -> str:
    """
    BI 大战报风格的人话预警：定调 → 变更 → 影响 → 建议。
    不输出 risk 码、箭头列表或 change_alert_result（后者仅写日志）。
    """
    events = fact_pack.get("analyzed_events") or []
    if not events:
        return "今日需求表无待跟进变更。"

    has_red = any(
        (ev.get("schedule_axis") or {}).get("verdict") == "alert"
        or (ev.get("personnel_axis") or {}).get("verdict") == "alert"
        or any(p.get("symbol") == "🚨" for p in (ev.get("personnel_axis") or {}).get("people") or [])
        for ev in events
    )
    has_warn = fact_pack.get("should_push") and not has_red

    if has_red:
        ding = "🎯 【定调】：本次表更存在**需要 PM 立即对齐**的资源/排期风险，不建议按表直接开干。"
    elif has_warn:
        ding = "🎯 【定调】：本次表更整体可控，但有**字段或节奏**值得 PM 快速确认。"
    else:
        ding = "🎯 【定调】：本次表更已记录，当前快照下**暂无明确冲突**。"

    changes = [_event_change_sentence(ev) for ev in events]
    bian = "📋 【变更】：" + ("；".join(changes) if len(changes) > 1 else changes[0]) + "。"

    impacts: list[str] = []
    for ev in events:
        for sent in _event_impact_sentences(ev):
            if sent not in impacts:
                impacts.append(sent)

    sections = [ding, "", bian]
    if impacts:
        sections.extend(["", "⚠️ 【影响】：" + " ".join(impacts)])

    actions: list[str] = []
    for ev in events:
        hint = _event_action_hint(ev)
        if hint and hint not in actions:
            actions.append(hint)
    if not actions and impacts:
        actions.append("核对负责人与交付日是否填全；跨 Epic 时产品和开发表对齐一下。")
    if actions:
        sections.extend(["", "💡 【建议】：" + " ".join(actions)])

    return "\n".join(sections)


def _describe_schedule_natural(sched: dict[str, Any], sem: dict[str, Any]) -> str:
    sprint = sem.get("sprint") or ""
    start = sem.get("start_date") or ""
    due = sem.get("expected_due") or ""
    acc = sem.get("acceptable_due") or ""
    risks = set(str(x) for x in (sched.get("risks") or []))

    when = ""
    if start and due:
        when = f"{start} 开工、{due} 要交付" if start != due else f"{start} 当天开工且当天要交付"
    elif due:
        when = f"期待 {due} 交付"

    bits: list[str] = []
    if sprint:
        bits.append(f"在 **{sprint}**")
    if when:
        bits.append(when)
    if acc and acc != due:
        bits.append(f"最晚可拖到 {acc}")

    cautions: list[str] = []
    if "zero_buffer" in risks or (start and due and start == due):
        cautions.append("缓冲几乎为零")
    elif "buffer_very_short" in risks:
        cautions.append("缓冲只有约 1 天")
    if "mid_sprint_change" in risks or "mid_sprint_insert" in risks:
        cautions.append("属于 Sprint 中途插单")
    if "due_past" in risks:
        cautions.append("交付日已早于今天")

    head = "，".join(bits) if bits else "排期信息不完整"
    if cautions:
        return f"{head}（{'；'.join(cautions)}）"
    sym = sched.get("symbol") or ""
    if sym == "✅":
        return head + "，排期看起来正常"
    return head


def _humanize_person_reason(reason: str) -> str:
    s = reason or ""
    s = re.sub(r"计划交付已过 (\d+) 项仍无完成记录", r"有 \1 项过了计划日还没关", s)
    s = s.replace("同日 Start=Due 插单，节奏恶化", "今天又插了一条当天就要交的任务")
    s = s.replace("本周期节奏正常", "本周节奏正常")
    return s


def _describe_change_event_natural(item: dict[str, Any], *, idx: int, total: int) -> list[str]:
    sem = item.get("semantic") or {}
    name = sem.get("requirement") or item.get("label") or "未命名需求"
    ct = _CHANGE_TYPE_CN.get(str(item.get("change_type")), "变更")
    assignees = item.get("assignees") or []
    sched = item.get("schedule_axis") or {}
    pers = item.get("personnel_axis") or {}

    lines: list[str] = []
    prefix = f"{idx}️⃣ " if total > 1 else ""
    who = f"（负责人 **{', '.join(assignees)}**）" if assignees else ""
    lines.append(f"{prefix}**{ct}**「{name}」{who}：{_describe_schedule_natural(sched, sem)}。")

    if pers.get("status") == "skipped":
        msg = pers.get("message") or "暂无有效负责人，人员负荷评不了"
        lines.append(f"   → 人员：⚠️ {msg}")
    else:
        for p in pers.get("people") or []:
            sym = p.get("symbol") or ""
            reason = _humanize_person_reason(p.get("reason") or "")
            overdue = p.get("overdue_tasks") or []
            extra = ""
            if overdue:
                names = "、".join(
                    f"「{t.get('task')}」({t.get('due')})"
                    for t in overdue[:3]
                    if t.get("task")
                )
                if names:
                    extra = f"，手头还有 {names} 已过计划日未关"
            lines.append(
                f"   → **{p.get('person')}** {sym} {reason}{extra}"
            )

    proj = item.get("project_axis") or {}
    proj_risks = proj.get("risks") or []
    if "mirror_row_missing" in proj_risks:
        lines.append("   → 数据：这条变更可能还没进镜像库（刚改表或同步 lag），结论基于当前快照。")
    elif "cross_view_mismatch" in proj_risks:
        lines.append("   → 数据：⚠️ 产品表和开发表对不上，建议核对 Epic / 负责人。")

    return lines


def format_change_alert_narrative_markdown(fact_pack: dict[str, Any]) -> str:
    """飞书推送：BI 大战报风格人话（定调/变更/影响/建议）。"""
    return build_change_alert_prose_brief(fact_pack)


def _build_change_alert_llm_context(fact_pack: dict[str, Any]) -> dict[str, Any]:
    """压缩 fact_pack 供 LLM  narrate，避免 raw JSON 诱导模型堆字段。"""
    events_out: list[dict[str, Any]] = []
    for ev in fact_pack.get("analyzed_events") or []:
        events_out.append(
            {
                "change": _event_change_sentence(ev),
                "impacts": _event_impact_sentences(ev),
                "action_hint": _event_action_hint(ev),
                "should_push": ev.get("decision_push"),
            }
        )
    return {
        "today": fact_pack.get("today"),
        "current_sprint": fact_pack.get("current_sprint"),
        "change_alert_result": fact_pack.get("change_alert_result"),
        "should_push": fact_pack.get("should_push"),
        "events": events_out,
    }


def _change_alert_use_technical_markdown() -> bool:
    return os.environ.get("PMO_CHANGE_ALERT_TECHNICAL", "").strip().lower() in ("1", "true", "yes")


def _change_alert_llm_narrate_enabled() -> bool:
    """默认用大模型写推送正文；设 PMO_CHANGE_ALERT_LLM_NARRATE=0 关闭。"""
    v = os.environ.get("PMO_CHANGE_ALERT_LLM_NARRATE", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _llm_polish_change_alert_narrative(fact_pack: dict[str, Any], draft: str) -> str | None:
    """用 LLM 将分析结论写成 BI 大战报风格人话（禁止改事实；失败返回 None）。"""
    try:
        import asyncio

        from l3_node.__main__ import _create_engine_standalone

        engine = _create_engine_standalone()
        brief = _build_change_alert_llm_context(fact_pack)
        brief_json = json.dumps(brief, ensure_ascii=False)[:8000]
        system = (
            "你是 PMO 变更影响分析编辑，文风对标 BI 增长大战报：先定调、再陈述事实、再讲影响、最后给建议。\n"
            "宿主已完成三轴交叉分析，下方 analysis_brief 是唯一事实来源。\n\n"
            "输出要求：\n"
            "1. 用中文写 180～420 字，可直接发飞书群；禁止 Markdown 表格、禁止英文 risk 代码、禁止 JSON 原文。\n"
            "2. 结构固定四段（每段标题保留 emoji 前缀）：\n"
            "   🎯 【定调】：一句话说明 PM 要不要马上动作（紧急 / 待确认 / 暂无冲突）。\n"
            "   📋 【变更】：用人话说明改了什么、谁负责、落在哪个 Sprint。\n"
            "   ⚠️ 【影响】：只写有业务含义的交叉风险（延期叠加、零缓冲、无人 Owner 等）；无风险则整段省略。\n"
            "   💡 【建议】：最多 2 条可执行动作，像同事在群里 @PM，不要套话。\n"
            "3. 只能使用 brief 里的人名、需求名、日期与影响句，禁止编造。\n"
            "4. 不要输出 change_alert_result、不要免责声明、不要「→」字段列表。\n"
            "5. 只输出正文。"
        )
        user = (
            f"analysis_brief（JSON）：\n{brief_json}\n\n"
            f"规则模板草稿（可完全重写，勿丢事实）：\n{draft}"
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        async def _run() -> str:
            raw = await engine.generate_response(messages, tools=None, temperature=0.35, max_tokens=900)
            if isinstance(raw, dict):
                text = str(raw.get("content") or "").strip()
                if not text and raw.get("reasoning_content"):
                    text = str(raw.get("reasoning_content") or "").strip()[:600]
                return text
            return str(raw or "").strip()

        text = asyncio.run(_run())
        if len(text) < 60 or "【定调】" not in text:
            return None
        return text
    except Exception as e:
        logger.warning("[PMO change_alert] LLM narrate skipped: %s", e)
        return None


def resolve_change_alert_push_markdown(fact_pack: dict[str, Any]) -> tuple[str, str]:
    """
    推送正文：默认 LLM 读 fact_pack 写预警；失败则规则人话；PMO_CHANGE_ALERT_TECHNICAL=1 走旧表格。
    """
    if _change_alert_use_technical_markdown():
        return format_change_alert_markdown(fact_pack), "technical"
    draft = format_change_alert_narrative_markdown(fact_pack)
    if _change_alert_llm_narrate_enabled():
        llm_text = _llm_polish_change_alert_narrative(fact_pack, draft)
        if llm_text:
            return llm_text, "llm"
    return draft, "narrative"


def human_change_alert_title(fact_pack: dict[str, Any]) -> str:
    """飞书卡片标题（口语化）。"""
    events = fact_pack.get("analyzed_events") or []
    today_s = fact_pack.get("today") or _today_iso()
    if not events:
        return f"【PMO】表更通知 · {today_s}"
    first = events[0]
    label = (first.get("semantic") or {}).get("requirement") or first.get("label") or "需求变更"
    alerts = [
        p.get("person")
        for ev in events
        for p in (ev.get("personnel_axis") or {}).get("people") or []
        if p.get("symbol") == "🚨" and p.get("person")
    ]
    if alerts:
        who = alerts[0] if len(alerts) == 1 else f"{alerts[0]} 等"
        return f"【PMO】{label} · {who} 需关注 · {today_s}"
    if str(fact_pack.get("change_alert_result")) == "alert_sent":
        return f"【PMO】{label} · 请 PM 确认 · {today_s}"
    return f"【PMO】表更 · {label} · {today_s}"


def _resolve_lark_credentials(
    app_id: str | None,
    app_secret: str | None,
) -> tuple[str, str]:
    aid = (app_id or os.environ.get("LARK_APP_ID") or "").strip()
    sec = (app_secret or os.environ.get("LARK_APP_SECRET") or "").strip()
    if aid and sec:
        return aid, sec
    try:
        from pathlib import Path

        import yaml

        from l3_node.jachin_config import get_config_root

        cfg_path = get_config_root() / "mcps" / "atom_lark_notifier" / "config.yaml"
        if not cfg_path.is_file():
            cfg_path = Path(__file__).resolve().parents[2] / "config" / "mcps" / "atom_lark_notifier" / "config.yaml"
        if cfg_path.is_file():
            raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            aid = aid or str(raw.get("app_id") or "").strip()
            sec = sec or str(raw.get("app_secret") or "").strip()
    except Exception:
        pass
    return aid, sec


def push_change_alert(
    markdown: str,
    *,
    title: str,
    chat_id: str,
    monitor_chat_id: str | None = None,
    push_monitor: bool = True,
    dry_run: bool = False,
    app_id: str | None = None,
    app_secret: str | None = None,
) -> dict[str, Any]:
    from l3_node.tools.pmo_bitable_watch import send_watch_notification
    from l3_node.pmo_lark_env import pmo_change_alert_monitor_chat_id

    targets = [chat_id.strip()]
    monitor = (monitor_chat_id or pmo_change_alert_monitor_chat_id()).strip()
    if push_monitor and monitor and monitor not in targets:
        targets.append(monitor)

    pushes: list[dict[str, Any]] = []
    all_ok = True
    for cid in targets:
        r = send_watch_notification(
            markdown,
            chat_id=cid,
            title=title,
            app_id=app_id,
            app_secret=app_secret,
            dry_run=dry_run,
        )
        pushes.append({"chat_id": cid, **r})
        if str(r.get("status") or "").lower() not in ("success", "ok") and not r.get("dry_run"):
            all_ok = False

    return {
        "status": "ok" if all_ok or dry_run else "partial",
        "notified": all_ok,
        "pushes": pushes,
    }


def run_change_alert_pipeline(
    events: list[dict[str, Any]],
    *,
    table_id: str = "",
    view_id: str = "",
    session_started_at: str | None = None,
    chat_id: str | None = None,
    monitor_chat_id: str | None = None,
    push_monitor: bool = True,
    dry_run: bool = False,
    app_id: str | None = None,
    app_secret: str | None = None,
) -> dict[str, Any]:
    """bitable_watch 会话结束后调用：分析 + 有条件推送。"""
    if not events:
        return {
            "status": "ok",
            "change_alert_result": "all_clear",
            "notified": False,
            "message": "无变更事件",
        }

    fact = analyze_change_events(events)
    out: dict[str, Any] = {
        "status": "ok",
        "change_alert_result": fact.get("change_alert_result"),
        "fact_pack": fact,
        "table_id": table_id,
        "view_id": view_id,
        "session_started_at": session_started_at,
        "notified": False,
    }

    if not fact.get("should_push"):
        out["message"] = "分析完成，无需推送"
        logger.info("[PMO change_alert] all_clear events=%d", len(events))
        return out

    dedup_fp = _change_alert_dedup_fingerprint(events, fact)
    if not dry_run and _dedup_recently_pushed(dedup_fp):
        out["message"] = "与近期已推送的预警重复，跳过（dedup）"
        out["dedup_skipped"] = True
        out["dedup_fingerprint"] = dedup_fp
        logger.info("[PMO change_alert] dedup skip fp=%s events=%d", dedup_fp[:12], len(events))
        return out

    md, md_mode = resolve_change_alert_push_markdown(fact)
    title = human_change_alert_title(fact)

    cfg_chat = chat_id
    if not cfg_chat:
        try:
            from l3_node.tools.pmo_bitable_watch import _load_watch_config

            cfg_chat = str(_load_watch_config().get("chat_id") or "").strip()
        except Exception:
            cfg_chat = ""

    if not cfg_chat:
        out["status"] = "partial"
        out["message"] = "应推送但未配置 chat_id"
        out["markdown_preview"] = md[:500]
        return out

    push_out = push_change_alert(
        md,
        title=title,
        chat_id=cfg_chat,
        monitor_chat_id=monitor_chat_id,
        push_monitor=push_monitor,
        dry_run=dry_run,
        app_id=app_id,
        app_secret=app_secret,
    )
    out["push"] = push_out
    out["notified"] = bool(push_out.get("notified"))
    if out["notified"] and not dry_run:
        _dedup_mark_pushed(dedup_fp)
        out["dedup_fingerprint"] = dedup_fp
    out["markdown_mode"] = md_mode
    out["markdown_preview"] = md[:800]
    out["message"] = "变更预警已推送" if out["notified"] else "推送未确认成功"
    return out


def run_change_alert_analyze(
    *,
    events: list[dict[str, Any]] | None = None,
    webhook_payload: dict[str, Any] | None = None,
    view_id: str = "",
    table_id: str = "",
    push: bool = False,
    dry_run: bool = False,
    chat_id: str | None = None,
    monitor_chat_id: str | None = None,
    push_monitor: bool = True,
    app_id: str | None = None,
    app_secret: str | None = None,
) -> dict[str, Any]:
    """core:pmo_change_alert_analyze 工具入口。"""
    ev_list = list(events or [])
    if webhook_payload and isinstance(webhook_payload, dict):
        from l3_node.tools.pmo_bitable_watch import run_change_diff

        diff = run_change_diff(webhook_payload=webhook_payload)
        ev_list.extend(diff.get("events") or [])

    if not ev_list:
        return {
            "status": "ok",
            "change_alert_result": "all_clear",
            "message": "无 events 或 webhook_payload",
        }

    if push:
        return run_change_alert_pipeline(
            ev_list,
            table_id=table_id,
            view_id=view_id,
            chat_id=chat_id,
            monitor_chat_id=monitor_chat_id,
            push_monitor=push_monitor,
            dry_run=dry_run,
            app_id=app_id,
            app_secret=app_secret,
        )

    fact = analyze_change_events(ev_list)
    md, md_mode = resolve_change_alert_push_markdown(fact)
    return {
        "status": "ok",
        "change_alert_result": fact.get("change_alert_result"),
        "should_push": fact.get("should_push"),
        "fact_pack": fact,
        "markdown_mode": md_mode,
        "markdown_preview": md[:800],
        "title_preview": human_change_alert_title(fact),
    }
