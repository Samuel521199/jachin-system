"""
PMO 宏观看板（Work 总）：Worker B/C 宿主预取 → GFM 战报 → 飞书 native_table 卡片。

SSOT 案例：docs/architecture/PMO_WORK_ZONG_CASE_STUDY.md §3.5～§3.8、§9。
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from l3_node.pmo_epic_aggregate import (
    epic_completion_pct,
    epic_participants,
    group_children_by_epic,
    merge_personnel_progress_into_children,
)
_PMO_PUSH_WORKER_D_CACHE: dict[str, Any] | None = None


def set_pmo_worker_d_push_cache(seed: dict[str, Any] | None) -> None:
    """多 Agent 阶段三推送前注入 Worker D 宿主结果，避免重复调邮件 API。"""
    global _PMO_PUSH_WORKER_D_CACHE
    _PMO_PUSH_WORKER_D_CACHE = seed


def get_pmo_worker_d_push_cache() -> dict[str, Any] | None:
    return _PMO_PUSH_WORKER_D_CACHE


def clear_pmo_worker_d_push_cache() -> None:
    global _PMO_PUSH_WORKER_D_CACHE
    _PMO_PUSH_WORKER_D_CACHE = None


from l3_node.pmo_report_format import (
    PMO_DEMAND_TABLE_HEADERS_NATIVE,
    PMO_PERSONNEL_TASK_LINE_MAX_LEN,
    PMO_WAR_REPORT_VISUAL_FIG1,
    build_person_rhythm_alert,
    format_demand_table_gfm_row_native,
    format_personnel_matrix_tasks_cell,
    is_terminal_personnel_task,
    personnel_matrix_entries_sorted,
    polish_pmo_war_report_markdown,
    sort_epics_for_demand_table,
)
from l3_node.pmo_lark_env import (
    DEFAULT_PMO_MONITOR_CHAT_ID,
    DEFAULT_PMO_PRIMARY_CHAT_ID,
    ensure_pmo_dotenv_loaded,
    pmo_monitor_chat_id,
    pmo_primary_chat_id,
)
from l3_node.pmo_workflow_stage import (
    format_workflow_progress_bar,
    infer_epic_workflow_status,
)
from l3_node.tools.pmo_personnel_query import person_keys_from_task

# 向后兼容：旧代码引用模块级常量
DEFAULT_PRIMARY_CHAT_ID = DEFAULT_PMO_PRIMARY_CHAT_ID
DEFAULT_MONITOR_CHAT_ID = DEFAULT_PMO_MONITOR_CHAT_ID


def _dash(v: Any) -> str:
    if v is None or v == "" or v == "null":
        return "—"
    s = str(v).strip()
    return s if s else "—"


def _date_mmdd(iso: str | None) -> str:
    if not iso:
        return ""
    s = str(iso).strip()[:10]
    if len(s) >= 10 and s[4] == "-":
        return f"{s[5:7]}/{s[8:10]}"
    return s


def _sprint_span(sprint: str | None) -> str:
    m = re.match(r"(\d{4})/(\d{2})/(\d{2})-Sprint", str(sprint or ""))
    if not m:
        return _dash(sprint)
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    start = date(y, mo, d)
    end = date.fromordinal(start.toordinal() + 6)
    return f"{start:%Y/%m/%d}→{end:%Y/%m/%d}"


def _epic_status_label(pct: int, epic: dict[str, Any], children: list[dict[str, Any]]) -> str:
    preset = str(epic.get("workflow_status") or "").strip()
    if preset:
        return preset
    return infer_epic_workflow_status(epic, children, completion_pct=pct)


def _epic_time_span(epic: dict[str, Any], children: list[dict[str, Any]]) -> str:
    dates: list[str] = []
    for row in [epic, *children]:
        for k in ("start_date", "start_date_iso", "expected_delivery_date", "expected_delivery_date_iso"):
            v = row.get(k)
            if v:
                dates.append(str(v)[:10])
    if dates:
        dates.sort()
        return f"{_date_mmdd(dates[0])}→{_date_mmdd(dates[-1])}"
    return _sprint_span(epic.get("sprint"))


def _person_tasks_cell(tasks: list[dict[str, Any]]) -> str:
    return format_personnel_matrix_tasks_cell(
        tasks,
        compact_for_feishu=False,
        name_max_len=PMO_PERSONNEL_TASK_LINE_MAX_LEN,
        status_max_len=18,
    )


def build_macro_dashboard_markdown(
    worker_b: dict[str, Any],
    worker_c: dict[str, Any],
    *,
    release_mapping_section: str | None = None,
) -> str:
    """从 Worker B/C JSON 组装宏观看板 GFM（未 polish）。"""
    current_sprint = worker_b.get("current_sprint") or worker_c.get("current_sprint")
    cs_date = worker_b.get("current_sprint_date") or ""
    today = date.today()

    all_children = (
        (worker_c.get("dev_tasks") or [])
        + (worker_c.get("product_tasks") or [])
        + (worker_c.get("art_tasks") or [])
        + (worker_c.get("epic_children") or [])
    )
    children_by_epic = group_children_by_epic(all_children, current_sprint=str(current_sprint or ""))
    personnel_tasks = worker_b.get("personnel_tasks") or []

    epics = [
        e
        for e in (worker_c.get("epics") or [])
        if str(e.get("sprint") or "") == str(current_sprint or "")
    ]
    epics = sort_epics_for_demand_table(epics)

    p0_count = sum(1 for e in epics if str(e.get("priority") or "").upper() == "P0")
    in_prog = sum(
        1
        for e in epics
        if epic_completion_pct(e, children_by_epic.get(str(e.get("epic_name") or ""), []))
        not in (0, 100)
    )

    by_person = worker_b.get("by_person") or {}
    if not by_person:
        bp: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for t in worker_b.get("personnel_tasks") or []:
            if t.get("is_current_week") or t.get("sprint") == current_sprint:
                keys = person_keys_from_task(t) or ["(无)"]
                for pk in keys:
                    bp[pk].append(t)
        by_person = dict(bp)

    n_people = worker_b.get("summary", {}).get("person_count") or len(by_person)
    alert_red = 0
    for person, ptasks in by_person.items():
        week = [
            t
            for t in ptasks
            if t.get("is_current_week") or t.get("sprint") == current_sprint
        ]
        if week and "🚨" in build_person_rhythm_alert(week, today=today):
            alert_red += 1

    if p0_count and in_prog:
        overall_emoji = "🟢"
        overall_txt = f"**项目整体进展顺利**，**{p0_count}** 个 **P0** 大需求正在推进中"
    elif alert_red:
        overall_emoji = "🟡"
        overall_txt = f"**需关注**：**{alert_red}** 人节奏预警，请 PM 跟进排期"
    else:
        overall_emoji = "🟢"
        overall_txt = "**本周期需求按计划推进**"

    summary_line = (
        f"**当前 Sprint**：**{current_sprint}**"
        + (f"（`{cs_date}`）" if cs_date else "")
        + "  \n**目标版本**：**K11**"
    )
    status_line = (
        f"{overall_emoji} {overall_txt}。  \n"
        f"本周期 **{len(epics)}** 个大需求（**P0** **{p0_count}** 项 · 进行中 **{in_prog}** 项）"
        f" · 人员矩阵 **{n_people}** 人"
    )

    demand_rows: list[str] = []
    for epic in epics[:15]:
        name = str(epic.get("epic_name") or "—").strip()
        kids = list(children_by_epic.get(name, []))
        merge_personnel_progress_into_children(
            kids, name, personnel_tasks, str(current_sprint or "")
        )
        pct = epic_completion_pct(epic, kids)
        participants = epic_participants(epic, kids, personnel_tasks=personnel_tasks)
        demand_rows.append(
            format_demand_table_gfm_row_native(
                priority=epic.get("priority"),
                epic_name=name,
                time_span=_epic_time_span(epic, kids),
                participants=participants,
                progress_bar=format_workflow_progress_bar(pct),
                workflow_status=_epic_status_label(pct, epic, kids),
            )
        )
    if not demand_rows:
        demand_rows.append("| — | （无数据） | - | - | ⚠️ 原表字段全空，建议补充 |")

    people_entries = personnel_matrix_entries_sorted(
        by_person,
        current_sprint=str(current_sprint or ""),
        today=today,
    )
    people_rows: list[str] = []
    for person, tasks, alert in people_entries:
        people_rows.append(
            "| "
            + " | ".join(
                [
                    f"**{person or '(无)'}**",
                    _person_tasks_cell(tasks),
                    alert,
                ]
            )
            + " |"
        )
    if not people_rows:
        people_rows.append("| （无数据） | - | ⚠️ 人员看板无本周任务 |")

    if release_mapping_section and release_mapping_section.strip():
        version_block = release_mapping_section.strip()
    else:
        req_ctx = worker_b.get("requirement_context") or []
        filled = sum(
            1
            for r in req_ctx
            if r.get("version_goal") not in (None, "", "null", "—")
        )
        total_ctx = len(req_ctx)
        fill_pct = round(100 * filled / total_ctx) if total_ctx else 0
        version_note = (
            f"需求辅表 {total_ctx} 行，Version Goal 填写 {filled} 行（{fill_pct}%）"
            if total_ctx
            else "⚠️ 无 requirement_context"
        )
        version_block = "\n".join(
            [
                "### **📦 版本发布需求映射**",
                "| 数据源 | 记录数 | Version Goal 填写 | 填写率 | 说明 |",
                "| --- | --- | --- | --- | --- |",
                f"| vewpI8lyYw 辅表 | {total_ctx} | {filled} | {fill_pct}% | {version_note} |",
            ]
        )

    return "\n".join(
        [
            "## 🎯 **Executive Summary**",
            "",
            summary_line,
            "",
            f"**总体状况**  \n{status_line}",
            "",
            "---",
            "",
            "### **📊 需求进度全览**",
            "**优先级**：**【P0】高优** · **【P1】中优** · **【P2】其它**",
            "",
            "| " + " | ".join(PMO_DEMAND_TABLE_HEADERS_NATIVE) + " |",
            "| " + " | ".join("---" for _ in PMO_DEMAND_TABLE_HEADERS_NATIVE) + " |",
            *demand_rows,
            "",
            "---",
            "",
            "### **👥 人员任务矩阵**",
            "**节奏判定**：完成进度 × 计划周期（非任务条数排名）",
            "",
            "| 人员 | 负责需求（含优先级） | 状态预警 |",
            "| --- | --- | --- |",
            *people_rows,
            "",
            "---",
            "",
            version_block,
        ]
    )


def fetch_worker_bc_json() -> tuple[dict[str, Any], dict[str, Any]]:
    """B/C 宿主预取（与 FanOut 前 bootstrap 同源）。"""
    from l3_node.pmo_worker_result_backfill import (
        run_worker_b_host_bootstrap,
        run_worker_c_host_bootstrap,
    )

    os.environ.setdefault("PMO_WAR_REPORT_VISUAL", PMO_WAR_REPORT_VISUAL_FIG1)
    worker_b = run_worker_b_host_bootstrap()
    worker_c = run_worker_c_host_bootstrap()
    worker_b["current_sprint"] = worker_b.get("current_sprint") or worker_c.get("current_sprint")
    return worker_b, worker_c


def build_polished_macro_dashboard_markdown(
    worker_b: dict[str, Any] | None = None,
    worker_c: dict[str, Any] | None = None,
    *,
    release_mapping_section: str | None = None,
    worker_d: dict[str, Any] | None = None,
    app_id: str | None = None,
    app_secret: str | None = None,
    use_release_epic_mapping: bool = True,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    if worker_b is None or worker_c is None:
        worker_b, worker_c = fetch_worker_bc_json()
    release_section = release_mapping_section
    worker_d_seed = worker_d or get_pmo_worker_d_push_cache()
    if not release_section and worker_d_seed:
        release_section = str(worker_d_seed.get("markdown_section") or "").strip() or None
        if release_section:
            worker_b["_release_epic_mapping"] = worker_d_seed
    if use_release_epic_mapping and not release_section:
        from l3_node.tools.pmo_release_epic_mapping import run_release_epic_mapping

        rel = run_release_epic_mapping(app_id=app_id, app_secret=app_secret)
        if str(rel.get("status") or "").lower() == "ok":
            release_section = rel.get("markdown_section")
            worker_b["_release_epic_mapping"] = rel
        else:
            worker_b["_release_epic_mapping_error"] = rel.get("error") or rel
    raw = build_macro_dashboard_markdown(
        worker_b,
        worker_c,
        release_mapping_section=release_section,
    )
    return polish_pmo_war_report_markdown(raw), worker_b, worker_c


def _send_markdown_card_to_chat(
    *,
    chat_id: str,
    markdown: str,
    title: str,
    app_id: str,
    app_secret: str,
    project_root: Path | None = None,
) -> dict[str, Any]:
    os.environ["LARK_USE_FEISHU"] = "0"
    from l3_node.channels.lark.client import LARK_API_BASE_DEFAULT
    from l3_node.channels.lark.im import send_interactive_card, send_markdown_card
    from l3_node.channels.lark.md_native_table_card import build_schema_v2_card_from_markdown

    api_base = LARK_API_BASE_DEFAULT
    v2 = build_schema_v2_card_from_markdown(markdown, title, max_tables=5, table_page_size=4)
    if v2 is not None:
        result = send_interactive_card(
            chat_id,
            v2,
            receive_id_type="chat_id",
            app_id=app_id,
            app_secret=app_secret,
            api_base=api_base,
            http_timeout=60.0,
        )
    else:
        result = send_markdown_card(
            receive_id=chat_id,
            markdown_content=markdown,
            title=title,
            receive_id_type="chat_id",
            app_id=app_id,
            app_secret=app_secret,
            api_base=api_base,
        )
    if str(result.get("status") or "").lower() == "success":
        return result

    err = str(result.get("error") or "")
    if "out of the chat" not in err.lower() and result.get("lark_code") != 230002:
        return result

    root = project_root or Path(__file__).resolve().parents[2]
    from l3_node.jachin_config import load_mcp_config

    cfg = load_mcp_config("atom_lark_notifier", project_root=root)
    fb_id = (cfg.get("app_id") or "").strip()
    fb_sec = (cfg.get("app_secret") or "").strip()
    if not fb_id or not fb_sec or (fb_id == app_id and fb_sec == app_secret):
        return result

    if v2 is not None:
        return send_interactive_card(
            chat_id,
            v2,
            receive_id_type="chat_id",
            app_id=fb_id,
            app_secret=fb_sec,
            api_base=api_base,
            http_timeout=60.0,
        )
    return send_markdown_card(
        receive_id=chat_id,
        markdown_content=markdown,
        title=title,
        receive_id_type="chat_id",
        app_id=fb_id,
        app_secret=fb_sec,
        api_base=api_base,
    )


def _resolve_lark_credentials(
    app_id: str | None,
    app_secret: str | None,
    project_root: Path | None = None,
) -> tuple[str, str]:
    aid = (app_id or os.environ.get("LARK_APP_ID") or "").strip()
    sec = (app_secret or os.environ.get("LARK_APP_SECRET") or "").strip()
    if aid and sec:
        return aid, sec
    root = project_root or Path(__file__).resolve().parents[2]
    from l3_node.jachin_config import load_mcp_config

    cfg = load_mcp_config("atom_lark_notifier", project_root=root)
    return (cfg.get("app_id") or "").strip(), (cfg.get("app_secret") or "").strip()


def run_macro_dashboard_preview(
    *,
    title: str | None = None,
) -> dict[str, Any]:
    """仅组装并 polish Markdown，不推送飞书。"""
    from l3_node.tools.pmo_db_tools import pmo_mirror_db_ready

    if not pmo_mirror_db_ready():
        return {
            "status": "failed",
            "error": "pmo_raw_records 为空，请先 INIT（core:pmo_mirror_import）",
        }

    md, worker_b, worker_c = build_polished_macro_dashboard_markdown()
    epics = [
        e
        for e in (worker_c.get("epics") or [])
        if str(e.get("sprint") or "") == str(worker_b.get("current_sprint") or "")
    ]
    n_people = worker_b.get("summary", {}).get("person_count") or len(worker_b.get("by_person") or {})
    return {
        "status": "ok",
        "current_sprint": worker_b.get("current_sprint"),
        "epic_count": len(epics),
        "person_count": n_people,
        "title": title or f"【K11 · PMO 宏观看板】{datetime.now():%Y-%m-%d}",
        "markdown": md,
        "markdown_preview": md[:500],
    }


def run_macro_dashboard_push(
    *,
    chat_id: str | None = None,
    monitor_chat_id: str | None = None,
    push_monitor: bool = True,
    app_id: str | None = None,
    app_secret: str | None = None,
    dry_run: bool = False,
    title: str | None = None,
    project_root: Path | None = None,
    use_release_epic_mapping: bool = True,
    release_mapping_section: str | None = None,
    worker_d: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Work 总一键推送：B/C 预取 → 战报 Markdown → 飞书 native_table 卡片。
    默认主群 + 监控群双推（与 SKILL §1.3 一致）。
    """
    from l3_node.tools.pmo_db_tools import pmo_mirror_db_ready

    if not pmo_mirror_db_ready():
        return {
            "status": "failed",
            "error": "pmo_raw_records 为空，请先 INIT（core:pmo_mirror_import）",
        }

    ensure_pmo_dotenv_loaded()
    primary = (chat_id or pmo_primary_chat_id()).strip()
    monitor = (monitor_chat_id or pmo_monitor_chat_id()).strip()
    chat_targets = [primary]
    if push_monitor and monitor and monitor != primary:
        chat_targets.append(monitor)

    md, worker_b, worker_c = build_polished_macro_dashboard_markdown(
        use_release_epic_mapping=use_release_epic_mapping,
        release_mapping_section=release_mapping_section,
        worker_d=worker_d,
        app_id=app_id,
        app_secret=app_secret,
    )
    card_title = title or f"【K11 · PMO 宏观看板】{datetime.now():%Y-%m-%d}"

    epics = [
        e
        for e in (worker_c.get("epics") or [])
        if str(e.get("sprint") or "") == str(worker_b.get("current_sprint") or "")
    ]
    n_people = worker_b.get("summary", {}).get("person_count") or len(worker_b.get("by_person") or {})

    base: dict[str, Any] = {
        "status": "ok",
        "current_sprint": worker_b.get("current_sprint"),
        "epic_count": len(epics),
        "person_count": n_people,
        "title": card_title,
        "markdown_preview": md[:500],
        "chat_ids": chat_targets,
        "pushes": [],
    }

    if dry_run:
        base["dry_run"] = True
        base["markdown"] = md
        return base

    aid, sec = _resolve_lark_credentials(app_id, app_secret, project_root=project_root)
    if not aid or not sec:
        return {
            "status": "failed",
            "error": "缺少飞书 app_id/app_secret（参数或 config/mcps/atom_lark_notifier）",
            **{k: base[k] for k in ("current_sprint", "epic_count", "person_count")},
        }

    all_ok = True
    message_ids: list[str] = []
    for cid in chat_targets:
        result = _send_markdown_card_to_chat(
            chat_id=cid,
            markdown=md,
            title=card_title,
            app_id=aid,
            app_secret=sec,
            project_root=project_root,
        )
        push_rec = {
            "chat_id": cid,
            "status": str(result.get("status") or "failed"),
            "message_id": result.get("message_id"),
            "error": result.get("error"),
            "lark_code": result.get("lark_code"),
        }
        base["pushes"].append(push_rec)
        if push_rec["status"].lower() != "success":
            all_ok = False
        elif push_rec.get("message_id"):
            message_ids.append(str(push_rec["message_id"]))

    base["message_ids"] = message_ids
    base["message_id"] = message_ids[0] if message_ids else None
    base["status"] = "success" if all_ok else "partial" if message_ids else "failed"
    if not all_ok and message_ids:
        base["warning"] = "部分群推送失败，见 pushes[]"
    elif not all_ok:
        failed = [p for p in base["pushes"] if p.get("status") != "success"]
        base["error"] = failed[0].get("error") if failed else "推送失败"
    return base
