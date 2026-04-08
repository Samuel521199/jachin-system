"""
自然周 / 自然月 · 新增用户&付费留存对比

1. 按区间抓取「新增用户留存对比」「新增付费留存对比」→ raw_natural/*.csv（SPA/CDP，逻辑同全量抓取）
2. 读取 CSV 聚合行摘要，分别推送两条飞书 Markdown 卡片（不同步多维表）

定时建议：
  周一 09:00  --mode weekly   （上周一至上周日 vs 上上周一至上上周日）
  每月1日 09:15 --mode monthly （上月1～28 vs 上上月1～28）

配置: config/skills/com.jachin.bi.natural_retention/bi_natural.yaml
也可调用 mcp:atom_bi_natural_retention_collect 仅抓取。
"""
from __future__ import annotations

import csv
import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 每次执行一份独立日志，便于对照终端 [DIFF-LOG] 排查「无法选择日期」等 SPA 问题
NATURAL_LOG_DIR = Path(r"D:\zzz\bi\bi日志\natural日志")
_RUN_LOG = logging.getLogger("bi_natural.run")
_RUN_LOG.propagate = False


def _summarize_cfg_for_log(cfg: dict[str, Any]) -> dict[str, Any]:
    fs = cfg.get("full_spa") or {}
    du = fs.get("direct_urls") or {}
    dist = cfg.get("distribution") or {}
    return {
        "raw_dir_resolved": str(_resolve_raw_natural_dir(cfg)),
        "full_spa": {
            "base_url": str(fs.get("base_url") or "")[:160],
            "cdp_url": str(fs.get("cdp_url") or ""),
            "use_direct_urls": fs.get("use_direct_urls"),
            "direct_url_slugs": sorted(str(k) for k in du.keys()) if isinstance(du, dict) else [],
        },
        "distribution": {
            "lark_chat_id_config": bool(str(dist.get("lark_chat_id") or "").strip()),
            "lark_webhook_placeholder": str(dist.get("lark_webhook_url") or "")[:40],
            "env_BI_LARK_CHAT_ID": bool(str(os.environ.get("BI_LARK_CHAT_ID") or "").strip()),
            "lark_app_id_config": bool(str(dist.get("lark_app_id") or dist.get("app_id") or "").strip()),
            "lark_use_feishu": dist.get("lark_use_feishu"),
        },
        "natural_retention": {
            "scraper_filters": (cfg.get("natural_retention") or {}).get("scraper_filters"),
        },
    }


def _setup_natural_run_file_logging() -> Path:
    NATURAL_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = NATURAL_LOG_DIR / f"natural_{datetime.now():%Y%m%d_%H%M%S}.log"
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    for h in list(_RUN_LOG.handlers):
        _RUN_LOG.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    _RUN_LOG.addHandler(fh)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    _RUN_LOG.addHandler(ch)
    _RUN_LOG.setLevel(logging.DEBUG)
    return log_path


def _resolve_env_placeholders(obj: Any) -> Any:
    """解析 `${ENV_VAR}`，与 bi_daily_report 配置风格一致；未设置环境变量时保留原字符串。"""
    if isinstance(obj, str):
        s = obj.strip()
        if s.startswith("${") and s.endswith("}") and len(s) > 3:
            inner = s[2:-1].strip()
            if inner and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", inner):
                return os.environ.get(inner, obj)
        return obj
    if isinstance(obj, dict):
        return {k: _resolve_env_placeholders(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env_placeholders(x) for x in obj]
    return obj


def _natural_week_compare_ranges(today: date | None = None) -> tuple[tuple[date, date], tuple[date, date]]:
    """周一触发：段1=刚结束的完整自然周（上周一～上周日），段2=再前一周。"""
    t = today or date.today()
    # 本周一
    monday = t - timedelta(days=t.weekday())
    p1_end = monday - timedelta(days=1)
    p1_start = p1_end - timedelta(days=6)
    p2_end = p1_start - timedelta(days=1)
    p2_start = p2_end - timedelta(days=6)
    return (p1_start, p1_end), (p2_start, p2_end)


def _natural_month_28_compare_ranges(today: date | None = None) -> tuple[tuple[date, date], tuple[date, date]]:
    """每月1日触发：段1=上月1日～上月28日；段2=上上月1日～上上月28日（与 BI 等长对比）。"""
    t = today or date.today()
    first_this = t.replace(day=1)
    last_day_prev = first_this - timedelta(days=1)
    m1_first = last_day_prev.replace(day=1)
    p1_start = m1_first
    p1_end = m1_first.replace(day=28)
    m0_last = m1_first - timedelta(days=1)
    m0_first = m0_last.replace(day=1)
    p2_start = m0_first
    p2_end = m0_first.replace(day=28)
    return (p1_start, p1_end), (p2_start, p2_end)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml

        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("[bi_natural] 读取 %s 失败: %s", path, e)
        return {}


def _load_merged_config() -> dict[str, Any]:
    from l3_node.paths import get_app_root

    jachin = Path.home() / ".jachin"
    root = get_app_root()
    nat_paths = [
        jachin / "config" / "skills" / "com.jachin.bi.natural_retention" / "bi_natural.yaml",
        root / "config" / "skills" / "com.jachin.bi.natural_retention" / "bi_natural.yaml",
    ]
    daily_paths = [
        jachin / "config" / "skills" / "com.jachin.bi.daily_report" / "bi_daily_report.yaml",
        root / "config" / "skills" / "com.jachin.bi.daily_report" / "bi_daily_report.yaml",
    ]
    cfg: dict[str, Any] = {}
    for p in nat_paths:
        if p.exists():
            cfg = _load_yaml(p)
            break
    daily: dict[str, Any] = {}
    for p in daily_paths:
        if p.exists():
            daily = _load_yaml(p)
            break
    if not (cfg.get("full_spa") or {}).get("cdp_url") and (daily.get("full_spa") or {}).get("cdp_url"):
        cfg.setdefault("full_spa", {})
        cfg["full_spa"]["cdp_url"] = daily["full_spa"]["cdp_url"]
    if not (cfg.get("full_spa") or {}).get("base_url") and (daily.get("full_spa") or {}).get("base_url"):
        cfg.setdefault("full_spa", {})
        cfg["full_spa"]["base_url"] = daily["full_spa"]["base_url"]
    if not (cfg.get("full_spa") or {}).get("direct_urls") and (daily.get("full_spa") or {}).get("direct_urls"):
        cfg.setdefault("full_spa", {})
        cfg["full_spa"]["direct_urls"] = daily["full_spa"].get("direct_urls") or {}
    if not (cfg.get("full_spa") or {}).get("use_direct_urls") and daily.get("full_spa"):
        cfg.setdefault("full_spa", {})
        if "use_direct_urls" in daily["full_spa"]:
            cfg["full_spa"]["use_direct_urls"] = daily["full_spa"]["use_direct_urls"]
    dist = cfg.get("distribution") or {}
    dd = daily.get("distribution") or {}
    if not dist.get("lark_chat_id") and dd.get("lark_chat_id"):
        cfg.setdefault("distribution", {})
        cfg["distribution"]["lark_chat_id"] = dd["lark_chat_id"]
    if not (dist.get("lark_webhook_url") or "").strip() and (dd.get("lark_webhook_url") or "").strip():
        cfg.setdefault("distribution", {})
        cfg["distribution"]["lark_webhook_url"] = dd["lark_webhook_url"]
    # 无 Webhook 时用 chat_id + IM API，需与 bi_daily_report.lark_bitable 一致的应用凭证
    lb = daily.get("lark_bitable") or {}
    cfg.setdefault("distribution", {})
    dist2 = cfg["distribution"]
    if lb.get("app_id") and not (dist2.get("lark_app_id") or "").strip():
        dist2["lark_app_id"] = lb["app_id"]
    if lb.get("app_secret") and not (dist2.get("lark_app_secret") or "").strip():
        dist2["lark_app_secret"] = lb["app_secret"]
    cfg = _resolve_env_placeholders(cfg)
    daily_resolved = _resolve_env_placeholders(daily)
    lb2 = daily_resolved.get("lark_bitable") or {}
    dist3 = cfg.setdefault("distribution", {})

    def _cred_unresolved(x: str) -> bool:
        s = (x or "").strip()
        if not s:
            return True
        return s.startswith("${") and s.endswith("}") and len(s) > 3

    aid2 = (dist3.get("lark_app_id") or "").strip()
    sec2 = (dist3.get("lark_app_secret") or "").strip()
    if _cred_unresolved(aid2) and lb2.get("app_id"):
        dist3["lark_app_id"] = str(lb2["app_id"]).strip()
    if _cred_unresolved(sec2) and lb2.get("app_secret"):
        dist3["lark_app_secret"] = str(lb2["app_secret"]).strip()
    return cfg


def _ensure_lark_env_from_distribution(cfg: dict[str, Any]) -> None:
    """
    chat_id 推送前注入 LARK_APP_ID/SECRET（与 atom_lark_notifier、im_channels 同源）。
    优先 YAML distribution；~/.jachin 下 MCP 若为空占位符时，此处可补救。
    """
    dist = cfg.get("distribution") or {}
    aid = (dist.get("lark_app_id") or dist.get("app_id") or "").strip()
    sec = (dist.get("lark_app_secret") or dist.get("app_secret") or "").strip()
    if aid and not aid.startswith("${"):
        os.environ.setdefault("LARK_APP_ID", aid)
    if sec and not sec.startswith("${"):
        os.environ.setdefault("LARK_APP_SECRET", sec)
    # 中国飞书 open.feishu.cn；与 config/mcps/atom_lark_notifier 默认一致
    if dist.get("lark_use_feishu") is False:
        os.environ.pop("LARK_USE_FEISHU", None)
    else:
        os.environ.setdefault("LARK_USE_FEISHU", "1")


def _resolve_raw_natural_dir(cfg: dict[str, Any]) -> Path:
    override = ((cfg.get("storage") or {}).get("raw_natural_path") or "").strip()
    if override:
        p = Path(override)
        if p.is_absolute():
            return p
        return Path.home() / ".jachin" / p
    from l3_node.primitives.mcp.mcp_tools.bi.paths import get_bi_raw_natural_dir

    return get_bi_raw_natural_dir()


def _cell_top_line(val: Any) -> str:
    s = (str(val) if val is not None else "").strip()
    if not s:
        return "—"
    lines = re.split(r"[\n\r]+", s)
    return (lines[0] or "—").strip()[:200]


def _is_aggregate_compare_row(row: dict[str, str], date_key: str, scope_key: str | None) -> bool:
    dv = (row.get(date_key) or "").strip()
    if "~" not in dv and "～" not in dv:
        return False
    if not scope_key:
        return True
    sv = (row.get(scope_key) or "").strip().upper()
    return sv in ("ALL", "", "全部汇总", "全平台")


def _find_col(columns: list[str], *candidates: str) -> str | None:
    for cand in candidates:
        for c in columns:
            if cand and (cand.lower() in (c or "").lower() or (c or "") == cand):
                return c
    return None


def _parse_user_compare_cell_analysis(s: str) -> dict[str, Any]:
    """
    解析新增用户留存对比单元格（与 bi_daily_report._parse_compare_pct 同源结构）。
    常见「本期% + 增幅% + 对比期%」；异常「--100%60%」取末段为有效留存。
    """
    raw = (s or "").strip()
    out: dict[str, Any] = {"raw": raw, "p1": None, "p2": None, "delta_pp": None}
    if not raw or raw in ("---", "--", "N/A", "--0%"):
        return out
    matches = re.findall(r"([+-]?\d+\.?\d*)\s*%", raw)
    if not matches:
        return out
    try:
        vals = [float(x) for x in matches]
    except ValueError:
        return out
    if not vals:
        return out
    if len(vals) > 1 and vals[0] >= 99.99:
        out["p1"] = round(vals[-1], 2)
        return out
    out["p1"] = round(vals[0], 2)
    if len(vals) >= 3:
        out["p2"] = round(vals[-1], 2)
        out["delta_pp"] = round(out["p1"] - out["p2"], 2)
    elif len(vals) == 2:
        out["p2"] = round(vals[1], 2)
        out["delta_pp"] = round(out["p1"] - out["p2"], 2)
    return out


def _parse_paid_compare_change(s: str) -> float | None:
    """与 bi_daily_report._parse_paid_compare_pct 一致：取付费留存对比「环比变化率」。"""
    st = (s or "").strip()
    if not st or st in ("-", "—", "－", "---", "--", "N/A", "n/a"):
        return None
    if re.fullmatch(r"[\s\-—－]+", st):
        return None
    matches = re.findall(r"([+-]?\d+\.?\d*)\s*%", st)
    if not matches:
        return None
    try:
        vals = [float(x) for x in matches]
    except ValueError:
        return None

    def _is_neg100(x: float) -> bool:
        return abs(x + 100.0) < 0.02

    if len(vals) >= 3:
        if abs(vals[0]) < 1e-5 and _is_neg100(vals[1]):
            return None
        return round(vals[1], 2)
    if len(vals) == 2 and _is_neg100(vals[0]):
        return None
    if len(vals) == 2:
        return round(vals[0], 2)
    return round(vals[0], 2)


def _trend_word_delta_pp(d: float, *, eps: float = 0.05) -> str:
    if d > eps:
        return "回升"
    if d < -eps:
        return "走弱"
    return "基本持平"


def _trend_word_change_pct(ch: float, *, eps: float = 0.05) -> str:
    if ch > eps:
        return "改善"
    if ch < -eps:
        return "承压"
    return "基本持平"


def _analysis_user_retention_markdown(agg: dict[str, str], cols: list[str], compare_word: str) -> str:
    """基于聚合行生成「新增用户留存对比」文字解读。"""
    pairs = [
        ("T+1 留存", ["T+1 留存率", "这周T+1留存率", "这周留存率", "本周留存率", "T+1留存率"]),
        ("T+3 留存", ["T+3 留存率", "这周T+3留存率", "T+3留存率", "T+4 留存率"]),
        ("T+5 留存", ["T+5 留存率", "这周T+5留存率", "T+5留存率", "T+6 留存率", "这周T+6留存率"]),
    ]
    lines: list[str] = [f"### 数据解读（{compare_word}环比 · 全平台汇总）", ""]
    parsed: list[tuple[str, dict[str, Any]]] = []
    for title, cands in pairs:
        col = _find_col(cols, *cands)
        if not col:
            lines.append(f"- **{title}**：表中未匹配到对应列名，请从下表核对。")
            continue
        d = _parse_user_compare_cell_analysis(str(agg.get(col, "") or ""))
        parsed.append((title, d))
        if d.get("p1") is None:
            lines.append(f"- **{title}**：无有效解析值（空或「-」）。")
            continue
        p1, p2, dd = d.get("p1"), d.get("p2"), d.get("delta_pp")
        if p2 is not None and dd is not None:
            tw = _trend_word_delta_pp(float(dd))
            lines.append(
                f"- **{title}**：本期 **{p1:.2f}%** vs 对比期 **{p2:.2f}%**，"
                f"差 **{dd:+.2f} 个百分点**，相对对比期**{tw}**。"
            )
        else:
            lines.append(f"- **{title}**：本期约 **{p1:.2f}%**（导出未含完整双期百分比，未算差值）。")

    with_dd = [d for _, d in parsed if d.get("delta_pp") is not None]
    if with_dd:
        ups = sum(1 for d in with_dd if float(d["delta_pp"]) > 0.05)
        downs = sum(1 for d in with_dd if float(d["delta_pp"]) < -0.05)
        flat = len(with_dd) - ups - downs
        lines.append("")
        lines.append(
            f"**小结**：在可算差值的指标中，**{ups}** 项优于对比期、**{downs}** 项弱于对比期、**{flat}** 项基本持平。"
        )
    lines.append("")
    return "\n".join(lines)


def _analysis_paid_retention_markdown(agg: dict[str, str], cols: list[str], compare_word: str) -> str:
    """基于聚合行生成「新增付费留存对比」文字解读（列为环比变化率）。"""
    pairs = [
        ("T+1 环比", ["T+1 留存率", "这周T+1留存率", "T+1留存率"]),
        ("T+2 环比", ["T+2 留存率", "这周T+2留存率", "T+2留存率"]),
        ("T+3 环比", ["T+3 留存率", "这周T+3留存率", "T+3留存率"]),
    ]
    lines: list[str] = [
        f"### 数据解读（{compare_word}环比 · 全平台汇总）",
        "",
        "_说明：下列为 BI 导出中的**相对对比期的变化率**（非绝对留存率）；「-」表示无有效数据。_",
        "",
    ]
    changes: list[float | None] = []
    for title, cands in pairs:
        col = _find_col(cols, *cands)
        if not col:
            lines.append(f"- **{title}**：未匹配到列名。")
            changes.append(None)
            continue
        ch = _parse_paid_compare_change(str(agg.get(col, "") or ""))
        changes.append(ch)
        if ch is None:
            lines.append(f"- **{title}**：无有效变化率。")
            continue
        tw = _trend_word_change_pct(ch)
        lines.append(f"- **{title}**：变化率 **{ch:+.2f}%**，相对对比期留存表现**{tw}**。")

    valid = [x for x in changes if x is not None]
    if valid:
        pos = sum(1 for x in valid if x > 0.05)
        neg = sum(1 for x in valid if x < -0.05)
        mid = len(valid) - pos - neg
        lines.append("")
        lines.append(
            f"**小结**：有效变化率 **{len(valid)}** 项中，**{pos}** 项为正、**{neg}** 项为负、**{mid}** 项接近零。"
        )
    lines.append("")
    return "\n".join(lines)


def _markdown_table_from_csv(
    path: Path,
    heading: str,
    period_lines: str,
    *,
    analysis_kind: str | None = None,
    compare_word: str = "周",
) -> str:
    """
    analysis_kind: 'user' | 'paid' | None
    compare_word: 卡片内文案用「周」或「月」
    """
    if not path.is_file():
        return f"## {heading}\n\n**区间**：{period_lines}\n\n_未找到文件 `{path.name}`_\n"
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    except Exception as e:
        return f"## {heading}\n\n读取失败：`{e}`\n"

    if not rows:
        return f"## {heading}\n\n**区间**：{period_lines}\n\n_CSV 无数据行_\n"

    fieldnames = list(rows[0].keys())
    dk = next((c for c in fieldnames if "日期对比" in c or c == "日期对比"), fieldnames[0] if fieldnames else "")
    sk = next((c for c in fieldnames if "统计范围" in c or "渠道" in c), None)

    agg = None
    for r in rows:
        if _is_aggregate_compare_row(r, dk, sk):
            agg = r
            break
    if agg is None:
        agg = rows[0]

    skip = {dk, sk or ""}
    cols = [c for c in fieldnames if c and c not in skip][:12]

    analysis_block = ""
    if analysis_kind == "user":
        analysis_block = _analysis_user_retention_markdown(agg, list(fieldnames), compare_word)
    elif analysis_kind == "paid":
        analysis_block = _analysis_paid_retention_markdown(agg, list(fieldnames), compare_word)

    lines = [
        f"## {heading}",
        "",
        f"**区间**：{period_lines}",
        "",
    ]
    if analysis_block:
        lines.append(analysis_block)
        lines.append("---")
        lines.append("")
    lines.extend(
        [
            f"**聚合行** `{_cell_top_line(agg.get(dk))}`",
            "",
            "| 指标 | 摘要（首行） |",
            "| --- | --- |",
        ]
    )
    for c in cols:
        lines.append(f"| {c} | {_cell_top_line(agg.get(c))} |")
    lines.append("")
    lines.append(f"_源文件：`{path.name}`_")
    return "\n".join(lines)


def run_bi_natural_retention(
    mode: str = "weekly",
    *,
    skip_collect: bool = False,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    mode: weekly | monthly
    skip_collect: True 时只读已有 raw_natural CSV 并推送（不跑浏览器）
    """
    log_path = _setup_natural_run_file_logging()
    _RUN_LOG.info("======== 自然留存任务开始 ========")
    _RUN_LOG.info("日志文件: %s", log_path)
    _RUN_LOG.info("参数 mode=%r skip_collect=%r", mode, skip_collect)

    cfg = config if isinstance(config, dict) and config else _load_merged_config()
    _RUN_LOG.debug("合并后配置摘要: %s", json.dumps(_summarize_cfg_for_log(cfg), ensure_ascii=False, indent=2))

    raw_dir = _resolve_raw_natural_dir(cfg)
    _RUN_LOG.info("raw_natural 目录: %s", raw_dir.resolve())

    mode = (mode or "weekly").strip().lower()
    if mode == "monthly":
        (p1a, p1b), (p2a, p2b) = _natural_month_28_compare_ranges()
        label = "自然月环比（上月1～28 vs 上上月1～28）"
        compare_word = "月"
    else:
        (p1a, p1b), (p2a, p2b) = _natural_week_compare_ranges()
        label = "自然周环比（上周 vs 上上周）"
        compare_word = "周"

    p1s, p1e = p1a.isoformat(), p1b.isoformat()
    p2s, p2e = p2a.isoformat(), p2b.isoformat()
    period_lines = f"段1 `{p1s}` ~ `{p1e}` · 段2 `{p2s}` ~ `{p2e}`"
    _RUN_LOG.info("对比区间: 段1 %s ~ %s | 段2 %s ~ %s", p1s, p1e, p2s, p2e)
    _RUN_LOG.info("任务标签: %s", label)

    base_out: dict[str, Any] = {
        "log_file": str(log_path),
        "mode": mode,
        "label": label,
        "period1": [p1s, p1e],
        "period2": [p2s, p2e],
        "raw_dir": str(raw_dir.resolve()),
    }

    collect_result: dict[str, Any] = {}
    if not skip_collect:
        from l3_node.primitives.mcp.mcp_tools.bi.tool_natural_retention_collect import run_natural_retention_compare_collect
        from l3_node.primitives.mcp.mcp_tools.bi.spa_collector import parse_direct_url_map_from_full_spa

        fs = cfg.get("full_spa") or {}
        use_d = bool(fs.get("use_direct_urls"))
        dmap = parse_direct_url_map_from_full_spa(fs if use_d else None)
        _RUN_LOG.info(
            "即将抓取: use_direct_urls=%s direct_map_slugs=%s base_url=%r cdp_url=%r",
            use_d,
            sorted(dmap.keys()) if dmap else [],
            (fs.get("base_url") or "")[:120],
            (fs.get("cdp_url") or ""),
        )
        _RUN_LOG.debug(
            "direct_url 目标（仅两表）: user=%s paid=%s",
            (dmap or {}).get("stats_retention_user_compare", "")[:100] if dmap else "",
            (dmap or {}).get("stats_retention_paid_compare", "")[:100] if dmap else "",
        )

        def _progress_cb(idx: int, total: int, slug: str, r: dict[str, Any]) -> None:
            err = (r.get("error") or "").strip()
            _RUN_LOG.info(
                "抓取进度 [%d/%d] slug=%s status=%s err_摘要=%s",
                idx,
                total,
                slug,
                r.get("status"),
                err[:500] if err else "",
            )
            if r.get("status") != "success" and err:
                _RUN_LOG.warning(
                    "抓取失败 slug=%s 完整错误(截断2500字): %s",
                    slug,
                    err[:2500],
                )
                _RUN_LOG.warning(
                    "定位提示: 控制台 [DIFF-LOG] 搜「视觉序打标」可看 DOM 总数/跳过原因；"
                    "搜「回退定位」表示已走 Playwright 可见序。文件日志: %s",
                    log_path,
                )
            _RUN_LOG.debug("抓取进度详情 slug=%s payload=%s", slug, json.dumps(r, ensure_ascii=False)[:8000])

        nr_cfg = cfg.get("natural_retention") or {}
        sf = nr_cfg.get("scraper_filters")
        scraper_fo = sf if isinstance(sf, dict) and sf else None
        if scraper_fo:
            _RUN_LOG.info("抓取 filters 覆盖（来自 natural_retention.scraper_filters）: %s", scraper_fo)
        _RUN_LOG.info(
            "日期区间填写策略: 默认 date_range_compare_use_visual_order=True（左→右=段1/段2）；"
            "可在 bi_natural.yaml 的 natural_retention.scraper_filters 覆盖"
        )
        _RUN_LOG.info(
            "付费/用户留存对比: 填完每段不按 Escape（date_range_compare_no_escape_after_fill）；"
            "避免 Heron-BI 整段卸载筛选区。面板一般选完日期会自行关闭"
        )
        _RUN_LOG.info("开始 run_natural_retention_compare_collect（终端 [DIFF-LOG] 会输出 Playwright 每步）…")
        collect_result = run_natural_retention_compare_collect(
            p1s,
            p1e,
            p2s,
            p2e,
            raw_dir=raw_dir,
            base_url=(fs.get("base_url") or "").strip() or None,
            cdp_url=(fs.get("cdp_url") or "").strip() or None,
            direct_url_map=dmap,
            auto_ingest=False,
            progress_cb=_progress_cb,
            scraper_filter_overrides=scraper_fo,
        )
        _RUN_LOG.info(
            "抓取结束 ok=%s ok_count=%s fail_count=%s failed_slugs=%s",
            collect_result.get("ok"),
            collect_result.get("ok_count"),
            collect_result.get("fail_count"),
            collect_result.get("failed_slugs"),
        )
        _RUN_LOG.debug("抓取完整结果: %s", json.dumps(collect_result, ensure_ascii=False, indent=2))
        if not collect_result.get("ok"):
            _RUN_LOG.error("抓取失败，请查看上文与 %s；常见原因：CDP 未开/未登录 BI、日期控件未就绪、直链与登录态不一致", log_path)
            return {
                **base_out,
                "success": False,
                "error": "抓取未完成",
                "collect": collect_result,
            }

    user_csv = raw_dir / "stats_retention_user_compare.csv"
    paid_csv = raw_dir / "stats_retention_paid_compare.csv"
    for p, name in ((user_csv, "user_compare"), (paid_csv, "paid_compare")):
        if p.is_file():
            _RUN_LOG.info("CSV %s 存在 size=%s bytes", name, p.stat().st_size)
        else:
            _RUN_LOG.warning("CSV 缺失: %s", p)

    md_user = _markdown_table_from_csv(
        user_csv,
        f"📊 {label} · 新增用户留存对比",
        period_lines,
        analysis_kind="user",
        compare_word=compare_word,
    )
    md_paid = _markdown_table_from_csv(
        paid_csv,
        f"💰 {label} · 新增付费留存对比",
        period_lines,
        analysis_kind="paid",
        compare_word=compare_word,
    )
    _RUN_LOG.debug("用户卡片 Markdown 长度=%d 前400字:\n%s", len(md_user), md_user[:400])
    _RUN_LOG.debug("付费卡片 Markdown 长度=%d 前400字:\n%s", len(md_paid), md_paid[:400])

    dist = cfg.get("distribution") or {}
    chat_id = (dist.get("lark_chat_id") or os.environ.get("BI_LARK_CHAT_ID") or "").strip()
    webhook = (dist.get("lark_webhook_url") or os.environ.get("BI_LARK_WEBHOOK_URL") or "").strip()
    _RUN_LOG.info(
        "推送: chat_id=%s webhook_configured=%s",
        chat_id[:20] + "…" if len(chat_id) > 20 else chat_id,
        bool(webhook and not str(webhook).strip().startswith("${")),
    )
    _ensure_lark_env_from_distribution(cfg)
    if (chat_id or "").strip() and not (
        (webhook or "").strip() and not str(webhook).strip().startswith("${")
    ):
        _RUN_LOG.info(
            "Lark IM 模式: LARK_APP_ID 已%s LARK_USE_FEISHU=%s",
            "设置" if os.environ.get("LARK_APP_ID") else "缺失(请配 distribution.lark_app_id 或 .env)",
            os.environ.get("LARK_USE_FEISHU", ""),
        )

    push_results: list[dict[str, Any]] = []
    try:
        from l3_node.primitives.mcp.mcp_tools.bi.tool_lark_notifier import send_lark_markdown

        r1 = send_lark_markdown(
            webhook_url=webhook,
            markdown_content=md_user,
            title=f"BI · {label} · 用户留存",
            chat_id=chat_id or None,
        )
        push_results.append({"card": "user", **r1})
        _RUN_LOG.info("用户留存卡片 send_lark_markdown -> %s", r1)
        r2 = send_lark_markdown(
            webhook_url=webhook,
            markdown_content=md_paid,
            title=f"BI · {label} · 付费留存",
            chat_id=chat_id or None,
        )
        push_results.append({"card": "paid", **r2})
        _RUN_LOG.info("付费留存卡片 send_lark_markdown -> %s", r2)
    except Exception as e:
        logger.exception("[bi_natural] Lark 推送失败: %s", e)
        _RUN_LOG.exception("Lark 推送异常: %s", e)
        return {
            **base_out,
            "success": False,
            "error": str(e),
            "collect": collect_result,
            "push": push_results,
        }

    ok_push = all(x.get("status") == "success" for x in push_results)
    _RUN_LOG.info("======== 任务结束 success=%s ========", ok_push)
    return {
        **base_out,
        "success": ok_push,
        "collect": collect_result,
        "push": push_results,
    }


def run_bi_natural_retention_cli(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="自然周/月留存对比抓取与 Lark 双卡片")
    p.add_argument("--weekly", action="store_true", help="自然周（上周 vs 上上周）")
    p.add_argument("--monthly", action="store_true", help="自然月（上月1～28 vs 上上月1～28）")
    p.add_argument("--skip-collect", action="store_true", help="仅推送，不抓取")
    args = p.parse_args(argv)
    mode = "monthly" if args.monthly else "weekly"
    if args.monthly and args.weekly:
        print("请只选 --weekly 或 --monthly 之一")
        return 2
    out = run_bi_natural_retention(mode=mode, skip_collect=args.skip_collect)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("success") else 1
