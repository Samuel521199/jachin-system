"""
BI SPA 批量抓取 — 供 scripts 与 main_skill 复用

职责：针对 bi-admin-web 侧栏菜单的批量抓取，输出 raw/*.csv。
前置：Chrome 调试模式已启动且已登录。设计: docs/bi_daily_report/09_SPA_SCRAPER_PLACEMENT_ANALYSIS.md
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
import re
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# 默认配置（可被调用方覆盖）
DEFAULT_BASE_URL = "https://bi-admin-web.heronpro.xin/#/layout/person"
DEFAULT_CDP_URL = "http://127.0.0.1:9222"
TABLE_SEL = "table:not(.el-date-table), .el-table__body-wrapper table"
MENU = ".el-menu"


def _build_leaf_actions(
    parents: list[str],
    leaf: str,
    click_query: bool = True,
    menu_sel: str = MENU,
    table_sel: str = TABLE_SEL,
) -> list[dict[str, Any]]:
    """
    构建「等待页面就绪 -> 展开父级 -> 点击叶子 -> 点查询 -> 等表格」的 action 序列。
    """
    actions: list[dict[str, Any]] = []
    actions.append({"type": "wait_visible", "selector": f"{menu_sel} >> text={parents[0]}", "timeout": 15})
    actions.append({"type": "wait_ms", "ms": 500})
    for i, p in enumerate(parents):
        actions.append({"type": "click_expand", "selector": f"{menu_sel} >> text={p}", "text": p})
        next_item = parents[i + 1] if i + 1 < len(parents) else leaf
        next_sel = f"{menu_sel} >> .el-menu-item >> text={next_item}" if next_item == p else f"{menu_sel} >> text={next_item}"
        actions.append({"type": "wait_visible", "selector": next_sel, "timeout": 5})
        actions.append({"type": "wait_ms", "ms": 400})
    leaf_sel = f"{menu_sel} >> .el-menu-item >> text={leaf}" if leaf in parents else f"{menu_sel} >> text={leaf}"
    actions.append({"type": "click", "selector": leaf_sel})
    actions.append({"type": "wait_ms", "ms": 1500})
    if click_query:
        actions.append({"type": "click_if_exists", "selector": "button:has-text('查询'), .el-button:has-text('查询')", "force": True})
        actions.append({"type": "wait_ms", "ms": 3000})
    actions.append({"type": "wait", "selector": table_sel, "timeout": 20})
    return actions


# 平台数据 + 数据统计分析 + 数据明细 下各子菜单的叶子项
MENU_ITEMS: list[tuple[str, str, list[dict[str, Any]]]] = [
    # === 平台数据 ===
    ("daily_ops_summary", "每日运营数据汇总", _build_leaf_actions(["平台数据", "日常报表"], "每日运营数据汇总")),
    ("daily_ops_compare", "运营数据汇总对比", _build_leaf_actions(["平台数据", "日常报表"], "运营数据汇总对比")),
    ("daily_acquisition", "买量数据统计", _build_leaf_actions(["平台数据", "日常报表"], "买量数据统计")),
    ("prod_sales", "平台产销情况", _build_leaf_actions(["平台数据", "平台产销"], "平台产销情况")),
    ("prod_sales_compare", "平台产销情况对比", _build_leaf_actions(["平台数据", "平台产销"], "平台产销情况对比")),
    ("recharge_history", "用户历史充值汇总", _build_leaf_actions(["平台数据", "平台充值"], "用户历史充值汇总")),
    ("recharge_daily", "用户每日充值汇总", _build_leaf_actions(["平台数据", "平台充值"], "用户每日充值汇总")),
    ("recharge_status", "平台充值情况", _build_leaf_actions(["平台数据", "平台充值"], "平台充值情况")),
    ("recharge_compare", "平台充值情况对比", _build_leaf_actions(["平台数据", "平台充值"], "平台充值情况对比")),
    ("alert_gold", "用户金币产出预警", _build_leaf_actions(["平台数据", "平台预警信息"], "用户金币产出预警")),
    ("alert_traffic", "用户流量来源", _build_leaf_actions(["平台数据", "平台预警信息"], "用户流量来源")),
    # === 数据统计分析 ===
    ("stats_user_dau", "日活统计", _build_leaf_actions(["数据统计分析", "用户数据统计"], "日活统计")),
    ("stats_user_dau_compare", "日活统计对比", _build_leaf_actions(["数据统计分析", "用户数据统计"], "日活统计对比")),
    ("stats_user_new", "日新用户统计", _build_leaf_actions(["数据统计分析", "用户数据统计"], "日新用户统计")),
    ("stats_user_new_compare", "日新用户对比", _build_leaf_actions(["数据统计分析", "用户数据统计"], "日新用户对比")),
    ("stats_retention_user", "新增用户留存统计", _build_leaf_actions(["数据统计分析", "留存数据统计"], "新增用户留存统计")),
    ("stats_retention_user_compare", "新增用户留存对比", _build_leaf_actions(["数据统计分析", "留存数据统计"], "新增用户留存对比")),
    ("stats_retention_paid", "新增付费留存统计", _build_leaf_actions(["数据统计分析", "留存数据统计"], "新增付费留存统计")),
    ("stats_retention_paid_compare", "新增付费留存对比", _build_leaf_actions(["数据统计分析", "留存数据统计"], "新增付费留存对比")),
    ("stats_recharge", "充值数据统计", _build_leaf_actions(["数据统计分析", "充值数据统计"], "充值数据统计")),
    ("stats_recharge_compare", "充值统计对比", _build_leaf_actions(["数据统计分析", "充值数据统计"], "充值统计对比")),
    ("stats_game_daily", "每日游戏数据", _build_leaf_actions(["数据统计分析", "游戏数据统计"], "每日游戏数据")),
    ("stats_game_compare", "游戏数据统计对比", _build_leaf_actions(["数据统计分析", "游戏数据统计"], "游戏数据统计对比")),
    ("stats_game_core", "核心产品每日数据表", _build_leaf_actions(["数据统计分析", "游戏数据统计"], "核心产品每日数据表")),
    ("stats_game_active", "游戏活跃留存", _build_leaf_actions(["数据统计分析", "游戏数据统计"], "游戏活跃留存")),
    ("stats_game_new", "游戏新增留存", _build_leaf_actions(["数据统计分析", "游戏数据统计"], "游戏新增留存")),
    # === 数据明细 ===
    ("detail_page_access", "页面访问记录", _build_leaf_actions(["数据明细", "页面明细"], "页面访问记录")),
    ("detail_user_register", "新用户注册明细", _build_leaf_actions(["数据明细", "活跃明细"], "新用户注册明细")),
    ("detail_user_active", "用户活跃明细", _build_leaf_actions(["数据明细", "活跃明细"], "用户活跃明细")),
    ("detail_recharge_daily", "每日充值明细", _build_leaf_actions(["数据明细", "充值明细"], "每日充值明细")),
    ("detail_game_room", "游戏进房明细", _build_leaf_actions(["数据明细", "游戏明细"], "游戏进房明细")),
    ("detail_game_room_actual", "实际进房明细", _build_leaf_actions(["数据明细", "游戏明细"], "实际进房明细")),
    ("detail_game_start", "游戏开局明细", _build_leaf_actions(["数据明细", "游戏明细"], "游戏开局明细")),
    ("detail_tracking", "数据埋点明细", _build_leaf_actions(["数据明细"], "数据埋点明细")),
]


def _slug(text: str, prefix: str = "") -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff]", "_", text.strip())
    return f"{prefix}_{s}" if prefix else s


def discover_menu_items(
    base_url: str = DEFAULT_BASE_URL,
    cdp_url: str = DEFAULT_CDP_URL,
) -> list[tuple[str, str, list[dict[str, Any]]]]:
    """遍历侧栏菜单，返回 (slug, display_name, actions)。失败时返回 []。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("[SPACollector] playwright 未安装，跳过菜单发现")
        return []

    result: list[tuple[str, str, list[dict[str, Any]]]] = []

    def collect(menu_locator: Any, path_actions: list[dict], path_prefix: str) -> None:
        submenus = menu_locator.locator(":scope > .el-submenu")
        for i in range(submenus.count()):
            sub = submenus.nth(i)
            title_el = sub.locator(".el-submenu__title").first
            try:
                title_text = title_el.inner_text(timeout=2000).strip()
            except Exception:
                title_text = f"sub_{i}"
            slug_part = _slug(title_text)
            title_el.click(timeout=3000)
            menu_locator.page.wait_for_timeout(500)
            inner_ul = sub.locator(":scope > ul.el-menu").first
            if inner_ul.count() > 0:
                new_actions = path_actions + [
                    {"type": "click", "selector": f"text={title_text}"},
                    {"type": "wait_ms", "ms": 500},
                ]
                collect(inner_ul, new_actions, f"{path_prefix}_{slug_part}" if path_prefix else slug_part)
            title_el.click(timeout=3000)
            menu_locator.page.wait_for_timeout(300)

        items = menu_locator.locator(":scope > .el-menu-item")
        for i in range(items.count()):
            item = items.nth(i)
            try:
                text = item.inner_text(timeout=2000).strip()
            except Exception:
                text = f"item_{i}"
            slug_name = _slug(text, path_prefix) if path_prefix else _slug(text)
            actions = path_actions + [
                {"type": "click", "selector": f"text={text}"},
                {"type": "wait_ms", "ms": 1500},
                {"type": "wait", "selector": TABLE_SEL, "timeout": 15},
            ]
            result.append((slug_name, text, actions))

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(cdp_url, timeout=8000)
            ctx = browser.contexts[0] if browser.contexts else None
            if not ctx or not ctx.pages:
                logger.warning("[SPACollector] Discovery: no browser context or pages")
                return []
            page = ctx.pages[0]
            page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_load_state("networkidle", timeout=15000)
            page.wait_for_timeout(2000)

            menu = page.locator(".el-menu").first
            if menu.count() == 0:
                menu = page.locator(".el-aside .el-menu, aside .el-menu").first
            if menu.count() == 0:
                logger.warning("[SPACollector] Discovery: .el-menu not found")
                return []

            collect(menu, [], "")
            browser.close()
    except Exception as e:
        logger.exception("[SPACollector] Discovery failed: %s", e)
        return []

    return result


def run_full_spa_collect(
    *,
    slugs: list[str] | None = None,
    base_url: str = DEFAULT_BASE_URL,
    cdp_url: str = DEFAULT_CDP_URL,
    use_discover: bool = False,
    auto_ingest: bool = False,
    raw_dir: Path | None = None,
    progress_cb: Callable[[int, int, str, dict], None] | None = None,
) -> tuple[int, int, list[str]]:
    """
    批量抓取 BI SPA 表。

    Args:
        slugs: 指定要抓取的 slug 列表，None 表示全部
        base_url: BI 后台入口 URL
        cdp_url: Chrome DevTools Protocol URL
        use_discover: 是否自动发现菜单（否则用 MENU_ITEMS）
        auto_ingest: 抓取成功后是否调用 ingest_csv 导入 DuckDB
        raw_dir: raw 目录，None 时用 get_bi_raw_dir()
        progress_cb: 进度回调 (idx, total, slug, result)

    Returns:
        (ok_count, fail_count, failed_slugs)
    """
    from l3_node.mcp_tools.bi.tool_web_scraper import harvest_table_data
    from l3_node.mcp_tools.bi.paths import get_bi_raw_dir, ensure_bi_dirs

    ensure_bi_dirs()
    out_dir = raw_dir or get_bi_raw_dir()

    if use_discover:
        all_items = discover_menu_items(base_url=base_url, cdp_url=cdp_url)
        if not all_items:
            logger.warning("[SPACollector] Discovery failed, falling back to MENU_ITEMS")
            all_items = list(MENU_ITEMS)
    else:
        all_items = list(MENU_ITEMS)

    if slugs:
        slug_set = set(slugs)
        all_items = [(s, n, a) for s, n, a in all_items if s in slug_set]

    ok, fail = 0, 0
    failed_slugs: list[str] = []
    total = len(all_items)

    # 日活/日新统计需筛选前一日并展开首行以获取渠道明细
    t1 = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    dau_dnu_filters = {
        "date_range": [t1, t1],
        "query_selector": "button:has-text('查询'), .el-button:has-text('查询')",
        "wait_after_query_ms": 3000,
        "expand_first_row": True,
    }

    for idx, (slug_name, display_name, actions) in enumerate(all_items):
        out_path = str(out_dir / f"{slug_name}.csv")
        automation = {
            "start_url": base_url,
            "actions": actions,
            "expand_table_rows": True,  # 展开树形行，抓取子项（渠道明细、各游戏数据等）
            "expand_wait_ms": 600,  # 展开后等待，弱网可调大至 800
        }
        if slug_name in ("stats_user_dau", "stats_user_new", "stats_game_daily"):
            automation["filters"] = dau_dnu_filters
        try:
            r = harvest_table_data(
                url=base_url,
                output_path=out_path,
                config={
                    "cdp_url": cdp_url,
                    "output_format": "csv",
                    "timeout": 45,
                    "extract_rules": TABLE_SEL,
                    "automation": automation,
                },
            )
            if progress_cb:
                progress_cb(idx + 1, total, slug_name, r)

            if r.get("status") == "success":
                ok += 1
                if auto_ingest:
                    from l3_node.mcp_tools.bi.data_store import ingest_csv

                    ingest_r = ingest_csv(out_path, slug_name)
                    if ingest_r.get("status") != "success":
                        logger.warning("[SPACollector] ingest_csv %s: %s", slug_name, ingest_r.get("error"))
            else:
                fail += 1
                failed_slugs.append(slug_name)
                logger.warning("[SPACollector] %s FAIL: %s", slug_name, r.get("error", r))
        except Exception as e:
            fail += 1
            failed_slugs.append(slug_name)
            logger.exception("[SPACollector] %s Exception: %s", slug_name, e)
            if progress_cb:
                progress_cb(idx + 1, total, slug_name, {"status": "error", "error": str(e)})

    return (ok, fail, failed_slugs)
