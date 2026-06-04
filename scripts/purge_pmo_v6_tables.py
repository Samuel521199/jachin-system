#!/usr/bin/env python3
"""Remove v6 LLM-structured PMO tables from pmo_db.sqlite (keep v7 mirror)."""
from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path

# v6 structured extraction (LLM / db_write path) — not v7 raw mirror
V6_TABLES = (
    "pmo_personnel_task_progress",  # FK -> pmo_people, drop first
    "pmo_product_requirements",
    "pmo_dev_requirements",
    "pmo_design_requirements",
    "pmo_people",
    "pmo_extraction_log",
    "pmo_change_queue",
)

V7_KEEP = (
    "pmo_raw_records",
    "pmo_views_meta",
    "pmo_schema_meta",
    "pmo_sync_state",
)


def default_db_path() -> Path:
    home = Path(os.environ.get("JACHIN_HOME", Path.home() / ".jachin"))
    env = os.environ.get("JACHIN_PMO_DB_PATH", "").strip()
    if env:
        return Path(env)
    return home / "workspace" / "pmo_db.sqlite"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=default_db_path(),
        help="Path to pmo_db.sqlite",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print row counts, do not drop",
    )
    args = parser.parse_args()
    db = args.db.expanduser().resolve()
    if not db.is_file():
        print(f"ERROR: database not found: {db}")
        return 1

    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    existing = {r[0] for r in cur.fetchall()}

    print(f"Database: {db}")
    print("\n--- Before ---")
    for t in sorted(existing):
        try:
            n = cur.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0]
        except sqlite3.Error:
            n = "?"
        mark = "DROP" if t in V6_TABLES else ("KEEP" if t in V7_KEEP else "?")
        print(f"  {t}: {n} rows  [{mark}]")

    raw_n = 0
    if "pmo_raw_records" in existing:
        raw_n = cur.execute("SELECT COUNT(*) FROM pmo_raw_records").fetchone()[0]
    print(f"\nv7 pmo_raw_records: {raw_n} rows (must remain > 0 for analysis)")

    if args.dry_run:
        conn.close()
        print("\n(dry-run: no changes)")
        return 0

    if raw_n == 0:
        print("\nWARN: pmo_raw_records is empty; aborting to avoid empty DB.")
        conn.close()
        return 2

    cur.execute("PRAGMA foreign_keys=OFF")
    dropped = []
    for t in V6_TABLES:
        if t in existing:
            cur.execute(f"DROP TABLE IF EXISTS [{t}]")
            dropped.append(t)

    # Remove v6-oriented sync rows (optional cleanup)
    if "pmo_sync_state" in existing:
        placeholders = ",".join("?" for _ in V6_TABLES)
        cur.execute(
            f"DELETE FROM pmo_sync_state WHERE target_table IN ({placeholders})",
            V6_TABLES,
        )
        print(f"pmo_sync_state: deleted {cur.rowcount} v6 target rows")

    conn.commit()
    cur.execute("VACUUM")
    conn.close()

    print("\n--- Dropped tables ---")
    for t in dropped:
        print(f"  {t}")
    print("\nDone. v7 mirror (pmo_raw_records / pmo_views_meta) unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
