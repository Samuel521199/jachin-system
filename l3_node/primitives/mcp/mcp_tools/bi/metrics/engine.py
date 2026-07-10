"""
BI 指标 — 执行引擎

加载配置、调度插件、提取指标、输出
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from l3_node.primitives.mcp.mcp_tools.bi.metrics.registry import get_data_source, get_outputter

# 加载内置插件（注册到 registry）
import l3_node.primitives.mcp.mcp_tools.bi.metrics.plugins  # noqa: F401


def _get_compare_date(target_date: str, period: str) -> str:
    """根据对比周期计算对比日期。period: day|week|month"""
    try:
        dt = datetime.strptime(target_date[:10], "%Y-%m-%d")
    except ValueError:
        return ""
    if period == "day":
        prev = dt - timedelta(days=1)
    elif period == "week":
        prev = dt - timedelta(days=7)
    elif period == "month":
        prev = dt - timedelta(days=30)  # 近似一个月
    else:
        prev = dt - timedelta(days=1)
    return prev.strftime("%Y-%m-%d")


def _find_col(columns: list[str], *candidates: str) -> str | None:
    for cand in candidates:
        for col in columns:
            if cand.lower() in col.lower() or cand in col:
                return col
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


def _load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """加载配置。规范 075：优先 config/skills/com.jachin.bi.daily_report/bi_metrics.yaml"""
    if config_path is None:
        root = Path(__file__).resolve().parent.parent.parent.parent.parent
        jachin_root = Path.home() / ".jachin"
        candidates = [
            jachin_root / "config" / "skills" / "com.jachin.bi.daily_report" / "bi_metrics.yaml",
            root / "config" / "skills" / "com.jachin.bi.daily_report" / "bi_metrics.yaml",
            root / "config" / "mcps" / "atom_bi_metrics" / "bi_metrics.yaml",
        ]
        config_path = next((p for p in candidates if p.exists()), candidates[1])
    path = Path(config_path)
    if not path.exists():
        return {"error": f"配置文件不存在: {path}"}

    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        return {"error": f"配置加载失败: {e}"}


def _extract_from_row(row: dict[str, Any], col_candidates: list[str]) -> Any:
    cols = list(row.keys())
    for cand in col_candidates:
        c = _find_col(cols, cand)
        if c:
            return row.get(c)
    return None


def run(
    date_str: str | None = None,
    show_compare: bool = True,
    compare_period: str = "day",
    output_format: str = "console",
    config_path: str | Path | None = None,
) -> tuple[dict[str, Any], str]:
    """
    执行指标查询并输出。

    Returns:
        (metrics_dict, output_string)
    """
    cfg = _load_config(config_path)
    if "error" in cfg:
        return ({"_error": cfg["error"]}, f"错误: {cfg['error']}")

    metrics_cfg = cfg.get("metrics", [])
    output_cfg = cfg.get("output", {})
    data_source_name = cfg.get("data_source", "duckdb")
    ds_class = get_data_source(data_source_name)
    if not ds_class:
        return ({"_error": f"数据源未注册: {data_source_name}"}, f"错误: 数据源 {data_source_name} 未注册")

    # 收集需拉取的表（不再用 compare_table，同一表取两期）
    tables = []
    for m in metrics_cfg:
        t = m.get("table")
        if t and t not in tables:
            tables.append(t)

    if not tables:
        return ({"_error": "无指标配置"}, "错误: 无指标配置")

    # 拉取数据（先取当前，再算对比日期）
    ds = ds_class()
    date_col = cfg.get("date_column")
    raw = ds.fetch(tables, date_col, date_str, None, cfg.get("data_source_config", {}))

    # 获取实际目标日期（若未指定则从数据中取）
    primary_table = metrics_cfg[0].get("table") if metrics_cfg else None
    target_date = date_str
    if not target_date and primary_table and primary_table in raw:
        tbl = raw[primary_table]
        row = tbl.get("current") if isinstance(tbl, dict) else tbl
        if row:
            cols = list(row.keys())
            dc = _find_col(cols, "日期", "date", "统计日期") or (cols[0] if cols else None)
            if dc:
                target_date = str(row.get(dc, ""))

    # 计算对比日期并拉取对比数据
    compare_date = _get_compare_date(target_date, compare_period) if (show_compare and target_date) else None
    if compare_date:
        raw = ds.fetch(tables, date_col, date_str, compare_date, cfg.get("data_source_config", {}))

    # 提取指标（支持 current/compare 结构）
    def _get_row(tbl_data: dict, which: str) -> dict | None:
        if isinstance(tbl_data, dict) and "current" in tbl_data:
            return tbl_data.get(which)
        return tbl_data if which == "current" else None

    result: dict[str, Any] = {}
    result_prev: dict[str, Any] = {}  # 上期值，用于 formula 和 pct

    for m in metrics_cfg:
        key = m.get("key")
        if not key:
            continue
        formula = m.get("formula")
        if formula:
            # 派生指标：用已有 result 计算
            try:
                safe: dict[str, Any] = {"__builtins__": {}}
                for k, v in result.items():
                    if isinstance(v, (int, float)) and not k.endswith("_pct"):
                        safe[k] = v
                val = eval(formula, safe)
                result[key] = _safe_float(val)
            except Exception:
                result[key] = 0.0
            # 派生指标的环比：用上期 result_prev 计算
            if show_compare and key in result:
                try:
                    safe_prev = {"__builtins__": {}}
                    for k, v in result_prev.items():
                        if isinstance(v, (int, float)) and not k.endswith("_pct"):
                            safe_prev[k] = v
                    prev_val = _safe_float(eval(formula, safe_prev))
                    cur_val = result[key]
                    if prev_val and prev_val != 0:
                        result[f"{key}_pct"] = (cur_val - prev_val) / prev_val * 100
                    else:
                        result[f"{key}_pct"] = 0.0
                except Exception:
                    result[f"{key}_pct"] = 0.0
            else:
                result[f"{key}_pct"] = None
            continue

        table = m.get("table")
        tbl_data = raw.get(table) if table else None
        if not tbl_data:
            result[key] = 0.0
            result[f"{key}_pct"] = None
            continue

        row = _get_row(tbl_data, "current")
        comp_row = _get_row(tbl_data, "compare")
        if not row:
            result[key] = 0.0
            result[f"{key}_pct"] = None
            continue

        col_cands = m.get("column_candidates", [])
        val = _extract_from_row(row, col_cands)
        result[key] = _safe_float(val)
        if m.get("value_scale") == "win_rate_pct":
            v = result[key]
            if 0 < v <= 1.0:
                result[key] = round(v * 100, 2)

        prev_val = _extract_from_row(comp_row, col_cands) if comp_row else None
        prev_float = _safe_float(prev_val)
        if m.get("value_scale") == "win_rate_pct" and 0 < prev_float <= 1.0:
            prev_float = round(prev_float * 100, 2)
        result_prev[key] = prev_float

        # 环比：(当前 - 上期) / 上期 * 100，无上期则 0.00%
        need_compare = m.get("compare", True) and show_compare
        if need_compare and comp_row is not None:
            cur_val = result[key]
            if prev_float != 0:
                result[f"{key}_pct"] = (cur_val - prev_float) / prev_float * 100
            else:
                result[f"{key}_pct"] = 0.0
        elif need_compare:
            result[f"{key}_pct"] = 0.0  # 无上期数据
        else:
            result[f"{key}_pct"] = None

    # 日期
    if primary_table and primary_table in raw:
        tbl = raw[primary_table]
        row = _get_row(tbl, "current") if isinstance(tbl, dict) else tbl
        if row:
            cols = list(row.keys())
            dc = _find_col(cols, "日期", "date", "统计日期") or (cols[0] if cols else None)
            if dc:
                result["date"] = str(row.get(dc, ""))

    # 输出
    out_plugin = output_format or output_cfg.get("plugin", "console")
    out_class = get_outputter(out_plugin)
    if not out_class:
        return (result, f"错误: 输出器未注册: {out_plugin}")

    out_section = output_cfg.get(out_plugin, output_cfg)
    if isinstance(out_section, dict):
        layout = out_section.get("layout", output_cfg.get("layout", []))
    else:
        layout = output_cfg.get("layout", [])
    out = out_class().format(result, {"layout": layout})
    return (result, out)


def main_cli() -> int:
    """命令行入口"""
    import sys
    args = sys.argv[1:]
    date_str = None
    show_compare = "--no-compare" not in args
    compare_period = "day"
    output = "console"
    for i, a in enumerate(args):
        if a == "--date" and i + 1 < len(args):
            date_str = args[i + 1]
        elif a == "--no-compare":
            show_compare = False
        elif a == "--compare-period" and i + 1 < len(args):
            compare_period = args[i + 1].lower()
            if compare_period not in ("day", "week", "month"):
                compare_period = "day"
        elif a == "--format" and i + 1 < len(args):
            output = args[i + 1]
        elif a == "--markdown":
            output = "markdown"

    _, out_str = run(
        date_str=date_str,
        show_compare=show_compare,
        compare_period=compare_period,
        output_format=output,
    )
    print(out_str)
    return 0
