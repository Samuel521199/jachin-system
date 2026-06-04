#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性：Worker B/C 宿主预取 → §1.4 宏观看板 → atom_lark_notifier 推送。"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

_env_root = (os.environ.get("JACHIN_APP_ROOT") or "").strip()
ROOT = Path(_env_root).resolve() if _env_root else Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from l3_node.pmo_epic_aggregate import epic_participants, group_children_by_epic
from l3_node.tools.pmo_personnel_query import person_keys_from_task


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


def _progress_bar(pct: int) -> str:
    pct = max(0, min(100, int(pct)))
    filled = round(pct / 10)
    return f"[{'▓' * filled}{'░' * (10 - filled)}] {pct}%"


def _is_terminal_task(t: dict[str, Any]) -> bool:
    st = str(t.get("status") or t.get("status_text") or "").strip()
    prog = str(t.get("progress") or "").strip()
    if t.get("actual_delivery_date_iso") or t.get("actual_delivery_date"):
        return True
    if "🟢" in st or "提前" in st:
        return True
    if any(x in prog for x in ("完成", "上线", "发布", "验收")):
        return True
    if any(x in st for x in ("完成", "上线")):
        return True
    if "提交测试" in prog or "测试通过" in prog:
        return True
    return False


def _epic_status_label(pct: int, epic: dict[str, Any], children: list[dict[str, Any]]) -> str:
    st = str(epic.get("status") or "").strip()
    prog = str(epic.get("progress") or "").strip()
    if pct >= 100:
        return st or "🟢 已完成"
    if pct <= 0 and not children:
        if "待" in st or "待" in prog:
            return "🟡 待开始"
        return st or prog or "🟡 待开始"
    if pct > 0:
        return st or prog or "🔵 进行中"
    return st or "🟡 待开始"


def _epic_completion(epic: dict[str, Any], children: list[dict[str, Any]]) -> int:
    if not children:
        if _is_terminal_task(epic):
            return 100
        prog = str(epic.get("progress") or "")
        if "完成" in prog:
            return 100
        if epic.get("start_date") and not epic.get("expected_delivery_date"):
            return 30
        return 0
    done = sum(1 for c in children if _is_terminal_task(c))
    return round(100 * done / len(children))


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


def _person_alert(person: str, tasks: list[dict[str, Any]], *, today: date) -> str:
    if not tasks:
        return "⚠️ 数据不足，无法节奏判定"
    total = len(tasks)
    done = sum(1 for t in tasks if _is_terminal_task(t))
    pct = round(100 * done / total) if total else 0
    sprint_start = None
    for t in tasks:
        sd = str(t.get("start_date_iso") or "")[:10]
        if sd:
            try:
                sprint_start = date.fromisoformat(sd)
                break
            except ValueError:
                pass
    if sprint_start is None:
        sprint_start = today
    days_elapsed = max(1, (today - sprint_start).days + 1)
    time_pct = min(100, round(100 * days_elapsed / 7))
    if done == total and total > 0:
        return f"🟡 偏闲（本周计划 {total}/完成 {done}，进度超前）"
    if pct < 30 and time_pct >= 50:
        return f"🚨 进度落后（时间已过约 {time_pct}%，完成 {pct}%）"
    overdue = sum(
        1
        for t in tasks
        if t.get("expected_delivery_date_iso")
        and str(t.get("expected_delivery_date_iso"))[:10] < today.isoformat()
        and not _is_terminal_task(t)
    )
    if overdue:
        return f"🚨 延期 {overdue} 项（本周计划 {total}/完成 {done}）"
    return f"✅ 正常（本周计划 {total}/完成 {done}）"


def _status_dot(pct: int, label: str) -> str:
    if "🟢" in label or "完成" in label or pct >= 100:
        return "🟢"
    if "🟡" in label or "待" in label:
        return "🟡"
    if "🔴" in label or "延期" in label:
        return "🔴"
    if pct > 0:
        return "🔵"
    return "🟡"


def _epic_core_summary(epic: dict[str, Any], children: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    vg = str(epic.get("version_goal") or "").strip()
    if vg and vg not in ("—", "null"):
        parts.append(vg[:40])
    seen: set[str] = set()
    for c in children:
        tn = str(c.get("task") or "").strip()
        if not tn or tn in seen or len(tn) <= 8:
            continue
        seen.add(tn)
        parts.append(tn[:36] + ("…" if len(tn) > 36 else ""))
        if len(parts) >= 3:
            break
    if not parts:
        prog = str(epic.get("progress") or epic.get("status") or "").strip()
        if prog:
            parts.append(prog[:50])
    return "；".join(parts[:3]) if parts else "⚠️ 子任务/摘要字段为空，待补全"


def _person_core_load_cell(tasks: list[dict[str, Any]], limit: int = 3) -> str:
    """核心负荷：按任务聚合，标注 P0 数量（对齐参考图 👥 表）。"""
    if not tasks:
        return "—"
    p0_n = sum(1 for t in tasks if str(t.get("priority") or "").upper() == "P0")
    bits: list[str] = []
    for t in tasks[:limit]:
        pr = str(t.get("priority") or "").upper()
        name = str(t.get("task") or "—").strip()
        if len(name) > 22:
            name = name[:20] + "…"
        bits.append(name)
    tail = f"（{p0_n}项P0）" if p0_n else f"（{len(tasks)}项）"
    mark = " 🔴" if p0_n else ""
    return "、".join(bits) + tail + mark


def _person_tasks_cell(tasks: list[dict[str, Any]], limit: int = 4) -> str:
    parts: list[str] = []
    for t in tasks[:limit]:
        pr = _dash(t.get("priority"))
        name = str(t.get("task") or "—").strip()
        if len(name) > 28:
            name = name[:26] + "…"
        st = str(t.get("status") or t.get("status_text") or t.get("progress") or "—").strip()
        if len(st) > 12:
            st = st[:10] + "…"
        tag = f"【{pr}】" if pr != "—" else ""
        parts.append(f"{tag}{name} · {st}")
    if len(tasks) > limit:
        parts.append(f"…等共 {len(tasks)} 项")
    return "；".join(parts) if parts else "—"


def build_macro_dashboard_markdown(worker_b: dict[str, Any], worker_c: dict[str, Any]) -> str:
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
    epics.sort(key=lambda x: str(x.get("priority") or "P9"))

    p0_count = sum(1 for e in epics if str(e.get("priority") or "").upper() == "P0")
    in_prog = sum(
        1
        for e in epics
        if _epic_completion(e, children_by_epic.get(str(e.get("epic_name") or ""), [])) not in (0, 100)
    )
    summary_line = (
        f"当前 Sprint：**{current_sprint}**"
        + (f"（{cs_date}）" if cs_date else "")
        + " | 目标版本：**K11**"
    )
    status_line = (
        f"本周期共 **{len(epics)}** 个大需求（P0 **{p0_count}** 项），"
        f"**{in_prog}** 项进行中；人员矩阵 **{worker_b.get('summary', {}).get('person_count', 0)}** 人。"
    )

    epic_rows: list[str] = []
    for epic in epics[:12]:
        name = str(epic.get("epic_name") or "—").strip()
        kids = children_by_epic.get(name, [])
        pct = _epic_completion(epic, kids)
        label = _epic_status_label(pct, epic, kids)
        dot = _status_dot(pct, label)
        epic_rows.append(
            "| "
            + " | ".join(
                [
                    name,
                    f"{dot} {_progress_bar(pct)} {label}",
                    _epic_core_summary(epic, kids),
                ]
            )
            + " |"
        )
    if not epic_rows:
        epic_rows.append("| （无数据） | 🟡 — | ⚠️ 本周无 Epic 采集结果 |")

    by_person = worker_b.get("by_person") or {}
    if not by_person:
        bp: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for t in worker_b.get("personnel_tasks") or []:
            if t.get("is_current_week") or t.get("sprint") == current_sprint:
                keys = person_keys_from_task(t) or ["(无)"]
                for pk in keys:
                    bp[pk].append(t)
        by_person = dict(bp)

    people_rows: list[str] = []
    for person in sorted(by_person.keys(), key=lambda x: (x == "", x)):
        tasks = [
            t
            for t in by_person[person]
            if t.get("is_current_week") or t.get("sprint") == current_sprint
        ]
        if not tasks:
            continue
        people_rows.append(
            "| "
            + " | ".join(
                [
                    person or "(无)",
                    _person_core_load_cell(tasks),
                    _person_alert(person, tasks, today=today),
                ]
            )
            + " |"
        )
    if not people_rows:
        people_rows.append("| （无数据） | - | ⚠️ 人员看板无本周任务 |")

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

    overall_dot = "🔵" if in_prog else "🟢"
    return "\n".join(
        [
            "## 🎯 Executive Summary",
            summary_line,
            "",
            f"**总体状况**：{overall_dot} 进行中 · {status_line}（产研多维表镜像汇总）",
            "",
            "**📊 关键 Epic 进度视图**",
            "| Epic 模块 | 状态与进度 | 核心摘要 |",
            "| --- | --- | --- |",
            *epic_rows,
            "",
            "**👥 核心资源负荷看板**",
            "🔴 P0 高优 · 🟠 P1/P2 · 🟢 其它",
            "",
            "| 人员 | 核心负荷 | 状态预警 |",
            "| --- | --- | --- |",
            *people_rows,
            "",
            f"📋 数据：Worker C（`core:pmo_sprint_epic_report`）+ Worker B（`core:pmo_personnel_report`）· Version Goal 填写率 {fill_pct}%（{version_note}）",
        ]
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chat-id", default="oc_437c98d11106295fb10751a5481ee465")
    ap.add_argument("--app-id", default=os.environ.get("LARK_APP_ID", ""))
    ap.add_argument("--app-secret", default=os.environ.get("LARK_APP_SECRET", ""))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out-md", default="")
    args = ap.parse_args()

    from l3_node.tools.pmo_db_tools import pmo_mirror_db_ready

    if not pmo_mirror_db_ready():
        print("pmo_raw_records 为空，请先 INIT", file=sys.stderr)
        return 1

    from l3_node.pmo_worker_result_backfill import (
        run_worker_b_host_bootstrap,
        run_worker_c_host_bootstrap,
    )

    worker_b = run_worker_b_host_bootstrap()
    worker_c = run_worker_c_host_bootstrap()
    worker_b["current_sprint"] = worker_b.get("current_sprint") or worker_c.get("current_sprint")

    md = build_macro_dashboard_markdown(worker_b, worker_c)
    title = f"【K11项目 · 宏观看板】{datetime.now():%Y-%m-%d}"

    if args.out_md:
        Path(args.out_md).write_text(md, encoding="utf-8")
        print(f"markdown -> {args.out_md}")

    if args.dry_run:
        print(md[:4000])
        return 0

    os.environ["LARK_USE_FEISHU"] = "0"
    os.environ.setdefault("PMO_PRIMARY_CHAT_ID", args.chat_id)

    from l3_node.channels.lark.client import LARK_API_BASE_DEFAULT
    from l3_node.channels.lark.im import send_interactive_card, send_markdown_card
    from l3_node.channels.lark.md_native_table_card import build_schema_v2_card_from_markdown
    from l3_node.primitives.mcp.mcp_tools.bi.tool_lark_notifier import (
        _load_atom_lark_notifier_config,
        send_lark_markdown,
    )

    api_base = LARK_API_BASE_DEFAULT
    v2 = build_schema_v2_card_from_markdown(md, title, max_tables=5, table_page_size=4)

    def _send_with_app(aid: str, sec: str) -> dict[str, Any]:
        if v2 is not None and aid and sec:
            return send_interactive_card(
                args.chat_id,
                v2,
                receive_id_type="chat_id",
                app_id=aid,
                app_secret=sec,
                api_base=api_base,
                http_timeout=60.0,
            )
        if aid and sec:
            return send_markdown_card(
                receive_id=args.chat_id,
                markdown_content=md,
                title=title,
                receive_id_type="chat_id",
                app_id=aid,
                app_secret=sec,
                api_base=api_base,
            )
        return send_lark_markdown("", md, title=title, chat_id=args.chat_id, native_table_card=True)

    attempts: list[tuple[str, str, str]] = []
    u_aid = (args.app_id or "").strip()
    u_sec = (args.app_secret or "").strip()
    if u_aid and u_sec:
        attempts.append(("用户指定 App", u_aid, u_sec))
    cfg = _load_atom_lark_notifier_config()
    c_aid = str(cfg.get("app_id") or "").strip()
    c_sec = str(cfg.get("app_secret") or "").strip()
    if c_aid and c_sec and (c_aid, c_sec) != (u_aid, u_sec):
        attempts.append(("config atom_lark_notifier", c_aid, c_sec))
    if not attempts:
        attempts.append(("环境/MCP 默认", "", ""))

    result: dict[str, Any] = {"status": "error", "error": "no attempt"}
    for label, aid, sec in attempts:
        result = _send_with_app(aid, sec)
        print(f"[push] {label} ({aid[:8]}…): {result.get('status')} {result.get('error') or result.get('msg')}")
        if str(result.get("status") or "").lower() == "success":
            break

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if str(result.get("status") or "").lower() != "success":
        print(
            "\n若 lark_code=230002：请把对应应用机器人拉入群 "
            f"{args.chat_id} 后再重试。",
            file=sys.stderr,
        )
    return 0 if str(result.get("status") or "").lower() == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
