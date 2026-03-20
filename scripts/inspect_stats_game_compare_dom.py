#!/usr/bin/env python3
"""检查 stats_game_compare 页面 DOM 结构"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

URL = "https://bi-admin-web.heronpro.xin/#/layout/BIManager/DataS/GameDataS/biGameDailySummaryCompare"


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
            print("No page. 请先启动 Chrome 调试模式并登录 BI 后台")
            return 1

        page.bring_to_front()
        page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)

        # 点对比查询（使用默认日期）
        page.locator("button:has-text('对比查询')").first.click()
        page.wait_for_timeout(3000)

        # 分析表格结构
        info = page.evaluate(
            """
            () => {
                const w = document.querySelector('.el-table__body-wrapper:not(.el-picker-panel *)') || document.querySelector('.el-table__body-wrapper');
                if (!w) return { ok: false };
                const trs = w.querySelectorAll('tbody tr');
                const samples = [];
                for (let i = 0; i < Math.min(trs.length, 15); i++) {
                    const tds = trs[i].querySelectorAll('td');
                    const cells = Array.from(tds).map(t => (t.innerText || '').trim().substring(0, 40));
                    const hasExpand = trs[i].querySelector('.el-table__expand-icon');
                    const hasExpanded = trs[i].querySelector('.el-table__expand-icon--expanded');
                    const expandedCell = trs[i].querySelector('.el-table__expanded-cell');
                    const nestedTable = expandedCell ? expandedCell.querySelector('table') : null;
                    const nestedTrs = nestedTable ? nestedTable.querySelectorAll('tbody tr') : [];
                    samples.push({
                        idx: i,
                        cellCount: tds.length,
                        col0: cells[0],
                        col1: cells[1],
                        hasExpand: !!hasExpand,
                        hasExpanded: !!hasExpanded,
                        hasExpandedCell: !!expandedCell,
                        nestedRowCount: nestedTrs.length,
                    });
                }
                return { ok: true, totalTrs: trs.length, samples };
            }
            """
        )
        print("===  collapsed 状态 ===")
        print(json.dumps(info, indent=2, ensure_ascii=False))

        # 点击第二行（第一个日期行）第二列展开
        page.evaluate(
            """
            () => {
                const w = Array.from(document.querySelectorAll('.el-table__body-wrapper')).find(x => !x.closest('.el-picker-panel'));
                if (!w) return;
                const trs = w.querySelectorAll('tbody tr');
                const tr = trs[1];
                const tds = tr.querySelectorAll('td');
                const cell = tds[1] || tds[0];
                const target = cell.querySelector('.expand-btn') || cell.querySelector('.date-expand-label') || cell.querySelector('.el-table__expand-icon') || cell.querySelector('.cell') || cell;
                if (target) { target.scrollIntoView({ block: 'center' }); target.click(); }
            }
            """
        )
        page.wait_for_timeout(2000)

        after = page.evaluate(
            """
            () => {
                const w = document.querySelector('.el-table__body-wrapper:not(.el-picker-panel *)');
                if (!w) return null;
                const trs = w.querySelectorAll('tbody tr');
                const samples = [];
                for (let i = 0; i < Math.min(trs.length, 20); i++) {
                    const tds = trs[i].querySelectorAll('td');
                    const cells = Array.from(tds).map(t => (t.innerText || '').trim().substring(0, 50));
                    const expandedCell = trs[i].querySelector('.el-table__expanded-cell');
                    const nestedTable = expandedCell ? expandedCell.querySelector('table') : null;
                    const nestedTrs = nestedTable ? nestedTable.querySelectorAll('tbody tr') : [];
                    let nestedSamples = [];
                    for (let j = 0; j < Math.min(nestedTrs.length, 5); j++) {
                        const ntds = nestedTrs[j].querySelectorAll('td');
                        nestedSamples.push(Array.from(ntds).map(t => (t.innerText || '').trim().substring(0, 30)));
                    }
                    samples.push({
                        idx: i,
                        col0: cells[0],
                        col1: cells[1],
                        hasExpandedCell: !!expandedCell,
                        nestedRowCount: nestedTrs.length,
                        nestedSamples,
                    });
                }
                return { totalTrs: trs.length, samples };
            }
            """
        )
        print("\n=== 展开第一行后 ===")
        print(json.dumps(after, indent=2, ensure_ascii=False))

        # 检查 row 1 col 1 的 HTML 结构
        cell_html = page.evaluate(
            """
            () => {
                const w = document.querySelector('.el-table__body-wrapper:not(.el-picker-panel *)');
                const tr = w?.querySelectorAll('tbody tr')[1];
                const td = tr?.querySelectorAll('td')[1];
                return td ? td.innerHTML.substring(0, 600) : null;
            }
            """
        )
        print("\n=== Row1 Col1 innerHTML ===")
        print(cell_html)

        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
