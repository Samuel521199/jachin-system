#!/usr/bin/env python3
"""
Jachin L3 交叉火力 MCP 压测 — 本地 SQLite 靶场初始化。
生成 ~/.jachin/workspace/test_db.sqlite
"""
from __future__ import annotations

import os
import sqlite3


def main() -> None:
    db_path = os.path.join(os.path.expanduser("~"), ".jachin", "workspace", "test_db.sqlite")
    parent = os.path.dirname(db_path)
    os.makedirs(parent, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS inventory")
        cur.execute(
            """
            CREATE TABLE inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                price REAL NOT NULL,
                count INTEGER NOT NULL
            )
            """
        )
        rows = [
            ("苹果", 5.5, 50),
            ("香蕉", 3.2, 0),
            ("橙子", 6.0, 20),
            ("钛合金键盘", 1000.0, 5),
            ("人体工学鼠标", 299.0, 15),
        ]
        cur.executemany(
            "INSERT INTO inventory (name, price, count) VALUES (?, ?, ?)",
            rows,
        )
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM inventory")
        n = cur.fetchone()[0]
        print(f"OK: {db_path} rows={n}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
