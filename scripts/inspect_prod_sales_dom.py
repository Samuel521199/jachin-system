#!/usr/bin/env python3
"""调试：检查平台产销情况页面的 DOM 结构，定位反复点击全部汇总的原因"""
from __future__ import annotations

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

PROD_SALES_URL = "https://bi-admin-web.heronpro.xin/#/layout/BIManager/PlatformData/PlatformAsset/biGameDailyAssetSummary"


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("pip install playwright")
        return 1

    cdp_url = "http://127.0.0.1:9222"
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(cdp_url, timeout=5000)
        page = browser.contexts[0].pages[0] if browser.contexts and browser.contexts[0].pages else None
        if not page:
            print("No page")
            return 1

        page.bring_to_front()
        page.goto(PROD_SALES_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
        if "--expand" in sys.argv:
            for _ in range(5):
                c = page.evaluate("() => { const el = document.querySelector('.el-table__expand-icon:not(.el-table__expand-icon--expanded)'); if (el) { el.click(); return 1; } return 0; }")
                if not c:
                    break
                page.wait_for_timeout(2000)
            print("Expanded 5 rounds...")

        info = page.evaluate(
            """
            () => {
                const rows = document.querySelectorAll('.el-table__body-wrapper tbody tr');
                const result = { rowCount: rows.length, rows: [] };
                const getIndent = (r) => {
                    const cells = r.querySelectorAll('td');
                    for (const c of cells) {
                        const div = c.querySelector('div[style*="padding-left"]');
                        if (div && div.style) {
                            const m = (div.style.paddingLeft || '').match(/(\d+)/);
                            if (m) return parseInt(m[1], 10);
                        }
                    }
                    return 0;
                };
                for (let i = 0; i < rows.length; i++) {
                    const r = rows[i];
                    const next = r.nextElementSibling;
                    const indent = getIndent(r);
                    const nextIndent = next ? getIndent(next) : -1;
                    const text = r.innerText ? r.innerText.substring(0, 50) : '';
                    const hasCursor = r.querySelector('div[style*="cursor"]') ? 1 : 0;
                    const wouldSkip = next && (getIndent(next) > indent);
                    result.rows.push({
                        idx: i,
                        text: text,
                        indent: indent,
                        nextIndent: nextIndent,
                        hasCursor: hasCursor,
                        wouldSkip: wouldSkip,
                    });
                }
                result.cellCounts = [];
                for (let i = 0; i < rows.length; i++) {
                    const tds = rows[i].querySelectorAll('td');
                    const texts = Array.from(tds).map(t => (t.innerText || '').trim().substring(0, 30));
                    result.cellCounts.push({ idx: i, count: tds.length, texts: texts });
                }
                return result;
            }
            """
        )
        print("平台产销情况 DOM 分析:")
        if "cellCounts" in info:
            print("  每行 cell 数量与内容:")
            for c in info["cellCounts"][:15]:
                print("    [%d] count=%d %s" % (c["idx"], c["count"], c["texts"]))
        print("  rowCount:", info["rowCount"])
        print("  行分析 (indent/nextIndent/wouldSkip 决定是否跳过):")
        for r in info["rows"][:20]:
            print("    [%d] indent=%d nextIndent=%d skip=%s cursor=%d | %s" % (
                r["idx"], r["indent"], r["nextIndent"], r["wouldSkip"], r["hasCursor"], r["text"][:60]))

        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
