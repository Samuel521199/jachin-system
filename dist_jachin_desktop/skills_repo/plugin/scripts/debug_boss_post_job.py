#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
调试脚本：检查 Boss 职位管理页上的元素。
运行后输出当前页 URL、以及各选择器是否能找到「发布职位」相关元素。
支持「职位类型」专项调试：点击职位类型输入框后打印相关 DOM 元素。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "2-track-a-atomic-mcp"))

# 职位类型相关选择器（图1：点击后检查）
JOB_CATEGORY_SELECTORS = [
    "input[name='jobCategory']",
    "input.ipt[placeholder='选择职位类型']",
    "span.job-recommend-content_title-icon",
    "div.job-recommend-content_item",
    "[class*='job-recommend-content']",
]


def _dump_job_category_after_click(page, form_frame, scope_name: str):
    """点击职位类型输入框后，打印各选择器在指定 scope 中的 count。"""
    scope = form_frame if "frame" in scope_name.lower() else page
    results = {}
    for sel in JOB_CATEGORY_SELECTORS:
        try:
            cnt = scope.locator(sel).count()
            results[sel] = cnt
        except Exception as e:
            results[sel] = f"err:{e}"
    print(f"  [职位类型] scope={scope_name}: {results}")
    # 包含「推荐」或「职位类型」的文本节点
    try:
        rec_cnt = scope.get_by_text("推荐", exact=False).count()
        cat_cnt = scope.get_by_text("职位类型", exact=False).count()
        print(f"  [职位类型] get_by_text: 推荐={rec_cnt}, 职位类型={cat_cnt}")
    except Exception as e:
        print(f"  [职位类型] get_by_text err: {e}")


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
            on_list = "job/list" in url
            on_edit = "job/edit" in url
            if not on_list and not on_edit:
                continue
            p.wait_for_load_state("domcontentloaded", timeout=5000)
            p.wait_for_timeout(2000)

            frames = p.frames
            print(f"  共 {len(frames)} 个 frame")

            if on_list:
                for fi, f in enumerate(frames):
                    name = f.url[:60] if f.url else "(anonymous)"
                    print(f"  Frame {fi}: {name}")
                    for sel in ["div.add-btn", "[class*='add-btn']", "div:has-text('发布职位')"]:
                        loc = f.locator(sel)
                        cnt = loc.count()
                        if cnt > 0:
                            print(f"    ✓ {sel}: count={cnt} (在此 frame 找到!)")

            # === 职位类型专项调试（图1）：定位表单 → 点击职位类型 → 打印 DOM ===
            print("\n--- [职位类型] 专项调试：点击职位类型后检查 DOM ---")
            form_frame = None
            scopes = list(p.frames) + [p]
            for _ in range(6):
                for f in scopes:
                    try:
                        if f.locator("p.job-type-item").count() > 0 or f.locator("input.job-name-input").count() > 0:
                            form_frame = f
                            break
                    except Exception:
                        pass
                if form_frame:
                    break
                if on_list and not form_frame:
                    job_frame = None
                    for f in p.frames:
                        if f.locator("div.add-btn").count() > 0:
                            job_frame = f
                            break
                    if job_frame:
                        job_frame.locator("div.add-btn").first.click(timeout=5000)
                p.wait_for_timeout(1000)

            if form_frame:
                inp = form_frame.locator(
                    "input[name='jobCategory'][placeholder='选择职位类型'], "
                    "input.ipt[placeholder='选择职位类型'], input[name='jobCategory']"
                ).first
                if inp.count() > 0:
                    try:
                        inp.click(timeout=3000)
                        p.wait_for_timeout(1500)
                        _dump_job_category_after_click(p, form_frame, "main_page")
                        _dump_job_category_after_click(p, form_frame, "form_frame")
                    except Exception as ex:
                        print(f"  [职位类型] 点击或检查异常: {ex}")
                else:
                    print("  [职位类型] 未找到职位类型输入框")
            else:
                print("  [职位类型] 未找到表单 frame（含 p.job-type-item 或 input.job-name-input）")

if __name__ == "__main__":
    main()
