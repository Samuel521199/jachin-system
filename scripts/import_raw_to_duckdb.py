#!/usr/bin/env python3
"""
将 raw/ 目录下的 CSV 批量导入 DuckDB (D)

Usage: python scripts/import_raw_to_duckdb.py [--dry-run]
  --dry-run: 仅列出待导入文件，不执行导入

Input:  ~/.jachin/client_volumes/bi_data/raw/*.csv
Output: DuckDB ~/.jachin/client_volumes/bi_data/duckdb/bi.duckdb
"""
from __future__ import annotations

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from l3_node.mcp_tools.bi.paths import get_bi_raw_dir, ensure_bi_dirs
from l3_node.mcp_tools.bi.data_store import ingest_csv


def main() -> int:
    ensure_bi_dirs()
    raw_dir = get_bi_raw_dir()
    dry_run = "--dry-run" in sys.argv

    csv_files = sorted(raw_dir.glob("*.csv"))
    if not csv_files:
        print(f"No CSV files in {raw_dir}")
        return 0

    print(f"Found {len(csv_files)} CSV file(s) in {raw_dir}")
    if dry_run:
        for f in csv_files:
            print(f"  - {f.name} -> slug={f.stem}")
        return 0

    ok, fail = 0, 0
    for f in csv_files:
        slug = f.stem
        print(f"[{slug}] ... ", end="", flush=True)
        r = ingest_csv(str(f), slug)
        if r.get("status") == "success":
            rows = r.get("rows", 0)
            print(f"OK ({rows} rows)")
            ok += 1
        else:
            print(f"FAIL: {r.get('error', r)}")
            fail += 1

    print()
    print(f"Done: {ok} OK, {fail} FAIL")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
