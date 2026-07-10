"""
BI 指标 — DuckDB 数据源插件
"""
from __future__ import annotations

from typing import Any

from l3_node.primitives.mcp.mcp_tools.bi.metrics.plugins.base import DataSource
from l3_node.primitives.mcp.mcp_tools.bi.data_store import _get_conn


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


def _fetch_row_prefer_scope(
    conn,
    table: str,
    col_names: list[str],
    qd: str,
    date_value: str | None,
    scope_col: str,
    scope_val: str,
):
    """同一业务日多行时优先取「当日总计」等平台汇总行（stats_game_daily）。"""
    qs = _q(scope_col)
    if date_value:
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE {qd} = '{date_value}' ORDER BY _ingested_date DESC"
        ).fetchall()
    else:
        try:
            rows = conn.execute(
                f"SELECT * FROM {table} ORDER BY {qd} DESC NULLS LAST LIMIT 64"
            ).fetchall()
        except Exception:
            rows = conn.execute(
                f"SELECT * FROM {table} ORDER BY _ingested_date DESC LIMIT 64"
            ).fetchall()
    if not rows:
        return None
    scope_labels = (
        scope_val,
        "当日总计",
        "日总计",
        "当日合计",
        "全部汇总",
        "全平台汇总",
        "平台汇总",
    )
    for label in scope_labels:
        if not label:
            continue
        for row in rows:
            d = dict(zip(col_names, row))
            if str(d.get(scope_col, "") or "").strip() == label:
                return d
    return dict(zip(col_names, rows[0]))


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
                dc = date_col or _find_col(col_names, "日期", "date", "统计日期", "业务日期") or col_names[0]
                qd = _q(dc)
                scope_col = _find_col(col_names, "统计范围", "游戏类型", "游戏名称", "汇总项目")
                use_scope = slug == "stats_game_daily" and scope_col

                if use_scope:
                    current = _fetch_row_prefer_scope(
                        conn, table, col_names, qd, date_value, scope_col, "当日总计"
                    )
                    compare = (
                        _fetch_row_prefer_scope(
                            conn, table, col_names, qd, compare_date, scope_col, "当日总计"
                        )
                        if compare_date
                        else None
                    )
                else:
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
