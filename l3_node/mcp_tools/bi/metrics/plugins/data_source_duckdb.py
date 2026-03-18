"""
BI 指标 — DuckDB 数据源插件
"""
from __future__ import annotations

from typing import Any

from l3_node.mcp_tools.bi.metrics.plugins.base import DataSource
from l3_node.mcp_tools.bi.data_store import _get_conn


def _find_col(columns: list[str], *candidates: str) -> str | None:
    for cand in candidates:
        for col in columns:
            if cand.lower() in col.lower() or cand in col:
                return col
    return None


def _q(col: str) -> str:
    return f'"{col.replace(chr(34), chr(34)+chr(34))}"' if col else "1"


def _fetch_row(conn, table: str, col_names: list[str], qd: str, date_value: str | None):
    if date_value:
        row = conn.execute(
            f"SELECT * FROM {table} WHERE {qd} = '{date_value}' ORDER BY _ingested_date DESC LIMIT 1"
        ).fetchone()
    else:
        try:
            row = conn.execute(
                f"SELECT * FROM {table} ORDER BY {qd} DESC NULLS LAST LIMIT 1"
            ).fetchone()
        except Exception:
            row = conn.execute(
                f"SELECT * FROM {table} ORDER BY _ingested_date DESC LIMIT 1"
            ).fetchone()
    return dict(zip(col_names, row)) if row else None


class DuckDBDataSource(DataSource):
    """从 DuckDB 读取 BI 表数据"""

    def fetch(
        self,
        tables: list[str],
        date_col: str | None,
        date_value: str | None,
        compare_date: str | None,
        config: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        conn = _get_conn()
        try:
            all_tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
            for slug in tables:
                table = f"bi_{slug}" if not slug.startswith("bi_") else slug
                if table not in all_tables:
                    continue
                desc = conn.execute(f"DESCRIBE {table}").fetchall()
                col_names = [d[0] for d in desc]
                dc = date_col or _find_col(col_names, "日期", "date", "统计日期") or col_names[0]
                qd = _q(dc)

                current = _fetch_row(conn, table, col_names, qd, date_value)
                compare = _fetch_row(conn, table, col_names, qd, compare_date) if compare_date else None

                if current:
                    if compare_date is not None:
                        result[slug] = {"current": current, "compare": compare}
                    else:
                        result[slug] = {"current": current, "compare": None}
        finally:
            conn.close()
        return result
