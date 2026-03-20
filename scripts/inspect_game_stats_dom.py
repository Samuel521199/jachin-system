#!/usr/bin/env python3
"""调试：检查游戏数据统计页面的 DOM 结构，定位展开子行未抓取的原因"""
from __future__ import annotations

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

URL = "https://bi-admin-web.heronpro.xin/#/layout/BIManager/DataS/GameDataS/biGameDailySummary"


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
        page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)

        # 填写日期并查询
        page.locator(".el-date-editor input:first-of-type").fill("2026-03-13")
        page.locator(".el-date-editor input:last-of-type").fill("2026-03-19")
        page.locator("button:has-text('查询')").first.click()
        page.wait_for_timeout(3000)

        # 展开所有行
        if "--expand" in sys.argv:
            for round_i in range(15):
                clicked = page.evaluate(
                    """
                    () => {
                        const icons = document.querySelectorAll('.el-table__body-wrapper .el-table__expand-icon:not(.el-table__expand-icon--expanded)');
                        if (icons.length === 0) return 0;
                        icons[0].scrollIntoView({ block: 'center', behavior: 'instant' });
                        icons[0].click();
                        return 1;
                    }
                    """
                )
                if not clicked:
                    break
                page.wait_for_timeout(600)
            print("Expanded all rows...")

        info = page.evaluate(
            """
            () => {
                const wrapper = document.querySelector('.el-table__body-wrapper');
                const result = { rowCount: 0, rows: [], expandCount: 0, nestedTables: 0, expandedRows: [], allTrs: [], tablesInWrapper: 0 };
                if (!wrapper) return result;
                const tables = wrapper.querySelectorAll('table');
                result.tablesInWrapper = tables.length;
                const rows = wrapper.querySelectorAll('tbody tr');
                result.rowCount = rows.length;
                result.expandCount = wrapper.querySelectorAll('.el-table__expand-icon').length;
                result.expandUnexpandedCount = wrapper.querySelectorAll('.el-table__expand-icon:not(.el-table__expand-icon--expanded)').length;
                for (let i = 0; i < Math.min(rows.length, 20); i++) {
                    const r = rows[i];
                    const tds = r.querySelectorAll('td');
                    const texts = Array.from(tds).map(t => (t.innerText || '').trim().substring(0, 25));
                    const cls = r.className || '';
                    const isExpandedRow = cls.includes('expanded') || r.querySelector('td.el-table__expanded-cell');
                    const nestedTable = r.querySelector('table');
                    if (nestedTable) {
                        result.nestedTables++;
                        const nestedRows = nestedTable.querySelectorAll('tbody tr');
                        result.expandedRows.push({ parentIdx: i, nestedRowCount: nestedRows.length, sample: nestedRows[0] ? nestedRows[0].innerText.substring(0, 80) : '' });
                    }
                    result.rows.push({
                        idx: i,
                        class: cls,
                        tdCount: tds.length,
                        col1: texts[0] || '',
                        col2: texts[1] || '',
                        isExpandedRow,
                        hasNestedTable: !!nestedTable,
                    });
                }
                result.allTrs = Array.from(rows).slice(0, 20).map((r, i) => ({ idx: i, class: r.className, text: r.innerText.substring(0, 50) }));
                return result;
            }
            """
        )
        print("每日游戏数据 DOM 分析:")
        print("  tablesInWrapper:", info.get("tablesInWrapper", 0))
        print("  rowCount:", info["rowCount"])
        print("  expand icons:", info["expandCount"], "unexpanded:", info["expandUnexpandedCount"])
        print("  nestedTables:", info.get("nestedTables", 0))
        for r in info["rows"][:25]:
            print("    [%d] cls=%r tdCount=%d col1=%r col2=%r expanded=%s nested=%s" % (
                r["idx"], r["class"][:50], r["tdCount"], r["col1"], r["col2"], r.get("isExpandedRow"), r.get("hasNestedTable")))
        for ex in info.get("expandedRows", []):
            print("  expanded parent %d: nestedRows=%d sample=%r" % (ex["parentIdx"], ex["nestedRowCount"], ex.get("sample", "")))
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
