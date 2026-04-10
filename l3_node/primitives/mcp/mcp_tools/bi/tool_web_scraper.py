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
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_DIFF_PREFIX = "[DIFF-LOG] | "


def _diff_log(msg: str) -> None:
    """终端 + 日志双写，便于 Beyond Compare 比对两台机器输出。"""
    line = f"{_DIFF_PREFIX}{msg}"
    print(line, flush=True)
    logger.info("%s", line)


def _safe_page_url(page: Any) -> str:
    try:
        u = (page.url or "").strip()
        return u if u else "(empty)"
    except Exception:
        return "(url_unavailable)"


def _cdp_route_identity(u: str) -> str | None:
    """
    用于在 CDP 多标签下选对页：按「主机 + hash 路由」归一化。
    禁止再用 `target in page.url`：例如 biUserDailySummary 会误匹配 biUserDailySummaryCompare。
    """
    u = (u or "").strip()
    if not u.lower().startswith("http"):
        return None
    try:
        p = urlparse(u)
        host = (p.netloc or "").lower()
        if not host:
            return None
        frag = (p.fragment or "").split("?")[0].rstrip("/")
        path = (p.path or "").rstrip("/")
        route = frag if frag else path
        return f"{host}|{route}"
    except Exception:
        return None


def _same_cdp_route(url_a: str, url_b: str) -> bool:
    """两 URL 是否同一 BI hash 路由（避免 substring 误判）。"""
    ia, ib = _cdp_route_identity(url_a), _cdp_route_identity(url_b)
    if ia and ib:
        return ia == ib
    return (url_a or "").strip().rstrip("/") == (url_b or "").strip().rstrip("/")


def _pick_cdp_target_page(pages: list[Any], url: str, start_url: str) -> Any | None:
    """在 connect_over_cdp 的 context.pages 里选要操作的 Page；优先与目标 URL 路由完全一致。"""
    if not pages:
        return None
    want = {_cdp_route_identity(url), _cdp_route_identity(start_url)}
    want.discard(None)
    for p in pages:
        try:
            pu = (p.url or "").strip()
        except Exception:
            continue
        rid = _cdp_route_identity(pu)
        if rid and rid in want:
            return p
    # 无路由级命中：选与目标同主机的标签（优先最后一个，常见为最近打开的 BI 页）
    ref = start_url or url
    try:
        host = urlparse(ref).netloc.lower() if ref.lower().startswith("http") else ""
    except Exception:
        host = ""
    same_host: list[Any] = []
    if host:
        for p in pages:
            try:
                pu = (p.url or "").strip()
            except Exception:
                continue
            try:
                if urlparse(pu).netloc.lower() == host:
                    same_host.append(p)
            except Exception:
                continue
    return same_host[-1] if same_host else pages[0]


def _spa_anti_ghost_settle(page: Any, diff_label: str, phase: str) -> None:
    """
    Hash 路由 SPA 反残影：避免 page.goto 后旧页表格仍在 DOM，wait_for_selector(table) 误匹配上一路由的数据。
    1) 固定缓冲，让 Vue 拆掉旧视图、挂上 loading
    2) 等待所有 .el-loading-mask 在布局上不可见（多遮罩时用 JS 判断，比单 selector 可靠）
    """
    label = diff_label or "—"
    _diff_log(f"[{label}] | SPA 反残影({phase}): 缓冲 1500ms …")
    page.wait_for_timeout(1500)
    try:
        page.wait_for_function(
            """() => {
                const nodes = document.querySelectorAll('.el-loading-mask');
                for (const n of nodes) {
                    const r = n.getBoundingClientRect();
                    const s = window.getComputedStyle(n);
                    const shown = s.display !== 'none' && s.visibility !== 'hidden'
                        && parseFloat(s.opacity || '1') > 0.01;
                    if (shown && r.width > 0 && r.height > 0) return false;
                }
                return true;
            }""",
            timeout=15000,
        )
        _diff_log(f"[{label}] | SPA 反残影({phase}): 已无可见 Element UI loading 遮罩")
    except Exception as e:
        _diff_log(f"[{label}] | SPA 反残影({phase}): loading 等待结束(超时或忽略): {type(e).__name__}: {e}")


def _log_locator_diag(page: Any, selector: str, ctx: str) -> None:
    """打印 locator.count() 与第一个匹配可见性（不改动 DOM）。"""
    sel = (selector or "").strip()
    if not sel:
        _diff_log(f"[{ctx}] | Selector [(无)] 跳过 count/可见性诊断")
        return
    try:
        loc = page.locator(sel)
        cnt = loc.count()
        first_vis = "N/A"
        if cnt > 0:
            try:
                first_vis = str(loc.first.is_visible())
            except Exception as ve:
                first_vis = f"(is_visible异常:{type(ve).__name__}:{ve})"
        _diff_log(f"[{ctx}] | Selector [{sel}] 找到元素数量: {cnt}, 第一个元素可见状态: {first_vis}")
    except Exception as e:
        _diff_log(f"[{ctx}] | Selector [{sel}] 诊断异常: {type(e).__name__}: {e}")


def _locate_date_editor_by_form_item_label(page: Any, label_text: str, ctx: str, sel_timeout: int) -> Any:
    """
    留存对比等页有两个并排 .el-date-editor，按「第 N 个可见」易点到错误实例或填不进 Vue。
    通过 .el-form-item 内标签文案（如 时间段1 / 时间段2）定位对应日期范围框。
    """
    txt = str(label_text).strip()
    if not txt:
        raise ValueError("form_item_label 为空")
    pat = re.compile(re.escape(txt))
    t = min(int(sel_timeout), 25000)
    candidates = [
        page.locator(".el-form-item")
        .filter(has=page.locator(".el-form-item__label").filter(has_text=pat))
        .locator(".el-date-editor")
        .first,
        page.locator(".el-form-item")
        .filter(has=page.locator("label").filter(has_text=pat))
        .locator(".el-date-editor")
        .first,
    ]
    last_err: Exception | None = None
    for ed in candidates:
        try:
            ed.wait_for(state="visible", timeout=t)
            _diff_log(f"[{ctx}] | fill_date_range 按表单项标签 {txt!r} 已定位到 .el-date-editor")
            return ed
        except Exception as e:
            last_err = e
            continue
    raise TimeoutError(
        f"未找到标签含 {txt!r} 的表单项下的 .el-date-editor（已尝试 el-form-item__label / label）。"
        f" 上一错误: {last_err!r}"
    )


def _soft_pause_after_range_cell_fill(page: Any, ctx: str) -> None:
    """对比页勿按 Escape：用户反馈选完日期面板自行关闭；Escape 会把整段筛选区从 DOM 卸掉（.el-date-editor 变 0）。"""
    page.wait_for_timeout(350)


def _set_el_range_input_value(inp: Any, value: str, sel_timeout: int) -> None:
    """对 .el-range-input 写入并触发 Vue 常见监听（fill 单独有时第二段不生效）。"""
    v = str(value or "").strip()
    if not v:
        return
    inp.click(timeout=min(sel_timeout, 8000))
    inp.fill(v, timeout=sel_timeout)
    try:
        inp.evaluate(
            """(el) => {
                try {
                    const ev = new Event('input', { bubbles: true });
                    Object.defineProperty(ev, 'target', { value: el, enumerable: true });
                    el.dispatchEvent(ev);
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                } catch (e) {}
            }"""
        )
    except Exception:
        pass


def _fill_el_date_range_inputs(
    page: Any,
    chosen_ed: Any,
    start_val: str,
    end_val: str,
    sel_timeout: int,
    ctx: str,
) -> None:
    """Element Plus 范围选择：逐格点击 .el-range-input 填写并同步 Vue；结束后面板收起便于下一段。"""
    chosen_ed.scroll_into_view_if_needed(timeout=min(sel_timeout, 15000))
    ins = chosen_ed.locator("input.el-range-input")
    if ins.count() == 0:
        ins = chosen_ed.locator("input")
    ic = ins.count()
    _diff_log(f"[{ctx}] | fill_date_range 目标编辑器内 input 数量={ic}")
    if start_val and ic > 0:
        _set_el_range_input_value(ins.nth(0), str(start_val), sel_timeout)
        page.wait_for_timeout(80)
    if end_val and ic > 1:
        _set_el_range_input_value(ins.nth(1), str(end_val), sel_timeout)
        page.wait_for_timeout(80)
    _soft_pause_after_range_cell_fill(page, ctx)


def _clear_date_editor_scraper_marks(page: Any) -> None:
    try:
        page.evaluate(
            """() => {
                document.querySelectorAll('[data-bi-scraper-order]').forEach(
                    (e) => e.removeAttribute('data-bi-scraper-order')
                );
            }"""
        )
    except Exception:
        pass


def _mark_main_date_editors_by_visual_x(page: Any, ctx: str) -> int:
    """
    主内容区内可见的 .el-date-editor 按视口 X（左→右）编号到 data-bi-scraper-order。
    只排除挂在日历面板 DOM 内的节点；勿用 closest('.el-popper') —— Element Plus 常把表单项上的
    日期触发器也包在含 el-popper 的祖先里，首段填完后会误杀全部，第二次打标变成 0 个。
    """
    diag = page.evaluate(
        """() => {
            document.querySelectorAll('[data-bi-scraper-order]').forEach(
                (e) => e.removeAttribute('data-bi-scraper-order')
            );
            const all = document.querySelectorAll('.el-date-editor');
            let skipPicker = 0, skipDisplay = 0, skipRect = 0;
            const candidates = [];
            for (const el of all) {
                if (el.closest('.el-picker-panel')) {
                    skipPicker++;
                    continue;
                }
                const r = el.getBoundingClientRect();
                const s = window.getComputedStyle(el);
                if (s.display === 'none' || s.visibility === 'hidden') {
                    skipDisplay++;
                    continue;
                }
                if (parseFloat(s.opacity || '1') < 0.05) {
                    skipDisplay++;
                    continue;
                }
                if (r.width < 2 || r.height < 2) {
                    skipRect++;
                    continue;
                }
                candidates.push({ el, x: r.left + r.width * 0.5 });
            }
            candidates.sort((a, b) => a.x - b.x);
            candidates.forEach((c, i) => {
                c.el.setAttribute('data-bi-scraper-order', String(i));
            });
            return {
                n: candidates.length,
                total: all.length,
                skipPicker,
                skipDisplay,
                skipRect,
            };
        }"""
    )
    n = int(diag.get("n", 0))
    _diff_log(
        f"[{ctx}] | fill_date_range 视觉序打标: 入选={n} "
        f"DOM中.el-date-editor总数={diag.get('total')} "
        f"跳过(在.el-picker-panel内)={diag.get('skipPicker')} "
        f"跳过(display/opacity)={diag.get('skipDisplay')} "
        f"跳过(宽高过小)={diag.get('skipRect')}"
    )
    return n


def _pick_date_editor_visible_main_form(
    page: Any,
    want_idx: int,
    ctx: str,
    sel_timeout: int,
) -> Any:
    """
    回退：Playwright is_visible + 排除 .el-picker-panel 内节点，按 bounding_box 中心 X 左→右取第 want_idx 个。
    """
    roots = page.locator(".el-date-editor")
    nscan = min(roots.count(), 48)
    scored: list[tuple[float, int]] = []
    for j in range(nscan):
        cell = roots.nth(j)
        try:
            if not cell.is_visible(timeout=600):
                continue
        except Exception:
            continue
        try:
            in_panel = cell.evaluate("el => !!el.closest('.el-picker-panel')")
        except Exception:
            in_panel = False
        if in_panel:
            continue
        try:
            box = cell.bounding_box()
            if not box or box.get("width", 0) < 2 or box.get("height", 0) < 2:
                continue
            cx = float(box["x"]) + float(box["width"]) * 0.5
            scored.append((cx, j))
        except Exception:
            continue
    scored.sort(key=lambda t: t[0])
    if want_idx >= len(scored):
        raise TimeoutError(
            f"回退定位失败：可见且非 picker 内 .el-date-editor 仅 {len(scored)} 个（按 X 排序），需要第 {want_idx} 个"
        )
    j_pick = scored[want_idx][1]
    _diff_log(
        f"[{ctx}] | fill_date_range 回退定位: 按 X 左→右第 {want_idx} 个 → DOM 序号 j={j_pick}（共候选 {len(scored)}）"
    )
    return roots.nth(j_pick)


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


def _diff_action_done(step_i: int, t0: float, ctx: str) -> None:
    elapsed = int((time.perf_counter() - t0) * 1000)
    _diff_log(f"[{ctx}] | 动作执行完毕: 步骤序号[{step_i}] 耗时: {elapsed} ms")


def _run_automation_actions(
    page: Any,
    actions: list[dict],
    timeout_ms: int,
    context_label: str = "",
) -> str:
    """
    按顺序执行自动化操作。失败时返回错误信息，成功返回空串。
    actions 每项: {type: "click"|"fill"|"press"|"wait"|"wait_selector", selector?: str, value?: str, ms?: int, timeout?: int}
    """
    ctx = (context_label or "").strip() or "—"
    for i, act in enumerate(actions):
        if not isinstance(act, dict):
            _diff_log(f"[{ctx}] | 跳过非 dict 动作: 索引[{i}]")
            continue
        typ = (act.get("type") or "").strip().lower()
        sel = act.get("selector", "").strip()
        error_diag_sel = sel  # 异常时 locator 诊断用（部分动作实际点击的选择器与 selector 字段不同）
        logger.debug("[Automation] action[%d] %s selector=%r", i, typ, sel[:80] if sel else "")
        val = act.get("value", "")
        ms = int(act.get("ms") or act.get("timeout") or 500)
        sel_timeout = int(act.get("timeout") or act.get("ms") or 5) * 1000
        sel_disp = (sel[:400] + "…") if len(sel) > 400 else sel
        t0 = time.perf_counter()
        _diff_log(f"[{ctx}] | 准备执行动作: 步骤序号[{i}] - 类型[{typ}] - Selector:[{sel_disp or '(无)'}]")

        try:
            if typ == "click":
                if sel:
                    _diff_log(f"当前真实 URL: {_safe_page_url(page)}")
                    _log_locator_diag(page, sel, ctx)
                    force = bool(act.get("force", False))
                    loc = page.locator(sel).first
                    if force:
                        loc.click(timeout=sel_timeout, force=True)
                    else:
                        loc.click(timeout=sel_timeout)
                else:
                    _diff_log(f"[{ctx}] | 动作提前结束(参数错误): 步骤[{i}] click 缺少 selector")
                    _diff_action_done(i, t0, ctx)
                    return f"action[{i}] click 缺少 selector"
            elif typ == "click_if_exists":
                if sel:
                    _diff_log(f"当前真实 URL: {_safe_page_url(page)}")
                    _log_locator_diag(page, sel, ctx)
                    try:
                        force = bool(act.get("force", False))
                        t = min(int(act.get("timeout") or 3) * 1000, 5000)
                        loc = page.locator(sel).first
                        loc.click(timeout=t, force=force)
                    except Exception as e:
                        logger.debug("[Automation] action[%d] click_if_exists: %s, continuing", i, e)
                        _diff_log(f"[{ctx}] | click_if_exists 内层捕获(按设计忽略): {type(e).__name__}: {e}")
                else:
                    _diff_log(f"[{ctx}] | 动作提前结束(参数错误): 步骤[{i}] click_if_exists 缺少 selector")
                    _diff_action_done(i, t0, ctx)
                    return f"action[{i}] click_if_exists 缺少 selector"
            elif typ == "click_expand":
                # Element UI 子菜单：仅当折叠时点击，避免误折叠已展开项（刷新后侧栏状态会保持）
                txt = (act.get("text") or "").strip()
                if sel or txt:
                    diag_sel = sel if sel else f".el-menu >> text={txt}"
                    _diff_log(f"当前真实 URL: {_safe_page_url(page)}")
                    _log_locator_diag(page, diag_sel, ctx)
                    loc = page.locator(sel).first if sel else page.locator(f".el-menu >> text={txt}").first
                    try:
                        loc.scroll_into_view_if_needed(timeout=5000)
                        _diff_log(f"强制等待开始: 150ms (click_expand 滚动后缓冲) ...")
                        page.wait_for_timeout(150)
                        _diff_log(f"强制等待结束: 150ms")
                        _diff_log(f"[{ctx}] | (click_expand) 即将 evaluate 检查 aria-expanded")
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
                                    _diff_log(f"强制等待开始: 100ms (click_expand title 前) ...")
                                    page.wait_for_timeout(100)
                                    _diff_log(f"强制等待结束: 100ms")
                                    _diff_log(f"当前真实 URL: {_safe_page_url(page)}")
                                    _log_locator_diag(page, f".el-menu .el-submenu__title:has-text('{txt}')", ctx)
                                    title_loc.click(timeout=sel_timeout, force=True)
                                except Exception:
                                    loc.scroll_into_view_if_needed(timeout=5000)
                                    _diff_log(f"当前真实 URL: {_safe_page_url(page)}")
                                    _log_locator_diag(page, diag_sel, ctx)
                                    loc.click(timeout=sel_timeout, force=True)
                            else:
                                _diff_log(f"当前真实 URL: {_safe_page_url(page)}")
                                _log_locator_diag(page, diag_sel, ctx)
                                loc.click(timeout=sel_timeout, force=True)
                    except Exception:
                        loc.scroll_into_view_if_needed(timeout=5000)
                        _diff_log(f"当前真实 URL: {_safe_page_url(page)}")
                        _log_locator_diag(page, diag_sel, ctx)
                        loc.click(timeout=sel_timeout, force=True)
                else:
                    _diff_log(f"[{ctx}] | 动作提前结束(参数错误): 步骤[{i}] click_expand 缺少 selector 或 text")
                    _diff_action_done(i, t0, ctx)
                    return f"action[{i}] click_expand 缺少 selector 或 text"
            elif typ == "fill":
                if sel and val is not None:
                    _diff_log(f"当前真实 URL: {_safe_page_url(page)}")
                    _log_locator_diag(page, sel, ctx)
                    page.locator(sel).first.fill(str(val), timeout=sel_timeout)
                else:
                    _diff_log(f"[{ctx}] | 动作提前结束(参数错误): 步骤[{i}] fill 缺少 selector 或 value")
                    _diff_action_done(i, t0, ctx)
                    return f"action[{i}] fill 缺少 selector 或 value"
            elif typ == "press":
                key = act.get("key") or val or "Enter"
                if sel:
                    _diff_log(f"当前真实 URL: {_safe_page_url(page)}")
                    _log_locator_diag(page, sel, ctx)
                    page.locator(sel).first.press(key, timeout=sel_timeout)
                else:
                    page.keyboard.press(key)
            elif typ == "wait":
                if sel:
                    _log_locator_diag(page, sel, ctx)
                    page.wait_for_selector(sel, timeout=sel_timeout)
                if ms and ms > 0:
                    wm = min(ms, 10000)
                    _diff_log(f"强制等待开始: {wm}ms (wait 附带) ...")
                    page.wait_for_timeout(wm)
                    _diff_log(f"强制等待结束: {wm}ms")
            elif typ == "wait_visible":
                if sel:
                    _log_locator_diag(page, sel, ctx)
                    page.locator(sel).first.wait_for(state="visible", timeout=sel_timeout)
                else:
                    _diff_log(f"[{ctx}] | 动作提前结束(参数错误): 步骤[{i}] wait_visible 缺少 selector")
                    _diff_action_done(i, t0, ctx)
                    return f"action[{i}] wait_visible 缺少 selector"
            elif typ == "wait_attached":
                if sel:
                    _log_locator_diag(page, sel, ctx)
                    page.locator(sel).first.wait_for(state="attached", timeout=sel_timeout)
                else:
                    _diff_log(f"[{ctx}] | 动作提前结束(参数错误): 步骤[{i}] wait_attached 缺少 selector")
                    _diff_action_done(i, t0, ctx)
                    return f"action[{i}] wait_attached 缺少 selector"
            elif typ == "expand_sidebar_if_collapsed":
                # 侧栏折叠时菜单文字 visibility:hidden，需展开或强制显示（同事侧栏默认展开故无此问题）
                _log_locator_diag(page, ".el-menu.el-menu--collapse", ctx)
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
                        _diff_log(f"强制等待开始: 500ms (expand_sidebar 后) ...")
                        page.wait_for_timeout(500)
                        _diff_log(f"强制等待结束: 500ms")
                except Exception as e:
                    logger.debug("[Automation] expand_sidebar_if_collapsed: %s, continuing", e)
                    _diff_log(f"[{ctx}] | expand_sidebar_if_collapsed 内层异常(按设计继续): {type(e).__name__}: {e}")
            elif typ == "wait_ms":
                wms = min(int(act.get("ms", 500)), 10000)
                _diff_log(f"强制等待开始: {wms}ms ...")
                page.wait_for_timeout(wms)
                _diff_log(f"强制等待结束: {wms}ms")
            elif typ == "click_expand_first_row":
                # 日活/日新统计表：点击首行日期或展开图标，展开渠道明细
                # 优先点 .el-table__expand-icon，否则点首行首列（日期）
                # force=True：避免 el-table__border-left-patch 等装饰层拦截点击（如用户流量来源页）
                er_sel = ".el-table__body-wrapper tbody tr:first-child .el-table__expand-icon, .el-table__body-wrapper tbody tr:first-child td:first-child .el-table__expand-icon"
                error_diag_sel = er_sel
                fb_sel = ".el-table__body-wrapper tbody tr:first-child td:first-child"
                _diff_log(f"当前真实 URL: {_safe_page_url(page)}")
                _log_locator_diag(page, er_sel, ctx)
                try:
                    page.locator(er_sel).first.click(timeout=3000, force=True)
                except Exception:
                    _log_locator_diag(page, fb_sel, ctx)
                    page.locator(fb_sel).first.click(timeout=3000, force=True)
            elif typ == "fill_date_range":
                # 日期范围：填写开始、结束两个输入框
                # date_editor_index：对比页多段时间用第 N 个 .el-date-editor（nth-of-type 在 Vue 下常失效）
                start_val = act.get("start") or act.get("value", "")
                end_val = act.get("end", "")
                start_sel = act.get("start_selector") or sel
                end_sel = act.get("end_selector") or ""
                optional = act.get("optional", False)
                editor_idx = act.get("date_editor_index")
                form_lbl = (act.get("form_item_label") or "").strip()
                vis_idx = act.get("date_editor_visual_index")
                _diff_log(f"当前真实 URL: {_safe_page_url(page)}")
                try:
                    if vis_idx is not None:
                        # 付费留存对比等页标签文案可能与用户对比不一致，按横向「左=段1、右=段2」最稳
                        _clear_date_editor_scraper_marks(page)
                        try:
                            idx_v = int(vis_idx)
                            n_ed = _mark_main_date_editors_by_visual_x(page, ctx)
                            if n_ed == 0:
                                _diff_log(f"[{ctx}] | 视觉序入选 0，短等 1.5s 后重试打标（DOM 抖动）…")
                                page.wait_for_timeout(1500)
                                n_ed = _mark_main_date_editors_by_visual_x(page, ctx)
                            chosen_ed = None
                            if n_ed > 0 and idx_v < n_ed:
                                le = page.locator(f'.el-date-editor[data-bi-scraper-order="{idx_v}"]').first
                                try:
                                    le.wait_for(state="visible", timeout=min(sel_timeout, 20000))
                                    chosen_ed = le
                                except Exception as e_vis:
                                    _diff_log(f"[{ctx}] | 打标后 locator 等待可见失败，改回退路径: {type(e_vis).__name__}: {e_vis}")
                            if chosen_ed is None:
                                chosen_ed = _pick_date_editor_visible_main_form(
                                    page, idx_v, ctx, sel_timeout
                                )
                                chosen_ed.wait_for(state="visible", timeout=min(sel_timeout, 20000))
                            _fill_el_date_range_inputs(
                                page,
                                chosen_ed,
                                str(start_val or ""),
                                str(end_val or ""),
                                sel_timeout,
                                ctx,
                            )
                        finally:
                            _clear_date_editor_scraper_marks(page)
                    elif form_lbl:
                        # 留存对比：按表单项标签定位（部分页面与视觉序可并存，优先由 filters 选用）
                        chosen_ed = _locate_date_editor_by_form_item_label(page, form_lbl, ctx, sel_timeout)
                        _fill_el_date_range_inputs(
                            page,
                            chosen_ed,
                            str(start_val or ""),
                            str(end_val or ""),
                            sel_timeout,
                            ctx,
                        )
                    elif editor_idx is not None:
                        # 对比页常有多个 .el-date-editor（侧栏/弹层/隐藏副本）；按「可见」顺序取第 N 个，避免 nth(0) 点到不可见节点导致「无法选择日期」
                        idx = int(editor_idx)
                        roots = page.locator(".el-date-editor")
                        nscan = min(roots.count(), 48)
                        chosen_ed = None
                        vis_rank = 0
                        for j in range(nscan):
                            cell = roots.nth(j)
                            try:
                                if not cell.is_visible(timeout=800):
                                    continue
                            except Exception:
                                continue
                            if vis_rank == idx:
                                chosen_ed = cell
                                break
                            vis_rank += 1
                        if chosen_ed is None:
                            raise TimeoutError(
                                f"未找到第 {idx} 个可见的 .el-date-editor（已扫描 {nscan} 个节点，可见序共尝试到 {vis_rank}）"
                            )
                        chosen_ed.wait_for(state="visible", timeout=min(sel_timeout, 20000))
                        _fill_el_date_range_inputs(
                            page,
                            chosen_ed,
                            str(start_val or ""),
                            str(end_val or ""),
                            sel_timeout,
                            ctx,
                        )
                    else:
                        if start_sel:
                            _log_locator_diag(page, str(start_sel), ctx)
                        if end_sel:
                            _log_locator_diag(page, str(end_sel), ctx)
                        if start_sel and start_val:
                            page.locator(start_sel).first.fill(str(start_val), timeout=sel_timeout)
                        if end_sel and end_val:
                            page.locator(end_sel).first.fill(str(end_val), timeout=sel_timeout)
                    no_esc = bool(act.get("no_escape_after_fill"))
                    if no_esc:
                        _diff_log(
                            f"[{ctx}] | fill_date_range 对比页: 跳过 Escape（避免整段筛选卸载）；"
                            f"缓冲 280ms 供面板自行关闭"
                        )
                        page.wait_for_timeout(280)
                    else:
                        # 非对比页：仍用 Escape 关浮层，避免挡后续菜单/按钮
                        page.keyboard.press("Escape")
                        _diff_log(f"强制等待开始: 200ms (fill_date_range Escape 后) ...")
                        page.wait_for_timeout(200)
                        _diff_log(f"强制等待结束: 200ms")
                except Exception as fill_err:
                    if optional:
                        logger.debug("[Automation] fill_date_range optional 失败，继续: %s", fill_err)
                        _diff_log(f"[{ctx}] | fill_date_range optional 失败(按设计继续): {type(fill_err).__name__}: {fill_err}")
                    else:
                        raise
            elif typ == "wait_for_data_ready":
                # 等待数据完全加载：优先等待加载遮罩消失，否则固定等待
                wait_ms = int(act.get("wait_after_query_ms") or 5000)
                loading_sel = (act.get("wait_for_loading_hidden") or "").strip()
                t_sec = int(act.get("timeout") or 30) * 1000
                if loading_sel:
                    _log_locator_diag(page, loading_sel, ctx)
                cap = min(wait_ms, 120000)
                _diff_log(f"强制等待开始: wait_for_data_ready 最长约 {cap}ms (遮罩或固定) ...")
                try:
                    if loading_sel:
                        # 等待加载遮罩隐藏（Element UI: .el-loading-mask）
                        page.locator(loading_sel).first.wait_for(state="hidden", timeout=t_sec)
                        extra = min(wait_ms, 3000)
                        _diff_log(f"强制等待开始: {extra}ms (遮罩隐藏后缓冲) ...")
                        page.wait_for_timeout(extra)
                        _diff_log(f"强制等待结束: {extra}ms")
                    else:
                        page.wait_for_timeout(min(wait_ms, 120000))
                except Exception:
                    # 无加载遮罩或超时：回退到固定等待
                    page.wait_for_timeout(min(wait_ms, 120000))
                _diff_log(f"强制等待结束: wait_for_data_ready")
            else:
                _diff_log(f"[{ctx}] | 未知动作类型(跳过): [{typ}]")
            _diff_action_done(i, t0, ctx)
        except Exception as e:
            elapsed = int((time.perf_counter() - t0) * 1000)
            _diff_log(
                f"🚨 致命卡点: 步骤[{i}] 执行失败，错误类型: {type(e).__name__}，错误详情: {e}"
            )
            _diff_log(f"当前真实 URL: {_safe_page_url(page)}")
            if error_diag_sel:
                _log_locator_diag(page, error_diag_sel, ctx)
            elif sel:
                _log_locator_diag(page, sel, ctx)
            _diff_log(f"[{ctx}] | 步骤[{i}] 已耗时(至异常): {elapsed} ms")
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
      expand_include_parent_row: True 时每一轮在子行前追加父 tr（每日游戏数据「当日总计」在该行）
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
                const expandedRowIdx = args.expandedRowIdx, useFullParent = args.useFullParent, includeParent = args.includeParent;
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
                if (includeParent) {
                    const parentTds = parentTr.querySelectorAll('td');
                    const parentCells = Array.from(parentTds).map(t => (t.innerText || '').trim());
                    if (parentCells.length) {
                        rows.push({ cells: parentCells, parentDate });
                    }
                }
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
            {
                "expandedRowIdx": row_idx,
                "useFullParent": parent_full_cell,
                "includeParent": bool(automation.get("expand_include_parent_row")),
            },
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
    filters.date_range_compare_form_labels: ["时间段1","时间段2"] 可选；与 date_range_compare 同行数，
        按表单项标签定位 .el-date-editor
    filters.date_range_compare_use_visual_order: True 时按主区域内日期框「从左到右」填段1/段2，
        适用于付费/用户对比页标签文案不一致的情况（与 form_labels 二选一，此项优先于 form_labels）
    filters.date_range_compare_no_escape_after_fill: True 时每段 fill_date_range 后不按 Escape；
        Heron-BI 等站点多按 Escape 会整段卸载筛选区（DOM 中 .el-date-editor 变为 0）
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
        use_visual_order = bool(filters.get("date_range_compare_use_visual_order"))
        no_esc_compare = bool(filters.get("date_range_compare_no_escape_after_fill"))
        raw_lbls = filters.get("date_range_compare_form_labels")
        form_lbls: list[str | None] = []
        if isinstance(raw_lbls, (list, tuple)):
            form_lbls = [str(x).strip() if x is not None and str(x).strip() else None for x in raw_lbls[:2]]
        while len(form_lbls) < 2:
            form_lbls.append(None)
        for i, pr in enumerate(dr_compare[:2]):
            if isinstance(pr, (list, tuple)) and len(pr) >= 2:
                s = sels_list[i] if i < len(sels_list) else {}
                form_lbl = form_lbls[i] if i < len(form_lbls) else None
                # 自定义选择器优先；否则用第 i 个日期范围组件（避免 :nth-of-type(2) 在表单布局下匹配不到）
                if s.get("start") or s.get("end"):
                    actions.append({
                        "type": "fill_date_range",
                        "start_selector": s.get("start") or ".el-date-editor input:first-of-type",
                        "end_selector": s.get("end") or ".el-date-editor input:last-of-type",
                        "start": str(pr[0]),
                        "end": str(pr[1]),
                        "optional": i > 0,
                    })
                else:
                    act: dict[str, Any] = {
                        "type": "fill_date_range",
                        "start": str(pr[0]),
                        "end": str(pr[1]),
                        # 两段区间均需填写；原先第二段 optional=True 会导致静默跳过、对比查询口径错误
                        "optional": False,
                    }
                    if use_visual_order:
                        act["date_editor_visual_index"] = i
                    else:
                        act["date_editor_index"] = i
                        if form_lbl:
                            act["form_item_label"] = form_lbl
                    if no_esc_compare:
                        act["no_escape_after_fill"] = True
                    actions.append(act)
                    # 段1 填完会打开日历；收起并稳定 DOM 后再打标段2，减少付费对比页第二段仍为空
                    if use_visual_order and i == 0:
                        actions.append({"type": "wait_ms", "ms": 500})
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
        # BI 顶栏 fixed + z-10 常挡住表单区「查询」按钮的真实命中；force 与侧栏模式 click_if_exists 一致
        actions.append({"type": "click", "selector": qs, "force": True})
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
    from l3_node.primitives.mcp.mcp_tools.bi.paths import get_bi_raw_dir, ensure_bi_dirs
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


def _visible_el_pagination_locator(target_page: Any) -> Any | None:
    """主内容区可见的 Element UI 分页条；无则返回 None。"""
    pags = target_page.locator(".el-pagination")
    try:
        n = pags.count()
    except Exception:
        return None
    for i in range(n):
        loc = pags.nth(i)
        try:
            if loc.is_visible():
                return loc
        except Exception:
            continue
    return pags.first if n else None


def _extract_element_ui_table_rows(target_page: Any, automation: dict) -> list[dict[str, Any]]:
    """从当前页提取 Element UI 表格（表头与 body 分离、展开行嵌套表等逻辑与单次抓取一致）。"""
    rows: list[dict[str, Any]] = []
    split_merged = automation.get("split_merged_cells", True)
    _date_re = re.compile(r"^\d{4}-\d{2}-\d{2}")
    last_date = ""
    header_cells = [
        h.strip()
        for h in target_page.locator(
            ".el-table__header-wrapper thead th, .el-table__header-wrapper thead td"
        ).all_text_contents()
        if h.strip()
    ]
    if not header_cells:
        header_cells = [
            h.strip() for h in target_page.locator("table thead th, table thead td").all_text_contents() if h.strip()
        ]
    body_trs = target_page.locator(
        ".el-table__body-wrapper table tbody tr, .el-table__body-wrapper tbody tr, .el-table tbody tr"
    ).all()
    if not body_trs:
        body_trs = target_page.locator("table:not(.el-date-table) tbody tr").all()
    if header_cells and body_trs:
        for tr in body_trs:
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
    return rows


def _harvest_table_rows_with_optional_pagination(
    target_page: Any,
    automation: dict,
    diff_label: str,
) -> list[dict[str, Any]]:
    """
    提取表格行；automation.pagination_all_pages 为 True 时翻遍 Element UI 分页（下一页）直到末页。
    用于「每日充值明细」等默认 10 条/页、仅 DOM 当前页有数据的页面。
    """
    if not automation.get("pagination_all_pages"):
        return _extract_element_ui_table_rows(target_page, automation)

    all_rows: list[dict[str, Any]] = []
    max_pages = int(automation.get("pagination_max_pages") or 200)
    wait_ms = int(automation.get("pagination_wait_ms") or 1600)
    wait_ms = max(400, min(wait_ms, 10000))

    for page_i in range(max_pages):
        # 第 1 页前已在 pre_table 做过反残影；翻页后需再等 loading
        if page_i > 0:
            _spa_anti_ghost_settle(target_page, diff_label, f"pagination_page_{page_i + 1}")
        chunk = _extract_element_ui_table_rows(target_page, automation)
        if chunk:
            all_rows.extend(chunk)

        pag = _visible_el_pagination_locator(target_page)
        if pag is None:
            break
        next_btn = pag.locator("button.btn-next").first
        if next_btn.count() == 0:
            break
        try:
            if next_btn.is_disabled():
                break
        except Exception:
            cls = (next_btn.get_attribute("class") or "") + " " + (next_btn.get_attribute("disabled") or "")
            if "disabled" in cls.lower():
                break
        try:
            next_btn.click(timeout=8000)
        except Exception:
            break
        target_page.wait_for_timeout(wait_ms)

    return all_rows


def _harvest_via_playwright(
    url: str,
    cdp_url: str,
    table_selector: str,
    timeout: int,
    automation: dict | None = None,
    diff_log_context: str = "",
) -> tuple[list[dict[str, Any]] | None, str]:
    """SPA 模式：Playwright 连接已登录 Chrome，可选执行 automation 后抓取表格"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, "playwright 未安装，请执行 pip install playwright && playwright install chromium"

    automation = automation or {}
    diff_label = (diff_log_context or automation.get("_diff_log_context") or "").strip()
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
            _diff_log(
                f"[{diff_label or '—'}] | Playwright CDP 已连接: cdp_url={cdp_url} context_pages={len(pages)}"
            )

            target_page = _pick_cdp_target_page(pages, url, start_url)
            if not target_page:
                target_page = context.new_page()
            else:
                try:
                    _diff_log(
                        f"[{diff_label or '—'}] | CDP 操作标签页: {_safe_page_url(target_page)} "
                        f"(目标路由: {_cdp_route_identity(start_url) or _cdp_route_identity(url) or '—'})"
                    )
                except Exception:
                    pass

            # 导航到入口页（start_url 或 url）
            nav_url = automation.get("start_url") or url
            # 直链/菜单模式均需严格等网络空闲，避免 hash 已变而旧表 DOM 仍在
            nav_timeout_ms = max(15000, int(timeout * 1000))
            navigated = False
            try:
                target_page.bring_to_front()
                # 有 automation 时强制导航，确保每次从入口页开始（避免上一项展开的菜单被误点折叠）
                if actions:
                    target_page.goto(nav_url, wait_until="networkidle", timeout=nav_timeout_ms)
                    navigated = True
                elif not _same_cdp_route(nav_url, target_page.url or ""):
                    target_page.goto(nav_url, wait_until="networkidle", timeout=nav_timeout_ms)
                    navigated = True
                if navigated:
                    _spa_anti_ghost_settle(target_page, diff_label, "post_goto")
                else:
                    _diff_log(f"[{diff_label or '—'}] | 导航跳过(URL 已匹配)，轻量缓冲 500ms")
                    target_page.wait_for_timeout(500)
            except Exception:
                pass

            _diff_log(
                f"[{diff_label or '—'}] | 导航阶段结束 当前真实 URL: {_safe_page_url(target_page)}"
            )

            # 执行自动化操作（点击菜单、填写筛选等）
            if actions:
                _diff_log(
                    f"[{diff_label or '—'}] | 即将执行 automation 共 {len(actions)} 步 | URL: {_safe_page_url(target_page)}"
                )
                err = _run_automation_actions(
                    target_page, actions, timeout * 1000, context_label=diff_label
                )
                if err:
                    return None, f"自动化步骤失败: {err}"

            # 查询/填表动作后再次反残影，再匹配表格（避免点到查询后仍短暂残留旧表）
            _spa_anti_ghost_settle(target_page, diff_label, "pre_table")

            # 等待表格加载（排除日期选择器 el-date-table）
            sel = table_selector or "table:not(.el-date-table)"
            sel_short = (sel[:200] + "…") if len(sel) > 200 else sel
            _diff_log(f"[{diff_label or '—'}] | 等待表格选择器: [{sel_short}]")
            try:
                target_page.wait_for_selector(sel, timeout=timeout * 1000)
                _diff_log(f"[{diff_label or '—'}] | 表格选择器已出现 (wait_for_selector 返回)")
            except Exception as wse:
                _diff_log(
                    f"[{diff_label or '—'}] | 表格选择器等待未成功(按设计继续): {type(wse).__name__}: {wse}"
                )

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

            # 提取表格：Element UI 表头/表体分离；可选翻遍分页（见 automation.pagination_all_pages）
            try:
                rows = _harvest_table_rows_with_optional_pagination(target_page, automation, diff_label)
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
            automation: dict 自动化配置 {start_url, actions: [{type,selector,value}], filters: {date_range,query_selector}},
            diff_log_context: str 可选，终端 [DIFF-LOG] 中的表名/批次标签（便于两台机器 diff）
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
    diff_ctx = str(config.get("diff_log_context") or "").strip()
    if use_api and not config.get("cdp_url"):
        rows, err = _harvest_via_api(url, headers, timeout)
    else:
        # SPA 模式：连接已登录 Chrome，可选执行 automation
        rows, err = _harvest_via_playwright(
            url,
            cdp_url,
            table_selector,
            timeout,
            automation=automation,
            diff_log_context=diff_ctx,
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
    from l3_node.primitives.mcp.mcp_tools.bi.paths import get_bi_raw_dir, ensure_bi_dirs
    ensure_bi_dirs()
    out = str(get_bi_raw_dir() / "test.csv")
    # 将下方 URL 替换为点击左侧数据菜单后地址栏的实际路径
    data_url = "https://bi-admin-web.heronpro.xin/#/layout/person"  # 示例，请改为数据页 URL
    r = harvest_table_data(data_url, output_path=out, config={
        "cdp_url": "http://127.0.0.1:9222", "output_format": "csv", "timeout": 15,
    })
    print(r)
