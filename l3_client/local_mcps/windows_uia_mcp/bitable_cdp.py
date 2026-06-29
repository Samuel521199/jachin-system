"""CDP/DOM automation for Lark/Feishu Bitable pages.

This is an optional browser layer for cases where official OpenAPI access is
not available but the user is already logged into Lark in a browser. It is kept
separate from PMO and from the Windows UIA coordinate fallback.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _safe_label(s: str) -> str:
    raw = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff._-]+", "_", str(s or "").strip())
    return (raw[:64] or "bitable")


def _default_out_dir(out_dir: str | None = None) -> Path:
    p = Path(out_dir or "output/os_vision/lark_bitable_cdp").expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _cdp_reachable(cdp_url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{cdp_url.rstrip('/')}/json/version", timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def _find_browser_exe() -> str:
    candidates = [
        os.environ.get("JACHIN_CDP_BROWSER_EXE") or "",
        os.environ.get("OS_VISION_BROWSER_EXE") or "",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return c
    return ""


def launch_cdp_browser(
    *,
    cdp_url: str = "http://127.0.0.1:9222",
    open_url: str = "",
    user_data_dir: str = "",
) -> dict[str, Any]:
    """Launch an isolated browser with remote debugging enabled."""
    port_match = re.search(r":(\d+)(?:/|$)", cdp_url)
    port = port_match.group(1) if port_match else "9222"
    if _cdp_reachable(cdp_url):
        return {"ok": True, "already_running": True, "cdp_url": cdp_url}
    browser = _find_browser_exe()
    if not browser:
        return {"ok": False, "error": "browser_exe_not_found", "cdp_url": cdp_url}
    profile = Path(
        user_data_dir
        or os.environ.get("JACHIN_LARK_CDP_USER_DATA_DIR")
        or (Path(os.environ.get("LOCALAPPDATA") or str(Path.home())) / "Jachin" / "cdp-lark-profile")
    ).expanduser()
    profile.mkdir(parents=True, exist_ok=True)
    args = [
        browser,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if open_url:
        args.append(open_url)
    flags = 0
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        flags |= subprocess.CREATE_NEW_PROCESS_GROUP
    if hasattr(subprocess, "DETACHED_PROCESS"):
        flags |= subprocess.DETACHED_PROCESS
    try:
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    except Exception as exc:
        return {"ok": False, "error": f"launch_failed:{exc!r}", "browser": browser, "cdp_url": cdp_url}
    deadline = time.time() + 12
    while time.time() < deadline:
        if _cdp_reachable(cdp_url):
            return {"ok": True, "launched": True, "browser": browser, "profile": str(profile), "cdp_url": cdp_url}
        time.sleep(0.4)
    return {
        "ok": False,
        "error": "cdp_not_reachable_after_launch",
        "browser": browser,
        "profile": str(profile),
        "cdp_url": cdp_url,
    }


def _confirmation_required(task: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "task": task,
        "ok": False,
        "detail": "confirmation_required",
        "evidence": {
            **evidence,
            "dangerous": True,
            "confirmation_required": True,
            "note": "This operation may create shared Lark Bitable records.",
        },
    }


def _write_bypass(allow_dangerous: bool) -> bool:
    if allow_dangerous:
        return True
    return _truthy(os.environ.get("JACHIN_LARK_BITABLE_WRITE_NO_CONFIRM"))


async def _visible_text(page: Any) -> str:
    try:
        return await page.locator("body").inner_text(timeout=3000)
    except Exception:
        return ""


async def _click_text(page: Any, patterns: list[str], *, timeout_ms: int = 5000) -> str:
    last_error = ""
    for pat in patterns:
        try:
            loc = page.get_by_text(re.compile(pat, re.I)).first()
            await loc.wait_for(state="visible", timeout=timeout_ms)
            await loc.click(timeout=timeout_ms)
            return pat
        except Exception as exc:
            last_error = str(exc)[:300]
    raise RuntimeError(f"text_locator_not_clicked patterns={patterns!r} last={last_error}")


async def _find_bitable_page(browser: Any, *, bitable_url: str, table_name: str) -> Any:
    table_id = ""
    m = re.search(r"[?&]table=([^&]+)", bitable_url or "")
    if m:
        table_id = m.group(1)
    candidates: list[Any] = []
    for ctx in browser.contexts:
        for page in ctx.pages:
            try:
                url = page.url or ""
                title = await page.title()
            except Exception:
                continue
            low = f"{url}\n{title}".lower()
            if table_id and table_id.lower() in low:
                return page
            if table_name and table_name.lower() in low:
                return page
            if "larksuite.com/wiki" in low or "feishu.cn/wiki" in low or "/base/" in low:
                candidates.append(page)
    if candidates:
        return candidates[0]
    ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
    page = await ctx.new_page()
    if bitable_url:
        await page.goto(bitable_url, wait_until="domcontentloaded", timeout=45000)
    return page


async def _run_ai_paste(
    *,
    table_name: str,
    bitable_url: str,
    records_text: str,
    target_group: str,
    cdp_url: str,
    launch_if_missing: bool,
    submit: bool,
    out_dir: str,
) -> dict[str, Any]:
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        return {
            "task": "lark_bitable_cdp_ai_paste_records",
            "ok": False,
            "detail": "playwright_not_available",
            "evidence": {
                "error": repr(exc),
                "install": "pip install playwright && playwright install chromium",
            },
        }

    if not _cdp_reachable(cdp_url):
        if not launch_if_missing:
            return {
                "task": "lark_bitable_cdp_ai_paste_records",
                "ok": False,
                "detail": "cdp_not_reachable",
                "evidence": {"cdp_url": cdp_url, "hint": "Start Edge/Chrome with --remote-debugging-port=9222."},
            }
        launched = launch_cdp_browser(cdp_url=cdp_url, open_url=bitable_url)
        if not launched.get("ok"):
            return {
                "task": "lark_bitable_cdp_ai_paste_records",
                "ok": False,
                "detail": "cdp_launch_failed",
                "evidence": launched,
            }

    outp = _default_out_dir(out_dir)
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(cdp_url)
        page = await _find_bitable_page(browser, bitable_url=bitable_url, table_name=table_name)
        await page.bring_to_front()
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=20000)
        except Exception:
            pass
        try:
            await page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:
            pass
        before = outp / f"cdp_bitable_before_{_safe_label(table_name)}.png"
        await page.screenshot(path=str(before), full_page=False)
        text = await _visible_text(page)
        ready_hits = [x for x in ("添加记录", "字段配置", "视图配置", "筛选", "分组", target_group) if x and x in text]
        if "添加记录" not in text:
            return {
                "task": "lark_bitable_cdp_ai_paste_records",
                "ok": False,
                "detail": "bitable_page_not_ready",
                "evidence": {
                    "url": page.url,
                    "title": await page.title(),
                    "ready_hits": ready_hits,
                    "screenshot": str(before),
                    "hint": "Open the grid/table view, not dashboard, or pass the exact bitable_url.",
                },
            }
        await _click_text(page, [r"^\s*\+?\s*添加记录\s*$", r"添加记录"], timeout_ms=7000)
        await page.wait_for_timeout(500)
        await _click_text(page, [r"AI\s*粘贴录入", r"粘贴录入"], timeout_ms=7000)
        await page.wait_for_timeout(1200)
        modal = outp / f"cdp_bitable_ai_paste_modal_{_safe_label(table_name)}.png"
        await page.screenshot(path=str(modal), full_page=False)
        modal_text = await _visible_text(page)
        if not any(k in modal_text for k in ("AI 粘贴录入", "粘贴录入", "粘贴文本", "自动识别")):
            return {
                "task": "lark_bitable_cdp_ai_paste_records",
                "ok": False,
                "detail": "ai_paste_modal_not_verified",
                "evidence": {"url": page.url, "screenshot": str(modal), "visible_text_preview": modal_text[:1000]},
            }

        editor = page.locator('textarea, [contenteditable="true"], div[role="textbox"], input:not([type="hidden"])').last
        try:
            await editor.click(timeout=5000)
        except Exception:
            await page.keyboard.press("Tab")
        await page.keyboard.insert_text(records_text)
        await page.wait_for_timeout(800)
        pasted = outp / f"cdp_bitable_ai_paste_pasted_{_safe_label(table_name)}.png"
        await page.screenshot(path=str(pasted), full_page=False)
        after_text = await _visible_text(page)
        payload_visible = any(line.strip() and line.strip() in after_text for line in records_text.splitlines()[:3])

        submitted = False
        submit_error = ""
        if submit:
            try:
                await _click_text(page, [r"确认录入", r"开始录入", r"导入", r"提交", r"确定"], timeout_ms=6000)
                submitted = True
                await page.wait_for_timeout(1800)
            except Exception as exc:
                submit_error = str(exc)[:400]
        final = outp / f"cdp_bitable_ai_paste_final_{_safe_label(table_name)}.png"
        await page.screenshot(path=str(final), full_page=False)
        return {
            "task": "lark_bitable_cdp_ai_paste_records",
            "ok": payload_visible and (not submit or submitted),
            "detail": "ai_paste_payload_entered" if not submit else ("ai_paste_submitted" if submitted else "submit_not_completed"),
            "evidence": {
                "url": page.url,
                "title": await page.title(),
                "target_group": target_group,
                "payload_visible": payload_visible,
                "submitted": submitted,
                "submit_error": submit_error,
                "screenshots": {"before": str(before), "modal": str(modal), "pasted": str(pasted), "final": str(final)},
                "note": "CDP/DOM path uses the user's browser login state; OpenAPI permissions are not required.",
            },
        }


def windows_lark_bitable_cdp_ai_paste_records(
    table_name: str = "",
    bitable_url: str = "",
    records_text: str = "",
    target_group: str = "2026/6/22",
    cdp_url: str = "http://127.0.0.1:9222",
    launch_if_missing: bool = True,
    submit: bool = False,
    confirm: bool = False,
    allow_dangerous: bool = False,
    out_dir: str = "",
) -> str:
    name = (table_name or "").strip()
    text = str(records_text or "").strip()
    if not text:
        return json.dumps({"task": "lark_bitable_cdp_ai_paste_records", "ok": False, "detail": "records_text_empty"}, ensure_ascii=False)
    if not confirm and not _write_bypass(allow_dangerous):
        return json.dumps(
            _confirmation_required(
                "lark_bitable_cdp_ai_paste_records",
                {"table_name": name, "bitable_url": bitable_url, "target_group": target_group, "submit": submit},
            ),
            ensure_ascii=False,
            indent=2,
        )
    try:
        result = asyncio.run(
            _run_ai_paste(
                table_name=name,
                bitable_url=bitable_url,
                records_text=text,
                target_group=target_group,
                cdp_url=cdp_url or "http://127.0.0.1:9222",
                launch_if_missing=bool(launch_if_missing),
                submit=bool(submit),
                out_dir=out_dir,
            )
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps(
            {"task": "lark_bitable_cdp_ai_paste_records", "ok": False, "detail": f"failed:{exc!r}"},
            ensure_ascii=False,
            indent=2,
        )
