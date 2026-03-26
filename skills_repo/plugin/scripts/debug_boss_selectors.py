#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
调试 Boss 沟通页元素查找 - 检查当前页面能找到哪些目标元素

用法：
  1. 用 launch_chrome_debug.ps1 启动 Chrome
  2. 登录 Boss 直聘，打开沟通页，选中张俊的对话（确保能看到「点击预览附件简历」）
  3. 运行: python scripts\debug_boss_selectors.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "com.jachin.hr.recruitment"))

def main():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222", timeout=5000)
        context = browser.contexts[0]
        pages = context.pages

        print("=" * 60)
        print("标签页列表:")
        for i, p in enumerate(pages):
            url = (p.url or "")[:90]
            is_zhipin = "zhipin" in url or "zhpin" in url
            mark = " <-- Boss 页" if is_zhipin else ""
            print(f"  [{i}] {url}{mark}")

        page = None
        for p in pages:
            if "zhipin" in (p.url or "") or "zhpin" in (p.url or ""):
                page = p
                break
        if not page:
            page = pages[0]
            print("\n警告: 未找到 Boss 页，使用第一个标签")

        print(f"\n当前操作页面: {page.url[:80]}...")
        page.wait_for_load_state("domcontentloaded", timeout=3000)

        # 检查全部职位
        print("\n--- 全部职位下拉 ---")
        for sel in [".chat-select-job", ".ui-dropmenu-label", "span:has-text('全部职位')"]:
            try:
                c = page.locator(sel).count()
                print(f"  {sel}: 找到 {c} 个")
            except Exception as e:
                print(f"  {sel}: 异常 {e}")

        # 检查张俊 Java 对话（geek-item 为 Boss 对话列表项专用类名）
        print("\n--- 张俊 Java 对话 ---")
        for sel in [
            "div.geek-item", ".geek-item",
            "[class*='geek-item']",
            "li", "[class*='item']",
        ]:
            try:
                # 优先用 geek-name + source-job 精确定位
                loc = page.locator(sel).filter(
                    has=page.locator("span.geek-name:has-text('张俊')")
                ).filter(
                    has=page.locator("span.source-job:has-text('Java')")
                )
                c = loc.count()
                print(f"  {sel} (geek-name+source-job): 找到 {c} 个")
            except Exception:
                try:
                    loc = page.locator(sel).filter(has=page.get_by_text("张俊")).filter(has=page.get_by_text("Java"))
                    c = loc.count()
                    print(f"  {sel} (含张俊+Java): 找到 {c} 个")
                except Exception as e:
                    print(f"  {sel}: 异常 {e}")

        # 检查预览按钮
        print("\n--- 点击预览附件简历 ---")
        selectors = [
            "span.card-btn",
            "span.card-btn:has-text('点击预览附件简历')",
            "text=点击预览附件简历",
            "[class*='card-btn']",
        ]
        for sel in selectors:
            try:
                c = page.locator(sel).count()
                print(f"  {sel}: 找到 {c} 个")
                if c > 0:
                    el = page.locator(sel).first
                    txt = el.text_content() or ""
                    print(f"      首个元素文本: {repr(txt[:50])}")
            except Exception as e:
                print(f"  {sel}: 异常 {e}")

        # 检查是否有 viewer iframe（已打开预览时）
        print("\n--- PDF viewer iframe ---")
        try:
            iframes = page.locator('iframe[src*="viewer.html"]')
            print(f"  iframe[src*='viewer.html']: 找到 {iframes.count()} 个")
        except Exception as e:
            print(f"  异常: {e}")

        print("\n" + "=" * 60)

if __name__ == "__main__":
    main()
