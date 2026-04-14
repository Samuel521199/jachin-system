"""
全球标的（美股 / 加密货币 / 外汇对等 Yahoo 符号）— Native Tool（非 MCP）。

依赖：pip install yfinance（见 core/requirements.txt）。
数据来自 Yahoo Finance 公开接口；须遵守 Yahoo 使用条款，仅供研究与辅助分析。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# 供 loader.NATIVE_TOOLS 扩展
YFINANCE_NATIVE_TOOLS_LIST: list[dict[str, Any]] = [
    {
        "id": "core:yfinance_global_market_hist",
        "label": "core:yfinance_global_market_hist",
        "desc": (
            "【全球行情 · 优先】用户问美股/纳斯达克/纽交所标的、加密货币（如 BTC-USD）、或 Yahoo 可识别的外汇对等符号的"
            "历史 K 线、走势、OHLC 时必须先用本工具取结构化数据；**禁止**用 mcp:fetch 编造不存在的行情 URL 代替。"
            "基于 yfinance。JSON：ticker（如 AAPL、TSLA、NVDA、BTC-USD、EURUSD=X）；"
            "可选 period（1d|5d|1mo|3mo|6mo|1y|2y|5y|10y|ytd|max，默认 1mo）、"
            "interval（1m|2m|5m|15m|30m|60m|90m|1h|1d|5d|1wk|1mo|3mo，默认 1d）。"
        ),
        "params": ["ticker"],
    },
    {
        "id": "core:yfinance_ticker_info",
        "label": "core:yfinance_ticker_info",
        "desc": (
            "【全球标的快照 · 优先】用户问市盈率、市值、现价、52 周高低、成交量、板块等估值与报价字段时先用本工具；"
            "**禁止**仅依赖网页抓取冒充实时行情。"
            "基于 yfinance Ticker.info / fast_info。JSON：ticker（同上）。"
        ),
        "params": ["ticker"],
    },
]

_VALID_PERIOD = frozenset(
    {"1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"}
)
_VALID_INTERVAL = frozenset(
    {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d", "5d", "1wk", "1mo", "3mo"}
)

_INFO_KEYS: tuple[str, ...] = (
    "symbol",
    "shortName",
    "longName",
    "currency",
    "exchange",
    "quoteType",
    "regularMarketPrice",
    "currentPrice",
    "regularMarketPreviousClose",
    "regularMarketOpen",
    "regularMarketDayHigh",
    "regularMarketDayLow",
    "marketCap",
    "trailingPE",
    "forwardPE",
    "priceToBook",
    "dividendYield",
    "fiftyTwoWeekHigh",
    "fiftyTwoWeekLow",
    "averageVolume",
    "averageDailyVolume10Day",
    "sector",
    "industry",
    "longBusinessSummary",
)


def _normalize_ticker(raw: str) -> str:
    t = (raw or "").strip().upper()
    if not t:
        raise ValueError("ticker 不能为空，例如 AAPL、BTC-USD、EURUSD=X")
    if not re.match(r"^[A-Z0-9^=\-\.]+$", t):
        raise ValueError("ticker 包含非法字符，请使用 Yahoo Finance 符号（如 AAPL、BTC-USD）")
    return t


def _df_to_records(df: Any, *, max_rows: int) -> list[dict[str, Any]]:
    if df is None:
        return []
    try:
        if hasattr(df, "empty") and df.empty:
            return []
        df2 = df.head(max_rows).copy()
        return json.loads(df2.to_json(orient="records", date_format="iso", force_ascii=False))
    except Exception as e:
        logger.warning("[yfinance_tools] DataFrame 转 JSON 失败: %s", e)
        return []


def get_global_market_hist(
    ticker: str,
    *,
    period: str = "1mo",
    interval: str = "1d",
) -> dict[str, Any]:
    """
    获取标的的历史 OHLCV（K 线），返回结构化字典。
    """
    try:
        import yfinance as yf  # type: ignore[import-untyped]
    except ImportError as e:
        return {
            "ok": False,
            "error_class": "config",
            "error": f"未安装 yfinance：{e}；请执行 pip install yfinance",
        }

    try:
        sym = _normalize_ticker(ticker)
    except ValueError as e:
        return {"ok": False, "error_class": "config", "error": str(e)}

    per = (period or "1mo").strip().lower()
    if per not in _VALID_PERIOD:
        return {"ok": False, "error_class": "config", "error": f"不支持的 period: {per}，可选 {_VALID_PERIOD}"}

    iv = (interval or "1d").strip().lower()
    if iv not in _VALID_INTERVAL:
        return {"ok": False, "error_class": "config", "error": f"不支持的 interval: {iv}，可选 {_VALID_INTERVAL}"}

    try:
        tk = yf.Ticker(sym)
        df = tk.history(period=per, interval=iv, auto_adjust=True)
        rows = _df_to_records(df, max_rows=2500)
        if not rows:
            return {
                "ok": False,
                "error_class": "per_item",
                "error": "未返回行情数据：代码可能无效、已停牌或数据源暂无数据",
                "ticker": sym,
                "period": per,
                "interval": iv,
            }
        return {
            "ok": True,
            "ticker": sym,
            "period": per,
            "interval": iv,
            "row_count": len(rows),
            "bars": rows,
        }
    except Exception as e:
        err_s = str(e)
        logger.warning("[yfinance_tools] history 失败 ticker=%s: %s", sym, e)
        low = err_s.lower()
        ec = (
            "per_item"
            if ("invalid" in low or "not found" in low)
            else "transient"
        )
        return {"ok": False, "error_class": ec, "error": err_s, "ticker": sym}


def _merge_fast_into_info(ticker_obj: Any, info: dict[str, Any]) -> dict[str, Any]:
    out = dict(info)
    try:
        fi = getattr(ticker_obj, "fast_info", None)
        if fi is not None:
            if hasattr(fi, "items"):
                for k, v in fi.items():  # type: ignore[union-attr]
                    if k not in out or out[k] is None:
                        out[k] = v
            elif isinstance(fi, dict):
                for k, v in fi.items():
                    if k not in out or out[k] is None:
                        out[k] = v
    except Exception as e:
        logger.debug("[yfinance_tools] fast_info 合并跳过: %s", e)
    return out


def _pick_core_fields(flat: dict[str, Any]) -> dict[str, Any]:
    picked: dict[str, Any] = {}
    for k in _INFO_KEYS:
        if k in flat and flat[k] is not None:
            picked[k] = flat[k]
    return picked


def get_ticker_info(ticker: str) -> dict[str, Any]:
    """
    获取标的的快照：估值、市值、现价等核心字段（来自 Yahoo / yfinance）。
    """
    try:
        import yfinance as yf  # type: ignore[import-untyped]
    except ImportError as e:
        return {
            "ok": False,
            "error_class": "config",
            "error": f"未安装 yfinance：{e}；请执行 pip install yfinance",
        }

    try:
        sym = _normalize_ticker(ticker)
    except ValueError as e:
        return {"ok": False, "error_class": "config", "error": str(e)}

    try:
        tk = yf.Ticker(sym)
        info: dict[str, Any] = {}
        try:
            raw = tk.info
            if isinstance(raw, dict):
                info = raw
        except Exception as e:
            logger.warning("[yfinance_tools] ticker.info 失败 %s: %s", sym, e)
        info = _merge_fast_into_info(tk, info)
        core = _pick_core_fields(info)
        if not core and not info:
            return {
                "ok": False,
                "error_class": "per_item",
                "error": "无法获取标的信息：代码可能无效或数据源暂无数据",
                "ticker": sym,
            }
        return {
            "ok": True,
            "ticker": sym,
            "core_fields": core,
            "info_key_count": len(info),
        }
    except Exception as e:
        err_s = str(e)
        logger.warning("[yfinance_tools] get_ticker_info 失败 ticker=%s: %s", sym, e)
        low = err_s.lower()
        ec = "transient" if "timeout" in low or "connection" in low else "permanent"
        return {"ok": False, "error_class": ec, "error": err_s, "ticker": sym}


def dispatch_yfinance_core(tool_id: str, **kwargs: Any) -> dict[str, Any]:
    """供 core.native_tools.dispatch_native_tool 转发。"""
    tid = (tool_id or "").strip().lower()
    if tid == "core:yfinance_global_market_hist":
        per = str(kwargs.get("period") or "1mo")
        iv = str(kwargs.get("interval") or "1d")
        return get_global_market_hist(str(kwargs.get("ticker") or ""), period=per, interval=iv)
    if tid == "core:yfinance_ticker_info":
        return get_ticker_info(str(kwargs.get("ticker") or ""))
    return {"ok": False, "error_class": "config", "error": f"未知 yfinance 工具: {tool_id}"}
