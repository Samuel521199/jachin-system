"""
通用网页抓取器 — mcp:atom_web_scraper

契约: docs/bi_daily_report/01_PARALLEL_DEVELOPMENT_GUIDE.md
支持两种模式：
  1. API 模式：url 为接口地址，用 requests 请求，支持 headers
  2. SPA 模式：url 为页面地址，用 Playwright 连接已登录 Chrome（cdp_url）抓取表格

SPA 模式支持 automation 配置，实现全自动：导航、点击菜单、填写筛选（日期范围等）、等待加载、抓取表格。
"""
from __future__ import annotations

import csv
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 合并单元格模式：当前值 (+/-X%) 上期值，如 "3,383 (+50.09%) 2,254" 或 "120.00 (+300.00%) 30.00"
_MERGED_CELL_RE = re.compile(
    r"^(.+?)\s*\(([+-]?[\d.]+%)\)\s*(.+)$",
    re.DOTALL,
)


def _split_merged_cell_value(val: str) -> str:
    """
    若单元格为「当前值 (+X%) 上期值」合并格式，拆分为「当前值 | 环比 | 上期值」便于下游解析。
    否则返回原值。
    """
    val = (val or "").strip()
    m = _MERGED_CELL_RE.match(val)
    if m:
        return f"{m.group(1).strip()} | {m.group(2)} | {m.group(3).strip()}"
    return val


def _run_automation_actions(page: Any, actions: list[dict], timeout_ms: int) -> str:
    """
    按顺序执行自动化操作。失败时返回错误信息，成功返回空串。
    actions 每项: {type: "click"|"fill"|"press"|"wait"|"wait_selector", selector?: str, value?: str, ms?: int, timeout?: int}
    """
    for i, act in enumerate(actions):
        if not isinstance(act, dict):
            continue
        typ = (act.get("type") or "").strip().lower()
        sel = act.get("selector", "").strip()
        logger.debug("[Automation] action[%d] %s selector=%r", i, typ, sel[:80] if sel else "")
        val = act.get("value", "")
        ms = int(act.get("ms") or act.get("timeout") or 500)
        sel_timeout = int(act.get("timeout") or act.get("ms") or 5) * 1000

        try:
            if typ == "click":
                if sel:
                    force = bool(act.get("force", False))
                    loc = page.locator(sel).first
                    if force:
                        loc.click(timeout=sel_timeout, force=True)
                    else:
                        loc.click(timeout=sel_timeout)
                else:
                    return f"action[{i}] click 缺少 selector"
            elif typ == "click_if_exists":
                if sel:
                    try:
                        force = bool(act.get("force", False))
                        t = min(int(act.get("timeout") or 3) * 1000, 5000)
                        loc = page.locator(sel).first
                        loc.click(timeout=t, force=force)
                    except Exception as e:
                        logger.debug("[Automation] action[%d] click_if_exists: %s, continuing", i, e)
                else:
                    return f"action[{i}] click_if_exists 缺少 selector"
            elif typ == "click_expand":
                # Element UI 子菜单：仅当折叠时点击，避免误折叠已展开项（刷新后侧栏状态会保持）
                txt = (act.get("text") or "").strip()
                if sel or txt:
                    loc = page.locator(sel).first if sel else page.locator(f".el-menu >> text={txt}").first
                    try:
                        loc.scroll_into_view_if_needed(timeout=5000)
                        page.wait_for_timeout(150)
                        is_expanded = loc.evaluate("""
                            el => {
                                const li = el.closest('li[class*="sub-menu"], li[aria-expanded]');
                                return li ? li.getAttribute('aria-expanded') === 'true' : false;
                            }
                        """)
                        if not is_expanded:
                            # 优先点击 .el-submenu__title（Element UI 可点击区域）
                            if txt:
                                title_loc = page.locator(f".el-menu .el-submenu__title:has-text('{txt}')").first
                                try:
                                    title_loc.scroll_into_view_if_needed(timeout=5000)
                                    page.wait_for_timeout(100)
                                    title_loc.click(timeout=sel_timeout, force=True)
                                except Exception:
                                    loc.scroll_into_view_if_needed(timeout=5000)
                                    loc.click(timeout=sel_timeout, force=True)
                            else:
                                loc.click(timeout=sel_timeout, force=True)
                    except Exception:
                        loc.scroll_into_view_if_needed(timeout=5000)
                        loc.click(timeout=sel_timeout, force=True)
                else:
                    return f"action[{i}] click_expand 缺少 selector 或 text"
            elif typ == "fill":
                if sel and val is not None:
                    page.locator(sel).first.fill(str(val), timeout=sel_timeout)
                else:
                    return f"action[{i}] fill 缺少 selector 或 value"
            elif typ == "press":
                key = act.get("key") or val or "Enter"
                if sel:
                    page.locator(sel).first.press(key, timeout=sel_timeout)
                else:
                    page.keyboard.press(key)
            elif typ == "wait":
                if sel:
                    page.wait_for_selector(sel, timeout=sel_timeout)
                if ms and ms > 0:
                    page.wait_for_timeout(min(ms, 10000))
            elif typ == "wait_visible":
                if sel:
                    page.locator(sel).first.wait_for(state="visible", timeout=sel_timeout)
                else:
                    return f"action[{i}] wait_visible 缺少 selector"
            elif typ == "wait_attached":
                if sel:
                    page.locator(sel).first.wait_for(state="attached", timeout=sel_timeout)
                else:
                    return f"action[{i}] wait_attached 缺少 selector"
            elif typ == "expand_sidebar_if_collapsed":
                # 侧栏折叠时菜单文字 visibility:hidden，需展开或强制显示（同事侧栏默认展开故无此问题）
                try:
                    if page.locator(".el-menu.el-menu--collapse").count() > 0:
                        clicked = page.evaluate("""
                            () => {
                                const icons = document.querySelectorAll('[class*="el-icon-s-unfold"]');
                                for (const el of icons) {
                                    if (el.closest('.el-menu')) continue;
                                    el.click();
                                    return true;
                                }
                                const btns = document.querySelectorAll('div.fixed.top-0 button:not(.reset-btn), .el-aside button:not(.reset-btn)');
                                for (const btn of btns) {
                                    if (btn.offsetParent !== null) { btn.click(); return true; }
                                }
                                return false;
                            }
                        """)
                        if not clicked:
                            page.evaluate("() => { document.querySelectorAll('.el-menu.el-menu--collapse').forEach(m => m.classList.remove('el-menu--collapse')); }")
                        page.evaluate("""
                            () => {
                                if (document.getElementById('bi-scraper-menu-visible')) return;
                                const s = document.createElement('style');
                                s.id = 'bi-scraper-menu-visible';
                                s.textContent = '.el-menu--collapse .el-submenu__title span, .el-menu--collapse .el-menu-item span { visibility: visible !important; }';
                                document.head.appendChild(s);
                            }
                        """)
                        page.wait_for_timeout(500)
                except Exception as e:
                    logger.debug("[Automation] expand_sidebar_if_collapsed: %s, continuing", e)
            elif typ == "wait_ms":
                page.wait_for_timeout(min(int(act.get("ms", 500)), 10000))
            elif typ == "click_expand_first_row":
                # 日活/日新统计表：点击首行日期或展开图标，展开渠道明细
                # 优先点 .el-table__expand-icon，否则点首行首列（日期）
                sel = ".el-table__body-wrapper tbody tr:first-child .el-table__expand-icon, .el-table__body-wrapper tbody tr:first-child td:first-child .el-table__expand-icon"
                try:
                    page.locator(sel).first.click(timeout=3000)
                except Exception:
                    page.locator(".el-table__body-wrapper tbody tr:first-child td:first-child").first.click(timeout=3000)
            elif typ == "fill_date_range":
                # 日期范围：填写开始、结束两个输入框
                start_val = act.get("start") or act.get("value", "")
                end_val = act.get("end", "")
                start_sel = act.get("start_selector") or sel
                end_sel = act.get("end_selector") or ""
                optional = act.get("optional", False)
                try:
                    if start_sel and start_val:
                        page.locator(start_sel).first.fill(str(start_val), timeout=sel_timeout)
                    if end_sel and end_val:
                        page.locator(end_sel).first.fill(str(end_val), timeout=sel_timeout)
                    # 关闭可能打开的日期选择器弹窗，避免遮挡后续点击
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(200)
                except Exception as fill_err:
                    if optional:
                        logger.debug("[Automation] fill_date_range optional 失败，继续: %s", fill_err)
                    else:
                        raise
            elif typ == "wait_for_data_ready":
                # 等待数据完全加载：优先等待加载遮罩消失，否则固定等待
                wait_ms = int(act.get("wait_after_query_ms") or 5000)
                loading_sel = (act.get("wait_for_loading_hidden") or "").strip()
                t_sec = int(act.get("timeout") or 30) * 1000
                try:
                    if loading_sel:
                        # 等待加载遮罩隐藏（Element UI: .el-loading-mask）
                        page.locator(loading_sel).first.wait_for(state="hidden", timeout=t_sec)
                        page.wait_for_timeout(min(wait_ms, 3000))  # 额外缓冲
                    else:
                        page.wait_for_timeout(min(wait_ms, 120000))
                except Exception:
                    # 无加载遮罩或超时：回退到固定等待
                    page.wait_for_timeout(min(wait_ms, 120000))
        except Exception as e:
            logger.warning("[Automation] action[%d] %s failed: %s", i, typ, e)
            return f"action[{i}] {typ} 失败: {e}"
    return ""


def _expand_all_table_rows(
    page: Any,
    *,
    expand_selector: str = ".el-table__expand-icon:not(.el-table__expand-icon--expanded)",
    wait_ms: int = 400,
    max_rounds: int = 100,
) -> None:
    """
    抓取前展开表格内所有可展开的树形行，确保子项（子渠道、各游戏明细等）被纳入抓取。

    支持两种模式：
    1. Element UI 标准：.el-table__expand-icon:not(.el-table__expand-icon--expanded)
    2. 自定义树形（平台产销等）：div[style*="cursor"] + getIndent，按行序展开，避免反复点同一图标
    """
    # 平台产销/平台产销情况：有 div[style*="cursor"] 时用自定义路径（按行序+缩进），避免反复点「全部汇总」
    # 游戏数据统计等：仅有 .el-table__expand-icon 时走标准 Playwright 路径，逐行展开
    has_cursor = page.locator(".el-table__body-wrapper div[style*='cursor']").count() > 0
    has_std_icon = page.locator(".el-table__body-wrapper .el-table__expand-icon").count() > 0
    use_custom_first = has_cursor and not has_std_icon  # 仅平台产销用自定义；游戏数据用标准

    # 标准路径（游戏数据、平台产销等）：逐行点击未展开图标
    for round_idx in range(max_rounds):
        if use_custom_first:
            count = 0  # 强制走自定义路径
        else:
            icons = page.locator(expand_selector)
            count = icons.count()
        if count == 0:
            # 标准选择器无结果时，尝试自定义树形（平台产销情况对比等）
            clicked = page.evaluate(
                """
                () => {
                    const rows = document.querySelectorAll('.el-table__body-wrapper tbody tr');
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
                    const isChildRow = (r, prev) => {
                        if (getIndent(r) > getIndent(prev)) return true;
                        const c0 = r.querySelector('td:first-child');
                        const t0 = c0 ? (c0.innerText || '').trim() : '';
                        const dateLike = /^\\d{4}-\\d{2}-\\d{2}/.test(t0) || /\\d{4}-\\d{2}-\\d{2}/.test(t0);
                        if (!dateLike && prev) return true;
                        return false;
                    };
                    for (const row of rows) {
                        const next = row.nextElementSibling;
                        if (next) {
                            if (/el-table__row--level-1|level-1/.test(next.className || '')) continue;
                            if (getIndent(next) > getIndent(row)) continue;
                            if (isChildRow(next, row)) continue;
                        }
                        const cells = row.querySelectorAll('td');
                        for (const cell of cells) {
                            let target = cell.querySelector('.cell div[style*="cursor"]');
                            if (!target) target = cell.querySelector('.cell span[style*="cursor"]');
                            if (!target) target = cell.querySelector('div[style*="cursor"]');
                            if (!target) target = cell.querySelector('span[style*="cursor"]');
                            if (!target) target = cell.querySelector('.el-table__expand-icon:not(.el-table__expand-icon--expanded)');
                            if (!target) target = cell.querySelector('.caret-wrapper .el-table__expand-icon:not(.el-table__expand-icon--expanded)');
                            if (!target) target = cell.querySelector('[class*="expand-icon"]:not([class*="expanded"])');
                            if (!target) continue;
                            target.scrollIntoView({ block: 'center', behavior: 'instant' });
                            target.click();
                            return 1;
                        }
                    }
                    return 0;
                }
                """
            )
            if clicked:
                page.wait_for_timeout(min(wait_ms, 2000))
                continue
            if has_std_icon and round_idx == 0:
                try:
                    icons_loc = page.locator(expand_selector)
                    if icons_loc.count() > 0:
                        icons_loc.first.scroll_into_view_if_needed(timeout=3000)
                        page.wait_for_timeout(100)
                        icons_loc.first.click(timeout=3000, force=True)
                        page.wait_for_timeout(min(wait_ms, 2000))
                        continue
                except Exception:
                    pass
            if round_idx > 0:
                logger.debug("[Expand Table] 已全部展开，共 %d 轮", round_idx)
            return
        try:
            first = icons.first
            first.scroll_into_view_if_needed(timeout=5000)
            page.wait_for_timeout(200)
            first.click(timeout=5000, force=True)
            page.wait_for_timeout(min(wait_ms, 2000))
        except Exception as e:
            try:
                expanded = page.evaluate(
                    """
                    (sel) => {
                        const icons = document.querySelectorAll(sel);
                        if (icons.length === 0) return 0;
                        icons[0].scrollIntoView({ block: 'center', behavior: 'instant' });
                        icons[0].click();
                        return 1;
                    }
                    """,
                    expand_selector,
                )
                if expanded:
                    page.wait_for_timeout(min(wait_ms, 2000))
                else:
                    logger.debug("[Expand Table] 第 %d 轮点击异常: %s", round_idx + 1, e)
                    return
            except Exception as e2:
                logger.debug("[Expand Table] 第 %d 轮点击异常: %s", round_idx + 1, e2)
                return


def _harvest_expand_extract_collapse_loop(
    page: Any,
    automation: dict,
) -> tuple[list[dict[str, Any]], str]:
    """
    逐行「展开→提取→折叠→下一行」抓取。适用于 stats_game_daily、stats_game_compare 等页面：
    展开一行会占满整页，无法点击其他日期行，必须折叠后再展开下一行。

    automation 可选：
      expand_target_column: 0=首列(默认), 1=第二列(对比页统计范围)
      expand_parent_full_cell: True 时用首列完整文本作父级标识(如 "2026-03-13 VS 2026-03-06")
      expand_capture_first_rows: 展开循环前先抓取的首行数（如汇总行）
    """
    import re as _re
    _date_re = _re.compile(r"^\d{4}-\d{2}-\d{2}")
    expand_wait = int(automation.get("expand_wait_ms") or 1500)
    expand_wait = max(500, min(expand_wait, 3000))
    collapse_wait = 600
    max_rounds = int(automation.get("expand_max_rounds") or 20)
    skip_first = int(automation.get("expand_skip_first_rows") or 0)
    capture_first = int(automation.get("expand_capture_first_rows") or 0)
    split_merged = automation.get("split_merged_cells", True)
    expand_target_col = int(automation.get("expand_target_column") or 0)
    parent_full_cell = automation.get("expand_parent_full_cell", False)

    # 取表头
    header_cells = [
        h.strip()
        for h in page.locator(
            ".el-table__header-wrapper thead th, .el-table__header-wrapper thead td"
        ).all_text_contents()
        if h.strip()
    ]
    if not header_cells:
        header_cells = [
            h.strip()
            for h in page.locator("table thead th, table thead td").all_text_contents()
            if h.strip()
        ]
    if not header_cells:
        return [], "未找到表头"

    all_rows: list[dict[str, Any]] = []

    # 展开循环前先抓取首行（如汇总行）
    if capture_first > 0:
        first_rows = page.evaluate(
            """
            (n) => {
                const w = Array.from(document.querySelectorAll('.el-table__body-wrapper')).find(x => !x.closest('.el-picker-panel'));
                if (!w) return null;
                const trs = w.querySelectorAll('tbody tr');
                const rows = [];
                for (let i = 0; i < Math.min(n, trs.length); i++) {
                    const tds = trs[i].querySelectorAll('td');
                    rows.push(Array.from(tds).map(t => (t.innerText || '').trim()));
                }
                return rows.length ? rows : null;
            }
            """,
            capture_first,
        )
        if first_rows:
            for ncells in first_rows:
                if split_merged:
                    ncells = [_split_merged_cell_value(c) for c in ncells]
                if len(ncells) >= len(header_cells):
                    all_rows.append(dict(zip(header_cells, ncells[: len(header_cells)])))
                elif ncells:
                    pad = [""] * (len(header_cells) - len(ncells))
                    all_rows.append(dict(zip(header_cells, ncells + pad)))

    rounds_done = 0
    for round_i in range(max_rounds):
        # 1. 点击第 skip_first+round_i+1 行展开（可跳过首行如总计行）
        row_idx = skip_first + round_i + 1
        clicked = page.evaluate(
            """
            (args) => {
                const idx = args.idx, targetCol = args.targetCol;
                const w = Array.from(document.querySelectorAll('.el-table__body-wrapper')).find(x => !x.closest('.el-picker-panel'));
                if (!w) return 0;
                const trs = w.querySelectorAll('tbody tr');
                const tr = trs[idx - 1];
                if (!tr) return 0;
                const tds = tr.querySelectorAll('td');
                const cell = tds[targetCol] || tds[0];
                if (!cell) return 0;
                const target = cell.querySelector('.date-expand-label') || cell.querySelector('.expand-btn') || cell.querySelector('.el-table__expand-icon') || cell.querySelector('.cell') || cell;
                target.scrollIntoView({ block: 'center', behavior: 'instant' });
                target.click();
                return 1;
            }
            """,
            {"idx": row_idx, "targetCol": expand_target_col},
        )
        if not clicked:
            break
        rounds_done += 1
        page.wait_for_timeout(expand_wait)

        # 2. 提取当前展开行的子行（stats_game_daily 为同级 tr，展开行在 trs[row_idx-1]）
        extracted = page.evaluate(
            """
            (args) => {
                const expandedRowIdx = args.expandedRowIdx, useFullParent = args.useFullParent;
                const wrapper = document.querySelector('.el-table__body-wrapper');
                if (!wrapper || wrapper.closest('.el-picker-panel')) return null;
                const trs = wrapper.querySelectorAll('tbody tr');
                const parentTr = trs[expandedRowIdx - 1];
                if (!parentTr) return null;
                const dateTd = parentTr.querySelector('td:first-child');
                const c0raw = dateTd ? (dateTd.innerText || '').trim() : '';
                let parentDate = c0raw.match(/^\\d{4}-\\d{2}-\\d{2}/)?.[0] || '';
                if (useFullParent && /\\d{4}-\\d{2}-\\d{2}.*VS.*\\d{4}-\\d{2}-\\d{2}/.test(c0raw)) parentDate = c0raw;
                if (!parentDate && useFullParent) {
                    const c1raw = parentTr.querySelector('td:nth-child(2)') ? (parentTr.querySelector('td:nth-child(2)').innerText || '').trim() : '';
                    if (/\\d{4}-\\d{2}-\\d{2}.*VS.*\\d{4}-\\d{2}-\\d{2}/.test(c1raw)) parentDate = c1raw;
                }
                if (!parentDate) return null;
                const rows = [];
                for (let i = expandedRowIdx; i < trs.length; i++) {
                    const tr = trs[i];
                    const tds = tr.querySelectorAll('td');
                    const c0 = tds[0] ? (tds[0].innerText || '').trim() : '';
                    if (/^\\d{4}-\\d{2}-\\d{2}/.test(c0) || (c0.indexOf('VS') >= 0 && /\\d{4}-\\d{2}-\\d{2}/.test(c0))) break;
                    rows.push({
                        cells: Array.from(tds).map(t => (t.innerText || '').trim()),
                        parentDate,
                    });
                }
                return rows.length ? rows : null;
            }
            """,
            {"expandedRowIdx": row_idx, "useFullParent": parent_full_cell},
        )
        if extracted:
            for item in extracted:
                ncells = item.get("cells") or []
                parent_date = item.get("parentDate") or ""
                if split_merged:
                    ncells = [_split_merged_cell_value(c) for c in ncells]
                if ncells and parent_date and not _date_re.match(ncells[0] if ncells else ""):
                    # 子行首列空或非日期：插入父级标识到首列
                    ncells = [parent_date] + (ncells[1:] if ncells and not (ncells[0] or "").strip() else ncells)
                if len(ncells) >= len(header_cells):
                    all_rows.append(dict(zip(header_cells, ncells[: len(header_cells)])))
                elif len(ncells) == len(header_cells) - 1 and parent_date:
                    all_rows.append(dict(zip(header_cells, [parent_date] + ncells)))

        # 3. 折叠当前行（点被展开的那一行的图标）
        page.evaluate(
            """
            (args) => {
                const idx = args.idx, targetCol = args.targetCol;
                const w = Array.from(document.querySelectorAll('.el-table__body-wrapper')).find(x => !x.closest('.el-picker-panel'));
                if (!w) return;
                const trs = w.querySelectorAll('tbody tr');
                const tr = trs[idx - 1];
                if (!tr) return;
                const tds = tr.querySelectorAll('td');
                const cell = tds[targetCol] || tds[0];
                if (!cell) return;
                const target = cell.querySelector('.el-table__expand-icon--expanded') || cell.querySelector('.expand-btn') || cell.querySelector('.date-expand-label') || cell.querySelector('.el-table__expand-icon') || cell.querySelector('.cell') || cell;
                target.scrollIntoView({ block: 'center', behavior: 'instant' });
                target.click();
            }
            """,
            {"idx": row_idx, "targetCol": expand_target_col},
        )
        page.wait_for_timeout(collapse_wait)

    logger.debug("[Scraper] expand_extract_collapse 共 %d 轮，提取 %d 行", rounds_done, len(all_rows))
    return all_rows, ""


def _expand_filters_to_actions(filters: dict) -> list[dict]:
    """
    将 filters 配置展开为 automation actions。
    filters.date_range: [start_date, end_date]
    filters.date_range_selectors: {start, end} 可选
    filters.query_selector: 查询按钮选择器
    filters.wait_after_query_ms: 点击查询后的固定等待毫秒，默认 5000（弱网环境可调大）
    filters.wait_for_loading_hidden: 加载遮罩选择器，等待其隐藏表示数据加载完成
    filters.wait_for_data_timeout: 等待数据就绪的最大秒数，默认 30
    """
    actions: list[dict] = []
    if not filters:
        return actions
    dr = filters.get("date_range")
    dr_compare = filters.get("date_range_compare")  # [[start1,end1],[start2,end2]] 对比页两时间段
    if isinstance(dr_compare, (list, tuple)) and len(dr_compare) >= 2:
        # 对比页：填写两个时间段。第 0 个用通用选择器；第 1 个可选（部分页面 DOM 不同）
        sels_list = filters.get("date_range_compare_selectors") or [{}, {}]
        for i, pr in enumerate(dr_compare[:2]):
            if isinstance(pr, (list, tuple)) and len(pr) >= 2:
                s = sels_list[i] if i < len(sels_list) else {}
                start_sel = s.get("start") or (".el-date-editor input:first-of-type" if i == 0 else ".el-date-editor:nth-of-type(2) input:first-of-type")
                end_sel = s.get("end") or (".el-date-editor input:last-of-type" if i == 0 else ".el-date-editor:nth-of-type(2) input:last-of-type")
                actions.append({
                    "type": "fill_date_range",
                    "start_selector": start_sel,
                    "end_selector": end_sel,
                    "start": str(pr[0]),
                    "end": str(pr[1]),
                    "optional": i > 0,  # 第二时间段可选，失败时继续
                })
    elif isinstance(dr, (list, tuple)) and len(dr) >= 2:
        sels = filters.get("date_range_selectors") or {}
        start_sel = sels.get("start") or ".el-date-editor input:first-of-type"
        end_sel = sels.get("end") or ".el-date-editor input:last-of-type"
        actions.append({
            "type": "fill_date_range",
            "start_selector": start_sel,
            "end_selector": end_sel,
            "start": str(dr[0]),
            "end": str(dr[1]),
        })
    qs = filters.get("query_selector")
    if qs:
        actions.append({"type": "click", "selector": qs})
        # 等待数据加载完成：优先等待加载遮罩消失，否则使用可配置的固定等待
        wait_ms = int(filters.get("wait_after_query_ms") or 5000)
        wait_ms = max(1000, min(wait_ms, 120000))  # 1s~120s
        loading_sel = filters.get("wait_for_loading_hidden") or ""
        data_timeout = int(filters.get("wait_for_data_timeout") or 30)
        data_timeout = max(5, min(data_timeout, 120))
        actions.append({
            "type": "wait_for_data_ready",
            "wait_after_query_ms": wait_ms,
            "wait_for_loading_hidden": loading_sel,
            "timeout": data_timeout,
        })
    # 日活/日新统计表需点击首行展开渠道明细
    if filters.get("expand_first_row"):
        actions.append({"type": "click_expand_first_row"})
        actions.append({"type": "wait_ms", "ms": 800})
    return actions


def _resolve_output_path(output_path: str | Path | None, output_format: str) -> Path:
    """解析输出路径，为空时使用 bi.paths 下 YYYYMMDD.csv/json"""
    if output_path:
        p = Path(output_path)
        if p.suffix.lower() in (".csv", ".json"):
            return p
        return p.with_suffix(".csv" if output_format == "csv" else ".json")
    from l3_node.mcp_tools.bi.paths import get_bi_raw_dir, ensure_bi_dirs
    ensure_bi_dirs()
    date_str = datetime.now().strftime("%Y%m%d")
    ext = ".csv" if output_format == "csv" else ".json"
    return get_bi_raw_dir() / f"{date_str}{ext}"


def _harvest_via_api(url: str, headers: dict, timeout: int) -> tuple[list[dict[str, Any]] | None, str]:
    """API 模式：requests 请求，解析 JSON"""
    try:
        import httpx
        resp = httpx.get(url, headers=headers or {}, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data, ""
        if isinstance(data, dict):
            # 常见结构：{data: [...], rows: [...], list: [...]}
            for key in ("data", "rows", "list", "records", "items"):
                if key in data and isinstance(data[key], list):
                    return data[key], ""
            return [data], ""
        return None, "响应格式不支持"
    except Exception as e:
        return None, str(e)


def _harvest_via_playwright(
    url: str,
    cdp_url: str,
    table_selector: str,
    timeout: int,
    automation: dict | None = None,
) -> tuple[list[dict[str, Any]] | None, str]:
    """SPA 模式：Playwright 连接已登录 Chrome，可选执行 automation 后抓取表格"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, "playwright 未安装，请执行 pip install playwright && playwright install chromium"

    automation = automation or {}
    start_url = automation.get("start_url") or url
    actions: list[dict] = list(automation.get("actions") or [])
    filters = automation.get("filters") or {}
    actions.extend(_expand_filters_to_actions(filters))

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(cdp_url, timeout=5000)
            contexts = browser.contexts
            if not contexts:
                return None, "未找到浏览器上下文，请确保 Chrome 以调试模式启动"
            context = contexts[0]
            pages = context.pages
            if not pages:
                return None, "未找到页面"

            # 查找包含目标 URL 的页面，或使用第一个已登录页
            target_page = None
            for p in pages:
                try:
                    if url in (p.url or "") or start_url in (p.url or ""):
                        target_page = p
                        break
                except Exception:
                    pass
            if not target_page and pages:
                target_page = pages[0]

            if not target_page:
                target_page = context.new_page()

            # 导航到入口页（start_url 或 url）
            nav_url = automation.get("start_url") or url
            try:
                target_page.bring_to_front()
                # 有 automation 时强制导航，确保每次从入口页开始（避免上一项展开的菜单被误点折叠）
                if actions:
                    target_page.goto(nav_url, wait_until="domcontentloaded", timeout=timeout * 1000)
                    target_page.wait_for_load_state("networkidle", timeout=timeout * 1000)
                elif nav_url not in (target_page.url or ""):
                    target_page.goto(nav_url, wait_until="domcontentloaded", timeout=timeout * 1000)
                    target_page.wait_for_load_state("networkidle", timeout=timeout * 1000)
                target_page.wait_for_timeout(1000)  # SPA 挂载缓冲
            except Exception:
                pass

            # 执行自动化操作（点击菜单、填写筛选等）
            if actions:
                err = _run_automation_actions(target_page, actions, timeout * 1000)
                if err:
                    return None, f"自动化步骤失败: {err}"

            # 等待表格加载（排除日期选择器 el-date-table）
            sel = table_selector or "table:not(.el-date-table)"
            try:
                target_page.wait_for_selector(sel, timeout=timeout * 1000)
            except Exception:
                pass

            # stats_game_daily 等：展开会占满整页，无法点其他行 → 逐行「展开→提取→折叠→下一行」
            if automation.get("expand_extract_collapse_loop", False):
                rows, err = _harvest_expand_extract_collapse_loop(target_page, automation)
                if err:
                    return None, err
                browser.close()
                return rows if rows else None, "未提取到表格数据" if not rows else ""
            # 可选：展开所有树形行，抓取子项（渠道明细、各游戏数据等）
            elif automation.get("expand_table_rows", False):
                expand_sel = automation.get("expand_selector") or ".el-table__body-wrapper .el-table__expand-icon:not(.el-table__expand-icon--expanded)"
                expand_wait = int(automation.get("expand_wait_ms") or 600)
                expand_wait = max(200, min(expand_wait, 2000))
                try:
                    _expand_all_table_rows(
                        target_page,
                        expand_selector=expand_sel,
                        wait_ms=expand_wait,
                    )
                    post_wait = int(automation.get("expand_post_wait_ms") or 500)
                    post_wait = max(200, min(post_wait, 3000))
                    target_page.wait_for_timeout(post_wait)
                except Exception as e:
                    logger.warning("[Scraper] 展开表格行时异常（继续抓取）: %s", e)
                try:
                    target_page.evaluate(
                        """
                        () => {
                            const wrappers = document.querySelectorAll('.el-table__body-wrapper');
                            for (const w of wrappers) {
                                if (!w.closest('.el-picker-panel')) {
                                    w.scrollTop = w.scrollHeight;
                                    break;
                                }
                            }
                        }
                        """
                    )
                    target_page.wait_for_timeout(800)
                except Exception:
                    pass

            # 提取表格：Element UI 表头/表体分离，需分别取
            rows = []
            try:
                # Element UI：.el-table__header-wrapper 与 .el-table__body-wrapper 各有一个 table
                header_cells = [h.strip() for h in target_page.locator(".el-table__header-wrapper thead th, .el-table__header-wrapper thead td").all_text_contents() if h.strip()]
                if not header_cells:
                    header_cells = [h.strip() for h in target_page.locator("table thead th, table thead td").all_text_contents() if h.strip()]
                body_trs = target_page.locator(".el-table__body-wrapper table tbody tr, .el-table__body-wrapper tbody tr, .el-table tbody tr").all()
                if not body_trs:
                    body_trs = target_page.locator("table:not(.el-date-table) tbody tr").all()
                split_merged = automation.get("split_merged_cells", True)
                _date_re = re.compile(r"^\d{4}-\d{2}-\d{2}")
                last_date = ""
                if header_cells and body_trs:
                    for tr in body_trs:
                        # 跳过 Element UI 展开行：单 td 含 colspan，内容在 slot 内；需从嵌套表提取
                        tds = tr.locator("td").all()
                        expanded_td = None
                        if len(tds) == 1:
                            expanded_td = tds[0]
                        elif len(tds) > 1:
                            for td in tds:
                                if "expanded-cell" in (td.get_attribute("class") or ""):
                                    expanded_td = td
                                    break
                        if expanded_td is not None:
                            try:
                                nested_table = expanded_td.locator("table").first
                                if nested_table.count() > 0:
                                    nested_trs = nested_table.locator("tbody tr").all()
                                    for ntr in nested_trs:
                                        ncells = ntr.locator("td").all_text_contents()
                                        ncells = [c.strip() for c in ncells]
                                        if split_merged:
                                            ncells = [_split_merged_cell_value(c) for c in ncells]
                                        if ncells and last_date:
                                            if len(ncells) == len(header_cells) - 1:
                                                ncells = [last_date] + ncells
                                            elif not _date_re.match(ncells[0] if ncells else ""):
                                                ncells = [last_date] + ncells
                                        if len(ncells) >= len(header_cells):
                                            rows.append(dict(zip(header_cells, ncells[: len(header_cells)])))
                                    continue
                            except Exception:
                                pass
                        # 单 td 且无嵌套表：视为展开行占位，跳过（避免误当普通行）
                        if len(tds) == 1 and expanded_td is not None:
                            continue
                        cells = tr.locator("td").all_text_contents()
                        cells = [c.strip() for c in cells]
                        if split_merged:
                            cells = [_split_merged_cell_value(c) for c in cells]
                        if len(cells) == len(header_cells) - 1 and len(header_cells) >= 2:
                            cells = [last_date] + cells
                        if cells and len(cells) >= len(header_cells) and _date_re.match(cells[0]):
                            last_date = cells[0]
                        if len(cells) >= len(header_cells):
                            rows.append(dict(zip(header_cells, cells[: len(header_cells)])))
                        elif cells:
                            rows.append({"col_0": cells[0], "data": " | ".join(cells[1:])})
                if not rows and body_trs:
                    for tr in body_trs:
                        cells = tr.locator("td").all_text_contents()
                        cells = [c.strip() for c in cells if c]
                        if split_merged:
                            cells = [_split_merged_cell_value(c) for c in cells]
                        if cells:
                            rows.append({f"col_{i}": v for i, v in enumerate(cells)})
            except Exception as e:
                return None, f"表格提取失败: {e}"

            browser.close()
            return rows if rows else None, "未提取到表格数据" if not rows else ""
    except Exception as e:
        err = str(e)
        if "connect" in err.lower() or "Target" in err:
            return None, f"{err}\n提示：请用 Chrome 调试模式启动（--remote-debugging-port=9222）"
        return None, err


def harvest_table_data(
    url: str,
    output_path: str | Path | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    抓取网页/后台表格数据，保存为 CSV 或 JSON。

    Args:
        url: 目标 URL（页面或 API）
        output_path: 输出路径，为空时使用 bi.paths 下 YYYYMMDD.csv/json
        config: 可选 {
            extract_rules: str 表格 CSS 选择器（SPA 模式）,
            output_format: "json"|"csv",
            headers: dict HTTP 请求头（API 模式）,
            timeout: int 秒,
            cdp_url: str Chrome 调试地址（SPA 模式，默认 http://127.0.0.1:9222）,
            automation: dict 自动化配置 {start_url, actions: [{type,selector,value}], filters: {date_range,query_selector}}
        }

    Returns:
        {"status": "success", "file_path": "..."} 或 {"status": "error", "error": "..."}
    """
    config = config or {}
    output_format = (config.get("output_format") or "json").lower()
    if output_format not in ("json", "csv"):
        output_format = "json"
    timeout = int(config.get("timeout") or 30)
    headers = config.get("headers") or {}
    cdp_url = (config.get("cdp_url") or "http://127.0.0.1:9222").rstrip("/")
    extract_rules = config.get("extract_rules")
    table_selector = extract_rules if isinstance(extract_rules, str) else str(extract_rules or "")

    if not url or not url.strip():
        return {"status": "error", "error": "url 不能为空"}

    out_path = _resolve_output_path(output_path, output_format)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 优先 API 模式：url 含 /api/ 或显式指定 headers
    use_api = "/api/" in url or "api." in url or headers
    automation = config.get("automation")
    if use_api and not config.get("cdp_url"):
        rows, err = _harvest_via_api(url, headers, timeout)
    else:
        # SPA 模式：连接已登录 Chrome，可选执行 automation
        rows, err = _harvest_via_playwright(
            url, cdp_url, table_selector, timeout, automation=automation
        )

    if err:
        return {"status": "error", "error": err}
    if not rows:
        return {"status": "error", "error": "未获取到数据"}

    def _write_rows(target: Path) -> None:
        if output_format == "csv":
            with open(target, "w", newline="", encoding="utf-8") as f:
                if rows:
                    writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()), extrasaction="ignore")
                    writer.writeheader()
                    writer.writerows(rows)
        else:
            with open(target, "w", encoding="utf-8") as f:
                json.dump(rows, f, ensure_ascii=False, indent=2)

    try:
        _write_rows(out_path)
        return {"status": "success", "file_path": str(out_path), "rows_count": len(rows)}
    except OSError as e:
        if e.errno == 13:  # Permission denied
            fallback = Path.cwd() / "bi_data" / "raw" / out_path.name
            try:
                fallback.parent.mkdir(parents=True, exist_ok=True)
                _write_rows(fallback)
                logger.info("[Scraper] ~/.jachin 无写权限，已回退至 %s", fallback)
                return {"status": "success", "file_path": str(fallback), "rows_count": len(rows)}
            except Exception as e2:
                return {"status": "error", "error": f"{e}; 回退路径也失败: {e2}"}
        return {"status": "error", "error": str(e)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


if __name__ == "__main__":
    # 本地测试：需 Chrome 调试模式 + 已登录 bi-admin
    # Chrome 启动: chrome.exe --remote-debugging-port=9222
    # 注意：person 是首页/个人信息，不含业务数据；需用「平台数据/统计分析/明细」页的实际 URL
    from l3_node.mcp_tools.bi.paths import get_bi_raw_dir, ensure_bi_dirs
    ensure_bi_dirs()
    out = str(get_bi_raw_dir() / "test.csv")
    # 将下方 URL 替换为点击左侧数据菜单后地址栏的实际路径
    data_url = "https://bi-admin-web.heronpro.xin/#/layout/person"  # 示例，请改为数据页 URL
    r = harvest_table_data(data_url, output_path=out, config={
        "cdp_url": "http://127.0.0.1:9222", "output_format": "csv", "timeout": 15,
    })
    print(r)
