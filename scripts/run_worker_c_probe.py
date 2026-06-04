#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Worker C 采集能力探针（无 LLM · 秒级）

与 FanOut 阶段一 Worker C **宿主预取** 同路径：`run_worker_c_host_bootstrap()` →
内部 `core:pmo_sprint_epic_report`（`recent_window`）。

用法（仓库根）::

  # 默认：近三周战报窗（等同多 Agent Worker C 预取）
  python scripts/run_worker_c_probe.py

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


def _print_children_group(title: str, tasks: list[dict[str, Any]]) -> None:
    if not tasks:
        print(f"\n=== {title}（0 条）===")
        return
    by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in tasks:
        pe = str(row.get("parent_epic") or "").strip() or "(未挂接)"
        by_parent[pe].append(row)
    print(f"\n=== {title}（按 parent_epic 分组，共 {len(tasks)} 条）===")
    for pe in sorted(by_parent.keys(), key=lambda x: (x == "(未挂接)", x)):
        group = by_parent[pe]
        print(f"\n  【{pe}】 {len(group)} 条")
        for t in group[:8]:
            print(
                f"    - {t.get('task') or t.get('Requirement')}  "
                f"P={t.get('priority')}  person={t.get('person')}  no={t.get('task_no')}"
            )
        if len(group) > 8:
            print(f"    … +{len(group) - 8} 条")


def _print_brief(payload: dict[str, Any]) -> None:
    summ = payload.get("summary") or {}
    if not summ and payload.get("epic_count") is not None:
        summ = payload
    print("\n=== 摘要 ===")
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

    epics = payload.get("epics") or []
    if epics:
        print("\n=== 大需求 Epic（前 20 条）===")
        for i, e in enumerate(epics[:20], 1):
            name = e.get("epic_name") or e.get("Requirement") or "?"
            print(f"  {i:2}. {name}  P={e.get('priority')}  no={e.get('task_no')}")
        if len(epics) > 20:
            print(f"  … 共 {len(epics)} 条")

    _print_children_group("开发子任务", payload.get("dev_tasks") or [])
    _print_children_group("产品子任务", payload.get("product_tasks") or [])
    _print_children_group("美术子任务", payload.get("art_tasks") or [])


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
        from l3_node.pmo_worker_result_backfill import run_worker_c_host_bootstrap

        print("[worker-c-probe] 模式: FanOut 宿主预取 · run_worker_c_host_bootstrap()")
        payload = run_worker_c_host_bootstrap()
        payload["_mode"] = "host_bootstrap"
        if payload.get("report_error"):
            print(f"[worker-c-probe] 警告: report_error={payload['report_error']}", file=sys.stderr)

    if args.json_stdout:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_brief(payload)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[worker-c-probe] 已写入 {out_path.resolve()}")

    if args.check_0511 or sprint_s == "2026/05/11-Sprint":
        if not _check_0511(payload):
            return 2

    print("\n[worker-c-probe] 完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
