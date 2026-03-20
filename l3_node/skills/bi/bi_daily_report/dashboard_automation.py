"""
仪表盘分析 + Lark 自动化发送配置

在数据同步到 Lark 多维表格后，对每个仪表盘：
1. 基于对应 CSV 调用 LLM 生成图表级小分析
2. 保存分析到 output 目录
3. 通过 Playwright 打开 Lark 仪表盘，点击「设置自动化发送」→「更多配置」→ 填入分析、设置定时时间 →「保存并启用」

依赖：Chrome 调试模式已启动且已登录 Lark/飞书。
"""
from __future__ import annotations

import csv as csv_module
import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 仪表盘名称 → 图表列表（图表名, 对应 CSV）
_DASHBOARD_CHARTS: dict[str, list[tuple[str, str]]] = {
    "仪表盘_用户登录活跃情况": [
        ("DAU和DNU", "01_用户活跃_增幅表.csv"),
        ("DAU渠道来源", "03a_用户活跃_DAU渠道来源.csv"),
        ("周统计DAU和DNU数量", "02_用户活跃_日期数量表.csv"),
        ("DNU渠道来源", "03b_用户活跃_DNU渠道来源.csv"),
        ("新增设备数、增幅、占比", "13_用户活跃_新增设备表.csv"),
    ],
    "仪表盘_平台留存情况": [
        ("次留表", "04_留存_次留表.csv"),
        ("周环比", "06_留存_周环比表.csv"),
        ("付费用户次留表", "05_留存_付费用户次留表.csv"),
        ("付费用户周环比", "07_留存_付费用户周环比表.csv"),
    ],
    "仪表盘_平台消耗情况": [
        ("每日金币产出、消耗", "08_消耗_每日表.csv"),
        ("每个游戏的产出、消耗", "09_消耗_按游戏表.csv"),
        ("付费人数表格", "10_充值_付费人数按SKU.csv"),
        ("付费金额表格", "11_充值_付费金额按SKU.csv"),
        ("付费人数、金额、增幅", "14_充值_付费人数金额增幅表.csv"),
        ("ARPU", "15_充值_ARPU表.csv"),
        ("ARPPU", "16_充值_ARPPU表.csv"),
    ],
}

_DASHBOARD_SYSTEM_PROMPT = """你是 BI 数据分析师。用户将提供某个仪表盘下多个图表的 CSV 数据摘要。
请用 2～5 句话给出该仪表盘的整体洞察，聚焦：趋势、异常、建议。
输出直接可粘贴到 Lark 消息内容中，无需标题，禁止 Markdown 代码块。"""


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


async def generate_dashboard_analysis_async(
    dashboard_name: str,
    output_dir: Path,
    config: dict[str, Any] | None = None,
) -> str:
    """
    为指定仪表盘生成 LLM 分析，保存到 output 目录。

    Returns:
        分析文本；失败时返回降级文案
    """
    charts = _DASHBOARD_CHARTS.get(dashboard_name, [])
    if not charts:
        return f"仪表盘 {dashboard_name} 未配置图表映射"

    csv_files = [c[1] for c in charts]
    csv_summary = _load_csv_summary_for_dashboard(output_dir, csv_files)
    if csv_summary == "（无数据）":
        return f"仪表盘 {dashboard_name}：当前无对应 CSV 数据"

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
