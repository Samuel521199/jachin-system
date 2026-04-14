"""
AKShare A 股数据 — Native Tool（非 MCP）。

依赖：pip install akshare（见 core/requirements.txt）。
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# 供 loader.NATIVE_TOOLS 扩展
AKSHARE_NATIVE_TOOLS_LIST: list[dict[str, Any]] = [
    {
        "id": "core:akshare_a_share_hist",
        "label": "core:akshare_a_share_hist",
        "desc": (
            "【A 股行情 · 优先】用户问 A 股走势、K 线、日线/周线、某代码区间涨跌时必须先用本工具取结构化数据；"
            "**禁止**用 mcp:fetch 编造财经网站 URL 代替。"
            "AKShare 历史 K 线。JSON：symbol（6 位如 600519）、start_date、end_date；"
            "可选 period（daily|weekly|monthly，默认 daily）、adjust（qfq|hfq|，默认 qfq）。"
            "日期支持 YYYYMMDD 或 YYYY-MM-DD。"
        ),
        "params": ["symbol", "start_date", "end_date"],
    },
    {
        "id": "core:akshare_company_info",
        "label": "core:akshare_company_info",
        "desc": (
            "【A 股基本面 · 优先】用户问利润表、财报摘要、基本面指标时必须先用本工具；"
            "**禁止**仅依赖 mcp:fetch 网页抓取冒充财报数据。"
            "利润表（新浪）与财务摘要指标（若接口可用）。"
            "JSON：symbol（6 位代码）；可选 report_rows（默认 12，利润表最多返回行数）。"
        ),
        "params": ["symbol"],
    },
]


def _six_digit_a_code(symbol: str) -> str:
    digits = "".join(ch for ch in (symbol or "").strip() if ch.isdigit())
    if len(digits) >= 6:
        return digits[-6:]
    raise ValueError("symbol 须为 6 位 A 股代码，例如 600519、000001")


def _normalize_yyyymmdd(s: str) -> str:
    t = (s or "").strip().replace("-", "").replace("/", "")
    if len(t) == 8 and t.isdigit():
        return t
    raise ValueError("start_date/end_date 须为 YYYYMMDD 或 YYYY-MM-DD")


def _df_to_records(df: Any, *, max_rows: int) -> list[dict[str, Any]]:
    if df is None:
        return []
    try:
        import pandas as pd

        if hasattr(df, "empty") and df.empty:
            return []
        df2 = df.head(max_rows).copy()
        # NaN / NaT -> None for JSON
        return json.loads(df2.to_json(orient="records", date_format="iso", force_ascii=False))
    except Exception as e:
        logger.warning("[akshare_tools] DataFrame 转 JSON 失败: %s", e)
        return []


def get_a_share_hist(
    symbol: str,
    start_date: str,
    end_date: str,
    *,
    period: str = "daily",
    adjust: str = "qfq",
) -> dict[str, Any]:
    """
    获取 A 股历史 K 线（日/周/月），返回结构化字典。
    """
    try:
        import akshare as ak  # type: ignore[import-untyped]
    except ImportError as e:
        return {"ok": False, "error_class": "config", "error": f"未安装 akshare：{e}；请执行 pip install akshare"}

    code = _six_digit_a_code(symbol)
    sd = _normalize_yyyymmdd(start_date)
    ed = _normalize_yyyymmdd(end_date)
    per = (period or "daily").strip().lower()
    adj = (adjust or "qfq").strip().lower()
    if per not in ("daily", "weekly", "monthly"):
        return {"ok": False, "error_class": "config", "error": f"不支持的 period: {per}"}
    if adj not in ("", "qfq", "hfq"):
        return {"ok": False, "error_class": "config", "error": f"不支持的 adjust: {adj}"}

    try:
        df = ak.stock_zh_a_hist(symbol=code, period=per, start_date=sd, end_date=ed, adjust=adj)
        rows = _df_to_records(df, max_rows=2000)
        return {
            "ok": True,
            "symbol": code,
            "period": per,
            "adjust": adj,
            "start_date": sd,
            "end_date": ed,
            "row_count": len(rows),
            "bars": rows,
        }
    except Exception as e:
        logger.warning("[akshare_tools] stock_zh_a_hist 失败 symbol=%s: %s", code, e)
        return {
            "ok": False,
            "error_class": "transient" if "timeout" in str(e).lower() or "connection" in str(e).lower() else "permanent",
            "error": str(e),
            "symbol": code,
        }


def get_company_info(symbol: str, *, report_rows: int = 12) -> dict[str, Any]:
    """
    获取公司基本面：利润表摘要 + 财务摘要（若接口可用）。
    """
    try:
        import akshare as ak  # type: ignore[import-untyped]
    except ImportError as e:
        return {"ok": False, "error_class": "config", "error": f"未安装 akshare：{e}；请执行 pip install akshare"}

    code = _six_digit_a_code(symbol)
    mr = max(1, min(80, int(report_rows)))

    out: dict[str, Any] = {
        "ok": True,
        "symbol": code,
        "income_statement": [],
        "financial_abstract": [],
        "notes": [],
    }

    # 新浪财经财务报表：参数为 6 位数字股票代码
    try:
        df_lrb = ak.stock_financial_report_sina(stock=code, symbol="利润表")
        out["income_statement"] = _df_to_records(df_lrb, max_rows=mr)
    except Exception as e:
        logger.warning("[akshare_tools] stock_financial_report_sina 利润表 失败: %s", e)
        out["notes"].append(f"利润表拉取失败: {e}")

    # 财务摘要（关键指标，列较多，取前若干行）
    try:
        if hasattr(ak, "stock_financial_abstract"):
            df_abs = ak.stock_financial_abstract(symbol=code)
            out["financial_abstract"] = _df_to_records(df_abs, max_rows=min(mr, 24))
    except Exception as e:
        logger.debug("[akshare_tools] stock_financial_abstract 跳过: %s", e)
        out["notes"].append(f"财务摘要未获取: {e}")

    if not out["income_statement"] and not out["financial_abstract"]:
        out["ok"] = False
        out["error_class"] = "per_item"
        out["error"] = "未能获取利润表或财务摘要（数据源可能变更或网络异常）"
    return out


def dispatch_akshare_core(tool_id: str, **kwargs: Any) -> dict[str, Any]:
    """供 core.native_tools.dispatch_native_tool 转发。"""
    tid = (tool_id or "").strip().lower()
    if tid == "core:akshare_a_share_hist":
        return get_a_share_hist(
            str(kwargs.get("symbol") or ""),
            str(kwargs.get("start_date") or ""),
            str(kwargs.get("end_date") or ""),
            period=str(kwargs.get("period") or "daily"),
            adjust=str(kwargs.get("adjust") or "qfq"),
        )
    if tid == "core:akshare_company_info":
        rr = kwargs.get("report_rows", 12)
        try:
            rr_i = int(rr)
        except (TypeError, ValueError):
            rr_i = 12
        return get_company_info(str(kwargs.get("symbol") or ""), report_rows=rr_i)
    return {"ok": False, "error_class": "config", "error": f"未知 AKShare 工具: {tool_id}"}
