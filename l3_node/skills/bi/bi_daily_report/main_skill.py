"""
BI 每日战报 — 主技能逻辑（一个插件仅此一个 skill）

本 skill 完成 BI 日报全流程：数据新鲜度检查、抓取更新、提纯输出 CSV、同步飞书多维表。
所有逻辑集中于此，供 L3 agent 预检直接调用或通过 skill 描述理解执行步骤。

## Skill 描述（供 L3 Agent）

当用户发起「BI分析」「帮我开始今天的BI分析」等意图时，按以下步骤执行：

1. **数据新鲜度**：检查 bi.duckdb 是否含今日 _ingested_date；若无则执行 SPA 抓取（spa_collector）并 ingest_csv。抓取前按 `full_spa.report_date_end`（默认昨天）写入统计日期并点「查询/对比查询」，与战报「最新完整日」一致
2. **数据提纯**：从 DuckDB / raw 按 Lark 表结构（11_LARK_TABLE_SCHEMA.md）与业务规则提炼 CSV：
   用户活跃（增幅含数值、周统计含老用户、渠道、日活占比、新增设备）、留存四表、消耗（当日金币七列、按游戏）、
   充值 SKU（人数、金额及各档位较前一日该档位金额增幅%）、付费汇总、Arpu/Arppu（日运营汇总）、游戏（完成局数/获胜/RTP·GGR/深度参与）
3. **Lark 同步**：将 output 下 CSV 同步到飞书多维表格（atom_lark_bitable_sync）
3.4 **KPI 快照卡片**：同步完成后，从同目录 CSV 拼装指标（按 👥/💰/🎮/⚖️ 分组、`---` 分隔、涨跌 🟢/🔻）推送 Lark
4. **仪表盘分析**（Step 4a）：对每个仪表盘调用 LLM 分析统计图数据 → 保存到 output → 通过 Lark 机器人推送消息卡片（分析+仪表盘链接）。**先于大战报执行**。
5. **战略深度分析（大战报）**：System 从 `STRATEGIC_REPORT_ANALYSIS_SPEC.md`（**v4 长文交付**）加载；注入 `bi_project` + output/raw **T vs T-1** 摘要 + DuckDB/CSV；在 Step 4a 之后执行。
6. **邮件通知**：调用 mcp:atom_email_sender 将战报发送至 distribution.email.to_addrs（邮件内顺序：一、BI 数据快报 → 二、仪表盘 → 三、Lark 同步 → 四、战略分析）。

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
| 平台产销 → 平台产销情况 | prod_sales | 08 当日金币（全）、09 每个游戏的产出消耗 |
| 用户数据统计 → 日活统计 | stats_user_dau | 08b 当日金币（渠道层） |
| 平台数据 → 平台充值情况 | recharge_status | 10 付费人数按SKU、11 付费金额按SKU |
| 充值数据统计 → 充值数据统计 | stats_recharge | 14 付费人数金额增幅（无日运营时 Arpu/Arppu 兜底） |
| 平台数据 → 日常报表 → 每日运营数据汇总 | daily_ops_summary | 15 Arpu 表、16 Arppu 表 |
| 游戏数据统计 → 核心产品每日数据表 | stats_game_core | 17 完成局数、18 用户获胜、20 游戏深度参与、21–26 漏斗图新开六子表 |
| 游戏数据统计 → 每日游戏数据 | stats_game_daily | 19 GameRTP、GGR |
| 数据明细 → 充值明细 → 每日充值明细 | detail_recharge_daily | SPA 抓取时翻遍分页（默认 10 条/页）写全量 raw |

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
from datetime import date, datetime, timedelta
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
    "stats_game_daily",       # 每日游戏数据（按游戏产出消耗 / RTP·GGR）
    "stats_game_compare",     # 游戏数据统计对比
    "stats_game_core",        # 核心产品每日数据表（局数、胜负）
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

# 仪表盘对外分享链接（share/base/dashboard/...）。Step 4a Lark 卡片与 Step 3.6 邮件内「打开仪表盘」均优先用此表，未命中再回退 config 的 dashboard_automation.dashboards[].url
_DASHBOARD_DISPLAY_URLS = {
    "仪表盘_用户登录活跃情况": "https://ssgkm409t6q5.sg.larksuite.com/share/base/dashboard/shrlghRi3WdEjFX9aH3LI3Bbf2c",
    "仪表盘_平台留存情况": "https://ssgkm409t6q5.sg.larksuite.com/share/base/dashboard/shrlgr00stzCWoJ2cySEAy4Ryie",
    "仪表盘_平台消耗情况": "https://ssgkm409t6q5.sg.larksuite.com/share/base/dashboard/shrlg77U8jQvTTodea2ZQifK8ab",
    # 游戏情况：请在 bi_daily_report.yaml dashboard_automation.dashboards 中配置 url；未配置时 Step4a 仅用多维表/CSV 摘要
    "仪表盘_游戏情况": "https://ssgkm409t6q5.sg.larksuite.com/share/base/dashboard/shrlgc0qFebIwVIkEaTy7HpktIc",
}


def _bi_spa_report_date_end(cfg: dict[str, Any]) -> date:
    """
    SPA 在 BI 后台填「统计日期」时使用的区间结束日（通常为昨天）。
    可选 full_spa.report_date_end: \"YYYY-MM-DD\"，或顶层 spa_report_date_end。
    """
    fs = cfg.get("full_spa") if isinstance(cfg.get("full_spa"), dict) else {}
    raw = fs.get("report_date_end") or cfg.get("spa_report_date_end")
    if isinstance(raw, str) and raw.strip():
        try:
            parts = raw.strip()[:10].split("-")
            if len(parts) == 3:
                y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
                return date(y, m, d)
        except (ValueError, TypeError):
            pass
    return (datetime.now() - timedelta(days=1)).date()


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


def _nonempty_str(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _merge_lark_tables_from_project_yaml(result: dict[str, Any], proj_raw: dict[str, Any]) -> None:
    """~/.jachin 下 YAML 里 table_id 为空的项，用项目 config 中的非空 table_id 补齐（避免本机配置落后导致大量「未配置 table_id」）。"""
    tables_user = (result.get("lark_bitable") or {}).get("tables")
    tables_proj = (proj_raw.get("lark_bitable") or {}).get("tables")
    if not isinstance(tables_proj, dict):
        return
    merged = dict(tables_user) if isinstance(tables_user, dict) else {}
    for key, pid in tables_proj.items():
        if _nonempty_str(merged.get(key)):
            continue
        if _nonempty_str(pid):
            merged[key] = pid
    result.setdefault("lark_bitable", {})["tables"] = merged


def _merge_dashboards_by_name_from_project(result: dict[str, Any], proj_raw: dict[str, Any]) -> None:
    """用户目录仅配置了 3 个仪表盘时，按 name 从项目 YAML 追加缺失项（如 仪表盘_游戏情况）。"""
    da_u = result.get("dashboard_automation") or {}
    da_p = proj_raw.get("dashboard_automation") or {}
    dash_u = da_u.get("dashboards")
    dash_p = da_p.get("dashboards")
    if not isinstance(dash_p, list):
        return
    out = list(dash_u) if isinstance(dash_u, list) else []
    names = {d.get("name") for d in out if isinstance(d, dict) and d.get("name")}
    for d in dash_p:
        if not isinstance(d, dict):
            continue
        n = d.get("name")
        if n and n not in names:
            out.append(dict(d))
            names.add(n)
    if out:
        result.setdefault("dashboard_automation", {})["dashboards"] = out


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
                # 本机优先读 ~/.jachin 时：用项目仓库 YAML 补全空的 table_id、缺的仪表盘 URL
                proj_raw: dict[str, Any] = {}
                if path == candidates[0] and candidates[1].exists():
                    try:
                        with open(candidates[1], encoding="utf-8") as pf:
                            proj_raw = yaml.safe_load(pf) or {}
                    except Exception:
                        proj_raw = {}
                if proj_raw:
                    _merge_lark_tables_from_project_yaml(result, proj_raw)
                    _merge_dashboards_by_name_from_project(result, proj_raw)
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
    在 Step 1.1 长时间抓取之后、Step 4a/3.5 之前应再调用一次，避免仅依赖流程开头的环境状态。
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


def _find_col_arpu_or_arppu(columns: list[str], *, want_arppu: bool) -> str | None:
    """匹配每日运营汇总中的 Arpu / Arppu 列。勿用 _find_col('Arpu')：子串 arpu 会误命中「Arppu」。"""
    for c in columns:
        if not c:
            continue
        raw = str(c).strip()
        cl = raw.lower().replace(" ", "")
        if want_arppu:
            if "arppu" in cl or cl.startswith("arppu") or raw.upper() == "ARPPU":
                return raw
        else:
            if "arppu" in cl:
                continue
            if cl == "arpu" or cl.startswith("arpu") or raw.upper() == "ARPU":
                return raw
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


def _parse_bi_slash_count(val: Any) -> int:
    """从核心产品每日数据表「人数/通过率」单元格取人数，如 `7 / 50.00%` → 7；无法解析 → 0。"""
    if val is None:
        return 0
    s = str(val).strip()
    if not s or s in ("-", "NaN", "nan", "N/A"):
        return 0
    left = s.split("/")[0].strip().replace(",", "")
    try:
        return int(round(float(left)))
    except ValueError:
        return 0


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


def _lark_date_cell(d: str, *, fallback: str = "") -> str:
    """写入 CSV 的日期列：统一 YYYY-MM-DD，供 Lark 日期字段解析。

    毫秒时间戳写入 CSV 易被 Excel 变成科学计数法或丢精度，同步后表现为 1970-01-01 或空。
    """
    for cand in (d, fallback):
        if not cand:
            continue
        iso = _parse_date_to_iso(cand)
        if iso:
            return iso[:10]
    s = (d or fallback or "").strip()
    return s[:10] if len(s) >= 10 and s[4] == "-" and s[7] == "-" else ""


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


def _refine_user_activity(conn: Any, output_dir: Path, t1: str, t0: str, t7: str, raw_dir: Path | None = None) -> list[Path]:
    """用户活跃：01 增幅+数值、02 周统计（含老用户）、03a/03b 渠道（03b 含老用户）、12 日活占比、13 新增设备。
    数据来源：stats_user_dau、stats_user_new，优先 raw/*.csv。"""
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
    increase_rows = [
        {"类型": "DAU", "增幅（%）": dau_pct, "数值": float(dau1)},
        {"类型": "DNU", "增幅（%）": dnu_pct, "数值": float(dnu1)},
    ]
    _write_csv(output_dir / "01_用户活跃_增幅表.csv", increase_rows, ["类型", "增幅（%）", "数值"])
    written.append(output_dir / "01_用户活跃_增幅表.csv")

    # 02 周统计 DAU/DNU 数量：合并两表按日期；老用户数量 = DAU - DNU
    by_date_02: dict[str, dict] = {}
    for d in all_dates[:7]:
        di = int(dau_by_date.get(d, 0))
        ni = int(dnu_by_date.get(d, 0))
        by_date_02[d] = {
            "日期": _lark_date_cell(d),
            "DAU数量": di,
            "DNU数量": ni,
            "老用户数量": max(0, di - ni),
        }
    daily_rows = [by_date_02[d] for d in sorted(by_date_02.keys())]
    if not daily_rows:
        daily_rows = [{"日期": _lark_date_cell(t1), "DAU数量": 0, "DNU数量": 0, "老用户数量": 0}]
    _write_csv(output_dir / "02_用户活跃_日期数量表.csv", daily_rows, ["日期", "DAU数量", "DNU数量", "老用户数量"])
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
    # Lark「DNU渠道来源」子表多为两列：DNU渠道来源、数量（无「老用户数量」列，写入第三列会 FieldNameNotFound）
    _write_csv(output_dir / "03a_用户活跃_DAU渠道来源.csv", dau_rows if dau_rows else [{"DAU渠道来源": "（需抓取 stats_user_dau/日活统计）", "数量": 0}], ["DAU渠道来源", "数量"])
    _write_csv(
        output_dir / "03b_用户活跃_DNU渠道来源.csv",
        dnu_rows if dnu_rows else [{"DNU渠道来源": "（需抓取 stats_user_new/日新用户统计）", "数量": 0}],
        ["DNU渠道来源", "数量"],
    )
    written.extend([output_dir / "03a_用户活跃_DAU渠道来源.csv", output_dir / "03b_用户活跃_DNU渠道来源.csv"])

    # 12 日活占比：固定两行「新用户」「老用户」，日活 = DNU 与 DAU-DNU（最新日 d1）
    nu_live = int(dnu_by_date.get(d1, 0)) if d1 else 0
    du_live = int(dau_by_date.get(d1, 0)) if d1 else 0
    old_live = max(0, du_live - nu_live)
    ratio_rows = [{"用户": "新用户", "日活": float(nu_live)}, {"用户": "老用户", "日活": float(old_live)}]
    _write_csv(output_dir / "12_用户活跃_日活占比.csv", ratio_rows, ["用户", "日活"])
    written.append(output_dir / "12_用户活跃_日活占比.csv")

    # 13 新增设备数：来自 stats_user_new（日日新用户统计），取 ALL 汇总行的日新设备数、DNU
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
    dev_table = [
        {
            "日期": _lark_date_cell(d1 if d1 else t1, fallback=t1),
            "新增设备数量": dev_val,
            "新增设备增幅（%）": dev_pct,
            "新增用户/新增设备": dnu_dev_ratio,
        }
    ]
    _write_csv(
        output_dir / "13_用户活跃_新增设备表.csv",
        dev_table,
        ["日期", "新增设备数量", "新增设备增幅（%）", "新增用户/新增设备"],
    )
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

    def _parse_paid_compare_pct(s: str) -> float | None:
        """解析 stats_retention_paid_compare（新增付费留存对比）的周环比变化。
        BI 展示：上空/中变化率/下上周基线。需取中间的变化率，非上周基线。
        主格为「-」「---」等表示无数据 → 返回 None（CSV 写「-」），勿臆造 -100。
        抓取串常为「-100%16.67%」：本周为空时 BI 仍可能算出 -100% 环比，页面中间为「-」；
        仅两节百分比且首节为 -100 时按无数据处理。"""
        s = str(s or "").strip()
        if not s or s in ("-", "—", "－", "---", "--", "N/A", "n/a"):
            return None
        if re.fullmatch(r"[\s\-—－]+", s):
            return None
        matches = re.findall(r"([+-]?\d+\.?\d*)\s*%", s)
        if not matches:
            return None
        try:
            vals = [float(x) for x in matches]

            def _is_neg100(x: float) -> bool:
                return abs(x + 100.0) < 0.02

            if len(vals) >= 3:
                # 本周约 0 且中间行为 -100：与「- / -100% / 基线」一致，按无数据
                if abs(vals[0]) < 1e-5 and _is_neg100(vals[1]):
                    return None
                return round(vals[1], 2)
            if len(vals) == 2 and _is_neg100(vals[0]):
                return None
            if len(vals) == 2:
                return round(vals[0], 2)
            return round(vals[0], 2)
        except ValueError:
            return None

    def _paid_wow_cell(v: float | None) -> str | float:
        """None →「-」与 BI 空展示一致；Lark 数字列同步时「-」在 atom_lark_bitable_sync 中省略字段留空。"""
        if v is None:
            return "-"
        return round(float(v), 2)

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
    # 07 付费用户周环比：来源 stats_retention_paid_compare（新增付费留存对比）第一行 统计范围=ALL 且 日期对比含~，T+1/T+2/T+3 周环比变化（取变化率；无数据为 None →「-」）
    paid_wow_t1: float | None = None
    paid_wow_t2: float | None = None
    paid_wow_t3: float | None = None
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
                num = _parse_paid_compare_pct(raw_val)
                if key == "t1":
                    paid_wow_t1 = num
                elif key == "t2":
                    paid_wow_t2 = num
                else:
                    paid_wow_t3 = num
    # 07 付费用户周环比：类型 T+1/T+2/T+3，Lark 表「付费用户周环比」固定三行
    paid_wow_rows = [
        {"类型": "T+1", "留存率": _paid_wow_cell(paid_wow_t1)},
        {"类型": "T+2", "留存率": _paid_wow_cell(paid_wow_t2)},
        {"类型": "T+3", "留存率": _paid_wow_cell(paid_wow_t3)},
    ]
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


def _refine_consumption_channel_gold(
    conn: Any, output_dir: Path, t1: str, t7: str, raw_dir: Path | None = None
) -> list[Path]:
    """
    当日金币产出、消耗（渠道层）→ 08b_消耗_金币_渠道层.csv
    数据来源：日活统计 stats_user_dau；取「最新数据日」（优先 t1）下各渠道行，排除汇总行。
    多维表侧通常无「日期」列，CSV 仅三列：渠道、金币产出、金币消耗（数据日 = 战报 t1，由同步批次隐含）。
    """
    q = lambda slug, df=None, dt=None: _query_table_or_raw(conn, raw_dir, slug, df, dt)
    dau = q("stats_user_dau", t7, None) or q("stats_user_dau", None, None)
    if not dau:
        return []
    dau = _filter_rows_to_single_date(dau, ["日期", "date", "统计日期"], t1)
    cols = list(dau[0].keys())
    ch_col = _find_col(cols, "渠道", "channel", "渠道来源")
    prod_c = _find_col(
        cols,
        "当日金币产出",
        "用户金币产出",
        "金币产出",
        "产出",
    )
    cons_c = _find_col(
        cols,
        "当日金币消耗",
        "当日今日消耗",
        "今日消耗",
        "用户金币消耗",
        "消耗",
    )
    skip_ch = (
        "全部汇总",
        "全平台",
        "ALL",
        "> ALL",
        ">ALL",
        "＞ ALL",
        "＞ALL",
        "全量合计",
        "总计",
        "合计",
        "当日总计",
        "",
    )
    out_rows: list[dict] = []
    for r in dau:
        ch = str(r.get(ch_col, "")).strip() if ch_col else ""
        if not ch or ch in skip_ch:
            continue
        pv = _safe_prod_cons(r.get(prod_c)) if prod_c else 0.0
        cv = _safe_prod_cons(r.get(cons_c)) if cons_c else 0.0
        out_rows.append(
            {
                "渠道": ch,
                "金币产出": round(pv, 2),
                "金币消耗": round(cv, 2),
            }
        )
    out_rows = sorted(out_rows, key=lambda x: -float(x["金币产出"]))
    if not out_rows:
        out_rows = [
            {
                "渠道": "（需抓取 stats_user_dau 日活统计并展开日期/渠道，且表中含当日金币产出/消耗列）",
                "金币产出": 0.0,
                "金币消耗": 0.0,
            }
        ]
    path = output_dir / "08b_消耗_金币_渠道层.csv"
    _write_csv(path, out_rows, ["渠道", "金币产出", "金币消耗"])
    return [path]


def _refine_consumption(conn: Any, output_dir: Path, t1: str, t7: str, raw_dir: Path | None = None) -> list[Path]:
    """消耗：08 当日金币产出/消耗（七列）、09 每个游戏的产出消耗。数据来源：prod_sales + stats_user_new（新用户金流）。"""
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
        written.extend(_refine_consumption_channel_gold(conn, output_dir, t1, t7, raw_dir=raw_dir))
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

    # 08 当日金币产出、消耗：prod_sales 全部汇总 + stats_user_new 汇总行的新用户产出/消耗；旧用户 = 全部 - 新用户
    by_date_daily: dict[str, dict] = {}
    for r in (rows_daily or []):
        d = _parse_date_to_iso(r.get(date_col, "")) if date_col else ""
        if not d:
            continue
        g = str(r.get(game_col_daily, "") or "").strip()
        is_total = g in ("全部汇总", "全量合计", "全平台汇总", "ALL", "> ALL")
        if d not in by_date_daily:
            by_date_daily[d] = {
                "日期": d,
                "全部产出": 0.0,
                "全部消耗": 0.0,
                "新用户金币产出": 0.0,
                "新用户金币消耗": 0.0,
                "_has_total_row": False,
            }
        if is_total:
            by_date_daily[d]["全部产出"] = _safe_prod_cons(r.get(prod_col_daily))
            by_date_daily[d]["全部消耗"] = _safe_prod_cons(r.get(cons_col_daily))
            by_date_daily[d]["_has_total_row"] = True
        elif not by_date_daily[d].get("_has_total_row"):
            by_date_daily[d]["全部产出"] += _safe_prod_cons(r.get(prod_col_daily))
            by_date_daily[d]["全部消耗"] += _safe_prod_cons(r.get(cons_col_daily))

    # 新用户金流：stats_user_new 汇总行（ALL/全部汇总）
    new_rows = q("stats_user_new", t7, None) or q("stats_user_new", None, None) or []
    if new_rows:
        ncols = list(new_rows[0].keys())
        ndate = _find_col(ncols, "日期", "date", "统计日期")
        nch = _find_col(ncols, "渠道", "channel")
        new_prod_c = _find_col(
            ncols,
            "当日新增金币产出",
            "当日新增用户产出",
            "新增用户产出",
            "新增用户金币产出",
            "当日新增产出",
        )
        new_cons_c = _find_col(
            ncols,
            "当日新增金币消耗",
            "当日新增用户消耗",
            "新增用户消耗",
            "新增用户金币消耗",
            "当日新增消耗",
        )
        # 日新用户统计汇总行：渠道多为 > ALL / ALL；无空格写法也需识别
        new_total_labels = ("ALL", "全部汇总", "全平台", "> ALL", ">ALL", "＞ ALL", "＞ALL")
        for r in new_rows:
            if nch and str(r.get(nch, "")).strip() not in new_total_labels:
                continue
            d = _parse_date_to_iso(r.get(ndate, "")) if ndate else ""
            if not d or d not in by_date_daily:
                continue
            if new_prod_c:
                by_date_daily[d]["新用户金币产出"] = _safe_prod_cons(r.get(new_prod_c))
            if new_cons_c:
                by_date_daily[d]["新用户金币消耗"] = _safe_prod_cons(r.get(new_cons_c))

    daily_rows = []
    dates_sorted = sorted([d for d in by_date_daily if d])[-7:]
    for d in dates_sorted:
        v = by_date_daily[d]
        v.pop("_has_total_row", None)
        tp, tc = float(v["全部产出"]), float(v["全部消耗"])
        np, nc = float(v["新用户金币产出"]), float(v["新用户金币消耗"])
        daily_rows.append(
            {
                "日期": _lark_date_cell(d),
                "全部产出": round(tp, 2),
                "全部消耗": round(tc, 2),
                "新用户金币产出": round(np, 2),
                "新用户金币消耗": round(nc, 2),
                "旧用户金币产出": round(max(0.0, tp - np), 2),
                "旧用户金币消耗": round(max(0.0, tc - nc), 2),
            }
        )
    if not daily_rows:
        z = _lark_date_cell(t1)
        daily_rows = [
            {
                "日期": z,
                "全部产出": 0.0,
                "全部消耗": 0.0,
                "新用户金币产出": 0.0,
                "新用户金币消耗": 0.0,
                "旧用户金币产出": 0.0,
                "旧用户金币消耗": 0.0,
            }
        ]
    _write_csv(
        output_dir / "08_消耗_每日表.csv",
        daily_rows,
        [
            "日期",
            "全部产出",
            "全部消耗",
            "新用户金币产出",
            "新用户金币消耗",
            "旧用户金币产出",
            "旧用户金币消耗",
        ],
    )
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
    written.extend(_refine_consumption_channel_gold(conn, output_dir, t1, t7, raw_dir=raw_dir))
    return written


def _refine_daily_metrics(conn: Any, output_dir: Path, t1: str, t0: str, t7: str, raw_dir: Path | None = None) -> list[Path]:
    """14 付费人数金额增幅（stats_recharge / daily_ops）；15/16 Arpu、Arppu 表（优先 daily_ops_summary：Arpu=付费总金额/活跃玩家总数，否则读列或 stats_recharge）。"""
    written: list[Path] = []
    q = lambda slug, df=None, dt=None: _query_table_or_raw(conn, raw_dir, slug, df, dt)

    def _pct(cur: float, prev: float) -> float:
        return round((cur - prev) / prev * 100, 2) if prev else 0.0

    rows = q("stats_recharge", t0, t1)
    if not rows:
        rows = q("daily_ops_summary", t0, t1)
    if not rows:
        _write_csv(output_dir / "14_充值_付费人数金额增幅表.csv", [{"当天付费人数": 0, "付费总金额": 0.0, "增幅": 0.0}], ["当天付费人数", "付费总金额", "增幅"])
        zd = _lark_date_cell(t1)
        _write_csv(
            output_dir / "15_消耗_Arup表.csv",
            [{"日期": zd, "Arpu数值": 0.0, "Arpu增幅（%）": 0.0}],
            ["日期", "Arpu数值", "Arpu增幅（%）"],
        )
        _write_csv(
            output_dir / "16_消耗_Arppu表.csv",
            [{"日期": zd, "Arppu数值": 0.0, "增幅（%）": 0.0}],
            ["日期", "Arppu数值", "增幅（%）"],
        )
        return [
            output_dir / "14_充值_付费人数金额增幅表.csv",
            output_dir / "15_消耗_Arup表.csv",
            output_dir / "16_消耗_Arppu表.csv",
        ]

    cols = list(rows[0].keys())
    date_col = _find_col(cols, "日期", "date", "统计日期") or "_ingested_date"
    paid_count_col = _find_col(cols, "充值人数", "付费人数", "付费", "人数")
    paid_amt_col = _find_col(cols, "当日充值总额", "付费总金额", "充值", "金额", "总金额")
    arpu_col = _find_col_arpu_or_arppu(cols, want_arppu=False)
    arppu_col = _find_col_arpu_or_arppu(cols, want_arppu=True)
    ch_col_rec = _find_col(cols, "渠道", "channel")
    rec_total_labels = ("ALL", "全部汇总", "全平台", "> ALL")
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

    pc1, pc0 = _safe_float(r1.get(paid_count_col)), _safe_float(r0.get(paid_count_col))
    pa1, pa0 = _safe_float(r1.get(paid_amt_col)), _safe_float(r0.get(paid_amt_col))
    paid_pct = _pct(pa1, pa0)
    row14 = [{"当天付费人数": int(pc1), "付费总金额": round(pa1, 2), "增幅": paid_pct}]
    _write_csv(output_dir / "14_充值_付费人数金额增幅表.csv", row14, ["当天付费人数", "付费总金额", "增幅"])
    written.append(output_dir / "14_充值_付费人数金额增幅表.csv")

    # Arpu / Arppu：优先 daily_ops_summary（平台数据 → 日常报表 → 每日运营数据汇总）
    ops = q("daily_ops_summary", t7, t1) or q("daily_ops_summary", None, None) or []
    arpu1, arpu0, arppu1, arppu0 = 0.0, 0.0, 0.0, 0.0
    d1_iso, d0_iso = pick_d1, pick_d0
    used_ops_arpu, used_ops_arppu = False, False
    if ops:
        ocols = list(ops[0].keys())
        odate = _find_col(ocols, "日期", "date", "统计日期", "业务日期")
        och = _find_col(ocols, "渠道", "channel", "统计范围")
        o_total = ("ALL", "全部汇总", "全平台", "> ALL", ">ALL", "＞ ALL", "＞ALL", "全部")

        def _ops_row_is_platform_total(r: dict) -> bool:
            if not och:
                return True
            chv = str(r.get(och, "") or "").strip()
            if not chv:
                return True
            if chv in o_total:
                return True
            return chv.replace(" ", "").replace("＞", ">").upper() in (">ALL", "ALL")

        filtered = [r for r in ops if _ops_row_is_platform_total(r)]
        if not filtered:
            filtered = ops
        oby: dict[str, dict] = {}
        for r in filtered:
            dk = _parse_date_to_iso(r.get(odate, "")) if odate else ""
            if not dk:
                continue
            prev = oby.get(dk)
            if prev is None:
                oby[dk] = r
            elif _ops_row_is_platform_total(r) and not _ops_row_is_platform_total(prev):
                oby[dk] = r
        o_dates = sorted([d for d in oby if d], reverse=True)
        od1 = t1 if t1 in oby else (o_dates[0] if o_dates else "")
        od0 = ""
        if od1 and od1 in o_dates:
            i = o_dates.index(od1) + 1
            od0 = o_dates[i] if i < len(o_dates) else ""
        or1, or0 = oby.get(od1, {}), oby.get(od0, {}) if od0 else {}
        o_arpu = _find_col_arpu_or_arppu(ocols, want_arppu=False)
        o_arppu = _find_col_arpu_or_arppu(ocols, want_arppu=True)
        o_pc = _find_col(ocols, "充值人数", "付费人数", "人数")
        o_pa = _find_col(ocols, "当日充值总额", "付费总金额", "充值总额", "金额")
        # 每日运营汇总：Arpu = 付费总金额 / 活跃玩家总数（与 Arppu=付费总金额/付费人数 并列；勿仅读 Arpu 列以免误列/空值得到 0）
        o_active = _find_col(
            ocols,
            "活跃玩家总数",
            "活跃玩家",
            "活跃用户数",
            "活跃用户",
            "平台活跃",
            "日活用户数",
            "DAU",
            "日活",
        )
        if o_pa and o_active:
            oac1, oac0 = _safe_float(or1.get(o_active)), _safe_float(or0.get(o_active)) if or0 else 0.0
            opa1v, opa0v = _safe_float(or1.get(o_pa)), _safe_float(or0.get(o_pa)) if or0 else 0.0
            arpu1 = opa1v / oac1 if oac1 else 0.0
            arpu0 = opa0v / oac0 if oac0 else 0.0
            used_ops_arpu = True
        elif o_arpu:
            arpu1, arpu0 = _safe_float(or1.get(o_arpu)), _safe_float(or0.get(o_arpu))
            used_ops_arpu = True
        if o_arppu:
            arppu1, arppu0 = _safe_float(or1.get(o_arppu)), _safe_float(or0.get(o_arppu))
            used_ops_arppu = True
        elif o_pc and o_pa:
            opc1, opc0 = _safe_float(or1.get(o_pc)), _safe_float(or0.get(o_pc))
            opa1, opa0 = _safe_float(or1.get(o_pa)), _safe_float(or0.get(o_pa))
            arppu1 = opa1 / opc1 if opc1 else 0.0
            arppu0 = opa0 / opc0 if opc0 else 0.0
            used_ops_arppu = True
        d1_iso, d0_iso = od1 or pick_d1, od0 or pick_d0
    if not used_ops_arpu:
        arpu1, arpu0 = _safe_float(r1.get(arpu_col)), _safe_float(r0.get(arpu_col))
    if not used_ops_arppu:
        arppu1 = pa1 / pc1 if pc1 else 0.0
        arppu0 = pa0 / pc0 if pc0 else 0.0

    arpu_pct = _pct(arpu1, arpu0)
    arppu_pct = _pct(arppu1, arppu0)
    ts1 = _lark_date_cell(d1_iso or t1, fallback=t1)
    row15 = [{"日期": ts1, "Arpu数值": round(arpu1, 2), "Arpu增幅（%）": arpu_pct}]
    _write_csv(output_dir / "15_消耗_Arup表.csv", row15, ["日期", "Arpu数值", "Arpu增幅（%）"])
    written.append(output_dir / "15_消耗_Arup表.csv")
    row16 = [{"日期": ts1, "Arppu数值": round(arppu1, 2), "增幅（%）": arppu_pct}]
    _write_csv(output_dir / "16_消耗_Arppu表.csv", row16, ["日期", "Arppu数值", "增幅（%）"])
    written.append(output_dir / "16_消耗_Arppu表.csv")

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
    range_col = _find_col(
        cols,
        "统计范围",
        "不同充值金额分等级",
        "不同充值金分等级",
        "充值金额档位",
        "渠道",
        "金额档位",
        "等级",
    )
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
    tier_col = _find_col(
        cols,
        "不同充值金额分等级",
        "不同充值金分等级",
        "统计范围",
        "金额档位",
        "充值金额档位",
        "SKU",
    )
    cnt_col = _find_col(cols, "充值人数", "人数", "付费人数", "充值次数", "订单数", "笔数")
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


def _recharge_amount_by_tier_label_for_date(
    conn: Any,
    raw_dir: Path | None,
    date_iso: str,
    days: int,
    q: Any,
) -> dict[str, float]:
    """给定数据日 YYYY-MM-DD，返回各充值档位（与提纯相同的格式化标签）-> 此等级总金额合计。
    数据源优先级与 _refine_recharge 一致，用于按档位计算「较前一日该档位金额」增幅。"""
    date_iso = date_iso[:10]
    dt = datetime.strptime(date_iso, "%Y-%m-%d")
    date_from = (dt - timedelta(days=days)).strftime("%Y-%m-%d")
    sku_key = "不同充值金额分等级"

    tier_rows = _try_tier_rows_from_stats_recharge_raw(raw_dir, date_iso)
    if tier_rows:
        m: dict[str, float] = {}
        for r in tier_rows:
            k = str(r.get(sku_key, ""))
            m[k] = m.get(k, 0.0) + _safe_float(r.get("此等级总金额", 0))
        return m

    rows_from_status = _load_recharge_status_latest(raw_dir, date_from, target_date=date_iso)
    if rows_from_status:
        cols = list(rows_from_status[0].keys())
        range_col = _find_col(
            cols,
            "统计范围",
            "不同充值金额分等级",
            "不同充值金分等级",
            "充值金额档位",
            "渠道",
            "金额档位",
            "等级",
        )
        count_col = _find_col(cols, "充值人数", "充值次数", "人数", "付费人数", "订单数")
        amt_col = _find_col(cols, "充值金额", "金额", "档位金额", "此等级总金额", "当日充值总额")
        if range_col and count_col:
            m = {}
            for r in rows_from_status:
                label = str(r.get(range_col, "")).strip() or "（未知）"
                kk = _format_recharge_tier_label(label)
                cnt = int(_safe_float(r.get(count_col, 0)))
                amt_val = _safe_float(r.get(amt_col, 0))
                total_amt = amt_val if amt_val > 0 else (cnt * _parse_tier_amount(label))
                m[kk] = m.get(kk, 0.0) + total_amt
            return m

    ag = _aggregate_recharge_from_recharge_daily(conn, date_iso, date_iso)
    if ag:
        m = {}
        for r in ag:
            k = str(r.get(sku_key, ""))
            m[k] = m.get(k, 0.0) + _safe_float(r.get("此等级总金额", 0))
        if m:
            return m

    ag2 = _aggregate_recharge_from_detail(conn, date_iso, date_iso)
    if ag2:
        m = {}
        for r in ag2:
            k = str(r.get(sku_key, ""))
            m[k] = m.get(k, 0.0) + _safe_float(r.get("此等级总金额", 0))
        if m:
            return m

    for slug in ("recharge_status", "stats_recharge", "recharge_daily", "recharge_history"):
        raw_rows = q(slug, date_from, date_iso)
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
                    if eff == date_iso:
                        filtered.append(r)
                raw_rows = filtered
        sku_col = _find_col(
            cols,
            "充值金额",
            "SKU",
            "渠道",
            "金额档位",
            "等级",
            "不同充值金额分等级",
            "amount",
            "price",
            "档位",
            "金额等级",
        )
        count_col = _find_col(cols, "人数", "付费人数", "用户数", "count", "充值人数")
        amount_col = _find_col(cols, "总金额", "金额", "此等级总金额", "amount", "当日充值总额")
        if not (sku_col and count_col and amount_col):
            continue
        vals = set(str(r.get(sku_col, "")).strip().upper() for r in raw_rows)
        if vals <= {"ALL", ""} or (len(vals) == 1 and "ALL" in vals):
            continue
        by_sku: dict[str, float] = {}
        for r in raw_rows:
            raw_sku = str(r.get(sku_col, ""))
            k = _format_recharge_tier_label(raw_sku)
            by_sku[k] = by_sku.get(k, 0.0) + _safe_float(r.get(amount_col))
        return by_sku

    return {}


def _refine_recharge(
    conn: Any, output_dir: Path, t1: str, days: int = 7, raw_dir: Path | None = None, t_prev: str | None = None
) -> list[Path]:
    """10 付费人数按SKU、11 付费金额按SKU（11 表「付费金额增幅（%）」= 各档位较前一日该档位金额的增长率）。数据来源：recharge_status / stats_recharge。人数=充值次数。"""
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
            range_col = _find_col(
                cols,
                "统计范围",
                "不同充值金额分等级",
                "不同充值金分等级",
                "充值金额档位",
                "渠道",
                "金额档位",
                "等级",
            )
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
        range_col = _find_col(
            cols,
            "统计范围",
            "不同充值金额分等级",
            "不同充值金分等级",
            "充值金额档位",
            "渠道",
            "金额档位",
            "等级",
        )
        count_col = _find_col(cols, "充值人数", "充值次数", "人数", "付费人数", "订单数")
        amt_col = _find_col(cols, "充值金额", "金额", "档位金额", "此等级总金额", "当日充值总额")
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
            sku_col = _find_col(
                cols,
                "充值金额",
                "SKU",
                "渠道",
                "金额档位",
                "等级",
                "不同充值金额分等级",
                "不同充值金分等级",
            )
            count_col = _find_col(cols, "充值人数", "人数", "付费人数", "充值次数", "订单数")
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
        _write_csv(
            output_dir / "11_充值_付费金额按SKU.csv",
            [{"不同充值金额分等级": "（需抓取充值数据）", "此等级总金额": 0.0, "付费金额增幅（%）": 0.0}],
            ["不同充值金额分等级", "此等级总金额", "付费金额增幅（%）"],
        )
        return [output_dir / "10_充值_付费人数按SKU.csv", output_dir / "11_充值_付费金额按SKU.csv"]

    cols = list(rows[0].keys())
    sku_col = _find_col(
        cols,
        "充值金额",
        "SKU",
        "金额档位",
        "等级",
        "不同充值金额",
        "不同充值金额分等级",
        "不同充值金分等级",
        "amount",
        "price",
        "档位",
        "金额等级",
        "渠道",
    )
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

    prev_key = (t_prev or "").strip()[:10]
    if not prev_key:
        prev_key = (datetime.strptime(t1[:10], "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    prev_by_label = _recharge_amount_by_tier_label_for_date(conn, raw_dir, prev_key, days, q)

    def _tier_amt_wow_pct(amt_today: float, label: str) -> float:
        prev_amt = prev_by_label.get(label, 0.0)
        if prev_amt > 0:
            return round((amt_today - prev_amt) / prev_amt * 100, 2)
        return 0.0

    p10, p11 = output_dir / "10_充值_付费人数按SKU.csv", output_dir / "11_充值_付费金额按SKU.csv"
    try:
        _write_csv(p10, [{SKU_COL: _lark_safe_text(str(r[SKU_COL])), "人数": r["人数"]} for r in out_rows], [SKU_COL, "人数"])
        written.append(p10)
    except PermissionError as e:
        logger.warning("[Refiner] 无法写入 %s（可能被占用）: %s", p10.name, e)
    try:
        _write_csv(
            p11,
            [
                {
                    SKU_COL: _lark_safe_text(str(r[SKU_COL])),
                    "此等级总金额": round(r["此等级总金额"], 2),
                    "付费金额增幅（%）": _tier_amt_wow_pct(float(r["此等级总金额"]), str(r[SKU_COL])),
                }
                for r in out_rows
            ],
            [SKU_COL, "此等级总金额", "付费金额增幅（%）"],
        )
        written.append(p11)
    except PermissionError as e:
        logger.warning("[Refiner] 无法写入 %s（可能被占用）: %s", p11.name, e)
    return written


# 游戏深度参与情况：与 Lark 子表「游戏深度参与情况」列名一致（首列游戏类型 + 19 指标 = 20 列）
# 仅同步下列两款（与核心产品每日数据表「游戏名称」逐字匹配）；其它游戏不写入该表
_GAME_DEEP_ENGAGEMENT_GAMES: tuple[str, ...] = ("Tongits King", "Color Blitz Social")
_GAME_DEEP_ENGAGEMENT_GAMES_SET: frozenset[str] = frozenset(_GAME_DEEP_ENGAGEMENT_GAMES)

_GAME_DEEP_ENGAGEMENT_COLS: list[str] = [
    "游戏类型",
    "进入游戏房间用户数（全平台）",
    "真实进房总人数（全平台）",
    "完成游戏用户数（全平台）",
    "3局通过人数（全平台）",
    "6局通过人数（全平台）",
    "进入游戏房间用户数（老用户）",
    "真实进房总人数（老用户）",
    "完成游戏用户数（老用户）",
    "3局通过人数（老用户）",
    "6局通过人数（老用户）",
    "进入游戏房间用户数（新用户）",
    "真实进房总人数（新用户）",
    "完成游戏用户数（新用户）",
    "1局通过人数（新用户）",
    "2局通过人数（新用户）",
    "3局通过人数（新用户）",
    "4局通过人数（新用户）",
    "5局通过人数（新用户）",
    "6局通过人数（新用户）",
]

# 漏斗图新开：与多维表「漏斗图新开」下六子表列名一致（类型 + 单列游戏数值）；数据与 20_ 同源（stats_game_core 最新日）
_FUNNEL_METRIC_KEYS_ALL: tuple[str, ...] = (
    "进入游戏房间用户数（全平台）",
    "真实进房总人数（全平台）",
    "完成游戏用户数（全平台）",
    "3局通过人数（全平台）",
    "6局通过人数（全平台）",
)
_FUNNEL_METRIC_KEYS_OLD: tuple[str, ...] = (
    "进入游戏房间用户数（老用户）",
    "真实进房总人数（老用户）",
    "完成游戏用户数（老用户）",
    "3局通过人数（老用户）",
    "6局通过人数（老用户）",
)
_FUNNEL_METRIC_KEYS_NEW: tuple[str, ...] = (
    "进入游戏房间用户数（新用户）",
    "真实进房总人数（新用户）",
    "完成游戏用户数（新用户）",
    "1局通过人数（新用户）",
    "2局通过人数（新用户）",
    "3局通过人数（新用户）",
    "4局通过人数（新用户）",
    "5局通过人数（新用户）",
    "6局通过人数（新用户）",
)


def _deep_metric_int(deep: dict[str, Any], key: str) -> int:
    v = deep.get(key)
    if v is None:
        return 0
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return 0


def _write_game_funnel_facet_csvs(output_dir: Path, deep_by_name: dict[str, dict[str, Any]]) -> list[Path]:
    """输出「漏斗图新开」六子表 CSV：行=固定「类型」文案，列=对应游戏单列数值。"""
    specs: list[tuple[str, str, str, tuple[str, ...]]] = [
        ("21_漏斗_Tongits_King_全平台.csv", "Tongits King", "Tongits King", _FUNNEL_METRIC_KEYS_ALL),
        ("22_漏斗_Tongits_King_老用户.csv", "Tongits King", "Tongits King", _FUNNEL_METRIC_KEYS_OLD),
        ("23_漏斗_Tongits_King_新用户.csv", "Tongits King", "Tongits King", _FUNNEL_METRIC_KEYS_NEW),
        ("24_漏斗_Color_Blitz_Social_全平台.csv", "Color Blitz Social", "Color Blitz Social", _FUNNEL_METRIC_KEYS_ALL),
        ("25_漏斗_Color_Blitz_Social_老用户.csv", "Color Blitz Social", "Color Blitz Social", _FUNNEL_METRIC_KEYS_OLD),
        ("26_漏斗_Color_Blitz_Social_新用户.csv", "Color Blitz Social", "Color Blitz Social", _FUNNEL_METRIC_KEYS_NEW),
    ]
    paths: list[Path] = []
    for fname, game_name, val_col, keys in specs:
        deep = deep_by_name.get(game_name) or {}
        rows = [{"类型": k, val_col: _deep_metric_int(deep, k)} for k in keys]
        _write_csv(output_dir / fname, rows, ["类型", val_col])
        paths.append(output_dir / fname)
    return paths


def _refine_game_situation(conn: Any, output_dir: Path, t1: str, t0: str, raw_dir: Path | None = None) -> list[Path]:
    """17/18/19/20 及 21～26 游戏情况：stats_game_core（核心产品每日数据表）、stats_game_daily（每日游戏数据）。
    17 表新/老局数优先取「新用户参与总局次数」「老用户参与总局次数」，人均取「全用户平均局数」。
    20 表：进房/真实进房/完成/局通过（全平台、老、新）来自同源列；「人数/通过率」列只提纯人数；
    全平台 3/6 局 = 新用户对应局通过人数 + 老用户对应局通过人数。仅输出 `_GAME_DEEP_ENGAGEMENT_GAMES` 两款，固定两行顺序。
    21～26：漏斗图新开六子表，行=固定「类型」、列=Tongits King 或 Color Blitz Social，指标与 20_ 宽表列名一一对应。"""
    written: list[Path] = []
    q = lambda slug, df=None, dt=None: _query_table_or_raw(conn, raw_dir, slug, df, dt)
    skip_g = ("ALL", "全部汇总", "全平台", "> ALL", "全量合计", "总计", "合计", "奖励", "赠送", "游戏输赢", "兑换")

    core = q("stats_game_core", t0, t1)
    core = _filter_rows_to_single_date(core or [], ["日期", "date", "统计日期", "业务日期"], t1)
    out17: list[dict] = []
    out18: list[dict] = []
    deep_by_name: dict[str, dict[str, Any]] = {}
    if core:
        ccols = list(core[0].keys())
        game_col = _find_col(ccols, "统计范围", "游戏类型", "游戏名称", "游戏", "产品")
        rounds_c = _find_col(ccols, "完成游戏局数", "游戏局数", "总局数", "完成局数", "局数")
        users_c = _find_col(ccols, "完成游戏用户数", "完成用户数", "游戏用户数", "完成用户")
        legacy_total = _find_col(ccols, "全部用户局数", "完成游戏用户数")
        # 核心产品每日数据表：新/老为「参与总局次数」非「完成游戏新/老用户数」；均值为「全用户平均局数」
        new_g = _find_col(
            ccols,
            "新用户参与总局次数",
            "完成游戏新用户数",
            "新用户局数",
            "完成游戏新用户",
            "新用户完成局数",
        )
        old_g = _find_col(
            ccols,
            "老用户参与总局次数",
            "完成游戏老用户数",
            "老用户局数",
            "完成游戏老用户",
            "老用户完成局数",
        )
        avg_g = _find_col(
            ccols,
            "全用户平均局数",
            "人均游戏局数",
            "人均局数",
            "人均完成局数",
            "平均游戏局数",
            "局均",
        )
        w_new = _find_col(ccols, "新用户胜利数", "新用户胜")
        w_old = _find_col(ccols, "老用户胜利数", "老用户胜")
        # —— 20 游戏深度参与情况（与 Lark「游戏深度参与情况」列名一致）——
        name_cn = _find_col(ccols, "游戏名称", "游戏")
        enter_room = _find_col(ccols, "进入游戏房间用户数")
        enter_old_u = _find_col(ccols, "进入游戏房间老用户数", "进入游戏房间老用户")
        enter_new_u = _find_col(ccols, "进入游戏房间新用户数", "进入游戏房间新用户")
        real_all_c = _find_col(ccols, "真实进房总人数")
        real_new_c = _find_col(ccols, "新用户真实进房总人数")
        real_old_c = _find_col(ccols, "老用户真实进房总人数")
        done_new_u = _find_col(ccols, "完成游戏新用户数", "完成游戏新用户")
        done_old_u = _find_col(ccols, "完成游戏老用户数", "完成游戏老用户")
        sn_round: dict[int, str | None] = {}
        for k in range(1, 7):
            sn_round[k] = _find_col(ccols, f"新用户{k}局通过人数/通过率", f"新用户{k}局通过人数")
        so3_c = _find_col(ccols, "老用户3局通过人数/通过率", "老用户3局通过人数")
        so6_c = _find_col(ccols, "老用户6局通过人数/通过率", "老用户6局通过人数")
        pcn = _parse_bi_slash_count
        for r in core:
            g = str(r.get(game_col, "")).strip() if game_col else ""
            if not g or g in skip_g:
                continue
            rr = int(round(_safe_float(r.get(rounds_c)))) if rounds_c else 0
            uu = int(round(_safe_float(r.get(users_c)))) if users_c else 0
            tg = rr if rr else (uu if users_c else 0)
            if not tg and legacy_total:
                tg = int(round(_safe_float(r.get(legacy_total))))
            ng = int(round(_safe_float(r.get(new_g)))) if new_g else 0
            og = int(round(_safe_float(r.get(old_g)))) if old_g else 0
            if not tg and (ng + og) > 0:
                tg = ng + og
            av = _safe_float(r.get(avg_g)) if avg_g else 0.0
            if (not av or av == 0.0) and rounds_c and users_c:
                rrv = _safe_float(r.get(rounds_c))
                uuv = _safe_float(r.get(users_c))
                if uuv:
                    av = rrv / uuv
            out17.append(
                {
                    "游戏类型": g,
                    "全部用户局数": tg,
                    "新用户局数": ng,
                    "老用户局数": og,
                    "人均游戏局数": round(av, 4) if av else 0.0,
                }
            )
            wn = int(_safe_float(r.get(w_new))) if w_new else 0
            wo = int(_safe_float(r.get(w_old))) if w_old else 0
            wr = round((wn + wo) / tg * 100, 2) if tg else 0.0
            out18.append(
                {
                    "游戏类型": g,
                    "老用户胜利数": wo,
                    "新用户胜利数": wn,
                    "总胜率（%）": wr,
                }
            )
            gnm = (str(r.get(name_cn, "")).strip() if name_cn else "") or g
            er = int(round(_safe_float(r.get(enter_room)))) if enter_room else 0
            eold = int(round(_safe_float(r.get(enter_old_u)))) if enter_old_u else 0
            enew = int(round(_safe_float(r.get(enter_new_u)))) if enter_new_u else 0
            ra = int(round(_safe_float(r.get(real_all_c)))) if real_all_c else 0
            rn = int(round(_safe_float(r.get(real_new_c)))) if real_new_c else 0
            ro = int(round(_safe_float(r.get(real_old_c)))) if real_old_c else 0
            du = int(round(_safe_float(r.get(users_c)))) if users_c else 0
            d_new = int(round(_safe_float(r.get(done_new_u)))) if done_new_u else 0
            d_old = int(round(_safe_float(r.get(done_old_u)))) if done_old_u else 0
            n3 = pcn(r.get(sn_round.get(3))) if sn_round.get(3) else 0
            n6 = pcn(r.get(sn_round.get(6))) if sn_round.get(6) else 0
            o3 = pcn(r.get(so3_c)) if so3_c else 0
            o6 = pcn(r.get(so6_c)) if so6_c else 0
            if gnm in _GAME_DEEP_ENGAGEMENT_GAMES_SET:
                deep_by_name[gnm] = {
                    "游戏类型": gnm,
                    "进入游戏房间用户数（全平台）": er,
                    "真实进房总人数（全平台）": ra,
                    "完成游戏用户数（全平台）": du,
                    "3局通过人数（全平台）": n3 + o3,
                    "6局通过人数（全平台）": n6 + o6,
                    "进入游戏房间用户数（老用户）": eold,
                    "真实进房总人数（老用户）": ro,
                    "完成游戏用户数（老用户）": d_old,
                    "3局通过人数（老用户）": o3,
                    "6局通过人数（老用户）": o6,
                    "进入游戏房间用户数（新用户）": enew,
                    "真实进房总人数（新用户）": rn,
                    "完成游戏用户数（新用户）": d_new,
                    "1局通过人数（新用户）": pcn(r.get(sn_round.get(1))) if sn_round.get(1) else 0,
                    "2局通过人数（新用户）": pcn(r.get(sn_round.get(2))) if sn_round.get(2) else 0,
                    "3局通过人数（新用户）": n3,
                    "4局通过人数（新用户）": pcn(r.get(sn_round.get(4))) if sn_round.get(4) else 0,
                    "5局通过人数（新用户）": pcn(r.get(sn_round.get(5))) if sn_round.get(5) else 0,
                    "6局通过人数（新用户）": n6,
                }

    if not out17:
        out17 = [{"游戏类型": "（需抓取 stats_game_core 核心产品每日数据表）", "全部用户局数": 0, "新用户局数": 0, "老用户局数": 0, "人均游戏局数": 0.0}]
    if not out18:
        out18 = [{"游戏类型": "（需抓取 stats_game_core）", "老用户胜利数": 0, "新用户胜利数": 0, "总胜率（%）": 0.0}]
    out20 = [
        deep_by_name.get(gt)
        or {k: (0 if k != "游戏类型" else gt) for k in _GAME_DEEP_ENGAGEMENT_COLS}
        for gt in _GAME_DEEP_ENGAGEMENT_GAMES
    ]
    _write_csv(
        output_dir / "17_游戏_完成局数.csv",
        out17,
        ["游戏类型", "全部用户局数", "新用户局数", "老用户局数", "人均游戏局数"],
    )
    written.append(output_dir / "17_游戏_完成局数.csv")
    _write_csv(
        output_dir / "18_游戏_用户获胜.csv",
        out18,
        ["游戏类型", "老用户胜利数", "新用户胜利数", "总胜率（%）"],
    )
    written.append(output_dir / "18_游戏_用户获胜.csv")

    gday = q("stats_game_daily", t0, t1)
    gday = _filter_rows_to_single_date(gday or [], ["日期", "date", "统计日期", "业务日期"], t1)
    out19: list[dict] = []
    if gday:
        gcols = list(gday[0].keys())
        ggc = _find_col(gcols, "统计范围", "游戏类型", "游戏名称", "游戏")
        rtp_c = _find_col(gcols, "回报率RTP", "回报率", "RTP", "rtp", "RTP(%)")
        ggr_c = _find_col(gcols, "GGR", "ggr")
        total19: dict | None = None
        for r in gday:
            g = str(r.get(ggc, "")).strip() if ggc else ""
            if _is_stats_game_daily_total_scope(g):
                total19 = {
                    "游戏类型": g,
                    "RTP(%)": round(_safe_float(r.get(rtp_c)), 4) if rtp_c else 0.0,
                    "GGR": round(_safe_float(r.get(ggr_c)), 2) if ggr_c else 0.0,
                }
                break
        for r in gday:
            g = str(r.get(ggc, "")).strip() if ggc else ""
            if _is_stats_game_daily_total_scope(g):
                continue
            if not g or g in skip_g:
                continue
            out19.append(
                {
                    "游戏类型": g,
                    "RTP(%)": round(_safe_float(r.get(rtp_c)), 4) if rtp_c else 0.0,
                    "GGR": round(_safe_float(r.get(ggr_c)), 2) if ggr_c else 0.0,
                }
            )
        if total19:
            out19.insert(0, total19)
    if not out19:
        out19 = [{"游戏类型": "（需抓取 stats_game_daily）", "RTP(%)": 0.0, "GGR": 0.0}]
    _write_csv(output_dir / "19_游戏_RTP_GGR.csv", out19, ["游戏类型", "RTP(%)", "GGR"])
    written.append(output_dir / "19_游戏_RTP_GGR.csv")

    _write_csv(output_dir / "20_游戏_游戏深度参与情况.csv", out20, _GAME_DEEP_ENGAGEMENT_COLS)
    written.append(output_dir / "20_游戏_游戏深度参与情况.csv")
    written.extend(_write_game_funnel_facet_csvs(output_dir, deep_by_name))

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
        written += _refine_daily_metrics(conn, out, t1, t0, t7, raw_dir=raw)
        written += _refine_recharge(conn, out, t1, days=7, raw_dir=raw, t_prev=t0)
        written += _refine_game_situation(conn, out, t1, t0, raw_dir=raw)
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
        plugin_root = get_app_root() / "skills_repo" / "plugin" / "com.jachin.hr.recruitment"
        if plugin_root.exists() and str(plugin_root) not in sys.path:
            sys.path.insert(0, str(plugin_root))
        from tools.atom_lark_bitable_sync import sync_csv_to_bitable  # type: ignore[import-untyped]
    except ImportError as e:
        return (0, [f"无法导入 atom_lark_bitable_sync: {e}"], [])

    ok_count = 0
    errors: list[str] = []
    skipped: list[tuple[str, str]] = []
    default_text_columns = {
        "10_充值_付费人数按SKU.csv": ["不同充值金额分等级"],
        "11_充值_付费金额按SKU.csv": ["不同充值金额分等级"],
        "12_用户活跃_日活占比.csv": ["用户"],
        "17_游戏_完成局数.csv": ["游戏类型"],
        "18_游戏_用户获胜.csv": ["游戏类型"],
        "19_游戏_RTP_GGR.csv": ["游戏类型"],
        "20_游戏_游戏深度参与情况.csv": ["游戏类型"],
        "21_漏斗_Tongits_King_全平台.csv": ["类型"],
        "22_漏斗_Tongits_King_老用户.csv": ["类型"],
        "23_漏斗_Tongits_King_新用户.csv": ["类型"],
        "24_漏斗_Color_Blitz_Social_全平台.csv": ["类型"],
        "25_漏斗_Color_Blitz_Social_老用户.csv": ["类型"],
        "26_漏斗_Color_Blitz_Social_新用户.csv": ["类型"],
        "08b_消耗_金币_渠道层.csv": ["渠道"],
        # 07「留存率」无数据时 CSV 为「-」；同步层对数字列会跳过「-」留空。勿把整列放 text_columns。
    }
    text_cols_per_table = lark_bitable_config.get("text_columns") or {}
    field_mapping_per_table = dict(lark_bitable_config.get("field_mapping") or {})
    # 01 表列名已与 Lark 统一为「增幅（%）」；06 表为「留存率（%）」。若 Lark 表用其他字段名，可在 field_mapping 中配置
    # 10/11 若 Lark 列名与 CSV 不一致（如「不同充值金分等级」），须在 bi_daily_report.yaml 的 field_mapping 中按 CSV→Lark 配置

    replace_table_default = lark_bitable_config.get("replace_table", False)
    # replace_tables 为 [] 时原先会变成 set()，导致整表 do_replace=False、仅追加记录，多维表旧行不刷新
    _rt_raw = lark_bitable_config.get("replace_tables")
    if _rt_raw is None or (isinstance(_rt_raw, (list, tuple)) and len(_rt_raw) == 0):
        replace_tables = None
    else:
        replace_tables = set(_rt_raw)
    # 充值分档两表必须删旧写新；否则仅追加时视图仍似「未更新」
    _recharge_tier_csv = frozenset({"10_充值_付费人数按SKU.csv", "11_充值_付费金额按SKU.csv"})
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
        # replace_tables 未配置或空列表时：全部表先清空再写入（覆盖）；非空名单则按 replace_table ∪ 名单
        if replace_tables is None:
            do_replace = True  # 未配置 replace_tables 时，默认全部覆盖
        else:
            do_replace = replace_table_default or (name in replace_tables)
        if name in _recharge_tier_csv:
            do_replace = True
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


def _load_output_csv_rows(csv_path: Path) -> list[dict[str, str]]:
    """读取提纯 output 下的 CSV（utf-8-sig），不存在或为空则返回 []。"""
    if not csv_path.is_file():
        return []
    try:
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except Exception as e:
        logger.warning("[BI KPI] 读取 %s 失败: %s", csv_path.name, e)
        return []


def _csv_first_nonempty(row: dict[str, str], *keys: str) -> Any:
    """按顺序取第一个「键存在且非空白」的单元格；勿用 a or b，否则合法数值 0 会被跳过。"""
    for k in keys:
        if k not in row:
            continue
        v = row[k]
        if v is None:
            continue
        if isinstance(v, str) and not str(v).strip():
            continue
        return v
    return None


def _csv_float(val: Any, default: float = 0.0) -> float:
    if val is None or (isinstance(val, str) and not str(val).strip()):
        return default
    s = str(val).strip().replace(",", "")
    if s in ("-", "NaN", "nan"):
        return default
    try:
        return float(s)
    except ValueError:
        return default


def _csv_int(val: Any, default: int = 0) -> int:
    return int(round(_csv_float(val, float(default))))


def _fmt_kpi_pct_badge(pct: float) -> str:
    """KPI 快报涨跌可视化：🟢 **+x%** / 🔻 **-x%**（Lark Markdown；零增幅用中性粗体）。"""
    if abs(pct) < 1e-9:
        return " **`0.00%`**"
    if pct > 0:
        return f" 🟢 **+{pct:.2f}%**"
    return f" 🔻 **{pct:.2f}%**"


def _game_row_is_placeholder(game_type: str) -> bool:
    g = (game_type or "").strip()
    return not g or g.startswith("（需") or g.startswith("(需")


def _is_stats_game_daily_total_scope(scope: str) -> bool:
    """BI「每日游戏数据」表中首行「当日总计」口径（非各游戏分行）。用于 RTP/GGR 平台合计。"""
    g = (scope or "").strip()
    if not g:
        return False
    if g in ("当日总计", "日总计", "当日合计", "本日合计"):
        return True
    # 少数导出为「全部」汇总行，与当日总计同列位置
    if g in ("全部汇总", "全平台汇总", "平台汇总"):
        return True
    return False


def _build_bi_kpi_snapshot_markdown(output_dir: Path, report_date: str) -> str:
    """
    从 output 目录 CSV 拼装「多维表同步后、大战报前」的 KPI 快照（Markdown：分组 + 涨跌徽章）。
    数据来源与 Lark 子表一一对应（提纯输出）；数值口径与旧版一行式一致，仅排版分层。
    """
    od = output_dir
    # 1) DAU / DNU — 01_用户活跃_增幅表.csv
    dau_v, dnu_v = 0, 0
    dau_pct, dnu_pct = 0.0, 0.0
    for r in _load_output_csv_rows(od / "01_用户活跃_增幅表.csv"):
        typ = str(r.get("类型", "")).strip().upper()
        if typ == "DAU":
            dau_v = _csv_int(r.get("数值"), 0)
            dau_pct = _csv_float(r.get("增幅（%）"), 0.0)
        elif typ == "DNU":
            dnu_v = _csv_int(r.get("数值"), 0)
            dnu_pct = _csv_float(r.get("增幅（%）"), 0.0)

    # 2) 新增设备 — 13
    r13 = (_load_output_csv_rows(od / "13_用户活跃_新增设备表.csv") or [{}])[0]
    new_dev = _csv_int(r13.get("新增设备数量"), 0)
    new_dev_pct = _csv_float(_csv_first_nonempty(r13, "新增设备增幅（%）", "新增设备涨幅（%）"), 0.0)
    ratio_raw = r13.get("新增用户/新增设备", "")
    if ratio_raw is None or str(ratio_raw).strip() in ("", "-"):
        ratio_s = "-"
    else:
        try:
            ratio_s = f"{_csv_float(ratio_raw, 0.0):.2f}"
        except Exception:
            ratio_s = str(ratio_raw).strip()

    # 3) 付费 — 14
    r14 = (_load_output_csv_rows(od / "14_充值_付费人数金额增幅表.csv") or [{}])[0]
    paid_n = _csv_int(r14.get("当天付费人数"), 0)
    paid_amt = _csv_float(r14.get("付费总金额"), 0.0)
    paid_amt_pct = _csv_float(r14.get("增幅"), 0.0)

    # 4) Arup / Arppu — 15、16（列名与多维表一致）
    r15 = (_load_output_csv_rows(od / "15_消耗_Arup表.csv") or [{}])[0]
    r16 = (_load_output_csv_rows(od / "16_消耗_Arppu表.csv") or [{}])[0]
    arpu_v = _csv_float(r15.get("Arpu数值"), 0.0)
    arpu_pct = _csv_float(_csv_first_nonempty(r15, "Arpu增幅（%）", "Arup涨幅（%）", "Arup增幅（%）"), 0.0)
    arppu_v = _csv_float(r16.get("Arppu数值"), 0.0)
    arppu_pct = _csv_float(_csv_first_nonempty(r16, "增幅（%）", "涨幅（%）"), 0.0)

    # 5) 完成局数 / 人均局数 — 17
    rows17 = _load_output_csv_rows(od / "17_游戏_完成局数.csv")
    total_rounds = 0
    w_num, w_den = 0.0, 0.0  # 加权人均局数
    for r in rows17:
        g = str(r.get("游戏类型", "")).strip()
        if _game_row_is_placeholder(g):
            continue
        tr = _csv_int(r.get("全部用户局数"), 0)
        total_rounds += tr
        pu = _csv_float(r.get("人均游戏局数"), 0.0)
        if tr > 0 and pu > 0:
            w_num += pu * tr
            w_den += float(tr)
    avg_rounds = (w_num / w_den) if w_den > 0 else 0.0

    # 6) 获胜次数 / 胜率 — 18 + 17 按游戏对齐
    rows18 = _load_output_csv_rows(od / "18_游戏_用户获胜.csv")
    by_game_17: dict[str, int] = {}
    for r in rows17:
        g = str(r.get("游戏类型", "")).strip()
        if _game_row_is_placeholder(g):
            continue
        by_game_17[g] = _csv_int(r.get("全部用户局数"), 0)
    total_wins = 0
    total_complete_for_rate = 0
    for r in rows18:
        g = str(r.get("游戏类型", "")).strip()
        if _game_row_is_placeholder(g):
            continue
        wo = _csv_int(r.get("老用户胜利数"), 0)
        wn = _csv_int(r.get("新用户胜利数"), 0)
        total_wins += wo + wn
        tc = by_game_17.get(g, 0)
        if tc <= 0:
            tc = wo + wn  # 兜底：无 17 对齐时用胜场和作分母近似
        total_complete_for_rate += tc
    win_rate = (100.0 * total_wins / total_complete_for_rate) if total_complete_for_rate > 0 else _csv_float(
        (rows18[0].get("总胜率（%）") if rows18 else 0), 0.0
    )

    # 7) RTP / GGR — 19（GameRTP 须为 BI「每日游戏数据」当日总计行「回报率RTP」，勿对各游戏 RTP 做平均或按 GGR 加权）
    rows19 = _load_output_csv_rows(od / "19_游戏_RTP_GGR.csv")
    ggr_sum = 0.0
    rtp_disp = 0.0
    total19: dict[str, str] | None = None
    for r in rows19:
        g = str(r.get("游戏类型", "")).strip()
        if _game_row_is_placeholder(g):
            continue
        if _is_stats_game_daily_total_scope(g):
            total19 = r
            break
    if total19 is not None:
        rtp_disp = _csv_float(total19.get("RTP(%)"), 0.0)
        ggr_sum = _csv_float(total19.get("GGR"), 0.0)
    else:
        rtp_w_num, rtp_w_den = 0.0, 0.0
        rtp_simple: list[float] = []
        for r in rows19:
            g = str(r.get("游戏类型", "")).strip()
            if _game_row_is_placeholder(g) or _is_stats_game_daily_total_scope(g):
                continue
            rtp = _csv_float(r.get("RTP(%)"), 0.0)
            ggr = _csv_float(r.get("GGR"), 0.0)
            ggr_sum += ggr
            rtp_simple.append(rtp)
            ag = abs(ggr)
            if ag > 0:
                rtp_w_num += rtp * ag
                rtp_w_den += ag
        rtp_disp = (rtp_w_num / rtp_w_den) if rtp_w_den > 0 else (sum(rtp_simple) / len(rtp_simple) if rtp_simple else 0.0)

    ratio_disp = f"`{ratio_s}`" if ratio_s != "-" else ratio_s
    lines = [
        f"**数据日 {report_date}**｜多维表已同步",
        "",
        "---",
        "",
        "### 👥 用户与流量",
        f"- DAU：`{dau_v}`{_fmt_kpi_pct_badge(dau_pct)}",
        f"- DNU：`{dnu_v}`{_fmt_kpi_pct_badge(dnu_pct)}",
        f"- 新增设备数：`{new_dev}`{_fmt_kpi_pct_badge(new_dev_pct)}",
        f"- 新增用户/设备数：{ratio_disp}",
        "",
        "---",
        "",
        "### 💰 充值与营收",
        f"- 付费人数：`{paid_n}`",
        f"- 付费总金额：`{paid_amt:.2f}`{_fmt_kpi_pct_badge(paid_amt_pct)}",
        f"- Arpu：`{arpu_v:.2f}`{_fmt_kpi_pct_badge(arpu_pct)}",
        f"- Arppu：`{arppu_v:.2f}`{_fmt_kpi_pct_badge(arppu_pct)}",
        "",
        "---",
        "",
        "### 🎮 游戏与生态",
        f"- 完成游戏局数：`{total_rounds}`",
        f"- 人均游戏局数：`{avg_rounds:.2f}`",
        f"- 用户获胜次数：`{total_wins}`",
        f"- 胜率：`{win_rate:.2f}%`",
        "",
        "---",
        "",
        "### ⚖️ 经济与风控（RTP / GGR）",
        f"- GameRTP：`{rtp_disp:.2f}%`",
        f"- GGR：`{ggr_sum:.2f}`",
    ]
    return "\n".join(lines)


def _kpi_inline_md_to_html(segment: str) -> str:
    """KPI 快报行内 Markdown：`code` 与 **粗体** → HTML（用于邮件）。"""
    import html as html_module

    out: list[str] = []
    pos = 0
    for m in re.finditer(r"`([^`]+)`|\*\*([^*]+)\*\*", segment):
        if m.start() > pos:
            out.append(html_module.escape(segment[pos : m.start()]))
        if m.group(1) is not None:
            out.append(
                "<code style=\"background:#f5f5f5; padding:2px 6px; border-radius:4px;\">"
                f"{html_module.escape(m.group(1))}</code>"
            )
        else:
            out.append(f"<strong>{html_module.escape(m.group(2))}</strong>")
        pos = m.end()
    if pos < len(segment):
        out.append(html_module.escape(segment[pos:]))
    return "".join(out)


def _kpi_snapshot_md_to_email_html(md: str) -> str:
    """将 _build_bi_kpi_snapshot_markdown 产出的 Markdown 转为邮件用 HTML 片段。"""
    lines = md.split("\n")
    parts: list[str] = []
    in_list = False
    for line in lines:
        raw = line.rstrip()
        if not raw:
            if in_list:
                parts.append("</ul>")
                in_list = False
            parts.append("<br/>")
            continue
        if raw == "---":
            if in_list:
                parts.append("</ul>")
                in_list = False
            parts.append('<hr style="margin:12px 0; border:none; border-top:1px solid #e8e8e8;"/>')
            continue
        if raw.startswith("### "):
            if in_list:
                parts.append("</ul>")
                in_list = False
            title = html.escape(raw[4:].strip())
            parts.append(f'<h4 style="margin:16px 0 8px 0; color:#333;">{title}</h4>')
            continue
        if raw.startswith("- "):
            if not in_list:
                parts.append('<ul style="margin:8px 0; padding-left:20px;">')
                in_list = True
            parts.append(f"<li style=\"margin:4px 0;\">{_kpi_inline_md_to_html(raw[2:])}</li>")
            continue
        if in_list:
            parts.append("</ul>")
            in_list = False
        parts.append(f"<p style=\"margin:8px 0;\">{_kpi_inline_md_to_html(raw)}</p>")
    if in_list:
        parts.append("</ul>")
    return "\n".join(parts)


def _resolve_bi_lark_push_targets(cfg: dict[str, Any]) -> tuple[str, str]:
    """返回 (webhook_url, chat_id)，与 Step 3.5 战略推送一致。"""
    dist = cfg.get("distribution") or {}
    webhook = (dist.get("lark_webhook_url") or "").strip()
    chat_id = (os.environ.get("BI_LARK_CHAT_ID") or os.environ.get("LARK_CHAT_ID") or "").strip()
    if not chat_id:
        chat_id = (dist.get("lark_chat_id") or "").strip()
    if str(webhook).startswith("${"):
        webhook = ""
    if str(chat_id).startswith("${"):
        chat_id = ""
    if not chat_id:
        try:
            from l3_node.jachin_config import load_mcp_config
            from l3_node.paths import get_app_root

            mcp_cfg = load_mcp_config("atom_lark_notifier", project_root=get_app_root())
            chat_id = (mcp_cfg.get("default_chat_id") or "").strip()
            if str(chat_id).startswith("${"):
                chat_id = ""
        except Exception:
            pass
    return webhook, chat_id


def _load_bi_project_context_md(project_root: Path, max_total_chars: int = 75000) -> str:
    """大战报用：聚合 docs/bi_daily_report/bi_project 下全部 .md。"""
    d = project_root / "docs" / "bi_daily_report" / "bi_project"
    if not d.is_dir():
        return "（目录不存在：docs/bi_daily_report/bi_project）"
    parts: list[str] = []
    total = 0
    for p in sorted(d.glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as e:
            text = f"（读取失败: {e}）"
        block = f"\n\n#### 文件 `{p.name}`\n\n{text}"
        if total + len(block) > max_total_chars:
            parts.append("\n\n…（后续 bi_project 文档因长度上限省略）")
            break
        parts.append(block)
        total += len(block)
    body = "".join(parts).strip()
    return body if body else "（bi_project 下无 .md）"


def _norm_strategic_csv_date(val: Any) -> str:
    """统一为 YYYY-MM-DD，无法解析则返回空串。"""
    s = str(val or "").strip()
    if not s:
        return ""
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        cand = s[:10]
        if re.match(r"^\d{4}-\d{2}-\d{2}$", cand):
            return cand
    if s.isdigit() and len(s) >= 13:
        try:
            ms = int(s[:13])
            return datetime.utcfromtimestamp(ms / 1000.0).strftime("%Y-%m-%d")
        except (ValueError, OSError):
            pass
    m = re.match(r"^(\d{4})/(\d{1,2})/(\d{1,2})", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{y:04d}-{mo:02d}-{d:02d}"
    return ""


def _strategic_pick_date_column(fieldnames: list[str]) -> str | None:
    priority = ("统计日期", "业务日期", "日期", "data_date")
    for key in priority:
        if key in fieldnames:
            return key
    for c in fieldnames:
        cs = str(c or "")
        if "日期" in cs or str(c).lower() in ("date", "day"):
            return c
    return None


def _strategic_row_for_date(rows: list[dict[str, Any]], date_col: str, target: str) -> dict[str, Any] | None:
    for r in rows:
        if _norm_strategic_csv_date(r.get(date_col)) == target:
            return r
    return None


def _strategic_diff_two_rows(
    r_t1: dict[str, Any],
    r_t2: dict[str, Any],
    skip: set[str],
    max_cols: int = 24,
) -> list[str]:
    out: list[str] = []
    keys = sorted(set(r_t1.keys()) | set(r_t2.keys()))
    n = 0
    for k in keys:
        if k in skip or not str(k).strip():
            continue
        a, b = r_t1.get(k), r_t2.get(k)
        if a == b:
            continue
        fa, fb = _sf_float(a), _sf_float(b)
        if fa is not None and fb is not None:
            delta = fa - fb
            pct = (delta / fb * 100.0) if fb not in (0, 0.0) else float("nan")
            pct_s = f"{pct:+.2f}%" if pct == pct else "n/a"
            out.append(f"  - **{k}**: T={fa:g} | T-1={fb:g} | Δ={delta:+.6g} ({pct_s})")
        else:
            sa, sb = str(a).strip()[:80], str(b).strip()[:80]
            if sa != sb:
                out.append(f"  - **{k}**: T=`{sa}` | T-1=`{sb}`")
        n += 1
        if n >= max_cols:
            out.append("  - …（更多列省略）")
            break
    return out


def _sf_float(v: Any) -> float | None:
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _strategic_channel_two_day_digest(
    rows: list[dict[str, Any]],
    date_col: str,
    ch_col: str,
    qty_col: str,
    t1: str,
    t2: str,
    top_n: int = 5,
) -> list[str]:
    from collections import defaultdict

    m1: dict[str, float] = defaultdict(float)
    m2: dict[str, float] = defaultdict(float)
    for r in rows:
        d = _norm_strategic_csv_date(r.get(date_col))
        ch = str(r.get(ch_col) or "").strip() or "(空)"
        q = _sf_float(r.get(qty_col))
        if q is None:
            continue
        if d == t1:
            m1[ch] += q
        elif d == t2:
            m2[ch] += q
    s1, s2 = sum(m1.values()), sum(m2.values())
    lines = [
        f"  - **{qty_col} 合计**: T={s1:g} | T-1={s2:g} | Δ={s1 - s2:+.6g}",
    ]
    all_ch = set(m1.keys()) | set(m2.keys())
    deltas: list[tuple[float, str]] = []
    for ch in all_ch:
        a, b = m1.get(ch, 0.0), m2.get(ch, 0.0)
        deltas.append((a - b, ch))
    deltas.sort(key=lambda x: -abs(x[0]))
    for dlt, ch in deltas[:top_n]:
        a, b = m1.get(ch, 0.0), m2.get(ch, 0.0)
        lines.append(f"  - 渠道 `{ch}`: T={a:g} | T-1={b:g} | Δ={dlt:+.6g}")
    return lines


def _summarize_one_csv_dod(path: Path, t1: str, t2: str, label: str) -> list[str]:
    lines: list[str] = []
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []
            rows = list(reader)
    except OSError as e:
        return [f"- （读取失败 {label}: {e}）"]

    if not rows:
        return [f"- （空表 {label}）"]

    fn = path.name
    # 01 增幅表：按类型快照 + 增幅列已含日环比语义
    if fn.startswith("01_用户活跃_增幅表"):
        lines.append(f"**{label}**（无日期列；提纯为 DAU/DNU 快照，增幅列为相对前日）")
        for r in rows[:12]:
            typ = str(r.get("类型", "") or "").strip()
            if typ:
                lines.append(
                    f"  - {typ}: 数值={r.get('数值', '')} 增幅（%）={r.get('增幅（%）', '')}"
                )
        return lines

    if fn.startswith(
        ("04_留存_次留表", "05_留存_付费用户次留表", "06_留存_周环比表", "07_留存_付费用户周环比表")
    ):
        lines.append(f"**{label}**（留存/周环比多行快照；对照 T 业务日与周结构）")
        for r in rows[:18]:
            ks = list(r.keys())[:6]
            lines.append("  - " + " | ".join(f"{k}={r.get(k, '')}" for k in ks))
        return lines

    if fn.startswith("10_充值_付费人数按SKU") or fn.startswith("11_充值_付费金额按SKU"):
        lines.append(f"**{label}**（SKU 分档快照；判断小额单 vs 大R 结构）")
        for r in rows[:22]:
            ks = list(r.keys())[:5]
            lines.append("  - " + " | ".join(f"{k}={r.get(k, '')}" for k in ks))
        return lines

    if fn.startswith("17_游戏_完成局数") or fn.startswith("18_游戏_用户获胜") or fn.startswith("19_游戏_RTP_GGR"):
        lines.append(f"**{label}**（按游戏多行；关注 RTP/GGR/局数/胜负）")
        for r in rows[:16]:
            g = str(r.get("游戏类型", "") or "").strip()
            if not g or "需抓取" in g:
                continue
            ks = [k for k in r.keys() if k != "游戏类型"][:5]
            lines.append(f"  - {g}: " + " | ".join(f"{k}={r.get(k, '')}" for k in ks))
        return lines

    dc = _strategic_pick_date_column(list(fieldnames))
    if not dc:
        lines.append(f"**{label}**（无日期列；首行抽样）")
        r0 = rows[0]
        for k in list(r0.keys())[:8]:
            lines.append(f"  - {k}={r0.get(k, '')}")
        return lines

    r1 = _strategic_row_for_date(rows, dc, t1)
    r2 = _strategic_row_for_date(rows, dc, t2)

    # 渠道类：按日聚合
    if "03a_" in fn or "03b_" in fn:
        ch_c = _find_col(list(fieldnames), "DAU渠道来源", "DNU渠道来源", "渠道", "来源")
        qty_c = _find_col(list(fieldnames), "数量", "人数", "DAU数量", "DNU数量")
        if ch_c and qty_c:
            lines.append(f"**{label}**（按 `{dc}` 聚合 `{qty_c}`）")
            lines.extend(_strategic_channel_two_day_digest(rows, dc, ch_c, qty_c, t1, t2))
            return lines

    lines.append(f"**{label}**（日期列 `{dc}`）")
    if r1 and r2:
        skip = {dc}
        diff = _strategic_diff_two_rows(r1, r2, skip)
        if diff:
            lines.extend(diff)
        else:
            lines.append("  - （T 与 T-1 行非数值字段一致或无可比列）")
    elif r1:
        lines.append(f"  - 仅有 **T={t1}** 行，缺少 **T-1={t2}**（无法做双日 diff）")
    elif r2:
        lines.append(f"  - 仅有 **T-1={t2}** 行，缺少 **T={t1}**")
    else:
        have = {_norm_strategic_csv_date(r.get(dc)) for r in rows if r.get(dc)}
        sample = sorted(x for x in have if x)[:5]
        lines.append(f"  - 未命中 T/T-1；表中出现的日期样例: {sample}")
    return lines


def _build_strategic_dod_summary(
    output_dir: Path,
    raw_dir: Path | None,
    t1_iso: str,
    t2_iso: str,
    *,
    max_lines: int = 340,
) -> str:
    """
    大战报专用：对 strategic_report 关注的 output CSV + 对应 raw slug 做 T vs T-1 字段级对照文本。
    """
    try:
        from l3_node.skills.bi.bi_daily_report.strategic_report import (
            _STRATEGIC_KEY_FILES,
            _STRATEGIC_KEY_TO_RAW,
            _STRATEGIC_RAW_ONLY_SLUGS,
        )
    except ImportError as e:
        return f"（日环比摘要：无法导入战略模块列表: {e}）"

    t1 = t1_iso[:10]
    t2 = t2_iso[:10]
    acc: list[str] = [
        f"说明：T 为数据日（通常昨日），T-1 为前一自然日。数值型字段给出差值与环比%；若缺某日行则标明。",
        "",
        "**口径防错（大战报）**：`stats_user_dau`/`stats_user_new` 金币产出消耗列换算「亿」须按 10⁸（例 53,179,122≈0.53 亿，勿写成 5.32 亿）。"
        "`prod_sales` 用户金币与机器人金币分列叙述。`daily_acquisition` 每行=单条落地链接。`stats_retention_user` 的 T+1 若为 0/NaN% 优先写未闭合/ETL，勿单归因架构。",
        "",
        "**大战报归因要求（v3）**：以下 diff 须结合 `docs/bi_daily_report/bi_project` 背景与《STRATEGIC_REPORT_ANALYSIS_SPEC.md》——"
        " 异动不可单一归因；并列检验 **平台/运营/版本**（上新、活动、买量、支付、体验房与弹窗）与 **外部**（发薪日、圣周、台风/断网、渠道）及 **风控/埋点/黑产**。"
        " output 为提纯表重点；同路径下 raw 明细已在下文对照。",
        "",
        "## output（提纯表，与 Lark 多维表同源）",
        "",
    ]
    od = Path(output_dir)
    for name in _STRATEGIC_KEY_FILES:
        p = od / name
        if not p.exists():
            continue
        acc.extend(_summarize_one_csv_dod(p, t1, t2, f"`{name}`"))
        acc.append("")
        if len(acc) > max_lines:
            break

    acc.append("## raw（client_volumes/bi_data/raw，与 SPA 抓取同源）")
    acc.append("")
    seen: set[str] = set()
    if raw_dir and Path(raw_dir).is_dir():
        for name in _STRATEGIC_KEY_FILES:
            for slug in _STRATEGIC_KEY_TO_RAW.get(name, []):
                if slug in seen:
                    continue
                seen.add(slug)
                rp = Path(raw_dir) / f"{slug}.csv"
                if not rp.exists():
                    continue
                acc.extend(_summarize_one_csv_dod(rp, t1, t2, f"raw `{slug}.csv`"))
                acc.append("")
                if len(acc) > max_lines:
                    break
        for slug in _STRATEGIC_RAW_ONLY_SLUGS:
            if slug in seen:
                continue
            rp = Path(raw_dir) / f"{slug}.csv"
            if not rp.exists():
                continue
            seen.add(slug)
            acc.extend(_summarize_one_csv_dod(rp, t1, t2, f"raw `{slug}.csv`"))
            acc.append("")
            if len(acc) > max_lines:
                break
    else:
        acc.append("（raw 目录不可用，跳过 raw 对照）")

    text = "\n".join(acc).strip()
    if len(text) > 45000:
        text = text[:45000] + "\n\n…（日环比全文截断）"
    return text


_STRATEGIC_SPEC_AESTHETICS_MARKER = "## 战报输出美学与 Markdown 排版规范"


def _extract_strategic_report_aesthetics_section(project_root: Path) -> str:
    """从 STRATEGIC_REPORT_ANALYSIS_SPEC.md 截取「战报输出美学」章节，供大战报 System Prompt 追加（与 SSOT 同步）。"""
    p = project_root / "docs" / "bi_daily_report" / "STRATEGIC_REPORT_ANALYSIS_SPEC.md"
    if not p.is_file():
        return ""
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        logger.debug("[BI] 读取战略规范节选排版失败: %s", e)
        return ""
    start_i: int | None = None
    for i, line in enumerate(lines):
        if line.strip() == _STRATEGIC_SPEC_AESTHETICS_MARKER or line.startswith(
            _STRATEGIC_SPEC_AESTHETICS_MARKER
        ):
            start_i = i
            break
    if start_i is None:
        return ""
    out: list[str] = [lines[start_i]]
    for j in range(start_i + 1, len(lines)):
        line = lines[j]
        if line.startswith("## ") and not line.startswith("###"):
            break
        out.append(line)
    return "\n".join(out).strip()


def _merge_strategic_report_config_for_llm(
    cfg: dict[str, Any],
    *,
    project_root: Path,
    output_dir: Path,
    raw_dir: Path,
) -> dict[str, Any]:
    """在不改仪表盘逻辑的前提下，为 Step 3.5 大战报注入 bi_project 背景 + T/T-1 数据摘要 + 排版美学节选（注入 strategic_report System 追加节）。"""
    from l3_node.skills.bi.bi_daily_report.strategic_report import _detect_report_date_from_raw

    out_cfg = dict(cfg or {})
    sr0 = dict(out_cfg.get("strategic_report") or {})

    k11_md = _load_bi_project_context_md(project_root)
    rd = Path(raw_dir)
    if rd.is_dir():
        t1_iso, _ = _detect_report_date_from_raw(rd)
    else:
        t1_iso = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    t2_iso = (datetime.strptime(t1_iso[:10], "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        dod = _build_strategic_dod_summary(Path(output_dir), rd if rd.is_dir() else None, t1_iso, t2_iso)
    except Exception as e:
        logger.warning("[BI] 大战报日环比摘要生成失败: %s", e)
        dod = f"（生成失败: {e}）"

    sr0["_k11_project_context_md"] = k11_md
    sr0["_strategic_dod_summary"] = dod
    sr0["_strategic_dod_t1"] = t1_iso[:10]
    sr0["_strategic_dod_t2"] = t2_iso[:10]
    _aest = _extract_strategic_report_aesthetics_section(project_root)
    if _aest:
        sr0["_strategic_aesthetics_section"] = _aest
    out_cfg["strategic_report"] = sr0
    return out_cfg


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


def _try_autolaunch_chrome_for_bi(cdp_url: str, open_url: str) -> tuple[bool, str]:
    """
    在 Windows 上尝试启动带远程调试端口的 Chrome（与 scripts/launch_chrome_debug_bi.ps1 行为一致）。
    返回 (是否已成功发起进程, 说明文案)。
    """
    import subprocess
    from urllib.parse import urlparse

    if os.name != "nt":
        return False, "当前仅 Windows 支持自动拉起 Chrome（Linux/Mac 请先手动启动调试 Chrome）"
    u = urlparse((cdp_url or "").strip() or "http://127.0.0.1:9222")
    try:
        port = int(u.port or 9222)
    except (TypeError, ValueError):
        port = 9222
    chrome_paths = [
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), r"Google\Chrome\Application\chrome.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), r"Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    chrome_exe = next((p for p in chrome_paths if p and os.path.isfile(p)), None)
    if not chrome_exe:
        return False, "未找到 chrome.exe，请安装 Google Chrome"
    user_data_dir = os.path.join(os.environ.get("TEMP", os.environ.get("TMP", ".")), "chrome-debug-bi")
    url = (open_url or "").strip() or "https://bi-admin-web.heronpro.xin/#/layout/person"
    args = [chrome_exe, f"--remote-debugging-port={port}", f"--user-data-dir={user_data_dir}", url]
    creationflags = 0
    if hasattr(subprocess, "DETACHED_PROCESS"):
        creationflags |= subprocess.DETACHED_PROCESS
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(
            args,
            cwd=os.path.dirname(chrome_exe) or None,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        return True, f"已启动 Chrome 调试端口 {port}，用户数据目录 {user_data_dir}"
    except Exception as e:
        return False, str(e)


def _cdp_unavailable_user_message(cdp_url: str) -> str:
    return (
        f"无法连接 Chrome 远程调试 ({cdp_url})，本机拒绝连接(ECONNREFUSED) 表示该端口没有浏览器在监听。\n\n"
        "【原因】SPA 抓取通过 CDP 控制本机 Chrome；未先启动「带 --remote-debugging-port 的 Chrome」时，9222 端口无人监听即会失败。\n\n"
        "【处理方式 A — 手动】\n"
        "1) 先关掉平时用的 Chrome（避免占端口）。\n"
        "2) 在项目根 PowerShell 执行: .\\scripts\\launch_chrome_debug_bi.ps1\n"
        "3) 在弹出的 Chrome 中登录 BI，保持窗口不关。\n"
        "4) 再执行: python scripts\\run_bi_daily_report.py\n\n"
        "【处理方式 B — 自动（仅 Windows）】\n"
        "在 bi_daily_report.yaml 的 full_spa 下设置 auto_launch_chrome_when_cdp_unreachable: true，\n"
        "或设置环境变量 BI_SPA_AUTO_LAUNCH_CHROME=1，脚本会尝试自动拉起调试 Chrome 并等待端口就绪。\n\n"
        "【无需抓数时】若 DuckDB/raw 已是今日数据，Step 1 会跳过抓取，可不启 Chrome。"
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
    result: dict[str, Any] = {
        "success": False,
        "stage": "init",
        "data_updated": False,
        "output_paths": [],
        "lark_sync_ok": 0,
        "lark_sync_errors": [],
        "kpi_snapshot_sent": False,
        "strategic_report_sent": False,
        "dashboard_analysis_sent": False,
        "email_ok": False,
        "email_error": "",
        "error": "",
    }

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
                auto_launch = bool(full_spa.get("auto_launch_chrome_when_cdp_unreachable")) or os.environ.get(
                    "BI_SPA_AUTO_LAUNCH_CHROME", ""
                ).strip().lower() in ("1", "true", "yes")
                if auto_launch:
                    ok_launch, launch_msg = await asyncio.to_thread(_try_autolaunch_chrome_for_bi, cdp_url, base_url)
                    _bi_log("Step 1.1: CDP 不可达，尝试自动启动 Chrome", detail=launch_msg, progress=True)
                    if not ok_launch:
                        result["stage"] = "collect"
                        result["error"] = _cdp_unavailable_user_message(cdp_url) + f"\n\n自动启动失败：{launch_msg}"
                        _bi_log("Step 1.1 前置检查失败", detail="CDP 不可达且自动启动 Chrome 失败", progress=True)
                        _bi_debug("Step 1.1", "cdp_unreachable", data={"cdp_url": cdp_url, "auto_launch": False})
                        return result
                    for wait_sec in range(25):
                        await asyncio.sleep(1)
                        if await asyncio.to_thread(_cdp_endpoint_reachable, cdp_url):
                            _bi_log(
                                "Step 1.1: CDP 已连通",
                                detail=f"等待约 {wait_sec + 1}s 后检测到调试端口，继续 SPA 抓取",
                                progress=True,
                            )
                            break
                    else:
                        result["stage"] = "collect"
                        result["error"] = (
                            _cdp_unavailable_user_message(cdp_url)
                            + "\n\n已自动启动 Chrome，但 25 秒内仍未检测到 CDP。"
                            "若为首次使用的调试用户目录，请在弹出的窗口中完成登录后重试；"
                            "或确认 9222 未被防火墙/安全软件拦截。"
                        )
                        _bi_log("Step 1.1 前置检查失败", detail="自动启动后 CDP 仍不可达", progress=True)
                        _bi_debug("Step 1.1", "cdp_unreachable_after_autolaunch", data={"cdp_url": cdp_url})
                        return result
                else:
                    result["stage"] = "collect"
                    result["error"] = _cdp_unavailable_user_message(cdp_url)
                    _bi_log("Step 1.1 前置检查失败", detail="CDP 不可达，已中止抓取（避免逐表重复报错）", progress=True)
                    _bi_debug("Step 1.1", "cdp_unreachable", data={"cdp_url": cdp_url})
                    return result
            # 与 run_bi_scraper_spa：未传 slug 时 slugs=None → 整表 MENU_ITEMS；仅 full_spa.slugs 非空列表时才收窄
            _cfg_slugs = full_spa.get("slugs")
            slugs = _cfg_slugs if isinstance(_cfg_slugs, list) and len(_cfg_slugs) > 0 else None
            dm = parse_direct_url_map_from_full_spa(full_spa)
            spa_end = _bi_spa_report_date_end(cfg)
            _bi_log(
                "Step 1.1: SPA 统计日期区间结束日",
                detail=spa_end.isoformat(),
                progress=True,
            )

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
                report_date_end=spa_end,
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

    # 长耗时 SPA 后再次对齐 Key/engine_ref，避免 Step 4a/3.5 仍 import __main__ 且 os.environ 未含 DASHSCOPE
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
                    _bi_log(
                        "Step 3 提示",
                        detail="FieldNameNotFound 表示 Lark 表字段名与 CSV 列名不一致。提纯侧约定：13「新增设备增幅（%）」；08b 渠道层表仅「渠道、金币产出、金币消耗」；15「Arpu增幅（%）」、16「增幅（%）」；其余含日期表为 YYYY-MM-DD。若 Lark 列名不同，请在 bi_daily_report.yaml 的 lark_bitable.field_mapping 中配置（见 docs/bi_daily_report/11_LARK_TABLE_SCHEMA.md）。",
                    )
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

    # Step 3.4: 多维表同步完成后、战略大战报前 — 推送 KPI 快照卡片（读 output CSV，与 Lark 表同源）
    snap_cfg = cfg.get("kpi_snapshot_card") or {}
    if snap_cfg.get("enabled", True) and output_dir.is_dir():
        report_d = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        _wh, _cid = _resolve_bi_lark_push_targets(cfg)
        if _wh or _cid:
            try:
                kpi_md = _build_bi_kpi_snapshot_markdown(output_dir, report_d)
                lark_cfg = cfg.get("lark_bitable") or {}
                if lark_cfg.get("app_id") and lark_cfg.get("app_secret"):
                    os.environ.setdefault("LARK_APP_ID", str(lark_cfg.get("app_id", "")).strip())
                    os.environ.setdefault("LARK_APP_SECRET", str(lark_cfg.get("app_secret", "")).strip())
                if lark_cfg.get("lark_use_feishu"):
                    os.environ["LARK_USE_FEISHU"] = "1"
                from l3_node.mcp_tools.bi.tool_lark_notifier import send_lark_markdown

                _r = send_lark_markdown(_wh or "", kpi_md[:6000], title="📊 BI 数据快报", chat_id=_cid or None)
                if _r.get("status") == "success":
                    result["kpi_snapshot_sent"] = True
                    _bi_log("Step 3.4: KPI 快照卡片已推送至 Lark", detail=f"数据日={report_d}", progress=True)
                else:
                    _bi_log("Step 3.4 警告: KPI 快照推送失败", detail=str(_r.get("error", _r)))
            except Exception as e:
                _bi_log("Step 3.4 警告: KPI 快照异常", detail=str(e))
                _bi_debug("Step 3.4", "exception", exc=e)
        else:
            _bi_log("Step 3.4: 未配置 Lark webhook/chat_id，跳过 KPI 快照卡片")
    else:
        _bi_debug("Step 3.4", "skip", data={"enabled": snap_cfg.get("enabled", True), "dir_exists": output_dir.is_dir()})

    _bi_reconcile_llm_engine_ref_with_agent()

    # Step 4a: 先于大战报 — 生成所有仪表盘分析（供邮件 + Lark 使用）
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

    _bi_reconcile_llm_engine_ref_with_agent()

    # Step 3.5: 仪表盘之后 — 战略大战报 + Lark 推送
    strategic_cfg = cfg.get("strategic_report") or {}
    _bi_debug("Step 3.5", "branch", data={"enabled": strategic_cfg.get("enabled", True)})
    if strategic_cfg.get("enabled", True):
        _bi_debug("Step 3.5", "entry", detail="generate_bi_strategic_report_async")
        _bi_log("Step 3.5: 正在调用 LLM 生成战略深度分析战报（v4 长文形态）...", progress=True)
        try:
            from l3_node.paths import get_app_root
            from l3_node.skills.bi.bi_daily_report.strategic_report import generate_bi_strategic_report_async

            cfg_strategic = _merge_strategic_report_config_for_llm(
                cfg,
                project_root=get_app_root(),
                output_dir=output_dir,
                raw_dir=raw_dir_collect,
            )
            strategic_md = await generate_bi_strategic_report_async(
                metrics=None,
                output_dir=output_dir,
                config=cfg_strategic,
            )
            result["strategic_report"] = strategic_md
            _bi_debug("Step 3.5", "exit", data={"report_len": len(strategic_md), "push_to_lark": strategic_cfg.get("push_to_lark", True)})
            _bi_log("Step 3.5 结果: 战略报告已生成", detail=f"长度={len(strategic_md)} 字符", progress=True)
            if strategic_cfg.get("push_to_lark", True) and strategic_md:
                dist_push = cfg.get("distribution") or {}
                webhook = (dist_push.get("lark_webhook_url") or "").strip()
                chat_id = (os.environ.get("BI_LARK_CHAT_ID") or os.environ.get("LARK_CHAT_ID") or "").strip()
                if not chat_id:
                    chat_id = (dist_push.get("lark_chat_id") or "").strip()
                elif dist_push.get("lark_chat_id") and dist_push.get("lark_chat_id") != chat_id:
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

    # Step 3.6: 仪表盘与战略均完成后发送邮件
    _bi_log("接下来执行 Step 3.6 发送 BI 战报邮件（一、BI 数据快报 → 二、仪表盘 → 三、Lark 同步 → 四、战略分析）", progress=True)

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
        _bi_log("Step 3.6: 发送 BI 战报邮件（数据快报 + 仪表盘 + Lark 同步 + 战略分析）...", progress=True)
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
<h3>三、Lark 多维表同步结果</h3>
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
                    email_durl = (_DASHBOARD_DISPLAY_URLS.get(dname) or durl or "").strip()
                    link_html = (
                        f'<a href="{html.escape(email_durl)}">打开仪表盘</a>'
                        if email_durl and not email_durl.startswith("${")
                        else ""
                    )
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
<h3>二、仪表盘统计图与分析</h3>
<p>以下为 Lark 多维表格各仪表盘及 LLM 分析结果（与流程执行顺序一致：先于战略大战报）。</p>
{"".join(dashboard_section_parts)}"""

                kpi_section = ""
                try:
                    kpi_md = _build_bi_kpi_snapshot_markdown(output_dir, report_date)
                    if (kpi_md or "").strip():
                        kpi_inner = _kpi_snapshot_md_to_email_html(kpi_md)
                        kpi_section = f"""
<h3>一、BI 数据快报</h3>
<p style="color:#666; font-size:13px;">与 Step 3.4 推送至 Lark 的「📊 BI 数据快报」卡片同源（output 目录提纯 CSV）。</p>
<div style="background:#fafafa; padding:16px; border-radius:8px; border-left:4px solid #52c41a;">
{kpi_inner}
</div>"""
                    else:
                        kpi_section = """
<h3>一、BI 数据快报</h3>
<p style="color:#999;">（内容为空，请确认 output 下提纯 CSV 已生成）</p>"""
                except Exception as _kpi_e:
                    kpi_section = f"""
<h3>一、BI 数据快报</h3>
<p style="color:#c00;">生成失败：{html.escape(str(_kpi_e))}</p>"""

                body = f"""<html><body style="font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; font-size:14px; line-height:1.6; color:#333;">
<div style="background:#e6f7ff; padding:12px 16px; border-radius:8px; margin-bottom:16px; border-left:4px solid #1890ff;">
<p style="margin:0 0 8px 0; font-weight:600;">本邮件由 jachin 系统自动发送</p>
<p style="margin:0; color:#c41d7f; font-size:13px;">⚠ 注意将此账号放入白名单，以防被当垃圾邮件误删！</p>
</div>
<h2 style="color:#1890ff;">📊 BI 每日战报 ({report_date})</h2>
{kpi_section}
{dashboard_section}
{lark_section}
<h3>四、战略深度分析</h3>
<div style="background:#f5f5f5; padding:16px; border-radius:8px; white-space: pre-wrap;">{strategic_html}</div>

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

    流程: 1) 检查 DuckDB 今日数据 2) 无则 SPA 抓取+ingest 3) 提纯输出 CSV 4) 同步 Lark 多维表 5) 仪表盘分析并推送 Lark 6) 战略大战报并推送 Lark 7) 邮件通知（含与 Lark 同源的 BI 数据快报 + 仪表盘 + 同步摘要 + 战略分析）
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
