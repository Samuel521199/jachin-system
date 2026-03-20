"""
通用网页抓取器 — mcp:atom_web_scraper

从 l3_mcp_cache 加载时使用本地 paths，依赖 l3_node.channels。契约: docs/bi_daily_report/01_PARALLEL_DEVELOPMENT_GUIDE.md
"""
from __future__ import annotations

import csv
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .paths import ensure_bi_dirs, get_bi_raw_dir

logger = logging.getLogger(__name__)

_MERGED_CELL_RE = re.compile(r"^(.+?)\s*\(([+-]?[\d.]+%)\)\s*(.+)$", re.DOTALL)


def _split_merged_cell_value(val: str) -> str:
    """若单元格为「当前值 (+X%) 上期值」合并格式，拆分为「当前值 | 环比 | 上期值」。"""
    val = (val or "").strip()
    m = _MERGED_CELL_RE.match(val)
    if m:
        return f"{m.group(1).strip()} | {m.group(2)} | {m.group(3).strip()}"
    return val


def _run_automation_actions(page: Any, actions: list[dict], timeout_ms: int) -> str:
    """按顺序执行自动化操作。失败时返回错误信息，成功返回空串。"""
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
                txt = (act.get("text") or "").strip()
                if sel or txt:
                    loc = page.locator(sel).first if sel else page.locator(f".el-menu >> text={txt}").first
                    try:
                        is_expanded = loc.evaluate("""
                            el => {
                                const li = el.closest('li[class*="sub-menu"], li[aria-expanded]');
                                return li ? li.getAttribute('aria-expanded') === 'true' : false;
                            }
                        """)
                        if not is_expanded:
                            if txt:
                                title_loc = page.locator(f".el-menu .el-submenu__title:has-text('{txt}')").first
                                try:
                                    title_loc.click(timeout=sel_timeout, force=True)
                                except Exception:
                                    loc.click(timeout=sel_timeout, force=True)
                            else:
                                loc.click(timeout=sel_timeout, force=True)
                    except Exception:
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
            elif typ == "wait_ms":
                page.wait_for_timeout(min(int(act.get("ms", 500)), 10000))
            elif typ == "fill_date_range":
                start_val = act.get("start") or act.get("value", "")
                end_val = act.get("end", "")
                start_sel = act.get("start_selector") or sel
                end_sel = act.get("end_selector") or ""
                if start_sel and start_val:
                    page.locator(start_sel).first.fill(str(start_val), timeout=sel_timeout)
                if end_sel and end_val:
                    page.locator(end_sel).first.fill(str(end_val), timeout=sel_timeout)
            elif typ == "wait_for_data_ready":
                wait_ms = int(act.get("wait_after_query_ms") or 5000)
                loading_sel = (act.get("wait_for_loading_hidden") or "").strip()
                t_sec = int(act.get("timeout") or 30) * 1000
                try:
                    if loading_sel:
                        page.locator(loading_sel).first.wait_for(state="hidden", timeout=t_sec)
                        page.wait_for_timeout(min(wait_ms, 3000))
                    else:
                        page.wait_for_timeout(min(wait_ms, 120000))
                except Exception:
                    page.wait_for_timeout(min(wait_ms, 120000))
        except Exception as e:
            logger.warning("[Automation] action[%d] %s failed: %s", i, typ, e)
            return f"action[{i}] {typ} 失败: {e}"
    return ""


def _expand_filters_to_actions(filters: dict) -> list[dict]:
    """将 filters 配置展开为 automation actions。"""
    actions: list[dict] = []
    if not filters:
        return actions
    dr = filters.get("date_range")
    if isinstance(dr, (list, tuple)) and len(dr) >= 2:
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
        wait_ms = int(filters.get("wait_after_query_ms") or 5000)
        wait_ms = max(1000, min(wait_ms, 120000))
        loading_sel = filters.get("wait_for_loading_hidden") or ""
        data_timeout = int(filters.get("wait_for_data_timeout") or 30)
        data_timeout = max(5, min(data_timeout, 120))
        actions.append({
            "type": "wait_for_data_ready",
            "wait_after_query_ms": wait_ms,
            "wait_for_loading_hidden": loading_sel,
            "timeout": data_timeout,
        })
    return actions


def _expand_all_table_rows(
    page: Any,
    *,
    expand_selector: str = ".el-table__expand-icon:not(.el-table__expand-icon--expanded)",
    wait_ms: int = 400,
    max_rounds: int = 100,
) -> None:
    """抓取前展开表格内所有可展开的树形行。支持 Element UI 标准及自定义 span[style*='cursor: pointer']。"""
    for round_idx in range(max_rounds):
        icons = page.locator(expand_selector)
        count = icons.count()
        if count == 0:
            clicked = page.evaluate(
                """
                () => {
                    const rows = document.querySelectorAll('.el-table__body-wrapper tbody tr');
                    for (const row of rows) {
                        const span = row.querySelector('.cell span[style*="cursor: pointer"]');
                        if (!span) continue;
                        const next = row.nextElementSibling;
                        if (next && /el-table__row--level-1|level-1/.test(next.className || '')) continue;
                        span.scrollIntoView({ block: 'center', behavior: 'instant' });
                        span.click();
                        return 1;
                    }
                    return 0;
                }
                """
            )
            if clicked:
                page.wait_for_timeout(min(wait_ms, 2000))
                continue
            if round_idx > 0:
                logger.debug("[Expand Table] 已全部展开，共 %d 轮", round_idx)
            return
        try:
            first = icons.first
            first.scroll_into_view_if_needed(timeout=5000)
            page.wait_for_timeout(100)
            first.click(timeout=5000, force=True)
            page.wait_for_timeout(min(wait_ms, 2000))
        except Exception as e:
            try:
                expanded = page.evaluate(
                    "(sel) => { const icons = document.querySelectorAll(sel); if (!icons.length) return 0; icons[0].scrollIntoView({block:'center',behavior:'instant'}); icons[0].click(); return 1; }",
                    expand_selector,
                )
                if expanded:
                    page.wait_for_timeout(min(wait_ms, 2000))
            except Exception:
                return


def _resolve_output_path(output_path: str | Path | None, output_format: str) -> Path:
    """解析输出路径，为空时使用 bi paths 下 YYYYMMDD.csv/json"""
    if output_path:
        p = Path(output_path)
        if p.suffix.lower() in (".csv", ".json"):
            return p
        return p.with_suffix(".csv" if output_format == "csv" else ".json")
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

            nav_url = automation.get("start_url") or url
            try:
                target_page.bring_to_front()
                if actions:
                    target_page.goto(nav_url, wait_until="domcontentloaded", timeout=timeout * 1000)
                    target_page.wait_for_load_state("networkidle", timeout=timeout * 1000)
                elif nav_url not in (target_page.url or ""):
                    target_page.goto(nav_url, wait_until="domcontentloaded", timeout=timeout * 1000)
                    target_page.wait_for_load_state("networkidle", timeout=timeout * 1000)
                target_page.wait_for_timeout(1000)
            except Exception:
                pass

            if actions:
                err = _run_automation_actions(target_page, actions, timeout * 1000)
                if err:
                    return None, f"自动化步骤失败: {err}"

            sel = table_selector or "table:not(.el-date-table)"
            try:
                target_page.wait_for_selector(sel, timeout=timeout * 1000)
            except Exception:
                pass

            if automation.get("expand_table_rows", False):
                expand_sel = automation.get("expand_selector") or ".el-table__expand-icon:not(.el-table__expand-icon--expanded)"
                expand_wait = max(200, min(int(automation.get("expand_wait_ms") or 600), 2000))
                try:
                    _expand_all_table_rows(target_page, expand_selector=expand_sel, wait_ms=expand_wait)
                except Exception:
                    pass

            rows = []
            try:
                header_cells = [h.strip() for h in target_page.locator(".el-table__header-wrapper thead th, .el-table__header-wrapper thead td").all_text_contents() if h.strip()]
                if not header_cells:
                    header_cells = [h.strip() for h in target_page.locator("table thead th, table thead td").all_text_contents() if h.strip()]
                body_trs = target_page.locator(".el-table__body-wrapper table tbody tr, .el-table__body-wrapper tbody tr, .el-table tbody tr").all()
                if not body_trs:
                    body_trs = target_page.locator("table:not(.el-date-table) tbody tr").all()
                split_merged = automation.get("split_merged_cells", True)
                if header_cells and body_trs:
                    for tr in body_trs:
                        cells = tr.locator("td").all_text_contents()
                        cells = [c.strip() for c in cells]
                        if split_merged:
                            cells = [_split_merged_cell_value(c) for c in cells]
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
    """抓取网页/后台表格数据，保存为 CSV 或 JSON。"""
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

    use_api = "/api/" in url or "api." in url or headers
    automation = config.get("automation")
    if use_api and not config.get("cdp_url"):
        rows, err = _harvest_via_api(url, headers, timeout)
    else:
        rows, err = _harvest_via_playwright(
            url, cdp_url, table_selector, timeout, automation=automation
        )

    if err:
        return {"status": "error", "error": err}
    if not rows:
        return {"status": "error", "error": "未获取到数据"}

    try:
        if output_format == "csv":
            with open(out_path, "w", newline="", encoding="utf-8") as f:
                if rows:
                    writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()), extrasaction="ignore")
                    writer.writeheader()
                    writer.writerows(rows)
        else:
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(rows, f, ensure_ascii=False, indent=2)
        return {"status": "success", "file_path": str(out_path), "rows_count": len(rows)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def atom_web_scraper(
    url: str = "",
    output_path: str = "",
    config: dict | None = None,
    cdp_url: str = "",
) -> dict[str, Any]:
    """MCP 接口：atom_web_scraper。cdp_url 合并到 config。"""
    cfg = dict(config or {})
    if cdp_url:
        cfg["cdp_url"] = cdp_url
    return harvest_table_data(url=url or "", output_path=output_path or None, config=cfg)
