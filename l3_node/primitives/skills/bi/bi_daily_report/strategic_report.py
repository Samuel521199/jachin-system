"""
BI 战略大战报（长文深度分析）

分析规范以 **docs/bi_daily_report/STRATEGIC_REPORT_ANALYSIS_SPEC.md**（v4 交付形态）为 SSOT，
运行时加载为 System Prompt；缺失时回退 _STRATEGIC_ANALYSIS_SPEC_FALLBACK。

User 侧注入：bi_project 背景、T vs T-1 字段摘要、指标 JSON、CSV/Lark 摘要。

数据来源：优先 Lark 多维表；否则 raw/output CSV。仪表盘小分析（Step 4a）不在此模块。
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
    from l3_node.primitives.mcp.mcp_tools.bi.paths import get_bi_raw_dir
    return get_bi_raw_dir()

# 战略分析所需多维表子集（与 _load_csv_summary / 日环比 / Lark 拉取一致）
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


def _finalize_strategic_report_output(raw: str) -> str:
    """保留模型全文输出；仅去空白与偶发的整段 ``` 代码围栏包裹。"""
    text = (raw or "").strip()
    if not text:
        return text
    if text.startswith("```"):
        lines = text.split("\n")
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
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
_STRATEGIC_ANALYSIS_SPEC_FALLBACK = """# BI 战略战报（兜底规范）

未加载到 `docs/bi_daily_report/STRATEGIC_REPORT_ANALYSIS_SPEC.md`。请按 User 中 bi_project + T vs T-1 + JSON/CSV 输出 **长文 Markdown**（勿单一归因），须含：

# 标题（含数据日）
## 一、执行摘要（须含：菲律宾发薪窗与 Petsa de Peligro 判断；IAP 弱时追问 IAA 兜底；付费跌+RTP/产销异常时提死亡螺旋假设）
## 二、红榜与黑榜（各 2 项；黑榜成对串联相关异动）
## 三、分维度数据解读
## 四、重点异动归因树（≤5 条；含「网赚双轨与死亡螺旋」段；无 IAA 数据须写明强需求）
## 五、跨部门行动清单
## 六、待澄清（首条优先：补 IAA/激励视频/eCPM 等）

建议总篇幅约 1200～3500 汉字。禁止编造；缺数据写「未提供」。"""


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
比对：**T = {dod_t1}**，**T-1 = {dod_t2}**
{dod_summary_section}

---

## 核心指标 JSON（引擎/同环比）
```json
{metrics_json}
```

## 多维表 / CSV 数据摘要（与上表互补）
{csv_summary_section}

请**先**结合「背景知识」与「T vs T-1 摘要」理解异动，再综合下方 JSON 与摘要，**严格遵循 System 中的《战略战报分析规范》v4 交付形态**（长文 Markdown：执行摘要、红黑榜、分维度解读、归因树、行动清单、待澄清；**不要**再用固定 🚨/⚡/🩸 三段子作为主结构）。**若 System 含「战报输出美学」追加节，排版（引用块摘要、涨跌符号与颜色、维度 emoji、`---` 分隔、行动项标签、表名行内代码）必须一并遵守。** 日期用 {report_date_mmdd}（数据所属日）。篇幅以说清为准（建议约 1200～3500 汉字）。"""


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
    "02_用户活跃_日期数量表.csv": ["stats_user_dau", "stats_user_new"],
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
    return _USER_PROMPT_TEMPLATE.format(
        k11_context_section=k11_sec,
        dod_summary_section=dod_sec,
        dod_t1=dt1,
        dod_t2=dt2,
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

    k11_md = str(sr_cfg.get("_k11_project_context_md") or "").strip()
    dod_txt = str(sr_cfg.get("_strategic_dod_summary") or "").strip()
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

    try:
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ]
        result = await engine.generate_response(
            messages,
            temperature=0.2,
            max_tokens=8192,
        )
        if isinstance(result, dict):
            text = result.get("content", "")
        else:
            text = (result or "").strip()
        if not text:
            return "LLM 返回空内容"
        return _finalize_strategic_report_output(text)
    except Exception as e:
        logger.exception("[Strategic] LLM 调用失败: %s", e)
        return f"""# 📊 BI 战略分析（生成失败）

LLM 调用异常: {e}

请检查网络与 API Key 后重试。"""
