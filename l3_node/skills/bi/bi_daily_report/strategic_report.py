"""
BI 闪电战报 — 1-3-1-3-1 终极法则（手机端首屏极致脱水）

专供 C-Level 高管在手机端首屏阅读，基于 DuckDB 核心指标生成《闪电战报 (Blitz Report)》。
输出格式：1 句定性 | 3 项紧急行动 | 1 行极简看板 | 3 组致命异动 | 1 句战略前瞻。
铁律：≤300 字、剔除推理、动作可执行（老板可回复「同意，去执行」）。
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_METRICS_KEY_MAP = {
    "dau": "日活", "dnu": "新增用户", "dau_pct": "日活增幅", "dnu_pct": "新增用户增幅",
    "new_devices": "新增设备数", "dnu_per_device": "新增用户设备比",
    "paid_count": "付费人数", "paid_amount": "付费金额", "paid_count_pct": "付费人数增幅", "paid_amount_pct": "付费金额增幅",
    "arpu": "ARPU", "arppu": "ARPPU", "game_rounds": "游戏局数", "game_rounds_per_user": "人均局数",
    "win_count": "获胜次数", "win_rate": "胜率", "rtp": "返币率", "ggr": "GGR",
    "date": "统计日期",
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


def _extract_blitz_report(raw: str) -> str:
    """仅保留 1-3-1-3-1 闪电战报块，剔除旧格式。若含多条战报，取最后一条。"""
    start = raw.rfind("🚨 Jachin OS")  # 取最后一次出现（避免旧格式在前）
    if start < 0:
        return raw.strip()
    idx = raw.find("💡", start)
    if idx >= 0:
        line_end = raw.find("\n", idx)
        end = line_end + 1 if line_end >= 0 else len(raw)
    else:
        end = len(raw)
    return raw[start:end].strip()


def _to_json_serializable(obj: Any) -> Any:
    """递归将 date/datetime 转为 ISO 字符串，供 json.dumps 使用"""
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _to_json_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_json_serializable(v) for v in obj]
    return obj

# Jachin OS 闪电战报 — 完整 System Prompt（100% 保留排版）
_SYSTEM_PROMPT = """你现在是 Jachin OS 的最高战略分析大脑，负责为极其繁忙的 C-Level 高管撰写《闪电战报 (Blitz Report)》。数据背景为菲律宾社交游戏平台。 你必须基于传入的底层数据，严格遵守以下 3 条铁律：
绝对字数限制：总字数绝对不可超过 300 字。严禁解释基础名词概念（老板都懂）。
剔除推理过程：直接把 DuckDB 算出的数据转化为结论。禁止出现"根据数据推测"等废话。
动作必须可执行：给出的行动建议必须是动词开头，且老板可以直接回复"同意，去执行"。
🚨【绝对红线】：你的输出必须 100% 严格遵守下方的 Markdown 格式和排版间距！绝不允许把文字挤成一团，绝不允许遗漏 1. 2. 3. 序号和 * 符号！
【强制输出格式与完美范例】（请完全照搬此排版风格填入今日数据）：
🚨 Jachin OS 核心战报 (MM/DD)
🔴 大盘定性：[一句话定性，例如：流量暴涨但营收归零，极高概率遭遇支付链路崩溃及黑产攻击！]
⚡ 请您批示的 3 项紧急行动：
[技术部] (立刻熔断)：[极其具体的指令，例如：停止 Meta 投放，紧急排查全站支付接口可用性。]
[风控部] (立刻封禁)：[极其具体的指令，例如：全网封禁昨日 DNU/设备数 > 2 的异常账号，清洗白名单。]
[运营部] (立刻核查)：[极其具体的指令，例如：核对昨日 3 名付费用户的真实订单，排查 RTP 数值模型漏洞。]
📊 极简大盘看板： DAU: XXX (+xx.x%) | DNU: XXX (+xx.x%) | GGR: $X.X | GameRTP: X.XX% | 新增用户/设备数: X.XX
🩸 致命异动交叉归因：
DNU vs 设备数：比值高达 2.15。核心异动：流量虚高，已被黑产工作室批量注册刷号。
GGR vs 局数：活跃上涨但毛收为 0。核心异动：玩家有消耗但无充值，支付系统大概率中断。
付费人数 vs 金额：仅 3 人付费 30 元。核心异动：商业破冰模型完全失效。
🔭 核心战略前瞻： 💡 [1-2 句话长线建议，例如：立即全线止损！在支付链路和反作弊系统修复前，停止一切低质拉新，将剩余资源全部切向高留存老客的防流失维护。]"""


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
        from l3_node.mcp_tools.bi.data_store import _get_conn
        from l3_node.skills.bi.bi_daily_report.main_skill import _query_table, _find_col, _safe_float
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

        # 产销比、通胀预警
        for slug in ("prod_sales", "stats_game_daily"):
            rows_prod = _query_table(conn, slug, date_from=t7, date_to=t1)
            if not rows_prod:
                continue
            cols = list(rows_prod[0].keys())
            prod_col = _find_col(cols, "用户金币产出总数", "产出", "用户金币产出", "金币产出")
            cons_col = _find_col(cols, "用户金币消耗总数", "消耗", "用户金币消耗", "金币消耗")
            if prod_col and cons_col:
                total_prod = sum(_safe_float(r.get(prod_col)) for r in rows_prod)
                total_cons = sum(_safe_float(r.get(cons_col)) for r in rows_prod)
                if total_cons > 0:
                    out["dim2_fragility"]["prod_cons_ratio"] = round(total_prod / total_cons, 2)
                    out["dim2_fragility"]["inflation_risk"] = "高" if total_prod > total_cons * 1.2 else "中低"
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
            if dau_col:
                out["dim4_predictive"]["dau_series"] = [_safe_float(r.get(dau_col)) for r in rows_dau[-7:]]
            if dnu_col:
                out["dim4_predictive"]["dnu_series"] = [_safe_float(r.get(dnu_col)) for r in rows_dau[-7:]]
    finally:
        conn.close()

    return out


# User Prompt 模板：仅注入数据，格式由 System Prompt 强制
_USER_PROMPT_TEMPLATE = """核心数据：
```json
{metrics_json}
```
{csv_summary_section}

请基于以上数据，严格按照 System Prompt 中的 1-3-1-3-1 格式输出。日期用今日 MM/DD。"""


def _load_csv_summary(output_dir: Path) -> str:
    """从 output 目录的 CSV 中提取关键摘要文本，供 LLM 参考"""
    lines: list[str] = []
    key_files = [
        "01_用户活跃_增幅表.csv",
        "04_留存_次留表.csv",
        "08_消耗_每日表.csv",
        "09_消耗_按游戏表.csv",
        "10_充值_付费人数按SKU.csv",
        "11_充值_付费金额按SKU.csv",
    ]
    for name in key_files:
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


def _build_user_prompt(metrics: dict[str, Any], csv_summary: str) -> str:
    # 仅传递中文 key 的 core 指标，避免 LLM 输出英文变量名
    core = {k: v for k, v in (metrics or {}).items() if not k.startswith("_")}
    metrics_cn = _metrics_to_chinese(core)
    metrics_clean = _to_json_serializable(metrics_cn)
    metrics_json = json.dumps(metrics_clean, ensure_ascii=False, indent=2)
    csv_section = ""
    if csv_summary and csv_summary != "（无 CSV 摘要）":
        csv_section = "\nCSV 提纯数据摘要：\n" + csv_summary
    return _USER_PROMPT_TEMPLATE.format(
        metrics_json=metrics_json,
        csv_summary_section=csv_section,
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

    if metrics is None:
        try:
            from l3_node.mcp_tools.bi.metrics.engine import run as run_metrics
            date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
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

    # 补充 DuckDB 四维度战略指标（鲸鱼依赖、产销比、DAU 趋势等）
    date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    strategic_metrics = _collect_duckdb_strategic_metrics(date_str)
    metrics = dict(metrics or {})
    metrics["_strategic_duckdb"] = strategic_metrics

    csv_summary = ""
    if output_dir:
        csv_summary = _load_csv_summary(Path(output_dir))

    user_prompt = _build_user_prompt(metrics, csv_summary)
    sys_prompt = _SYSTEM_PROMPT

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

🚨 核心战略论断：数据已就绪，但 LLM 引擎不可用。请配置 DASHSCOPE_API_KEY 或 OPENAI_API_KEY 后重试。

💡 建议：检查 .env 或 L2 下发的 API Key。"""
        return fallback

    try:
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ]
        result = await engine.generate_response(
            messages,
            temperature=0.2,
            max_tokens=600,  # 1-3-1-3-1 闪电战报 ≤300 字，严格控制
        )
        if isinstance(result, dict):
            text = result.get("content", "")
        else:
            text = (result or "").strip()
        if not text:
            return "LLM 返回空内容"
        # 仅保留 1-3-1-3-1 闪电战报，剔除旧格式及括号内容
        return _extract_blitz_report(text)
    except Exception as e:
        logger.exception("[Strategic] LLM 调用失败: %s", e)
        return f"""# 📊 BI 战略分析（生成失败）

LLM 调用异常: {e}

请检查网络与 API Key 后重试。"""
