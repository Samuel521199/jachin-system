#!/usr/bin/env python3
"""
从 bi.duckdb 查询「今日」各渠道 DAU 并打印。

Usage:
  python scripts/query_bi_dau_by_channel.py [YYYY-MM-DD]
  python scripts/query_bi_dau_by_channel.py [YYYY-MM-DD] 渠道1 渠道2 ...
  python scripts/query_bi_dau_by_channel.py --channels "unknown,meta ads01,meta ads03,..."
  不传日期时使用「最新一批入库数据」对应的业务日期（stats_user_dau 表）。
  传入渠道名时只输出这些渠道的 DAU（未匹配到的显示 0）。

库路径: ~/.jachin/client_volumes/bi_data/duckdb/bi.duckdb
表: bi_stats_user_dau（日活统计，需 SPA 抓取时展开日期/渠道）
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from l3_node.mcp_tools.bi.data_store import _get_conn, _sanitize_table_name


def _find_col(columns: list[str], *candidates: str) -> str | None:
    for c in candidates:
        if not c:
            continue
        for col in columns:
            if col == c or (c.lower() in (col or "").lower()):
                return col
    return None


def _safe_float(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("%", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _normalize_channel(s: str) -> str:
    """渠道名规范化后比较：小写、空格/下划线统一为空格并去多余空白"""
    if not s:
        return ""
    return " ".join(str(s).lower().replace("_", " ").split())


def query_dau_by_channel(date_str: str | None = None) -> tuple[list[dict], str | None]:
    """
    查询指定日期（或最新数据）各渠道 DAU。
    返回 ( [{"渠道": str, "数量": int}, ...], 实际使用的日期或 None )，按数量降序。
    """
    table = _sanitize_table_name("stats_user_dau")
    conn = _get_conn()
    try:
        tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
        if table not in tables:
            print(f"表 {table} 不存在，请先执行 SPA 抓取（日活统计）并 ingest。")
            return [], None

        cols = [r[0] for r in conn.execute(f"DESCRIBE {table}").fetchall()]
        date_col = _find_col(cols, "日期", "date", "统计日期") or ("_ingested_date" if "_ingested_date" in cols else None)
        ch_col = _find_col(cols, "渠道", "channel", "渠道来源")
        dau_col = _find_col(cols, "日活（DAU）", "日活(DAU)", "日活", "DAU", "数量")

        if not ch_col or not dau_col:
            print(f"表 {table} 列: {cols}")
            print("未找到渠道列或日活列，请检查抓取 CSV 表头。")
            return [], None

        # 取全表最近一批数据（按 _ingested_date 或日期列 DESC）
        order_col = date_col if date_col else "_ingested_date"
        sql = f'SELECT * FROM {table} ORDER BY "{order_col}" DESC LIMIT 500'
        try:
            rel = conn.execute(sql)
        except Exception:
            sql = f"SELECT * FROM {table} ORDER BY _ingested_date DESC LIMIT 500"
            rel = conn.execute(sql)

        col_names = [d[0] for d in rel.description]
        rows = [dict(zip(col_names, r)) for r in rel.fetchall()]

        if not rows:
            return [], None

        # 若指定了日期则只保留该日期的行；否则保留「最新日期」对应的行（首行日期 + 空日期子行）
        target_date = date_str[:10] if date_str else None
        if not target_date and date_col:
            first_val = rows[0].get(date_col)
            if first_val is not None and str(first_val).strip():
                target_date = str(first_val).strip()[:10]

        out = []
        for r in rows:
            if target_date and date_col:
                d = str(r.get(date_col) or "").strip()[:10]
                if d and d != target_date:
                    continue
            ch = str(r.get(ch_col) or "").strip()
            if not ch:
                continue
            if ch in ("全部汇总", "ALL", "> ALL"):
                continue
            cnt = int(_safe_float(r.get(dau_col)))
            out.append({"渠道": ch, "数量": cnt})

        if not out and rows:
            # 仅有「全部汇总」等汇总行、无渠道展开：打印汇总行 DAU 并提示需展开抓取
            skip_labels = ("全部汇总", "ALL", "> ALL")
            for r in rows:
                ch = str(r.get(ch_col) or "").strip()
                if ch in skip_labels:
                    cnt = int(_safe_float(r.get(dau_col)))
                    out.append({"渠道": f"[汇总] {ch}", "数量": cnt})
            if not out:
                print(f"[DEBUG] Table has {len(rows)} rows but no channel column match. Columns: {cols}")

        # 同渠道合并（若有多行）、按数量降序
        by_ch: dict[str, int] = {}
        for x in out:
            by_ch[x["渠道"]] = by_ch.get(x["渠道"], 0) + x["数量"]
        return [{"渠道": k, "数量": v} for k, v in sorted(by_ch.items(), key=lambda t: -t[1])], target_date
    finally:
        conn.close()


def main() -> int:
    args = [a.strip() for a in sys.argv[1:] if a.strip()]
    date_str = None
    filter_channels: list[str] = []

    if args and args[0] == "--channels":
        if len(args) > 1:
            filter_channels = [c.strip() for c in args[1].split(",") if c.strip()]
        args = args[2:]
    if args and len(args[0]) >= 10:
        try:
            datetime.strptime(args[0][:10], "%Y-%m-%d")
            date_str = args[0][:10]
            args = args[1:]
        except ValueError:
            pass
    if args and not filter_channels:
        filter_channels = args

    print("bi.duckdb 各渠道 DAU 查询")
    print("表: bi_stats_user_dau（日活统计）")
    if date_str:
        print(f"业务日期: {date_str}")
    else:
        print("业务日期: 表中最新一批（自动取最新日期）")
    if filter_channels:
        print(f"筛选渠道: {filter_channels}")
    print("-" * 50)

    rows, resolved_date = query_dau_by_channel(date_str)
    if resolved_date:
        print(f"实际使用日期: {resolved_date}")
        print("-" * 50)
    if not rows:
        print("无渠道数据。若表存在但为空，请设置 skip_collect=false 并重新抓取「日活统计」且展开日期/渠道。")
        return 0

    # 按渠道名匹配（规范化后比较）
    by_normalized: dict[str, tuple[str, int]] = {}
    for r in rows:
        ch = r["渠道"]
        norm = _normalize_channel(ch)
        if norm not in by_normalized or r["数量"] > by_normalized[norm][1]:
            by_normalized[norm] = (ch, r["数量"])

    if filter_channels:
        total = 0
        for i, want in enumerate(filter_channels, 1):
            norm = _normalize_channel(want)
            count = 0
            display_name = want
            for k, (orig, cnt) in by_normalized.items():
                if k == norm or norm in k or k in norm:
                    count = cnt
                    display_name = orig
                    break
            total += count
            print(f"  {i}. {display_name}: {count}")
        print("-" * 50)
        print(f"  筛选合计 DAU: {total}")
    else:
        total = sum(r["数量"] for r in rows)
        for i, r in enumerate(rows, 1):
            print(f"  {i:2}. {r['渠道']}: {r['数量']}")
        print("-" * 50)
        print(f"  合计渠道数: {len(rows)}  总 DAU: {total}")
    if len(rows) == 1 and "[汇总]" in str(rows[0].get("渠道", "")):
        print("\n  说明: 当前表仅有汇总行、无各渠道明细。若需按渠道 DAU，请设置 skip_collect=false 并重新抓取「日活统计」，在 BI 页面展开日期/渠道后再执行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
