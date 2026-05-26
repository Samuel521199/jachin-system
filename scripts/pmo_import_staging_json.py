#!/usr/bin/env python3
"""CLI：从 PMO staging JSON 批量导入 SQLite（与 core:pmo_import_json 同逻辑）。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from l3_node.tools.pmo_db_tools import (  # noqa: E402
    get_pmo_staging_dir,
    run_import_json,
    run_init_gap_report,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="PMO INIT：JSON/NDJSON → SQLite 批量导入")
    ap.add_argument("file_path", nargs="?", default="", help="staging JSON 路径")
    ap.add_argument("--operation", default="upsert", choices=("insert", "update", "upsert"))
    ap.add_argument("--gap-report", action="store_true", help="导入后输出 INIT 缺口报告")
    ap.add_argument("--manifest", default="", help="gap-report 用的 manifest 路径")
    args = ap.parse_args()

    if not args.file_path.strip():
        staging = get_pmo_staging_dir()
        print(f"[pmo_import] 请指定 file_path；staging 目录: {staging}", file=sys.stderr)
        return 2

    out = run_import_json(file_path=args.file_path, operation=args.operation)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if out.get("status") not in ("ok", "partial"):
        return 1

    if args.gap_report:
        gap = run_init_gap_report(manifest_path=args.manifest)
        print("\n--- gap report ---")
        print(json.dumps(gap, ensure_ascii=False, indent=2))
        if gap.get("status") != "ok":
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
