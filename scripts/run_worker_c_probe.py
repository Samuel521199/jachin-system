#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Worker C 采集能力探针（无 LLM · 秒级）

与 FanOut 阶段一 Worker C **宿主预取** 同路径：`run_worker_c_host_bootstrap()` →
内部 `core:pmo_sprint_epic_report`（`recent_window`）。

用法（仓库根）::

  # 默认：近三周战报窗（按 Sprint 分开展示，不合并明细）
  python scripts/run_worker_c_probe.py

  # 近三周仍写出 FanOut 用的合并 JSON（--out 时额外含 sprints[] 分周明细）
  python scripts/run_worker_c_probe.py --out data/worker_c_recent.json

  # 指定单 Sprint（案例 5/11）
  python scripts/run_worker_c_probe.py --sprint "2026/05/11-Sprint"

  # 写出完整 JSON
  python scripts/run_worker_c_probe.py --sprint "2026/05/11-Sprint" --out data/worker_c_out.json

  # 对照案例验收数字（15 Epic / 26 开发子任务 / 7 个 Epic 有子任务）
  python scripts/run_worker_c_probe.py --sprint "2026/05/11-Sprint" --check-0511
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

_env_root = (os.environ.get("JACHIN_APP_ROOT") or "").strip()
ROOT = Path(_env_root).resolve() if _env_root else Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _epics_with_children(tasks: list[dict[str, Any]]) -> int:
    parents = {str(c.get("parent_epic") or "").strip() for c in tasks if c.get("parent_epic")}
    return len(parents)


def _fmt(val: Any) -> str:
    if val is None:
        return "—"
    if isinstance(val, str):
        s = val.strip()
        return s if s else "—"
    return str(val)


def _task_title(row: dict[str, Any]) -> str:
    return str(
        row.get("task")
        or row.get("epic_name")
        or row.get("Requirement")
        or "?"
    ).strip()


def _print_task_fields(
    row: dict[str, Any],
    *,
    indent: str = "      ",
    index: int | None = None,
    total: int | None = None,
) -> None:
    """打印单条任务/Epic 的业务字段（与 pmo_sprint_query 打包字段对齐）。"""
    dept = row.get("department")
    lines: list[tuple[str, Any]] = [
        ("priority 优先级", row.get("priority")),
        ("sprint 周期", row.get("sprint")),
        ("version_goal", row.get("version_goal")),
        ("expectation_purpose", row.get("expectation_purpose")),
        ("progress 过程状态", row.get("progress")),
        ("status 状态", row.get("status")),
        ("person 执行人", row.get("person")),
        ("start_date 开始", row.get("start_date")),
        ("review_date 审核", row.get("review_date")),
        ("acceptance_date 接受", row.get("acceptance_date")),
        ("expected_delivery_date 预计交货", row.get("expected_delivery_date")),
        ("actual_delivery_date 实际交货", row.get("actual_delivery_date")),
        ("task_no 任务编号", row.get("task_no")),
    ]
    if dept:
        lines.insert(0, ("department 部门", dept))
    if row.get("parent_epic"):
        lines.insert(0, ("parent_epic 所属大需求", row.get("parent_epic")))
    if index is not None and total is not None:
        print(f"{indent}[{index}/{total}] {_task_title(row)}")
    elif index is not None:
        print(f"{indent}[{index}] {_task_title(row)}")
    else:
        print(f"{indent}· {_task_title(row)}")
    for label, val in lines:
        print(f"{indent}  {label}: {_fmt(val)}")


def _print_children_group(
    title: str,
    tasks: list[dict[str, Any]],
    *,
    max_per_parent: int = 0,
) -> None:
    if not tasks:
        print(f"\n=== {title}（0 条）===")
        return
    by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in tasks:
        pe = str(row.get("parent_epic") or "").strip() or "(未挂接)"
        by_parent[pe].append(row)
    print(f"\n=== {title}（按 parent_epic 分组，共 {len(tasks)} 条）===")
    sep = "    " + ("─" * 64)
    for pe in sorted(by_parent.keys(), key=lambda x: (x == "(未挂接)", x)):
        group = by_parent[pe]
        total = len(group)
        print(f"\n  【{pe}】 子任务 {total} 条")
        show = group if max_per_parent <= 0 else group[:max_per_parent]
        for i, t in enumerate(show, 1):
            if i > 1:
                print(sep)
            _print_task_fields(t, indent="    ", index=i, total=len(show))
        if max_per_parent > 0 and total > max_per_parent:
            print(sep)
            print(
                f"    … 本 Epic 另有 {total - max_per_parent} 条未展示"
                f"（可用 --max-per-parent 0 显示全部）"
            )


def _print_epics(epics: list[dict[str, Any]], *, max_epics: int = 0) -> None:
    if not epics:
        return
    cap = len(epics) if max_epics <= 0 else min(max_epics, len(epics))
    sep = "  " + ("─" * 66)
    print(f"\n=== 大需求 Epic（共 {len(epics)} 条，展示 {cap} 条）===")
    for i, e in enumerate(epics[:cap], 1):
        if i > 1:
            print(sep)
        _print_task_fields(e, indent="    ", index=i, total=cap)
    if max_epics > 0 and len(epics) > max_epics:
        print(sep)
        print(f"  … 另有 {len(epics) - max_epics} 条 Epic 未展示")


def _payload_from_report(rep: dict[str, Any], sprint: str) -> dict[str, Any]:
    return {
        "sprint": sprint,
        "epics": rep.get("epics") or [],
        "dev_tasks": rep.get("dev_tasks") or [],
        "product_tasks": rep.get("product_tasks") or [],
        "art_tasks": rep.get("art_tasks") or [],
        "epic_children": rep.get("epic_children") or [],
        "summary": rep.get("summary") or {},
    }


def _rollup_summary(weeks: list[dict[str, Any]]) -> dict[str, Any]:
    dev = sum(len(w.get("dev_tasks") or []) for w in weeks)
    product = sum(len(w.get("product_tasks") or []) for w in weeks)
    art = sum(len(w.get("art_tasks") or []) for w in weeks)
    epics = sum(len(w.get("epics") or []) for w in weeks)
    return {
        "sprint_count": len(weeks),
        "epic_count": epics,
        "dev_task_count": dev,
        "product_task_count": product,
        "art_task_count": art,
        "child_task_count": dev + product + art,
    }


def _build_recent_weeks_payload(
    *,
    days: int = 21,
    limit: int = 3,
) -> dict[str, Any]:
    from l3_node.tools.pmo_sprint_query import list_recent_sprints, run_sprint_epic_report

    recent = list_recent_sprints(days=days, limit=limit)
    sprint_names = [str(r.get("sprint") or "").strip() for r in recent if r.get("sprint")]
    weeks: list[dict[str, Any]] = []
    for sp in sprint_names:
        rep = run_sprint_epic_report(sprint=sp)
        if str(rep.get("status") or "").lower() != "ok":
            weeks.append({"sprint": sp, "status": "error", "message": rep.get("message") or rep})
            continue
        weeks.append(_payload_from_report(rep, sp))

    current = sprint_names[0] if sprint_names else None
    return {
        "current_sprint": current,
        "recent_sprints": recent,
        "sprints": weeks,
        "summary_roll_up": _rollup_summary(weeks),
        "completed_sql_ids": ["C-TOOL"],
        "_host_bootstrap": ["C-TOOL"],
        "_mode": "recent_weeks_split",
    }


def _print_brief(
    payload: dict[str, Any],
    *,
    sprint_label: str | None = None,
    max_epics: int = 0,
    max_per_parent: int = 0,
) -> None:
    summ = payload.get("summary") or {}
    if not summ and payload.get("epic_count") is not None:
        summ = payload
    header = f"=== 摘要 · {sprint_label} ===" if sprint_label else "=== 摘要 ==="
    print(f"\n{header}")
    if sprint_label is None:
        print(f"  current_sprint   : {payload.get('current_sprint') or summ.get('current_sprint') or '—'}")
    print(f"  epic_count       : {summ.get('epic_count', len(payload.get('epics') or []))}")
    print(f"  dev_task_count   : {summ.get('dev_task_count', len(payload.get('dev_tasks') or []))}")
    print(f"  product_task_count : {summ.get('product_task_count', len(payload.get('product_tasks') or []))}")
    print(f"  art_task_count   : {summ.get('art_task_count', len(payload.get('art_tasks') or []))}")
    print(f"  epics_with_dev     : {summ.get('epics_with_dev', _epics_with_children(payload.get('dev_tasks') or []))}")
    print(f"  epics_with_product : {summ.get('epics_with_product', _epics_with_children(payload.get('product_tasks') or []))}")
    print(f"  epics_with_art     : {summ.get('epics_with_art', _epics_with_children(payload.get('art_tasks') or []))}")
    rs = payload.get("recent_sprints") or []
    if rs:
        names = [str(r.get("sprint") or r) for r in rs[:5]]
        print(f"  recent_sprints   : {names}")
    print(f"  completed_sql_ids : {payload.get('completed_sql_ids')}")
    print(f"  _host_bootstrap   : {payload.get('_host_bootstrap')}")

    _print_epics(payload.get("epics") or [], max_epics=max_epics)
    _print_children_group(
        "开发子任务", payload.get("dev_tasks") or [], max_per_parent=max_per_parent
    )
    _print_children_group(
        "产品子任务", payload.get("product_tasks") or [], max_per_parent=max_per_parent
    )
    _print_children_group(
        "美术子任务", payload.get("art_tasks") or [], max_per_parent=max_per_parent
    )


def _print_recent_weeks_split(
    payload: dict[str, Any],
    *,
    max_epics: int = 0,
    max_per_parent: int = 0,
) -> None:
    recent = payload.get("recent_sprints") or []
    if recent:
        names = [str(r.get("sprint") or r) for r in recent]
        print("\n=== 近三周 Sprint 列表（新→旧）===")
        for i, name in enumerate(names, 1):
            print(f"  {i}. {name}")
    print(f"  current_sprint : {payload.get('current_sprint') or '—'}")

    weeks = payload.get("sprints") or []
    for i, week in enumerate(weeks, 1):
        sp = week.get("sprint") or f"第{i}周"
        print("\n" + "=" * 72)
        print(f"  Sprint [{i}/{len(weeks)}]  {sp}")
        print("=" * 72)
        if week.get("status") == "error":
            print(f"  错误: {week.get('message')}")
            continue
        _print_brief(week, sprint_label=sp, max_epics=max_epics, max_per_parent=max_per_parent)

    roll = payload.get("summary_roll_up") or {}
    if roll and len(weeks) > 1:
        print("\n" + "-" * 72)
        print("=== 三周合计（仅计数，明细见上方各 Sprint）===")
        print(f"  sprint_count       : {roll.get('sprint_count', len(weeks))}")
        print(f"  epic_count         : {roll.get('epic_count')}")
        print(f"  dev_task_count     : {roll.get('dev_task_count')}")
        print(f"  product_task_count : {roll.get('product_task_count')}")
        print(f"  art_task_count     : {roll.get('art_task_count')}")


def _check_0511(payload: dict[str, Any]) -> bool:
    summ = payload.get("summary") or {}
    ec = int(summ.get("epic_count") or len(payload.get("epics") or []))
    dc = int(summ.get("dev_task_count") or len(payload.get("dev_tasks") or []))
    ew = int(summ.get("epics_with_dev") or _epics_with_children(payload.get("dev_tasks") or []))
    ok = ec == 15 and dc == 26 and ew == 7
    print("\n=== 案例 §6 验收（2026/05/11-Sprint · 仅开发）===")
    print(f"  Epic 15     : {'OK' if ec == 15 else f'FAIL ({ec})'}")
    print(f"  开发任务 26 : {'OK' if dc == 26 else f'FAIL ({dc})'}")
    print(f"  有子任务 Epic 7 : {'OK' if ew == 7 else f'FAIL ({ew})'}")
    pc = int(summ.get("product_task_count") or len(payload.get("product_tasks") or []))
    ac = int(summ.get("art_task_count") or len(payload.get("art_tasks") or []))
    print(f"  （参考）产品 {pc} 条 · 美术 {ac} 条")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="Worker C 采集探针（C-TOOL / 宿主预取）")
    ap.add_argument(
        "--sprint",
        default="",
        help='单 Sprint，如 "2026/05/11-Sprint"；省略则走近三周 host bootstrap',
    )
    ap.add_argument(
        "--out",
        default="",
        help="写出完整 JSON 路径",
    )
    ap.add_argument(
        "--check-0511",
        action="store_true",
        help="对照案例文档验收 15/26/7（须配合 --sprint 2026/05/11-Sprint）",
    )
    ap.add_argument(
        "--json-stdout",
        action="store_true",
        help="向 stdout 打印完整 JSON（默认只打印摘要）",
    )
    ap.add_argument(
        "--max-epics",
        type=int,
        default=0,
        help="每 Sprint 最多展示的 Epic 条数，0=全部",
    )
    ap.add_argument(
        "--max-per-parent",
        type=int,
        default=0,
        help="每个大需求下最多展示的子任务条数，0=全部",
    )
    args = ap.parse_args()

    from l3_node.tools.pmo_db_tools import get_pmo_db_path, pmo_mirror_db_ready

    db_path = get_pmo_db_path()
    print(f"[worker-c-probe] DB: {db_path}")
    if not pmo_mirror_db_ready():
        print("[worker-c-probe] 错误: pmo_raw_records 为空，请先 INIT（run_pmo_copilot_skill.py --init）", file=sys.stderr)
        return 1

    sprint_s = (args.sprint or "").strip()
    if sprint_s:
        from l3_node.tools.pmo_sprint_query import run_sprint_epic_report

        print(f"[worker-c-probe] 模式: 单 Sprint · core:pmo_sprint_epic_report (all 部门)")
        rep = run_sprint_epic_report(sprint=sprint_s)
        if str(rep.get("status") or "").lower() != "ok":
            print(json.dumps(rep, ensure_ascii=False, indent=2))
            return 1
        payload: dict[str, Any] = {
            "current_sprint": sprint_s,
            "recent_sprints": [{"sprint": sprint_s}],
            "epics": rep.get("epics") or [],
            "dev_tasks": rep.get("dev_tasks") or [],
            "product_tasks": rep.get("product_tasks") or [],
            "art_tasks": rep.get("art_tasks") or [],
            "epic_children": rep.get("epic_children") or [],
            "summary": rep.get("summary"),
            "completed_sql_ids": ["C-TOOL"],
            "_host_bootstrap": ["C-TOOL"],
            "_mode": "single_sprint",
        }
    else:
        print("[worker-c-probe] 模式: 近三周 · 按 Sprint 分别采集（不合并明细）")
        payload = _build_recent_weeks_payload()
        if not payload.get("sprints"):
            print("[worker-c-probe] 警告: 近三周窗内无 Sprint", file=sys.stderr)

    if args.json_stdout:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif payload.get("_mode") == "recent_weeks_split":
        _print_recent_weeks_split(
            payload,
            max_epics=args.max_epics,
            max_per_parent=args.max_per_parent,
        )
    else:
        _print_brief(
            payload,
            max_epics=args.max_epics,
            max_per_parent=args.max_per_parent,
        )

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[worker-c-probe] 已写入 {out_path.resolve()}")

    if args.check_0511 or sprint_s == "2026/05/11-Sprint":
        check_payload = payload
        if payload.get("_mode") == "recent_weeks_split":
            target = sprint_s or "2026/05/11-Sprint"
            check_payload = next(
                (w for w in (payload.get("sprints") or []) if w.get("sprint") == target),
                {},
            )
            if not check_payload:
                print(f"[worker-c-probe] 近三周窗内未找到 {target}，跳过 --check-0511", file=sys.stderr)
                check_payload = payload
        if sprint_s or args.check_0511:
            if not _check_0511(check_payload):
                return 2

    print("\n[worker-c-probe] 完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
