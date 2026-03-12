#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
调试脚本：检查 Boss 职位管理页上的元素。
运行后输出当前页 URL、以及各选择器是否能找到「发布职位」相关元素。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "2-track-a-atomic-mcp"))

def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("需安装 playwright: pip install playwright && playwright install")
        return

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222", timeout=5000)
        except Exception as e:
            print(f"连接失败: {e}\n请先运行 launch_chrome_debug.ps1")
            return

        contexts = browser.contexts
        if not contexts:
            print("未找到浏览器上下文")
            return
        context = contexts[0]
        pages = context.pages
        if not pages:
            print("未找到页面")
            return

        for i, p in enumerate(pages):
            url = (p.url or "")[:90]
            print(f"\n--- 标签页 {i+1}: {url}")
            if "job/list" not in url:
                continue
            p.wait_for_load_state("domcontentloaded", timeout=5000)
            p.wait_for_timeout(3000)

            frames = p.frames
            print(f"  共 {len(frames)} 个 frame")
            for fi, f in enumerate(frames):
                name = f.url[:60] if f.url else "(anonymous)"
                print(f"  Frame {fi}: {name}")
                for sel in ["div.add-btn", "[class*='add-btn']", "div:has-text('发布职位')"]:
                    loc = f.locator(sel)
                    cnt = loc.count()
                    if cnt > 0:
                        print(f"    ✓ {sel}: count={cnt} (在此 frame 找到!)")
                txt_loc = f.get_by_text("发布职位", exact=False)
                if txt_loc.count() > 0:
                    print(f"    ✓ get_by_text('发布职位'): count={txt_loc.count()}")

            print("  (主帧)")
            for sel in ["div.add-btn", "[class*='add-btn']", "div:has-text('发布职位')"]:
                loc = p.locator(sel)
                print(f"  {sel}: count={loc.count()}")
            print(f"  get_by_text('发布职位'): count={p.get_by_text('发布职位', exact=False).count()}")

            html = p.content()
            if "add-btn" in html:
                print("  HTML 中包含 'add-btn'")
            elif "发布职位" in html:
                print("  HTML 中包含 '发布职位' (但无 add-btn)")
            else:
                print("  HTML 中既不包含 'add-btn' 也不包含 '发布职位'，可能为不同页面")

if __name__ == "__main__":
    main()
