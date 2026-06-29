#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K11 · Tongits 自动打牌接管冒烟（独立按钮）。

流程：
1. 连接调试 Chrome（CDP）或自启 Chromium
2. 接管用户已打开的 Tongits 主页面（不再自动进大厅 / 点入口 / Join）
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
    """优先复用已经打开的 Tongits / Kalaroko 页签。"""
    candidates: list[tuple[int, Any, str]] = []
    for ctx in browser.contexts:
        for pg in ctx.pages:
            try:
                u = (pg.url or "").lower()
                frame_urls = []
                try:
                    frame_urls = [str(getattr(fr, "url", "") or "").lower() for fr in pg.frames]
                except Exception:
                    frame_urls = []
                joined = "\n".join([u] + frame_urls)
                score = 0
                if "game-frame" in joined:
                    score += 100
                if "gweb." in joined or "heronpro" in joined:
                    score += 80
                if "tongits" in joined:
                    score += 60
                if host and host in u:
                    score += 30
                if "kalaroko.com" in joined:
                    score += 20
                if score > 0:
                    candidates.append((score, pg, pg.url))
            except Exception:
                continue
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        score, pg, url = candidates[0]
        log(f"  [cdp] 复用当前页签: score={score} url={url[:160]}")
        return pg
    ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
    pg = await ctx.new_page()
    log(
        "  [cdp] 未找到已打开的 Kalaroko/Tongits 页签，"
        f"仅新开目标页用于诊断: {target_url}"
    )
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

    log("———————— K11 Tongits 自动打牌接管冒烟 ————————")
    log(f"目标: {target_url}  CDP: {_kalaroko_cdp(args.cdp_http) if use_cdp else '(自启浏览器)'}")
    log(f"等待结算上限: {wait_sec:.0f}s")
    log("模式: 请先手动打开 Tongits 主页面/牌桌；本脚本只接管当前页面开始打牌，不再自动入场")

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

            attached = await session.attach_ready_tongits_page(page, target_url=target_url)
            if not attached:
                row = session.build_result_row(extra_wait_sec=0)
                send_tongits_lark_notification(
                    row,
                    target_url=target_url,
                    log=log,
                    no_lark=bool(args.no_lark_report),
                    lark_wiki_url=(args.lark_wiki_url or "").strip(),
                )
                return 1

            log(f"  [tongits] 等待本局协议结算（最长 {wait_sec:.0f}s）…")
            import time as _time

            deadline = _time.monotonic() + wait_sec
            heartbeat_at = _time.monotonic()
            while _time.monotonic() < deadline:
                if session._last_settlement:
                    break
                try:
                    from_proto = session._load_recent_settlement_from_proto_status()
                    if from_proto:
                        session._last_settlement = from_proto
                        break
                    from_log = session._load_recent_settlement_from_log()
                    if from_log:
                        session._last_settlement = from_log
                        break
                except Exception:
                    pass
                await asyncio.sleep(2.0)
                now = _time.monotonic()
                if now >= heartbeat_at:
                    remain = max(0.0, deadline - now)
                    bot = session._bot_proc
                    bot_code = bot.poll() if bot is not None else None
                    bot_state = (
                        "未启动"
                        if bot is None
                        else ("运行中" if bot_code is None else f"已退出({bot_code})")
                    )
                    log(
                        "  [tongits] 等待结算中..."
                        f" 剩余 {remain:.0f}s | bot={bot_state} | "
                        f"settlement={'yes' if session._last_settlement else 'no'}"
                    )
                    heartbeat_at = now + 15.0
            if not session._last_settlement:
                log(
                    "  [tongits] 等待结束但未收到协议结算；"
                    "将发送未监听到结算的 Lark 失败卡片"
                )

            row = session.build_result_row(extra_wait_sec=0)
            v = str(row.get("verdict") or "FAIL")
            log("")
            log(f"[结果] {row.get('verdict_zh')} ({v})")
            log(f"[备注] {row.get('detail', '')[:500]}")
            settlement = row.get("tongits_settlement")
            if settlement:
                log(f"[结算] {settlement}")
            else:
                log("[结算] 未收到协议结算")

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
        help="等待一局协议结算的最长秒数（默认 600）",
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
