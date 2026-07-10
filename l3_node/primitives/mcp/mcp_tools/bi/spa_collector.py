"""
BI SPA 批量抓取 — 供 scripts 与 main_skill 复用

职责：针对 bi-admin-web 侧栏菜单的批量抓取，输出 raw/*.csv。
前置：Chrome 调试模式已启动且已登录。设计: docs/bi_daily_report/09_SPA_SCRAPER_PLACEMENT_ANALYSIS.md
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
import re
from pathlib import Path
from typing import Any, Callable


def resolve_spa_report_date_end(report_date_end: date | None) -> date:
    """SPA 填「统计日期」时使用的区间结束日：默认日历昨天（最新完整日）。"""
    if report_date_end is not None:
        return report_date_end
    return (datetime.now() - timedelta(days=1)).date()


def _build_7d_date_range_strings(as_of: date) -> tuple[str, str]:
    t_end = as_of.strftime("%Y-%m-%d")
    t_start = (as_of - timedelta(days=6)).strftime("%Y-%m-%d")
    return t_start, t_end


def _build_compare_date_range_strings(as_of: date) -> tuple[list[str], list[str]]:
    """与历史 stats_game_compare 一致：近 7 日 vs 再往前 7 日。"""
    t_start, t_end = _build_7d_date_range_strings(as_of)
    t_end2 = (as_of - timedelta(days=7)).strftime("%Y-%m-%d")
    t_start2 = (as_of - timedelta(days=13)).strftime("%Y-%m-%d")
    return [t_start, t_end], [t_start2, t_end2]

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
    侧栏折叠时（el-menu--collapse）菜单文字 hidden，需先 expand_sidebar_if_collapsed。
    """
    actions: list[dict[str, Any]] = []
    actions.append({"type": "expand_sidebar_if_collapsed"})
    actions.append({"type": "wait_attached", "selector": f"{menu_sel} >> text={parents[0]}", "timeout": 15})
    actions.append({"type": "wait_ms", "ms": 500})
    for i, p in enumerate(parents):
        actions.append({"type": "click_expand", "selector": f"{menu_sel} >> text={p}", "text": p})
        next_item = parents[i + 1] if i + 1 < len(parents) else leaf
        next_sel = f"{menu_sel} >> .el-menu-item >> text={next_item}" if next_item == p else f"{menu_sel} >> text={next_item}"
        actions.append({"type": "wait_attached", "selector": next_sel, "timeout": 5})
        actions.append({"type": "wait_ms", "ms": 400})
    leaf_sel = f"{menu_sel} >> .el-menu-item >> text={leaf}" if leaf in parents else f"{menu_sel} >> text={leaf}"
    actions.append({"type": "click", "selector": leaf_sel, "force": True})
    actions.append({"type": "wait_ms", "ms": 1500})
    if click_query:
        # 对比页用「对比查询」，普通页用「查询」
        actions.append({"type": "click_if_exists", "selector": "button:has-text('对比查询'), button:has-text('查询'), .el-button:has-text('查询')", "force": True})
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


# 直链模式下，无日期筛选的页面仍要点「查询/对比查询」刷新表格（等价于原菜单 automation 末尾）
_DEFAULT_QUERY_REFRESH_FILTERS: dict[str, Any] = {
    "query_selector": "button:has-text('对比查询'), button:has-text('查询'), .el-button:has-text('查询')",
    "wait_after_query_ms": 3000,
}


def _build_single_date_range_filters(
    as_of: date,
    *,
    form_label: str | None = None,
    expand_first_row: bool = True,
) -> dict[str, Any]:
    """单时间段页（业务日期 起~止）：定位 .el-date-editor + 填后校验。"""
    t_start, t_end = _build_7d_date_range_strings(as_of)
    flt: dict[str, Any] = {
        "date_range": [t_start, t_end],
        "date_range_use_visual_order": True,
        "date_range_verify": True,
        "query_selector": "button:has-text('查询'), .el-button:has-text('查询')",
        "wait_after_query_ms": 3000,
        "expand_first_row": expand_first_row,
    }
    if form_label:
        flt["date_range_form_label"] = form_label
    return flt


def _apply_slug_specific_automation(
    automation: dict[str, Any],
    slug_name: str,
    report_date_end: date | None = None,
) -> None:
    """
    按 slug 合并 filters / 展开策略；菜单模式与直链模式共用。
    report_date_end：统计区间结束日（通常为昨天），与 main_skill 战报口径一致。
    """
    prod_sales_slugs = ("prod_sales", "prod_sales_compare")
    recharge_expand_slugs = ("recharge_status", "recharge_compare")
    game_stats_expand_slugs = (
        "stats_game_daily",
        "stats_game_compare",
        "stats_game_core",
        "stats_game_active",
        "stats_game_new",
    )
    # 含统计日期筛选的其它表（与产销/充值同类：须 fill_date_range + 查询）
    other_dated_slugs = (
        "daily_ops_summary",
        "daily_ops_compare",
        "daily_acquisition",
        "recharge_daily",
        "stats_recharge",
        "stats_recharge_compare",
        "stats_retention_user",
        "stats_retention_user_compare",
        "stats_retention_paid",
        "stats_retention_paid_compare",
        "stats_user_dau_compare",
        "stats_user_new_compare",
        "alert_gold",
        "alert_traffic",
    )
    expand_pages = (
        ("stats_user_dau", "stats_user_new")
        + game_stats_expand_slugs
        + prod_sales_slugs
        + recharge_expand_slugs
        + other_dated_slugs
    )

    as_of = resolve_spa_report_date_end(report_date_end)
    dau_dnu_filters = _build_single_date_range_filters(as_of, expand_first_row=True)
    stats_game_daily_filters = _build_single_date_range_filters(as_of, expand_first_row=False)

    p1, p2 = _build_compare_date_range_strings(as_of)
    stats_game_compare_filters = {
        "date_range_compare": [p1, p2],
        "date_range_compare_use_visual_order": True,
        "date_range_compare_no_escape_after_fill": True,
        "date_range_compare_form_labels": ["时间段1", "时间段2"],
        "query_selector": "button:has-text('对比查询'), .el-button:has-text('对比查询')",
        "wait_after_query_ms": 3000,
        "expand_first_row": False,
    }

    is_expand_heavy = slug_name in prod_sales_slugs or slug_name in recharge_expand_slugs
    needs_longer_expand = slug_name in expand_pages or slug_name in prod_sales_slugs or slug_name in recharge_expand_slugs
    game_stats_wait = 2000 if slug_name in game_stats_expand_slugs else 800

    if slug_name in game_stats_expand_slugs:
        automation["expand_wait_ms"] = game_stats_wait
        automation["expand_post_wait_ms"] = 3000
    elif slug_name in prod_sales_slugs or slug_name in recharge_expand_slugs:
        automation["expand_wait_ms"] = 800
        automation["expand_post_wait_ms"] = 1500
    elif needs_longer_expand:
        automation["expand_wait_ms"] = max(automation.get("expand_wait_ms") or 600, 800)
        automation["expand_post_wait_ms"] = max(automation.get("expand_post_wait_ms") or 500, 800)
    else:
        automation["expand_wait_ms"] = automation.get("expand_wait_ms") or 600
        automation["expand_post_wait_ms"] = automation.get("expand_post_wait_ms") or (
            1500 if is_expand_heavy else 500
        )

    compare_slugs = (
        "daily_ops_compare",
        "prod_sales_compare",
        "recharge_compare",
        "stats_recharge_compare",
        "stats_retention_user_compare",
        "stats_retention_paid_compare",
        "stats_user_dau_compare",
        "stats_user_new_compare",
    )
    dated_7d_expand_slugs = (
        "prod_sales",
        "recharge_status",
        "daily_ops_summary",
        "daily_acquisition",
        "recharge_daily",
        "alert_gold",
        "alert_traffic",
        "stats_retention_user",
        "stats_retention_paid",
    )

    if slug_name == "stats_game_daily":
        automation["filters"] = stats_game_daily_filters
        automation["expand_extract_collapse_loop"] = True
        automation["expand_table_rows"] = False
        # 父 tr 含「当日总计」与整日 GGR/RTP；原先只采子 tr 会漏该行
        automation["expand_include_parent_row"] = True
    elif slug_name == "stats_game_compare":
        automation["filters"] = dict(stats_game_compare_filters)
        automation["expand_extract_collapse_loop"] = True
        automation["expand_table_rows"] = False
        automation["expand_target_column"] = 1
        automation["expand_parent_full_cell"] = True
        automation["expand_skip_first_rows"] = 1
        automation["expand_capture_first_rows"] = 1
        automation["expand_wait_ms"] = 2000
    elif slug_name == "recharge_history":
        # 页面无「统计日期」范围框，仅有用户ID/渠道等；直接查询后翻页抓全量
        automation["filters"] = {
            "query_selector": "button:has-text('查询'), .el-button:has-text('查询')",
            "wait_after_query_ms": 3000,
        }
    elif slug_name == "stats_game_active":
        automation["filters"] = stats_game_daily_filters
        automation["expand_extract_collapse_loop"] = True
        automation["expand_table_rows"] = False
        automation["expand_include_parent_row"] = True
    elif slug_name == "stats_game_new":
        automation["filters"] = stats_game_daily_filters
        automation["expand_extract_collapse_loop"] = True
        automation["expand_table_rows"] = False
        automation["expand_include_parent_row"] = True
    elif slug_name in compare_slugs:
        automation["filters"] = dict(stats_game_compare_filters)
    elif slug_name == "stats_recharge":
        automation["filters"] = dict(stats_game_daily_filters)
    elif slug_name == "daily_ops_summary":
        automation["filters"] = _build_single_date_range_filters(as_of, form_label="业务日期")
    elif slug_name in dated_7d_expand_slugs:
        automation["filters"] = dict(dau_dnu_filters)
    elif slug_name in ("stats_user_dau", "stats_user_new") or slug_name in game_stats_expand_slugs:
        automation["filters"] = dict(dau_dnu_filters)


def get_automation_for_direct_url(slug: str, direct_url: str, cdp_url: str = DEFAULT_CDP_URL) -> dict[str, Any]:
    """
    返回某 slug 直接打开 URL 时的 automation 配置，供单页测试脚本复用。
    统计日期 / 对比区间由 _apply_slug_specific_automation 统一注入（含 report_date_end）。
    """
    recharge_expand_slugs = ("recharge_status", "recharge_compare")
    prod_sales_slugs = ("prod_sales", "prod_sales_compare")
    is_expand_heavy = slug in prod_sales_slugs or slug in recharge_expand_slugs
    expand_wait = 800 if (slug in prod_sales_slugs or slug in recharge_expand_slugs) else 600
    expand_post = 1500 if is_expand_heavy else 500

    if slug == "stats_game_compare":
        return {
            "start_url": direct_url,
            "actions": [],
            "expand_table_rows": False,
            "expand_extract_collapse_loop": True,
            "expand_target_column": 1,
            "expand_parent_full_cell": True,
            "expand_skip_first_rows": 1,
            "expand_capture_first_rows": 1,
            "expand_wait_ms": 2000,
        }

    return {
        "start_url": direct_url,
        "actions": [],
        "expand_table_rows": True,
        "expand_wait_ms": expand_wait,
        "expand_post_wait_ms": expand_post,
    }


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


def parse_direct_url_map_from_full_spa(full_spa: dict[str, Any] | None) -> dict[str, str] | None:
    """
    从 bi_daily_report.yaml 的 full_spa 段解析 slug -> 直链。
    use_direct_urls: false 或未配置 direct_urls 时返回 None（走侧栏菜单）。
    """
    if not full_spa or full_spa.get("use_direct_urls") is False:
        return None
    m = full_spa.get("direct_urls")
    if not isinstance(m, dict):
        return None
    out: dict[str, str] = {}
    for k, v in m.items():
        key = str(k or "").strip()
        s = str(v or "").strip()
        if key and s.lower().startswith("http"):
            out[key] = s
    return out if out else None


def run_full_spa_collect(
    *,
    slugs: list[str] | None = None,
    base_url: str = DEFAULT_BASE_URL,
    cdp_url: str = DEFAULT_CDP_URL,
    use_discover: bool = False,
    auto_ingest: bool = False,
    raw_dir: Path | None = None,
    progress_cb: Callable[[int, int, str, dict], None] | None = None,
    direct_url_map: dict[str, str] | None = None,
    report_date_end: date | None = None,
) -> tuple[int, int, list[str]]:
    """
    批量抓取 BI SPA 表。

    Args:
        slugs: 指定要抓取的 slug 列表，None 表示全部
        base_url: BI 后台入口 URL（菜单模式；直链模式失败回退时亦作 harvest 的 url 参数）
        cdp_url: Chrome DevTools Protocol URL
        use_discover: 是否自动发现菜单（否则用 MENU_ITEMS）
        auto_ingest: 抓取成功后是否调用 ingest_csv 导入 DuckDB
        raw_dir: raw 目录，None 时用 get_bi_raw_dir()
        progress_cb: 进度回调 (idx, total, slug, result)
        direct_url_map: slug -> 页面直链；非空且某 slug 有有效 URL 时跳过侧栏点击，避免菜单不可见导致失败
        report_date_end: 统计日期区间结束日（通常为昨天）；None 时按日历昨天。由 main_skill 传入与战报口径一致

    Returns:
        (ok_count, fail_count, failed_slugs)
    """
    from l3_node.primitives.mcp.mcp_tools.bi.tool_web_scraper import harvest_table_data
    from l3_node.primitives.mcp.mcp_tools.bi.paths import get_bi_raw_dir, ensure_bi_dirs

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

    prod_sales_slugs = ("prod_sales", "prod_sales_compare")
    recharge_expand_slugs = ("recharge_status", "recharge_compare")
    game_stats_expand_slugs = (
        "stats_game_daily",
        "stats_game_compare",
        "stats_game_core",
        "stats_game_active",
        "stats_game_new",
    )
    expand_pages = ("stats_user_dau", "stats_user_new") + game_stats_expand_slugs + prod_sales_slugs + recharge_expand_slugs

    durl = direct_url_map or {}

    for idx, (slug_name, display_name, actions) in enumerate(all_items):
        out_path = str(out_dir / f"{slug_name}.csv")
        raw_du = str(durl.get(slug_name) or "").strip()
        use_direct = bool(raw_du) and raw_du.lower().startswith("http")

        if use_direct:
            automation = get_automation_for_direct_url(slug_name, raw_du)
            automation["split_merged_cells"] = True
            _apply_slug_specific_automation(automation, slug_name, report_date_end=report_date_end)
            if not automation.get("filters"):
                automation["filters"] = dict(_DEFAULT_QUERY_REFRESH_FILTERS)
            page_url = raw_du
        else:
            is_expand_heavy = slug_name in prod_sales_slugs or slug_name in recharge_expand_slugs
            needs_longer_expand = slug_name in expand_pages or slug_name in prod_sales_slugs or slug_name in recharge_expand_slugs
            game_stats_wait = 2000 if slug_name in game_stats_expand_slugs else 800
            automation = {
                "start_url": base_url,
                "actions": actions,
                "expand_table_rows": True,
                "expand_wait_ms": game_stats_wait if slug_name in game_stats_expand_slugs else (800 if needs_longer_expand else 600),
                "expand_post_wait_ms": 3000 if slug_name in game_stats_expand_slugs else (1500 if is_expand_heavy else (800 if slug_name in expand_pages else 500)),
                "split_merged_cells": True,
            }
            _apply_slug_specific_automation(automation, slug_name, report_date_end=report_date_end)
            page_url = base_url

        # 分页表：仅抓当前 DOM 会漏后续页
        if slug_name in ("detail_recharge_daily", "recharge_history"):
            automation["pagination_all_pages"] = True

        try:
            diff_label = f"{slug_name}|{display_name}"
            print(
                f"[DIFF-LOG] | [{diff_label}] | 开始 harvest_table_data -> {out_path}",
                flush=True,
            )
            r = harvest_table_data(
                url=page_url,
                output_path=out_path,
                config={
                    "cdp_url": cdp_url,
                    "output_format": "csv",
                    "timeout": 45,
                    "extract_rules": TABLE_SEL,
                    "automation": automation,
                    "diff_log_context": diff_label,
                },
            )
            if progress_cb:
                progress_cb(idx + 1, total, slug_name, r)

            if r.get("status") == "success":
                ok += 1
                if auto_ingest:
                    from l3_node.primitives.mcp.mcp_tools.bi.data_store import ingest_csv

                    actual_path = r.get("file_path", out_path)
                    ingest_r = ingest_csv(actual_path, slug_name)
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
