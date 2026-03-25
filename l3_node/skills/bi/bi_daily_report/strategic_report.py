"""
BI 闪电战报 — 1-3-3 闪电战报法则（首席商业决策智库 CSO）

专供 CEO 在手机端首屏阅读，基于 Lark 多维表数据生成《闪电战报 (Blitz Report)》：
1 句核心战略论断 | 3 条统帅决策行动批示 | 3 组支撑论断的致命数据点。
铁律：≤400 字、剔除推理与名词解释、动作可执行（老板可回复「同意，去执行」）。
诊断规则：反作弊与渠道验真、核心控盘逻辑(RTP)、经济通胀与阶级固化。

数据来源：优先从 Lark 多维表（同 base 下各子表）拉取；无配置时从 output CSV 读取。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _get_bi_raw_dir() -> Path:
    """延迟导入避免循环依赖"""
    from l3_node.mcp_tools.bi.paths import get_bi_raw_dir
    return get_bi_raw_dir()

# 战略分析所需多维表子集（与 _load_csv_summary 的 key_files 对应）
_STRATEGIC_KEY_FILES = [
    "01_用户活跃_增幅表.csv",
    "03a_用户活跃_DAU渠道来源.csv",
    "03b_用户活跃_DNU渠道来源.csv",
    "13_用户活跃_新增设备表.csv",
    "04_留存_次留表.csv",
    "08_消耗_每日表.csv",
    "09_消耗_按游戏表.csv",
    "10_充值_付费人数按SKU.csv",
    "11_充值_付费金额按SKU.csv",
]


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


def _extract_blitz_report(raw: str) -> str:
    """仅保留 1-3-3 闪电战报块，剔除旧格式。若含多条战报，取最后一条。"""
    # 兼容新旧格式：🚨 核心战略论断 或 🚨 Jachin OS
    start = raw.rfind("🚨 核心战略论断")
    if start < 0:
        start = raw.rfind("🚨 Jachin OS")
    if start < 0:
        return raw.strip()
    return raw[start:].strip()


def _to_json_serializable(obj: Any) -> Any:
    """递归将 date/datetime 转为 ISO 字符串，供 json.dumps 使用"""
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _to_json_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_json_serializable(v) for v in obj]
    return obj

# 首席商业决策智库 (CSO) — 1-3-3 闪电战报 System Prompt
_SYSTEM_PROMPT = """# Role: 首席商业决策智库 (CSO) & Jachin OS 战略引擎
你的任务是深度透析每日运营与产销汇总数据，跨越冰冷的表格，直接向 CEO 输出致命的商业异动与战略决策。你极度精通 RMG (真金博彩) 游戏生态、黑产防范及虚拟经济学。

# 核心战法与异动判定规则 (Diagnostic Rules)
1. 【反作弊与渠道验真】：
   - 重点监控 `日活(DAU)`、`当日新增用户(DNU)` 与 `当日新增设备数` 的差值。若 DNU 激增但新设备数平缓，或 `注册漏斗` 中"注册人数"极高但"进桌人数"极低，判定为黑产刷号。
   - 警惕 `Meta_ads` 等买量渠道的假量注入，结合 `CPR` 与 `CPP` 评估买量健康度。
2. 【核心控盘逻辑 (RTP 警戒线)】：
   - 永远分离 `参与用户数` 与 `参与机器人数` 的经济流转。
   - 严密监控 `用户回报率RTP`，精度校验至万分位。若个别游戏（如 Mines, Tongits）RTP 异常飙高，判定为赔率漏洞或薅羊毛事故。
   - 极度关注 `充值用户回报率RTP`：若该值显著低于大盘 RTP，表明系统在过度"杀大 R"，极易导致金主骤性流失；若高于 100%，则平台处于倒贴状态。
3. 【经济通胀与阶级固化】：
   - 监控 `用户金币总资产变动(产-消)`。若持续为正大数，说明处于通胀期。
   - 交叉比对 `当日老用户金币产出/消耗` 与 `当日新增用户金币产出/消耗`。若老用户资产呈滚雪球式正向变动，说明底层资源获取过于容易，必须下发开启"消耗型活动"的指令。

# 强制输出格式 (1-3-3 闪电战报法则)
不要输出任何计算过程与名词解释。严格按照以下格式输出，总字数必须控制在 400 字以内：

🚨 核心战略论断
(一句话定性当前大盘健康度。例如：安全 / 通胀预警 / 渠道假量预警 / RTP击穿警告)

⚡ 统帅决策行动批示 (3条立刻执行的明确指令)
1. 研发/风控：(如：封禁异常设备、热更修复某游戏赔率)
2. 市场/运营：(如：熔断某链接买量、开启老客金币回收活动)
3. 经济宏观调控：(如：调整充值用户胜率权重，拉长破产周期)

🩸 支撑论断的致命数据点
- 风控盲区：(指出 DNU 与设备数、进桌数的异常断层)
- 控盘红线：(指出特定玩法或充值用户的 RTP 异常偏离点)
- 资产失衡：(指出金币总资产产消差的极端变化)"""


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
        from l3_node.skills.bi.bi_daily_report.main_skill import (
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
_USER_PROMPT_TEMPLATE = """核心数据：
```json
{metrics_json}
```
{csv_summary_section}

请基于以上数据，严格按照 System Prompt 中的 1-3-3 闪电战报法则输出。日期用 {report_date_mmdd}（数据所属日）。总字数≤400。"""


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
    "03a_用户活跃_DAU渠道来源.csv": ["stats_user_dau"],
    "03b_用户活跃_DNU渠道来源.csv": ["stats_user_new"],
    "13_用户活跃_新增设备表.csv": ["stats_user_dau", "stats_user_new", "daily_ops_summary"],
    "04_留存_次留表.csv": ["stats_retention_user"],
    "08_消耗_每日表.csv": ["prod_sales"],
    "09_消耗_按游戏表.csv": ["prod_sales"],
    "10_充值_付费人数按SKU.csv": ["recharge_status"],
    "11_充值_付费金额按SKU.csv": ["recharge_status"],
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
    lines: list[str] = []
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
    return "\n".join(lines) if lines else "（无 raw 数据）"


def _build_user_prompt(metrics: dict[str, Any], data_summary: str, report_date_mmdd: str = "") -> str:
    # 仅传递中文 key 的 core 指标，避免 LLM 输出英文变量名
    core = {k: v for k, v in (metrics or {}).items() if not k.startswith("_")}
    metrics_cn = _metrics_to_chinese(core)
    metrics_clean = _to_json_serializable(metrics_cn)
    metrics_json = json.dumps(metrics_clean, ensure_ascii=False, indent=2)
    data_section = ""
    if data_summary and data_summary not in ("（无 CSV 摘要）", "（无数据）", "（无配置）"):
        data_section = "\n多维表 / CSV 数据摘要：\n" + data_summary
    if not report_date_mmdd:
        report_date_mmdd = (datetime.now() - timedelta(days=1)).strftime("%m/%d")
    return _USER_PROMPT_TEMPLATE.format(
        metrics_json=metrics_json,
        csv_summary_section=data_section,
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

    # 数据日期：raw 模式从 raw CSV 推断；lark 模式不读本地 raw，默认昨日
    date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    data_summary = ""
    report_date_mmdd = ""
    analysis_src = (cfg.get("analysis_data_source") or "raw").strip().lower()
    raw_dir: Path | None = None
    if analysis_src == "raw":
        raw_dir_cfg = (cfg.get("storage") or {}).get("analysis_raw_dir") or ""
        raw_dir = Path(raw_dir_cfg) if raw_dir_cfg and str(raw_dir_cfg).strip() else _get_bi_raw_dir()
        raw_dir = raw_dir.expanduser().resolve()
    if analysis_src == "raw" and raw_dir and raw_dir.exists():
        data_summary = _load_raw_strategic_summary(raw_dir)
        if data_summary not in ("（无 raw 目录）", "（无 raw 数据）"):
            report_date_iso, report_date_mmdd = _detect_report_date_from_raw(raw_dir)
            date_str = report_date_iso
            logger.info("[Strategic] 使用 raw 目录 CSV 数据，数据日期 %s", report_date_mmdd)
    if metrics is None:
        try:
            from l3_node.mcp_tools.bi.metrics.engine import run as run_metrics
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

    if not data_summary and analysis_src != "raw":
        lark_cfg = cfg.get("lark_bitable") or {}
        if lark_cfg.get("enabled", True) and lark_cfg.get("app_token"):
            lark_summary = _fetch_lark_strategic_summary(lark_cfg)
            if lark_summary not in ("（无数据）", "（无配置）"):
                data_summary = lark_summary
                logger.info("[Strategic] 使用 Lark 多维表数据")
    if not data_summary and output_dir:
        data_summary = _load_csv_summary(Path(output_dir))
    if not report_date_mmdd:
        if analysis_src == "raw":
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

    user_prompt = _build_user_prompt(metrics, data_summary, report_date_mmdd)
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
            max_tokens=800,  # 1-3-3 闪电战报 ≤400 字
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
