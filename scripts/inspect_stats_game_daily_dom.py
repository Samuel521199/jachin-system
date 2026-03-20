#!/usr/bin/env python3
"""
深度分析 stats_game_daily 页面 DOM 结构。

核心问题：展开一行会占满整页，无法点击其他日期行。
解决思路：逐行「展开→提取→折叠→下一行」循环。
"""
from __future__ import annotations

import json
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
            print("No page. 请先启动 Chrome 调试模式并登录 BI 后台")
            return 1

        page.bring_to_front()
        page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)

        # 填写日期并查询
        page.locator(".el-date-editor input:first-of-type").fill("2026-03-13")
        page.locator(".el-date-editor input:last-of-type").fill("2026-03-19")
        page.locator("button:has-text('查询')").first.click()
        page.wait_for_timeout(3000)

        # 1. 分析表格容器与滚动结构
        scroll_info = page.evaluate(
            """
            () => {
                const wrapper = document.querySelector('.el-table__body-wrapper');
                if (!wrapper) return null;
                const table = wrapper.querySelector('table');
                const tbody = wrapper.querySelector('tbody');
                const trs = tbody ? tbody.querySelectorAll('tr') : [];
                const parentRows = [];
                for (let i = 0; i < trs.length; i++) {
                    const tr = trs[i];
                    const tds = tr.querySelectorAll('td');
                    const firstTd = tds[0];
                    const dateText = firstTd ? (firstTd.innerText || '').trim() : '';
                    const isDateRow = /^\\d{4}-\\d{2}-\\d{2}/.test(dateText);
                    const expandIcon = tr.querySelector('.el-table__expand-icon');
                    const isExpanded = expandIcon && expandIcon.classList.contains('el-table__expand-icon--expanded');
                    if (isDateRow) {
                        parentRows.push({
                            idx: i,
                            date: dateText.substring(0, 10),
                            isExpanded,
                            hasExpandIcon: !!expandIcon,
                        });
                    }
                }
                return {
                    wrapperScrollHeight: wrapper.scrollHeight,
                    wrapperClientHeight: wrapper.clientHeight,
                    wrapperScrollTop: wrapper.scrollTop,
                    hasOverflow: wrapper.scrollHeight > wrapper.clientHeight,
                    totalTrs: trs.length,
                    parentRowCount: parentRows.length,
                    parentRows,
                };
            }
            """
        )
        print("=== 1. 表格滚动结构 ===")
        print(json.dumps(scroll_info, indent=2, ensure_ascii=False))

        # 2. 展开第一行（用 date-expand-label），再分析
        page.evaluate(
            """
            () => {
                const w = Array.from(document.querySelectorAll('.el-table__body-wrapper')).find(x => !x.closest('.el-picker-panel'));
                if (!w) return;
                const firstRow = w.querySelector('tbody tr');
                const target = firstRow?.querySelector('td:first-child .date-expand-label') || firstRow?.querySelector('td:first-child .cell') || firstRow?.querySelector('td:first-child');
                if (target) {
                    target.scrollIntoView({ block: 'center' });
                    target.click();
                }
            }
            """
        )
        page.wait_for_timeout(2000)

        after_expand = page.evaluate(
            """
            () => {
                const wrapper = document.querySelector('.el-table__body-wrapper');
                if (!wrapper) return null;
                const trs = wrapper.querySelectorAll('tbody tr');
                const parentRows = [];
                let expandedRowHtml = null;
                for (let i = 0; i < trs.length; i++) {
                    const tr = trs[i];
                    const tds = tr.querySelectorAll('td');
                    const firstTd = tds[0];
                    const dateText = firstTd ? (firstTd.innerText || '').trim() : '';
                    const isDateRow = tds.length > 1 && /^\\d{4}-\\d{2}-\\d{2}/.test(dateText);
                    const expandedCell = tr.querySelector('td.el-table__expanded-cell');
                    const nestedTable = expandedCell ? expandedCell.querySelector('table') : null;
                    const nestedRowCount = nestedTable ? nestedTable.querySelectorAll('tbody tr').length : 0;
                    if (expandedCell && !expandedRowHtml) {
                        expandedRowHtml = expandedCell.outerHTML.substring(0, 500);
                    }
                    if (isDateRow || expandedCell) {
                        parentRows.push({
                            idx: i,
                            date: dateText.substring(0, 10) || '(expanded)',
                            tdCount: tds.length,
                            hasExpandedCell: !!expandedCell,
                            nestedRowCount,
                        });
                    }
                }
                const trSamples = [];
                for (let i = 0; i < Math.min(trs.length, 12); i++) {
                    const tds = trs[i].querySelectorAll('td');
                    const c0 = tds[0] ? (tds[0].innerText || '').trim().substring(0, 30) : '';
                    trSamples.push({ idx: i, tdCount: tds.length, col0: c0, cls: trs[i].className });
                }
                return {
                    wrapperScrollHeight: wrapper.scrollHeight,
                    totalTrs: trs.length,
                    parentRows,
                    expandedRowHtmlSample: expandedRowHtml,
                    trSamples,
                };
            }
            """
        )
        print("\n=== 2. 展开第一行后 ===")
        print(json.dumps(after_expand, indent=2, ensure_ascii=False))

        # 3. 验证：折叠后能否再点第二行
        page.evaluate(
            """
            () => {
                const icon = document.querySelector('.el-table__body-wrapper .el-table__expand-icon.el-table__expand-icon--expanded');
                if (icon) {
                    icon.scrollIntoView({ block: 'center' });
                    icon.click();
                }
            }
            """
        )
        page.wait_for_timeout(800)

        after_collapse = page.evaluate(
            """
            () => {
                const icons = document.querySelectorAll('.el-table__body-wrapper .el-table__expand-icon:not(.el-table__expand-icon--expanded)');
                return { unexpandedCount: icons.length };
            }
            """
        )
        print("\n=== 3. 折叠后未展开图标数 ===")
        print(after_collapse)

        # 4. 检查展开相关元素（多种可能的选择器）
        icon_check = page.evaluate(
            """
            () => {
                const bodyWrapper = document.querySelector('.el-table__body-wrapper:not(.el-picker-panel *)') || document.querySelector('.el-table__body-wrapper');
                const firstRow = bodyWrapper?.querySelector('tbody tr');
                const firstCell = firstRow?.querySelector('td:first-child');
                const expandLike = firstCell?.querySelectorAll('[class*="expand"], [class*="caret"], .el-icon, i, span');
                const classes = [];
                if (expandLike) {
                    for (let i = 0; i < Math.min(expandLike.length, 5); i++) {
                        classes.push(expandLike[i].className || expandLike[i].tagName);
                    }
                }
                return {
                    elExpandIcon: document.querySelectorAll('.el-table__expand-icon').length,
                    expandIcon: document.querySelectorAll('[class*="expand-icon"]').length,
                    caretWrapper: document.querySelectorAll('.caret-wrapper').length,
                    firstCellHtml: firstCell ? firstCell.innerHTML.substring(0, 300) : null,
                    firstCellClasses: classes,
                };
            }
            """
        )
        print("\n=== 4. 展开图标选择器检查 ===")
        print(json.dumps(icon_check, indent=2, ensure_ascii=False))

        # 5. 模拟「展开→提取→折叠」单次循环
        print("\n=== 5. 模拟逐行抓取逻辑 ===")
        result = page.evaluate(
            """
            () => {
                const wrapper = document.querySelector('.el-table__body-wrapper');
                if (!wrapper) return { ok: false, reason: 'no wrapper' };
                const trs = wrapper.querySelectorAll('tbody tr');
                const parentIndices = [];
                for (let i = 0; i < trs.length; i++) {
                    const tr = trs[i];
                    const tds = tr.querySelectorAll('td');
                    if (tds.length > 1) {
                        const t0 = (tds[0].innerText || '').trim();
                        if (/^\\d{4}-\\d{2}-\\d{2}/.test(t0)) parentIndices.push(i);
                    }
                }
                return {
                    ok: true,
                    parentIndices,
                    totalParentRows: parentIndices.length,
                };
            }
            """
        )
        print(result)

        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
