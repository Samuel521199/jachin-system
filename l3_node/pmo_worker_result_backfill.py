"""
PMO FanOut Worker 结果宿主回填：ReAct Observation 有数据但 Final Answer JSON 漏写时，确定性补全。

典型场景：Worker B 已执行 B-4 查到 personnel_tasks，Final Answer 却仅有空的 product_tasks/development_tasks。
Publisher / Auditor 只读 Final Answer 文本，须在此补全后再进入阶段二/三。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Any

from l3_node.pmo_multi_agent_queries import (
    sql_worker_b_b4,
    sql_worker_b_b_sup,
    sql_worker_b_s1,
    sql_worker_c_c1,
    sql_worker_c_c2,
)

logger = logging.getLogger(__name__)


def parse_worker_final_json(text: str) -> dict[str, Any] | None:
    """从 SubAgent Final Answer 提取 JSON 对象（容忍 markdown 围栏与前后缀）。"""
    raw = (text or "").strip()
    if not raw:
        return None
    for candidate in _json_candidates(raw):
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None


def _json_candidates(raw: str) -> list[str]:
    out: list[str] = [raw]
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    if fence:
        out.insert(0, fence.group(1).strip())
    start = raw.find("{")
    if start >= 0:
        depth, end = 0, start
        for i, ch in enumerate(raw[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end > start:
            out.insert(0, raw[start : end + 1])
    return out


def _run_sql(sql: str, *, max_rows: int = 500) -> list[dict[str, Any]]:
    from l3_node.tools.pmo_db_tools import run_db_query

    res = run_db_query(sql=sql, max_rows=max_rows)
    if str(res.get("status") or "").lower() != "ok":
        logger.warning(
            "[PMO backfill] SQL failed: %s — %s",
            res.get("error"),
            (res.get("message") or "")[:200],
        )
        return []
    rows = res.get("rows")
    return rows if isinstance(rows, list) else []


def _sprint_names(rows: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for r in rows:
        s = str(r.get("sprint") or "").strip()
        if s and s not in names:
            names.append(s)
    return names


def _personnel_tasks_empty(data: dict[str, Any]) -> bool:
    pt = data.get("personnel_tasks")
    if not isinstance(pt, list) or len(pt) == 0:
        return True
    # 全 null/空行视为无效
    for row in pt:
        if isinstance(row, dict) and any(v not in (None, "", "null") for v in row.values()):
            return False
    return True


def _epics_empty(data: dict[str, Any]) -> bool:
    ep = data.get("epics")
    if not isinstance(ep, list) or len(ep) == 0:
        return True
    for row in ep:
        if isinstance(row, dict) and str(row.get("epic_name") or row.get("Requirement") or "").strip():
            return False
    return True


def _apply_personnel_report_payload(data: dict[str, Any], rep: dict[str, Any]) -> None:
    if str(rep.get("status") or "").lower() != "ok":
        return
    for k in (
        "current_sprint",
        "current_sprint_date",
        "recent_sprints",
        "personnel_tasks",
        "requirement_context",
        "unassigned_tasks",
        "cross_week_tasks",
        "by_person",
        "_current_sprint_meta",
    ):
        if rep.get(k) is not None:
            data[k] = rep[k]
    summ = rep.get("summary")
    if isinstance(summ, dict):
        data["summary"] = summ
    ids = data.setdefault("completed_sql_ids", [])
    if isinstance(ids, list) and "B-TOOL" not in ids:
        ids.append("B-TOOL")
    names = _sprint_names(data.get("recent_sprints") or [])
    if names:
        data["sprint_names_for_in"] = names


def backfill_worker_b(raw: str) -> str:
    """若 personnel_tasks[] / recent_sprints[] 缺失或为空，宿主执行 B-TOOL 或 B-S1+B-4 回填。"""
    data = parse_worker_final_json(raw) or {}
    if not _personnel_tasks_empty(data):
        return raw

    try:
        from l3_node.tools.pmo_personnel_query import run_personnel_report_for_recent

        rep = run_personnel_report_for_recent()
        if str(rep.get("status") or "").lower() == "ok" and (rep.get("personnel_tasks") or []):
            _apply_personnel_report_payload(data, rep)
            meta = data.setdefault("_host_backfill", [])
            if isinstance(meta, list):
                meta.append("personnel_tasks:B-TOOL")
            logger.info(
                "[PMO backfill] Worker B: report tool injected %d personnel_tasks",
                len(data.get("personnel_tasks") or []),
            )
            return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("[PMO backfill] Worker B report tool failed: %s", e)

    s1_rows = _run_sql(sql_worker_b_s1())
    sprints = _sprint_names(s1_rows)
    if s1_rows:
        data["recent_sprints"] = s1_rows
        from l3_node.tools.pmo_personnel_query import resolve_current_sprint

        cs, cs_date, _ = resolve_current_sprint(s1_rows)
        if cs:
            data["current_sprint"] = cs
        if cs_date:
            data["current_sprint_date"] = cs_date
        data["sprint_names_for_in"] = sprints

    b4_rows = _run_sql(sql_worker_b_b4(sprints), max_rows=300)
    if b4_rows:
        data["personnel_tasks"] = b4_rows
        meta = data.setdefault("_host_backfill", [])
        if isinstance(meta, list):
            meta.append("personnel_tasks:B-S1+B-4")
        logger.info("[PMO backfill] Worker B: injected %d personnel_tasks rows", len(b4_rows))
        return json.dumps(data, ensure_ascii=False, indent=2)

    if s1_rows and _personnel_tasks_empty(data):
        logger.warning("[PMO backfill] Worker B: B-S1 ok but B-4 returned 0 rows")
    return raw if parse_worker_final_json(raw) else json.dumps(data, ensure_ascii=False, indent=2)


def _apply_report_payload(data: dict[str, Any], rep: dict[str, Any]) -> None:
    if str(rep.get("status") or "").lower() != "ok":
        return
    if rep.get("current_sprint"):
        data["current_sprint"] = rep.get("current_sprint")
    if rep.get("recent_sprints"):
        data["recent_sprints"] = rep.get("recent_sprints")
    if rep.get("epics"):
        data["epics"] = rep.get("epics")
    children = rep.get("epic_children")
    if not children:
        dev = rep.get("dev_tasks") or []
        product = rep.get("product_tasks") or []
        art = rep.get("art_tasks") or []
        children = dev + product + art if (product or art) else dev
    if children:
        data["epic_children"] = children
    if rep.get("dev_tasks"):
        data["dev_tasks"] = rep.get("dev_tasks")
    if rep.get("product_tasks"):
        data["product_tasks"] = rep.get("product_tasks")
    if rep.get("art_tasks"):
        data["art_tasks"] = rep.get("art_tasks")
    summ = rep.get("summary")
    if isinstance(summ, dict):
        data["summary"] = summ
    ids = data.setdefault("completed_sql_ids", [])
    if isinstance(ids, list) and "C-TOOL" not in ids:
        ids.append("C-TOOL")


def backfill_worker_c(raw: str) -> str:
    """若 epics[] 缺失或为空：优先 core:pmo_sprint_epic_report，再 C-1+C-2 SQL。"""
    data = parse_worker_final_json(raw) or {}
    if not _epics_empty(data):
        return raw

    try:
        from l3_node.tools.pmo_sprint_query import run_sprint_epic_report_for_recent

        rep = run_sprint_epic_report_for_recent()
        if str(rep.get("status") or "").lower() == "ok" and (rep.get("epics") or []):
            _apply_report_payload(data, rep)
            meta = data.setdefault("_host_backfill", [])
            if isinstance(meta, list):
                meta.append("epics:C-TOOL")
            logger.info(
                "[PMO backfill] Worker C: report tool injected %d epics",
                len(data.get("epics") or []),
            )
            return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("[PMO backfill] Worker C report tool failed: %s", e)

    c1_rows = _run_sql(sql_worker_c_c1())
    sprints = _sprint_names(c1_rows)
    if c1_rows:
        data["recent_sprints"] = c1_rows
        from l3_node.tools.pmo_personnel_query import resolve_current_sprint

        cs, cs_date, _ = resolve_current_sprint(c1_rows)
        data["current_sprint"] = cs or str(c1_rows[0].get("sprint") or "").strip() or None
        if cs_date:
            data["current_sprint_date"] = cs_date

    c2_rows = _run_sql(sql_worker_c_c2(sprints), max_rows=200)
    if c2_rows:
        data["epics"] = c2_rows
        meta = data.setdefault("_host_backfill", [])
        if isinstance(meta, list):
            meta.append("epics:C-1+C-2")
        logger.info("[PMO backfill] Worker C: injected %d epics rows (SQL fallback)", len(c2_rows))
        return json.dumps(data, ensure_ascii=False, indent=2)

    return raw if parse_worker_final_json(raw) else json.dumps(data, ensure_ascii=False, indent=2)


def run_worker_c_host_bootstrap() -> dict[str, Any]:
    """FanOut 前宿主确定性执行 recent_window report（等同步骤 0 · C-TOOL）。"""
    from l3_node.tools.pmo_sprint_query import run_sprint_epic_report_for_recent

    rep = run_sprint_epic_report_for_recent()
    seed: dict[str, Any] = {
        "current_sprint": rep.get("current_sprint"),
        "recent_sprints": rep.get("recent_sprints") or [],
        "epics": rep.get("epics") or [],
        "dev_tasks": rep.get("dev_tasks") or [],
        "product_tasks": rep.get("product_tasks") or [],
        "art_tasks": rep.get("art_tasks") or [],
        "epic_children": rep.get("epic_children") or [],
        "completed_sql_ids": ["C-TOOL"],
        "_host_bootstrap": ["C-TOOL"],
        "summary": rep.get("summary"),
    }
    if str(rep.get("status") or "").lower() != "ok":
        seed["report_error"] = rep.get("message") or rep.get("status")
    logger.info(
        "[PMO bootstrap] Worker C host: epics=%d dev=%d product=%d art=%d current_sprint=%s",
        len(seed.get("epics") or []),
        len(seed.get("dev_tasks") or []),
        len(seed.get("product_tasks") or []),
        len(seed.get("art_tasks") or []),
        seed.get("current_sprint"),
    )
    return seed


def merge_worker_c_result(host_seed: dict[str, Any], agent_raw: str) -> str:
    """合并宿主 C-TOOL 预取与 SubAgent Final Answer（兜底 SQL 仅补缺）。"""
    out: dict[str, Any] = {
        "current_sprint": host_seed.get("current_sprint"),
        "recent_sprints": host_seed.get("recent_sprints") or [],
        "epics": host_seed.get("epics") or [],
        "dev_tasks": host_seed.get("dev_tasks") or [],
        "product_tasks": host_seed.get("product_tasks") or [],
        "art_tasks": host_seed.get("art_tasks") or [],
        "epic_children": host_seed.get("epic_children") or [],
        "completed_sql_ids": list(host_seed.get("completed_sql_ids") or ["C-TOOL"]),
    }
    if host_seed.get("_host_bootstrap"):
        out["_host_bootstrap"] = list(host_seed["_host_bootstrap"])
    if host_seed.get("summary"):
        out["summary"] = host_seed["summary"]

    agent = parse_worker_final_json(agent_raw)
    if agent:
        if isinstance(agent.get("epic_children"), list) and agent["epic_children"]:
            out["epic_children"] = agent["epic_children"]
        for k in ("cross_check_notes", "field_empty", "column_missing_in_view"):
            if k in agent:
                out[k] = agent[k]

    if _epics_empty(out):
        return backfill_worker_c(json.dumps(out, ensure_ascii=False))

    return json.dumps(out, ensure_ascii=False, indent=2)


def backfill_worker_outputs(worker_b: str, worker_c: str) -> tuple[str, str]:
    """阶段一 FanOut 结束后、Auditor/Publisher 之前调用。"""
    return backfill_worker_b(worker_b), backfill_worker_c(worker_c)


def run_worker_b_host_bootstrap() -> dict[str, Any]:
    """
    FanOut 前宿主确定性执行 core:pmo_personnel_report（recent_window）。
    Worker B SubAgent 禁止重跑 B-S1/B-4；仅整理 Final Answer 或 B-SUP 兜底。
    """
    try:
        from l3_node.tools.pmo_personnel_query import run_personnel_report_for_recent

        rep = run_personnel_report_for_recent()
        if str(rep.get("status") or "").lower() == "ok":
            sprints = _sprint_names(rep.get("recent_sprints") or [])
            seed: dict[str, Any] = {
                "current_sprint": rep.get("current_sprint"),
                "current_sprint_date": rep.get("current_sprint_date"),
                "recent_sprints": rep.get("recent_sprints") or [],
                "personnel_tasks": rep.get("personnel_tasks") or [],
                "requirement_context": rep.get("requirement_context") or [],
                "unassigned_tasks": rep.get("unassigned_tasks") or [],
                "cross_week_tasks": rep.get("cross_week_tasks") or [],
                "by_person": rep.get("by_person") or {},
                "sprint_names_for_in": sprints,
                "completed_sql_ids": ["B-TOOL"],
                "_host_bootstrap": ["B-TOOL"],
                "summary": rep.get("summary"),
                "_current_sprint_meta": rep.get("_current_sprint_meta"),
            }
            if rep.get("sprint_window_empty"):
                seed["sprint_window_empty"] = True
            logger.info(
                "[PMO bootstrap] Worker B host (B-TOOL): current_sprint=%s sprints=%s personnel_rows=%d",
                seed.get("current_sprint"),
                sprints,
                len(seed.get("personnel_tasks") or []),
            )
            return seed
    except Exception as e:
        logger.warning("[PMO bootstrap] Worker B report tool failed, SQL fallback: %s", e)

    s1_rows = _run_sql(sql_worker_b_s1())
    sprints = _sprint_names(s1_rows)
    from l3_node.tools.pmo_personnel_query import resolve_current_sprint

    current_sprint, current_sprint_date, cs_meta = resolve_current_sprint(s1_rows)
    b4_rows = _run_sql(sql_worker_b_b4(sprints), max_rows=300)
    seed = {
        "recent_sprints": s1_rows,
        "current_sprint": current_sprint,
        "current_sprint_date": current_sprint_date,
        "personnel_tasks": b4_rows,
        "sprint_names_for_in": sprints,
        "completed_sql_ids": ["B-S1", "B-4"],
        "_host_bootstrap": ["B-S1", "B-4"],
        "_current_sprint_meta": cs_meta,
    }
    logger.info(
        "[PMO bootstrap] Worker B host (SQL fallback): current_sprint=%s sprints=%s personnel_rows=%d",
        current_sprint,
        sprints,
        len(b4_rows),
    )
    return seed


def merge_worker_b_result(host_seed: dict[str, Any], agent_raw: str) -> str:
    """
    合并宿主 B-S1/B-4 与 SubAgent Final Answer（B-SUP）。
    SubAgent 未交卷或缺 B-SUP 时，宿主兜底执行 B-SUP。
    """
    out: dict[str, Any] = {
        "current_sprint": host_seed.get("current_sprint"),
        "current_sprint_date": host_seed.get("current_sprint_date"),
        "recent_sprints": host_seed.get("recent_sprints") or [],
        "personnel_tasks": host_seed.get("personnel_tasks") or [],
        "completed_sql_ids": list(host_seed.get("completed_sql_ids") or ["B-S1", "B-4"]),
    }
    for k in (
        "requirement_context",
        "unassigned_tasks",
        "cross_week_tasks",
        "by_person",
        "summary",
        "_current_sprint_meta",
    ):
        if host_seed.get(k) is not None:
            out[k] = host_seed[k]
    if host_seed.get("_host_bootstrap"):
        out["_host_bootstrap"] = list(host_seed["_host_bootstrap"])

    agent = parse_worker_final_json(agent_raw)
    incomplete = (agent_raw or "").strip() and not agent
    if agent:
        agent_cs = agent.get("current_sprint")
        if agent_cs and agent_cs != out.get("current_sprint"):
            out["_current_sprint_mismatch"] = {"agent": agent_cs, "host": out.get("current_sprint")}
        rc = agent.get("requirement_context")
        if isinstance(rc, list) and rc:
            out["requirement_context"] = rc
            if "B-SUP" not in out["completed_sql_ids"] and "B-TOOL" not in out["completed_sql_ids"]:
                out["completed_sql_ids"].append("B-SUP")
        for k in ("cross_check_notes", "field_empty", "column_missing_in_view"):
            if k in agent:
                out[k] = agent[k]
        if isinstance(agent.get("personnel_tasks"), list) and _personnel_tasks_empty(out):
            pt = agent["personnel_tasks"]
            if isinstance(pt, list) and pt:
                out["personnel_tasks"] = pt
    elif incomplete:
        out["_agent_incomplete"] = True

    if not out.get("requirement_context"):
        sprints = host_seed.get("sprint_names_for_in") or _sprint_names(
            out.get("recent_sprints") or []
        )
        sup_rows = _run_sql(sql_worker_b_b_sup(sprints if isinstance(sprints, list) else []), max_rows=300)
        if sup_rows:
            out["requirement_context"] = sup_rows
            if "B-SUP" not in out["completed_sql_ids"]:
                out["completed_sql_ids"].append("B-SUP")
            meta = out.setdefault("_host_backfill", [])
            if isinstance(meta, list):
                meta.append("requirement_context:B-SUP")
            logger.info("[PMO merge] Worker B: host B-SUP fallback %d rows", len(sup_rows))

    if _personnel_tasks_empty(out):
        return backfill_worker_b(json.dumps(out, ensure_ascii=False))

    return json.dumps(out, ensure_ascii=False, indent=2)


def _iso_datetime(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    s = str(v).strip()
    return s if s else None


def _serialize_release_window(window: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(window, dict):
        return {}
    out = dict(window)
    for k in ("since", "until"):
        out[k] = _iso_datetime(out.get(k))
    since_mail = out.get("since_mail")
    if isinstance(since_mail, dict):
        sm = dict(since_mail)
        sm["internal_dt"] = _iso_datetime(sm.get("internal_dt"))
        out["since_mail"] = sm
    latest_mail = out.get("latest_mail")
    if isinstance(latest_mail, dict):
        lm = dict(latest_mail)
        lm["internal_dt"] = _iso_datetime(lm.get("internal_dt"))
        out["latest_mail"] = lm
    return out


def _placeholder_release_mapping_md(reason: str) -> str:
    return "\n".join(
        [
            "### **📦 版本发布需求映射**",
            f"**口径**：⚠️ {reason}",
            "",
            "| # | 大需求 (Epic) | Sprint | 完成日期 | 负责人 |",
            "| --- | --- | --- | --- | --- |",
            "| — | ⚠️ 数据不可用 | — | — | — |",
        ]
    )


def _worker_d_error_seed(
    reason: str,
    *,
    error_reason: str | None = None,
    error_class: str | None = None,
) -> dict[str, Any]:
    return {
        "window_since": None,
        "window_until": None,
        "since_mail_subject": None,
        "since_maintenance_date": None,
        "completed_epics": [],
        "completed_count": 0,
        "markdown_section": _placeholder_release_mapping_md(reason),
        "completed_sql_ids": [],
        "_host_bootstrap": [],
        "error_reason": error_reason or "worker_d_failed",
        "error_class": error_class or "config",
        "window": {"ok": False, "reason": error_reason or reason},
    }


def _worker_d_seed_from_tool_result(rep: dict[str, Any]) -> dict[str, Any]:
    status = str(rep.get("status") or "").lower()
    if status != "ok":
        err = str(rep.get("error") or "pmo_release_epic_mapping failed")
        return _worker_d_error_seed(
            err,
            error_reason=err,
            error_class=str(rep.get("error_class") or "transient"),
        )

    window = rep.get("window") if isinstance(rep.get("window"), dict) else {}
    since_mail = window.get("since_mail") if isinstance(window.get("since_mail"), dict) else {}
    since_dt = window.get("since")
    until_dt = window.get("until")
    maint = since_mail.get("maintenance_date") or window.get("since_maintenance_date")

    return {
        "window_since": _iso_datetime(since_dt),
        "window_until": _iso_datetime(until_dt),
        "since_mail_subject": since_mail.get("subject"),
        "since_maintenance_date": maint,
        "completed_epics": rep.get("completed_epics") or [],
        "completed_count": int(rep.get("completed_count") or len(rep.get("completed_epics") or [])),
        "markdown_section": rep.get("markdown_section") or _placeholder_release_mapping_md(
            "发版映射工具未返回 markdown_section"
        ),
        "mailbox": rep.get("mailbox"),
        "release_mails_found": rep.get("release_mails_found", 0),
        "completed_sql_ids": ["D-TOOL"],
        "_host_bootstrap": ["D-TOOL"],
        "window": _serialize_release_window(window),
        "degraded": bool(rep.get("degraded")),
        "mail_fetch_stats": rep.get("mail_fetch_stats"),
    }


def _worker_d_bootstrap_successful(seed: dict[str, Any]) -> bool:
    if "D-TOOL" not in (seed.get("completed_sql_ids") or []):
        return False
    if seed.get("error_reason"):
        return False
    return True


def _worker_d_mail_retry_count() -> int:
    raw = os.environ.get("PMO_WORKER_D_MAIL_RETRY_COUNT", "3").strip()
    try:
        return max(1, min(5, int(raw)))
    except ValueError:
        return 3


def _worker_d_mail_retry_delay_sec() -> float:
    raw = os.environ.get("PMO_WORKER_D_MAIL_RETRY_DELAY_SEC", "8").strip()
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 8.0


def run_worker_d_host_bootstrap_with_retry(
    *,
    app_id: str | None = None,
    app_secret: str | None = None,
    mailbox: str | None = None,
) -> dict[str, Any]:
    """Worker D 宿主预取：整轮失败时有限次重试（错开 A/B/C 后调用）。"""
    attempts = _worker_d_mail_retry_count()
    delay = _worker_d_mail_retry_delay_sec()
    last: dict[str, Any] | None = None
    for attempt in range(attempts):
        seed = run_worker_d_host_bootstrap(
            app_id=app_id,
            app_secret=app_secret,
            mailbox=mailbox,
        )
        last = seed
        if _worker_d_bootstrap_successful(seed):
            if attempt > 0:
                logger.info("[PMO bootstrap] Worker D succeeded on attempt %s", attempt + 1)
            return seed
        err = str(seed.get("error_reason") or "")
        logger.warning(
            "[PMO bootstrap] Worker D attempt %s/%s failed: %s",
            attempt + 1,
            attempts,
            err[:200],
        )
        if attempt + 1 < attempts:
            time.sleep(delay * (attempt + 1))
    return last or _worker_d_error_seed("worker_d_bootstrap_exhausted", error_class="transient")


def run_worker_d_host_bootstrap(
    *,
    app_id: str | None = None,
    app_secret: str | None = None,
    mailbox: str | None = None,
) -> dict[str, Any]:
    """
    FanOut 前宿主确定性执行 core:pmo_release_epic_mapping。
    Worker D SubAgent 禁止重跑邮件 API（除非宿主失败）。
    """
    try:
        from l3_node.tools.pmo_release_epic_mapping import run_release_epic_mapping

        rep = run_release_epic_mapping(
            app_id=app_id,
            app_secret=app_secret,
            mailbox=mailbox,
        )
        seed = _worker_d_seed_from_tool_result(rep)
        logger.info(
            "[PMO bootstrap] Worker D host (D-TOOL): completed_count=%s mails=%s",
            seed.get("completed_count"),
            seed.get("release_mails_found"),
        )
        return seed
    except Exception as e:
        logger.warning("[PMO bootstrap] Worker D report tool failed: %s", e)
        return _worker_d_error_seed(str(e), error_class="transient")


def _worker_d_empty(data: dict[str, Any]) -> bool:
    md = str(data.get("markdown_section") or "").strip()
    if md and "📦" in md:
        return False
    ep = data.get("completed_epics")
    if isinstance(ep, list) and ep:
        return False
    return not md


def backfill_worker_d(raw: str) -> str:
    """Worker D Final Answer 缺 markdown_section 时，再跑 D-TOOL 一次。"""
    data = parse_worker_final_json(raw) or {}
    if not _worker_d_empty(data):
        return raw if parse_worker_final_json(raw) else json.dumps(data, ensure_ascii=False, indent=2)
    seed = run_worker_d_host_bootstrap_with_retry()
    return json.dumps(seed, ensure_ascii=False, indent=2)


def merge_worker_d_result(host_seed: dict[str, Any], agent_raw: str) -> str:
    """合并宿主 D-TOOL 预取与 SubAgent Final Answer。"""
    out: dict[str, Any] = {
        "window_since": host_seed.get("window_since"),
        "window_until": host_seed.get("window_until"),
        "since_mail_subject": host_seed.get("since_mail_subject"),
        "since_maintenance_date": host_seed.get("since_maintenance_date"),
        "completed_epics": host_seed.get("completed_epics") or [],
        "completed_count": host_seed.get("completed_count", 0),
        "markdown_section": host_seed.get("markdown_section") or "",
        "mailbox": host_seed.get("mailbox"),
        "release_mails_found": host_seed.get("release_mails_found", 0),
        "completed_sql_ids": list(host_seed.get("completed_sql_ids") or ["D-TOOL"]),
        "window": host_seed.get("window") or {},
    }
    if host_seed.get("_host_bootstrap"):
        out["_host_bootstrap"] = list(host_seed["_host_bootstrap"])
    if host_seed.get("error_reason"):
        out["error_reason"] = host_seed["error_reason"]
    if host_seed.get("error_class"):
        out["error_class"] = host_seed["error_class"]

    agent = parse_worker_final_json(agent_raw)
    if agent:
        if isinstance(agent.get("markdown_section"), str) and agent["markdown_section"].strip():
            if not out.get("markdown_section") or "⚠️" in str(out.get("markdown_section")):
                out["markdown_section"] = agent["markdown_section"]
        if isinstance(agent.get("completed_epics"), list) and agent["completed_epics"]:
            if not out.get("completed_epics"):
                out["completed_epics"] = agent["completed_epics"]
                out["completed_count"] = len(agent["completed_epics"])
        for k in ("cross_check_notes", "field_empty", "error_reason"):
            if k in agent:
                out[k] = agent[k]

    if _worker_d_empty(out):
        return backfill_worker_d(json.dumps(out, ensure_ascii=False))

    return json.dumps(out, ensure_ascii=False, indent=2)
