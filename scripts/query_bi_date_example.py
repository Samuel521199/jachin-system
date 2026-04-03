#!/usr/bin/env python3
"""
DuckDB 查询示例 — 指定日期查询 DAU、DNU、ARPU、次日（T+1）留存

Usage: python scripts/query_bi_date_example.py [YYYY-MM-DD]
  默认日期: 昨日（今天 -1 天）

DuckDB 查询逻辑说明：
- 库路径: ~/.jachin/client_volumes/bi_data/duckdb/bi.duckdb
- 表名: slug 加 bi_ 前缀，如 daily_ops_summary → bi_daily_ops_summary
- 日期列: 优先 "日期"/"date"/"统计日期"，无则用 _ingested_date
- 列名: 与抓取 CSV 表头一致（日活、当日新增用户、Arpu 等）
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from l3_node.primitives.mcp.mcp_tools.bi.data_store import _get_conn
from l3_node.primitives.mcp.mcp_tools.bi.report_refiner import _find_col


def query_date_metrics(date_str: str) -> dict:
    """查询指定日期的 DAU、DNU、ARPU、次日（T+1）留存"""
    conn = _get_conn()
    result = {"日期": date_str, "DAU": None, "DNU": None, "ARPU": None, "次日T1留存": None}

    try:
        tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
        if "bi_daily_ops_summary" not in tables:
            return result

        # 1. 从 daily_ops_summary 查 DAU、DNU、ARPU
        cols_all = [r[0] for r in conn.execute("DESCRIBE bi_daily_ops_summary").fetchall()]
        date_col = "日期"
        for c in cols_all:
            if c in ("日期", "date", "统计日期"):
                date_col = c
                break
        rel = conn.execute(
            f"SELECT * FROM bi_daily_ops_summary "
            f"WHERE \"{date_col}\" = '{date_str}' ORDER BY \"{date_col}\" DESC LIMIT 1"
        )
        col_names = [d[0] for d in rel.description]
        rows = rel.fetchall()

        if rows:
            row = dict(zip(col_names, rows[0]))
            cols = list(row.keys())
            # 使用 _find_col 动态匹配列名（兼容 日活（DAU）/日活(DAU) 等全角/半角括号）
            dau_col = _find_col(cols, "日活(DAU)", "日活（DAU）", "日活", "DAU")
            dnu_col = _find_col(cols, "当日新增用户(DNU)", "当日新增用户（DNU）", "新增用户", "DNU")
            arpu_col = _find_col(cols, "Arpu", "ARPU", "arpu")
            result["DAU"] = _safe_num(row.get(dau_col)) if dau_col else None
            result["DNU"] = _safe_num(row.get(dnu_col)) if dnu_col else None
            result["ARPU"] = _safe_num(row.get(arpu_col)) if arpu_col else None

            # 次日（T+1）留存：若 daily_ops_summary 有此列则取
            t1_col = _find_col(list(row.keys()), "次日", "T+1", "留存")
            if t1_col:
                result["次日T1留存"] = _safe_num(row.get(t1_col))
            elif "bi_stats_retention_user" in tables:
                ret_rel = conn.execute(
                    f"SELECT * FROM bi_stats_retention_user "
                    f"WHERE \"日期\" = '{date_str}' LIMIT 10"
                )
                ret_cols = [d[0] for d in ret_rel.description]
                for r in ret_rel.fetchall():
                    rec = dict(zip(ret_cols, r))
                    typ = str(rec.get("类型", rec.get("指标", "")))
                    if "次留" in typ or "T+1" in typ:
                        result["次日T1留存"] = _safe_num(rec.get("百分比", rec.get("留存率")))
                        break
    finally:
        conn.close()

    return result


def _safe_num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return round(float(v), 2) if isinstance(v, float) else v
    s = str(v).replace(",", "").replace("%", "").strip()
    try:
        return round(float(s), 2)
    except ValueError:
        return v


def main() -> int:
    date_str = sys.argv[1] if len(sys.argv) > 1 else (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"查询日期: {date_str}")
    print("-" * 40)
    r = query_date_metrics(date_str)
    for k, v in r.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
