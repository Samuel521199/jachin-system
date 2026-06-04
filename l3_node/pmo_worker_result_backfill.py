"""
PMO FanOut Worker 结果宿主回填：ReAct Observation 有数据但 Final Answer JSON 漏写时，确定性补全。

典型场景：Worker B 已执行 B-4 查到 personnel_tasks，Final Answer 却仅有空的 product_tasks/development_tasks。
Publisher / Auditor 只读 Final Answer 文本，须在此补全后再进入阶段二/三。
"""
from __future__ import annotations

import json
import logging
import re
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


def backfill_worker_b(raw: str) -> str:
    """若 personnel_tasks[] / recent_sprints[] 缺失或为空，宿主执行 B-S1 + B-4 回填。"""
    data = parse_worker_final_json(raw) or {}
    if not _personnel_tasks_empty(data):
        return raw

    s1_rows = _run_sql(sql_worker_b_s1())
    sprints = _sprint_names(s1_rows)
    if s1_rows:
        data["recent_sprints"] = s1_rows

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
        data["current_sprint"] = str(c1_rows[0].get("sprint") or "").strip() or None

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
    FanOut 前宿主确定性执行 B-S1 + B-4（SSOT SQL）。
    Worker B SubAgent 禁止重跑这两步，仅做 B-SUP + 出错自纠。
    """
    s1_rows = _run_sql(sql_worker_b_s1())
    sprints = _sprint_names(s1_rows)
    b4_rows = _run_sql(sql_worker_b_b4(sprints), max_rows=300)
    seed: dict[str, Any] = {
        "recent_sprints": s1_rows,
        "personnel_tasks": b4_rows,
        "sprint_names_for_in": sprints,
        "completed_sql_ids": ["B-S1", "B-4"],
        "_host_bootstrap": ["B-S1", "B-4"],
    }
    logger.info(
        "[PMO bootstrap] Worker B host: sprints=%s personnel_rows=%d",
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
        "recent_sprints": host_seed.get("recent_sprints") or [],
        "personnel_tasks": host_seed.get("personnel_tasks") or [],
        "completed_sql_ids": list(host_seed.get("completed_sql_ids") or ["B-S1", "B-4"]),
    }
    if host_seed.get("_host_bootstrap"):
        out["_host_bootstrap"] = list(host_seed["_host_bootstrap"])

    agent = parse_worker_final_json(agent_raw)
    incomplete = (agent_raw or "").strip() and not agent
    if agent:
        rc = agent.get("requirement_context")
        if isinstance(rc, list) and rc:
            out["requirement_context"] = rc
            if "B-SUP" not in out["completed_sql_ids"]:
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
