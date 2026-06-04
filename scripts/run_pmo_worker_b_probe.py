#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Worker B 人员任务采集探针（无 LLM · 秒级）

与 FanOut 阶段一 Worker B **宿主预取** 同路径：`run_worker_b_host_bootstrap()` →
内部 `core:pmo_personnel_report`（`recent_window`）。

用法（仓库根）::

  python scripts/run_pmo_worker_b_probe.py
  python scripts/run_pmo_worker_b_probe.py --out data/worker_b_out.json
  python scripts/run_pmo_worker_b_probe.py --brief   # 仅摘要，不打印明细表
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

_env_root = (os.environ.get("JACHIN_APP_ROOT") or "").strip()
ROOT = Path(_env_root).resolve() if _env_root else Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


from l3_node.tools.pmo_personnel_format import format_personnel_report_text


def _print_summary(payload: dict[str, Any]) -> None:
    summ = payload.get("summary") or {}
    print("\n=== 摘要 ===")
    print(f"  current_sprint      : {payload.get('current_sprint') or '—'}")
    print(f"  current_sprint_date : {payload.get('current_sprint_date') or '—'}")
    print(f"  personnel_row_count : {summ.get('personnel_row_count', len(payload.get('personnel_tasks') or []))}")
    print(f"  person_count        : {summ.get('person_count', len(payload.get('by_person') or {}))}")
    print(f"  current_week_tasks  : {summ.get('current_week_task_count', '—')}")
    print(f"  unassigned_count    : {summ.get('unassigned_count', len(payload.get('unassigned_tasks') or []))}")
    print(f"  cross_week_count    : {summ.get('cross_week_count', len(payload.get('cross_week_tasks') or []))}")
    rs = payload.get("recent_sprints") or []
    if rs:
        names = [str(r.get("sprint") or r) for r in rs[:5]]
        print(f"  recent_sprints      : {names}")
    print(f"  requirement_context : {len(payload.get('requirement_context') or [])} 行")
    print(f"  completed_sql_ids   : {payload.get('completed_sql_ids')}")
    print(f"  _host_bootstrap     : {payload.get('_host_bootstrap')}")


def _print_personnel_detail(payload: dict[str, Any]) -> None:
    text = payload.get("formatted_text") or format_personnel_report_text(payload)
    print("\n" + text)


def _print_brief(payload: dict[str, Any]) -> None:
    _print_summary(payload)
    by_person = payload.get("by_person") or {}
    if not by_person:
        return
    cs = payload.get("current_sprint")
    print(f"\n=== 人员一览（{len(by_person)} 人）===")
    for person in sorted(by_person.keys(), key=lambda x: (x == "", x)):
        tasks = by_person[person]
        current = [t for t in tasks if t.get("is_current_week") or t.get("sprint") == cs]
        print(f"  {person or '(无)'}: {len(current)} 条本周任务")


def _check_current_sprint(payload: dict[str, Any]) -> bool:
    cs = str(payload.get("current_sprint") or "")
    rs = payload.get("recent_sprints") or []
    if not cs or not rs:
        return True
    first = str(rs[0].get("sprint") if isinstance(rs[0], dict) else rs[0])
    meta = payload.get("_current_sprint_meta") or {}
    ok = cs != first or meta.get("resolved_by") == "sd_lte_today_max"
    print("\n=== current_sprint 验收（sd≤today）===")
    print(f"  recent_sprints[0] : {first}")
    print(f"  current_sprint    : {cs}")
    print(f"  resolved_by       : {meta.get('resolved_by', '—')}")
    print(f"  结果              : {'OK' if ok else 'WARN'}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="Worker B 人员任务探针（B-TOOL / 宿主预取）")
    ap.add_argument("--sprint", default="", help='单 Sprint，如 "2026/06/01-Sprint"')
    ap.add_argument("--out", default="", help="写出完整 JSON 路径")
    ap.add_argument("--merge", action="store_true", help="额外跑 merge_worker_b_result（空 Agent）")
    ap.add_argument("--json-stdout", action="store_true", help="向 stdout 打印完整 JSON")
    ap.add_argument("--brief", action="store_true", help="仅摘要 + 人员条数，不打印明细表")
    args = ap.parse_args()

    from l3_node.tools.pmo_db_tools import get_pmo_db_path, pmo_mirror_db_ready

    db_path = get_pmo_db_path()
    print(f"[worker-b-probe] DB: {db_path}")
    if not pmo_mirror_db_ready():
        print("[worker-b-probe] 错误: pmo_raw_records 为空，请先 INIT", file=sys.stderr)
        return 1

    sprint_s = (args.sprint or "").strip()
    if sprint_s:
        from l3_node.tools.pmo_personnel_query import run_personnel_report

        print("[worker-b-probe] 模式: 单 Sprint · core:pmo_personnel_report")
        rep = run_personnel_report(sprint=sprint_s)
        if str(rep.get("status") or "").lower() != "ok":
            print(json.dumps(rep, ensure_ascii=False, indent=2))
            return 1
        payload: dict[str, Any] = {**rep, "_host_bootstrap": ["B-TOOL"], "_mode": "single_sprint"}
    else:
        from l3_node.pmo_worker_result_backfill import run_worker_b_host_bootstrap

        print("[worker-b-probe] 模式: FanOut 宿主预取 · run_worker_b_host_bootstrap()")
        payload = run_worker_b_host_bootstrap()
        payload["_mode"] = "host_bootstrap"

    if args.merge:
        from l3_node.pmo_worker_result_backfill import merge_worker_b_result

        merged = merge_worker_b_result(payload, "")
        payload = json.loads(merged)
        payload["_merge_applied"] = True
        print("[worker-b-probe] 已应用 merge_worker_b_result(host, '')")

    if args.json_stdout:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.brief:
        _print_brief(payload)
    else:
        _print_summary(payload)
        _print_personnel_detail(payload)

    _check_current_sprint(payload)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[worker-b-probe] 已写入 {out_path.resolve()}")

    print("\n[worker-b-probe] 完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
