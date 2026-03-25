"""
BI 每日战报 — 主技能逻辑（一个插件仅此一个 skill）

本 skill 完成 BI 日报全流程：数据新鲜度检查、抓取更新、提纯输出 CSV、同步飞书多维表。
所有逻辑集中于此，供 L3 agent 预检直接调用或通过 skill 描述理解执行步骤。

## Skill 描述（供 L3 Agent）

当用户发起「BI分析」「帮我开始今天的BI分析」等意图时，按以下步骤执行：

1. **数据新鲜度**：检查 bi.duckdb 是否含今日 _ingested_date；若无则执行 SPA 抓取（spa_collector）并 ingest_csv
2. **数据提纯**：从 DuckDB 按产品需求（12_PRODUCT_REQUIREMENTS.md）与 Lark 表结构（11_LARK_TABLE_SCHEMA.md）
   提炼 14 个 CSV：用户活跃(增幅/日期数量/渠道/新增设备)、留存(次留/周环比)、消耗(每日/按游戏)、充值(付费人数/付费金额按SKU/付费人数金额增幅/ARPU/ARPPU)
3. **Lark 同步**：将 output 下 CSV 同步到飞书多维表格（atom_lark_bitable_sync）
4. **战略深度分析**：基于 DuckDB 指标与 CSV，调用 LLM 生成金字塔原则战报，可选推送 Lark
5. **邮件通知**：调用 mcp:atom_email_sender（路由到 l3_node.mcp_tools.bi.tool_email_sender → channels.email.smtp）将战报发送至 distribution.email.to_addrs
6. **仪表盘分析**（Step 4a）：对每个仪表盘调用 LLM 分析统计图数据 → 保存到 output → 通过 Lark 机器人推送消息卡片（分析+仪表盘链接）。不再使用浏览器配置「设置自动化发送」。

配置项 `verbose_log`（默认 true）：控制是否在终端打印执行进度；false 时仅写日志文件。

## BI 平台 → raw/*.csv / DuckDB 数据源映射（供 L3 Agent 理解数据来源）

提纯(Refiner) 仍可从 raw/{slug}.csv 或 DuckDB 取数；战略/仪表盘「大数据分析」由 bi_daily_report.yaml 的 analysis_data_source 决定（raw 或 lark 多维表）。

| BI 菜单路径 | 页面名称 | slug | 产出表 |
|-------------|----------|------|--------|
| 用户数据统计 → 日活统计 | stats_user_dau | 01 DAU增幅、02 日期数量、03a DAU渠道来源 |
| 用户数据统计 → 日新用户统计 | stats_user_new | 01 DNU增幅、02、03b DNU渠道来源、13 新增设备 |
| 留存数据统计 → 新增用户留存统计 | stats_retention_user | 04 次留表（T+2/T+4/T+6） |
| 留存数据统计 → 新增用户留存对比 | stats_retention_user_compare | 06 周环比（T+1/T+3/T+5） |
| 留存数据统计 → 新增付费留存统计 | stats_retention_paid | 05 付费用户次留表 |
| 留存数据统计 → 新增付费留存对比 | stats_retention_paid_compare | 07 付费用户周环比 |
| 平台产销 → 平台产销情况 | prod_sales | 08 当日金币产出消耗、09 每个游戏的产出消耗 |
| 平台数据 → 平台充值情况 | recharge_status | 10 付费人数按SKU、11 付费金额按SKU |
| 充值数据统计 → 充值数据统计 | stats_recharge | 14 付费人数金额增幅、15 ARPU、16 ARPPU |

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
import socket
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
# 执行日志开关：True=打印详细进度到终端，False=不打印（默认 True）
_BI_VERBOSE: bool = True

_REQUIRED_SLUGS = [
    "daily_ops_summary",
    "stats_user_dau",         # 日活统计：03a DAU 渠道来源（点击日期展开渠道）
    "stats_user_new",         # 日新用户统计：03b DNU 渠道来源（点击渠道展开）
    "prod_sales",
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

# Step 1 判断 raw 是否「今日已抓过」：下列 slug 对应 CSV 须存在非空，且其中最新 mtime 的日历日为今天
_RAW_FRESHNESS_SLUGS = [
    "stats_user_dau",
    "stats_user_new",
    "prod_sales",
    "stats_recharge",
    "daily_ops_summary",
    "stats_retention_user",
]

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
    """写日志到文件；progress=True 且 _BI_VERBOSE=True 时同时打印到终端"""
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
        if progress and _BI_VERBOSE:
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
# LLM / L3 Agent 对齐（仅本 skill）：合并 .env + 维护 engine_ref
# =============================================================================
#
# 战略/仪表盘优先用 agent_ref.engine_ref["engine"]。CLI 下常为空，会退化为 import l3_node.__main__，
# 若此时 os.environ 未含 DASHSCOPE（例如 Key 只在 ~/.jachin/.env、或长耗时 SPA 后环境被其它库扰动），则报无 Key。
# 此处合并项目根与 ~/.jachin/.env，并把 Key 写入 engine_ref 或回填已有 engine 的 SecurityContext。


def _bi_merge_dotenv_for_skill() -> None:
    """项目根 .env 后再合并 ~/.jachin/.env（不覆盖 os.environ 已有非空同名变量）。"""
    try:
        from dotenv import load_dotenv
        from l3_node.paths import get_app_root

        pr = get_app_root() / ".env"
        if pr.exists():
            load_dotenv(pr, encoding="utf-8")
        jh = Path.home() / ".jachin" / ".env"
        if jh.exists():
            load_dotenv(jh, encoding="utf-8")
    except ImportError:
        pass
    except Exception as e:
        logger.debug("[BI] dotenv 合并跳过: %s", e)


def _bi_reconcile_llm_engine_ref_with_agent() -> None:
    """
    合并 .env 后：若环境中有 Key，则
    - engine_ref 已有引擎：将 Key 写回 engine.ctx 与 os.environ（与 L3 Agent 使用同一套上下文）；
    - 否则：按 L3 _create_engine_standalone 等价逻辑新建引擎并 register_host_services。
    在 Step 1.1 长时间抓取之后、Step 3.5/4a 之前应再调用一次，避免仅依赖流程开头的环境状态。
    """
    _bi_merge_dotenv_for_skill()
    dash = (
        os.environ.get("DASHSCOPE_API_KEY")
        or os.environ.get("QWEN_API_KEY")
        or os.environ.get("QWEN_AI_API_KEY")
        or ""
    ).strip()
    oa = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not dash and not oa:
        return
    try:
        from l3_node.agent_ref import engine_ref
    except Exception:
        return
    eng = engine_ref.get("engine")
    if eng is not None:
        try:
            if dash:
                eng.ctx.set_key("dashscope", dash)
                os.environ["DASHSCOPE_API_KEY"] = dash
            if oa:
                eng.ctx.set_key("openai", oa)
                os.environ["OPENAI_API_KEY"] = oa
        except Exception as e:
            logger.debug("[BI] 回填 engine.ctx / environ 失败: %s", e)
        return
    try:
        from l3_node.llm_client import LiteLLMEngine, SecurityContext
        from core.wasm_runner import register_host_services

        ctx = SecurityContext()
        if oa:
            ctx.set_key("openai", oa)
        if dash:
            ctx.set_key("dashscope", dash)
        fallback = None
        default_model = "gpt-4o-mini"
        if ctx.get_key("dashscope"):
            fallback = ["dashscope/qwen3.5-flash-2026-02-23"]
            default_model = os.environ.get("LLM_MODEL", "qwen3.5-flash-2026-02-23")
        _timeout = float(os.environ.get("LLM_TIMEOUT", "180"))
        new_eng = LiteLLMEngine(
            security_context=ctx,
            model_name=os.environ.get("L3_MODEL", default_model),
            fallback_models=fallback,
            timeout=_timeout,
            max_attempts=2,
        )
        register_host_services(llm_engine=new_eng, l2_base_url=os.environ.get("L2_BASE_URL", "http://localhost:18888"))
        engine_ref["engine"] = new_eng
        logger.info("[BI] 已注入 engine_ref，战略/仪表盘将走与 L3 Agent 一致的 LLM 引擎")
    except Exception as e:
        logger.debug("[BI] 注入 engine_ref 失败: %s", e)


# =============================================================================
# 数据提纯（原 report_refiner 逻辑，内置于 skill）
# =============================================================================

def _find_col(columns: list[str], *candidates: str) -> str | None:
    for cand in candidates:
        for c in columns:
            if cand and (cand.lower() in (c or "").lower() or (c or "") == cand):
                return c
    return None


def _parse_date_to_iso(v: Any) -> str:
    """
    将 BI 导出 CSV 常见日期格式统一为 YYYY-MM-DD。
    解决 2026/3/23、2026/03/23 等与 t1(2026-03-23) 字符串比较不一致导致误选 3/22 或混入多日期子行的问题。
    """
    s = (str(v) if v is not None else "").strip()
    if not s:
        return ""
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        cand = s[:10]
        if len(cand) == 10 and cand.replace("-", "").isdigit():
            return cand
    if "/" in s:
        try:
            part = s.split()[0] if s else s
            parts = part.replace("\\", "/").split("/")
            if len(parts) >= 3:
                y, mo, d = int(parts[0]), int(parts[1]), int(parts[2])
                return f"{y:04d}-{mo:02d}-{d:02d}"
        except (ValueError, IndexError):
            pass
    return ""


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


def _load_raw_csv(raw_dir: Path | None, slug: str) -> list[dict] | None:
    """从 raw 目录加载 {slug}.csv，优先于 DuckDB。返回 None 表示文件不存在。"""
    if not raw_dir or not raw_dir.exists():
        return None
    path = raw_dir / f"{slug}.csv"
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        if rows:
            logger.debug("[Refiner] 从 raw 加载 %s: %d 行", slug, len(rows))
            return rows
    except Exception as e:
        logger.warning("[Refiner] 读取 raw/%s.csv 失败: %s", slug, e)
    return None


def _pick_row_for_target_date(rows: list[dict], date_col: str | None, target_date: str) -> dict | None:
    """从 rows 中优先取 target_date（昨日）的行；若无则取第一行（已按日期倒序时的最新）。
    用于按今日实际日期判定：如今天 24 号则取 23 号数据，有则取 23 号。"""
    if not rows:
        return None
    target = str(target_date)[:10] if target_date else ""
    if date_col and target:
        for r in rows:
            d = _parse_date_to_iso(r.get(date_col, ""))
            if d and d == target:
                return r
    return rows[0]


def _filter_rows_by_date(rows: list[dict], date_from: str | None, date_to: str | None, date_col: str | None = None) -> list[dict]:
    """按日期范围过滤行。date_col 未指定时自动查找。"""
    if not rows or (not date_from and not date_to):
        return rows
    cols = list(rows[0].keys())
    dc = date_col or _find_col(cols, "日期", "date", "统计日期", "业务日期", "_ingested_date")
    if not dc:
        return rows

    out = []
    for r in rows:
        v = _parse_date_to_iso(r.get(dc, ""))
        if not v:
            out.append(r)
            continue
        if date_from and v < date_from[:10]:
            continue
        if date_to and v > date_to[:10]:
            continue
        out.append(r)
    return out


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


def _query_table_or_raw(
    conn: Any,
    raw_dir: Path | None,
    slug: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """优先从 raw/{slug}.csv 加载，无则查 DuckDB。"""
    raw = _load_raw_csv(raw_dir, slug)
    if raw is not None:
        filtered = _filter_rows_by_date(raw, date_from, date_to)
        if filtered:
            cols = list(filtered[0].keys())
            date_col = _find_col(cols, "日期", "date", "统计日期", "业务日期")
            if date_col:
                filtered.sort(key=lambda r: _parse_date_to_iso(r.get(date_col, "")), reverse=True)
            return filtered
        return raw
    return _query_table(conn, slug, date_from, date_to)


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


def _refine_user_activity(conn: Any, output_dir: Path, t1: str, t0: str, t7: str, raw_dir: Path | None = None) -> list[Path]:
    """用户活跃：01 DAU/DNU增幅、02 日期数量、03a DAU渠道、03b DNU渠道、13 新增设备。
    数据来源：stats_user_dau（日活统计）、stats_user_new（日新用户统计），优先 raw/*.csv。"""
    written: list[Path] = []
    q = lambda slug, df=None, dt=None: _query_table_or_raw(conn, raw_dir, slug, df, dt)

    # 01 DAU/DNU 增幅：来自 stats_user_dau 和 stats_user_new（日日活/日日新用户统计）
    dau_stat = q("stats_user_dau")
    dnu_stat = q("stats_user_new")
    dau_col = _find_col(list(dau_stat[0].keys()) if dau_stat else [], "日活（DAU）", "日活(DAU)", "日活", "DAU", "dau")
    dnu_col = _find_col(list(dnu_stat[0].keys()) if dnu_stat else [], "当日新增注册（DNU）", "当日新增注册", "当日新增用户（DNU）", "新增用户(DNU)", "DNU", "dnu")
    date_col = _find_col(list(dau_stat[0].keys()) if dau_stat else [], "日期", "date", "统计日期") or "日期"

    def _agg_by_date(rows: list[dict], date_c: str, val_c: str | None) -> dict[str, float]:
        by_d: dict[str, float] = {}
        for r in rows:
            d = _parse_date_to_iso(r.get(date_c))
            if not d:
                continue
            v = _safe_float(r.get(val_c)) if val_c else 0
            by_d[d] = by_d.get(d, 0) + v
        return by_d

    def _agg_by_date_total_row(
        rows: list[dict],
        date_c: str,
        val_c: str | None,
        ch_c: str,
        total_labels: tuple[str, ...],
    ) -> dict[str, float]:
        """按日期取「汇总行」的数值。stats_user_dau 用 全部汇总，stats_user_new 用 ALL。"""
        by_d: dict[str, float] = {}
        for r in rows:
            ch = str(r.get(ch_c, "") or "").strip()
            if ch not in total_labels:
                continue
            d = _parse_date_to_iso(r.get(date_c))
            if not d:
                continue
            v = _safe_float(r.get(val_c)) if val_c else 0
            by_d[d] = v  # 每日期仅一条汇总行，直接覆盖
        return by_d

    ch_col = _find_col(list(dau_stat[0].keys()) if dau_stat else [], "渠道", "channel") or _find_col(
        list(dnu_stat[0].keys()) if dnu_stat else [], "渠道", "channel"
    )
    # 02 周统计表：取「日日活统计」全部汇总、「日日新用户统计」ALL 行，避免渠道明细重复累加
    dau_total_labels = ("全部汇总", "全平台", "ALL", "> ALL")
    dnu_total_labels = ("ALL", "全部汇总", "全平台", "> ALL")
    if dau_stat and dau_col:
        dau_by_date = (
            _agg_by_date_total_row(dau_stat, date_col, dau_col, ch_col, dau_total_labels)
            if ch_col
            else _agg_by_date(dau_stat, date_col, dau_col)
        )
    else:
        dau_by_date = {}
    if dnu_stat and dnu_col:
        dnu_by_date = (
            _agg_by_date_total_row(dnu_stat, date_col, dnu_col, ch_col, dnu_total_labels)
            if ch_col
            else _agg_by_date(dnu_stat, date_col, dnu_col)
        )
    else:
        dnu_by_date = {}
    dates_dau = sorted([d for d in dau_by_date if d], reverse=True)[:8]
    dates_dnu = sorted([d for d in dnu_by_date if d], reverse=True)[:8]
    all_dates = sorted(set(dates_dau + dates_dnu), reverse=True)[:8]
    # 优先取 t1（昨日）：今天 24 号则取 23 号，表中有则用 23 号
    d1 = t1 if all_dates and t1 in all_dates else (all_dates[0] if all_dates else t1)
    d2 = all_dates[all_dates.index(d1) + 1] if d1 in all_dates and all_dates.index(d1) + 1 < len(all_dates) else (all_dates[1] if len(all_dates) >= 2 else None)
    dau1, dau0 = dau_by_date.get(d1, 0), dau_by_date.get(d2, 0) if d2 else 0
    dnu1, dnu0 = dnu_by_date.get(d1, 0), dnu_by_date.get(d2, 0) if d2 else 0
    dau_pct = round((dau1 - dau0) / dau0 * 100, 2) if dau0 else 0.0
    dnu_pct = round((dnu1 - dnu0) / dnu0 * 100, 2) if dnu0 else 0.0
    increase_rows = [{"类型": "DAU", "增幅（%）": dau_pct}, {"类型": "DNU", "增幅（%）": dnu_pct}]
    _write_csv(output_dir / "01_用户活跃_增幅表.csv", increase_rows, ["类型", "增幅（%）"])
    written.append(output_dir / "01_用户活跃_增幅表.csv")

    # 02 周统计 DAU/DNU 数量：合并两表按日期
    by_date_02: dict[str, dict] = {}
    for d in all_dates[:7]:
        by_date_02[d] = {"日期": _date_to_lark_ts(d), "DAU数量": int(dau_by_date.get(d, 0)), "DNU数量": int(dnu_by_date.get(d, 0))}
    daily_rows = [by_date_02[d] for d in sorted(by_date_02.keys())]
    if not daily_rows:
        daily_rows = [{"日期": _date_to_lark_ts(t1), "DAU数量": 0, "DNU数量": 0}]
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
        """只保留目标日期的数据。优先 t1（昨日）；展开子行继承上一行日期（与 BI 导出一致）。"""
        if not rows:
            return []
        cols = list(rows[0].keys())
        date_col = _find_col(cols, *date_cands)
        if not date_col:
            return rows
        last_iso = ""
        stamped: list[tuple[dict, str]] = []
        for r in rows:
            di = _parse_date_to_iso(r.get(date_col))
            if di:
                last_iso = di
            eff = di or last_iso
            stamped.append((r, eff))
        dates_in = sorted({eff for _, eff in stamped if eff}, reverse=True)
        target = (t1[:10] if t1 else "") if dates_in and t1[:10] in dates_in else (dates_in[0] if dates_in else (t1[:10] if t1 else ""))
        if not target:
            return rows
        return [r for r, eff in stamped if eff == target]

    dau_rows, dnu_rows = [], []
    # 03a DAU 渠道来源：日活统计，最新日期各渠道 DAU 数量
    dau_stat_ch = q("stats_user_dau")
    dau_stat_ch = _filter_rows_to_single_date(dau_stat_ch, ["日期", "date", "统计日期"], d1 if all_dates else t1)
    if dau_stat_ch:
        dau_extracted = _extract_channels_from_stat_table(
            dau_stat_ch,
            ch_cands=["渠道", "channel", "渠道来源", "统计范围"],
            count_cands=["日活（DAU）", "日活(DAU)", "日活", "DAU"],
        )
        dau_rows = [{"DAU渠道来源": x["渠道"], "数量": x["数量"]} for x in dau_extracted]
    # 03b DNU 渠道来源：日新用户统计，最新日期各渠道 DNU 数量
    dnu_stat_ch = q("stats_user_new")
    dnu_stat_ch = _filter_rows_to_single_date(dnu_stat_ch, ["日期", "date", "统计日期"], d1 if all_dates else t1)
    if dnu_stat_ch:
        extracted = _extract_channels_from_stat_table(
            dnu_stat_ch,
            ch_cands=["渠道", "channel", "渠道来源", "统计范围"],
            count_cands=["当日新增注册（DNU）", "当日新增注册", "当日新增", "DNU"],
        )
        dnu_rows = [{"DNU渠道来源": x["渠道"], "数量": x["数量"]} for x in extracted]

    # Fallback：stats_user_dau/stats_user_new 无数据时，用明细表或买量统计
    if not dau_rows:
        dau_active = q("detail_user_active", t0, t1)
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

    # 13 新增设备数、增幅、占比：来自 stats_user_new（日日新用户统计），取 ALL 汇总行的日新设备数、DNU
    dev_rows = q("stats_user_new", t0, t1)
    dev_val, dev_pct, dnu_dev_ratio = 0, 0.0, "-"
    if dev_rows:
        cols = list(dev_rows[0].keys())
        dev_col = _find_col(cols, "日新设备数", "新增设备数", "新增设备", "设备数")
        dnu_col_dev = _find_col(cols, "当日新增注册（DNU）", "当日新增注册", "当日新增用户（DNU）", "新增用户(DNU)", "DNU", "dnu")
        date_col_dev = _find_col(cols, "日期", "date", "统计日期") or ""
        ch_col_dev = _find_col(cols, "渠道", "channel")
        dev_total_labels = ("ALL", "全部汇总", "全平台", "> ALL")
        by_date_dev: dict[str, dict] = {}
        for r in dev_rows:
            if ch_col_dev and str(r.get(ch_col_dev, "")).strip() not in dev_total_labels:
                continue
            d = _parse_date_to_iso(r.get(date_col_dev, ""))
            if d:
                # 每日期仅保留一条汇总行（ALL/全部汇总）
                by_date_dev[d] = {
                    "dev": _safe_float(r.get(dev_col)) if dev_col else 0,
                    "dnu": _safe_float(r.get(dnu_col_dev)) if dnu_col_dev else 0,
                }
        dates_dev = sorted([d for d in by_date_dev if d], reverse=True)[:2]
        if len(dates_dev) >= 1:
            r1 = by_date_dev.get(dates_dev[0], {})
            dev_val = int(r1.get("dev", 0))
            dnu1 = r1.get("dnu", 0)
            dnu_dev_ratio = round(dnu1 / dev_val, 2) if dev_val else "-"
            if len(dates_dev) >= 2:
                r0 = by_date_dev.get(dates_dev[1], {})
                dev0 = r0.get("dev", 0)
                dev_pct = round((dev_val - dev0) / dev0 * 100, 2) if dev0 else 0.0
    dev_table = [{"新增设备数值": dev_val, "新增设备增幅": dev_pct, "新增用户/新增设备": dnu_dev_ratio}]
    _write_csv(output_dir / "13_用户活跃_新增设备表.csv", dev_table, ["新增设备数值", "新增设备增幅", "新增用户/新增设备"])
    written.append(output_dir / "13_用户活跃_新增设备表.csv")

    return written


# 次留表：类型 T-2、T-4、T-6（对应 stats_retention_user 第三日 T+2/第五日 T+4/第七日 T+6 留存）
# 付费用户次留表：文本列 T+2、T+4、T+6（对应 stats_retention_paid 的 T+2/T+4/T+6 留存）
# 周环比：类型 T+1、T+3、T+5（对应 stats_retention_user_compare 第一行 ALL 的 T+1/T+3/T+5 留存率）
# 付费用户周环比：类型 T+1、T+2、T+3（对应 stats_retention_paid_compare 的 T+1/T+2/T+3 留存率）
_RETENTION_TYPE_NEXT = {"t2": "T-2", "t4": "T-4", "t6": "T-6"}  # 次留表：第三日/第五日/第七日 → T-2/T-4/T-6
_RETENTION_TYPE_PAID_NEXT = {"t2": "T+2", "t4": "T+4", "t6": "T+6"}  # 付费用户次留表：文本列
_RETENTION_TYPE_WOW = {"t2": "T+1", "t4": "T+3", "t6": "T+5"}       # 周环比
_RETENTION_TYPE_PAID_WOW = {"t2": "T+1", "t4": "T+2", "t6": "T+3"}  # 付费用户周环比


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


def _refine_retention(conn: Any, output_dir: Path, t1: str, raw_dir: Path | None = None) -> list[Path]:
    """留存：04 次留表、05 付费用户次留、06 周环比、07 付费用户周环比。数据来源：新增用户留存统计、新增付费留存统计、留存对比。"""
    written: list[Path] = []
    q = lambda slug: _query_table_or_raw(conn, raw_dir, slug, None, None)
    user_rows = q("stats_retention_user")
    paid_rows = q("stats_retention_paid")
    if not paid_rows and raw_dir and raw_dir.exists():
        paid_rows = _load_raw_csv(raw_dir, "stats_retention_paid")
    if paid_rows and isinstance(paid_rows, list) and paid_rows:
        cols = list(paid_rows[0].keys())
        date_col = _find_col(cols, "日期", "date", "业务日期")
        if date_col:
            paid_rows = sorted(paid_rows, key=lambda r: _parse_date_to_iso(r.get(date_col, "")), reverse=True)
    # 图1 新增用户留存统计：取第一行（最新日期），渠道=ALL 或 > ALL
    def _filter_platform_rows(rows: list[dict], ch_col: str) -> list[dict]:
        if not rows or not ch_col:
            return rows
        all_rows = [r for r in rows if str(r.get(ch_col, "")).strip().upper() in ("ALL", "> ALL", "全平台")]
        return all_rows if all_rows else rows

    ch_col = _find_col(list(user_rows[0].keys()), "渠道", "channel") if user_rows else None
    if user_rows and ch_col:
        user_rows = _filter_platform_rows(user_rows, ch_col)
    # 次留表：优先取 t1（昨日）数据，如表中有 23 号则取 23 号，否则取最新
    udc = _find_col(list(user_rows[0].keys()), "日期", "date", "统计日期") if user_rows else None
    if user_rows and udc:
        user_rows = sorted(user_rows, key=lambda r: _parse_date_to_iso(r.get(udc, "")), reverse=True)
    r0_user = _pick_row_for_target_date(user_rows or [], udc, t1)

    # stats_retention_user（新增用户留存统计）：第三日(T+2)/第五日(T+4)/第七日(T+6) → Lark 次留表 T-2/T-4/T-6
    user_col_map = [
        (["第三日（T+2）留存", "第三日(T+2)留存", "T+2 留存", "T+2留存"], "t2"),
        (["第五日（T+4）留存", "第五日(T+4)留存", "T+4 留存", "T+4留存"], "t4"),
        (["第七日（T+6）留存", "第七日(T+6)留存", "T+6 留存", "T+6留存", "七留"], "t6"),
    ]
    by_type: dict[str, tuple[int, float]] = {}
    if r0_user:
        cols = list(r0_user.keys())
        for cands, key in user_col_map:
            col = _find_col(cols, *cands)
            if col:
                val = r0_user.get(col, "")
                n, p = _parse_retention_val(str(val))
                by_type[key] = (n, p)

    # 04 次留表：类型 T-2/T-4/T-6，留存率。数据来源：stats_retention_user 第一行（最新日期）ALL
    next_ret_rows = []
    for k in ("t2", "t4", "t6"):
        n, p = by_type.get(k, (0, 0.0))
        next_ret_rows.append({"类型": _RETENTION_TYPE_NEXT[k], "留存率": round(p * 100, 1)})
    _write_csv(output_dir / "04_留存_次留表.csv", next_ret_rows, ["类型", "留存率"])

    # 05 付费用户次留表：优先取 t1（昨日）数据，否则取最新
    pch_col = _find_col(list((paid_rows or [{}])[0].keys()), "渠道", "channel") if paid_rows else None
    if paid_rows and pch_col:
        paid_rows = _filter_platform_rows(paid_rows, pch_col)
    pdc = _find_col(list((paid_rows or [{}])[0].keys()), "日期", "date", "业务日期") if paid_rows else None
    if paid_rows and pdc:
        paid_rows = sorted(paid_rows, key=lambda r: _parse_date_to_iso(r.get(pdc, "")), reverse=True)
    pr0 = _pick_row_for_target_date(paid_rows or [], pdc, t1)
    paid_col_map = [
        (["T+2 留存", "T+2留存", "次日(T+1)留存"], "t2"),
        (["T+4 留存", "T+4留存", "第四日(T+3)留存", "第五日(T+4)留存"], "t4"),
        (["T+6 留存", "T+6留存", "第七日(T+6)留存"], "t6"),
    ]
    paid_by_type: dict[str, tuple[int, float]] = {}
    if pr0:
        pcols = list(pr0.keys())
        for cands, key in paid_col_map:
            col = _find_col(pcols, *cands)
            if col:
                val = pr0.get(col, "")
                n, p = _parse_retention_val(str(val))
                paid_by_type[key] = (n, p)
    paid_ret_rows = []
    for k in ("t2", "t4", "t6"):
        n, p = paid_by_type.get(k, (0, 0.0))
        paid_ret_rows.append({"文本": _RETENTION_TYPE_PAID_NEXT[k], "留存率": round(p * 100, 1)})
    _write_csv(output_dir / "05_留存_付费用户次留表.csv", paid_ret_rows, ["文本", "留存率"])

    def _parse_pct_to_num(s: str) -> float:
        """解析 '9.43%' 或 '9.43' 为数值"""
        s = (str(s or "").strip()).replace("%", "")
        try:
            return round(float(s), 2)
        except ValueError:
            return 0.0

    def _parse_compare_pct(s: str) -> float:
        """解析 stats_retention_user_compare 格式。
        正常格式 '12.91%+39.7537%9.23%'：本周/增幅/上周，取第一个（本周留存率）。
        异常格式 '--100%60.00%'：取最后一个（60.00 为实际值，100 为误抓）。"""
        s = str(s or "").strip()
        if not s or s in ("---", "--", "N/A", "--0%"):
            return 0.0
        matches = re.findall(r"([\d.]+)\s*%", s)
        if matches:
            try:
                vals = [float(x) for x in matches]
                # 异常：首值为 100 且存在多个 → 取最后（如 --100%60.00%）
                if len(vals) > 1 and vals[0] >= 99.99:
                    return round(vals[-1], 2)
                return round(vals[0], 2)
            except ValueError:
                pass
        return _parse_pct_to_num(s)

    def _parse_paid_compare_pct(s: str) -> float:
        """解析 stats_retention_paid_compare（新增付费留存对比）的周环比变化。
        BI 展示：上空/中变化率/下上周基线。需取中间的变化率（如 -100%），非上周基线（如 50%、16.67%）。
        格式：'--100%60.00%' → 变化 -100%， baseline 60% 忽略；'---' 空数据 → -100。"""
        s = str(s or "").strip()
        if not s or s in ("---", "--", "N/A"):
            return -100.0  # 空数据表示本周无数据，环比视为 -100%
        # 匹配带符号的百分数：-100%、+50%、0% 等
        matches = re.findall(r"([+-]?\d+\.?\d*)\s*%", s)
        if not matches:
            return -100.0
        try:
            vals = [float(x) for x in matches]
            # 三段式 [本周][变化][上周]：取中间
            if len(vals) >= 3:
                return round(vals[1], 2)
            # 两段式 [变化][上周]：如 --100%60.00%
            if len(vals) == 2:
                # 变化通常带负号或为 0
                return round(vals[0], 2)
            return round(vals[0], 2)
        except ValueError:
            return -100.0

    wow_t2, wow_t4, wow_t6 = 0.0, 0.0, 0.0
    # 06 周环比：来源 stats_retention_user_compare（新增用户留存对比）第一行 ALL，T+1/T+3/T+5
    user_compare = q("stats_retention_user_compare")
    if not user_compare:
        user_compare = _query_table_or_raw(conn, raw_dir, "stats_retention_user_compare", t1, t1)
    if user_compare:
        cols = list(user_compare[0].keys())
        scope_col = _find_col(cols, "统计范围", "渠道", "channel")
        date_compare_col = _find_col(cols, "日期对比", "日期", "date")
        # 取周汇总行：日期对比含 "~" 表示周范围，且 统计范围=ALL
        r = None
        for row in user_compare:
            scope_val = str(row.get(scope_col, "")).strip()
            date_val = str(row.get(date_compare_col, "") or "")
            if scope_val in ("ALL", "全部汇总", "全平台", "> ALL") and "~" in date_val:
                r = row
                break
        if r is None:
            for row in user_compare:
                if not scope_col or str(row.get(scope_col, "")).strip() in ("ALL", "全部汇总", "全平台", "> ALL"):
                    r = row
                    break
        if r is None:
            r = user_compare[0]
        wow_col_map = [
            (["T+1 留存率", "这周T+1留存率", "这周留存率", "本周留存率", "T+1留存率"], "t2"),
            (["T+3 留存率", "这周T+3留存率", "T+3留存率", "T+4 留存率"], "t4"),
            (["T+5 留存率", "这周T+5留存率", "T+5留存率", "T+6 留存率", "这周T+6留存率"], "t6"),
        ]
        for cands, key in wow_col_map:
            col = _find_col(cols, *cands)
            if col:
                raw_val = str(r.get(col, "") or "")
                num = _parse_compare_pct(raw_val)
                if key == "t2":
                    wow_t2 = num
                elif key == "t4":
                    wow_t4 = num
                else:
                    wow_t6 = num
    # 06 周环比：类型 T+1/T+3/T+5，留存率（%）。Lark 表「周环比」固定三行
    wow_rows = [{"类型": _RETENTION_TYPE_WOW["t2"], "留存率（%）": wow_t2}, {"类型": _RETENTION_TYPE_WOW["t4"], "留存率（%）": wow_t4}, {"类型": _RETENTION_TYPE_WOW["t6"], "留存率（%）": wow_t6}]
    _write_csv(output_dir / "06_留存_周环比表.csv", wow_rows, ["类型", "留存率（%）"])
    # 07 付费用户周环比：来源 stats_retention_paid_compare（新增付费留存对比）第一行 统计范围=ALL 且 日期对比含~，T+1/T+2/T+3 周环比变化（取变化率如-100%，非上周基线）
    paid_wow_t1, paid_wow_t2, paid_wow_t3 = 0.0, 0.0, 0.0
    paid_compare = q("stats_retention_paid_compare")
    if not paid_compare and raw_dir and raw_dir.exists():
        paid_compare = _load_raw_csv(raw_dir, "stats_retention_paid_compare")
    if paid_compare:
        pcols = list(paid_compare[0].keys())
        scope_col = _find_col(pcols, "统计范围", "渠道", "channel")
        date_compare_col = _find_col(pcols, "日期对比", "日期", "date")
        pr = None
        for row in paid_compare:
            scope_val = str(row.get(scope_col, "")).strip()
            date_val = str(row.get(date_compare_col, "") or "")
            if scope_val.upper() in ("ALL", "全部汇总", "全平台", "> ALL") and "~" in date_val:
                pr = row
                break
        if pr is None:
            pr = paid_compare[0]
        paid_wow_col_map = [
            (["T+1 留存率", "这周T+1留存率", "T+1留存率"], "t1"),
            (["T+2 留存率", "这周T+2留存率", "T+2留存率"], "t2"),
            (["T+3 留存率", "这周T+3留存率", "T+3留存率"], "t3"),
        ]
        for cands, key in paid_wow_col_map:
            col = _find_col(pcols, *cands)
            if col:
                raw_val = str(pr.get(col, "") or "")
                num = _parse_paid_compare_pct(raw_val)  # 取周环比变化（-100%），非上周基线（50%等）
                if key == "t1":
                    paid_wow_t1 = num
                elif key == "t2":
                    paid_wow_t2 = num
                else:
                    paid_wow_t3 = num
    # 07 付费用户周环比：类型 T+1/T+2/T+3，Lark 表「付费用户周环比」固定三行
    paid_wow_rows = [{"类型": "T+1", "留存率": paid_wow_t1}, {"类型": "T+2", "留存率": paid_wow_t2}, {"类型": "T+3", "留存率": paid_wow_t3}]
    _write_csv(output_dir / "07_留存_付费用户周环比表.csv", paid_wow_rows, ["类型", "留存率"])
    written.extend([output_dir / "04_留存_次留表.csv", output_dir / "05_留存_付费用户次留表.csv", output_dir / "06_留存_周环比表.csv", output_dir / "07_留存_付费用户周环比表.csv"])
    return written


def _safe_prod_cons(v: Any) -> float:
    """解析产出/消耗值，支持纯数字或对比格式如 '226,728,964.00 (+85.54%)122,198,191.00'"""
    if v is None:
        return 0.0
    s = str(v).strip()
    if "(" in s or "+%" in s:
        return _parse_compare_number(s)
    return _safe_float(v)


def _load_prod_sales_game_latest(raw_dir: Path | None, date_from: str | None, target_date: str | None = None) -> list[dict]:
    """从 raw/prod_sales.csv 加载目标日期的各游戏产出、消耗。优先 target_date（昨日），若无则取最新日期。"""
    raw = _load_raw_csv(raw_dir, "prod_sales")
    if not raw:
        return []
    cols = list(raw[0].keys())
    date_col = _find_col(cols, "日期", "date", "业务日期") or (cols[0] if cols else None)
    game_col = _find_col(cols, "汇总项目", "统计范围", "游戏名称", "游戏", "游戏名")
    prod_col = _find_col(cols, "用户金币产出总数", "用户金币产出", "产出", "金币产出")
    cons_col = _find_col(cols, "用户金币消耗总数", "用户金币消耗", "消耗", "金币消耗")
    if not (date_col and game_col and (prod_col or cons_col)):
        return []

    skip_items = ("全部汇总", "全量合计", "全平台汇总", "ALL", "奖励", "赠送", "游戏输赢", "兑换")
    valid_rows = []
    for r in raw:
        d = _parse_date_to_iso(r.get(date_col, ""))
        if not d:
            continue
        if date_from and d < date_from[:10]:
            continue
        g = str(r.get(game_col, "") or "").strip()
        if not g or g in skip_items:
            continue
        valid_rows.append({"date": d, "game": g, "prod": _safe_prod_cons(r.get(prod_col)), "cons": _safe_prod_cons(r.get(cons_col))})

    if not valid_rows:
        return []
    dates_avail = {r["date"] for r in valid_rows}
    pick_date = (target_date[:10] if target_date and target_date[:10] in dates_avail else None) or max(dates_avail)
    return [{"游戏名称": r["game"], "产出": round(r["prod"], 2), "消耗": round(r["cons"], 2)} for r in valid_rows if r["date"] == pick_date]


def _refine_consumption(conn: Any, output_dir: Path, t1: str, t7: str, raw_dir: Path | None = None) -> list[Path]:
    """消耗：08 每日金币产出消耗、09 每个游戏的产出消耗。数据来源：平台产销情况 prod_sales。"""
    written: list[Path] = []
    q = lambda slug, df=None, dt=None: _query_table_or_raw(conn, raw_dir, slug, df, dt)
    date_col_cands = ["日期", "date", "业务日期", "日期对比"]
    prod_col_cands = ["用户金币产出总数", "用户金币产出", "产出", "金币产出"]
    cons_col_cands = ["用户金币消耗总数", "用户金币消耗", "消耗", "金币消耗"]
    game_col_cands = ["汇总项目", "统计范围", "游戏名称", "游戏", "游戏名"]
    agg_labels = ("全平台汇总", "全平台", "总计", "合计", "ALL")
    agg_to_total = {"当日总计": "全部汇总", "汇总(分游戏)": "全部汇总", "总计(分游戏)": "全部汇总", "全量合计": "全部汇总"}

    # 08 表：优先 prod_sales（平台产销情况），取最新日期的「全部汇总」行；不限制 date_to 以包含最新数据
    rows_daily = q("prod_sales", t7, None)
    if not rows_daily:
        rows_daily = q("prod_sales", None, None)
    if not rows_daily:
        rows_daily = q("stats_game_daily", t7, t1)
    # 09「每个游戏的产出、消耗」仅来自平台产销 prod_sales，避免误用 stats_game_daily 其它口径
    rows_game = q("prod_sales", t7, None) or q("prod_sales", None, None) or rows_daily
    if not rows_game:
        rows_game = q("stats_game_compare")
    if not rows_daily and not rows_game:
        return written

    # 08 表必须用 prod_sales 的列结构（汇总项目、用户金币产出总数、用户金币消耗总数）
    cols_daily = list(rows_daily[0].keys()) if rows_daily else []
    cons_col_daily = _find_col(cols_daily, *cons_col_cands)
    prod_col_daily = _find_col(cols_daily, *prod_col_cands)
    game_col_daily = _find_col(cols_daily, *game_col_cands)
    date_col = _find_col(cols_daily, *date_col_cands)

    # 09 表用 stats_game_daily 的列结构（统计范围、用户金币产出、用户金币消耗）
    cols_game = list((rows_game or [{}])[0].keys()) if rows_game else []
    cons_col = _find_col(cols_game, *cons_col_cands)
    prod_col = _find_col(cols_game, *prod_col_cands)
    game_col = _find_col(cols_game, *game_col_cands)

    # 08 当日金币产出、消耗：取 prod_sales 每日期「全部汇总」行，否则按日期汇总
    by_date_daily: dict[str, dict] = {}
    for r in (rows_daily or []):
        d = _parse_date_to_iso(r.get(date_col, "")) if date_col else ""
        if not d:
            continue
        g = str(r.get(game_col_daily, "") or "").strip()
        is_total = g in ("全部汇总", "全量合计", "全平台汇总", "ALL", "> ALL")
        if d not in by_date_daily:
            by_date_daily[d] = {"日期": d, "产出": 0.0, "消耗": 0.0, "_has_total_row": False}
        if is_total:
            by_date_daily[d]["产出"] = _safe_prod_cons(r.get(prod_col_daily))
            by_date_daily[d]["消耗"] = _safe_prod_cons(r.get(cons_col_daily))
            by_date_daily[d]["_has_total_row"] = True
        elif not by_date_daily[d].get("_has_total_row"):
            by_date_daily[d]["产出"] += _safe_prod_cons(r.get(prod_col_daily))
            by_date_daily[d]["消耗"] += _safe_prod_cons(r.get(cons_col_daily))
    for v in list(by_date_daily.values()):
        v.pop("_has_total_row", None)
    dates_sorted = sorted([d for d in by_date_daily if d])[-7:]
    daily_rows = [{"日期": _date_to_lark_ts(d), "产出": round(by_date_daily[d]["产出"], 2), "消耗": round(by_date_daily[d]["消耗"], 2)} for d in dates_sorted]
    if not daily_rows:
        daily_rows = [{"日期": _date_to_lark_ts(t1), "产出": 0.0, "消耗": 0.0}]
    _write_csv(output_dir / "08_消耗_每日表.csv", daily_rows, ["日期", "产出", "消耗"])
    written.append(output_dir / "08_消耗_每日表.csv")

    # 09 每个游戏的产出、消耗：优先 t1（昨日），否则取最新
    game_rows = _load_prod_sales_game_latest(raw_dir, t7, target_date=t1)
    if not game_rows and rows_daily and prod_col_daily and cons_col_daily and game_col_daily and date_col:
        skip_items = ("全部汇总", "全量合计", "全平台汇总", "ALL", "奖励", "赠送", "游戏输赢", "兑换")
        dates_in_data = sorted({_parse_date_to_iso(r.get(date_col, "")) for r in rows_daily if date_col}, reverse=True)
        dates_in_data = [x for x in dates_in_data if x]
        pick_d = t1 if dates_in_data and t1 in dates_in_data else (dates_in_data[0] if dates_in_data else "")
        for r in rows_daily:
            d = _parse_date_to_iso(r.get(date_col, ""))
            if d != pick_d:
                continue
            g = str(r.get(game_col_daily, "") or "").strip()
            if not g or g in skip_items:
                continue
            prod_val = _safe_prod_cons(r.get(prod_col_daily))
            cons_val = _safe_prod_cons(r.get(cons_col_daily))
            game_rows.append({"游戏名称": g, "产出": round(prod_val, 2), "消耗": round(cons_val, 2)})
    if not game_rows and rows_game and game_col and (prod_col or cons_col):
        skip_items = ("全部汇总", "全量合计", "全平台汇总", "ALL", "奖励", "赠送", "游戏输赢", "兑换")
        date_col_g = _find_col(cols_game, *date_col_cands)
        dates_in_game = sorted({_parse_date_to_iso(r.get(date_col_g, "")) for r in rows_game if date_col_g}, reverse=True)
        dates_in_game = [x for x in dates_in_game if x]
        pick_dg = t1 if dates_in_game and t1 in dates_in_game else (dates_in_game[0] if dates_in_game else "")
        for r in rows_game:
            if pick_dg and date_col_g and _parse_date_to_iso(r.get(date_col_g, "")) != pick_dg:
                continue
            g = str(r.get(game_col, "") or "").strip()
            if not g or g in agg_labels or g in skip_items:
                continue
            prod_val = _safe_prod_cons(r.get(prod_col)) if prod_col else 0.0
            cons_val = _safe_prod_cons(r.get(cons_col)) if cons_col else 0.0
            game_rows.append({"游戏名称": g, "产出": round(prod_val, 2), "消耗": round(cons_val, 2)})
    if not game_rows:
        game_rows = [{"游戏名称": "（需抓取 prod_sales 平台产销情况）", "产出": 0.0, "消耗": 0.0}]
    if len(game_rows) == 1 and game_rows[0].get("游戏名称") in ("（需抓取 prod_sales 平台产销情况）",):
        _bi_log("09表仅含占位行，无按游戏明细", detail="请确保 skip_collect=false 且 prod_sales 抓取时已展开日期")
    _write_csv(output_dir / "09_消耗_按游戏表.csv", game_rows, ["游戏名称", "产出", "消耗"])
    written.append(output_dir / "09_消耗_按游戏表.csv")
    return written


def _refine_daily_metrics(conn: Any, output_dir: Path, t1: str, t0: str, raw_dir: Path | None = None) -> list[Path]:
    """14 付费人数金额增幅、15 ARPU、16 ARPPU — 来自 stats_recharge（充值数据统计），取 ALL/全部汇总 行"""
    written: list[Path] = []
    q = lambda slug, df=None, dt=None: _query_table_or_raw(conn, raw_dir, slug, df, dt)
    rows = q("stats_recharge", t0, t1)
    if not rows:
        rows = q("daily_ops_summary", t0, t1)
    if not rows:
        _write_csv(output_dir / "14_充值_付费人数金额增幅表.csv", [{"当天付费人数": 0, "付费总金额": 0.0, "增幅": 0.0}], ["当天付费人数", "付费总金额", "增幅"])
        _write_csv(output_dir / "15_充值_ARPU表.csv", [{"ARPU数值": 0.0, "ARPU增幅": 0.0}], ["ARPU数值", "ARPU增幅"])
        _write_csv(output_dir / "16_充值_ARPPU表.csv", [{"ARPPU数值": 0.0, "ARPPU涨幅": 0.0}], ["ARPPU数值", "ARPPU涨幅"])
        return [output_dir / "14_充值_付费人数金额增幅表.csv", output_dir / "15_充值_ARPU表.csv", output_dir / "16_充值_ARPPU表.csv"]

    cols = list(rows[0].keys())
    date_col = _find_col(cols, "日期", "date", "统计日期") or "_ingested_date"
    paid_count_col = _find_col(cols, "充值人数", "付费人数", "付费", "人数")
    paid_amt_col = _find_col(cols, "当日充值总额", "付费总金额", "充值", "金额", "总金额")
    arpu_col = _find_col(cols, "Arpu", "ARPU", "arpu")
    ch_col_rec = _find_col(cols, "渠道", "channel")
    rec_total_labels = ("ALL", "全部汇总", "全平台", "> ALL")
    # 仅取汇总行（ALL/全部汇总），避免渠道明细覆盖
    rows_all = [r for r in rows if not ch_col_rec or str(r.get(ch_col_rec, "")).strip() in rec_total_labels]
    if not rows_all:
        rows_all = rows
    by_date: dict[str, dict] = {}
    for r in rows_all:
        dk = _parse_date_to_iso(r.get(date_col, ""))
        if dk:
            by_date[dk] = r
    dates_sorted = sorted([d for d in by_date if d], reverse=True)
    pick_d1 = t1 if dates_sorted and t1 in dates_sorted else (dates_sorted[0] if dates_sorted else "")
    idx1 = dates_sorted.index(pick_d1) + 1 if pick_d1 and pick_d1 in dates_sorted else 1
    pick_d0 = dates_sorted[idx1] if idx1 < len(dates_sorted) else ""
    r1 = by_date.get(pick_d1, {}) if pick_d1 else {}
    r0 = by_date.get(pick_d0, {}) if pick_d0 else {}

    def _pct(cur: float, prev: float) -> float:
        return round((cur - prev) / prev * 100, 2) if prev else 0.0

    # 14 付费人数、金额、增幅（增幅=付费总金额相较于前一天的增幅）
    pc1, pc0 = _safe_float(r1.get(paid_count_col)), _safe_float(r0.get(paid_count_col))
    pa1, pa0 = _safe_float(r1.get(paid_amt_col)), _safe_float(r0.get(paid_amt_col))
    paid_pct = _pct(pa1, pa0)  # 增幅为付费总金额较前一日
    row14 = [{"当天付费人数": int(pc1), "付费总金额": round(pa1, 2), "增幅": paid_pct}]
    _write_csv(output_dir / "14_充值_付费人数金额增幅表.csv", row14, ["当天付费人数", "付费总金额", "增幅"])
    written.append(output_dir / "14_充值_付费人数金额增幅表.csv")

    # 15 ARPU
    arpu1, arpu0 = _safe_float(r1.get(arpu_col)), _safe_float(r0.get(arpu_col))
    arpu_pct = _pct(arpu1, arpu0)
    row15 = [{"ARPU数值": round(arpu1, 2), "ARPU增幅": arpu_pct}]
    _write_csv(output_dir / "15_充值_ARPU表.csv", row15, ["ARPU数值", "ARPU增幅"])
    written.append(output_dir / "15_充值_ARPU表.csv")

    # 16 ARPPU = 当日充值总额/充值人数，增幅为较前一日
    arppu1 = pa1 / pc1 if pc1 else 0.0
    arppu0 = pa0 / pc0 if pc0 else 0.0
    arppu_pct = _pct(arppu1, arppu0)
    row16 = [{"ARPPU数值": round(arppu1, 2), "ARPPU涨幅": arppu_pct}]
    _write_csv(output_dir / "16_充值_ARPPU表.csv", row16, ["ARPPU数值", "ARPPU涨幅"])
    written.append(output_dir / "16_充值_ARPPU表.csv")

    return written


def _parse_tier_amount(label: str) -> float:
    """从档位标签解析金额，如 (0,50] -> 50, (50,300] -> 300, 50元 -> 50（取区间上界）"""
    s = (label or "").strip()
    nums = re.findall(r"[\d.]+", s)
    if nums:
        try:
            return float(nums[-1])  # 区间取上界，如 (0,50] -> 50
        except ValueError:
            pass
    return 0.0


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


def _load_recharge_status_latest(raw_dir: Path | None, date_from: str | None, target_date: str | None = None) -> list[dict]:
    """从 raw/recharge_status.csv 加载目标日期的数据。优先 target_date（昨日），若无则取最新。保留父子行顺序。"""
    raw = _load_raw_csv(raw_dir, "recharge_status")
    if not raw:
        return []
    cols = list(raw[0].keys())
    date_col = _find_col(cols, "日期", "date", "统计日期", "业务日期") or (cols[0] if cols else None)
    range_col = _find_col(cols, "统计范围", "不同充值金额分等级", "渠道", "金额档位", "等级")
    skip_labels = ("所有", "全部", "ALL", "全部汇总", "全平台", "> ALL")

    def _norm_date(v: str) -> str:
        s = (str(v) or "").strip()
        if not s:
            return ""
        # 2026-03-19 或 2026/3/19 等
        try:
            if "-" in s and len(s) >= 10:
                return s[:10]
            if "/" in s:
                parts = s.replace("-", "/").split("/")
                if len(parts) == 3:
                    y, m, d = parts[0], parts[1].zfill(2), parts[2].zfill(2)
                    return f"{y}-{m}-{d}"
        except (ValueError, IndexError):
            pass
        return ""

    # 1. 传播空日期（子行继承上一行）
    last_date = ""
    for r in raw:
        v = r.get(date_col, "")
        d = _norm_date(v) if v else ""
        if d:
            last_date = d
        if last_date:
            r["_effective_date"] = last_date
        else:
            r["_effective_date"] = ""

    # 2. 过滤日期范围，优先 target_date（昨日），否则取最新
    candidates = [r for r in raw if r.get("_effective_date")]
    if date_from:
        candidates = [r for r in candidates if r["_effective_date"] >= date_from[:10]]
    if not candidates:
        return []
    dates_avail = {r["_effective_date"] for r in candidates}
    pick_date = (target_date[:10] if target_date and target_date[:10] in dates_avail else None) or max(dates_avail)
    latest_rows = [r for r in candidates if r["_effective_date"] == pick_date]

    # 3. 排除「所有」，保留各统计范围
    out = [
        r for r in latest_rows
        if range_col and str(r.get(range_col, "")).strip() and str(r.get(range_col, "")).strip() not in skip_labels
    ]
    for r in out:
        r.pop("_effective_date", None)
    return out


def _try_tier_rows_from_stats_recharge_raw(raw_dir: Path | None, t1: str) -> list[dict]:
    """
    若 stats_recharge.csv 含「不同充值金额分等级」等档位列，则按 t1（昨日）筛选生成 10/11 行。
    日期列支持 2026/3/23；展开子行继承上一行日期。
    """
    raw = _load_raw_csv(raw_dir, "stats_recharge")
    if not raw:
        return []
    cols = list(raw[0].keys())
    date_col = _find_col(cols, "日期", "date", "统计日期")
    tier_col = _find_col(cols, "不同充值金额分等级", "统计范围", "金额档位", "充值金额档位", "SKU")
    cnt_col = _find_col(cols, "充值人数", "人数", "付费人数", "充值次数")
    amt_col = _find_col(cols, "当日充值总额", "此等级总金额", "充值金额", "总金额")
    ch_col = _find_col(cols, "渠道", "channel")
    if not (date_col and tier_col and cnt_col):
        return []
    last = ""
    tgt = t1[:10]
    skip_tier = {"", "ALL", "全部汇总", "全平台", "> ALL", "所有"}
    skip_ch = ("ALL", "全部汇总", "全平台", "> ALL")
    out: list[dict] = []
    for r in raw:
        di = _parse_date_to_iso(r.get(date_col))
        if di:
            last = di
        eff = di or last
        if eff != tgt:
            continue
        tier = str(r.get(tier_col, "")).strip()
        if not tier or tier.upper() in {x.upper() for x in skip_tier if x}:
            continue
        if ch_col:
            chv = str(r.get(ch_col, "")).strip()
            if chv in skip_ch:
                continue
        cnt = int(_safe_float(r.get(cnt_col, 0)))
        amt = _safe_float(r.get(amt_col, 0)) if amt_col else 0.0
        total_amt = amt if amt > 0 else (cnt * _parse_tier_amount(tier))
        out.append(
            {"不同充值金额分等级": _format_recharge_tier_label(tier), "人数": cnt, "此等级总金额": round(total_amt, 2)}
        )
    return out


def _refine_recharge(conn: Any, output_dir: Path, t1: str, days: int = 7, raw_dir: Path | None = None) -> list[Path]:
    """10 付费人数按SKU、11 付费金额按SKU。数据来源：recharge_status（平台充值情况），最新日期各统计范围。人数=充值次数，此等级总金额=充值金额（或 充值次数×档位金额）。"""
    written: list[Path] = []
    dt = datetime.strptime(t1[:10], "%Y-%m-%d")
    date_from = (dt - timedelta(days=days)).strftime("%Y-%m-%d")
    q = lambda slug, df=None, dt_end=None: _query_table_or_raw(conn, raw_dir, slug, df, dt_end)

    # 优先：stats_recharge 含档位列时按昨日从 raw 取数（与多维表口径一致）
    tier_from_sr = _try_tier_rows_from_stats_recharge_raw(raw_dir, t1)

    # 其次：raw/recharge_status.csv，优先 t1（昨日），无则查 DuckDB
    rows_from_status = _load_recharge_status_latest(raw_dir, date_from, target_date=t1)
    if not rows_from_status:
        recharge_status_rows = q("recharge_status", date_from, None) or q("recharge_status", None, None)
        if recharge_status_rows:
            cols = list(recharge_status_rows[0].keys())
            range_col = _find_col(cols, "统计范围", "不同充值金额分等级", "渠道", "金额档位", "等级")
            date_col = _find_col(cols, "日期", "date") or (cols[0] if cols else None)
            by_date: dict[str, list] = {}
            last_date = ""
            for r in recharge_status_rows:
                d_iso = _parse_date_to_iso(r.get(date_col, ""))
                if d_iso:
                    last_date = d_iso
                d_key = last_date if last_date else "latest"
                if d_key not in by_date:
                    by_date[d_key] = []
                by_date[d_key].append(r)
            valid_dates = [k for k in by_date if k != "latest" and len(k) == 10 and k.replace("-", "").isdigit()]
            pick_date = t1 if valid_dates and t1 in valid_dates else (max(valid_dates, key=lambda x: x, default="") if valid_dates else "")
            if not pick_date and "latest" in by_date:
                pick_date = "latest"
            skip_labels = ("所有", "全部", "ALL", "全部汇总", "全平台", "> ALL")
            rows_from_status = [
                r for r in by_date.get(pick_date, [])
                if range_col and str(r.get(range_col, "")).strip() and str(r.get(range_col, "")).strip() not in skip_labels
            ]

    range_col, count_col, amt_col, date_col = None, None, None, None
    if rows_from_status:
        cols = list(rows_from_status[0].keys())
        range_col = _find_col(cols, "统计范围", "不同充值金额分等级", "渠道", "金额档位", "等级")
        count_col = _find_col(cols, "充值次数", "人数", "付费人数")
        amt_col = _find_col(cols, "充值金额", "金额", "档位金额", "此等级总金额")
        date_col = _find_col(cols, "日期", "date") or (cols[0] if cols else None)

    rows: list[dict] = []
    if tier_from_sr:
        rows = list(tier_from_sr)
    if not rows and rows_from_status and range_col and count_col:
        out_rows = []
        for r in rows_from_status:
            label = str(r.get(range_col, "")).strip() or "（未知）"
            cnt = int(_safe_float(r.get(count_col, 0)))
            amt_val = _safe_float(r.get(amt_col, 0))
            # 此等级总金额：优先用充值金额（实际总额），否则 充值次数×档位金额
            total_amt = amt_val if amt_val > 0 else (cnt * _parse_tier_amount(label))
            out_rows.append({"不同充值金额分等级": _format_recharge_tier_label(label), "人数": cnt, "此等级总金额": round(total_amt, 2)})
        if out_rows:
            rows = out_rows

    if not rows:
        rows = _aggregate_recharge_from_recharge_daily(conn, date_from, t1)
    if not rows:
        rows = _aggregate_recharge_from_detail(conn, date_from, t1)
    if not rows:
        recharge_slugs = ["recharge_status", "stats_recharge", "recharge_daily", "recharge_history"]
        for slug in recharge_slugs:
            raw_rows = q(slug, date_from, t1)
            if not raw_rows:
                continue
            cols = list(raw_rows[0].keys())
            if slug == "stats_recharge":
                dc = _find_col(cols, "日期", "date", "统计日期")
                if dc:
                    last_ff = ""
                    filtered: list[dict] = []
                    for r in raw_rows:
                        di = _parse_date_to_iso(r.get(dc))
                        if di:
                            last_ff = di
                        eff = di or last_ff
                        if eff == t1[:10]:
                            filtered.append(r)
                    raw_rows = filtered
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


def _run_refiner(date_str: str | None = None, output_dir: Path | None = None, raw_dir: Path | None = None) -> tuple[list[Path], list[str]]:
    """提纯逻辑：t1=报表**数据日**（通常为昨日）。

    date_str:
      - **传入时**：必须是 YYYY-MM-DD，且表示「要出数的那一天」（= t1）。主流程传入的已是昨日，**不得**再减一天。
      - **未传时**：由本函数用 datetime.now() 推算昨日为 t1。

    历史 Bug：曾把 date_str 当成「今天」再减一天，导致 t1 变成前天（例如应取 3/23 却取 3/22）。
    """
    from l3_node.mcp_tools.bi.data_store import _get_conn
    from l3_node.mcp_tools.bi.paths import get_bi_output_dir, get_bi_raw_dir, ensure_bi_dirs
    ensure_bi_dirs()
    out = output_dir or get_bi_output_dir()
    raw = raw_dir if raw_dir and raw_dir.exists() else get_bi_raw_dir()
    now = datetime.now()
    ds = (date_str or "").strip()
    if ds:
        try:
            t1 = ds[:10]
            dt1 = datetime.strptime(t1, "%Y-%m-%d")
            t0 = (dt1 - timedelta(days=1)).strftime("%Y-%m-%d")
            t7 = (dt1 - timedelta(days=6)).strftime("%Y-%m-%d")
        except ValueError:
            ds = ""
    if not ds:
        t1 = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        t0 = (now - timedelta(days=2)).strftime("%Y-%m-%d")
        t7 = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    _bi_log(
        "Step 2 提纯",
        detail=f"数据日 t1={t1}（date_str 直接作 t1，不二次减天） t0={t0} t7={t7} | raw_dir={raw} | output_dir={out}",
        progress=True,
    )

    conn = _get_conn()
    written: list[Path] = []
    errors: list[str] = []
    try:
        written += _refine_user_activity(conn, out, t1, t0, t7, raw_dir=raw)
        written += _refine_retention(conn, out, t1, raw_dir=raw)
        written += _refine_consumption(conn, out, t1, t7, raw_dir=raw)
        written += _refine_daily_metrics(conn, out, t1, t0, raw_dir=raw)
        written += _refine_recharge(conn, out, t1, days=7, raw_dir=raw)
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
) -> tuple[int, list[str], list[tuple[str, str]]]:
    """返回 (成功数, 错误列表, 跳过列表[(表名, 原因)])"""
    if not lark_bitable_config.get("enabled"):
        return (0, [], [])
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
        return (0, ["lark_bitable.app_token 或 tables 未配置"], [])

    try:
        import sys
        from l3_node.paths import get_app_root
        plugin_root = get_app_root() / "skills_repo" / "plugin" / "2-track-a-atomic-mcp"
        if plugin_root.exists() and str(plugin_root) not in sys.path:
            sys.path.insert(0, str(plugin_root))
        from tools.atom_lark_bitable_sync import sync_csv_to_bitable  # type: ignore[import-untyped]
    except ImportError as e:
        return (0, [f"无法导入 atom_lark_bitable_sync: {e}"], [])

    ok_count = 0
    errors: list[str] = []
    skipped: list[tuple[str, str]] = []
    default_text_columns = {"10_充值_付费人数按SKU.csv": ["不同充值金额分等级"], "11_充值_付费金额按SKU.csv": ["不同充值金额分等级"]}
    text_cols_per_table = lark_bitable_config.get("text_columns") or {}
    field_mapping_per_table = dict(lark_bitable_config.get("field_mapping") or {})
    # 01 表列名已与 Lark 统一为「增幅（%）」；06 表为「留存率（%）」。若 Lark 表用其他字段名，可在 field_mapping 中配置

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
            skipped.append((name, "未配置 table_id"))
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
    return (ok_count, errors, skipped)


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


def _is_raw_dir_fresh_for_today(raw_dir: Path) -> bool:
    """
    raw 目录存在，且核心 CSV 齐全非空；且这些文件中最新修改时间的「本地日历日」为今天，
    视为今日已执行过抓取（与 DuckDB 含今日 _ingested_date 口径并列）。
    """
    if not raw_dir.exists() or not raw_dir.is_dir():
        return False
    today = datetime.now().date()
    mtimes: list[float] = []
    for slug in _RAW_FRESHNESS_SLUGS:
        p = raw_dir / f"{slug}.csv"
        try:
            st = p.stat()
        except OSError:
            return False
        if not p.is_file() or st.st_size <= 0:
            return False
        mtimes.append(st.st_mtime)
    if not mtimes:
        return False
    newest = datetime.fromtimestamp(max(mtimes)).date()
    return newest == today


def _cdp_endpoint_reachable(cdp_url: str, timeout: float = 2.5) -> bool:
    """检测 Chrome DevTools 端口是否可连（避免 12+ 张表逐张报同一 ECONNREFUSED）。"""
    from urllib.parse import urlparse

    u = urlparse((cdp_url or "").strip() or "http://127.0.0.1:9222")
    host = u.hostname or "127.0.0.1"
    try:
        port = int(u.port or 9222)
    except (TypeError, ValueError):
        port = 9222
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _cdp_unavailable_user_message(cdp_url: str) -> str:
    return (
        f"无法连接 Chrome 远程调试 ({cdp_url})，本机拒绝连接(ECONNREFUSED) 表示该端口没有浏览器在监听。\n\n"
        "【浏览器需要你提前自己打开】脚本不会自动弹出 Chrome。必须先启动「带远程调试端口」的 Chrome，"
        "Python 才能连上去替你点菜单、抓表格（这样才能用你已登录的 BI 会话）。\n\n"
        "请按顺序处理：\n"
        "1) 先关掉平时用的 Chrome（避免占端口）。\n"
        "2) 在项目根打开 PowerShell，执行: .\\scripts\\launch_chrome_debug_bi.ps1\n"
        "   （或手动启动 Chrome，启动参数加上: --remote-debugging-port=9222）\n"
        "3) 用**这一只**弹出来的 Chrome 打开 BI 网址并完成登录。\n"
        "4) 保持该窗口不要关，核对 full_spa.cdp_url / BI_SPA_CDP_URL 与端口一致（默认 http://127.0.0.1:9222）。\n"
        "5) 再在项目根执行: python scripts\\run_bi_daily_report.py\n\n"
        "说明：当前设计是「连接已有浏览器」，不是「由脚本启动无登录的新浏览器」。"
    )


async def _run_bi_daily_report_async(config: dict[str, Any] | None = None) -> dict[str, Any]:
    global _BI_LOG_DIR, _BI_VERBOSE
    _bi_debug("_run_bi_daily_report_async", "entry", data={"config_keys": list((config or {}).keys())})
    _bi_merge_dotenv_for_skill()
    cfg = _load_config(config)
    _bi_reconcile_llm_engine_ref_with_agent()
    _BI_LOG_DIR = _get_bi_log_dir(cfg)
    _BI_VERBOSE = cfg.get("verbose_log", True)
    _bi_log_reset()
    _bi_log("========== BI 每日战报流程开始 ==========", detail=f"verbose_log={_BI_VERBOSE}", progress=True)
    _bi_log("日志目录", detail=str(_BI_LOG_DIR))
    _bi_debug(
        "init",
        "config_loaded",
        data={
            "output_refiner": str((cfg.get("storage") or {}).get("refiner_output_path", "")),
            "skip_collect": cfg.get("skip_collect"),
            "run_refiner": cfg.get("run_refiner", True),
            "lark_enabled": (cfg.get("lark_bitable") or {}).get("enabled"),
        },
    )
    result: dict[str, Any] = {"success": False, "stage": "init", "data_updated": False, "output_paths": [], "lark_sync_ok": 0, "lark_sync_errors": [], "strategic_report_sent": False, "dashboard_analysis_sent": False, "email_ok": False, "email_error": "", "error": ""}

    from l3_node.mcp_tools.bi.paths import get_bi_output_dir, ensure_bi_dirs, get_bi_raw_dir
    _bi_debug("Step 0", "entry", detail="ensure_bi_dirs")
    ensure_bi_dirs()
    output_dir = get_bi_output_dir((cfg.get("storage") or {}).get("refiner_output_path") or "")
    output_dir.mkdir(parents=True, exist_ok=True)
    _bi_log("Step 0: 配置加载完成，输出目录已就绪", detail=f"output_dir={output_dir}", progress=True)

    # 与 scripts/run_bi_scraper_spa.py 一致：默认 get_bi_raw_dir()，不用 --output-dir 时同一路径
    raw_dir_collect = get_bi_raw_dir()
    _bi_debug("Step 1", "entry", detail="检查 DuckDB 与 raw 目录新鲜度")
    _bi_log(
        "Step 1: 正在检查数据新鲜度（DuckDB 今日入库 + raw 目录文件）...",
        detail=f"raw_dir={raw_dir_collect}",
        progress=True,
    )
    duckdb_fresh = _is_duckdb_fresh_for_today()
    raw_fresh = _is_raw_dir_fresh_for_today(raw_dir_collect)
    need_collect = (not duckdb_fresh) or (not raw_fresh)
    _bi_debug(
        "Step 1",
        "exit",
        data={"duckdb_fresh": duckdb_fresh, "raw_fresh": raw_fresh, "need_collect": need_collect},
    )
    _bi_log(
        "Step 1 结果",
        detail=f"DuckDB含今日入库={duckdb_fresh} | raw目录今日已抓={raw_fresh} | 需重新抓取={need_collect}",
        progress=True,
    )
    if need_collect:
        _bi_log(
            "Step 1: DuckDB 或 raw 不新鲜/缺失，将执行 SPA 全量抓取（spa_collector / 等同 atom_web_scraper CDP 流程）",
            detail="任一不满足即抓：DuckDB 无今日 _ingested_date，或 raw 缺文件/非今日 mtime",
            progress=True,
        )
        _bi_debug("Step 1.1", "branch", data={"skip_collect": cfg.get("skip_collect")})
        # 数据不新鲜时必须抓数后再提纯/同步；skip_collect 仅表示「数据已新鲜时可跳过抓取」，不得在不新鲜时静默跳过
        if cfg.get("skip_collect", False):
            _bi_log(
                "Step 1.1 提示",
                detail="skip_collect=true 但 DuckDB/raw 不新鲜，本次仍强制执行 SPA 抓取；请先启动 Chrome 远程调试并登录 BI",
                progress=True,
            )
        _bi_log("Step 1.1: 开始执行 SPA 抓取（Chrome CDP）...", progress=True)
        _bi_debug("Step 1.1", "entry", detail="run_full_spa_collect")
        try:
            from l3_node.mcp_tools.bi.spa_collector import run_full_spa_collect, parse_direct_url_map_from_full_spa

            full_spa = cfg.get("full_spa") or {}
            _bu = str(full_spa.get("base_url") or "").strip() or "https://bi-admin-web.heronpro.xin/#/layout/person"
            base_url = _bu if _bu.lower().startswith("http") else "https://bi-admin-web.heronpro.xin/#/layout/person"
            _cdp = str(full_spa.get("cdp_url") or os.environ.get("BI_SPA_CDP_URL") or "").strip() or "http://127.0.0.1:9222"
            cdp_url = _cdp if _cdp.lower().startswith("http") else "http://127.0.0.1:9222"
            _bi_log("Step 1.1: 检测 CDP 端口是否可连", detail=cdp_url, progress=True)
            if not await asyncio.to_thread(_cdp_endpoint_reachable, cdp_url):
                result["stage"] = "collect"
                result["error"] = _cdp_unavailable_user_message(cdp_url)
                _bi_log("Step 1.1 前置检查失败", detail="CDP 不可达，已中止抓取（避免逐表重复报错）", progress=True)
                _bi_debug("Step 1.1", "cdp_unreachable", data={"cdp_url": cdp_url})
                return result
            # 与 run_bi_scraper_spa：未传 slug 时 slugs=None → 整表 MENU_ITEMS；仅 full_spa.slugs 非空列表时才收窄
            _cfg_slugs = full_spa.get("slugs")
            slugs = _cfg_slugs if isinstance(_cfg_slugs, list) and len(_cfg_slugs) > 0 else None
            dm = parse_direct_url_map_from_full_spa(full_spa)

            def _spa_collect_progress_cb(idx: int, total: int, slug: str, result: dict) -> None:
                st = result.get("status", "")
                if st == "success":
                    _bi_log(f"[{idx}/{total}] {slug}", detail=f"OK ({result.get('rows_count', 0)} rows)", progress=True)
                else:
                    _bi_log(f"[{idx}/{total}] {slug}", detail=f"FAIL: {result.get('error', result)}", progress=True)

            ok, fail, failed = await asyncio.to_thread(
                run_full_spa_collect,
                slugs=slugs,
                base_url=base_url,
                cdp_url=cdp_url,
                use_discover=False,
                auto_ingest=True,
                raw_dir=raw_dir_collect,
                direct_url_map=dm,
                progress_cb=_spa_collect_progress_cb,
            )
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
        _bi_log("Step 1 结果: DuckDB 与 raw 均新鲜，跳过抓取", progress=True)

    # 长耗时 SPA 后再次对齐 Key/engine_ref，避免 Step 3.5 仍 import __main__ 且 os.environ 未含 DASHSCOPE
    _bi_reconcile_llm_engine_ref_with_agent()

    # run_refiner: false 时跳过 Step 2，不生成 output CSV（后续 Lark/邮件将无新 CSV 可同步）
    if cfg.get("run_refiner", True) is False:
        _bi_debug("Step 2", "skipped", detail="run_refiner=false")
        _bi_log(
            "Step 2: 已跳过 Refiner（run_refiner=false），不生成提纯 CSV",
            detail="若需完整战报请改为 run_refiner: true",
            progress=True,
        )
        result["output_paths"] = []
    else:
        _bi_debug("Step 2", "entry", detail="运行提纯 _run_refiner")
        _bi_log("Step 2: 正在运行 Refiner 提纯（优先 raw/*.csv，否则 DuckDB → output）...", progress=True)
        try:
            raw_dir = raw_dir_collect
            date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            written, errs = _run_refiner(date_str=date_str, output_dir=output_dir, raw_dir=raw_dir)
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
        _bi_log("Step 3: 正在将 CSV 同步到 Lark 多维表格（atom_lark_bitable_sync）...", progress=True)
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
                if "FieldNameNotFound" in detail:
                    _bi_log("Step 3 提示", detail=f"FieldNameNotFound 表示 Lark 表字段名与 CSV 列名不一致。01 表列名「增幅（%）」、06 表「留存率（%）」。请在 bi_daily_report.yaml 的 lark_bitable.field_mapping 中配置映射（按 11_LARK_TABLE_SCHEMA.md 对照）")
                elif "TableIdNotFound" in detail:
                    _bi_log("Step 3 提示", detail=f"TableIdNotFound 表示 table_id 不存在。请在 Lark 多维表中打开对应子表，从 URL 的 ?table=tblXXX 获取正确 table_id，更新到 bi_daily_report.yaml 的 lark_bitable.tables")

        try:
            sync_ok, sync_errs, sync_skipped = _sync_refiner_to_lark([Path(p) for p in result["output_paths"]], lark_bitable, on_table=_on_table)
            result["lark_sync_ok"] = sync_ok
            result["lark_sync_errors"] = sync_errs
            result["lark_sync_skipped"] = sync_skipped
            _bi_debug("Step 3", "exit", data={"sync_ok": sync_ok, "sync_errs_count": len(sync_errs), "sync_skipped_count": len(sync_skipped)})
            if sync_skipped:
                _bi_log("Step 3 跳过汇总", detail="; ".join(f"{n}: {r}" for n, r in sync_skipped) + " — 请在 bi_daily_report.yaml 的 lark_bitable.tables 中为对应 CSV 配置 table_id（从 Lark 子表 URL 的 ?table=tblXXX 获取）")
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

    _bi_reconcile_llm_engine_ref_with_agent()

    # Step 3.5: 同步完 Lark 多维表后 — 自动用 Lark 机器人向用户汇报分析数据（调用战略分析 + 推送）
    strategic_cfg = cfg.get("strategic_report") or {}
    _bi_debug("Step 3.5", "branch", data={"enabled": strategic_cfg.get("enabled", True)})
    if strategic_cfg.get("enabled", True):
        _bi_debug("Step 3.5", "entry", detail="generate_bi_strategic_report_async")
        _bi_log("Step 3.5: 正在调用 LLM 生成战略深度分析战报（金字塔原则）...", progress=True)
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
                chat_id = (os.environ.get("BI_LARK_CHAT_ID") or os.environ.get("LARK_CHAT_ID") or "").strip()
                if not chat_id:
                    chat_id = (dist.get("lark_chat_id") or "").strip()
                elif dist.get("lark_chat_id") and dist.get("lark_chat_id") != chat_id:
                    _bi_log("Step 3.5: 使用环境变量 BI_LARK_CHAT_ID 覆盖 config 中的 lark_chat_id", detail=f"chat_id={chat_id[:20]}...", progress=True)
                if str(webhook).startswith("${"):
                    webhook = ""
                if str(chat_id).startswith("${"):
                    chat_id = ""
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
                    _bi_log("Step 3.5: 正在将战略报告推送到 Lark 机器人...", detail=f"chat_id={chat_id or '(无)'}, webhook={'已配置' if webhook else '无'}")
                    _bi_debug("Step 3.5", "lark_target", data={"chat_id": chat_id or "", "webhook": bool(webhook)})
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
                            err_msg = r.get("error", "")
                            _bi_log("Step 3.5 警告: Lark 推送失败", detail=err_msg)
                            if "can NOT be out of the chat" in str(err_msg):
                                _bi_log("Step 3.5 提示", detail=f"当前 chat_id={chat_id or '(无)'}。若 BI 助手已加入其他群，请设置 BI_LARK_CHAT_ID=该群会话ID 后重试")
                            elif "already been dissolved" in str(err_msg):
                                _bi_log("Step 3.5 提示", detail=f"当前 chat_id={chat_id or '(无)'} 对应的群聊已解散。请将 BI_LARK_CHAT_ID 改为一个仍存在的群聊会话 ID（如 oc_bb2b468cb04acb709c2e7aa5683f8f08）")
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
        _bi_reconcile_llm_engine_ref_with_agent()
        dashboards = da_cfg.get("dashboards") or []
        _bi_debug("Step 4a", "entry", data={"dashboards": [d.get("name", "") for d in dashboards if isinstance(d, dict)]})
        analysis_output_subdir = str(da_cfg.get("analysis_output_subdir") or "统计分析").strip() or "统计分析"
        analysis_output_dir = output_dir / analysis_output_subdir
        analysis_output_dir.mkdir(parents=True, exist_ok=True)
        _bi_log("Step 4a: 生成仪表盘 LLM 分析（供邮件 + Lark 消息卡片）...", progress=True)
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
                _bi_log("Step 4a: 仪表盘分析已生成", detail=f"[{i + 1}/{len(dashboards)}] {name} → {saved_path.name}", progress=True)
            except Exception as e:
                _bi_debug("Step 4a", "dashboard_fail", exc=e, data={"name": name})
                _bi_log("Step 4a 警告: 仪表盘分析失败", detail=f"{name}: {e}")
        _bi_debug("Step 4a", "exit", data={"analyses_count": len(dashboard_analyses)})
        if not dashboard_analyses:
            _bi_log("Step 4a: 无仪表盘分析产出", detail="跳过后续邮件仪表盘段与 Step 4b", progress=True)
        elif da_cfg.get("push_dashboard_to_lark", True):
            # 将仪表盘分析作为 Lark 卡片消息发送到同一会话（与战略报告同一 chat_id）
            _lark_webhook = (dist.get("lark_webhook_url") or "").strip()
            _lark_chat_id = (os.environ.get("BI_LARK_CHAT_ID") or os.environ.get("LARK_CHAT_ID") or "").strip()
            if not _lark_chat_id:
                _lark_chat_id = (dist.get("lark_chat_id") or "").strip()
            elif dist.get("lark_chat_id") and dist.get("lark_chat_id") != _lark_chat_id:
                _bi_log("Step 4a: 使用环境变量 BI_LARK_CHAT_ID 覆盖 config", detail=f"chat_id={_lark_chat_id[:20]}...")
            if str(_lark_webhook).startswith("${"):
                _lark_webhook = ""
            if str(_lark_chat_id).startswith("${"):
                _lark_chat_id = ""
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
                            _bi_log("Step 4a: 仪表盘卡片已推送至 Lark", detail=f"{_name}", progress=True)
                        else:
                            err4 = _r.get("error", "")
                            _bi_log("Step 4a 警告: 仪表盘卡片推送失败", detail=f"{_name}: {err4}")
                            if "can NOT be out of the chat" in str(err4):
                                _bi_log("Step 4a 提示", detail=f"当前 chat_id={_lark_chat_id or '(无)'}。若 BI 助手已加入其他群，请设置 BI_LARK_CHAT_ID=该群会话ID 后重试")
                            elif "already been dissolved" in str(err4):
                                _bi_log("Step 4a 提示", detail=f"当前 chat_id 对应的群聊已解散，请将 BI_LARK_CHAT_ID 改为仍存在的群聊 ID")
                    if sent_ok:
                        result["dashboard_analysis_sent"] = True
                        _bi_log("Step 4a: 仪表盘分析已推送至 Lark", detail=f"共 {sent_ok}/{len(dashboard_analyses)} 条", progress=True)
                except Exception as e:
                    _bi_log("Step 4a 警告: 仪表盘分析 Lark 推送异常", detail=str(e))
            else:
                _bi_log("Step 4a: 未配置 Lark chat_id/webhook，跳过仪表盘分析推送")

    # Step 3.6: 仪表盘推送完成后发送邮件（战略分析 + 仪表盘分析）
    _bi_log("接下来执行 Step 3.6 发送 BI 战报邮件（含战略分析 + 仪表盘分析）", progress=True)

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
            from_config = len(to_addrs)
            if not to_addrs:
                to_addrs = (os.environ.get("BI_SMTP_TO") or os.environ.get("BI_EMAIL_TO") or "").strip().split(",")
                to_addrs = [a.strip() for a in to_addrs if a.strip()]
                _bi_log("Step 3.6: 收件人来自环境变量", detail=f"BI_SMTP_TO/BI_EMAIL_TO 共 {len(to_addrs)} 人（config 中 to_addrs 为空或仅占位符）")
            _bi_debug("Step 3.6", "to_addrs_final", data={"count": len(to_addrs), "from_config": from_config})
            if len(to_addrs) == 1:
                _bi_log("Step 3.6: 当前收件人为 1 人", detail="若需多人收件，请在 ~/.jachin/config/skills/com.jachin.bi.daily_report/bi_daily_report.yaml 的 distribution.email.to_addrs 中配置多行邮箱（如 vivian@herontech.net、1807301549@qq.com），或设置 BI_SMTP_TO=邮箱1,邮箱2")
            if not smtp_config.get("user") or str(smtp_config.get("user", "")).startswith("${"):
                smtp_config["user"] = (os.environ.get("BI_SMTP_USER") or os.environ.get("SMTP_USER") or "").strip()
            if not smtp_config.get("password") or str(smtp_config.get("password", "")).startswith("${"):
                smtp_config["password"] = (os.environ.get("BI_SMTP_PASSWORD") or os.environ.get("SMTP_PASSWORD") or "").strip()
            # MCP atom_email_sender 仅作兜底：补全缺失的 SMTP；收件人已在 bi_daily_report.yaml / BI_SMTP_TO 时不得被 default_to_addrs 覆盖
            if not smtp_config.get("user") or not smtp_config.get("password") or not to_addrs:
                try:
                    from l3_node.jachin_config import load_mcp_config
                    from l3_node.paths import get_app_root
                    mcp_cfg = load_mcp_config("atom_email_sender", project_root=get_app_root())
                    mcp_smtp = mcp_cfg.get("smtp") or {}
                    if isinstance(mcp_smtp, dict) and (mcp_smtp.get("user") or "").strip() and (mcp_smtp.get("password") or "").strip():
                        if not smtp_config.get("user"):
                            smtp_config["user"] = str(mcp_smtp.get("user") or "").strip()
                        if not smtp_config.get("password"):
                            smtp_config["password"] = str(mcp_smtp.get("password") or "").strip()
                        if (mcp_smtp.get("host") or "").strip():
                            smtp_config["host"] = str(mcp_smtp.get("host")).strip()
                        try:
                            if mcp_smtp.get("port") is not None:
                                smtp_config["port"] = int(mcp_smtp.get("port"))
                        except (TypeError, ValueError):
                            pass
                    mcp_to = mcp_cfg.get("default_to_addrs") or []
                    if not to_addrs and isinstance(mcp_to, list) and mcp_to:
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
<div style="background:#e6f7ff; padding:12px 16px; border-radius:8px; margin-bottom:16px; border-left:4px solid #1890ff;">
<p style="margin:0 0 8px 0; font-weight:600;">本邮件由 jachin 系统自动发送</p>
<p style="margin:0; color:#c41d7f; font-size:13px;">⚠ 注意将此账号放入白名单，以防被当垃圾邮件误删！</p>
</div>
<h2 style="color:#1890ff;">📊 BI 每日战报 ({report_date})</h2>
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
                    _bi_log("Step 3.6: 邮件已发送", detail=f"收件人共 {len(to_addrs)} 人（mcp:atom_email_sender → tool_email_sender）", progress=True)
                    _bi_debug("Step 3.6", "email_sent", data={"to_count": len(to_addrs)})
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

    result["success"] = True
    result["stage"] = "done"
    result["report_sent"] = result["lark_sync_ok"] > 0
    result["lark_ok"] = result["lark_sync_ok"] > 0 and not result["lark_sync_errors"]

    _bi_debug("_run_bi_daily_report_async", "exit", data={"stage": result["stage"], "success": result["success"], "output_count": len(result["output_paths"])})
    _bi_log("========== BI 流程完成 ==========", progress=True)
    summary = {"success": result["success"], "stage": result["stage"], "output_count": len(result["output_paths"]), "lark_sync_ok": result["lark_sync_ok"], "lark_sync_errors": result["lark_sync_errors"], "strategic_report_sent": result.get("strategic_report_sent", False), "dashboard_analysis_sent": result.get("dashboard_analysis_sent", False), "email_ok": result.get("email_ok", False)}
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


def run_bi_daily_report_scheduled() -> dict[str, Any]:
    """
    供 APScheduler / BI loop 定时回调使用。

    Windows：在新控制台窗口中执行「等价于项目根下 python scripts/run_bi_daily_report.py」，
    终端可见 [BI] 步骤与 DIFF-LOG，避免后台黑箱。其它平台仍在本进程内 run_bi_daily_report()。
    子进程退出码由 scripts/run_bi_daily_report.py 根据 success 设置。
    """
    import subprocess
    import sys

    if sys.platform != "win32":
        return run_bi_daily_report()

    root = Path(__file__).resolve().parents[4]
    script = root / "scripts" / "run_bi_daily_report.py"
    if not script.is_file():
        logger.error("[BI Scheduled] 未找到 %s，退回进程内执行", script)
        return run_bi_daily_report()

    creation = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    try:
        cp = subprocess.run(
            [sys.executable, "-u", str(script)],
            cwd=str(root),
            env=os.environ.copy(),
            creationflags=creation,
        )
        ok = cp.returncode == 0
        return {
            "success": ok,
            "stage": "scheduled_console" if ok else "scheduled_console_failed",
            "data_updated": False,
            "output_paths": [],
            "lark_sync_ok": 0,
            "lark_sync_errors": [],
            "strategic_report_sent": False,
            "dashboard_analysis_sent": False,
            "email_ok": False,
            "email_error": "",
            "report_sent": False,
            "lark_ok": False,
            "scheduled_returncode": cp.returncode,
            "error": "" if ok else f"定时子进程退出码 {cp.returncode}（见弹出的控制台窗口输出）",
        }
    except Exception as e:
        logger.exception("[BI Scheduled] 启动新控制台子进程失败，退回进程内执行: %s", e)
        return run_bi_daily_report()


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
    _bi_merge_dotenv_for_skill()
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
            result = run_bi_daily_report_scheduled()
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
    _bi_merge_dotenv_for_skill()
    cfg = config or _load_config()
    _BI_LOG_DIR = _get_bi_log_dir(cfg)
    sched = cfg.get("schedule") or {}
    # 定时开关：schedule_enabled（顶层）与 schedule.enabled 任一为 false 即关闭
    top_ok = cfg.get("schedule_enabled", True) if "schedule_enabled" in cfg else True
    sched_ok = sched.get("enabled", True)
    enabled = bool(top_ok and sched_ok)
    _bi_debug("start_bi_scheduled_loop", "entry", data={"enabled": enabled, "schedule_enabled": top_ok, "schedule.enabled": sched_ok, "mode": sched.get("mode"), "run_at_hour": sched.get("run_at_hour"), "run_at_minute": sched.get("run_at_minute"), "interval_seconds": sched.get("interval_seconds")})
    if not enabled:
        logger.info("[BI 定时循环] 定时已关闭（schedule_enabled 或 schedule.enabled=false），跳过")
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
