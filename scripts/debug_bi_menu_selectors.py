#!/usr/bin/env python3
"""
BI 菜单选择器调试脚本 — 不修改抓取逻辑，仅验证 DOM 与选择器

前置：Chrome 调试模式已启动且已登录 BI 后台
用法：python scripts/debug_bi_menu_selectors.py

输出：控制台打印各选择器匹配数量、首个匹配的 HTML 片段，便于排查 click_expand 为何不生效。
"""
from __future__ import annotations

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))


def main() -> int:
    from playwright.sync_api import sync_playwright

    cdp_url = "http://127.0.0.1:9222"
    base_url = "https://bi-admin-web.heronpro.xin/#/layout/person"

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(cdp_url, timeout=8000)
        ctx = browser.contexts[0] if browser.contexts else None
        if not ctx or not ctx.pages:
            print("未找到浏览器上下文或页面")
            return 1
        page = ctx.pages[0]
        page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=15000)
        page.wait_for_timeout(2000)

        print("=" * 60)
        print("1. .el-menu 数量")
        menus = page.locator(".el-menu")
        print(f"   .el-menu count: {menus.count()}")

        print("\n2. 侧栏内 .el-menu")
        aside_menus = page.locator(".el-aside .el-menu, aside .el-menu")
        print(f"   .el-aside .el-menu count: {aside_menus.count()}")

        print("\n3. Element UI vs Element Plus 类名")
        submenu_ui = page.locator(".el-submenu")
        submenu_plus = page.locator(".el-sub-menu, [class*='el-sub-menu']")
        print(f"   .el-submenu (Element UI): {submenu_ui.count()}")
        print(f"   .el-sub-menu (Element Plus): {submenu_plus.count()}")

        print("\n4. 平台数据 相关选择器")
        sel_a = ".el-menu div[class*='sub-menu__title']:has-text('平台数据')"
        sel_b = ".el-menu div[class*='submenu__title']:has-text('平台数据')"
        sel_c = ".el-menu >> text=平台数据"
        for name, s in [("sub-menu__title", sel_a), ("submenu__title", sel_b), ("text=", sel_c)]:
            try:
                n = page.locator(s).count()
                print(f"   {name}: count={n}")
                if n > 0:
                    el = page.locator(s).first
                    html = el.evaluate("e => e.outerHTML").strip()[:120]
                    print(f"      first: {html}...")
            except Exception as e:
                print(f"   {name}: ERROR {e}")

        print("\n5. 日常报表（平台数据未展开时应 hidden）")
        sel_d = ".el-menu >> text=日常报表"
        try:
            loc = page.locator(sel_d).first
            n = page.locator(sel_d).count()
            print(f"   count: {n}")
            if n > 0:
                visible = loc.is_visible()
                print(f"   first is_visible: {visible}")
        except Exception as e:
            print(f"   ERROR: {e}")

        print("\n6. 平台数据 父级 li 的 aria-expanded")
        try:
            loc = page.locator(".el-menu >> text=平台数据").first
            expanded = loc.evaluate("""
                el => {
                    const li = el.closest('li[class*="sub-menu"], li[class*="submenu"], li[aria-expanded]');
                    if (!li) return 'no-li';
                    return li.getAttribute('aria-expanded') ?? 'null';
                }
            """)
            print(f"   aria-expanded: {expanded}")
        except Exception as e:
            print(f"   ERROR: {e}")

        print("\n7. 点击 平台数据 后 日常报表 是否 visible（模拟 click_expand）")
        try:
            title_sel = ".el-menu div[class*='sub-menu__title']:has-text('平台数据')"
            title_loc = page.locator(title_sel).first
            if title_loc.count() > 0:
                title_loc.click(timeout=5000, force=True)
                page.wait_for_timeout(500)
                daily_visible = page.locator(".el-menu >> text=日常报表").first.is_visible()
                print(f"   点击后 日常报表 is_visible: {daily_visible}")
            else:
                print("   未找到 title_loc，跳过点击")
        except Exception as e:
            print(f"   ERROR: {e}")

        browser.close()

    print("\n" + "=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
