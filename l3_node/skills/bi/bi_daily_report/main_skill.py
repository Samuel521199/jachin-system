"""
BI 每日战报 — 主技能逻辑（一个插件仅此一个 skill）

本 skill 完成 BI 日报全流程：数据新鲜度检查、抓取更新、提纯输出 CSV、同步飞书多维表。
所有逻辑集中于此，供 L3 agent 预检直接调用或通过 skill 描述理解执行步骤。

## Skill 描述（供 L3 Agent）

当用户发起「BI分析」「帮我开始今天的BI分析」等意图时，按以下步骤执行：

1. **数据新鲜度**：检查 bi.duckdb 是否含今日 _ingested_date；若无则执行 SPA 抓取（spa_collector）并 ingest_csv
2. **数据提纯**：从 DuckDB 按产品需求（12_PRODUCT_REQUIREMENTS.md）与 Lark 表结构（11_LARK_TABLE_SCHEMA.md）
   提炼 11 个 CSV：用户活跃(增幅/日期数量/渠道)、留存(次留/周环比/月环比)、消耗(每日/按游戏)、充值(付费人数/付费金额按SKU)
3. **Lark 同步**：将 output 下 CSV 同步到飞书多维表格（atom_lark_bitable_sync）
4. **战略深度分析**：基于 DuckDB 指标与 CSV，调用 LLM 生成金字塔原则战报，可选推送 Lark
5. **邮件通知**：调用 mcp:atom_email_sender 将战报发送至 distribution.email.to_addrs
6. **仪表盘自动化**（Step 4）：对每个仪表盘调用 LLM 分析统计图数据 → 保存到 output（~/.jachin/client_volumes/bi_data/output）→ 打开 Lark 仪表盘 → 点击「设置自动化发送」→「更多配置」→ 填入分析、设置定时（config.dashboard_automation.scheduled_time，可动态调整）→「保存并启用」。前置：Chrome --remote-debugging-port=9222 已登录 Lark，或 Playwright 自动启动浏览器首次登录。

## BI 平台 → bi.duckdb 数据源映射（供 L3 Agent 理解数据来源）

BI 平台管理 → 数据统计分析 下各页面与 DuckDB 表对应关系：

| BI 菜单路径 | 页面名称 | DuckDB slug | 产出表/字段 |
|-------------|----------|-------------|-------------|
| 用户数据统计 → 日活统计 | stats_user_dau | 03a DAU 渠道来源（点击日期展开） |
| 用户数据统计 → 日新用户统计 | stats_user_new | 03b DNU 渠道来源（点击渠道展开） |
| 平台数据 → 日常报表 | daily_ops_summary | 01 增幅、02 日期数量 |
| 留存数据统计 → 新增用户留存统计 | stats_retention_user | 04 次留表（第一行：T+1/T+3/T+6→T-2/T-4/T-7） |
| 留存数据统计 → 新增用户留存对比 | stats_retention_user_compare | 06 周环比（第一行 T+1/T+4/T+6）、12 月环比 |
| 留存数据统计 → 新增付费留存统计 | stats_retention_paid | 05 付费用户次留表（第一行） |
| 留存数据统计 → 新增付费留存对比 | stats_retention_paid_compare | 07 付费用户周环比（第一行 T+1/T+3/T+7） |
| 游戏数据统计 → 每日游戏数据 | stats_game_daily | 08 每日消耗、09 按游戏消耗（点击日期展开取各游戏 用户金币消耗/产出） |
| 游戏数据统计 → 游戏数据统计对比 | stats_game_compare | 09 按游戏消耗（统计范围=游戏名，用户金币消耗/产出） |
| 平台产销 → 平台产销情况 | prod_sales | 08/09 消耗 |
| 平台数据 → 买量数据统计 | daily_acquisition | 03a/03b 渠道 Fallback |
| 充值数据统计 → 充值数据统计 | stats_recharge | 10/11 付费人数/金额按SKU |

输出目录：~/.jachin/client_volumes/bi_data/output/
"""
from __future__ import annotations

import asyncio
import html
import csv
import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# =============================================================================
# BI 意图识别（供 L3 Agent 预检，Lark 对话触发 BI 分析流程）
# =============================================================================

# 命中则直接执行 run_bi_daily_report，不经过 LLM
_BI_INTENT_PATTERN = re.compile(
    r"BI\s*分析|bi\s*分析|帮我开始.*BI|今天的BI分析|开始BI分析|执行BI分析|"
    r"BI\s*日报|bi\s*日报|BI\s*战报|生成战报|今日战报|每日分析|"
    r"跑一下\s*BI|跑一下\s*bi|运行\s*BI|执行\s*BI",
    re.IGNORECASE,
)


def is_bi_analysis_intent(text: str) -> bool:
    """判断用户意图是否为触发 BI 每日分析流程（供 agent_core 预检调用）"""
    if not text or not isinstance(text, str):
        return False
    return bool(_BI_INTENT_PATTERN.search(text.strip()))


# =============================================================================
# 日志与配置
# =============================================================================

_BI_LOG_DIR_DEFAULT = Path(r"D:\zzz\bi\bi日志")
_BI_LOG_DIR: Path = _BI_LOG_DIR_DEFAULT
_BI_LOG_RUN_FILE: Path | None = None

_REQUIRED_SLUGS = [
    "daily_ops_summary",
    "stats_user_dau",         # 日活统计：03a DAU 渠道来源（点击日期展开渠道）
    "stats_user_new",         # 日新用户统计：03b DNU 渠道来源（点击渠道展开）
    "prod_sales",
    "recharge_status",        # 平台充值情况：日期+统计范围展开，_refine_recharge 备用数据源
    "stats_recharge",
    "stats_retention_user",
    "stats_retention_paid",   # 付费用户次留
    "stats_retention_user_compare",
    "stats_retention_paid_compare",  # 付费用户周环比
    "daily_acquisition",
    "stats_game_daily",       # 图2 每日游戏数据（游戏名称+消耗产出）
    "stats_game_compare",     # 图2 游戏数据统计对比（统计范围=游戏名）
]
# 周/月环比数据来源（若存在则填入）
_RETENTION_COMPARE_SLUGS = ["stats_retention_user_compare", "stats_retention_paid_compare"]

# 仪表盘展示链接（Lark 卡片中「打开仪表盘」使用，与 config 中编辑 URL 不同）
_DASHBOARD_DISPLAY_URLS = {
    "仪表盘_用户登录活跃情况": "https://ssgkm409t6q5.sg.larksuite.com/share/base/dashboard/shrlghRi3WdEjFX9aH3LI3Bbf2c",
    "仪表盘_平台留存情况": "https://ssgkm409t6q5.sg.larksuite.com/share/base/dashboard/shrlgr00stzCWoJ2cySEAy4Ryie",
    "仪表盘_平台消耗情况": "https://ssgkm409t6q5.sg.larksuite.com/share/base/dashboard/shrlg77U8jQvTTodea2ZQifK8ab",
}


def _get_bi_log_dir(cfg: dict[str, Any] | None = None) -> Path:
    if cfg:
        storage = cfg.get("storage") or {}
        path = (storage.get("bi_log_dir") or os.environ.get("BI_LOG_DIR", "")).strip()
        if path:
            return Path(path).expanduser().resolve()
    path = os.environ.get("BI_LOG_DIR", "").strip()
    if path:
        return Path(path).expanduser().resolve()
    return _BI_LOG_DIR_DEFAULT


def _bi_log(msg: str, *, detail: str = "", progress: bool = False) -> None:
    """写日志到文件；progress=True 时同时打印到终端（run_bi_analysis 等可见进度）"""
    global _BI_LOG_RUN_FILE
    try:
        _BI_LOG_DIR.mkdir(parents=True, exist_ok=True)
        if _BI_LOG_RUN_FILE is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            _BI_LOG_RUN_FILE = _BI_LOG_DIR / f"bi_{ts}.log"
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
        if detail:
            line += f"\n    {detail}"
        line += "\n"
        with open(_BI_LOG_RUN_FILE, "a", encoding="utf-8") as f:
            f.write(line)
        if progress:
            print(f"[BI] {msg}" + (f" — {detail}" if detail and len(detail) < 80 else ""))
    except Exception as e:
        logger.warning("[BI Log] 写入失败 dir=%s: %s", _BI_LOG_DIR, e)


def _bi_debug(step: str, phase: str, *, detail: str = "", data: dict[str, Any] | None = None, exc: BaseException | None = None) -> None:
    """调试日志：记录步骤 entry/exit/skip/error，便于分析执行中断原因。"""
    global _BI_LOG_RUN_FILE
    try:
        _BI_LOG_DIR.mkdir(parents=True, exist_ok=True)
        if _BI_LOG_RUN_FILE is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            _BI_LOG_RUN_FILE = _BI_LOG_DIR / f"bi_{ts}.log"
        parts = [f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [DEBUG] {step} | {phase}"]
        if detail:
            parts.append(f"\n    detail: {detail}")
        if data:
            parts.append(f"\n    data: {json.dumps(data, ensure_ascii=False)[:500]}")
        if exc is not None:
            import traceback
            tb = traceback.format_exc()
            parts.append(f"\n    exception: {exc}\n    traceback:\n{tb}")
        parts.append("\n")
        with open(_BI_LOG_RUN_FILE, "a", encoding="utf-8") as f:
            f.write("".join(parts))
    except Exception as e:
        logger.debug("[BI Debug Log] 写入失败: %s", e)


def _bi_log_reset() -> None:
    global _BI_LOG_RUN_FILE
    _BI_LOG_RUN_FILE = None


def _resolve_env(val: str) -> str:
    if not isinstance(val, str):
        return val
    m = re.match(r"^\$\{([^}]+)\}$", val.strip())
    if m:
        return os.environ.get(m.group(1), val)
    return val


def _resolve_config_values(cfg: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in cfg.items():
        if isinstance(v, dict):
            out[k] = _resolve_config_values(v)
        elif isinstance(v, list):
            out[k] = [_resolve_env(x) if isinstance(x, str) else x for x in v]
        elif isinstance(v, str):
            out[k] = _resolve_env(v)
        else:
            out[k] = v
    return out


def _load_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    if config and isinstance(config, dict):
        return _resolve_config_values(config)
    from l3_node.paths import get_app_root
    jachin_root = Path.home() / ".jachin"
    project_root = get_app_root()
    candidates = [
        jachin_root / "config" / "skills" / "com.jachin.bi.daily_report" / "bi_daily_report.yaml",
        project_root / "config" / "skills" / "com.jachin.bi.daily_report" / "bi_daily_report.yaml",
    ]
    for path in candidates:
        if path.exists():
            try:
                import yaml
                with open(path, encoding="utf-8") as f:
                    raw = yaml.safe_load(f) or {}
                result = _resolve_config_values(raw)
                da = result.get("dashboard_automation") or {}
                # 若 dashboard_automation 为空（用户目录配置较旧未同步该块），从另一处补全
                if not da or not da.get("dashboards"):
                    for other in candidates:
                        if other != path and other.exists():
                            try:
                                with open(other, encoding="utf-8") as f2:
                                    other_raw = yaml.safe_load(f2) or {}
                                other_da = (other_raw.get("dashboard_automation") or {})
                                if other_da:
                                    result["dashboard_automation"] = {**other_da, **da}
                                    break
                            except Exception:
                                pass
                # 若 distribution.email.to_addrs 仅含占位符（如 ["${BI_SMTP_TO}"] 且环境变量未设），从 project 合并显式列表
                dist = result.get("distribution") or {}
                email_cfg = dist.get("email") or {}
                resolved_to = email_cfg.get("to_addrs") or []
                if isinstance(resolved_to, list):
                    valid = [str(a).strip() for a in resolved_to if str(a).strip() and not str(a).strip().startswith("${")]
                    if not valid and path == candidates[0]:
                        for other in candidates:
                            if other != path and other.exists():
                                try:
                                    with open(other, encoding="utf-8") as f2:
                                        proj_raw = yaml.safe_load(f2) or {}
                                    proj_email = (proj_raw.get("distribution") or {}).get("email") or {}
                                    proj_to = proj_email.get("to_addrs") or []
                                    if isinstance(proj_to, list):
                                        proj_resolved = _resolve_config_values({"x": proj_to})["x"]
                                        proj_valid = [str(a).strip() for a in proj_resolved if str(a).strip() and not str(a).strip().startswith("${")]
                                        if proj_valid:
                                            if "distribution" not in result:
                                                result["distribution"] = {}
                                            if "email" not in result["distribution"]:
                                                result["distribution"]["email"] = dict(email_cfg)
                                            result["distribution"]["email"]["to_addrs"] = proj_valid
                                            break
                                except Exception:
                                    pass
                                break
                return result
            except Exception as e:
                logger.warning("[BI Daily Report] 配置加载失败 %s: %s", path, e)
    return {}


# =============================================================================
# 数据提纯（原 report_refiner 逻辑，内置于 skill）
# =============================================================================

def _find_col(columns: list[str], *candidates: str) -> str | None:
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


def _parse_compare_number(s: str) -> float:
    """解析对比格式如 '226,728,964.00 (+85.54%)122,198,191.00' 或 '158,412,964.00(+92.22%)82,412,191.00'，取第一个数字"""
    if not s:
        return 0.0
    s = str(s).replace(",", "").strip()
    # 取第一个连续数字串（含小数点）
    import re
    m = re.search(r"[\d.]+", s)
    if m:
        try:
            return float(m.group())
        except ValueError:
            return 0.0
    return 0.0


def _query_table(conn: Any, slug: str, date_from: str | None = None, date_to: str | None = None) -> list[dict]:
    from l3_node.mcp_tools.bi.data_store import _sanitize_table_name
    table = _sanitize_table_name(slug)
    try:
        tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
        if table not in tables:
            return []
    except Exception:
        return []

    date_col: str | None = None
    if date_from or date_to:
        cols = [r[0] for r in conn.execute(f"DESCRIBE {table}").fetchall()]
        date_col = _find_col(cols, "日期", "date", "统计日期", "业务日期", "登录时间", "注册时间", "创建日期") or ("_ingested_date" if "_ingested_date" in cols else None)
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
        return [dict(zip(cols, row)) for row in rel.fetchall()]
    except Exception as e:
        logger.warning("[Refiner] query %s: %s", table, e)
        return []


def _date_to_lark_ts(d: str) -> int:
    try:
        dt = datetime.strptime(d[:10], "%Y-%m-%d")
        return int(dt.timestamp() * 1000)
    except ValueError:
        return 0


def _lark_safe_text(v: str) -> str:
    s = (v or "").strip()
    if s and s.replace(".", "").replace("-", "").replace(",", "").isdigit():
        return s + "\u200b"
    return s


def _write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> int:
    """写入 CSV：先写临时文件再替换；Windows 文件被占用时重试（WinError 5/13）"""
    import time

    path.parent.mkdir(parents=True, exist_ok=True)
    # 使用系统临时目录避免与同目录下被占用的目标文件冲突
    import tempfile
    fd, tmp_path_str = tempfile.mkstemp(suffix=".csv.tmp", prefix="bi_", dir=path.parent)
    try:
        with open(fd, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
    except Exception:
        try:
            Path(tmp_path_str).unlink(missing_ok=True)
        except Exception:
            pass
        raise
    tmp_path = Path(tmp_path_str)

    max_attempts = 5
    delays = [1.0, 2.0, 3.0, 4.0, 5.0]
    last_err: Exception | None = None
    for attempt in range(max_attempts):
        try:
            tmp_path.replace(path)
            return len(rows)
        except (PermissionError, OSError) as e:
            last_err = e
            winerr = getattr(e, "winerror", None)
            if attempt < max_attempts - 1 and winerr in (5, 13, None):
                delay = delays[min(attempt, len(delays) - 1)]
                logger.warning("[Refiner] CSV 写入被占用，%s 秒后重试 (%d/%d): %s", delay, attempt + 1, max_attempts, path.name)
                time.sleep(delay)
                continue
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            raise PermissionError(
                f"无法写入 {path.name}，文件可能被 Excel/资源管理器等占用。请关闭后重试。"
            ) from last_err
    return len(rows)


def _refine_user_activity(conn: Any, output_dir: Path, t1: str, t0: str, t7: str) -> list[Path]:
    written: list[Path] = []
    rows = _query_table(conn, "daily_ops_summary", date_from=t7, date_to=t1)
    if not rows:
        logger.warning("[Refiner] daily_ops_summary 无数据")
        return written

    cols = list(rows[0].keys())
    date_col = _find_col(cols, "日期", "date", "统计日期") or "_ingested_date"
    dau_col = _find_col(cols, "日活（DAU）", "日活(DAU)", "日活", "DAU", "dau")
    dnu_col = _find_col(cols, "当日新增用户（DNU）", "当日新增用户", "新增用户(DNU)", "新增用户", "DNU", "dnu")
    if not dau_col or not dnu_col:
        return written

    def _norm_date(v: Any) -> str:
        if v is None:
            return ""
        s = str(v)[:10]
        return s if len(s) == 10 and s.replace("-", "").isdigit() else ""

    by_date = {_norm_date(r.get(date_col)): r for r in rows if _norm_date(r.get(date_col))}
    dates_sorted = sorted([d for d in by_date.keys() if d], reverse=True)[:8]
    d1 = dates_sorted[0] if dates_sorted else t1
    d2 = dates_sorted[1] if len(dates_sorted) >= 2 else None
    r1, r0 = by_date.get(d1, {}), by_date.get(d2, {})
    dau1, dnu1 = _safe_float(r1.get(dau_col)), _safe_float(r1.get(dnu_col))
    dau0, dnu0 = _safe_float(r0.get(dau_col)), _safe_float(r0.get(dnu_col))
    dau_pct = round((dau1 - dau0) / dau0, 4) if dau0 else 0.0
    dnu_pct = round((dnu1 - dnu0) / dnu0, 4) if dnu0 else 0.0

    # 增幅以「百分点」写入（4.13 表示 4.13%），避免 Lark 数字列 formatter="0" 整数格式将 0.0413 截断为 0
    dau_pct_points = round(dau_pct * 100, 2) if dau_pct is not None else 0.0
    dnu_pct_points = round(dnu_pct * 100, 2) if dnu_pct is not None else 0.0
    increase_rows = [{"类型": "DAU", "增幅": dau_pct_points}, {"类型": "DNU", "增幅": dnu_pct_points}]
    _write_csv(output_dir / "01_用户活跃_增幅表.csv", increase_rows, ["类型", "增幅"])
    written.append(output_dir / "01_用户活跃_增幅表.csv")

    daily_rows = []
    for d in (dates_sorted[:7][::-1] if len(dates_sorted) >= 7 else list(reversed(dates_sorted))):
        r = by_date.get(d, {})
        daily_rows.append({"日期": _date_to_lark_ts(d), "DAU数量": int(_safe_float(r.get(dau_col))), "DNU数量": int(_safe_float(r.get(dnu_col)))})
    _write_csv(output_dir / "02_用户活跃_日期数量表.csv", daily_rows, ["日期", "DAU数量", "DNU数量"])
    written.append(output_dir / "02_用户活跃_日期数量表.csv")

    def _agg_channel(rows: list[dict], ch_col: str, count_unique_col: str | None = None) -> list[dict]:
        by_ch: dict[str, int] = {}
        for r in rows:
            ch = str(r.get(ch_col, "") or "").strip() or "（未知）"
            if count_unique_col:
                by_ch[ch] = by_ch.get(ch, 0) + 1
            else:
                by_ch[ch] = by_ch.get(ch, 0) + 1
        return [{"渠道": k, "数量": v} for k, v in sorted(by_ch.items(), key=lambda x: -x[1])[:30]]

    def _extract_channels_from_stat_table(
        rows: list[dict], ch_cands: list[str], count_cands: list[str], skip_labels: tuple[str, ...] = ("全部汇总", "ALL", "> ALL")
    ) -> list[dict]:
        """从日活/日新统计表（已展开）提取渠道+数量，跳过汇总行"""
        if not rows:
            return []
        cols = list(rows[0].keys())
        ch_col = _find_col(cols, *ch_cands)
        count_col = _find_col(cols, *count_cands)
        if not ch_col or not count_col:
            return []
        out: list[dict] = []
        for r in rows:
            ch = str(r.get(ch_col, "") or "").strip()
            if not ch or ch in skip_labels:
                continue
            cnt = int(_safe_float(r.get(count_col, 0)))
            out.append({"渠道": ch, "数量": cnt})
        return sorted(out, key=lambda x: -x["数量"])[:30]

    def _filter_rows_to_single_date(rows: list[dict], date_cands: list[str], t1: str) -> list[dict]:
        """只保留「前一日 t1」的数据。展开后子行可能无日期，视为与首行同一天（表已按日期 DESC 排序）。"""
        if not rows:
            return []
        cols = list(rows[0].keys())
        date_col = _find_col(cols, *date_cands)
        if not date_col:
            return rows
        # 目标日期：取表中第一个非空日期（即最新一天）
        target = ""
        for r in rows:
            v = r.get(date_col)
            if v is not None and str(v).strip():
                target = str(v).strip()[:10]
                break
        if not target:
            return rows
        if target != t1:
            logger.warning("[Refiner] DAU/DNU 表最新日期 %s 与预期前一日 %s 不一致，仍按 %s 筛选", target, t1, target)
        out = []
        for r in rows:
            v = r.get(date_col)
            if v is None or str(v).strip() == "":
                out.append(r)  # 无日期子行视为与首行同一天
            elif str(v).strip()[:10] == target:
                out.append(r)
        return out

    dau_rows, dnu_rows = [], []
    # 03a DAU 渠道：日活统计（BI 数据统计分析->用户数据统计->日活统计），点击日期展开得渠道+日活，只取前一日 t1
    dau_stat = _query_table(conn, "stats_user_dau", date_from=None, date_to=None)
    dau_stat = _filter_rows_to_single_date(dau_stat, ["日期", "date", "统计日期"], t1)
    if dau_stat:
        dau_extracted = _extract_channels_from_stat_table(
            dau_stat,
            ch_cands=["渠道", "channel", "渠道来源"],
            count_cands=["日活（DAU）", "日活(DAU)", "日活", "DAU"],
        )
        dau_rows = [{"DAU渠道来源": x["渠道"], "数量": x["数量"]} for x in dau_extracted]
    # 03b DNU 渠道：日新用户统计（BI 数据统计分析->用户数据统计->日新用户统计），点击渠道展开得渠道+当日新增注册，只取前一日 t1
    dnu_stat = _query_table(conn, "stats_user_new", date_from=None, date_to=None)
    dnu_stat = _filter_rows_to_single_date(dnu_stat, ["日期", "date", "统计日期"], t1)
    if dnu_stat:
        extracted = _extract_channels_from_stat_table(
            dnu_stat,
            ch_cands=["渠道", "channel", "渠道来源"],
            count_cands=["当日新增注册（DNU）", "当日新增注册", "当日新增", "DNU"],
        )
        dnu_rows = [{"DNU渠道来源": x["渠道"], "数量": x["数量"]} for x in extracted]

    # Fallback：stats_user_dau/stats_user_new 无数据时，用明细表或买量统计
    if not dau_rows:
        dau_active = _query_table(conn, "detail_user_active", date_from=t0, date_to=t1)
        if dau_active:
            ac = list(dau_active[0].keys())
            ch = _find_col(ac, "推广渠道", "广告来源", "渠道", "渠道来源", "channel")
            if ch:
                for x in _agg_channel(dau_active, ch, "user"):
                    dau_rows.append({"DAU渠道来源": x["渠道"], "数量": x["数量"]})
    if not dnu_rows:
        dnu_register = _query_table(conn, "detail_user_register", date_from=t0, date_to=t1)
        if dnu_register:
            rc = list(dnu_register[0].keys())
            ch = _find_col(rc, "推广渠道", "广告来源", "渠道", "渠道来源", "channel")
            if ch:
                for x in _agg_channel(dnu_register, ch, "user"):
                    dnu_rows.append({"DNU渠道来源": x["渠道"], "数量": x["数量"]})
    if not dau_rows and not dnu_rows:
        channel_rows = _query_table(conn, "daily_acquisition", date_from=t0, date_to=t1)
        if channel_rows:
            ch_cols = list(channel_rows[0].keys())
            ch_col = _find_col(ch_cols, "渠道", "链接地址", "游戏ID", "来源", "channel")
            type_col = _find_col(ch_cols, "类型", "type")
            count_col = _find_col(ch_cols, "新增注册人数", "数量", "人数", "Count", "用户数")
            for r in channel_rows[:50]:
                typ = str(r.get(type_col or "", "")).strip().upper()
                ch_val = str(r.get(ch_col or "", "")).strip()
                cnt = int(_safe_float(r.get(count_col or "", 0)))
                if not ch_val and not typ:
                    continue
                if "DAU" in typ or typ in ("日活",):
                    dau_rows.append({"DAU渠道来源": ch_val or "（未知）", "数量": cnt})
                elif "DNU" in typ or typ in ("新增",):
                    dnu_rows.append({"DNU渠道来源": ch_val or "（未知）", "数量": cnt})
                elif not type_col and count_col:
                    dnu_rows.append({"DNU渠道来源": ch_val or "（未知）", "数量": cnt})

    if not dau_rows:
        _bi_log("Step 2 提示: 03a DAU 渠道无数据，已写入占位行", detail="若 Lark 表显示 0 或占位符，请设置 skip_collect=false 并重新运行，确保 SPA 抓取「日活统计」时已展开日期/渠道")
    if not dnu_rows:
        _bi_log("Step 2 提示: 03b DNU 渠道无数据，已写入占位行", detail="若 Lark 表显示 0 或占位符，请设置 skip_collect=false 并重新运行，确保 SPA 抓取「日新用户统计」时已展开渠道")
    _write_csv(output_dir / "03a_用户活跃_DAU渠道来源.csv", dau_rows if dau_rows else [{"DAU渠道来源": "（需抓取 stats_user_dau/日活统计）", "数量": 0}], ["DAU渠道来源", "数量"])
    _write_csv(output_dir / "03b_用户活跃_DNU渠道来源.csv", dnu_rows if dnu_rows else [{"DNU渠道来源": "（需抓取 stats_user_new/日新用户统计）", "数量": 0}], ["DNU渠道来源", "数量"])
    written.extend([output_dir / "03a_用户活跃_DAU渠道来源.csv", output_dir / "03b_用户活跃_DNU渠道来源.csv"])
    return written


# 次留表/付费用户次留表：类型 T-2、T-4、T-7（对应 BI 次日/第四日/第七日留存）
# 周环比/付费用户周环比：类型 T-2、T-4、T-6
_RETENTION_TYPE_NEXT = {"t2": "T-2", "t4": "T-4", "t7": "T-7"}
_RETENTION_TYPE_WOW = {"t2": "T-2", "t4": "T-4", "t6": "T-6"}


def _parse_retention_val(s: str) -> tuple[int, float]:
    """解析 '25 (6.63%)' 或 '0 (NaN%)' 格式，返回 (人数, 百分比小数)"""
    s = (s or "").strip()
    num, pct = 0, 0.0
    import re
    m = re.search(r"(\d+)\s*\(\s*([\d.NaN]+)\s*%?\s*\)", s)
    if m:
        num = int(m.group(1))
        p = m.group(2)
        pct = _safe_float(p) / 100.0 if p and p.upper() != "NAN" else 0.0
    elif s.replace(".", "").replace("-", "").isdigit():
        num = int(_safe_float(s))
    return (num, round(pct, 4))


def _refine_retention(conn: Any, output_dir: Path, t1: str) -> list[Path]:
    written: list[Path] = []
    user_rows = _query_table(conn, "stats_retention_user", date_from=None, date_to=None)
    paid_rows = _query_table(conn, "stats_retention_paid", date_from=None, date_to=None)
    # 图1 新增用户留存统计：取第一行（最新日期），渠道=ALL 或 > ALL
    def _filter_platform_rows(rows: list[dict], ch_col: str) -> list[dict]:
        if not rows or not ch_col:
            return rows
        all_rows = [r for r in rows if str(r.get(ch_col, "")).strip().upper() in ("ALL", "> ALL", "全平台")]
        return all_rows if all_rows else rows

    ch_col = _find_col(list(user_rows[0].keys()), "渠道", "channel") if user_rows else None
    if user_rows and ch_col:
        user_rows = _filter_platform_rows(user_rows, ch_col)

    # BI 新增用户留存统计列：次日(T+1)、第四日(T+3)、第七日(T+6) → Lark T-2/T-4/T-7
    user_col_map = [
        (["次日（T+1）留存", "次日(T+1)留存", "次日", "T+1 留存", "次留"], "t2"),
        (["第四日（T+3）留存", "第四日(T+3)留存", "第四日", "T+3 留存", "三留"], "t4"),
        (["第七日（T+6）留存", "第七日(T+6)留存", "第七日", "T+6 留存", "七留"], "t7"),
    ]
    by_type: dict[str, tuple[int, float]] = {}
    if user_rows:
        cols = list(user_rows[0].keys())
        r0 = user_rows[0]  # 第一行最新日期
        for cands, key in user_col_map:
            col = _find_col(cols, *cands)
            if col:
                val = r0.get(col, "")
                n, p = _parse_retention_val(str(val))
                by_type[key] = (n, p)

    # 04 次留表：类型 T-2/T-4/T-7，留存率。数据来源：新增用户留存统计第一行
    next_ret_rows = []
    for k in ("t2", "t4", "t7"):
        n, p = by_type.get(k, (0, 0.0))
        next_ret_rows.append({"类型": _RETENTION_TYPE_NEXT[k], "留存率": round(p * 100, 1)})
    _write_csv(output_dir / "04_留存_次留表.csv", next_ret_rows, ["类型", "留存率"])

    # 05 付费用户次留表：类型 T-2/T-4/T-7，数据来源：新增付费留存统计第一行
    paid_col_map = [
        (["T+1 留存", "次日（T+1）留存", "次日(T+1)留存", "T+1留存"], "t2"),
        (["T+4 留存", "第四日（T+3）留存", "第五日(T+4)留存", "T+4留存"], "t4"),
        (["T+6 留存", "第七日（T+6）留存", "第七日(T+6)留存", "T+6留存"], "t7"),
    ]
    paid_by_type: dict[str, tuple[int, float]] = {}
    if paid_rows:
        pcols = list(paid_rows[0].keys())
        pr0 = paid_rows[0]  # 第一行
        for cands, key in paid_col_map:
            col = _find_col(pcols, *cands)
            if col:
                val = pr0.get(col, "")
                n, p = _parse_retention_val(str(val))
                paid_by_type[key] = (n, p)
    paid_ret_rows = []
    for k in ("t2", "t4", "t7"):
        n, p = paid_by_type.get(k, (0, 0.0))
        paid_ret_rows.append({"类型": _RETENTION_TYPE_NEXT[k], "留存率": round(p * 100, 1)})
    _write_csv(output_dir / "05_留存_付费用户次留表.csv", paid_ret_rows, ["类型", "留存率"])

    def _parse_compare_pct(s: str) -> tuple[str, str]:
        """解析 '7.30%-22.5458%9.43%' 类格式，取第一个和最后一个百分数"""
        import re
        s = (s or "").strip()
        nums = re.findall(r"[\d.]+", s)
        if len(nums) >= 2:
            return (f"{nums[0]}%", f"{nums[-1]}%")
        if len(nums) == 1:
            return (f"{nums[0]}%", "-")
        return ("-", "-")

    def _parse_pct_to_num(s: str) -> float:
        """解析 '9.43%' 或 '9.43' 为数值"""
        s = (str(s or "").strip()).replace("%", "")
        try:
            return round(float(s), 2)
        except ValueError:
            return 0.0

    wow_t2, wow_t4, wow_t6 = 0.0, 0.0, 0.0
    mom_this, mom_last = "-", "-"
    # 06 周环比：来源 stats_retention_user_compare（新增用户留存对比）第一行
    # 列名：T+1 留存率、T+2 留存率、T+4 留存率、T+6 留存率 等
    user_compare = _query_table(conn, "stats_retention_user_compare", date_from=None, date_to=None)
    if not user_compare:
        user_compare = _query_table(conn, "stats_retention_user_compare", date_from=t1, date_to=t1)
    if user_compare:
        cols = list(user_compare[0].keys())
        r = user_compare[0]  # 第一行（周汇总）
        wow_col_map = [
            (["T+1 留存率", "这周T+1留存率", "这周留存率", "本周留存率", "T+1留存率"], "t2"),
            (["T+4 留存率", "这周T+3留存率", "T+3留存率", "T+4留存率"], "t4"),
            (["T+6 留存率", "这周T+6留存率", "T+6留存率", "第七日留存"], "t6"),
        ]
        for cands, key in wow_col_map:
            col = _find_col(cols, *cands)
            if col:
                num = _parse_pct_to_num(str(r.get(col, "") or ""))
                if key == "t2":
                    wow_t2 = num
                elif key == "t4":
                    wow_t4 = num
                else:
                    wow_t6 = num
    # 月环比：遍历 compare slugs 查找
    for slug in _RETENTION_COMPARE_SLUGS:
        compare_rows = _query_table(conn, slug, date_from=None, date_to=None)
        if not compare_rows:
            compare_rows = _query_table(conn, slug, date_from=t1, date_to=t1)
        if not compare_rows:
            continue
        cols = list(compare_rows[0].keys())
        r = compare_rows[0]
        if mom_this == "-":
            m1 = _find_col(cols, "这月留存率", "本月留存率", "这月", "本月", "T+14 留存率", "T+29 留存率", "第十五日(T+14)留存", "第三十日(T+29)留存")
            if m1:
                val = str(r.get(m1, "") or "")
                if "这月" in str(m1) or "本月" in str(m1):
                    mom_this = val.strip() or "-"
                    m2 = _find_col(cols, "上月留存率", "上月", "上月留存")
                    mom_last = str(r.get(m2, "") or "").strip() or "-" if m2 else "-"
                else:
                    mom_this, mom_last = _parse_compare_pct(val)
    # 月环比 Fallback：stats_retention_user_compare 无数据时，从 stats_retention_user 取最新日期的 T+14/T+29
    if mom_this == "-" and mom_last == "-":
        ur = _query_table(conn, "stats_retention_user", date_from=t1, date_to=t1)
        if ur:
            cols = list(ur[0].keys())
            t14 = _find_col(cols, "第十五日(T+14)留存", "第十五日", "T+14")
            t29 = _find_col(cols, "第三十日(T+29)留存", "第三十日", "T+29")
            r0 = ur[0]
            if t14:
                mom_this = str(r0.get(t14, "") or "").strip() or "-"
            if t29:
                mom_last = str(r0.get(t29, "") or "").strip() or "-"
    # 06 周环比：类型 T-2/T-4/T-6，留存率。数据来源：新增用户留存对比第一行
    wow_rows = [{"类型": _RETENTION_TYPE_WOW["t2"], "留存率": wow_t2}, {"类型": _RETENTION_TYPE_WOW["t4"], "留存率": wow_t4}, {"类型": _RETENTION_TYPE_WOW["t6"], "留存率": wow_t6}]
    _write_csv(output_dir / "06_留存_周环比表.csv", wow_rows, ["类型", "留存率"])
    # 07 付费用户周环比：来源 stats_retention_paid_compare（新增付费留存对比）第一行
    # 图5 列名：T+1 留存率、T+2 留存率、T+3 留存率、T+7 留存率（无 T+4/T+6，用 T+3/T+7）
    paid_wow_t2, paid_wow_t4, paid_wow_t6 = 0.0, 0.0, 0.0
    paid_compare = _query_table(conn, "stats_retention_paid_compare", date_from=None, date_to=None)
    if not paid_compare:
        paid_compare = _query_table(conn, "stats_retention_paid_compare", date_from=t1, date_to=t1)
    if paid_compare:
        pcols = list(paid_compare[0].keys())
        pr = paid_compare[0]  # 第一行
        paid_wow_col_map = [
            (["T+1 留存率", "这周T+1留存率", "这周留存率", "本周留存率"], "t2"),
            (["T+3 留存率", "T+4 留存率", "这周T+3留存率", "T+3留存率"], "t4"),
            (["T+7 留存率", "T+6 留存率", "这周T+6留存率", "T+6留存率"], "t6"),
        ]
        for cands, key in paid_wow_col_map:
            col = _find_col(pcols, *cands)
            if col:
                num = _parse_pct_to_num(str(pr.get(col, "") or ""))
                if key == "t2":
                    paid_wow_t2 = num
                elif key == "t4":
                    paid_wow_t4 = num
                else:
                    paid_wow_t6 = num
    # 07 付费用户周环比：类型 T-2/T-4/T-6，数据来源：新增付费留存对比第一行
    paid_wow_rows = [{"类型": _RETENTION_TYPE_WOW["t2"], "留存率": paid_wow_t2}, {"类型": _RETENTION_TYPE_WOW["t4"], "留存率": paid_wow_t4}, {"类型": _RETENTION_TYPE_WOW["t6"], "留存率": paid_wow_t6}]
    _write_csv(output_dir / "07_留存_付费用户周环比表.csv", paid_wow_rows, ["类型", "留存率"])
    _write_csv(output_dir / "12_留存_月环比表.csv", [{"这月留存率": mom_this, "上月留存率": mom_last}], ["这月留存率", "上月留存率"])
    written.extend([output_dir / "04_留存_次留表.csv", output_dir / "05_留存_付费用户次留表.csv", output_dir / "06_留存_周环比表.csv", output_dir / "07_留存_付费用户周环比表.csv", output_dir / "12_留存_月环比表.csv"])
    return written


def _safe_prod_cons(v: Any) -> float:
    """解析产出/消耗值，支持纯数字或对比格式如 '226,728,964.00 (+85.54%)122,198,191.00'"""
    if v is None:
        return 0.0
    s = str(v).strip()
    if "(" in s or "+%" in s:
        return _parse_compare_number(s)
    return _safe_float(v)


def _refine_consumption(conn: Any, output_dir: Path, t1: str, t7: str) -> list[Path]:
    written: list[Path] = []
    date_col_cands = ["日期", "date", "业务日期", "日期对比"]
    prod_col_cands = ["用户金币产出", "用户金币产出总数", "产出", "金币产出"]
    cons_col_cands = ["用户金币消耗", "用户金币消耗总数", "消耗", "金币消耗"]
    game_col_cands = ["统计范围", "游戏名称", "游戏", "游戏名", "汇总项目"]
    agg_labels = ("全平台汇总", "全平台", "总计", "合计", "ALL")
    agg_to_total = {"当日总计": "全部汇总", "汇总(分游戏)": "全部汇总", "总计(分游戏)": "全部汇总", "全量合计": "全部汇总"}

    rows_daily = _query_table(conn, "prod_sales", date_from=t7, date_to=t1)
    if not rows_daily:
        rows_daily = _query_table(conn, "stats_game_daily", date_from=t7, date_to=t1)
    # 09 表：优先 stats_game_daily（每日游戏数据，点击日期展开后含各游戏 用户金币消耗/产出）
    rows_game = _query_table(conn, "stats_game_daily", date_from=None, date_to=None)
    if not rows_game:
        rows_game = _query_table(conn, "prod_sales", date_from=t7, date_to=t1)
    if not rows_game:
        rows_game = _query_table(conn, "stats_game_compare", date_from=None, date_to=None)
    if not rows_daily and not rows_game:
        return written

    cols = list((rows_game or rows_daily)[0].keys())
    cons_col = _find_col(cols, *cons_col_cands)
    prod_col = _find_col(cols, *prod_col_cands)
    game_col = _find_col(cols, *game_col_cands)
    date_col = _find_col(cols, *date_col_cands)

    by_date_daily: dict[str, dict] = {}
    for r in (rows_daily or []):
        d = str(r.get(date_col, ""))[:10]
        if len(d) == 10 and d.replace("-", "").isdigit():
            if d not in by_date_daily:
                by_date_daily[d] = {"日期": d, "产出": 0.0, "消耗": 0.0}
            by_date_daily[d]["产出"] += _safe_prod_cons(r.get(prod_col))
            by_date_daily[d]["消耗"] += _safe_prod_cons(r.get(cons_col))
    dates_sorted = sorted([d for d in by_date_daily if d])[-7:]
    daily_rows = [{"日期": _date_to_lark_ts(d), "产出": round(by_date_daily[d]["产出"], 2), "消耗": round(by_date_daily[d]["消耗"], 2)} for d in dates_sorted]
    if not daily_rows:
        daily_rows = [{"日期": _date_to_lark_ts(t1), "产出": 0.0, "消耗": 0.0}]
    _write_csv(output_dir / "08_消耗_每日表.csv", daily_rows, ["日期", "产出", "消耗"])
    written.append(output_dir / "08_消耗_每日表.csv")

    # 09 每个游戏的产出、消耗：数据来源 stats_game_daily 第一行日期展开后（抓取时已筛选 t1 并展开）
    # 列：统计范围(全量合计/各游戏名)、用户金币消耗、用户金币产出。仅取 t1 或展开行（子行可能无日期）
    game_rows = []
    seen_total = False
    if rows_game and game_col and (prod_col or cons_col):
        for r in rows_game[:80]:
            g = str(r.get(game_col, "") or "").strip()
            if not g:
                continue
            if g in agg_labels:
                continue
            display_name = agg_to_total.get(g, g)
            if display_name == "全部汇总" and seen_total:
                continue
            if display_name == "全部汇总":
                seen_total = True
            prod_val = _safe_prod_cons(r.get(prod_col)) if prod_col else 0.0
            cons_val = _safe_prod_cons(r.get(cons_col)) if cons_col else 0.0
            game_rows.append({"游戏名称": display_name, "产出": round(prod_val, 2), "消耗": round(cons_val, 2)})
    if not game_rows and rows_game:
        for r in rows_game[:50]:
            g = str(r.get(game_col, "") or "").strip() or "（未知）"
            display_name = agg_to_total.get(g, g)
            if display_name == "全部汇总" and seen_total:
                continue
            if display_name == "全部汇总":
                seen_total = True
            game_rows.append({"游戏名称": display_name, "产出": round(_safe_prod_cons(r.get(prod_col)), 2), "消耗": round(_safe_prod_cons(r.get(cons_col)), 2)})
    if not game_rows:
        game_rows = [{"游戏名称": "（需抓取 stats_game_daily 每日游戏数据并展开日期）", "产出": 0.0, "消耗": 0.0}]
    if len(game_rows) == 1 and game_rows[0].get("游戏名称") in ("全部汇总", "（需抓取 stats_game_daily 每日游戏数据并展开日期）"):
        _bi_log("09表仅含汇总行，无按游戏明细", detail="请确保 skip_collect=false 且 stats_game_daily 抓取时已展开首行日期")
    _write_csv(output_dir / "09_消耗_按游戏表.csv", game_rows, ["游戏名称", "产出", "消耗"])
    written.append(output_dir / "09_消耗_按游戏表.csv")
    return written


def _format_recharge_tier_label(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return "（未知）"
    if s.replace(".", "").replace("-", "").replace(",", "").isdigit():
        try:
            v = float(s)
            return f"{int(v) if v == int(v) else v}元"
        except ValueError:
            return s
    return s


def _aggregate_recharge_from_detail(conn: Any, date_from: str, date_to: str) -> list[dict]:
    """从 detail_recharge_daily（每日充值明细）按充值金额档位聚合"""
    rows = _query_table(conn, "detail_recharge_daily", date_from=date_from, date_to=date_to)
    if not rows:
        return []
    cols = list(rows[0].keys())
    amt_col = _find_col(cols, "充值金额", "金额", "amount", "总金额", "recharge_amount")
    if not amt_col:
        return []
    tiers = [6, 12, 18, 30, 50, 68, 98, 100, 198, 328, 648]  # 含 50/100 匹配 BI 图表 (0,50],(50,100]
    by_tier: dict[str, dict] = {}
    user_col = _find_col(cols, "用户ID", "user_id", "用户", "uid", "用户uid", "用户id")
    for r in rows:
        amt = _safe_float(r.get(amt_col))
        label = "6元"
        for t in sorted(tiers):
            if amt <= t:
                label = f"{t}元"
                break
            if amt > tiers[-1]:
                label = f"{tiers[-1]}+元"
        if label not in by_tier:
            by_tier[label] = {"不同充值金额分等级": label, "人数": 0, "此等级总金额": 0.0, "_users": set()}
        by_tier[label]["此等级总金额"] += amt
        if user_col:
            uid = str(r.get(user_col, ""))
            if uid and uid not in by_tier[label]["_users"]:
                by_tier[label]["_users"].add(uid)
                by_tier[label]["人数"] += 1
        else:
            by_tier[label]["人数"] += 1
    for v in by_tier.values():
        v.pop("_users", None)
    return list(by_tier.values())


def _aggregate_recharge_from_recharge_daily(conn: Any, date_from: str, date_to: str) -> list[dict]:
    """从 recharge_daily（用户每日充值汇总，平台充值情况同源）按当日充值总额档位聚合"""
    rows = _query_table(conn, "recharge_daily", date_from=date_from, date_to=date_to)
    if not rows:
        return []
    cols = list(rows[0].keys())
    amt_col = _find_col(cols, "当日充值总额", "当日首笔充值金额", "充值金额", "金额")
    if not amt_col:
        return []
    tiers = [6, 12, 18, 30, 50, 68, 98, 100, 198, 328, 648]  # 含 50/100 匹配 BI 图表
    by_tier: dict[str, dict] = {}
    user_col = _find_col(cols, "用户ID", "用户id", "user_id", "uid", "用户uid")
    for r in rows:
        amt = _safe_float(r.get(amt_col))
        label = "6元"
        for t in sorted(tiers):
            if amt <= t:
                label = f"{t}元"
                break
            if amt > tiers[-1]:
                label = f"{tiers[-1]}+元"
        if label not in by_tier:
            by_tier[label] = {"不同充值金额分等级": label, "人数": 0, "此等级总金额": 0.0, "_users": set()}
        by_tier[label]["此等级总金额"] += amt
        if user_col:
            uid = str(r.get(user_col, ""))
            if uid and uid not in by_tier[label]["_users"]:
                by_tier[label]["_users"].add(uid)
                by_tier[label]["人数"] += 1
        else:
            by_tier[label]["人数"] += 1
    for v in by_tier.values():
        v.pop("_users", None)
    return list(by_tier.values())


def _refine_recharge(conn: Any, output_dir: Path, t1: str, days: int = 7) -> list[Path]:
    written: list[Path] = []
    dt = datetime.strptime(t1[:10], "%Y-%m-%d")
    date_from = (dt - timedelta(days=days)).strftime("%Y-%m-%d")

    # 图3 平台充值情况 的数据来源：recharge_daily（用户每日充值汇总）、detail_recharge_daily（每日充值明细）
    # 优先用这两表按档位聚合，与后台「充值金额与人数趋势图」一致
    rows = _aggregate_recharge_from_recharge_daily(conn, date_from, t1)
    if not rows:
        rows = _aggregate_recharge_from_detail(conn, date_from, t1)
    if not rows:
        recharge_slugs = ["recharge_status", "stats_recharge", "recharge_daily", "recharge_history"]
        for slug in recharge_slugs:
            raw_rows = _query_table(conn, slug, date_from=date_from, date_to=t1)
            if not raw_rows:
                continue
            cols = list(raw_rows[0].keys())
            sku_col = _find_col(cols, "充值金额", "SKU", "渠道", "金额档位", "等级", "不同充值金额分等级")
            count_col = _find_col(cols, "人数", "付费人数", "充值人数")
            amount_col = _find_col(cols, "总金额", "当日充值总额", "此等级总金额", "充值金额")
            if sku_col and count_col and amount_col:
                vals = set(str(r.get(sku_col, "")).strip().upper() for r in raw_rows)
                if vals <= {"ALL", ""} or (len(vals) == 1 and "ALL" in vals):
                    continue
                rows = raw_rows
                break
            if not sku_col:
                rows = raw_rows
                break

    if not rows:
        _write_csv(output_dir / "10_充值_付费人数按SKU.csv", [{"不同充值金额分等级": "（需抓取充值数据）", "人数": 0}], ["不同充值金额分等级", "人数"])
        _write_csv(output_dir / "11_充值_付费金额按SKU.csv", [{"不同充值金额分等级": "（需抓取充值数据）", "此等级总金额": 0.0}], ["不同充值金额分等级", "此等级总金额"])
        return [output_dir / "10_充值_付费人数按SKU.csv", output_dir / "11_充值_付费金额按SKU.csv"]

    cols = list(rows[0].keys())
    sku_col = _find_col(cols, "充值金额", "SKU", "金额档位", "等级", "不同充值金额", "不同充值金额分等级", "amount", "price", "档位", "金额等级", "渠道")
    count_col = _find_col(cols, "人数", "付费人数", "用户数", "count", "充值人数")
    amount_col = _find_col(cols, "总金额", "金额", "此等级总金额", "amount", "当日充值总额")
    SKU_COL = "不同充值金额分等级"

    if sku_col:
        by_sku: dict[str, dict] = {}
        for r in rows:
            raw = str(r.get(sku_col, ""))
            k = _format_recharge_tier_label(raw)
            if k not in by_sku:
                by_sku[k] = {SKU_COL: k, "人数": 0, "此等级总金额": 0.0}
            by_sku[k]["人数"] += int(_safe_float(r.get(count_col)))
            by_sku[k]["此等级总金额"] += _safe_float(r.get(amount_col))
        out_rows = list(by_sku.values())
    else:
        total_count = sum(int(_safe_float(r.get(count_col))) for r in rows)
        total_amount = sum(_safe_float(r.get(amount_col)) for r in rows)
        out_rows = [{SKU_COL: "合计", "人数": total_count, "此等级总金额": round(total_amount, 2)}]

    p10, p11 = output_dir / "10_充值_付费人数按SKU.csv", output_dir / "11_充值_付费金额按SKU.csv"
    try:
        _write_csv(p10, [{SKU_COL: _lark_safe_text(str(r[SKU_COL])), "人数": r["人数"]} for r in out_rows], [SKU_COL, "人数"])
        written.append(p10)
    except PermissionError as e:
        logger.warning("[Refiner] 无法写入 %s（可能被占用）: %s", p10.name, e)
    try:
        _write_csv(p11, [{SKU_COL: _lark_safe_text(str(r[SKU_COL])), "此等级总金额": round(r["此等级总金额"], 2)} for r in out_rows], [SKU_COL, "此等级总金额"])
        written.append(p11)
    except PermissionError as e:
        logger.warning("[Refiner] 无法写入 %s（可能被占用）: %s", p11.name, e)
    return written


def _run_refiner(date_str: str | None = None, output_dir: Path | None = None) -> tuple[list[Path], list[str]]:
    from l3_node.mcp_tools.bi.data_store import _get_conn
    from l3_node.mcp_tools.bi.paths import get_bi_output_dir, ensure_bi_dirs
    ensure_bi_dirs()
    out = output_dir or get_bi_output_dir()
    dt = datetime.now()
    if date_str:
        try:
            dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
        except ValueError:
            dt = datetime.now()
    t1 = (dt - timedelta(days=1)).strftime("%Y-%m-%d")
    t0 = (dt - timedelta(days=2)).strftime("%Y-%m-%d")
    t7 = (dt - timedelta(days=7)).strftime("%Y-%m-%d")

    conn = _get_conn()
    written: list[Path] = []
    errors: list[str] = []
    try:
        written += _refine_user_activity(conn, out, t1, t0, t7)
        written += _refine_retention(conn, out, t1)
        written += _refine_consumption(conn, out, t1, t7)
        written += _refine_recharge(conn, out, t1, days=7)
    except Exception as e:
        errors.append(str(e))
        logger.exception("[Refiner] 提纯异常: %s", e)
    finally:
        conn.close()
    return (written, errors)


def _sync_refiner_to_lark(
    written_paths: list[Path],
    lark_bitable_config: dict[str, Any],
    on_table: None | Any = None,
) -> tuple[int, list[str]]:
    if not lark_bitable_config.get("enabled"):
        return (0, [])
    _cid = (lark_bitable_config.get("app_id") or "").strip()
    _csec = (lark_bitable_config.get("app_secret") or "").strip()
    if _cid and _csec:
        os.environ.setdefault("LARK_APP_ID", _cid)
        os.environ.setdefault("LARK_APP_SECRET", _csec)
    if lark_bitable_config.get("lark_use_feishu"):
        os.environ["LARK_USE_FEISHU"] = "1"

    app_token = (lark_bitable_config.get("app_token") or "").strip() or None
    tables_map = lark_bitable_config.get("tables") or {}
    if not app_token or not tables_map:
        return (0, ["lark_bitable.app_token 或 tables 未配置"])

    try:
        import sys
        from l3_node.paths import get_app_root
        plugin_root = get_app_root() / "skills_repo" / "plugin" / "2-track-a-atomic-mcp"
        if plugin_root.exists() and str(plugin_root) not in sys.path:
            sys.path.insert(0, str(plugin_root))
        from tools.atom_lark_bitable_sync import sync_csv_to_bitable  # type: ignore[import-untyped]
    except ImportError as e:
        return (0, [f"无法导入 atom_lark_bitable_sync: {e}"])

    ok_count = 0
    errors: list[str] = []
    default_text_columns = {"10_充值_付费人数按SKU.csv": ["不同充值金额分等级"], "11_充值_付费金额按SKU.csv": ["不同充值金额分等级"]}
    text_cols_per_table = lark_bitable_config.get("text_columns") or {}
    field_mapping_per_table = dict(lark_bitable_config.get("field_mapping") or {})
    # 若 Lark 表用「增量」而非「增幅」，可在 config 中配置 field_mapping: {"01_用户活跃_增幅表.csv": {"增幅":"增量"}}

    replace_table_default = lark_bitable_config.get("replace_table", False)
    replace_tables = lark_bitable_config.get("replace_tables")
    if replace_tables is None:
        replace_tables = None  # 下面对「未配置」做特殊处理
    else:
        replace_tables = set(replace_tables)
    total = len([p for p in written_paths if (tables_map.get(p.name) or "").strip()])
    idx = 0
    for p in written_paths:
        name = p.name
        table_id = (tables_map.get(name) or "").strip()
        if not table_id:
            if on_table:
                on_table("skip", name, "未配置 table_id")
            continue
        idx += 1
        if on_table:
            on_table("start", name, f"[{idx}/{total}] table_id={table_id}")
        text_cols = text_cols_per_table.get(name) or default_text_columns.get(name)
        col_mapping = field_mapping_per_table.get(name)
        # replace_tables 未配置时：全部表先清空再写入（覆盖）；配置了则按名单决定
        if replace_tables is None:
            do_replace = True  # 未配置 replace_tables 时，默认全部覆盖
        else:
            do_replace = replace_table_default or (name in replace_tables)
        result = sync_csv_to_bitable(csv_path=str(p), app_token=app_token, table_id=table_id, replace_table=do_replace, ensure_columns=lark_bitable_config.get("ensure_columns", False), text_columns=text_cols, field_mapping=col_mapping)
        if result.get("success"):
            ok_count += 1
            if on_table:
                on_table("ok", name, "同步成功")
        else:
            err = result.get("error", "未知错误")
            errors.append(f"{name}: {err}")
            if on_table:
                on_table("fail", name, err)
    return (ok_count, errors)


# =============================================================================
# 主流程
# =============================================================================

def _is_duckdb_fresh_for_today() -> bool:
    """检查 bi.duckdb 是否含今日入库数据；优先检查 daily_ops_summary（DAU/DNU 核心表）"""
    try:
        from l3_node.mcp_tools.bi.data_store import list_available_dates
    except ImportError:
        return False
    today = datetime.now().strftime("%Y-%m-%d")
    priority = ["daily_ops_summary", "stats_user_dau", "stats_user_new", "prod_sales", "stats_recharge", "stats_retention_user", "daily_acquisition"]
    for slug in priority:
        if slug not in _REQUIRED_SLUGS:
            continue
        dates = list_available_dates(slug)
        if dates and today in dates:
            return True
    for slug in _REQUIRED_SLUGS:
        if slug in priority:
            continue
        dates = list_available_dates(slug)
        if dates and today in dates:
            return True
    return False


async def _run_bi_daily_report_async(config: dict[str, Any] | None = None) -> dict[str, Any]:
    global _BI_LOG_DIR
    _bi_debug("_run_bi_daily_report_async", "entry", data={"config_keys": list((config or {}).keys())})
    cfg = _load_config(config)
    _BI_LOG_DIR = _get_bi_log_dir(cfg)
    _bi_log_reset()
    _bi_log("========== BI 每日战报流程开始 ==========", progress=True)
    _bi_log("日志目录", detail=str(_BI_LOG_DIR))
    _bi_debug("init", "config_loaded", data={"output_refiner": str((cfg.get("storage") or {}).get("refiner_output_path", "")), "skip_collect": cfg.get("skip_collect"), "lark_enabled": (cfg.get("lark_bitable") or {}).get("enabled")})
    result: dict[str, Any] = {"success": False, "stage": "init", "data_updated": False, "output_paths": [], "lark_sync_ok": 0, "lark_sync_errors": [], "strategic_report_sent": False, "dashboard_analysis_sent": False, "email_ok": False, "email_error": "", "error": ""}

    from l3_node.mcp_tools.bi.paths import get_bi_output_dir, ensure_bi_dirs
    _bi_debug("Step 0", "entry", detail="ensure_bi_dirs")
    ensure_bi_dirs()
    output_dir = get_bi_output_dir((cfg.get("storage") or {}).get("refiner_output_path") or "")
    output_dir.mkdir(parents=True, exist_ok=True)
    _bi_log("Step 0: 配置加载完成", detail=f"output_dir={output_dir}", progress=True)

    _bi_debug("Step 1", "entry", detail="检查 DuckDB 今日数据")
    _bi_log("Step 1: 检查 DuckDB 数据是否为今日最新...", progress=True)
    duckdb_fresh = _is_duckdb_fresh_for_today()
    _bi_debug("Step 1", "exit", data={"duckdb_fresh": duckdb_fresh})
    if not duckdb_fresh:
        _bi_log("Step 1 结果: DuckDB 无今日数据，需要抓取更新")
        _bi_debug("Step 1.1", "branch", data={"skip_collect": cfg.get("skip_collect")})
        if not cfg.get("skip_collect", False):
            _bi_log("Step 1.1: 开始执行 SPA 抓取...")
            _bi_debug("Step 1.1", "entry", detail="run_full_spa_collect")
            try:
                from l3_node.mcp_tools.bi.spa_collector import run_full_spa_collect
                from l3_node.mcp_tools.bi.paths import get_bi_raw_dir
                full_spa = cfg.get("full_spa") or {}
                base_url = full_spa.get("base_url") or "https://bi-admin-web.heronpro.xin/#/layout/person"
                cdp_url = full_spa.get("cdp_url") or "http://127.0.0.1:9222"
                slugs = full_spa.get("slugs") or _REQUIRED_SLUGS
                ok, fail, failed = await asyncio.to_thread(run_full_spa_collect, slugs=slugs, base_url=base_url, cdp_url=cdp_url, use_discover=False, auto_ingest=True, raw_dir=get_bi_raw_dir())
                if ok > 0:
                    result["data_updated"] = True
                elif fail > 0 and ok == 0:
                    result["stage"] = "collect"
                    result["error"] = f"抓取全部失败: {failed[:5]}"
                    _bi_debug("Step 1.1", "error_return", data={"ok": ok, "fail": fail}, detail=result["error"])
                    _bi_log("Step 1.1 报错: 抓取全部失败", detail=result["error"])
                    return result
                _bi_debug("Step 1.1", "exit", data={"ok": ok, "fail": fail})
            except Exception as e:
                result["stage"] = "collect"
                result["error"] = str(e)
                _bi_debug("Step 1.1", "error_return", exc=e, data={"stage": "collect"})
                _bi_log("Step 1.1 报错: 抓取异常", detail=str(e))
                return result
        else:
            _bi_log("Step 1.1: 已跳过抓取 (skip_collect=true)", detail="提纯将使用 DuckDB 现有数据；若图1/2/3 异常，请设 skip_collect=false 并启动 Chrome 调试模式后重新运行以抓取最新表格。")
    else:
        _bi_log("Step 1 结果: DuckDB 已有今日数据，跳过抓取")

    _bi_debug("Step 2", "entry", detail="运行提纯 _run_refiner")
    _bi_log("Step 2: 运行提纯，输出 CSV 到 output 目录...", progress=True)
    try:
        date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        written, errs = _run_refiner(date_str=date_str, output_dir=output_dir)
        result["output_paths"] = [str(p) for p in written]
        _bi_debug("Step 2", "exit", data={"written_count": len(written), "errs_count": len(errs) if errs else 0})
        _bi_log("Step 2 结果: 提纯完成", detail=f"输出文件数={len(written)} 路径={[p.name for p in written]}", progress=True)
        if errs:
            _bi_log("Step 2 警告: Refiner 部分失败", detail=str(errs))
    except Exception as e:
        result["stage"] = "refiner"
        result["error"] = str(e)
        _bi_debug("Step 2", "error_return", exc=e, data={"stage": "refiner"})
        _bi_log("Step 2 报错: Refiner 异常", detail=str(e))
        return result

    lark_bitable = cfg.get("lark_bitable") or {}
    _bi_debug("Step 3", "branch", data={"lark_enabled": lark_bitable.get("enabled"), "has_output_paths": bool(result["output_paths"])})
    if lark_bitable.get("enabled") and result["output_paths"]:
        _bi_debug("Step 3", "entry", data={"paths_count": len(result["output_paths"])})
        _bi_log("Step 3: 同步到 Lark 多维表格...", progress=True)
        _bi_log("Step 3: 待同步文件列表", detail=", ".join(Path(p).name for p in result["output_paths"]))

        def _on_table(status: str, name: str, detail: str) -> None:
            if status == "skip":
                _bi_log("Step 3: 跳过表", detail=f"{name} — {detail}")
            elif status == "start":
                _bi_log("Step 3: 同步表中", detail=f"{name} | {detail}", progress=True)
            elif status == "ok":
                _bi_log("Step 3: 表同步成功", detail=name)
            elif status == "fail":
                _bi_log("Step 3: 表同步失败", detail=f"{name}: {detail}")

        try:
            sync_ok, sync_errs = _sync_refiner_to_lark([Path(p) for p in result["output_paths"]], lark_bitable, on_table=_on_table)
            result["lark_sync_ok"] = sync_ok
            result["lark_sync_errors"] = sync_errs
            _bi_debug("Step 3", "exit", data={"sync_ok": sync_ok, "sync_errs_count": len(sync_errs)})
            if sync_errs:
                _bi_log("Step 3 结果: 部分成功", detail=f"成功={sync_ok} 错误={sync_errs}")
            else:
                _bi_log("Step 3 结果: 全部同步成功", detail=f"成功表数={sync_ok}", progress=True)
        except Exception as e:
            _bi_debug("Step 3", "exception", exc=e, data={"lark_sync_errors": [str(e)]})
            result["lark_sync_errors"] = [str(e)]
            _bi_log("Step 3 报错: Lark 同步异常", detail=str(e))
    else:
        if not lark_bitable.get("enabled"):
            _bi_log("Step 3: 已跳过 Lark 同步 (lark_bitable.enabled=false)")
        elif not result["output_paths"]:
            _bi_log("Step 3: 已跳过 Lark 同步 (无输出文件)")

    # Step 3.5: 同步完 Lark 多维表后 — 自动用 Lark 机器人向用户汇报分析数据（调用战略分析 + 推送）
    strategic_cfg = cfg.get("strategic_report") or {}
    _bi_debug("Step 3.5", "branch", data={"enabled": strategic_cfg.get("enabled", True)})
    if strategic_cfg.get("enabled", True):
        _bi_debug("Step 3.5", "entry", detail="generate_bi_strategic_report_async")
        _bi_log("Step 3.5: 开始数据分析，调用 LLM 生成战略深度分析战报...", progress=True)
        try:
            from l3_node.skills.bi.bi_daily_report.strategic_report import generate_bi_strategic_report_async
            strategic_md = await generate_bi_strategic_report_async(
                metrics=None,
                output_dir=output_dir,
                config=cfg,
            )
            result["strategic_report"] = strategic_md
            _bi_debug("Step 3.5", "exit", data={"report_len": len(strategic_md), "push_to_lark": strategic_cfg.get("push_to_lark", True)})
            _bi_log("Step 3.5 结果: 战略报告已生成", detail=f"长度={len(strategic_md)} 字符", progress=True)
            if strategic_cfg.get("push_to_lark", True) and strategic_md:
                dist = cfg.get("distribution") or {}
                webhook = (dist.get("lark_webhook_url") or "").strip()
                chat_id = (dist.get("lark_chat_id") or "").strip()
                if str(webhook).startswith("${"):
                    webhook = ""
                if str(chat_id).startswith("${"):
                    chat_id = ""
                if not chat_id and not webhook:
                    chat_id = (os.environ.get("BI_LARK_CHAT_ID") or os.environ.get("LARK_CHAT_ID") or "").strip()
                if not chat_id and not webhook:
                    try:
                        from l3_node.jachin_config import load_mcp_config
                        from l3_node.paths import get_app_root
                        mcp_cfg = load_mcp_config("atom_lark_notifier", project_root=get_app_root())
                        chat_id = (mcp_cfg.get("default_chat_id") or "").strip()
                        if str(chat_id).startswith("${"):
                            chat_id = ""
                        if chat_id:
                            _bi_log("Step 3.5: 使用 atom_lark_notifier 的 default_chat_id 作为推送目标", detail=chat_id[:20] + "...")
                    except Exception:
                        pass
                if webhook or chat_id:
                    _bi_log("Step 3.5: 正在将战略报告推送到 Lark 机器人...", detail=f"chat_id={'已配置' if chat_id else '无'}, webhook={'已配置' if webhook else '无'}")
                    try:
                        lark_cfg = cfg.get("lark_bitable") or {}
                        if lark_cfg.get("app_id") and lark_cfg.get("app_secret"):
                            os.environ.setdefault("LARK_APP_ID", str(lark_cfg.get("app_id", "")).strip())
                            os.environ.setdefault("LARK_APP_SECRET", str(lark_cfg.get("app_secret", "")).strip())
                        if lark_cfg.get("lark_use_feishu"):
                            os.environ["LARK_USE_FEISHU"] = "1"
                        from l3_node.mcp_tools.bi.tool_lark_notifier import send_lark_markdown
                        r = send_lark_markdown(webhook or "", strategic_md, title="📊 BI 战略深度分析战报", chat_id=chat_id or None)
                        if r.get("status") == "success":
                            _bi_log("Step 3.5: 战略报告已推送至 Lark", detail="发送成功", progress=True)
                            result["strategic_report_sent"] = True
                        else:
                            result["strategic_report_sent"] = False
                            _bi_log("Step 3.5 警告: Lark 推送失败", detail=r.get("error", ""))
                    except Exception as e:
                        result["strategic_report_sent"] = False
                        _bi_log("Step 3.5 警告: Lark 推送异常", detail=str(e))
                else:
                    _bi_log("Step 3.5: 已跳过推送 (未配置 distribution.lark_chat_id / lark_webhook_url 或 atom_lark_notifier.default_chat_id)")
        except Exception as e:
            result["strategic_report_error"] = str(e)
            _bi_debug("Step 3.5", "exception", exc=e, data={"stage": "strategic_report"})
            _bi_log("Step 3.5 警告: 战略分析异常（不影响主流程）", detail=str(e))
    else:
        _bi_debug("Step 3.5", "skip", data={"reason": "strategic_report.enabled=false"})
        _bi_log("Step 3.5: 已跳过数据分析 (strategic_report.enabled=false)")

    # Step 4a: 生成所有仪表盘分析（供邮件 + Lark 使用）
    dist = cfg.get("distribution") or {}
    da_cfg = cfg.get("dashboard_automation") or {}
    dashboard_analyses: list[tuple[str, str]] = []
    _bi_debug("Step 4a", "branch", data={"da_enabled": da_cfg.get("enabled"), "dashboards_count": len(da_cfg.get("dashboards") or [])})
    if da_cfg.get("enabled", False):
        dashboards = da_cfg.get("dashboards") or []
        _bi_debug("Step 4a", "entry", data={"dashboards": [d.get("name", "") for d in dashboards if isinstance(d, dict)]})
        analysis_output_subdir = str(da_cfg.get("analysis_output_subdir") or "统计分析").strip() or "统计分析"
        analysis_output_dir = output_dir / analysis_output_subdir
        analysis_output_dir.mkdir(parents=True, exist_ok=True)
        _bi_log("Step 4a: 生成仪表盘 LLM 分析（供邮件 + Lark 定时）...", progress=True)
        for i, dash in enumerate(dashboards):
            if not isinstance(dash, dict):
                continue
            name = (dash.get("name") or "").strip()
            if not name:
                continue
            try:
                _bi_debug("Step 4a", "dashboard_start", data={"index": i + 1, "name": name})
                from l3_node.skills.bi.bi_daily_report.dashboard_automation import (
                    generate_dashboard_analysis_async,
                    _save_analysis_to_file,
                )
                analysis = await generate_dashboard_analysis_async(name, output_dir, cfg)
                saved_path = _save_analysis_to_file(analysis_output_dir, name, analysis)
                dashboard_analyses.append((name, analysis))
                _bi_debug("Step 4a", "dashboard_done", data={"name": name, "saved": str(saved_path.name)})
                _bi_log("Step 4a: 仪表盘分析已生成", detail=f"[{i + 1}/{len(dashboards)}] {name} → {saved_path.name}")
            except Exception as e:
                _bi_debug("Step 4a", "dashboard_fail", exc=e, data={"name": name})
                _bi_log("Step 4a 警告: 仪表盘分析失败", detail=f"{name}: {e}")
        _bi_debug("Step 4a", "exit", data={"analyses_count": len(dashboard_analyses)})
        if not dashboard_analyses:
            _bi_log("Step 4a: 无仪表盘分析产出", detail="跳过后续邮件仪表盘段与 Step 4b", progress=True)
        elif da_cfg.get("push_dashboard_to_lark", True):
            # 将仪表盘分析作为 Lark 卡片消息发送到同一会话（与战略报告同一 chat_id）
            _lark_webhook = (dist.get("lark_webhook_url") or "").strip()
            _lark_chat_id = (dist.get("lark_chat_id") or "").strip()
            if str(_lark_webhook).startswith("${"):
                _lark_webhook = ""
            if str(_lark_chat_id).startswith("${"):
                _lark_chat_id = ""
            if not _lark_chat_id and not _lark_webhook:
                _lark_chat_id = (os.environ.get("BI_LARK_CHAT_ID") or os.environ.get("LARK_CHAT_ID") or "").strip()
            if not _lark_chat_id and not _lark_webhook:
                try:
                    from l3_node.jachin_config import load_mcp_config
                    from l3_node.paths import get_app_root
                    _mcp = load_mcp_config("atom_lark_notifier", project_root=get_app_root())
                    _lark_chat_id = (_mcp.get("default_chat_id") or "").strip()
                    if str(_lark_chat_id).startswith("${"):
                        _lark_chat_id = ""
                except Exception:
                    pass
            if _lark_webhook or _lark_chat_id:
                # 每个仪表盘单独发一条 Lark 消息卡片：分析内容 + 仪表盘 URL
                dashboards = da_cfg.get("dashboards") or []
                url_by_name = {str(d.get("name", "")).strip(): str(d.get("url", "")).strip() for d in dashboards if isinstance(d, dict)}
                try:
                    lark_cfg = cfg.get("lark_bitable") or {}
                    if lark_cfg.get("app_id") and lark_cfg.get("app_secret"):
                        os.environ.setdefault("LARK_APP_ID", str(lark_cfg.get("app_id", "")).strip())
                        os.environ.setdefault("LARK_APP_SECRET", str(lark_cfg.get("app_secret", "")).strip())
                    if lark_cfg.get("lark_use_feishu"):
                        os.environ["LARK_USE_FEISHU"] = "1"
                    from l3_node.mcp_tools.bi.tool_lark_notifier import send_lark_markdown
                    sent_ok = 0
                    for _name, _text in dashboard_analyses:
                        _url = _DASHBOARD_DISPLAY_URLS.get(_name) or url_by_name.get(_name, "")
                        if _url and not _url.startswith("${"):
                            _card_md = f"{_text}\n\n---\n\n[打开仪表盘]({_url})"
                        else:
                            _card_md = _text
                        _r = send_lark_markdown(_lark_webhook or "", _card_md[:6000], title=f"📊 {_name}", chat_id=_lark_chat_id or None)
                        if _r.get("status") == "success":
                            sent_ok += 1
                            _bi_log("Step 4a: 仪表盘卡片已推送", detail=f"{_name}")
                        else:
                            _bi_log("Step 4a 警告: 仪表盘卡片推送失败", detail=f"{_name}: {_r.get('error', '')}")
                    if sent_ok:
                        result["dashboard_analysis_sent"] = True
                        _bi_log("Step 4a: 仪表盘分析已推送至 Lark", detail=f"共 {sent_ok}/{len(dashboard_analyses)} 条", progress=True)
                except Exception as e:
                    _bi_log("Step 4a 警告: 仪表盘分析 Lark 推送异常", detail=str(e))
            else:
                _bi_log("Step 4a: 未配置 Lark chat_id/webhook，跳过仪表盘分析推送")

    # Step 3.6: 数据分析完成后马上发送邮件（战略分析 + 仪表盘分析）
    email_cfg = dist.get("email") or {}
    _bi_debug("Step 3.6", "branch", data={"email_enabled": email_cfg.get("enabled", True), "is_dict": isinstance(email_cfg, dict)})
    if email_cfg.get("enabled", True) and isinstance(email_cfg, dict):
        _bi_debug("Step 3.6", "entry", detail="发送邮件")
        email_sched = str(email_cfg.get("scheduled_time") or "").strip()
        if email_sched:
            _bi_debug("Step 3.6", "scheduled_wait", data={"scheduled_time": email_sched})
            _bi_log("Step 3.6: 邮件定时发送", detail=f"配置时间={email_sched}，等待至该时刻再发送", progress=True)
            try:
                parts = email_sched.split(":")
                h, m = int(parts[0]) if len(parts) >= 1 else 18, int(parts[1]) if len(parts) >= 2 else 8
                schedule_dt = datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)
                if schedule_dt > datetime.now():
                    wait_sec = (schedule_dt - datetime.now()).total_seconds()
                    _bi_log("Step 3.6: 等待定时", detail=f"约 {int(wait_sec)} 秒后发送")
                    await asyncio.sleep(min(wait_sec, 86400))
                else:
                    _bi_log("Step 3.6: 配置时间已过", detail="立即发送")
            except Exception as e:
                _bi_log("Step 3.6 警告: 解析 scheduled_time 失败，立即发送", detail=str(e))
        else:
            _bi_log("Step 3.6: 无 scheduled_time，立即发送", detail="(可配置 distribution.email.scheduled_time 如 18:08)")
        _bi_log("Step 3.6: 发送 BI 战报邮件（战略分析 + 仪表盘分析）...", progress=True)
        try:
            smtp_config = {
                "host": (email_cfg.get("smtp_host") or email_cfg.get("host") or "smtp.qq.com"),
                "port": int(email_cfg.get("smtp_port") or email_cfg.get("port") or 587),
                "user": (email_cfg.get("smtp_user") or email_cfg.get("user") or "").strip(),
                "password": (email_cfg.get("smtp_password") or email_cfg.get("password") or "").strip(),
            }
            to_addrs = email_cfg.get("to_addrs") or []
            _bi_debug("Step 3.6", "to_addrs_raw", data={"raw_count": len(to_addrs), "has_BI_SMTP_TO": bool(os.environ.get("BI_SMTP_TO"))})
            if isinstance(to_addrs, list):
                to_addrs = [str(a).strip() for a in to_addrs if str(a).strip() and not str(a).strip().startswith("${")]
            expanded: list[str] = []
            for a in to_addrs:
                if "," in a:
                    expanded.extend(x.strip() for x in a.split(",") if x.strip())
                else:
                    expanded.append(a)
            to_addrs = expanded
            if not to_addrs:
                to_addrs = (os.environ.get("BI_SMTP_TO") or os.environ.get("BI_EMAIL_TO") or "").strip().split(",")
                to_addrs = [a.strip() for a in to_addrs if a.strip()]
            _bi_debug("Step 3.6", "to_addrs_final", data={"count": len(to_addrs)})
            if len(to_addrs) == 1:
                _bi_log("Step 3.6: 当前收件人为 1 人", detail="若需多人收件，请在 ~/.jachin/config/skills/com.jachin.bi.daily_report/bi_daily_report.yaml 的 distribution.email.to_addrs 中配置多行邮箱，或设置环境变量 BI_SMTP_TO=邮箱1,邮箱2（逗号分隔）")
            if not smtp_config.get("user") or str(smtp_config.get("user", "")).startswith("${"):
                smtp_config["user"] = (os.environ.get("BI_SMTP_USER") or os.environ.get("SMTP_USER") or "").strip()
            if not smtp_config.get("password") or str(smtp_config.get("password", "")).startswith("${"):
                smtp_config["password"] = (os.environ.get("BI_SMTP_PASSWORD") or os.environ.get("SMTP_PASSWORD") or "").strip()
            if not smtp_config.get("user") or not smtp_config.get("password") or not to_addrs:
                try:
                    from l3_node.jachin_config import load_mcp_config
                    from l3_node.paths import get_app_root
                    mcp_cfg = load_mcp_config("atom_email_sender", project_root=get_app_root())
                    mcp_smtp = mcp_cfg.get("smtp") or {}
                    if isinstance(mcp_smtp, dict) and (mcp_smtp.get("user") or "").strip() and (mcp_smtp.get("password") or "").strip():
                        smtp_config = {
                            "host": (mcp_smtp.get("host") or "smtp.qq.com"),
                            "port": int(mcp_smtp.get("port") or 587),
                            "user": str(mcp_smtp.get("user") or "").strip(),
                            "password": str(mcp_smtp.get("password") or "").strip(),
                        }
                    mcp_to = mcp_cfg.get("default_to_addrs") or []
                    if isinstance(mcp_to, list) and mcp_to:
                        to_addrs = [str(a).strip() for a in mcp_to if str(a).strip() and not str(a).strip().startswith("${")]
                except Exception:
                    pass

            if smtp_config.get("user") and smtp_config.get("password") and to_addrs:
                _bi_log("Step 3.6: 调用 mcp:atom_email_sender 一次性发送", detail=f"收件人共 {len(to_addrs)} 人")
                report_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
                subject = f"📊 BI 战略深度分析战报 ({report_date})"
                strategic_md = result.get("strategic_report") or ""
                strategic_escaped = html.escape(strategic_md or "（无战略分析内容）")
                strategic_html = strategic_escaped.replace("\n", "<br/>")

                lark_sync_ok = result.get("lark_sync_ok", 0)
                lark_errors = result.get("lark_sync_errors") or []
                output_paths = result.get("output_paths") or []
                tables_synced = [Path(p).name for p in output_paths]
                tables_list = ", ".join(tables_synced[:12]) + ("..." if len(tables_synced) > 12 else "") if tables_synced else "无"
                lark_section = f"""
<h3>二、Lark 多维表同步结果</h3>
<p>成功同步 <b>{lark_sync_ok}</b> 个表至 Lark 多维表格，输出 CSV 共 <b>{len(output_paths)}</b> 个。</p>
<p>输出文件：{html.escape(tables_list)}</p>"""
                if lark_errors:
                    err_escaped = html.escape("；".join(str(e) for e in lark_errors[:5]))
                    lark_section += f"""<p style="color:#c00;">同步异常：{err_escaped}</p>"""

                dashboards = da_cfg.get("dashboards") or []
                analyses_by_name = {n: t for n, t in dashboard_analyses}
                try:
                    from l3_node.skills.bi.bi_daily_report.dashboard_automation import _DASHBOARD_CHARTS
                except ImportError:
                    _DASHBOARD_CHARTS = {}
                dashboard_section_parts = []
                for i, dash in enumerate(dashboards):
                    if not isinstance(dash, dict):
                        continue
                    dname = (dash.get("name") or "").strip()
                    durl = (dash.get("url") or "").strip()
                    if not dname:
                        continue
                    chart_names = [c[0] for c in _DASHBOARD_CHARTS.get(dname, [])]
                    analysis_text = analyses_by_name.get(dname, "（无分析）")
                    chart_list = "、".join(chart_names[:8]) + ("…" if len(chart_names) > 8 else "") if chart_names else "—"
                    link_html = f'<a href="{html.escape(durl)}">打开仪表盘</a>' if durl and not durl.startswith("${") else ""
                    analysis_escaped = html.escape(analysis_text).replace("\n", "<br/>")
                    dashboard_section_parts.append(f"""
<div style="margin:12px 0; padding:12px; background:#f8f9fa; border-radius:8px; border-left:4px solid #1890ff;">
<h4 style="margin:0 0 8px 0;">{i + 1}. {html.escape(dname)}</h4>
<p style="margin:4px 0; color:#666; font-size:13px;">📊 统计图：{html.escape(chart_list)} {link_html}</p>
<p style="margin:8px 0 0 0; white-space: pre-wrap;">{analysis_escaped}</p>
</div>""")
                dashboard_section = ""
                if dashboard_section_parts:
                    dashboard_section = f"""
<h3>三、仪表盘统计图与分析</h3>
<p>以下为 Lark 多维表格各仪表盘及 LLM 分析结果。</p>
{"".join(dashboard_section_parts)}"""

                body = f"""<html><body style="font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; font-size:14px; line-height:1.6; color:#333;">
<h2 style="color:#1890ff;">📊 BI 每日战报 ({report_date})</h2>
<p>Jachin OS BI 每日战报已完成，含战略分析、Lark 多维表同步、仪表盘统计图分析。</p>

<h3>一、战略深度分析（闪电战报）</h3>
<div style="background:#f5f5f5; padding:16px; border-radius:8px; white-space: pre-wrap;">{strategic_html}</div>
{lark_section}
{dashboard_section}

<hr style="margin:20px 0; border:none; border-top:1px solid #eee;"/>
<p style="color:#999; font-size:12px;">— Jachin OS BI 战报系统 · 自动发送</p>
</body></html>"""
                action_input = json.dumps({
                    "smtp_config": smtp_config,
                    "to_addrs": to_addrs,
                    "subject": subject,
                    "body": body,
                    "attachment_paths": [],
                }, ensure_ascii=False)
                from l3_node.skills.mcp_registry import get_mcp_registry
                mcp_registry = get_mcp_registry()
                _bi_debug("Step 3.6", "mcp_invoke", data={"to_count": len(to_addrs)})
                r_str = await mcp_registry.invoke("mcp:atom_email_sender", action_input, timeout=60.0)
                try:
                    r = json.loads(r_str) if isinstance(r_str, str) and r_str.strip().startswith("{") else {}
                except Exception:
                    r = {"status": "error", "error": str(r_str)}
                _bi_debug("Step 3.6", "mcp_result", data={"status": r.get("status"), "error": str(r.get("error", ""))[:200]})
                if r.get("status") == "success":
                    result["email_ok"] = True
                    _bi_log("Step 3.6: 邮件已发送", detail=f"收件人共 {len(to_addrs)} 人", progress=True)
                else:
                    result["email_ok"] = False
                    result["email_error"] = r.get("error", "未知错误")
                    _bi_log("Step 3.6 警告: 邮件发送失败", detail=result["email_error"])
            else:
                _bi_debug("Step 3.6", "skip", data={"reason": "no_smtp_or_to_addrs", "has_user": bool(smtp_config.get("user")), "has_password": bool(smtp_config.get("password")), "to_count": len(to_addrs)})
                _bi_log("Step 3.6: 已跳过", detail=f"user={'有' if smtp_config.get('user') else '无'} password={'有' if smtp_config.get('password') else '无'} to_addrs={len(to_addrs) if to_addrs else 0}，请在 config 中配置 distribution.email.to_addrs 或 atom_email_sender.default_to_addrs")
        except Exception as e:
            result["email_ok"] = False
            result["email_error"] = str(e)
            _bi_debug("Step 3.6", "exception", exc=e, data={"stage": "email"})
            _bi_log("Step 3.6 警告: 邮件发送异常", detail=str(e))
    elif isinstance(email_cfg, dict) and email_cfg.get("enabled") is False:
        _bi_log("Step 3.6: 已跳过 (distribution.email.enabled=false)")

    # Step 4b: 打开每个 Lark 仪表盘，填入分析、设置定时，通过「设置自动化发送」配置定时推送（非直接发消息）
    _bi_debug("Step 4b", "branch", data={"da_enabled": da_cfg.get("enabled"), "has_analyses": bool(dashboard_analyses)})
    if da_cfg.get("enabled", False) and dashboard_analyses:
        dashboards = da_cfg.get("dashboards") or []
        scheduled_time = str(da_cfg.get("scheduled_time") or "").strip()
        _bi_debug("Step 4b", "entry", data={"scheduled_time": scheduled_time, "dashboards_count": len(dashboards)})
        if not scheduled_time or ":" not in scheduled_time or len(scheduled_time) < 4:
            _bi_log("Step 4b: 已跳过", detail="dashboard_automation.scheduled_time 未配置或格式无效，请在 config 中设置 HH:MM 如 18:05", progress=True)
        else:
            cdp_url = str(da_cfg.get("cdp_url") or "").strip()
            _bi_log("Step 4b: 开始配置 Lark 仪表盘定时发送", detail=f"定时时间（来自 config）={scheduled_time}", progress=True)
            result["dashboard_automation"] = {"done": 0, "failed": 0, "errors": []}
            analyses_by_name = {n: t for n, t in dashboard_analyses}
            for i, dash in enumerate(dashboards):
                if not isinstance(dash, dict):
                    continue
                name = (dash.get("name") or "").strip()
                url = (dash.get("url") or "").strip()
                if not url or str(url).startswith("${"):
                    url = ""
                if not name:
                    continue
                if not url:
                    _bi_log("Step 4b: 跳过仪表盘", detail=f"{name} — 未配置 URL")
                    continue
                analysis = analyses_by_name.get(name, "")
                if not analysis:
                    _bi_log("Step 4b: 跳过仪表盘", detail=f"{name} — 无对应分析")
                    continue
                _bi_debug("Step 4b", "dashboard_start", data={"index": i + 1, "name": name})
                _bi_log("Step 4b: 配置仪表盘", detail=f"[{i + 1}/{len(dashboards)}] {name}，URL={url[:50]}...", progress=True)
                try:
                    from l3_node.skills.bi.bi_daily_report.dashboard_automation import setup_lark_dashboard_automation_via_browser
                    r = await asyncio.to_thread(
                        setup_lark_dashboard_automation_via_browser,
                        dashboard_url=url,
                        analysis_text=analysis,
                        scheduled_time=scheduled_time,
                        cdp_url=cdp_url,
                    )
                    _bi_debug("Step 4b", "dashboard_result", data={"name": name, "status": r.get("status"), "error": str(r.get("error", ""))[:150]})
                    if r.get("status") == "success":
                        result["dashboard_automation"]["done"] += 1
                        _bi_log("Step 4b: 仪表盘自动化已配置", detail=f"{name}，定时={scheduled_time}")
                    else:
                        result["dashboard_automation"]["failed"] += 1
                        err_msg = r.get("error", "未知错误")
                        result["dashboard_automation"]["errors"].append(f"{name}: {err_msg}")
                        _bi_log("Step 4b 警告: 自动化配置失败", detail=f"{name}: {err_msg}", progress=True)
                except Exception as e:
                    result["dashboard_automation"]["failed"] += 1
                    result["dashboard_automation"]["errors"].append(f"{name}: {e}")
                    _bi_debug("Step 4b", "dashboard_exception", exc=e, data={"name": name})
                    _bi_log("Step 4b 警告: 仪表盘处理异常", detail=f"{name}: {e}", progress=True)
            _bi_debug("Step 4b", "exit", data={"done": result["dashboard_automation"]["done"], "failed": result["dashboard_automation"]["failed"]})
            _bi_log("Step 4b 完成", detail=f"成功={result['dashboard_automation']['done']} 失败={result['dashboard_automation']['failed']}", progress=True)
    elif da_cfg.get("enabled", False):
        _bi_log("Step 4b: 已跳过", detail="无仪表盘分析或未配置 dashboards")

    result["success"] = True
    result["stage"] = "done"
    result["report_sent"] = result["lark_sync_ok"] > 0
    result["lark_ok"] = result["lark_sync_ok"] > 0 and not result["lark_sync_errors"]

    _bi_debug("_run_bi_daily_report_async", "exit", data={"stage": result["stage"], "success": result["success"], "output_count": len(result["output_paths"])})
    _bi_log("========== BI 流程完成 ==========", progress=True)
    summary = {"success": result["success"], "stage": result["stage"], "output_count": len(result["output_paths"]), "lark_sync_ok": result["lark_sync_ok"], "lark_sync_errors": result["lark_sync_errors"], "strategic_report_sent": result.get("strategic_report_sent", False), "dashboard_analysis_sent": result.get("dashboard_analysis_sent", False), "email_ok": result.get("email_ok", False)}
    if "dashboard_automation" in result:
        summary["dashboard_automation"] = result["dashboard_automation"]
    _bi_log("最终结果", detail=json.dumps(summary, ensure_ascii=False, indent=2))
    _bi_log("日志文件", detail=str(_BI_LOG_RUN_FILE or ""))
    return result


def run_bi_daily_report(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    BI 每日战报主入口。

    流程: 1) 检查 DuckDB 今日数据 2) 无则 SPA 抓取+ingest 3) 提纯输出 CSV 4) 同步 Lark 多维表 5) 战略分析并推送 Lark 6) 邮件通知
    """
    try:
        return asyncio.run(_run_bi_daily_report_async(config))
    except Exception as e:
        logger.exception("[BI Daily Report] 异常: %s", e)
        try:
            _BI_LOG_DIR.mkdir(parents=True, exist_ok=True)
            err_file = _BI_LOG_DIR / f"bi_error_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
            import traceback
            err_file.write_text(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 顶层异常\n    {e}\n{traceback.format_exc()}",
                encoding="utf-8",
            )
            _bi_debug("run_bi_daily_report", "top_level_exception", exc=e, data={"stage": "error"})
        except Exception:
            pass
        return {"success": False, "stage": "error", "data_updated": False, "output_paths": [], "lark_sync_ok": 0, "lark_sync_errors": [], "error": str(e)}


def _get_tz_info(tz_name: str):
    """解析时区"""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(tz_name or "Asia/Shanghai")
    except Exception:
        from datetime import timezone, timedelta
        return timezone(timedelta(hours=8))


def _bi_scheduled_loop_worker() -> None:
    """后台线程：等待到 run_at 时间后，每轮结束间隔 interval_seconds 执行下一轮"""
    import time
    global _BI_LOG_DIR
    cfg = _load_config()
    _BI_LOG_DIR = _get_bi_log_dir(cfg)
    sched = cfg.get("schedule") or {}
    hour = int(sched.get("run_at_hour", 15))
    minute = int(sched.get("run_at_minute", 8))
    interval = int(sched.get("interval_seconds", 30))
    tz_name = sched.get("timezone") or "Asia/Shanghai"
    tz = _get_tz_info(tz_name)
    now = datetime.now(tz)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now >= target:
        delta = 0  # 已过今日 run_at，立即开始
    else:
        delta = (target - now).total_seconds()
    _bi_debug("scheduler_worker", "entry", data={"now": now.strftime("%H:%M:%S"), "target": target.strftime("%H:%M"), "delta_sec": delta, "hour": hour, "minute": minute})
    log_dir = _get_bi_log_dir(cfg)
    _schedule_log = log_dir / "bi_schedule.log"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        ready = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [BI 定时] 已就绪，run_at={hour:02d}:{minute:02d} 间隔={interval}s\n"
        with open(_schedule_log, "a", encoding="utf-8") as f:
            f.write(ready)
    except Exception:
        pass
    if delta > 0:
        logger.info("[BI 定时循环] 等待至 %s 首次执行，间隔 %d 秒", target.strftime("%H:%M"), interval)
        time.sleep(delta)
    round_num = 0
    while True:
        round_num += 1
        try:
            _bi_debug("scheduler_worker", "round_start", data={"round_num": round_num})
            logger.info("[BI 定时循环] 第 %d 轮开始", round_num)
            try:
                line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [BI 定时] 第 {round_num} 轮已触发，开始执行\n"
                with open(_schedule_log, "a", encoding="utf-8") as f:
                    f.write(line)
            except Exception:
                pass
            result = run_bi_daily_report(cfg)
            _bi_debug("scheduler_worker", "round_done", data={"round_num": round_num, "success": result.get("success"), "stage": result.get("stage"), "error": str(result.get("error", ""))[:100]})
            if result.get("success"):
                logger.info("[BI 定时循环] 第 %d 轮完成", round_num)
            else:
                logger.warning("[BI 定时循环] 第 %d 轮失败: %s", round_num, result.get("error", ""))
        except Exception as e:
            _bi_debug("scheduler_worker", "round_exception", exc=e, data={"round_num": round_num})
            logger.exception("[BI 定时循环] 第 %d 轮异常: %s", round_num, e)
        time.sleep(interval)


def start_bi_scheduled_loop(config: dict[str, Any] | None = None) -> bool:
    """
    启动 BI 定时循环任务（后台守护线程）。
    首次在 schedule.run_at_hour:run_at_minute 执行，此后每轮结束后间隔 interval_seconds 再执行下一轮。
    配置: config/skills/com.jachin.bi.daily_report/bi_daily_report.yaml 的 schedule 段。
    """
    import threading
    global _BI_LOG_DIR
    cfg = config or _load_config()
    _BI_LOG_DIR = _get_bi_log_dir(cfg)
    sched = cfg.get("schedule") or {}
    _bi_debug("start_bi_scheduled_loop", "entry", data={"enabled": sched.get("enabled"), "mode": sched.get("mode"), "run_at_hour": sched.get("run_at_hour"), "run_at_minute": sched.get("run_at_minute"), "interval_seconds": sched.get("interval_seconds")})
    if not sched.get("enabled", True):
        logger.info("[BI 定时循环] schedule.enabled=false，跳过")
        return False
    if (sched.get("mode") or "").lower() != "loop":
        _bi_debug("start_bi_scheduled_loop", "skip", data={"reason": "mode_not_loop", "mode": sched.get("mode")})
        return False
    t = threading.Thread(target=_bi_scheduled_loop_worker, daemon=True, name="bi_scheduled_loop")
    t.start()
    _bi_debug("start_bi_scheduled_loop", "thread_started", data={})
    logger.info("[BI 定时循环] 后台线程已启动，首次 run_at=%s:%s，间隔=%ds",
        sched.get("run_at_hour", 15), str(sched.get("run_at_minute", 8)).zfill(2), sched.get("interval_seconds", 30))
    return True
