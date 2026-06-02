"""
仪表盘分析 + Lark 自动化发送配置

在数据同步到 Lark 多维表格后，对每个仪表盘：
1. 优先从 Lark 多维表（同 base 下各子表）拉取数据；若无则从 output 目录 CSV 读取
2. 调用 LLM 生成图表级小分析
3. 保存分析到 output 目录
4. 通过 Playwright 打开 Lark 仪表盘，点击「设置自动化发送」→「更多配置」→ 填入分析、设置定时时间 →「保存并启用」

依赖：Chrome 调试模式已启动且已登录 Lark/飞书。
"""
from __future__ import annotations

import csv as csv_module
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _get_bi_raw_dir() -> Path:
    """延迟导入避免循环依赖"""
    from l3_node.primitives.mcp.mcp_tools.bi.paths import get_bi_raw_dir
    return get_bi_raw_dir()


# 仪表盘名称 → 图表列表（图表名, 对应 CSV）
_DASHBOARD_CHARTS: dict[str, list[tuple[str, str]]] = {
    "仪表盘_用户登录活跃情况": [
        ("DAU和DNU", "01_用户活跃_增幅表.csv"),
        ("DAU渠道来源", "03a_用户活跃_DAU渠道来源.csv"),
        ("周统计DAU和DNU数量", "02_用户活跃_日期数量表.csv"),
        ("DNU渠道来源", "03b_用户活跃_DNU渠道来源.csv"),
        ("日活占比", "12_用户活跃_日活占比.csv"),
        ("新增设备数", "13_用户活跃_新增设备表.csv"),
    ],
    "仪表盘_平台留存情况": [
        ("次留表", "04_留存_次留表.csv"),
        ("周环比", "06_留存_周环比表.csv"),
        ("付费用户次留表", "05_留存_付费用户次留表.csv"),
        ("付费用户周环比", "07_留存_付费用户周环比表.csv"),
    ],
    "仪表盘_平台消耗情况": [
        ("当日金币产出、消耗（全）", "08_消耗_每日表.csv"),
        ("当日金币产出、消耗（渠道层）", "08b_消耗_金币_渠道层.csv"),
        ("每个游戏的产出、消耗", "09_消耗_按游戏表.csv"),
        ("付费人数表格（不同充值金人数）", "10_充值_付费人数按SKU.csv"),
        ("付费人数表格（不同充值金金额）", "11_充值_付费金额按SKU.csv"),
        ("付费人数、金额、增幅", "14_充值_付费人数金额增幅表.csv"),
        ("Arpu", "15_消耗_Arup表.csv"),
        ("Arppu", "16_消耗_Arppu表.csv"),
    ],
    "仪表盘_游戏情况": [
        ("完成游戏局数", "17_游戏_完成局数.csv"),
        ("用户获胜", "18_游戏_用户获胜.csv"),
        ("GameRTP、GGR", "19_游戏_RTP_GGR.csv"),
    ],
}

_DASHBOARD_SYSTEM_PROMPT = """你是 BI 增长分析官。用户将提供某个仪表盘下多个图表的数据摘要。

请用 **3～5 句口语化中文** 给出洞察，优先回答：
1. DAU/DNU 或留存有没有明显变化？
2. 哪个渠道或环节最值得盯？
3. 一条可执行建议。

严格依据数据，勿臆测；禁止 Markdown 代码块与论文腔。"""


# 仪表盘 CSV 与 raw slug 映射
_DASHBOARD_CSV_TO_RAW: dict[str, str] = {
    "01_用户活跃_增幅表.csv": "stats_user_dau",
    "02_用户活跃_日期数量表.csv": "stats_user_dau",
    "03a_用户活跃_DAU渠道来源.csv": "stats_user_dau",
    "03b_用户活跃_DNU渠道来源.csv": "stats_user_new",
    "12_用户活跃_日活占比.csv": "stats_user_new",
    "13_用户活跃_新增设备表.csv": "stats_user_new",
    "04_留存_次留表.csv": "stats_retention_user",
    "05_留存_付费用户次留表.csv": "stats_retention_paid",
    "06_留存_周环比表.csv": "stats_retention_user_compare",
    "07_留存_付费用户周环比表.csv": "stats_retention_paid_compare",
    "08_消耗_每日表.csv": "prod_sales",
    "08b_消耗_金币_渠道层.csv": "stats_user_dau",
    "09_消耗_按游戏表.csv": "prod_sales",
    "10_充值_付费人数按SKU.csv": "recharge_status",
    "11_充值_付费金额按SKU.csv": "recharge_status",
    "14_充值_付费人数金额增幅表.csv": "stats_recharge",
    "15_消耗_Arup表.csv": "daily_ops_summary",
    "16_消耗_Arppu表.csv": "daily_ops_summary",
    "17_游戏_完成局数.csv": "stats_game_core",
    "18_游戏_用户获胜.csv": "stats_game_core",
    "19_游戏_RTP_GGR.csv": "stats_game_daily",
}


def _load_csv_summary_for_dashboard(output_dir: Path, csv_files: list[str]) -> str:
    """从 output 目录读取多个 CSV 的摘要（前几行），供 LLM 分析"""
    lines: list[str] = []
    for csv_name in csv_files:
        p = output_dir / csv_name
        if not p.exists():
            continue
        try:
            with open(p, encoding="utf-8-sig") as f:
                reader = csv_module.DictReader(f)
                rows = list(reader)[:8]
            if rows:
                cols = list(rows[0].keys())
                lines.append(f"\n### {csv_name}")
                for r in rows[:5]:
                    line = " | ".join(f"{k}: {r.get(k, '')}" for k in cols[:5])
                    lines.append(f"  {line}")
        except Exception as e:
            logger.debug("[Dashboard] 读取 %s 失败: %s", csv_name, e)
    return "\n".join(lines) if lines else "（无数据）"


def _load_raw_summary_for_dashboard(raw_dir: Path, csv_files: list[str]) -> str:
    """从 raw 目录 CSV 读取仪表盘所需数据摘要（替代 Lark 多维表）"""
    lines: list[str] = []
    seen_slugs: set[str] = set()
    for csv_name in csv_files:
        slug = _DASHBOARD_CSV_TO_RAW.get(csv_name)
        if not slug or slug in seen_slugs:
            continue
        p = raw_dir / f"{slug}.csv"
        if not p.exists():
            continue
        seen_slugs.add(slug)
        try:
            with open(p, encoding="utf-8-sig") as f:
                reader = csv_module.DictReader(f)
                rows = list(reader)[:8]
            if rows:
                cols = list(rows[0].keys())
                lines.append(f"\n### {csv_name}（raw/{slug}.csv）")
                for r in rows[:5]:
                    line = " | ".join(f"{k}: {str(r.get(k, ''))[:30]}" for k in cols[:5])
                    lines.append(f"  {line}")
        except Exception as e:
            logger.debug("[Dashboard] 读取 raw %s 失败: %s", slug, e)
    return "\n".join(lines) if lines else "（无数据）"


def _lark_bitable_list_params(
    lark_config: dict[str, Any], csv_filename: str, page_token: str | None
) -> dict[str, Any]:
    params: dict[str, Any] = {"page_size": 100}
    if page_token:
        params["page_token"] = page_token
    vm = lark_config.get("list_records_view_by_csv") or {}
    vid = str(vm.get(csv_filename) or "").strip()
    if vid:
        params["view_id"] = vid
    return params


def _cell_to_str(val: Any) -> str:
    """将 Lark 多维表 cell 值转为可读字符串（支持日期、多选等格式）"""
    if val is None:
        return ""
    if isinstance(val, dict):
        # 日期: {"type":"date","date":"2026-03-20"} 或 timestamp
        if val.get("type") == "date":
            return str(val.get("date", val.get("timestamp", "")))
        return str(val)
    return str(val)


def _fetch_bitable_summary_for_dashboard(
    lark_config: dict[str, Any],
    charts: list[tuple[str, str]],
) -> str:
    """从 Lark 多维表拉取数据摘要，供 LLM 分析。charts: [(图表名, csv_filename), ...]"""
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
    except ImportError as e:
        logger.debug("[Dashboard] Lark 依赖未就绪: %s", e)
        return "（无配置）"

    try:
        token = get_tenant_access_token()
        api_base = get_lark_api_base()
    except Exception as e:
        logger.warning("[Dashboard] Lark token 获取失败: %s", e)
        return "（无配置）"

    lines: list[str] = []
    for _chart_name, csv_name in charts:
        table_id = (tables_map.get(csv_name) or "").strip()
        if not table_id or table_id.startswith("${"):
            continue
        try:
            records: list[dict] = []
            page_token = None
            while True:
                url = f"{api_base}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
                params = _lark_bitable_list_params(lark_config, csv_name, page_token)
                resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=15)
                data = resp.json()
                if data.get("code") != 0:
                    logger.debug("[Dashboard] Lark 表 %s 拉取失败: %s", csv_name, data.get("msg", data))
                    break
                items = data.get("data", {}).get("items", [])
                records.extend(items)
                page_token = data.get("data", {}).get("page_token")
                if not page_token or not items:
                    break
            if records:
                # 取前 5 条，格式与 CSV 摘要一致
                cols: list[str] = []
                for r in records[:1]:
                    fields = r.get("fields", {})
                    cols = list(fields.keys())[:8]
                    break
                if not cols and records:
                    fields = records[0].get("fields", {})
                    cols = list(fields.keys())[:8]
                lines.append(f"\n### {csv_name}（Lark 多维表）")
                for r in records[:5]:
                    fields = r.get("fields", {})
                    line = " | ".join(f"{k}: {_cell_to_str(fields.get(k))}" for k in cols[:5])
                    lines.append(f"  {line}")
        except Exception as e:
            logger.debug("[Dashboard] Lark 表 %s 拉取异常: %s", csv_name, e)
    return "\n".join(lines) if lines else "（无数据）"


async def generate_dashboard_analysis_async(
    dashboard_name: str,
    output_dir: Path,
    config: dict[str, Any] | None = None,
) -> str:
    """
    为指定仪表盘生成 LLM 分析，保存到 output 目录。

    数据来源优先级：Lark 多维表 > 本地 output CSV。
    配置 lark_bitable（app_token、tables、app_id、app_secret）时，从多维表拉取最新数据。

    Returns:
        分析文本；失败时返回降级文案
    """
    charts = _DASHBOARD_CHARTS.get(dashboard_name, [])
    if not charts:
        return f"仪表盘 {dashboard_name} 未配置图表映射"

    csv_files = [c[1] for c in charts]
    csv_summary = "（无数据）"

    # analysis_data_source=raw 时从 raw CSV 读取；否则优先 Lark 多维表，无则 output CSV
    analysis_src = (str((config or {}).get("analysis_data_source") or "lark")).strip().lower()
    raw_dir: Path | None = None
    if analysis_src == "raw":
        raw_dir_cfg = ((config or {}).get("storage") or {}).get("analysis_raw_dir") or ""
        raw_dir = Path(raw_dir_cfg) if raw_dir_cfg and str(raw_dir_cfg).strip() else _get_bi_raw_dir()
    if raw_dir and raw_dir.exists():
        csv_summary = _load_raw_summary_for_dashboard(raw_dir, csv_files)
        if csv_summary not in ("（无数据）",):
            logger.info("[Dashboard] 使用 raw 目录 CSV 数据: %s", dashboard_name)
    if csv_summary == "（无数据）" and analysis_src != "raw":
        lark_cfg = (config or {}).get("lark_bitable") or {}
        if lark_cfg.get("enabled", True) and lark_cfg.get("app_token"):
            lark_summary = _fetch_bitable_summary_for_dashboard(lark_cfg, charts)
            if lark_summary not in ("（无数据）", "（无配置）"):
                csv_summary = lark_summary
                logger.info("[Dashboard] 使用 Lark 多维表数据: %s", dashboard_name)
    if csv_summary == "（无数据）":
        csv_summary = _load_csv_summary_for_dashboard(output_dir, csv_files)

    if csv_summary == "（无数据）":
        return f"仪表盘 {dashboard_name}：当前无对应数据（Lark 多维表或本地 CSV 均无）"

    user_prompt = f"""仪表盘：{dashboard_name}
图表及数据摘要：
{csv_summary}

请输出该仪表盘的整体洞察（2～5 句，可粘贴到 Lark 消息）。"""

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
            logger.warning("[Dashboard] 无法创建 LLM 引擎: %s", e)

    if not engine:
        return f"仪表盘 {dashboard_name}：LLM 未就绪，请配置 API Key"

    try:
        messages = [
            {"role": "system", "content": _DASHBOARD_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        result = await engine.generate_response(
            messages,
            temperature=0.3,
            max_tokens=400,
            extra_body={"enable_thinking": False},
        )
        if isinstance(result, dict):
            text = result.get("content", "")
        else:
            text = (result or "").strip()
        if not text:
            return f"仪表盘 {dashboard_name}：LLM 返回空"

        # 去除可能的 Markdown 代码块
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
        return text.strip()
    except Exception as e:
        logger.exception("[Dashboard] LLM 调用失败: %s", e)
        return f"仪表盘 {dashboard_name}：分析生成失败 ({e})"


def _save_analysis_to_file(output_dir: Path, dashboard_name: str, analysis_text: str) -> Path:
    """将分析保存为 MD 文档到 output 目录（如 output/统计分析）"""
    safe_name = re.sub(r"[^\w\u4e00-\u9fff-]", "_", dashboard_name)
    fpath = output_dir / f"仪表盘分析_{safe_name}.md"
    fpath.write_text(analysis_text, encoding="utf-8")
    return fpath


def setup_lark_dashboard_automation_via_browser(
    dashboard_url: str,
    analysis_text: str,
    scheduled_time: str,
    cdp_url: str,
    timeout_sec: int = 90,
) -> dict[str, Any]:
    """
    通过 Playwright 打开 Lark 仪表盘，配置自动化发送并填入分析内容。

    流程：打开 URL → 点击「设置自动化发送」→ 点击「更多配置」→ 填入内容、设置时间 → 点击「保存并启用」

    cdp_url 留空时：自动启动 Chromium，使用持久化配置目录 ~/.jachin/lark_automation_browser 保存登录态，
    首次运行会弹出浏览器需手动登录 Lark，之后复用会话。cdp_url 非空时连接已有 Chrome（--remote-debugging-port=9222）。

    Args:
        dashboard_url: 仪表盘完整 URL
        analysis_text: 要填入消息内容的分析文本
        scheduled_time: 定时发送时间，如 "18:05"
        cdp_url: Chrome 调试地址，留空则自动启动浏览器
        timeout_sec: 操作超时秒数

    Returns:
        {"status": "success"} 或 {"status": "error", "error": "..."}
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"status": "error", "error": "playwright 未安装，请执行 pip install playwright && playwright install chromium"}

    use_cdp = bool((cdp_url or "").strip())
    try:
        with sync_playwright() as pw:
            if use_cdp:
                cdp = (cdp_url or "http://127.0.0.1:9222").rstrip("/")
                browser = pw.chromium.connect_over_cdp(cdp, timeout=5000)
                contexts = browser.contexts
                if not contexts:
                    return {"status": "error", "error": "未找到浏览器上下文，请确保 Chrome 以 --remote-debugging-port=9222 启动"}
                context = contexts[0]
                pages = context.pages
                page = pages[0] if pages else context.new_page()
            else:
                # 自动启动浏览器，使用持久化配置保存 Lark 登录态（首次需手动登录）
                user_data_dir = Path.home() / ".jachin" / "lark_automation_browser"
                user_data_dir.mkdir(parents=True, exist_ok=True)
                context = pw.chromium.launch_persistent_context(
                    user_data_dir=str(user_data_dir),
                    headless=False,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                page = context.pages[0] if context.pages else context.new_page()

            page.bring_to_front()
            page.goto(dashboard_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_load_state("load", timeout=15000)
            page.wait_for_timeout(5000)

            # 1. 点击「设置自动化发送」button.base_dashboard_analysis_send_guide
            try:
                btn = page.locator('button.base_dashboard_analysis_send_guide').first
                if btn.count() == 0:
                    btn = page.locator('button:has-text("设置自动化发送")').first
                btn.click(timeout=10000)
            except Exception as e:
                return {"status": "error", "error": f"未找到「设置自动化发送」按钮: {e}"}
            page.wait_for_timeout(2000)

            # 2. 点击「更多配置」button.bitable-automation-simple-reminder-config-panel__footer-more
            try:
                more_btn = page.locator('button.bitable-automation-simple-reminder-config-panel__footer-more').first
                if more_btn.count() == 0:
                    more_btn = page.locator('button:has-text("更多配置")').first
                if more_btn.count() == 0:
                    more_btn = page.locator('text=更多配置').first
                more_btn.click(timeout=8000)
            except Exception as e:
                return {"status": "error", "error": f"未找到「更多配置」: {e}"}
            page.wait_for_timeout(3000)

            # 3. 设置定时时间 input.bitable-701dd3__time-picker-input（placeholder hh:mm）
            time_parts = scheduled_time.strip().split(":")
            hour = time_parts[0] if len(time_parts) >= 1 else "18"
            minute = time_parts[1] if len(time_parts) >= 2 else "05"
            time_val = f"{hour.zfill(2)}:{minute.zfill(2)}"
            time_filled = False
            for sel in [
                'input.bitable-701dd3__time-picker-input',
                'input[placeholder="hh:mm"]',
                'input[type="time"]',
                '[class*="time-picker"] input',
                'input[placeholder*="时间"]',
            ]:
                try:
                    inp = page.locator(sel).first
                    if inp.count() > 0:
                        inp.fill(time_val)
                        time_filled = True
                        break
                except Exception:
                    continue
            page.wait_for_timeout(1500)

            # 4. 填入内容 div.bitable-automation-tag-editor__editor-comp[contenteditable="true"]
            content_selectors = [
                'div.bitable-automation-tag-editor__editor-comp[contenteditable="true"]',
                'div.editor-kit-container.bitable-automation-tag-editor__editor-comp',
                'div[contenteditable="true"][data-slate-editor="true"]',
                'div[contenteditable="true"]',
                '.ProseMirror',
            ]
            filled = False
            for sel in content_selectors:
                try:
                    el = page.locator(sel).first
                    if el.count() > 0:
                        el.click()
                        el.press("Control+a")
                        el.pressSequentially(analysis_text[:2000], delay=5)
                        filled = True
                        break
                except Exception:
                    continue
            if not filled:
                try:
                    editable = page.locator('[contenteditable="true"]').first
                    if editable.count() > 0:
                        editable.click()
                        page.keyboard.press("Control+a")
                        page.keyboard.type(analysis_text[:2000], delay=10)
                        filled = True
                except Exception:
                    pass
            if not filled:
                logger.warning("[Dashboard] 未能填入内容，尝试继续")
            page.wait_for_timeout(1000)

            # 5. 点击「保存并启用」或「保存」按钮（不同仪表盘弹窗文案可能不同，多 selector 容错）
            save_timeout_ms = 10000
            try:
                save_btn = page.locator('button.ud__button--filled.ud__button--filled-default:has-text("保存并启用")').first
                if save_btn.count() == 0:
                    save_btn = page.locator('button:has-text("保存并启用")').first
                if save_btn.count() == 0:
                    save_btn = page.locator('button:has-text("保存")').first
                if save_btn.count() == 0:
                    save_btn = page.locator('button:has-text("确定")').first
                if save_btn.count() == 0:
                    save_btn = page.locator('[class*="ud__button"]:has-text("保存")').first
                if save_btn.count() == 0:
                    save_btn = page.locator('button.ud__button--filled:has-text("保存")').first
                save_btn.click(timeout=save_timeout_ms)
            except Exception as e:
                return {"status": "error", "error": f"未找到「保存并启用」按钮: {e}\n提示：部分仪表盘弹窗结构不同，可手动在该仪表盘内点击保存后重试。"}
            page.wait_for_timeout(2000)

            return {"status": "success", "msg": "仪表盘自动化已配置"}
    except Exception as e:
        err = str(e)
        if "connect" in err.lower() or "Target" in err:
            return {"status": "error", "error": f"{err}\n提示：请用 chrome.exe --remote-debugging-port=9222 启动 Chrome"}
        return {"status": "error", "error": err}


def get_dashboard_names() -> list[str]:
    """返回已配置的仪表盘名称列表"""
    return list(_DASHBOARD_CHARTS.keys())
