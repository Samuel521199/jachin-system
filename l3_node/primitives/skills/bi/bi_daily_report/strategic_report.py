"""
BI 战略大战报（长文深度分析）

分析规范以 **docs/bi_daily_report/STRATEGIC_REPORT_ANALYSIS_SPEC.md**（v6.4 冲 DAU · 不谈 RTP）为 SSOT，
运行时加载为 System Prompt；缺失时回退 _STRATEGIC_ANALYSIS_SPEC_FALLBACK。

User 侧注入：bi_project 背景、T vs T-1 字段摘要（默认 DuckDB）、指标 JSON、表摘要。

数据来源：默认 DuckDB `bi.duckdb`；可配置回退 output/raw CSV。仪表盘小分析（Step 4a）不在此模块。
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _get_bi_raw_dir() -> Path:
    """延迟导入避免循环依赖"""
    from l3_node.primitives.mcp.mcp_tools.bi.paths import get_bi_raw_dir
    return get_bi_raw_dir()

# 战略分析 DuckDB 表 slug 清单（SPA 抓取 ingest 后的 bi_* 表，大战报主数据源）
STRATEGIC_DUCKDB_SLUGS: tuple[str, ...] = (
    "daily_ops_summary",
    "stats_user_dau",
    "stats_user_new",
    "stats_retention_user",
    "stats_retention_paid",
    "stats_retention_user_compare",
    "stats_retention_paid_compare",
    "daily_acquisition",
    "stats_recharge",
    "recharge_status",
    "prod_sales",
    "stats_game_daily",
    "stats_game_core",
    "stats_game_compare",
    "alert_gold",
    "alert_traffic",
)

_DUCKDB_INTERNAL_COLS = frozenset({"_ingested_at", "_ingested_date"})


def _resolve_strategic_data_source(cfg: dict[str, Any] | None) -> str:
    """大战报数据源：strategic_report.data_source > analysis_data_source > duckdb（默认）。"""
    c = cfg or {}
    sr = c.get("strategic_report") or {}
    src = (sr.get("data_source") or c.get("analysis_data_source") or "duckdb").strip().lower()
    if src in ("duckdb", "db", "bi.duckdb"):
        return "duckdb"
    if src in ("lark", "bitable"):
        return "lark"
    return "raw"


def _duckdb_conn_query_rows(slug: str, date_from: str | None = None, date_to: str | None = None) -> list[dict[str, Any]]:
    """从 bi.duckdb 查询 slug 表，返回行 dict 列表。"""
    try:
        from l3_node.primitives.mcp.mcp_tools.bi.data_store import _get_conn
        from l3_node.primitives.skills.bi.bi_daily_report.main_skill import _query_table

        conn = _get_conn()
        try:
            return _query_table(conn, slug, date_from=date_from, date_to=date_to)
        finally:
            conn.close()
    except Exception as e:
        logger.debug("[Strategic] DuckDB 查询 %s 失败: %s", slug, e)
        return []


def _max_business_date_in_duckdb_slug(slug: str) -> str | None:
    """取 slug 表业务日期列的最大 ISO 日期。"""
    from l3_node.primitives.skills.bi.bi_daily_report.main_skill import (
        _norm_strategic_csv_date,
        _strategic_pick_date_column,
    )

    rows = _duckdb_conn_query_rows(slug)
    if not rows:
        return None
    dc = _strategic_pick_date_column([k for k in rows[0].keys() if k not in _DUCKDB_INTERNAL_COLS])
    if not dc:
        return None
    dates = [_norm_strategic_csv_date(r.get(dc)) for r in rows]
    dates = [d for d in dates if d]
    return max(dates) if dates else None


def _detect_report_date_from_duckdb() -> tuple[str, str]:
    """从 DuckDB 推断最新完整业务日 T。返回 (YYYY-MM-DD, MM/DD)。"""
    priority = (
        "daily_ops_summary",
        "stats_user_dau",
        "stats_user_new",
        "prod_sales",
        "stats_recharge",
        "stats_retention_user",
        "daily_acquisition",
    )
    latest: str | None = None
    for slug in priority:
        d = _max_business_date_in_duckdb_slug(slug)
        if d and (latest is None or d > latest):
            latest = d
    if not latest:
        for slug in STRATEGIC_DUCKDB_SLUGS:
            if slug in priority:
                continue
            d = _max_business_date_in_duckdb_slug(slug)
            if d and (latest is None or d > latest):
                latest = d
    if latest:
        try:
            y, m, d = latest.split("-")
            return (latest, f"{int(m):02d}/{int(d):02d}")
        except ValueError:
            pass
    fallback = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    return (fallback, (datetime.now() - timedelta(days=1)).strftime("%m/%d"))


def _format_duckdb_rows_preview(slug: str, rows: list[dict[str, Any]], *, max_rows: int = 12) -> list[str]:
    """将 DuckDB 行格式化为 LLM 可读摘要行。"""
    if not rows:
        return []
    lines = [f"### DuckDB `{slug}`（{len(rows)} 行抽样，最多 {max_rows} 行）"]
    cols = [c for c in rows[0].keys() if c not in _DUCKDB_INTERNAL_COLS]
    show_cols = cols[:8]
    for r in rows[:max_rows]:
        parts = [f"{k}: {str(r.get(k, ''))[:40]}" for k in show_cols]
        lines.append("  - " + " | ".join(parts))
    if len(rows) > max_rows:
        lines.append(f"  - …（另有 {len(rows) - max_rows} 行未展示）")
    return lines


def _load_duckdb_strategic_summary(
    t1_iso: str | None = None,
    *,
    lookback_days: int = 21,
) -> str:
    """从 bi.duckdb 各 slug 表加载战略分析摘要（完整 SPA 入库数据，非提纯 CSV）。"""
    try:
        from l3_node.primitives.mcp.mcp_tools.bi.paths import get_bi_duckdb_path
    except ImportError:
        get_bi_duckdb_path = lambda: Path.home() / ".jachin" / "client_volumes" / "bi_data" / "duckdb" / "bi.duckdb"  # noqa: E731

    db_path = get_bi_duckdb_path()
    lines: list[str] = [
        _RAW_STRATEGIC_PREAMBLE.rstrip(),
        "",
        f"**数据源**：DuckDB `{db_path}`（SPA 抓取全量入库，非 output 提纯 CSV）",
        "",
    ]
    if t1_iso:
        try:
            t0 = datetime.strptime(t1_iso[:10], "%Y-%m-%d")
            date_from = (t0 - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
            date_to = t1_iso[:10]
        except ValueError:
            date_from, date_to = None, None
    else:
        date_from, date_to = None, None

    any_data = False
    for slug in STRATEGIC_DUCKDB_SLUGS:
        rows = _duckdb_conn_query_rows(slug, date_from=date_from, date_to=date_to)
        if not rows and date_from:
            rows = _duckdb_conn_query_rows(slug)
        if not rows:
            continue
        any_data = True
        lines.extend(_format_duckdb_rows_preview(slug, rows))
        lines.append("")

    if not any_data:
        return f"（DuckDB 无可用表数据：{db_path}）"
    return "\n".join(lines).strip()
_STRATEGIC_KEY_FILES = [
    "01_用户活跃_增幅表.csv",
    "02_用户活跃_日期数量表.csv",
    "03a_用户活跃_DAU渠道来源.csv",
    "03b_用户活跃_DNU渠道来源.csv",
    "13_用户活跃_新增设备表.csv",
    "04_留存_次留表.csv",
    "05_留存_付费用户次留表.csv",
    "06_留存_周环比表.csv",
    "07_留存_付费用户周环比表.csv",
    "08_消耗_每日表.csv",
    "08b_消耗_金币_渠道层.csv",
    "09_消耗_按游戏表.csv",
    "10_充值_付费人数按SKU.csv",
    "11_充值_付费金额按SKU.csv",
    "14_充值_付费人数金额增幅表.csv",
    "15_消耗_Arup表.csv",
    "16_消耗_Arppu表.csv",
    "17_游戏_完成局数.csv",
    "18_游戏_用户获胜.csv",
    "19_游戏_RTP_GGR.csv",
]

# 无对应 output 表名、仅 raw 存在的 slug（漏斗与告警）
_STRATEGIC_RAW_ONLY_SLUGS = ["daily_acquisition", "alert_gold", "alert_traffic"]

# 注入 User 摘要开头，防止 LLM 误读千分位或混淆用户/机器人金币列（与 STRATEGIC_REPORT_ANALYSIS_SPEC §数据口径与数值防错 对齐）
_RAW_STRATEGIC_PREAMBLE = """### 【raw 摘要 · 口径提示】（生成战报时必须遵守）
- **`stats_user_dau`「全部汇总」** 的当日金币产出/消耗：按真实位数换算（**1 亿 = 10⁸**）。例如 `53,179,122` ≈ **5318 万** 或 **约 0.53 亿**，**禁止**误写为 5.32 亿。
- **`prod_sales`「全部汇总」**：**用户金币产出/消耗** 与 **机器人金币产出/消耗** 为不同列，须**分开**写；无合并定义时不要自行相加统称「全平台总产出」。
- **`daily_acquisition`**：每行 = **单条落地 URL**，非全站唯一漏斗总和。
- **`stats_retention_user`**：**数据日 T 当行的「次日 T+1 留存」为 0/NaN% 是日历常态**（要等 T+1 自然日结束才有数），**禁止**写成 ETL 故障或留存崩盘；分析次留只用 **T-1 及更早** 已闭合日期（见 User「留存日历」）。
- **局数**：优先与 **`stats_game_daily`（完成局数）** 对齐；若用 `stats_game_core` 请注明表名。

"""


_METRICS_KEY_MAP = {
    "dau": "日活", "dnu": "新增用户", "dau_pct": "日活增幅", "dnu_pct": "新增用户增幅",
    "new_devices": "新增设备数", "dnu_per_device": "新增用户设备比",
    "paid_count": "付费人数", "paid_amount": "付费金额", "paid_count_pct": "付费人数增幅", "paid_amount_pct": "付费金额增幅",
    "arpu": "ARPU", "arppu": "ARPPU", "game_rounds": "游戏局数", "game_rounds_per_user": "人均局数",
    "win_count": "获胜次数", "win_rate": "胜率", "rtp": "返币率", "ggr": "GGR",
    "date": "统计日期",
    "recharge_user_rtp_pct": "充值用户回报率RTP", "participating_users": "参与用户数", "participating_bots": "参与机器人数",
    "old_user_asset_change": "老用户总资产变动", "new_user_asset_change": "新用户总资产变动",
    "old_user_prod": "老用户金币产出", "new_user_prod": "新用户金币产出",
    "old_user_cons": "老用户金币消耗", "new_user_cons": "新用户金币消耗",
    "register_to_table_ratio": "注册进桌比", "dnu_register_t1": "当日注册数", "table_entry_t1": "进桌人数",
}


def _metrics_to_chinese(metrics: dict[str, Any]) -> dict[str, Any]:
    """将英文字段名转为中文，避免 LLM 输出代码变量名"""
    out: dict[str, Any] = {}
    for k, v in (metrics or {}).items():
        if k.startswith("_"):
            continue
        cn = _METRICS_KEY_MAP.get(k, k)
        if isinstance(v, dict):
            out[cn] = _metrics_to_chinese(v)
        else:
            out[cn] = v
    return out


# qwen3.5 偶发将「思考链/自我修正/占位段落」写入 content（即使 enable_thinking=False）
_LEAK_PATTERN_PARTS = (
    r"此处模拟思考",
    r"此处修正思考",
    r"模拟思考过程",
    r"模拟纠错",
    r"模拟过程中的",
    r"实际输出应直接陈述",
    r"正式输出保持简洁",
    r"正式输出修正",
    r"正式输出段落",
    r"正式内容片段",
    r"以下为正式内容",
    r"最终决定跳过模拟",
    r"放弃模拟纠错",
    r"自我纠正",
    r"自我修正",
    r"Self-Correction",
    r"self-correction",
    r"thought process",
    r"thought block",
    r"Final Check on Constraints",
    r"Final Polish",
    r"Ready to Output",
    r"placeholder for the actual table",
    r"In the final output",
    r"In this thought block",
    r"最后一次检查",
    r"最终版逻辑",
    r"最终确认\)",
    r"再次自我纠正",
    r"好了,下面是正式",
    r"好了,直接写",
    r"\(以下为正式",
    r"直接写正确的\)",
    r"直接生成正确文本\)",
    r"好吧,直接写",
    r"\*\(注:\s*此处",
    r"\*\(Note:",
    r"\*\(修正后\)",
    r"\*\*\(正式输出",
    r"\*\(正式输出",
    r"\*\*\(正式内容",
    r"\*\(正式内容",
    r"\*\*\(截断\)",
    r"\*\*\(续\)",
    r"\*\*\(完整\)",
    r"\*\*\(修正\)",
    r"\*\*\(最终\)",
    r"\*\*\(输出\)",
    r"\*\*\(结束\)",
    r"\*\*\(Done\)",
    r"\*\*\(End\)",
    r"\*\*\(Stop\)",
    r"\*\*\(Finish\)",
    r"\*\*\(Complete\)",
    r"\*\*\(OK\)",
    r"\*\*\(Yes\)",
    r"\*\*\(No\)",
    r"\*\*\(Maybe\)",
    r"\*\*\(Unknown\)",
    r"\*\*\(Error\)",
    r"\*\*\(Warning\)",
    r"\*\*\(Info\)",
    r"\*\*\(Debug\)",
    r"\*\*\(Trace\)",
    r"\*\*\(Log\)",
    r"\*\*\(Record\)",
    r"\*\*\(Data\)",
    r"\*\*\(Metric\)",
    r"\*\*\(KPI\)",
    r"Wait,",
    r"re-read carefully",
    r"Let me re-read",
    r"checking Input",
    r"hallucinating",
    r"正式输出应",
    r"Correction:",
    r"Data Correction on",
)
_THINKING_LEAK_RE = re.compile("|".join(_LEAK_PATTERN_PARTS), re.IGNORECASE)

# 大战报章节锚点（用于按节截断污染尾部）
_STRATEGIC_SECTION_MARKERS: tuple[str, ...] = (
    "## 一、",
    "##一、",
    "## 二、",
    "##二、",
    "## 三、",
    "##三、",
    "## 四、",
    "##四、",
    "## 五、",
    "##五、",
)


def _count_numbered_sections(text: str) -> int:
    """统计「一、」～「五、」大节出现数（兼容 ## 前缀）。"""
    if not text:
        return 0
    compact = text.replace(" ", "")
    n = 0
    for ch in "一二三四五":
        if (
            re.search(rf"(?:^|\n)\s*{ch}、", text)
            or f"##{ch}、" in compact
            or f"## {ch}、" in text
        ):
            n += 1
    return n


def _structure_failure_reasons(text: str) -> list[str]:
    """返回结构校验未通过原因（供日志与降级交付）。"""
    reasons: list[str] = []
    if not (text or "").strip():
        return ["empty"]
    if not _has_opening_section(text):
        reasons.append("missing_ch1")
    sec_n = _count_numbered_sections(text)
    if sec_n < 5:
        reasons.append(f"sections={sec_n}/5")
    s5_ok = any(
        k in text
        for k in (
            "增长战略",
            "战略与决策",
            "战略决策",
            "战术清单",
            "决策方向",
            "决策优先",
            "待验证",
            "🧭",
            "🔍",
        )
    )
    if not s5_ok:
        reasons.append("missing_s5_decision_markers")
    theme_hits = sum(
        1 for k in ("买量", "漏斗", "生态", "晴雨表", "大盘")
        if k in text
    )
    if theme_hits < 3:
        reasons.append(f"theme_hits={theme_hits}/3")
    return reasons


def _has_five_panel_structure(text: str) -> bool:
    """v6 五板块大战报：须含第一～第五大节 + 第五节决策语义。"""
    return len(_structure_failure_reasons(text)) == 0

# v6 首章标题
_STRATEGIC_OPENING_SECTION_HINTS: tuple[str, ...] = (
    "大盘晴雨表",
    "晴雨表",
    "今日三句话",
    "人话摘要",
    "执行摘要",
)


def _has_opening_section(text: str) -> bool:
    """大战报是否包含第一章（v6 大盘晴雨表 或兼容旧版首章）。"""
    if not (text or "").strip():
        return False
    compact = text.replace(" ", "")
    has_ch1 = (
        "##一、" in compact
        or "## 一、" in text
        or bool(re.search(r"(?:^|\n)\s*一、", text))
    )
    if not has_ch1:
        return False
    if any(h in text for h in _STRATEGIC_OPENING_SECTION_HINTS):
        return True
    return len(text) >= 400


_CHAPTER_HEAD_RE = re.compile(r"^[一二三四五]、")
_BLOCK_LABEL_RE = re.compile(r"^[🎯📊💡🧭🔍]")


def _is_chapter_heading(line: str) -> bool:
    return bool(_CHAPTER_HEAD_RE.match(line.strip()))


def _normalize_strategic_report_line(raw_ln: str) -> str | None:
    """单行归一化；返回 None 表示丢弃该行。"""
    ln = raw_ln.rstrip()
    if re.fullmatch(r"\s*-{3,}\s*", ln):
        return None
    if not ln.strip():
        return None

    if ln.lstrip().startswith(">"):
        ln = re.sub(r"^\s*>\s*", "", ln)

    hm = re.match(r"^(#{1,6})\s+(.*)$", ln)
    if hm:
        title = re.sub(r"\*\*([^*]+)\*\*", r"\1", hm.group(2).strip())
        if title in ("人话结论",) or title.startswith("人话结论"):
            return "📊 【结论】："
        if "要点" in title:
            return "💡 【要点】："
        return title

    if re.match(r"^#{1,6}\s", ln):
        ln = re.sub(r"^#{1,6}\s+", "", ln)

    ln = re.sub(r"`([^`]+)`", r"\1", ln)
    if re.match(r"^\*\*数据源\*\*", ln) or re.match(r"^数据源[：:]", ln.strip()):
        return None

    sub = re.match(
        r"^(人话结论|分析要点(?:（必须覆盖）)?|运营\s*/\s*数值|买量\s*/\s*市场|技术\s*/\s*数仓)\s*$",
        ln.strip(),
    )
    if sub:
        label = sub.group(1)
        if "结论" in label:
            return "📊 【结论】："
        if "要点" in label:
            return "💡 【要点】："
        return None

    if re.match(r"^定调[：:]", ln.strip()) and "🎯" not in ln:
        ln = re.sub(r"^定调[：:]\s*", "🎯 【定调】：", ln.strip())
    elif re.match(r"^结论[：:]", ln.strip()) and "📊" not in ln:
        ln = re.sub(r"^结论[：:]\s*", "📊 【结论】：", ln.strip())
    elif re.match(r"^要点[：:]", ln.strip()) and "💡" not in ln:
        ln = re.sub(r"^要点[：:]\s*", "💡 【要点】：", ln.strip())

    if re.match(r"^\[(?:紧急|重要)\]\s*/\s*", ln.strip()):
        ln = re.sub(
            r"^\[(紧急|重要)\]\s*/\s*\[([^\]]+)\]\s*([^/\n]+?)(?:\s*/\s*([^/\n]+))?\s*$",
            r"【\1·\2·\3】",
            ln.strip(),
        )

    if re.match(r"^【(?:紧急|重要)", ln.strip()) and not _BLOCK_LABEL_RE.match(ln.strip()):
        ln = re.sub(r"^【(?:紧急|重要)[^】]*】\s*", "", ln.strip())

    ln = re.sub(r"\*\*([^*]+)\*\*", r"\1", ln)
    return ln.strip()


def _format_strategic_report_spacing(lines: list[str]) -> str:
    """
    章内紧凑（单行间距），章间双空行（一眼分节）。
    规则：「一、」～「五、」前插入 2 行空白；章内 🎯📊💡 与列表项不再插空行。
    """
    if not lines:
        return ""

    out: list[str] = []
    for ln in lines:
        if _is_chapter_heading(ln):
            if out:
                while out and out[-1] == "":
                    out.pop()
                out.extend(["", ""])
            out.append(ln)
        else:
            out.append(ln)

    text = "\n".join(out).strip()
    # 文档总标题（非「一、」开头）与第一章之间：仅 1 行空白
    text = re.sub(
        r"^([^\n]+)\n\n+([一二三四五]、)",
        r"\1\n\n\2",
        text,
        count=1,
    )
    return text


def _polish_strategic_report_prose(text: str) -> str:
    """轻量排版：去 ** 与噪声；章内紧凑、章间双空行；保留 🎯📊💡🧭🔍。"""
    if not (text or "").strip():
        return text

    lines: list[str] = []
    for raw_ln in text.splitlines():
        norm = _normalize_strategic_report_line(raw_ln)
        if norm:
            lines.append(norm)

    return _format_strategic_report_spacing(lines)


def _deliver_strategic_report(text: str) -> str:
    """泄漏截断 → 结构校验 → 排版抛光；结构软失败时仍交付正文并打日志。"""
    text = _ensure_deliverable_strategic_report(text)
    reasons = _structure_failure_reasons(text)
    if reasons:
        sec_n = _count_numbered_sections(text)
        # 五节齐全且篇幅足够：视为 LLM 标题措辞偏差，降级交付而非整篇丢弃
        if sec_n >= 5 and len(text) >= 800:
            logger.warning(
                "[Strategic] 结构校验软失败（%s），五节齐全且 %d 字符，仍交付",
                ",".join(reasons),
                len(text),
            )
        else:
            logger.warning(
                "[Strategic] 结构校验硬失败（%s），拒绝原样发送",
                ",".join(reasons),
            )
            return (
                "BI 战略分析（结构异常）\n\n"
                f"模型输出未通过结构校验（{', '.join(reasons)}），已拒绝原样发送。"
                "请重跑 Step 3.5 或检查 LLM 配置。"
            )
    return _polish_strategic_report_prose(text)


def _detect_table_spam_anomaly(text: str) -> bool:
    """检测 Markdown 表格 pipe 滥用（模型占位/截断续写）。"""
    for line in (text or "").splitlines():
        ln = line.strip()
        if not ln:
            continue
        pipe_n = ln.count("|")
        if pipe_n >= 12 and ln.count("---") >= 2:
            return True
        if pipe_n >= 20:
            return True
        if re.match(r"^\|(\s*\|\s*){8,}", ln):
            return True
    return False


def _detect_header_corruption(text: str) -> bool:
    """双标题拼接、空章节分隔等结构异常。"""
    if not text:
        return False
    if re.search(r"##\s*❓\s*##", text):
        return True
    if re.search(r"##\s*---\s*\n\s*##\s*---", text):
        return True
    if text.count("## ---") >= 2:
        return True
    return False


def _detect_metric_token_repetition(text: str) -> bool:
    """检测 DNU=/DNU=)/D NU/D AU 等指标占位符死循环（qwen 写渠道占比时高发）。"""
    if not text:
        return False
    tail = text[-4500:] if len(text) > 4500 else text
    if len(re.findall(r"DNU=\)?", tail, flags=re.I)) >= 8:
        return True
    if re.search(r"(?:DNU=\)?,\s*){4,}", tail, re.I):
        return True
    if re.search(r"(?:DNU=,\s*){4,}", tail, re.I):
        return True
    if tail.count("D NU/D AU") >= 3:
        return True
    if re.search(r"(比例异常高\s*\(>[\d.]+%\)\s*[。]?){4,}", tail):
        return True
    if re.search(r"(`DNU=`|DNU=\s*,){3,}", tail, re.I):
        return True
    return False


def _detect_repetition_anomaly(text: str) -> bool:
    """检测尾部重复循环（模型卡在数值/占位符上）。"""
    if not text:
        return False
    tail = text[-2500:] if len(text) > 2500 else text
    if tail.count("正式输出") >= 3:
        return True
    if tail.count("**(正式") >= 2:
        return True
    if re.search(r"\*\*\((?:截断|续|完整|修正|最终|输出|Done|End)\)\*\*", tail, re.I):
        return True
    if tail.count("数值为 `NaN%`") >= 3:
        return True
    if re.search(r"(\+\d+%[`\(]?达\s*){3,}", tail):
        return True
    if re.search(r"(\+\d+%`\(达\s*){4,}", tail):
        return True
    if re.search(r"(\(达到\+\s*){4,}", tail):
        return True
    if re.search(r"(\+\s*){20,}", tail):
        return True
    if re.search(r"(% % % ){5,}", tail):
        return True
    lines = [ln.strip() for ln in tail.splitlines() if ln.strip()]
    for i in range(len(lines) - 2):
        if len(lines[i]) > 8 and lines[i] == lines[i + 1] == lines[i + 2]:
            return True
    if _detect_metric_token_repetition(text):
        return True
    return False


def _is_report_corrupted(text: str) -> bool:
    """统一判定：thinking 泄漏 / 重复异常 / 表格 spam / 标题结构异常。"""
    if not (text or "").strip():
        return False
    if _THINKING_LEAK_RE.search(text):
        return True
    if _detect_repetition_anomaly(text):
        return True
    if _detect_table_spam_anomaly(text):
        return True
    if _detect_header_corruption(text):
        return True
    return bool(re.search(r"\*\*\(正式[^\n]{0,40}$", text.strip()))


def _detect_thinking_leak(text: str) -> bool:
    return _is_report_corrupted(text)


_RE_DNU_LOOP = re.compile(r"(?:DNU=\)?,\s*){4,}", re.I)
_RE_DAU_RATIO_LOOP = re.compile(r"(?:D\s*NU/D\s*AU比例异常高[^。]{0,48}[。]?){3,}", re.I)

# 第四节内异动小节锚点（污染时尽量保留上一小节）
_SUBSECTION_ANCHORS: tuple[str, ...] = (
    "### 🟡异动三",
    "### 🟡 异动三",
    "### 🟡异动二",
    "### 🟡 异动二",
    "### 🔴异动三",
    "### 🔴 异动三",
    "### 🔴异动二",
    "### 🔴 异动二",
    "### 🟡异动",
    "### 🔴异动",
)


def _find_earliest_corruption_index(text: str) -> int | None:
    """返回全文最早污染起点（泄漏标记 / 指标复读 / 表格 spam）。"""
    earliest: int | None = _find_leak_cut_index(text)
    for pat in (_RE_DNU_LOOP, _RE_DAU_RATIO_LOOP):
        m = pat.search(text)
        if m and (earliest is None or m.start() < earliest):
            earliest = m.start()
    if _detect_metric_token_repetition(text):
        for pat in (
            r"(?:DNU=\)?,\s*){2,}",
            r"D NU/D AU",
            r"(比例异常高\s*\(>[\d.]+%\)\s*[。]?){2,}",
        ):
            m = re.search(pat, text, re.I)
            if m and (earliest is None or m.start() < earliest):
                earliest = m.start()
    return earliest


def _truncate_loopy_lines(text: str) -> str:
    """单行内 DNU=/占比 死循环：截断该行并附省略说明，避免整节丢弃。"""
    out: list[str] = []
    for line in text.splitlines():
        ln = line
        if _RE_DNU_LOOP.search(ln) or ln.count("D NU/D AU") >= 3:
            m = re.search(r"(?:DNU=\)?,\s*){2,}", ln, re.I)
            if m:
                ln = ln[: m.start()].rstrip().rstrip("，,;；") + " …（渠道 DNU 枚举异常截断，详见多维表）"
            elif _RE_DAU_RATIO_LOOP.search(ln):
                m2 = _RE_DAU_RATIO_LOOP.search(ln)
                ln = ln[: m2.start()].rstrip() + " …（DNU/DAU 占比描述异常截断）"
            else:
                ln = ln[: min(280, len(ln))].rstrip() + " …"
        out.append(ln)
    return "\n".join(out)


def _smart_truncate_corrupted_report(text: str) -> str:
    """优先在污染点/异动小节边界截断，尽量保留第四节前半有效归因。"""
    if not _is_report_corrupted(text):
        return text
    text = _truncate_loopy_lines(text)
    if not _is_report_corrupted(text):
        return text
    cut = _find_earliest_corruption_index(text)
    if cut is not None and cut > 150:
        for anchor in _SUBSECTION_ANCHORS:
            pos = text.rfind(anchor, 0, cut + 1)
            if pos >= 0 and pos >= len(text) * 0.18:
                return text[:pos].rstrip()
        return text[:cut].rstrip()
    return _truncate_at_first_corrupted_section(text)


def _find_leak_cut_index(text: str) -> int | None:
    """返回应截断的最早字符位置；无泄漏则 None。"""
    earliest: int | None = None
    for m in _THINKING_LEAK_RE.finditer(text):
        if earliest is None or m.start() < earliest:
            earliest = m.start()
    if _detect_repetition_anomaly(text):
        # 重复异常通常出现在后半段：从第一个「##四」或「### 🔴异动二」之后找重复起点
        for anchor in ("### 🔴异动二", "###异动二", "## 四、", "##四、"):
            pos = text.find(anchor)
            if pos >= 0:
                sub = text[pos:]
                m2 = re.search(
                    r"(\+\d+%[`\(]?达\s*){2,}|(\(达到\+\s*){2,}|\*\*?\(正式|\*\*\((?:截断|续|完整|修正|最终|输出)\)|"
                    r"(?:DNU=\)?,\s*){2,}|(?:D\s*NU/D\s*AU比例异常高)",
                    sub,
                    re.I,
                )
                if m2:
                    cut = pos + m2.start()
                    if earliest is None or cut < earliest:
                        earliest = cut
                break
    if _detect_table_spam_anomaly(text):
        for line in text.splitlines():
            if line.count("|") >= 12:
                pos = text.find(line)
                if pos >= 0 and (earliest is None or pos < earliest):
                    earliest = pos
                break
    if _detect_metric_token_repetition(text):
        m = _RE_DNU_LOOP.search(text) or _RE_DAU_RATIO_LOOP.search(text)
        if m and (earliest is None or m.start() < earliest):
            earliest = m.start()
    return earliest


def _line_looks_like_leak_fragment(line: str) -> bool:
    ln = (line or "").strip()
    if not ln:
        return False
    if _THINKING_LEAK_RE.search(ln):
        return True
    if ln.startswith("*") and ("注:" in ln or "Note:" in ln or "修正" in ln or "正式" in ln):
        return True
    if re.match(r"^[\*\+%\(\)\s`\-|]+$", ln) and len(ln) > 6:
        return True
    if ln.count("|") >= 12:
        return True
    if _RE_DNU_LOOP.search(ln) or ln.count("D NU/D AU") >= 3:
        return True
    return False


def _iter_strategic_section_starts(text: str) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for marker in _STRATEGIC_SECTION_MARKERS:
        pos = 0
        while True:
            idx = text.find(marker, pos)
            if idx < 0:
                break
            hits.append((idx, marker))
            pos = idx + len(marker)
    hits.sort(key=lambda x: x[0])
    # 同位置只保留最长 marker
    dedup: list[tuple[int, str]] = []
    for pos, marker in hits:
        if dedup and dedup[-1][0] == pos:
            if len(marker) > len(dedup[-1][1]):
                dedup[-1] = (pos, marker)
        else:
            dedup.append((pos, marker))
    return dedup


def _section_number_from_marker(marker: str) -> int | None:
    compact = marker.replace(" ", "")
    for n, cn in enumerate("一二三四五六", 1):
        if f"{cn}、" in compact:
            return n
    return None


def _truncate_at_first_corrupted_section(text: str) -> str:
    """从第四节起逐节检测；首个污染节及之后整段丢弃。"""
    sections = _iter_strategic_section_starts(text)
    if not sections:
        return text
    for i, (pos, marker) in enumerate(sections):
        if (_section_number_from_marker(marker) or 0) < 4:
            continue
        end = sections[i + 1][0] if i + 1 < len(sections) else len(text)
        if _is_report_corrupted(text[pos:end]):
            return text[:pos].rstrip()
    return text


def _truncate_before_corrupted_section4(text: str) -> str:
    """若第四节及以后污染，保留前三节主干（兼容旧名）。"""
    return _truncate_at_first_corrupted_section(text)


def _trim_corrupted_tail_lines(text: str) -> str:
    lines = text.split("\n")
    while lines:
        ln = lines[-1]
        if not ln.strip():
            lines.pop()
            continue
        if _line_looks_like_leak_fragment(ln):
            lines.pop()
            continue
        break
    return "\n".join(lines).rstrip()


def _sanitize_strategic_report_thinking_leak(text: str) -> str:
    """截断 thinking/元注释泄漏点，并去掉末尾不完整片段。"""
    if not text:
        return text
    cut = _find_leak_cut_index(text)
    if cut is not None:
        text = text[:cut].rstrip()
    text = re.sub(r"\*\*\(正式[^\n]*$", "", text).rstrip()
    text = _smart_truncate_corrupted_report(text)
    text = _trim_corrupted_tail_lines(text)
    # 回退到最后一个完整章节分隔（保留前三节为主干）
    if text:
        last_hr = text.rfind("\n---\n")
        if last_hr > len(text) * 0.45:
            tail = text[last_hr + 5 :].strip()
            if _detect_thinking_leak(tail) or _detect_repetition_anomaly(tail):
                text = text[:last_hr].rstrip()
    return text


def _append_strategic_truncation_notice(text: str, *, force: bool = False) -> str:
    notice = (
        "\n\n---\n\n> ⚠️ 部分章节因模型输出异常已自动截断（常见为第四～六节）。"
        " 请人工复核留存/归因/待澄清部分，或稍后单独重跑 Step 3.5。"
    )
    if notice.strip() in text:
        return text
    if force:
        base = text.rstrip()
        if base and not base.endswith("。") and not base.endswith("）"):
            base += "。"
        return base + notice
    sections = _iter_strategic_section_starts(text)
    late_ok = True
    late_markers = ("## 四、", "##四、", "## 五、", "##五、", "## 六、", "##六、")
    for i, (pos, marker) in enumerate(sections):
        is_late = any(m.replace(" ", "") in marker.replace(" ", "") for m in late_markers)
        if not is_late:
            continue
        end = sections[i + 1][0] if i + 1 < len(sections) else len(text)
        if _is_report_corrupted(text[pos:end]):
            late_ok = False
            break
    if late_ok and not _is_report_corrupted(text):
        return text
    base = text.rstrip()
    if base and not base.endswith("。") and not base.endswith("）"):
        base += "。"
    return base + notice


def _finalize_strategic_report_output(raw: str) -> tuple[str, bool]:
    """去空白/代码围栏；检测并清理 thinking 泄漏。返回 (正文, 是否曾污染)。"""
    text = (raw or "").strip()
    if not text:
        return text, False
    if text.startswith("```"):
        lines = text.split("\n")
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    corrupted = _is_report_corrupted(text)
    was_corrupted = corrupted
    if corrupted:
        logger.warning("[Strategic] 战报含 thinking/元注释泄漏，执行截断清理")
        pre_len = len(text)
        text = _sanitize_strategic_report_thinking_leak(text)
        text = _smart_truncate_corrupted_report(text)
        text = _trim_corrupted_tail_lines(text)
        if _is_report_corrupted(text):
            text = _sanitize_strategic_report_thinking_leak(text)
        truncated = len(text) < pre_len - 20
        text = _append_strategic_truncation_notice(text, force=truncated or was_corrupted)
        was_corrupted = True
    return text, was_corrupted


def _pick_best_strategic_candidate(candidates: list[tuple[str, bool, str]]) -> tuple[str, bool]:
    """从多轮 LLM 结果中选最优：优先无污染且结构合格，其次无污染且足够长。"""
    valid = [(t.strip(), c, label) for t, c, label in candidates if (t or "").strip()]
    if not valid:
        return "", True
    clean = [(t, label) for t, c, label in valid if not c]
    if clean:
        struct_ok = [(t, label) for t, label in clean if _has_five_panel_structure(t)]
        if struct_ok:
            t, label = max(struct_ok, key=lambda x: len(x[0]))
            logger.info("[Strategic] 选用 %s 模型输出（无泄漏+结构合格，%d 字符）", label, len(t))
            return t, False
        t, label = max(clean, key=lambda x: len(x[0]))
        logger.info("[Strategic] 选用 %s 模型输出（无泄漏，%d 字符）", label, len(t))
        return t, False
    # 全部污染：取最长并再做截断
    t, c, label = max(valid, key=lambda x: len(x[0]))
    logger.warning("[Strategic] 各轮均含泄漏，对 %s 输出做强制截断（原长 %d）", label, len(t))
    t2, c2 = _finalize_strategic_report_output(t)
    return t2, c2


def _scrub_dod_summary_for_prompt(dod: str) -> str:
    """压缩 prompt 中易触发模型复读的 NaN/占位片段。"""
    if not dod:
        return dod
    lines: list[str] = []
    for ln in dod.splitlines():
        if ln.count("NaN%") >= 3:
            lines.append(re.sub(r"(NaN%[\s,，]*){2,}", "NaN%（T日行T+1未到期为常态，勿当ETL故障复读） ", ln))
            continue
        if re.search(r"(\+\d+%`?\(达\s*){3,}", ln):
            lines.append(re.sub(r"(\+\d+%`?\(达\s*)+", "+N%（…环比，勿复读） ", ln))
            continue
        if re.search(r"(?:DNU=\)?,\s*){3,}", ln, re.I) or ln.count("DNU=") >= 6:
            lines.append(
                re.sub(
                    r"(?:DNU=\)?,\s*)+",
                    "DNU=(见T日03b/01表，勿列举空DNU=) ",
                    ln,
                    count=1,
                )
            )
            continue
        if re.search(r"(D\s*NU/D\s*AU[^。]{0,40}[。]?){2,}", ln, re.I):
            lines.append(
                re.sub(
                    r"(D\s*NU/D\s*AU[^。]{0,40}[。]?)+",
                    "DNU/DAU占比见01/03b表（勿复读） ",
                    ln,
                    count=1,
                )
            )
            continue
        lines.append(ln)
    return "\n".join(lines)


def _ensure_deliverable_strategic_report(text: str) -> str:
    """终检：截断泄漏、补截断说明，保证可进邮件。"""
    text = (text or "").strip()
    if not text:
        return text
    if _is_report_corrupted(text):
        logger.warning("[Strategic][ExecutionBrief] 终检仍含泄漏，强制截断后交付")
        text = _sanitize_strategic_report_thinking_leak(text)
        text = _smart_truncate_corrupted_report(text)
        text = _append_strategic_truncation_notice(text)
    return text


def _to_json_serializable(obj: Any) -> Any:
    """递归将 date/datetime 转为 ISO 字符串，供 json.dumps 使用"""
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _to_json_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_json_serializable(v) for v in obj]
    return obj

# 未找到 STRATEGIC_REPORT_ANALYSIS_SPEC.md 时的兜底（保持可运行）
_STRATEGIC_ANALYSIS_SPEC_FALLBACK = """# BI 增长战报（兜底 v6.5）

五节诊断+战略决策；冲DAU；禁止RTP；T日次留0为常态；分析次留只用T-1及更早。
用局数/进桌/留存/买量/IAA盲区；🎯📊💡🧭🔍；禁止派活。"""
def _load_strategic_analysis_spec(project_root: Path | None = None) -> str:
    """加载大战报分析规范 MD；缺失则回退 _STRATEGIC_ANALYSIS_SPEC_FALLBACK。"""
    try:
        from l3_node.paths import get_app_root
    except ImportError:
        get_app_root = lambda: Path(__file__).resolve().parents[4]  # noqa: E731
    root = project_root or get_app_root()
    p = root / "docs" / "bi_daily_report" / "STRATEGIC_REPORT_ANALYSIS_SPEC.md"
    if not p.is_file():
        logger.warning("[Strategic] 未找到 %s，使用兜底 System 提示", p)
        return _STRATEGIC_ANALYSIS_SPEC_FALLBACK
    try:
        text = p.read_text(encoding="utf-8").strip()
        if not text:
            return _STRATEGIC_ANALYSIS_SPEC_FALLBACK
        logger.info(
            "[Strategic] 已加载大战报分析规范 MD: %s（%d 字符）",
            p.resolve(),
            len(text),
        )
        return text
    except OSError as e:
        logger.warning("[Strategic] 读取分析规范失败: %s", e)
        return _STRATEGIC_ANALYSIS_SPEC_FALLBACK


def _collect_duckdb_strategic_metrics(date_str: str | None = None) -> dict[str, Any]:
    """从 DuckDB 直接拉取四维度所需战略指标（鲸鱼依赖、产销比、DAU 趋势等）"""
    dt = datetime.now()
    if date_str:
        try:
            dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
        except ValueError:
            pass
    t1 = (dt - timedelta(days=1)).strftime("%Y-%m-%d")
    t7 = (dt - timedelta(days=7)).strftime("%Y-%m-%d")

    out: dict[str, Any] = {
        "report_date": t1,
        "dim1_capital": {},
        "dim2_fragility": {},
        "dim3_moat": {},
        "dim4_predictive": {},
    }

    try:
        from l3_node.primitives.mcp.mcp_tools.bi.data_store import _get_conn
        from l3_node.primitives.skills.bi.bi_daily_report.main_skill import (
            _find_col,
            _query_table,
            _safe_float,
            _safe_prod_cons,
        )
    except ImportError as e:
        logger.debug("[Strategic] DuckDB 战略指标拉取跳过: %s", e)
        return out

    conn = _get_conn()
    try:
        # 维度一：资本效率 — 渠道数据
        rows_acq = _query_table(conn, "daily_acquisition", date_from=t7, date_to=t1)
        if rows_acq:
            out["dim1_capital"]["has_channel_data"] = True
            out["dim1_capital"]["channels_sample"] = rows_acq[:8]

        # 维度二：系统性脆弱度 — 鲸鱼依赖、产销比
        for slug in ("stats_recharge", "detail_recharge_daily", "recharge_daily"):
            rows_rec = _query_table(conn, slug, date_from=t7, date_to=t1)
            if not rows_rec:
                continue
            cols = list(rows_rec[0].keys())
            amt_col = _find_col(cols, "充值金额", "金额", "amount", "总金额", "当日充值总额", "此等级总金额")
            if not amt_col:
                continue
            amounts = sorted([_safe_float(r.get(amt_col)) for r in rows_rec if r.get(amt_col)], reverse=True)
            total = sum(amounts)
            if total > 0:
                n_top = max(1, min(len(amounts), int(len(amounts) * 0.03)))
                top_sum = sum(amounts[:n_top])
                out["dim2_fragility"]["whale_revenue_pct"] = round(top_sum / total * 100, 1)
                out["dim2_fragility"]["whale_desc"] = f"头部3%大R贡献{out['dim2_fragility']['whale_revenue_pct']}%营收"
            break

        # 产销比、通胀预警；参与用户数 vs 参与机器人数（分离用户与机器人经济流转）
        agg_labels = ("全部汇总", "全量合计", "全平台汇总", "ALL", "> ALL")
        for slug in ("prod_sales", "stats_game_daily"):
            rows_prod = _query_table(conn, slug, date_from=t7, date_to=t1)
            if not rows_prod:
                continue
            cols = list(rows_prod[0].keys())
            prod_col = _find_col(cols, "用户金币产出总数", "产出", "用户金币产出", "金币产出")
            cons_col = _find_col(cols, "用户金币消耗总数", "消耗", "用户金币消耗", "金币消耗")
            agg_col = _find_col(cols, "汇总项目", "统计范围", "游戏名称")
            total_prod, total_cons = 0.0, 0.0
            for r in rows_prod:
                agg_val = str(r.get(agg_col, "") or "").strip()
                is_total = agg_val in agg_labels
                if prod_col and cons_col:
                    if is_total:
                        total_prod = _safe_prod_cons(r.get(prod_col))
                        total_cons = _safe_prod_cons(r.get(cons_col))
                        break
            if total_cons > 0:
                out["dim2_fragility"]["prod_cons_ratio"] = round(total_prod / total_cons, 2)
                out["dim2_fragility"]["inflation_risk"] = "高" if total_prod > total_cons * 1.2 else "中低"
            # 若无全部汇总行，回退为按行累加（stats_game_daily 无汇总行时）
            if total_cons <= 0 and prod_col and cons_col:
                total_prod = sum(_safe_prod_cons(r.get(prod_col)) for r in rows_prod)
                total_cons = sum(_safe_prod_cons(r.get(cons_col)) for r in rows_prod)
                if total_cons > 0:
                    out["dim2_fragility"]["prod_cons_ratio"] = round(total_prod / total_cons, 2)
                    out["dim2_fragility"]["inflation_risk"] = "高" if total_prod > total_cons * 1.2 else "中低"
            # 参与用户数 vs 参与机器人数（prod_sales 全部汇总行）
            user_col = _find_col(cols, "参与玩家数", "参与用户数", "活跃用户数")
            bot_col = _find_col(cols, "参与机器人数", "机器人参与")
            for r in rows_prod:
                agg_val = str(r.get(agg_col, "") or "").strip()
                if agg_val in agg_labels:
                    if user_col:
                        out["dim2_fragility"]["participating_users"] = int(_safe_float(r.get(user_col)))
                    if bot_col:
                        out["dim2_fragility"]["participating_bots"] = int(_safe_float(r.get(bot_col)))
                    break
            if out["dim2_fragility"]:
                break

        # 维度三：留存、社交相关
        rows_ret = _query_table(conn, "stats_retention_user", date_from=t1, date_to=t1)
        if rows_ret:
            cols = list(rows_ret[0].keys())
            for r in rows_ret[:3]:
                for c in cols:
                    if "次留" in (c or ""):
                        out["dim3_moat"]["retention_2d"] = r.get(c)
                    elif "七留" in (c or ""):
                        out["dim3_moat"]["retention_7d"] = r.get(c)

        # 维度四：DAU/DNU 趋势（预测用）
        rows_dau = _query_table(conn, "daily_ops_summary", date_from=t7, date_to=t1)
        if rows_dau:
            cols = list(rows_dau[0].keys())
            dau_col = _find_col(cols, "日活（DAU）", "日活(DAU)", "日活", "DAU", "dau")
            dnu_col = _find_col(cols, "当日新增用户（DNU）", "DNU", "dnu")
            dev_col = _find_col(cols, "当日新增设备数", "新增设备", "设备数")
            if dau_col:
                out["dim4_predictive"]["dau_series"] = [_safe_float(r.get(dau_col)) for r in rows_dau[-7:]]
            if dnu_col:
                out["dim4_predictive"]["dnu_series"] = [_safe_float(r.get(dnu_col)) for r in rows_dau[-7:]]
            # 风控盲区：DNU vs 设备数（T-1 最新）
            r1 = rows_dau[-1] if rows_dau else {}
            dnu_val = _safe_float(r1.get(dnu_col)) if dnu_col else 0
            dev_val = _safe_float(r1.get(dev_col)) if dev_col else 0
            if dev_val > 0:
                out["dim4_predictive"]["dnu_per_device_t1"] = round(dnu_val / dev_val, 2)
                out["dim4_predictive"]["dnu_t1"] = dnu_val
                out["dim4_predictive"]["new_devices_t1"] = dev_val
            # 经济通胀与阶级固化：老用户 vs 新用户 资产变动、产出消耗
            old_asset_col = _find_col(cols, "老用户总资产变动", "老用户资产变动")
            new_asset_col = _find_col(cols, "新用户总资产变动", "新用户资产变动")
            old_prod_col = _find_col(cols, "当日老用户金币产出", "老用户金币产出")
            new_prod_col = _find_col(cols, "当日新增用户金币产出", "新增用户金币产出")
            old_cons_col = _find_col(cols, "当日老用户金币消耗", "老用户金币消耗")
            new_cons_col = _find_col(cols, "当日新增用户金币消耗", "新增用户金币消耗")
            if old_asset_col:
                out["dim2_fragility"]["old_user_asset_change"] = _safe_prod_cons(r1.get(old_asset_col))
            if new_asset_col:
                out["dim2_fragility"]["new_user_asset_change"] = _safe_prod_cons(r1.get(new_asset_col))
            if old_prod_col and new_prod_col:
                out["dim2_fragility"]["old_user_prod"] = _safe_prod_cons(r1.get(old_prod_col))
                out["dim2_fragility"]["new_user_prod"] = _safe_prod_cons(r1.get(new_prod_col))
            if old_cons_col and new_cons_col:
                out["dim2_fragility"]["old_user_cons"] = _safe_prod_cons(r1.get(old_cons_col))
                out["dim2_fragility"]["new_user_cons"] = _safe_prod_cons(r1.get(new_cons_col))

        # 风控盲区：注册漏斗（注册人数 vs 进桌人数，stats_user_new ALL 行）
        rows_new = _query_table(conn, "stats_user_new", date_from=t1, date_to=t1)
        if rows_new:
            cols = list(rows_new[0].keys())
            dnu_col = _find_col(cols, "当日新增注册（DNU）", "DNU", "新增注册")
            table_col = _find_col(cols, "日新开始游戏人数", "开始游戏人数", "进桌人数")
            channel_col = _find_col(cols, "渠道", "统计范围")
            for r in rows_new:
                ch = str(r.get(channel_col, "") or "").strip().upper()
                if ch in ("ALL", "全部", "全部汇总", ""):
                    dnu_val = _safe_float(r.get(dnu_col))
                    table_val = _safe_float(r.get(table_col)) if table_col else 0
                    if dnu_val > 0:
                        out["dim4_predictive"]["register_to_table_ratio"] = round(table_val / dnu_val, 2)
                        out["dim4_predictive"]["dnu_register_t1"] = dnu_val
                        out["dim4_predictive"]["table_entry_t1"] = table_val
                    break

        # 控盘红线：按游戏 RTP（产出/消耗，stats_game_daily）
        for slug in ("stats_game_daily", "stats_game_compare", "prod_sales"):
            rows_game = _query_table(conn, slug, date_from=t1, date_to=t1)
            if not rows_game:
                continue
            cols = list(rows_game[0].keys())
            prod_col = _find_col(cols, "用户金币产出", "产出", "金币产出")
            cons_col = _find_col(cols, "用户金币消耗", "消耗", "金币消耗")
            rtp_col = _find_col(cols, "返币率", "RTP", "GameRTP", "用户回报率")
            game_col = _find_col(cols, "统计范围", "汇总项目", "游戏名称", "游戏", "游戏名")
            if (prod_col and cons_col) or rtp_col:
                game_rtps: list[dict[str, Any]] = []
                for r in rows_game[:30]:
                    g = str(r.get(game_col, "") or "").strip()
                    if g in ("全部汇总", "全平台", "ALL", ""):
                        continue
                    if rtp_col:
                        rtp_val = _safe_float(r.get(rtp_col))
                        if rtp_val > 0:
                            game_rtps.append({"游戏": g, "RTP": round(rtp_val, 4)})
                    elif prod_col and cons_col:
                        prod_val = _safe_prod_cons(r.get(prod_col))
                        cons_val = _safe_prod_cons(r.get(cons_col))
                        if cons_val > 0:
                            rtp_pct = round(prod_val / cons_val * 100, 4)
                            game_rtps.append({"游戏": g, "RTP": rtp_pct})
                if game_rtps:
                    out["dim2_fragility"]["game_rtp_list"] = game_rtps[:10]
                break

        # 控盘红线：充值用户回报率 RTP（recharge_status 平台充值情况）
        for slug in ("recharge_status", "recharge_daily"):
            rows_rec = _query_table(conn, slug, date_from=t1, date_to=t1)
            if not rows_rec:
                continue
            cols = list(rows_rec[0].keys())
            rtp_col = _find_col(cols, "充值用户回报率RTP", "充值用户回报率", "RTP")
            range_col = _find_col(cols, "统计范围", "渠道", "等级")
            for r in rows_rec:
                g = str(r.get(range_col, "") or "").strip()
                if g in ("所有", "全部", "ALL", ""):
                    rtp_val = _safe_float(r.get(rtp_col))
                    if rtp_val > 0:
                        out["dim2_fragility"]["recharge_user_rtp_pct"] = round(rtp_val, 2)
                    break
            if "recharge_user_rtp_pct" in out["dim2_fragility"]:
                break

        # 资产失衡：产销差（产-消）近 7 日，优先取「全部汇总」行避免重复累加
        agg_labels = ("全部汇总", "全量合计", "全平台汇总", "ALL", "> ALL")
        for slug in ("prod_sales", "stats_game_daily"):
            rows_pc = _query_table(conn, slug, date_from=t7, date_to=t1)
            if not rows_pc:
                continue
            cols = list(rows_pc[0].keys())
            prod_col = _find_col(cols, "用户金币产出", "产出", "金币产出")
            cons_col = _find_col(cols, "用户金币消耗", "消耗", "金币消耗")
            date_col = _find_col(cols, "日期", "date", "业务日期")
            game_col = _find_col(cols, "统计范围", "汇总项目", "游戏名称", "游戏", "游戏名")
            if prod_col and cons_col:
                by_date: dict[str, dict[str, float]] = {}
                for r in rows_pc:
                    d = str(r.get(date_col, ""))[:10]
                    if len(d) != 10:
                        continue
                    g = str(r.get(game_col, "") or "").strip()
                    is_total = g in agg_labels
                    if d not in by_date:
                        by_date[d] = {"prod": 0.0, "cons": 0.0, "_has_total": False}
                    if is_total:
                        by_date[d]["prod"] = _safe_prod_cons(r.get(prod_col))
                        by_date[d]["cons"] = _safe_prod_cons(r.get(cons_col))
                        by_date[d]["_has_total"] = True
                    elif not by_date[d].get("_has_total"):
                        by_date[d]["prod"] += _safe_prod_cons(r.get(prod_col))
                        by_date[d]["cons"] += _safe_prod_cons(r.get(cons_col))
                for v in by_date.values():
                    v.pop("_has_total", None)
                dates_sorted = sorted(by_date.keys())[-7:]
                out["dim2_fragility"]["prod_cons_diff_series"] = [
                    {"日期": d, "产消差": round(by_date[d]["prod"] - by_date[d]["cons"], 0)}
                    for d in dates_sorted
                ]
                if dates_sorted:
                    last = by_date[dates_sorted[-1]]
                    out["dim2_fragility"]["last_day_prod_cons_diff"] = round(
                        last["prod"] - last["cons"], 0
                    )
                break
    finally:
        conn.close()

    return out


# User Prompt 模板：仅注入数据，格式由 System Prompt 强制
_USER_PROMPT_TEMPLATE = """## K11 / BI 项目背景知识（docs/bi_daily_report/bi_project）
{k11_context_section}

## 数据日 T 相对前一日 T-1 的字段级变化摘要（output 提纯表 + raw 源表，供对照）
比对：**T = {dod_t1}**，**T-1 = {dod_t2}**（战报标题与摘要中的 **公历年份** 须与此处 T 的 YYYY 一致。）
{dod_summary_section}

---

## 核心指标 JSON（引擎/同环比）
```json
{metrics_json}
```

## 多维表 / DuckDB / CSV 数据摘要（与上表互补）
{csv_summary_section}

## IAA 数据自检（系统预判 · 须服从）
{iaa_self_check_section}

## 留存日历（系统预判 · 须服从）
{retention_calendar_section}

请**先**结合「背景知识」与「T vs T-1 摘要」，再综合下方 JSON 与摘要，**严格遵循 System 中的《战略战报分析规范》v6.4**：

- **北极星**：**不计成本冲 DAU/DNU**——流量、漏斗、留存、买量效率；**不是** RTP/GGR 控盘或「经济止血」
- **禁止 RTP 叙事**：正文不得写 RTP、GGR、放水、通胀、下调 RTP、经济模型止血等（即使 JSON 里有返币率字段也忽略）
- **次留日历**：**T 日（数据日）次留=0/NaN% 正常**（T+1 未到期）；**禁止**据此写「留存崩盘/ETL 未就绪/无法验证今日新增」；评价留存只用 **T-1 及更早** 真实次留%，今日新增写「待明日 T+1 落地后验证」
- **结构（五节）**：① 大盘晴雨表 → ② 买量复盘 → ③ 漏斗解剖（最厚）→ ④ 生态留客（局数/参与度，不谈 RTP）→ ⑤ 增长战略与决策
- **第一～四节**：🎯📊💡；第五节 🎯📊💡🧭🔍；禁止 ** 加粗、人名、派活
- **IAA 缺数**：第三节盲区疾呼；第五节 🔍【待验证】

日期用 {report_date_mmdd}。建议 1200～2500 汉字。禁止思考链；直接输出正文。"""


_IAA_FIELD_RE = re.compile(
    r"IAA|eCPM|ecpm|激励视频|广告观看|广告完播|InAppAdvertisement|iaa_",
    re.IGNORECASE,
)
_IAA_NUMERIC_NEAR_RE = re.compile(
    r"(?:IAA|eCPM|激励视频|广告观看).{0,100}?\d[\d.,%]*|\d[\d.,%]*.{0,100}?(?:IAA|eCPM|激励视频)",
    re.IGNORECASE,
)


def _iaa_data_likely_available(
    metrics: dict[str, Any] | None,
    data_summary: str,
    dod_summary: str,
) -> bool:
    """输入侧是否含 IAA/eCPM 等可分析数值（非 bi_project 概念性提及）。"""
    metrics_blob = json.dumps(
        _to_json_serializable(metrics or {}),
        ensure_ascii=False,
    )
    blob = metrics_blob + "\n" + (data_summary or "") + "\n" + (dod_summary or "")
    if not _IAA_FIELD_RE.search(blob):
        return False
    return bool(_IAA_NUMERIC_NEAR_RE.search(blob))


def _build_retention_calendar_note(dod_t1: str, dod_t2: str = "") -> str:
    """向 LLM 说明 T 日次留 0 是日历常态，避免误判为 ETL/崩盘。"""
    t = (dod_t1 or "").strip()[:10] or "T"
    t_prev = (dod_t2 or "").strip()[:10] or "T-1"
    return (
        f"**数据日 T = {t}**（战报分析日）。留存表「次日(T+1)留存」列含义：T 日新增用户在 **T+1 自然日** 是否回访。\n"
        f"- **{t} 这一行 T+1 显示 0 或 NaN% 是正常的**：今天还没过完，明日才有「今日新增用户的次留」；**不是**留存崩盘，**不是** ETL 坏了，**不要**写「次留归零」「无法验证今日新增质量」。\n"
        f"- **只有 {t_prev} 及更早日期** 的次留% 才是已闭合的真实数据（例：截图里 {t_prev} 行可有 11% 量级次留）。\n"
        f"- 写大盘/买量时：引用 **昨日 {t_prev} 次留** + 近 7 日趋势；对 **今日 {t} 新增** 写「次留待 T+1 数据落地后验证（预期明日可见）」。"
    )


def _build_iaa_self_check_section(
    metrics: dict[str, Any] | None,
    data_summary: str,
    dod_summary: str,
) -> str:
    if _iaa_data_likely_available(metrics, data_summary, dod_summary):
        return (
            "**判定：已检测到 IAA / eCPM / 激励视频相关数值字段** → 第三板块可正常分析广告承接与 ROI 兜底；"
            "不必机械重复「盲区」套话，但仍须点明 IAA 与 IAP 谁主承接。"
        )
    return (
        "**判定：输入中未检测到 IAA（激励视频）观看、eCPM、完播率等有效数值**（当前 DuckDB 常规 slug 通常不含此项）。\n"
        "→ 第三板块「漏斗解剖」**必须**单独设 **IAA 致命盲区** 要点，大声疾呼："
        "「连用户有没有看广告都不知道！在 IAP 几乎不起量的情况下，无法计算 ROI 兜底，这是极度危险的盲区！」\n"
        "→ 第五板块「增长战略与决策」须在 🔍【待验证】中说明：缺 IAA 时哪些买量决策无法拍板；**勿**把「T 日次留=0」列为待验证（那是日历常态）。"
    )


def _lark_bitable_list_params(
    lark_config: dict[str, Any], csv_filename: str, page_token: str | None
) -> dict[str, Any]:
    """Lark 多维表 list records 查询参数；可选 per-CSV view_id（bi_daily_report.yaml list_records_view_by_csv）"""
    params: dict[str, Any] = {"page_size": 100}
    if page_token:
        params["page_token"] = page_token
    vm = lark_config.get("list_records_view_by_csv") or {}
    vid = str(vm.get(csv_filename) or "").strip()
    if vid:
        params["view_id"] = vid
    return params


def _cell_to_str(val: Any) -> str:
    """将 Lark 多维表 cell 值转为可读字符串"""
    if val is None:
        return ""
    if isinstance(val, dict):
        if val.get("type") == "date":
            return str(val.get("date", val.get("timestamp", "")))
        return str(val)
    return str(val)


def _fetch_lark_strategic_summary(lark_config: dict[str, Any]) -> str:
    """从 Lark 多维表拉取战略分析所需数据摘要"""
    app_token = (lark_config.get("app_token") or "").strip()
    tables_map = lark_config.get("tables") or {}
    if not app_token or not tables_map:
        return "（无配置）"

    app_id = (lark_config.get("app_id") or "").strip()
    app_secret = (lark_config.get("app_secret") or "").strip()
    use_feishu = lark_config.get("lark_use_feishu", False)
    if app_id and app_secret:
        os.environ.setdefault("LARK_APP_ID", app_id)
        os.environ.setdefault("LARK_APP_SECRET", app_secret)
    if use_feishu:
        os.environ["LARK_USE_FEISHU"] = "1"

    try:
        from l3_node.channels.lark.client import get_tenant_access_token, get_lark_api_base
        import requests
    except ImportError:
        return "（无配置）"

    try:
        token = get_tenant_access_token()
        api_base = get_lark_api_base()
    except Exception as e:
        logger.warning("[Strategic] Lark token 失败: %s", e)
        return "（无配置）"

    lines: list[str] = []
    for name in _STRATEGIC_KEY_FILES:
        table_id = (tables_map.get(name) or "").strip()
        if not table_id or table_id.startswith("${"):
            continue
        try:
            records: list[dict] = []
            page_token = None
            while True:
                url = f"{api_base}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
                params = _lark_bitable_list_params(lark_config, name, page_token)
                resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=15)
                data = resp.json()
                if data.get("code") != 0:
                    break
                items = data.get("data", {}).get("items", [])
                records.extend(items)
                page_token = data.get("data", {}).get("page_token")
                if not page_token or not items:
                    break
            if records:
                cols = list(records[0].get("fields", {}).keys())[:8]
                lines.append(f"### {name}（Lark 多维表）")
                for r in records[:5]:
                    fields = r.get("fields", {})
                    line = " | ".join(f"{k}: {_cell_to_str(fields.get(k))}" for k in cols[:5])
                    lines.append(f"  {line}")
        except Exception as e:
            logger.debug("[Strategic] Lark 表 %s 拉取失败: %s", name, e)
    return "\n".join(lines) if lines else "（无数据）"


def _load_csv_summary(output_dir: Path) -> str:
    """从 output 目录的 CSV 中提取关键摘要文本，供 LLM 参考（Lark 无数据时回退）"""
    lines: list[str] = []
    for name in _STRATEGIC_KEY_FILES:
        p = output_dir / name
        if not p.exists():
            continue
        try:
            import csv as csv_module
            with open(p, encoding="utf-8-sig") as f:
                reader = csv_module.DictReader(f)
                rows = list(reader)[:10]
            if rows:
                cols = list(rows[0].keys())
                lines.append(f"### {name}")
                for r in rows[:5]:
                    line = " | ".join(f"{k}: {r.get(k, '')}" for k in cols[:5])
                    lines.append(f"  {line}")
        except Exception as e:
            logger.debug("[Strategic] 读取 CSV 摘要失败 %s: %s", name, e)
    return "\n".join(lines) if lines else "（无 CSV 摘要）"


# 战略分析 key 与 raw CSV 映射（output 表名 → raw slug）
_STRATEGIC_KEY_TO_RAW: dict[str, list[str]] = {
    "01_用户活跃_增幅表.csv": ["stats_user_dau", "stats_user_new"],
    "02_用户活跃_日期数量表.csv": ["stats_user_dau", "stats_user_new", "stats_retention_user"],
    "03a_用户活跃_DAU渠道来源.csv": ["stats_user_dau"],
    "03b_用户活跃_DNU渠道来源.csv": ["stats_user_new"],
    "13_用户活跃_新增设备表.csv": ["stats_user_dau", "stats_user_new", "daily_ops_summary"],
    "04_留存_次留表.csv": ["stats_retention_user"],
    "05_留存_付费用户次留表.csv": ["stats_retention_paid"],
    "06_留存_周环比表.csv": ["stats_retention_user_compare"],
    "07_留存_付费用户周环比表.csv": ["stats_retention_paid_compare"],
    "08_消耗_每日表.csv": ["prod_sales"],
    "08b_消耗_金币_渠道层.csv": ["prod_sales", "stats_user_dau"],
    "09_消耗_按游戏表.csv": ["prod_sales"],
    "10_充值_付费人数按SKU.csv": ["recharge_status"],
    "11_充值_付费金额按SKU.csv": ["recharge_status"],
    "14_充值_付费人数金额增幅表.csv": ["stats_recharge"],
    "15_消耗_Arup表.csv": ["daily_ops_summary"],
    "16_消耗_Arppu表.csv": ["daily_ops_summary"],
    "17_游戏_完成局数.csv": ["stats_game_core"],
    "18_游戏_用户获胜.csv": ["stats_game_core"],
    "19_游戏_RTP_GGR.csv": ["stats_game_daily"],
}


def _detect_report_date_from_raw(raw_dir: Path) -> tuple[str, str]:
    """从 raw 目录 CSV 检测最新数据日期。返回 (YYYY-MM-DD, MM/DD) 供战报与 DuckDB 查询使用"""
    date_cols = ("日期", "date", "统计日期", "业务日期", "_ingested_date")
    date_candidates = [
        "daily_ops_summary.csv",
        "prod_sales.csv",
        "stats_recharge.csv",
        "stats_user_dau.csv",
        "stats_user_new.csv",
    ]
    latest: str | None = None
    import csv as csv_module
    import re
    for name in date_candidates:
        p = raw_dir / name
        if not p.exists():
            continue
        try:
            with open(p, encoding="utf-8-sig") as f:
                reader = csv_module.DictReader(f)
                rows = list(reader)[:50]
            if not rows:
                continue
            cols = [c for c in rows[0].keys() if c in date_cols]
            if not cols:
                cols = [c for c in rows[0].keys() if "date" in (c or "").lower() or "日期" in (c or "")]
            for c in cols:
                for r in rows:
                    val = str(r.get(c, "") or "").strip()[:10]
                    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", val)
                    if m:
                        dt_str = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
                        if latest is None or dt_str > latest:
                            latest = dt_str
            if latest:
                break
        except Exception:
            continue
    if latest:
        try:
            y, m, d = latest.split("-")
            return (latest, f"{int(m):02d}/{int(d):02d}")
        except ValueError:
            pass
    fallback = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    return (fallback, (datetime.now() - timedelta(days=1)).strftime("%m/%d"))


def _load_raw_strategic_summary(raw_dir: Path | None) -> str:
    """从 raw 目录 CSV 提取战略分析所需数据摘要（替代 Lark 多维表）"""
    if not raw_dir or not raw_dir.exists():
        return "（无 raw 目录）"
    lines: list[str] = [_RAW_STRATEGIC_PREAMBLE.rstrip(), ""]
    seen_slugs: set[str] = set()
    import csv as csv_module
    for name in _STRATEGIC_KEY_FILES:
        slugs = _STRATEGIC_KEY_TO_RAW.get(name, [])
        if not slugs:
            continue
        for slug in slugs:
            if slug in seen_slugs:
                continue
            p = raw_dir / f"{slug}.csv"
            if not p.exists():
                continue
            seen_slugs.add(slug)
            try:
                with open(p, encoding="utf-8-sig") as f:
                    reader = csv_module.DictReader(f)
                    rows = list(reader)[:10]
                if rows:
                    cols = list(rows[0].keys())
                    lines.append(f"### {slug}.csv（raw 数据）")
                    for r in rows[:5]:
                        line = " | ".join(f"{k}: {str(r.get(k, ''))[:30]}" for k in cols[:5])
                        lines.append(f"  {line}")
            except Exception as e:
                logger.debug("[Strategic] 读取 raw %s 失败: %s", slug, e)
    for slug in _STRATEGIC_RAW_ONLY_SLUGS:
        if slug in seen_slugs:
            continue
        p = raw_dir / f"{slug}.csv"
        if not p.exists():
            continue
        seen_slugs.add(slug)
        try:
            with open(p, encoding="utf-8-sig") as f:
                reader = csv_module.DictReader(f)
                rows = list(reader)[:10]
            if rows:
                cols = list(rows[0].keys())
                lines.append(f"### {slug}.csv（raw 数据，漏斗/告警）")
                for r in rows[:5]:
                    line = " | ".join(f"{k}: {str(r.get(k, ''))[:30]}" for k in cols[:5])
                    lines.append(f"  {line}")
        except Exception as e:
            logger.debug("[Strategic] 读取 raw %s 失败: %s", slug, e)
    return "\n".join(lines) if lines else "（无 raw 数据）"


def _build_user_prompt(
    metrics: dict[str, Any],
    data_summary: str,
    report_date_mmdd: str = "",
    *,
    k11_context_md: str = "",
    dod_summary: str = "",
    dod_t1: str = "",
    dod_t2: str = "",
) -> str:
    # 仅传递中文 key 的 core 指标，避免 LLM 输出英文变量名
    core = {k: v for k, v in (metrics or {}).items() if not k.startswith("_")}
    metrics_cn = _metrics_to_chinese(core)
    metrics_clean = _to_json_serializable(metrics_cn)
    metrics_json = json.dumps(metrics_clean, ensure_ascii=False, indent=2)
    data_section = (data_summary or "").strip() or "（无）"
    if not report_date_mmdd:
        report_date_mmdd = (datetime.now() - timedelta(days=1)).strftime("%m/%d")
    k11_sec = (k11_context_md or "").strip() or "（未注入：请仍按 System 要求保守表述）"
    dod_sec = (dod_summary or "").strip() or "（未生成 T/T-1 对照摘要，仅依据下列 JSON 与摘要）"
    dt1 = (dod_t1 or "").strip() or report_date_mmdd
    dt2 = (dod_t2 or "").strip() or "T-1"
    iaa_sec = _build_iaa_self_check_section(metrics, data_section, dod_sec)
    ret_sec = _build_retention_calendar_note(dt1, dt2)
    return _USER_PROMPT_TEMPLATE.format(
        k11_context_section=k11_sec,
        dod_summary_section=dod_sec,
        dod_t1=dt1,
        dod_t2=dt2,
        metrics_json=metrics_json,
        csv_summary_section=data_section,
        iaa_self_check_section=iaa_sec,
        retention_calendar_section=ret_sec,
        report_date_mmdd=report_date_mmdd,
    )


async def generate_bi_strategic_report_async(
    metrics: dict[str, Any] | None = None,
    output_dir: Path | None = None,
    config: dict[str, Any] | None = None,
) -> str:
    """
    基于 BI 指标与 CSV 数据，调用 LLM 生成战略战报。

    Args:
        metrics: 来自 query_bi_metrics 的指标字典；若为 None 则自动调用
        output_dir: 提纯 CSV 所在目录，用于补充摘要
        config: 技能配置，可含 strategic_report 段

    Returns:
        战略战报 Markdown 文本；LLM 失败时返回降级文案
    """
    cfg = config or {}
    sr_cfg = cfg.get("strategic_report") or {}
    if not sr_cfg.get("enabled", True):
        return "战略分析已关闭 (strategic_report.enabled=false)"

    # 数据日期与摘要：duckdb（默认）| raw CSV | lark 多维表
    date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    data_summary = ""
    report_date_mmdd = ""
    analysis_src = _resolve_strategic_data_source(cfg)
    raw_dir: Path | None = None

    if analysis_src == "duckdb":
        report_date_iso, report_date_mmdd = _detect_report_date_from_duckdb()
        date_str = report_date_iso
        data_summary = _load_duckdb_strategic_summary(date_str)
        if "无可用表数据" not in data_summary:
            logger.info("[Strategic] 使用 DuckDB 数据源，数据日期 %s", report_date_mmdd)
    elif analysis_src == "raw":
        raw_dir_cfg = (cfg.get("storage") or {}).get("analysis_raw_dir") or ""
        raw_dir = Path(raw_dir_cfg) if raw_dir_cfg and str(raw_dir_cfg).strip() else _get_bi_raw_dir()
        raw_dir = raw_dir.expanduser().resolve()
        if raw_dir.exists():
            data_summary = _load_raw_strategic_summary(raw_dir)
            if data_summary not in ("（无 raw 目录）", "（无 raw 数据）"):
                report_date_iso, report_date_mmdd = _detect_report_date_from_raw(raw_dir)
                date_str = report_date_iso
                logger.info("[Strategic] 使用 raw 目录 CSV 数据，数据日期 %s", report_date_mmdd)
    if metrics is None:
        try:
            from l3_node.primitives.mcp.mcp_tools.bi.metrics.engine import run as run_metrics
            metrics, _ = run_metrics(
                date_str=date_str,
                show_compare=True,
                compare_period="week",
                output_format="console",
            )
            if "_error" in metrics:
                metrics = {"dau": 0, "dnu": 0, "_metrics_error": metrics["_error"]}
        except Exception as e:
            logger.warning("[Strategic] 拉取指标失败: %s", e)
            metrics = {"_metrics_error": str(e)}

    if not data_summary and analysis_src == "lark":
        lark_cfg = cfg.get("lark_bitable") or {}
        if lark_cfg.get("enabled", True) and lark_cfg.get("app_token"):
            lark_summary = _fetch_lark_strategic_summary(lark_cfg)
            if lark_summary not in ("（无数据）", "（无配置）"):
                data_summary = lark_summary
                logger.info("[Strategic] 使用 Lark 多维表数据")
    if not data_summary and analysis_src == "duckdb":
        data_summary = _load_duckdb_strategic_summary(date_str)
    if not data_summary and output_dir:
        data_summary = _load_csv_summary(Path(output_dir))
        logger.info("[Strategic] 回退 output CSV 摘要")
    if not report_date_mmdd:
        if analysis_src == "duckdb":
            report_date_iso, report_date_mmdd = _detect_report_date_from_duckdb()
            date_str = report_date_iso
        elif analysis_src == "raw":
            detect_dir = raw_dir if (raw_dir and raw_dir.exists()) else _get_bi_raw_dir()
            if detect_dir and detect_dir.exists():
                report_date_iso, report_date_mmdd = _detect_report_date_from_raw(detect_dir)
                date_str = report_date_iso
        else:
            report_date_mmdd = (datetime.now() - timedelta(days=1)).strftime("%m/%d")

    # 补充 DuckDB 四维度战略指标（鲸鱼依赖、产销比、DAU 趋势等）；date_str 已按 raw 最新日期更新（若有）
    strategic_metrics = _collect_duckdb_strategic_metrics(date_str)
    metrics = dict(metrics or {})
    metrics["_strategic_duckdb"] = strategic_metrics

    k11_md = str(sr_cfg.get("_k11_project_context_md") or "").strip()
    dod_txt = str(sr_cfg.get("_strategic_dod_summary") or "").strip()
    dod_txt = _scrub_dod_summary_for_prompt(dod_txt)
    dod_t1 = str(sr_cfg.get("_strategic_dod_t1") or "").strip()
    dod_t2 = str(sr_cfg.get("_strategic_dod_t2") or "").strip()
    if dod_t1 and not dod_t2:
        try:
            d0 = datetime.strptime(dod_t1[:10], "%Y-%m-%d")
            dod_t2 = (d0 - timedelta(days=1)).strftime("%Y-%m-%d")
        except ValueError:
            pass

    user_prompt = _build_user_prompt(
        metrics,
        data_summary,
        report_date_mmdd,
        k11_context_md=k11_md,
        dod_summary=dod_txt,
        dod_t1=dod_t1 or date_str,
        dod_t2=dod_t2,
    )
    spec_ov = str(sr_cfg.get("_strategic_analysis_spec_override") or "").strip()
    if spec_ov:
        sys_prompt = spec_ov
    else:
        from l3_node.paths import get_app_root

        sys_prompt = _load_strategic_analysis_spec(get_app_root())

    aest = str(sr_cfg.get("_strategic_aesthetics_section") or "").strip()
    if aest:
        sys_prompt = (
            sys_prompt.rstrip()
            + "\n\n---\n\n## （追加）大战报 Markdown 排版与可读性 — 与上文 SSOT 同等效力\n\n"
            + aest
        )
    sys_prompt = (
        sys_prompt.rstrip()
        + "\n\n---\n\n## （硬性）输出纪律\n\n"
        "只输出可直接发送的正文（轻 Markdown / plain prose）。**禁止**输出思考过程、自我修正、占位符、"
        "「(截断)/(续)/(完整)/(最终)」等标记、英文 Self-Correction/Ready to Output/Wait 注释、"
        "或 pipe 滥用的空表格；**禁止**复读 `DNU=`, `DNU=),` 空占位或「D NU/D AU比例异常高」同句循环。"
        "渠道占比写一次具体数字即可，缺数写「未提供」。"
        "**禁止** v4/v5 旧结构；必须按 v6.4 五板块输出。"
        "阶段锚点：不计成本冲 DAU/DNU；第一～四节 🎯📊💡；第五节战略决策 🧭🔍。"
        "**全文禁止 RTP/GGR 叙事**：不得写返币率、放水、通胀、下调 RTP、经济止血等，即使输入数据含 RTP 字段。"
        "**次留日历**：T 日（数据日）次留 0/NaN% 为常态（T+1 未到期），禁止写 ETL 故障/留存崩盘；只用 T-1 及更早真实次留评价，今日新增写待明日验证。"
        "用局数/进桌率/留存/买量/IAA 讲增长；禁止派活与人名。"
        "排版：章内紧凑；章间双空行由系统处理。"
    )

    engine = None
    try:
        from l3_node.agent_ref import engine_ref
        engine = engine_ref.get("engine")
    except Exception:
        pass

    if not engine:
        try:
            from l3_node.__main__ import _create_engine_standalone
            engine = _create_engine_standalone()
        except Exception as e:
            logger.warning("[Strategic] 无法创建 LLM 引擎: %s", e)

    if not engine:
        fallback = """# 📊 BI 战略分析（LLM 未就绪）

## 说明
数据已就绪，但 LLM 引擎不可用。请在项目根 `.env` 配置 `DASHSCOPE_API_KEY` 或 `OPENAI_API_KEY`，或通过 L2 为子账号下发 Key 后重试。"""
        return fallback

    # qwen3.5-plus 在超大 prompt 时默认 thinking 会耗尽 max_tokens 输出预算，
    # 导致 content 为空；显式关闭 thinking 可避免此问题。
    # engine.generate_response 在 llm_client.py 中已支持 extra_body 透传。
    _no_thinking_extra = {"enable_thinking": False}

    async def _call_llm(eng: Any, max_tok: int = 8192) -> str:
        result = await eng.generate_response(
            messages,
            temperature=0.2,
            max_tokens=max_tok,
            extra_body=_no_thinking_extra,
        )
        if isinstance(result, dict):
            return result.get("content", "")
        return (result or "").strip()

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_prompt},
    ]
    def _make_flash_engine() -> Any | None:
        try:
            from l3_node.llm_client import LiteLLMEngine, SecurityContext
            import os as _os_sr

            ctx_fb = SecurityContext()
            dash_key = _os_sr.environ.get("DASHSCOPE_API_KEY_SEA") or _os_sr.environ.get("DASHSCOPE_API_KEY") or ""
            if not dash_key:
                return None
            ctx_fb.set_key("dashscope", dash_key)
            _timeout = float(_os_sr.environ.get("LLM_TIMEOUT", "180"))
            return LiteLLMEngine(
                security_context=ctx_fb,
                model_name="dashscope/qwen3.5-flash",
                timeout=_timeout,
                max_attempts=1,
            )
        except Exception as fb_e:
            logger.warning("[Strategic] flash 引擎初始化失败: %s", fb_e)
            return None

    async def _try_once(eng: Any | None, label: str, max_tok: int = 8192) -> tuple[str, bool, str]:
        if eng is None:
            return "", True, label
        raw = await _call_llm(eng, max_tok=max_tok)
        text, corrupted = _finalize_strategic_report_output(raw)
        return text, corrupted, label

    try:
        flash_eng = _make_flash_engine()
        candidates: list[tuple[str, bool, str]] = []

        # flash 优先：不易在第四节陷入自我修正死循环；plus 仅作补充
        if flash_eng:
            logger.info("[Strategic] 主路径：qwen3.5-flash（enable_thinking=False）")
            flash_text, flash_bad, _ = await _try_once(flash_eng, "flash", 8192)
            if flash_text and not flash_bad and len(flash_text) >= 400 and _has_five_panel_structure(flash_text):
                logger.info("[Strategic] flash 首轮通过终检（%d 字符），跳过 plus", len(flash_text))
                return _deliver_strategic_report(flash_text)
            if flash_text:
                candidates.append((flash_text, flash_bad, "flash"))

        logger.info("[Strategic] 备路径：默认引擎 qwen3.5-plus")
        plus_text, plus_bad, _ = await _try_once(engine, "plus", 8192)
        if plus_text:
            candidates.append((plus_text, plus_bad, "plus"))

        text, still_bad = _pick_best_strategic_candidate(candidates)
        if still_bad or not (text or "").strip():
            logger.warning("[Strategic] 首轮均不理想，flash 再试一次")
            if flash_eng:
                candidates.append(await _try_once(flash_eng, "flash-retry", 8192))
            text, still_bad = _pick_best_strategic_candidate(candidates)

        if not (text or "").strip():
            return "LLM 返回空内容"

        return _deliver_strategic_report(text)
    except Exception as e:
        logger.exception("[Strategic] LLM 调用失败: %s", e)
        return f"""# 📊 BI 战略分析（生成失败）

LLM 调用异常: {e}

请检查网络与 API Key 后重试。"""
