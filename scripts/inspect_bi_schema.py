#!/usr/bin/env python3
"""
查看 DuckDB 中 BI 表的列名（用于自定义 query_bi_metrics 列映射）

Usage: python scripts/inspect_bi_schema.py [slug]
  slug: 可选，指定表（如 daily_ops_summary），不指定则列出所有 bi_ 表
"""
from __future__ import annotations

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from l3_node.bi_data_store import _get_conn


def main() -> int:
    conn = _get_conn()
    try:
        tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
        bi_tables = [t for t in tables if t.startswith("bi_") and not t.startswith("bi__")]

        slug = sys.argv[1] if len(sys.argv) > 1 else None
        if slug:
            table = f"bi_{slug}" if not slug.startswith("bi_") else slug
            if table not in bi_tables:
                print(f"表 {table} 不存在。可用: {', '.join(bi_tables[:10])}...")
                return 1
            cols = conn.execute(f"DESCRIBE {table}").fetchall()
            print(f"=== {table} ===")
            for c in cols:
                print(f"  {c[0]}: {c[1]}")
        else:
            print("BI 表及列数:")
            for t in sorted(bi_tables):
                cols = conn.execute(f"DESCRIBE {t}").fetchall()
                print(f"  {t}: {len(cols)} 列")
            print("\n查看某表列名: python scripts/inspect_bi_schema.py daily_ops_summary")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
