#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K11 · Tongits 全自动打牌冒烟（独立入口，验证通过后再并入统合冒烟）。

流程：
1. 连接调试 Chrome（CDP）或自启 Chromium
2. 自动点击 Tongits King → Join → 等待进桌
3. 启动协议 3016 监控 + main_bot_loop 自动出牌
4. 等待一局结算，发送 Lark 金币变化卡片

用法：
  python scripts/test_k11_tongits_autoplay_smoke.py
  python scripts/test_k11_tongits_autoplay_smoke.py -v --no-lark-report
  python scripts/test_k11_tongits_autoplay_smoke.py --round-wait-sec 600

控制台按钮：K11 统合平台冒烟页 →「🃏 Tongits 自动打牌」
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from l3_client.local_mcps.kalaroko_monitor import mcp_kalaroko_monitor as mcp

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

TARGET_HOME = "https://www.kalaroko.com/"
SCHEMA = "k11_tongits_autoplay_smoke/v1"
os.environ.setdefault(
    "KALAROKO_MONITOR_ALLOWED_HOSTS",
    "kalaroko.com,www.kalaroko.com,gweb.kalaroko.com,gwp.heronpro.xin",
)


def _log_print(msg: str) -> None:
    print(msg, flush=True)


def _kalaroko_cdp(cli: str | None) -> str:
    raw = (cli or "").strip() or (os.environ.get("KALAROKO_CDP_ENDPOINT") or "").strip()
    if not raw:
        raw = "http://127.0.0.1:9222"
    if not raw.startswith("http://") and not raw.startswith("https://"):
        raw = "http://" + raw.lstrip("/")
    return raw.rstrip("/")


def _host_from_url(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


async def _acquire_page_cdp(
    browser: Any,
    *,
    host: str,
    target_url: str,
    log: Callable[[str], None],
) -> Any:
    """优先复用含目标域的页签，否则新开并 goto。"""
    for ctx in browser.contexts:
        for pg in ctx.pages:
            try:
                u = (pg.url or "").lower()
                if host and host in u:
                    log(f"  [cdp] 复用页签: {pg.url[:100]}")
                    return pg
            except Exception:
                continue
    ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
    pg = await ctx.new_page()
    log(f"  [cdp] 新开页签并打开 {target_url}")
    await mcp._goto_resilient(pg, target_url, "domcontentloaded", 30_000)
    return pg


async def _async_main(args: argparse.Namespace) -> int:
    import importlib.util

    ts_path = SCRIPTS / "k11_tongits_smoke_session.py"
    spec = importlib.util.spec_from_file_location("k11_tongits_smoke_session_run", ts_path)
    if spec is None or spec.loader is None:
        print(f"[失败] 无法加载 {ts_path}", file=sys.stderr)
        return 2
    ts_mod = importlib.util.module_from_spec(spec)
    sys.modules["k11_tongits_smoke_session_run"] = ts_mod
    spec.loader.exec_module(ts_mod)
    TongitsSmokeSession = ts_mod.TongitsSmokeSession
    send_tongits_lark_notification = ts_mod.send_tongits_lark_notification

    target_url = (args.target_url or TARGET_HOME).strip()
    host = _host_from_url(target_url)
    wait_sec = float(args.round_wait_sec)
    use_cdp = not bool(args.launch_browser)

    def log(msg: str) -> None:
        if args.verbose or not args.quiet:
            _log_print(msg)

    log("———————— K11 Tongits 全自动打牌冒烟 ————————")
    log(f"目标: {target_url}  CDP: {_kalaroko_cdp(args.cdp_http) if use_cdp else '(自启浏览器)'}")
    log(f"等待结算上限: {wait_sec:.0f}s")

    my_name = (os.environ.get("K11_TONGITS_MY_NAME") or "victor").strip()
    port = int((os.environ.get("K11_TONGITS_MONITOR_PORT") or "17889").strip() or "17889")
    out_dir = SCRIPTS / "omnioutput"
    session = TongitsSmokeSession(
        my_name=my_name,
        monitor_port=port,
        out_dir=out_dir,
        log=log,
    )
    row: dict[str, Any]
    exit_code = 1

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("请先安装: pip install playwright && playwright install chromium", file=sys.stderr)
        return 2

    if not session.start_monitor_server():
        row = session.build_result_row(extra_wait_sec=0)
        send_tongits_lark_notification(
            row, target_url=target_url, log=log, no_lark=bool(args.no_lark_report)
        )
        return 1

    try:
        async with async_playwright() as p:
            page: Any
            browser: Any | None = None
            must_close = False

            if use_cdp:
                cdp = _kalaroko_cdp(args.cdp_http)
                log(f"  [cdp] 连接 {cdp}")
                browser = await p.chromium.connect_over_cdp(cdp)
                page = await _acquire_page_cdp(
                    browser, host=host, target_url=target_url, log=log
                )
                try:
                    mcp.register_kalaroko_popup_guardian(page.context)
                except Exception:
                    pass
            else:
                h = host or "www.kalaroko.com"
                browser, context, page, must_close = await mcp._launch_kalaroko_browser_context(
                    p,
                    viewport_width=459,
                    viewport_height=851,
                    device_scale_factor=2.0,
                    headless=bool(args.headless),
                    preferred_host=h,
                )

            entered = await session.enter_tongits_on_page(page, target_url=target_url)
            if not entered:
                row = session.build_result_row(extra_wait_sec=0)
                send_tongits_lark_notification(
                    row,
                    target_url=target_url,
                    log=log,
                    no_lark=bool(args.no_lark_report),
                    lark_wiki_url=(args.lark_wiki_url or "").strip(),
                )
                return 1

            log(f"  [tongits] 等待本局结算（最长 {wait_sec:.0f}s）…")
            import time as _time

            deadline = _time.monotonic() + wait_sec
            while _time.monotonic() < deadline:
                if session._last_settlement:
                    break
                await asyncio.sleep(2.0)

            row = session.build_result_row(extra_wait_sec=0)
            v = str(row.get("verdict") or "FAIL")
            log("")
            log(f"[结果] {row.get('verdict_zh')} ({v})")
            log(f"[备注] {row.get('detail', '')[:500]}")

            send_tongits_lark_notification(
                row,
                target_url=target_url,
                log=log,
                no_lark=bool(args.no_lark_report),
                lark_wiki_url=(args.lark_wiki_url or "").strip(),
            )
            exit_code = 0 if v == "PASS" else 1

            if must_close and browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass
    except Exception as e:
        log(f"[失败] {type(e).__name__}: {e}")
        row = session.build_result_row(extra_wait_sec=0)
        row["verdict"] = "FAIL"
        row["verdict_zh"] = "失败"
        row["detail"] = f"游戏金币变化: 失败 | 执行异常: {e}"
        send_tongits_lark_notification(
            row,
            target_url=target_url,
            log=log,
            no_lark=bool(args.no_lark_report),
        )
        exit_code = 1
    finally:
        await session.stop()

    return exit_code


def main() -> int:
    ap = argparse.ArgumentParser(description="K11 Tongits 全自动打牌 + 协议金币 Lark")
    ap.add_argument("--target-url", default=TARGET_HOME, help="Kalaroko 站点根 URL")
    ap.add_argument("--cdp-http", default="", help="CDP 地址（默认 KALAROKO_CDP_ENDPOINT 或 9222）")
    ap.add_argument(
        "--launch-browser",
        action="store_true",
        help="不用 CDP，自启 Chromium（默认连接调试 Chrome）",
    )
    ap.add_argument("--headless", action="store_true", help="自启浏览器时用无头模式")
    ap.add_argument(
        "--round-wait-sec",
        type=float,
        default=float(os.environ.get("K11_TONGITS_ROUND_WAIT_SEC") or "600"),
        help="等待一局 3016 结算的最长秒数（默认 600）",
    )
    ap.add_argument("--no-lark-report", action="store_true", help="不发飞书卡片")
    ap.add_argument("--lark-wiki-url", default="", help="飞书 Wiki 链接（卡片展示用）")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    try:
        return asyncio.run(_async_main(args))
    except KeyboardInterrupt:
        print("\n[中断]", flush=True)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
