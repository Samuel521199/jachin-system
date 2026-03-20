#!/usr/bin/env python3
"""
调试：检查 BI 表格 DOM 结构，定位展开图标选择器

Prereq: Chrome 调试模式 + 已打开「平台产销情况对比」页面
Usage: python scripts/inspect_bi_table_dom.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("pip install playwright && playwright install chromium")
        return 1

    cdp_url = "http://127.0.0.1:9222"
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(cdp_url, timeout=5000)
        page = browser.contexts[0].pages[0] if browser.contexts and browser.contexts[0].pages else None
        if not page:
            print("No page found")
            return 1

        page.bring_to_front()
        # 可选：先点击第一个展开图标，观察子行结构
        if "--expand" in sys.argv:
            clicked = page.evaluate(
                """
                () => {
                    const el = document.querySelector('.el-table__body-wrapper div[style*="cursor"]');
                    if (el) { el.click(); return 1; }
                    return 0;
                }
                """
            )
            if clicked:
                page.wait_for_timeout(2000)
                print("Clicked first expand, waiting 2s...")

        info = page.evaluate(
            """
            () => {
                const rows = document.querySelectorAll('.el-table__body-wrapper tbody tr');
                const result = { rowCount: rows.length, samples: [], expandSelectors: {} };
                const selectors = [
                    '.el-table__expand-icon',
                    '.el-table__expand-icon:not(.el-table__expand-icon--expanded)',
                    '.cell div[style*="cursor"]',
                    '.cell span[style*="cursor"]',
                    'div[style*="cursor"]',
                    '[class*="expand-icon"]',
                ];
                for (const sel of selectors) {
                    const els = document.querySelectorAll('.el-table__body-wrapper ' + sel);
                    result.expandSelectors[sel] = els.length;
                }
                for (let i = 0; i < Math.min(rows.length, 15); i++) {
                    const row = rows[i];
                    const cls = row.className || '';
                    const next = row.nextElementSibling;
                    const nextCls = next ? (next.className || '') : '';
                    const tds = row.querySelectorAll('td');
                    const col1Html = tds[0] ? tds[0].innerHTML.substring(0, 150) : '';
                    const col2Html = tds[1] ? tds[1].innerHTML.substring(0, 300) : '';
                    const col2Text = tds[1] ? tds[1].innerText.substring(0, 80) : '';
                    result.samples.push({
                        idx: i,
                        rowClass: cls,
                        nextRowClass: nextCls,
                        col1Html: col1Html,
                        col2Html: col2Html,
                        col2Text: col2Text,
                        expandInRow: row.querySelector('.el-table__expand-icon') ? 1 : 0,
                        cursorSpanInRow: row.querySelector('span[style*="cursor"]') ? 1 : 0,
                        expandInCol2: tds[1] && tds[1].querySelector('.el-table__expand-icon') ? 1 : 0,
                        clickableInCol2: tds[1] && tds[1].querySelector('[style*="cursor"]') ? 1 : 0,
                    });
                }
                result.allRowClasses = [];
                for (let i = 0; i < rows.length; i++) {
                    const r = rows[i];
                    const txt = r.innerText ? r.innerText.substring(0, 40) : '';
                    result.allRowClasses.push({ idx: i, class: r.className, text: txt });
                }
                return result;
            }
            """
        )
        print("Table DOM inspection:")
        print("  rowCount:", info["rowCount"])
        print("  expand selector counts:", info["expandSelectors"])
        if "allRowClasses" in info:
            print("  all rows (class + text):")
            for r in info["allRowClasses"]:
                print("    [%d] %r | %r" % (r["idx"], r["class"], r["text"]))
        print("  sample rows:")
        for s in info["samples"]:
            print("    [%d] rowClass=%r expand=%d cursorSpan=%d expandCol2=%d clickCol2=%d" % (
                s["idx"], s["rowClass"], s["expandInRow"], s["cursorSpanInRow"], s["expandInCol2"], s["clickableInCol2"]))
            print("        col2Text: %r" % (s.get("col2Text", "")))
            print("        col2Html: %s..." % (s.get("col2Html", "")[:180]))

        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
