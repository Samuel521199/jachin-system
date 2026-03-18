"""
BI 数据日报提纯 — 输出 Lark 多维表格可导入的 CSV

从 DuckDB 读取抓取数据，按产品需求提炼为结构化表，输出 CSV 供 Lark 导入。
设计: 产品需求文档 — 用户活跃/留存/消耗/充值
"""
from __future__ import annotations

import csv
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from l3_node.mcp_tools.bi.data_store import _get_conn, _sanitize_table_name
from l3_node.mcp_tools.bi.paths import get_bi_output_dir, ensure_bi_dirs

logger = logging.getLogger(__name__)


def _find_col(columns: list[str], *candidates: str) -> str | None:
    """按候选名查找列"""
    for cand in candidates:
        for c in columns:
            if cand and (cand.lower() in (c or "").lower() or (c or "") == cand):
                return c
    return None


def _safe_float(v: Any) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def _get_date_col(conn: Any, table: str) -> str | None:
    """推断日期列"""
    cols = [r[0] for r in conn.execute(f"DESCRIBE {table}").fetchall()]
    return _find_col(cols, "日期", "date", "统计日期", "_ingested_date")


def _query_table(conn: Any, slug: str, date_from: str | None = None, date_to: str | None = None) -> list[dict]:
    """
    查询表数据。优先按业务日期列（日期/date/统计日期）过滤，若无则用 _ingested_date。
    _ingested_date 为导入日，业务日期列才是报表实际统计日期。
    """
    table = _sanitize_table_name(slug)
    try:
        tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
        if table not in tables:
            return []
    except Exception as ex:
        return []

    date_col: str | None = None
    if date_from or date_to:
        cols = [r[0] for r in conn.execute(f"DESCRIBE {table}").fetchall()]
        date_col = _find_col(cols, "日期", "date", "统计日期") or ("_ingested_date" if "_ingested_date" in cols else None)
        if not date_col:
            date_col = "_ingested_date"

    where = []
    if date_col and date_from:
        where.append(f'"{date_col}" >= \'{date_from}\'')
    if date_col and date_to:
        where.append(f'"{date_col}" <= \'{date_to}\'')
    sql = f"SELECT * FROM {table}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    order_col = date_col or "_ingested_date"
    sql += f' ORDER BY "{order_col}" DESC'
    try:
        rel = conn.execute(sql)
        cols = [d[0] for d in rel.description]
        rows = [dict(zip(cols, row)) for row in rel.fetchall()]
        return rows
    except Exception as e:
        logger.warning("[Refiner] query %s: %s", table, e)
        return []


def _date_to_lark_ts(d: str) -> int:
    """日期字符串 YYYY-MM-DD 转 Lark 日期字段所需的毫秒时间戳"""
    try:
        dt = datetime.strptime(d[:10], "%Y-%m-%d")
        return int(dt.timestamp() * 1000)
    except ValueError:
        return 0


def _lark_safe_text(v: str) -> str:
    """避免纯数字被 sync 误转为 float 导致 Lark 文本列 TextFieldConvFail，加零宽字符"""
    s = (v or "").strip()
    if s and s.replace(".", "").replace("-", "").replace(",", "").isdigit():
        return s + "\u200b"  # zero-width space，显示不变
    return s


def _write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> int:
    """写入 CSV，返回行数"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def refine_user_activity(conn: Any, output_dir: Path, t1: str, t0: str, t7: str) -> list[Path]:
    """
    用户登录活跃情况
    - 增幅表: (T-1-T-2)/T-2
    - 日期数量表: T-7~T-1
    - 渠道来源: T-1 日 DAU/DNU 按渠道
    """
    written: list[Path] = []
    rows = _query_table(conn, "daily_ops_summary", date_from=t7, date_to=t1)
    if not rows:
        logger.warning("[Refiner] daily_ops_summary 无数据")
        return written

    cols = list(rows[0].keys())
    date_col = _find_col(cols, "日期", "date", "统计日期") or "_ingested_date"
    # BI 每日运营数据汇总：日活、当日新增用户
    dau_col = _find_col(cols, "日活（DAU）", "日活(DAU)", "日活", "DAU", "dau")
    dnu_col = _find_col(cols, "当日新增用户（DNU）", "当日新增用户", "新增用户(DNU)", "新增用户", "DNU", "dnu")

    # 按日期倒序，取最新两天（T-1、T-2）
    by_date = {str(r.get(date_col, ""))[:10]: r for r in rows if r.get(date_col)}
    dates_sorted = sorted([d for d in by_date.keys() if len(d) == 10], reverse=True)[:8]

    if not dau_col or not dnu_col:
        logger.warning("[Refiner] daily_ops_summary 缺少 DAU/DNU 列，cols=%s", cols[:10])
        return written

    # 1. 增幅表（DAU和DNU）：(T-1 - T-2) / T-2，输出浮点数如 0.15 表示 15%，分母为0输出0
    d1 = dates_sorted[0] if len(dates_sorted) >= 1 else t1
    d2 = dates_sorted[1] if len(dates_sorted) >= 2 else t0
    r1 = by_date.get(d1, {})
    r0 = by_date.get(d2, {})
    dau1, dnu1 = _safe_float(r1.get(dau_col)), _safe_float(r1.get(dnu_col))
    dau0, dnu0 = _safe_float(r0.get(dau_col)), _safe_float(r0.get(dnu_col))
    dau_pct = round((dau1 - dau0) / dau0, 4) if dau0 else 0.0
    dnu_pct = round((dnu1 - dnu0) / dnu0, 4) if dnu0 else 0.0

    # 输出列：类型、增幅。Lark 表列为「类型」，使用 类型 避免 FieldNameNotFound
    increase_rows = [
        {"类型": "DAU", "增幅": dau_pct},
        {"类型": "DNU", "增幅": dnu_pct},
    ]
    p = output_dir / "01_用户活跃_增幅表.csv"
    _write_csv(p, increase_rows, ["类型", "增幅"])
    written.append(p)

    # 2. 日期数量表 (T-7~T-1)。日期列输出毫秒时间戳供 Lark 日期字段（避免 DatetimeFieldConvFail）
    daily_rows = []
    for d in (dates_sorted[:7][::-1] if len(dates_sorted) >= 7 else list(reversed(dates_sorted))):
        r = by_date.get(d, {})
        daily_rows.append({
            "日期": _date_to_lark_ts(d),
            "DAU数量": int(_safe_float(r.get(dau_col))),
            "DNU数量": int(_safe_float(r.get(dnu_col))),
        })
    p2 = output_dir / "02_用户活跃_日期数量表.csv"
    _write_csv(p2, daily_rows, ["日期", "DAU数量", "DNU数量"])
    written.append(p2)

    # 3a/3b. 渠道来源表 — 拆分为 DAU渠道来源、DNU渠道来源 两个子表（对应 Lark 两个独立表）
    channel_rows = _query_table(conn, "daily_acquisition", date_from=t0, date_to=t1)
    if not channel_rows:
        channel_rows = _query_table(conn, "alert_traffic", date_from=t0, date_to=t1)
    if channel_rows:
        ch_cols = list(channel_rows[0].keys())
        ch_col = _find_col(ch_cols, "渠道", "来源", "channel", "Source Channel", "DNU渠道来源", "DAU渠道来源")
        type_col = _find_col(ch_cols, "类型", "type", "DAU", "DNU")
        count_col = _find_col(ch_cols, "数量", "人数", "Count", "用户数")
        if ch_col or type_col:
            # 按 类型 拆分为 DAU / DNU 两组，与 Lark「DAU渠道来源」「DNU渠道来源」表结构一一对应
            dau_rows: list[dict] = []
            dnu_rows: list[dict] = []
            for r in channel_rows[:50]:
                typ = str(r.get(type_col, r.get("类型", ""))).strip().upper()
                ch_val = str(r.get(ch_col, r.get("渠道", ""))).strip()
                cnt = int(_safe_float(r.get(count_col))) if count_col else 1
                if not ch_val and not typ:
                    continue
                if "DAU" in typ or typ in ("日活", "日活用户"):
                    dau_rows.append({"DAU渠道来源": ch_val or "（未知）", "数量": cnt})
                elif "DNU" in typ or typ in ("新增", "新增用户"):
                    dnu_rows.append({"DNU渠道来源": ch_val or "（未知）", "数量": cnt})
                elif not type_col:
                    dau_rows.append({"DAU渠道来源": ch_val or "（未知）", "数量": cnt})
                    dnu_rows.append({"DNU渠道来源": ch_val or "（未知）", "数量": cnt})

            # Lark DAU渠道来源 表：DAU渠道来源, 数量
            p3a = output_dir / "03a_用户活跃_DAU渠道来源.csv"
            _write_csv(p3a, dau_rows if dau_rows else [{"DAU渠道来源": "（需抓取 daily_acquisition）", "数量": 0}], ["DAU渠道来源", "数量"])
            written.append(p3a)

            # Lark DNU渠道来源 表：DNU渠道来源, 数量
            p3b = output_dir / "03b_用户活跃_DNU渠道来源.csv"
            _write_csv(p3b, dnu_rows if dnu_rows else [{"DNU渠道来源": "（需抓取 daily_acquisition）", "数量": 0}], ["DNU渠道来源", "数量"])
            written.append(p3b)
    else:
        p3a = output_dir / "03a_用户活跃_DAU渠道来源.csv"
        _write_csv(p3a, [{"DAU渠道来源": "（需抓取 daily_acquisition）", "数量": 0}], ["DAU渠道来源", "数量"])
        written.append(p3a)
        p3b = output_dir / "03b_用户活跃_DNU渠道来源.csv"
        _write_csv(p3b, [{"DNU渠道来源": "（需抓取 daily_acquisition）", "数量": 0}], ["DNU渠道来源", "数量"])
        written.append(p3b)

    return written


def refine_retention(conn: Any, output_dir: Path, t1: str) -> list[Path]:
    """
    平台留存情况（与 Lark 多维表格一一对应）
    - 次留表: 全用户+付费用户合并，列 用户类型、类型、人数、百分比
    - 周环比: 这周留存率、上周留存率、周环比
    - 月环比: 占位，今后补全
    """
    written: list[Path] = []
    user_rows = _query_table(conn, "stats_retention_user", date_from=t1, date_to=t1)
    paid_rows = _query_table(conn, "stats_retention_paid", date_from=t1, date_to=t1)

    def _extract(rows: list[dict]) -> list[dict]:
        if not rows:
            return []
        cols = list(rows[0].keys())
        type_col = _find_col(cols, "类型", "指标", "留存类型")
        count_col = _find_col(cols, "人数", "Count", "数量")
        pct_col = _find_col(cols, "百分比", "留存率")
        out: list[dict] = []
        for r in rows[:10]:
            raw_pct = _safe_float(r.get(pct_col))
            # 百分比输出浮点数 0.45 表示 45%，raw 若为 45 则除 100
            pct_val = round(raw_pct / 100.0, 4) if raw_pct > 1 else round(raw_pct, 4)
            out.append({
                "类型": str(r.get(type_col, "")),
                "人数": int(_safe_float(r.get(count_col))),
                "百分比": pct_val,
            })
        return out

    # Lark 次留表：类型, 数字, 百分比（数字如 0.45=45%，无%符号）
    merged_rows: list[dict] = []
    for r in _extract(user_rows):
        merged_rows.append({"类型": f"【全用户】{r['类型']}", "数字": r["人数"], "百分比": r["百分比"]})
    for r in _extract(paid_rows):
        merged_rows.append({"类型": f"【付费用户】{r['类型']}", "数字": r["人数"], "百分比": r["百分比"]})

    if merged_rows:
        p = output_dir / "04_留存_次留表.csv"
        _write_csv(p, merged_rows, ["类型", "数字", "百分比"])
        written.append(p)
    else:
        p = output_dir / "04_留存_次留表.csv"
        _write_csv(p, [{"类型": "【全用户】次留", "数字": 0, "百分比": 0}], ["类型", "数字", "百分比"])
        written.append(p)

    # Lark 周环比：这周留存率, 上周留存率（占位用 -，待 BI 有周环比数据后补全）
    wow_rows = [{"这周留存率": "-", "上周留存率": "-"}]
    p = output_dir / "06_留存_周环比表.csv"
    _write_csv(p, wow_rows, ["这周留存率", "上周留存率"])
    written.append(p)

    # Lark 月环比：这月留存率, 上月留存率（占位用 -）
    mom_rows = [{"这月留存率": "-", "上月留存率": "-"}]
    p = output_dir / "12_留存_月环比表.csv"
    _write_csv(p, mom_rows, ["这月留存率", "上月留存率"])
    written.append(p)

    return written


def refine_consumption(conn: Any, output_dir: Path, t1: str, t7: str) -> list[Path]:
    """
    平台消耗情况
    - 每日: T-7 到 T-1 的日期、产出、消耗
    - 游戏: T-1 日按游戏分组
    """
    written: list[Path] = []
    # 每日表：取 T-7 到 T-1
    rows_daily = _query_table(conn, "prod_sales", date_from=t7, date_to=t1)
    if not rows_daily:
        rows_daily = _query_table(conn, "stats_game_daily", date_from=t7, date_to=t1)
    # 游戏表：取 T-1
    rows_game = _query_table(conn, "prod_sales", date_from=t1, date_to=t1)
    if not rows_game:
        rows_game = _query_table(conn, "stats_game_daily", date_from=t1, date_to=t1)

    if not rows_daily and not rows_game:
        logger.warning("[Refiner] prod_sales/stats_game_daily 无数据")
        return written

    rows = rows_daily or rows_game
    cols = list(rows[0].keys())
    cons_col = _find_col(cols, "消耗", "用户金币消耗", "金币消耗")
    prod_col = _find_col(cols, "产出", "用户金币产出", "金币产出")
    game_col = _find_col(cols, "游戏", "游戏名")
    date_col = _find_col(cols, "日期", "date")

    # 每日表：T-7 到 T-1，每日期一行
    by_date_daily: dict[str, dict] = {}
    for r in (rows_daily or []):
        d = str(r.get(date_col, ""))[:10]
        if d not in by_date_daily:
            by_date_daily[d] = {"日期": d, "产出": 0.0, "消耗": 0.0}
        by_date_daily[d]["产出"] += _safe_float(r.get(prod_col))
        by_date_daily[d]["消耗"] += _safe_float(r.get(cons_col))
    dates_sorted = sorted([d for d in by_date_daily if len(d) == 10])[-7:]  # 最近7天
    daily_rows = [{"日期": _date_to_lark_ts(d), "产出": round(by_date_daily[d]["产出"], 2), "消耗": round(by_date_daily[d]["消耗"], 2)} for d in dates_sorted]
    if not daily_rows:
        daily_rows = [{"日期": _date_to_lark_ts(t1), "产出": 0.0, "消耗": 0.0}]
    p2 = output_dir / "08_消耗_每日表.csv"
    _write_csv(p2, daily_rows, ["日期", "产出", "消耗"])
    written.append(p2)

    # Lark 每个游戏的产出、消耗：游戏名称, 产出, 消耗（T-1 日数据）
    game_rows = []
    if game_col and rows_game:
        for r in rows_game[:20]:
            game_rows.append({
                "游戏名称": str(r.get(game_col) or ""),
                "产出": round(_safe_float(r.get(prod_col)), 2),
                "消耗": round(_safe_float(r.get(cons_col)), 2),
            })
    if not game_rows:
        game_rows = [{"游戏名称": "（需 prod_sales 含游戏列）", "产出": 0.0, "消耗": 0.0}]
    p3 = output_dir / "09_消耗_按游戏表.csv"
    _write_csv(p3, game_rows, ["游戏名称", "产出", "消耗"])
    written.append(p3)

    return written


def refine_recharge(conn: Any, output_dir: Path, t1: str, days: int = 7) -> list[Path]:
    """
    平台充值情况（近一周累计）
    - 付费人数按 SKU
    - 付费金额按 SKU
    """
    written: list[Path] = []
    dt = datetime.strptime(t1[:10], "%Y-%m-%d")
    date_from = (dt - timedelta(days=days)).strftime("%Y-%m-%d")

    rows = _query_table(conn, "stats_recharge", date_from=date_from, date_to=t1)
    if not rows:
        rows = _query_table(conn, "recharge_status", date_from=date_from, date_to=t1)
    if not rows:
        logger.warning("[Refiner] stats_recharge/recharge_status 无数据")
        return written

    cols = list(rows[0].keys())
    sku_col = _find_col(cols, "充值金额", "SKU", "金额档位", "等级", "不同充值金额")
    count_col = _find_col(cols, "人数", "付费人数", "用户数")
    amount_col = _find_col(cols, "总金额", "金额", "此等级总金额")

    # 按 SKU 聚合，两表统一列名「不同充值金额分等级」
    SKU_COL = "不同充值金额分等级"
    if sku_col:
        by_sku: dict[str, dict] = {}
        for r in rows:
            k = str(r.get(sku_col, ""))
            if k not in by_sku:
                by_sku[k] = {SKU_COL: k, "人数": 0, "此等级总金额": 0.0}
            by_sku[k]["人数"] += int(_safe_float(r.get(count_col)))
            by_sku[k]["此等级总金额"] += _safe_float(r.get(amount_col))
        out_rows = list(by_sku.values())
    else:
        total_count = sum(int(_safe_float(r.get(count_col))) for r in rows)
        total_amount = sum(_safe_float(r.get(amount_col)) for r in rows)
        out_rows = [{SKU_COL: "合计", "人数": total_count, "此等级总金额": round(total_amount, 2)}]

    p1 = output_dir / "10_充值_付费人数按SKU.csv"
    _write_csv(p1, [{SKU_COL: _lark_safe_text(str(r[SKU_COL])), "人数": r["人数"]} for r in out_rows], [SKU_COL, "人数"])
    written.append(p1)

    p2 = output_dir / "11_充值_付费金额按SKU.csv"
    _write_csv(p2, [{SKU_COL: _lark_safe_text(str(r[SKU_COL])), "此等级总金额": round(r["此等级总金额"], 2)} for r in out_rows], [SKU_COL, "此等级总金额"])
    written.append(p2)

    return written


def run_refiner(
    date_str: str | None = None,
    output_dir: Path | None = None,
    config_path: str | Path | None = None,
) -> tuple[list[Path], list[str]]:
    """
    执行完整提纯流程。

    Args:
        date_str: 目标日期 YYYY-MM-DD，默认昨日
        output_dir: 输出目录，默认 get_bi_output_dir()
        config_path: 配置路径（预留）

    Returns:
        (written_paths, errors)
    """
    ensure_bi_dirs()
    out = output_dir or get_bi_output_dir()
    dt = datetime.now()
    if date_str:
        try:
            dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
        except ValueError:
            dt = datetime.now()
    t1 = (dt - timedelta(days=1)).strftime("%Y-%m-%d")   # T-1 昨日
    t0 = (dt - timedelta(days=2)).strftime("%Y-%m-%d")   # T-2 前日
    t7 = (dt - timedelta(days=7)).strftime("%Y-%m-%d")   # T-7 一周前

    conn = _get_conn()
    written: list[Path] = []
    errors: list[str] = []

    try:
        written += refine_user_activity(conn, out, t1, t0, t7)
        written += refine_retention(conn, out, t1)
        written += refine_consumption(conn, out, t1, t7)
        written += refine_recharge(conn, out, t1, days=7)
    except Exception as e:
        errors.append(str(e))
        logger.exception("[Refiner] 提纯异常: %s", e)
    finally:
        conn.close()

    return (written, errors)


def sync_refiner_to_lark(
    written_paths: list[Path],
    lark_bitable_config: dict[str, Any],
) -> tuple[int, list[str]]:
    """
    将提纯输出的 CSV 同步到 Lark 多维表格。
    使用 atom_lark_bitable_sync.sync_csv_to_bitable（复用已有工具）。

    Args:
        written_paths: 提纯生成的 CSV 路径列表
        lark_bitable_config: bi_daily_report.yaml 中的 lark_bitable 配置

    Returns:
        (成功数, 错误列表)
    """
    if not lark_bitable_config.get("enabled"):
        return (0, [])

    # 若配置中有 app_id/app_secret，注入到环境变量供 Lark API 使用
    _cid = (lark_bitable_config.get("app_id") or "").strip()
    _csec = (lark_bitable_config.get("app_secret") or "").strip()
    if _cid and _csec:
        os.environ.setdefault("LARK_APP_ID", _cid)
        os.environ.setdefault("LARK_APP_SECRET", _csec)
    # 飞书中国版：若配置 lark_use_feishu: true，使用 open.feishu.cn
    if lark_bitable_config.get("lark_use_feishu"):
        os.environ["LARK_USE_FEISHU"] = "1"

    app_token = (lark_bitable_config.get("app_token") or "").strip() or None
    tables_map = lark_bitable_config.get("tables") or {}
    if not app_token or not tables_map:
        return (0, ["lark_bitable.app_token 或 tables 未配置"])

    # 复用 atom_lark_bitable_sync
    try:
        import sys
        from l3_node.paths import get_app_root
        plugin_root = get_app_root() / "skills_repo" / "plugin" / "2-track-a-atomic-mcp"
        if plugin_root.exists() and str(plugin_root) not in sys.path:
            sys.path.insert(0, str(plugin_root))
        from tools.atom_lark_bitable_sync import sync_csv_to_bitable
    except ImportError as e:
        return (0, [f"无法导入 atom_lark_bitable_sync: {e}"])

    ok_count = 0
    errors: list[str] = []

    for p in written_paths:
        name = p.name
        table_id = (tables_map.get(name) or "").strip()
        if not table_id:
            continue
        ensure_cols = lark_bitable_config.get("ensure_columns", False)
        replace = lark_bitable_config.get("replace_table", False)
        result = sync_csv_to_bitable(
            csv_path=str(p),
            app_token=app_token,
            table_id=table_id,
            replace_table=replace,
            ensure_columns=ensure_cols,
        )
        if result.get("success"):
            ok_count += 1
            logger.info("[Refiner] Lark 同步成功: %s -> %s (%d 行)", name, table_id, result.get("count", 0))
        else:
            errors.append(f"{name}: {result.get('error', '未知错误')}")

    return (ok_count, errors)
