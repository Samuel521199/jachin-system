#!/usr/bin/env python3
"""
BI SPA full-auto scraper - auto-discover and scrape all sidebar menu items

Prereq: 1) .\scripts\launch_chrome_debug_bi.ps1  2) Login in Chrome
Usage: python scripts/run_bi_scraper_spa.py [--no-discover]
  --no-discover: use hardcoded MENU_ITEMS only (skip auto-discovery)
Output: ~/.jachin/client_volumes/bi_data/raw/{slug}.csv
"""
from __future__ import annotations

import logging
import re
import sys
from datetime import datetime
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

BASE_URL = "https://bi-admin-web.heronpro.xin/#/layout/person"
CDP_URL = "http://127.0.0.1:9222"
TABLE_SEL = "table:not(.el-date-table), .el-table__body-wrapper table"
# 侧栏菜单容器
MENU = ".el-menu"


def _build_leaf_actions(parents: list[str], leaf: str, click_query: bool = True) -> list[dict]:
    """
    可复用：构建「等待页面就绪 -> 展开父级 -> 等待下一级可见 -> 点击 -> ... -> 点查询 -> 等表格」的 action 序列。
    parents: 需依次展开的父级菜单（如 ["平台数据", "日常报表"]）
    leaf: 最终叶子项（如 "每日运营数据汇总"）
    click_query: 是否在进入页面后点击「查询」按钮加载数据

    关键：每次点击父级后必须 wait_visible 下一级，避免展开动画未完成就点击导致 not visible。
    当 leaf 与最后一级 parent 同名时（如 充值数据统计），用 .el-menu-item 限定为叶子项。
    """
    actions: list[dict] = []
    actions.append({"type": "wait_visible", "selector": f"{MENU} >> text={parents[0]}", "timeout": 15})
    actions.append({"type": "wait_ms", "ms": 500})
    for i, p in enumerate(parents):
        actions.append({"type": "click_expand", "selector": f"{MENU} >> text={p}", "text": p})
        next_item = parents[i + 1] if i + 1 < len(parents) else leaf
        # 下一项与当前 parent 同名时，限定为 .el-menu-item（叶子）
        next_sel = f"{MENU} >> .el-menu-item >> text={next_item}" if next_item == p else f"{MENU} >> text={next_item}"
        actions.append({"type": "wait_visible", "selector": next_sel, "timeout": 5})
        actions.append({"type": "wait_ms", "ms": 400})
    leaf_sel = f"{MENU} >> .el-menu-item >> text={leaf}" if leaf in parents else f"{MENU} >> text={leaf}"
    actions.append({"type": "click", "selector": leaf_sel})
    actions.append({"type": "wait_ms", "ms": 1500})
    if click_query:
        actions.append({"type": "click_if_exists", "selector": "button:has-text('查询'), .el-button:has-text('查询')", "force": True})
        actions.append({"type": "wait_ms", "ms": 3000})  # 等待接口返回与表格渲染
    actions.append({"type": "wait", "selector": TABLE_SEL, "timeout": 20})
    return actions


# 平台数据 + 数据统计分析 + 数据明细 下各子菜单的叶子项
MENU_ITEMS = [
    # === 平台数据 ===
    # 日常报表
    ("daily_ops_summary", "每日运营数据汇总", _build_leaf_actions(["平台数据", "日常报表"], "每日运营数据汇总")),
    ("daily_ops_compare", "运营数据汇总对比", _build_leaf_actions(["平台数据", "日常报表"], "运营数据汇总对比")),
    ("daily_acquisition", "买量数据统计", _build_leaf_actions(["平台数据", "日常报表"], "买量数据统计")),
    # 平台产销
    ("prod_sales", "平台产销情况", _build_leaf_actions(["平台数据", "平台产销"], "平台产销情况")),
    ("prod_sales_compare", "平台产销情况对比", _build_leaf_actions(["平台数据", "平台产销"], "平台产销情况对比")),
    # 平台充值
    ("recharge_history", "用户历史充值汇总", _build_leaf_actions(["平台数据", "平台充值"], "用户历史充值汇总")),
    ("recharge_daily", "用户每日充值汇总", _build_leaf_actions(["平台数据", "平台充值"], "用户每日充值汇总")),
    ("recharge_status", "平台充值情况", _build_leaf_actions(["平台数据", "平台充值"], "平台充值情况")),
    ("recharge_compare", "平台充值情况对比", _build_leaf_actions(["平台数据", "平台充值"], "平台充值情况对比")),
    # 平台预警信息
    ("alert_gold", "用户金币产出预警", _build_leaf_actions(["平台数据", "平台预警信息"], "用户金币产出预警")),
    ("alert_traffic", "用户流量来源", _build_leaf_actions(["平台数据", "平台预警信息"], "用户流量来源")),
    # === 数据统计分析 ===
    # 用户数据统计
    ("stats_user_dau", "日活统计", _build_leaf_actions(["数据统计分析", "用户数据统计"], "日活统计")),
    ("stats_user_dau_compare", "日活统计对比", _build_leaf_actions(["数据统计分析", "用户数据统计"], "日活统计对比")),
    ("stats_user_new", "日新用户统计", _build_leaf_actions(["数据统计分析", "用户数据统计"], "日新用户统计")),
    ("stats_user_new_compare", "日新用户对比", _build_leaf_actions(["数据统计分析", "用户数据统计"], "日新用户对比")),
    # 留存数据统计
    ("stats_retention_user", "新增用户留存统计", _build_leaf_actions(["数据统计分析", "留存数据统计"], "新增用户留存统计")),
    ("stats_retention_user_compare", "新增用户留存对比", _build_leaf_actions(["数据统计分析", "留存数据统计"], "新增用户留存对比")),
    ("stats_retention_paid", "新增付费留存统计", _build_leaf_actions(["数据统计分析", "留存数据统计"], "新增付费留存统计")),
    ("stats_retention_paid_compare", "新增付费留存对比", _build_leaf_actions(["数据统计分析", "留存数据统计"], "新增付费留存对比")),
    # 充值数据统计
    ("stats_recharge", "充值数据统计", _build_leaf_actions(["数据统计分析", "充值数据统计"], "充值数据统计")),
    ("stats_recharge_compare", "充值统计对比", _build_leaf_actions(["数据统计分析", "充值数据统计"], "充值统计对比")),
    # 游戏数据统计
    ("stats_game_daily", "每日游戏数据", _build_leaf_actions(["数据统计分析", "游戏数据统计"], "每日游戏数据")),
    ("stats_game_compare", "游戏数据统计对比", _build_leaf_actions(["数据统计分析", "游戏数据统计"], "游戏数据统计对比")),
    ("stats_game_core", "核心产品每日数据表", _build_leaf_actions(["数据统计分析", "游戏数据统计"], "核心产品每日数据表")),
    ("stats_game_active", "游戏活跃留存", _build_leaf_actions(["数据统计分析", "游戏数据统计"], "游戏活跃留存")),
    ("stats_game_new", "游戏新增留存", _build_leaf_actions(["数据统计分析", "游戏数据统计"], "游戏新增留存")),
    # === 数据明细 ===
    # 页面明细
    ("detail_page_access", "页面访问记录", _build_leaf_actions(["数据明细", "页面明细"], "页面访问记录")),
    # 活跃明细
    ("detail_user_register", "新用户注册明细", _build_leaf_actions(["数据明细", "活跃明细"], "新用户注册明细")),
    ("detail_user_active", "用户活跃明细", _build_leaf_actions(["数据明细", "活跃明细"], "用户活跃明细")),
    # 充值明细
    ("detail_recharge_daily", "每日充值明细", _build_leaf_actions(["数据明细", "充值明细"], "每日充值明细")),
    # 游戏明细
    ("detail_game_room", "游戏进房明细", _build_leaf_actions(["数据明细", "游戏明细"], "游戏进房明细")),
    ("detail_game_room_actual", "实际进房明细", _build_leaf_actions(["数据明细", "游戏明细"], "实际进房明细")),
    ("detail_game_start", "游戏开局明细", _build_leaf_actions(["数据明细", "游戏明细"], "游戏开局明细")),
    # 数据埋点明细（无子项，直接叶子）
    ("detail_tracking", "数据埋点明细", _build_leaf_actions(["数据明细"], "数据埋点明细")),
]


def _slug(text: str, prefix: str = "") -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff]", "_", text.strip())
    return f"{prefix}_{s}" if prefix else s


def _discover_menu_items() -> list[tuple[str, str, list[dict]]]:
    """Traverse sidebar menu recursively, return (slug, display_name, actions) for each leaf."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []

    result: list[tuple[str, str, list[dict]]] = []

    def collect(menu_locator, path_actions: list[dict], path_prefix: str) -> None:
        # el-submenu: expand, recurse into children, collapse
        submenus = menu_locator.locator(":scope > .el-submenu")
        for i in range(submenus.count()):
            sub = submenus.nth(i)
            title_el = sub.locator(".el-submenu__title").first
            try:
                title_text = title_el.inner_text(timeout=2000).strip()
            except Exception:
                title_text = f"sub_{i}"
            slug_part = _slug(title_text)
            # expand
            title_el.click(timeout=3000)
            menu_locator.page.wait_for_timeout(500)
            # children
            inner_ul = sub.locator(":scope > ul.el-menu").first
            if inner_ul.count() > 0:
                new_actions = path_actions + [
                    {"type": "click", "selector": f"text={title_text}"},
                    {"type": "wait_ms", "ms": 500},
                ]
                collect(inner_ul, new_actions, f"{path_prefix}_{slug_part}" if path_prefix else slug_part)
            # collapse for next sibling
            title_el.click(timeout=3000)
            menu_locator.page.wait_for_timeout(300)

        # el-menu-item: leaf
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
            browser = pw.chromium.connect_over_cdp(CDP_URL, timeout=8000)
            ctx = browser.contexts[0] if browser.contexts else None
            if not ctx or not ctx.pages:
                print("Discovery: no browser context or pages", file=sys.stderr)
                return []
            page = ctx.pages[0]
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_load_state("networkidle", timeout=15000)
            page.wait_for_timeout(2000)

            menu = page.locator(".el-menu").first
            if menu.count() == 0:
                menu = page.locator(".el-aside .el-menu, aside .el-menu").first
            if menu.count() == 0:
                print("Discovery: .el-menu not found", file=sys.stderr)
                return []

            collect(menu, [], "")
            browser.close()
    except Exception as e:
        import traceback
        print(f"Discovery failed: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return []

    return result


def _setup_logging(raw_dir: Path) -> logging.Logger:
    log_dir = raw_dir.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"bi_scraper_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    log = logging.getLogger("bi_scraper")
    log.info("Log file: %s", log_file)
    return log


def main() -> int:
    from l3_node.mcp_tools.bi.tool_web_scraper import harvest_table_data
    from l3_node.mcp_tools.bi.paths import get_bi_raw_dir, ensure_bi_dirs

    use_discover = "--no-discover" not in sys.argv
    ensure_bi_dirs()
    raw_dir = get_bi_raw_dir()
    log = _setup_logging(raw_dir)
    ok, fail = 0, 0

    log.info("BASE_URL=%s CDP_URL=%s use_discover=%s", BASE_URL, CDP_URL, use_discover)

    if use_discover:
        log.info("Discovering menu items...")
        all_items = _discover_menu_items()
        if not all_items:
            log.warning("Discovery failed, using hardcoded list.")
            all_items = list(MENU_ITEMS)
        else:
            log.info("Found %d items", len(all_items))
    else:
        all_items = list(MENU_ITEMS)
        log.info("Using hardcoded list, %d items", len(all_items))

    for idx, (slug_name, display_name, actions) in enumerate(all_items):
        out = str(raw_dir / f"{slug_name}.csv")
        log.info("[%d/%d] %s -> %s", idx + 1, len(all_items), display_name, slug_name)
        print(f"[{display_name}] -> {slug_name}.csv ... ", end="", flush=True)
        try:
            r = harvest_table_data(
                url=BASE_URL,
                output_path=out,
                config={
                    "cdp_url": CDP_URL,
                    "output_format": "csv",
                    "timeout": 45,
                    "extract_rules": TABLE_SEL,
                    "automation": {
                        "start_url": BASE_URL,
                        "actions": actions,
                        "expand_table_rows": True,  # 展开树形行，抓取子项（渠道明细、各游戏数据等）
                        "expand_wait_ms": 600,  # 展开后等待，弱网可调大至 800
                    },
                },
            )
            if r.get("status") == "success":
                rows = r.get("rows_count", 0)
                log.info("[%s] OK rows=%d", slug_name, rows)
                print(f"OK ({rows} rows)")
                ok += 1
            else:
                err = r.get("error", r)
                log.warning("[%s] FAIL: %s", slug_name, err)
                print(f"FAIL: {err}")
                fail += 1
        except Exception as e:
            log.exception("[%s] Exception: %s", slug_name, e)
            print(f"FAIL: {e}")
            fail += 1

    print()
    log.info("Done: %d OK, %d FAIL", ok, fail)
    print(f"Done: {ok} OK, {fail} FAIL")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
