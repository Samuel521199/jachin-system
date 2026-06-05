#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Worker D 发版 Epic 映射探针（无 LLM · 秒级）

与 FanOut 阶段一 Worker D **宿主预取** 同路径：`run_worker_d_host_bootstrap()` →
内部 `core:pmo_release_epic_mapping`。

用法（仓库根）::

  $env:PYTHONIOENCODING="utf-8"
  $env:LARK_APP_ID="cli_xxx"
  $env:LARK_APP_SECRET="xxx"

  python scripts/run_pmo_worker_d_probe.py
  python scripts/run_pmo_worker_d_probe.py --out data/worker_d_out.json
  python scripts/run_pmo_worker_d_probe.py --brief
  python scripts/run_pmo_worker_d_probe.py --tool-only
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


def _print_summary(payload: dict[str, Any]) -> None:
    print("\n=== Worker D 摘要 ===")
    print(f"  window_since          : {payload.get('window_since') or '—'}")
    print(f"  window_until          : {payload.get('window_until') or '—'}")
    print(f"  since_mail_subject    : {payload.get('since_mail_subject') or '—'}")
    print(f"  since_maintenance_date: {payload.get('since_maintenance_date') or '—'}")
    print(f"  completed_count       : {payload.get('completed_count', 0)}")
    print(f"  release_mails_found   : {payload.get('release_mails_found', '—')}")
    print(f"  mailbox               : {payload.get('mailbox') or '—'}")
    print(f"  completed_sql_ids     : {payload.get('completed_sql_ids')}")
    print(f"  _host_bootstrap       : {payload.get('_host_bootstrap')}")
    if payload.get("error_reason"):
        print(f"  error_reason          : {payload.get('error_reason')}")


def _print_epics(payload: dict[str, Any]) -> None:
    epics = payload.get("completed_epics") or []
    if not epics:
        print("\n=== 窗内已完成 Epic（0）===")
        return
    print(f"\n=== 窗内已完成 Epic（{len(epics)}）===")
    for i, row in enumerate(epics, 1):
        if not isinstance(row, dict):
            continue
        print(
            f"  {i:2}. [{row.get('priority') or '—'}] "
            f"{row.get('epic_name') or '—'} | "
            f"Sprint={row.get('sprint') or '—'} | "
            f"完成={row.get('completion_date') or '—'}"
        )


def _print_markdown_preview(payload: dict[str, Any]) -> None:
    md = str(payload.get("markdown_section") or "").strip()
    if not md:
        return
    print("\n=== markdown_section 预览（前 40 行）===")
    lines = md.splitlines()
    for line in lines[:40]:
        print(line)
    if len(lines) > 40:
        print(f"... ({len(lines) - 40} more lines)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Worker D 发版 Epic 映射探针")
    ap.add_argument("--out", default="", help="写出 JSON 到文件")
    ap.add_argument("--brief", action="store_true", help="仅摘要，不打印 Epic 明细与 Markdown")
    ap.add_argument("--tool-only", action="store_true", help="直接调 core:pmo_release_epic_mapping（不经 bootstrap 包装）")
    ap.add_argument("--app-id", default=os.environ.get("LARK_APP_ID", ""))
    ap.add_argument("--app-secret", default=os.environ.get("LARK_APP_SECRET", ""))
    ap.add_argument("--mailbox", default="")
    args = ap.parse_args()

    from l3_node.tools.pmo_db_tools import pmo_mirror_db_ready

    if not pmo_mirror_db_ready():
        print("[worker-d-probe] pmo_raw_records 为空，请先 INIT（core:pmo_mirror_import）", file=sys.stderr)
        return 2

    if args.tool_only:
        from l3_node.tools.pmo_release_epic_mapping import run_release_epic_mapping

        print("[worker-d-probe] 模式: 直连 Tool · core:pmo_release_epic_mapping")
        payload = run_release_epic_mapping(
            app_id=(args.app_id or None),
            app_secret=(args.app_secret or None),
            mailbox=(args.mailbox or None),
        )
    else:
        from l3_node.pmo_worker_result_backfill import run_worker_d_host_bootstrap

        print("[worker-d-probe] 模式: 宿主 bootstrap · run_worker_d_host_bootstrap()")
        payload = run_worker_d_host_bootstrap(
            app_id=(args.app_id or None),
            app_secret=(args.app_secret or None),
            mailbox=(args.mailbox or None),
        )

    _print_summary(payload)
    if not args.brief:
        _print_epics(payload)
        _print_markdown_preview(payload)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[worker-d-probe] JSON -> {out_path}")

    ok = bool(payload.get("markdown_section")) and (
        payload.get("completed_sql_ids") or payload.get("status") == "ok"
    )
    if payload.get("error_reason") and not payload.get("completed_epics"):
        print("\n[worker-d-probe] 部分成功：有 error_reason 但已生成占位 markdown_section")
        return 0
    return 0 if ok or args.tool_only else 1


if __name__ == "__main__":
    raise SystemExit(main())
