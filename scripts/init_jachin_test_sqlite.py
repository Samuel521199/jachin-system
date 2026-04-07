#!/usr/bin/env python3
"""
在 ~/.jachin/workspace/test_db.sqlite 创建压测用 inventory 表及 3 条样例行。
与 tools/mcp-official 中 official-sqlite-npx 的 --db-path 约定一致。
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def main() -> None:
    root = Path(os.environ.get("JACHIN_HOME", Path.home() / ".jachin")).expanduser().resolve()
    workspace = root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    db_path = workspace / "test_db.sqlite"

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item TEXT NOT NULL,
                quantity INTEGER NOT NULL
            )
            """
        )
        conn.execute("DELETE FROM inventory")
        conn.executemany(
            "INSERT INTO inventory (item, quantity) VALUES (?, ?)",
            [("苹果", 50), ("香蕉", 0), ("橙子", 20)],
        )
        conn.commit()
    finally:
        conn.close()

    print(f"OK: {db_path} (inventory table: 3 rows, UTF-8 item names)")


if __name__ == "__main__":
    main()
