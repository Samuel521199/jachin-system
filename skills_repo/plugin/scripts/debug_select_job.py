#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
调试职位选择：打印当前选中职位、下拉选项、校验逻辑中间值

用法：
  1. 用 launch_chrome_debug.ps1 启动 Chrome
  2. 登录 Boss 直聘，打开沟通页
  3. 运行: python scripts\debug_select_job.py --job "资深Golang语言开发_杭州 25-40K"
"""
import argparse
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "2-track-a-atomic-mcp"))

def main():
    from playwright.sync_api import sync_playwright

    p = argparse.ArgumentParser()
    p.add_argument("--job", default="资深Golang语言开发_杭州 25-40K")
    args = p.parse_args()
    job_text = args.job

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222", timeout=5000)
        page = None
        for p in browser.contexts[0].pages:
            if "zhipin" in (p.url or "") or "zhpin" in (p.url or ""):
                page = p
                break
        if not page:
            page = browser.contexts[0].pages[0]
        page.wait_for_load_state("domcontentloaded", timeout=5000)

        page.bring_to_front()
        page.wait_for_timeout(500)
        print("当前 URL:", (page.url or "")[:100])
        print("\n=== 1. 当前选中职位（未点开下拉前）===")
        for sel in ["span.chat-select-job", ".chat-select-job", "div.ui-dropmenu-label"]:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    t = (loc.inner_text() or "").strip()
                    tc = (loc.text_content() or "").strip()
                    print(f"  {sel}: inner={repr(t[:80])}, text_content={repr(tc[:80])}")
                    if not t and not tc:
                        try:
                            html = loc.evaluate("el => el.outerHTML").replace("><", ">\n<")[:400]
                            print(f"     HTML: {html}...")
                        except Exception:
                            pass
                    break
            except Exception as e:
                print(f"  {sel}: {e}")

        print("\n=== 2. 点击下拉，获取选项 ===")
        for sel in ["div.ui-dropmenu-label span.chat-select-job", "span.chat-select-job", "div.ui-dropmenu-label"]:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.click()
                    page.wait_for_timeout(1200)
                    break
            except Exception:
                pass

        # 找下拉菜单内的选项
        found_menu = False
        for menu_sel in ["div.ui-dropmenu-menu", "div[class*='dropmenu-menu']", "[class*='dropmenu'][class*='show']", ".dropmenu-list", "[class*='dropdown']", "[class*='popover']"]:
            try:
                menu = page.locator(menu_sel).first
                if menu.count() == 0:
                    continue
                print(f"  菜单选择器 {menu_sel} 找到")
                found_menu = True
                for item_sel in ["li", "[class*='option']", "[class*='item']", "[class*='dropmenu-item']", "div"]:
                    items = menu.locator(item_sel)
                    cnt = items.count()
                    if cnt > 0 and cnt < 200:
                        print(f"    子元素 {item_sel}: {cnt} 个")
                        for i in range(min(cnt, 15)):
                            txt = (items.nth(i).inner_text() or "").strip()
                            if txt and len(txt) > 2:
                                print(f"      [{i}] {repr(txt[:60])}")
                break
            except Exception as e:
                print(f"  {menu_sel}: {e}")
        if not found_menu:
            print("  未找到任何下拉菜单，尝试全页搜索含 Golang 的可点击元素:")
            try:
                hits = page.get_by_text("Golang", exact=False)
                print(f"    get_by_text('Golang'): {hits.count()} 个")
                for i in range(min(hits.count(), 8)):
                    try:
                        el = hits.nth(i)
                        txt = (el.inner_text() or "").strip()[:50]
                        print(f"      [{i}] {repr(txt)}")
                    except Exception:
                        pass
            except Exception as e:
                print(f"    {e}")

        print("\n=== 3. 当前选中职位（点开后）===")
        for sel in ["span.chat-select-job", ".chat-select-job"]:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    t = (loc.inner_text() or "").strip()
                    print(f"  {sel}: {repr(t)}")
                    break
            except Exception as e:
                print(f"  {sel}: {e}")

        # 校验逻辑模拟
        print("\n=== 4. 校验逻辑（目标 job_text）===")
        want = " ".join(job_text.split())
        curr = ""  # 从上面读取
        for sel in ["span.chat-select-job", ".chat-select-job"]:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    curr = (loc.inner_text() or "").strip()
                    break
            except Exception:
                pass
        print(f"  want(目标): {repr(want)}")
        print(f"  curr(当前): {repr(curr)}")
        want_core = want.replace(" ", "").replace("_", "")[:12]
        curr_core = curr.replace(" ", "").replace("_", "")
        print(f"  want_core[:12]: {repr(want_core)}")
        print(f"  want_core in curr_core: {want_core in curr_core}")
        print(f"  want[:8] in curr: {want[:8] in curr}")
        print(f"  Golang in want: {'Golang' in want}, Golang in curr: {'Golang' in curr}")

        # 关闭下拉（点击外部）
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        print("\n完成")

if __name__ == "__main__":
    main()
