#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K11 平台冒烟 · 扩展项（文档《K11_平台冒烟测试用例》约 48–58 行）

与 ``test_k11_p1_skill_herontest_playwright.py`` 相同：**不经过 L3**，本机 Playwright
``connect_over_cdp`` 附加已由 ``launch_chrome_debug.ps1`` 启动的 Chrome。

覆盖（自动化能力范围内）：
  P1 · 列表完整性、图片资源、静态资源异常（Console MIME/模块脚本等）、响应时间（分类切换）、无数据提示
  P2 · 轻量文案（替换字符）、轻量横向溢出、滚动后高度/稳定性；浏览器兼容 / 弱网 / 深度文案与样式标为 SKIP

前置与用法同 P1 脚本（仓库根 ``.env`` 中 ``KALAROKO_CDP_ENDPOINT`` 等）。

  python scripts/test_k11_platform_smoke_extended_playwright.py
  python scripts/test_k11_platform_smoke_extended_playwright.py -v --json-out out/k11_ext.json

退出码：0 无 FAIL；1 存在 FAIL；2 CDP/环境；3 未捕获异常。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", encoding="utf-8")
except ImportError:
    pass
except OSError:
    pass

DEFAULT_TARGET = "https://www.herontest.xin/"


def _kalaroko_cdp(cli: str | None) -> str:
    raw = (cli or "").strip() or (os.environ.get("KALAROKO_CDP_ENDPOINT") or "").strip()
    if not raw:
        raw = "http://127.0.0.1:9222"
    if not raw.startswith("http://") and not raw.startswith("https://"):
        raw = "http://" + raw.lstrip("/")
    return raw.rstrip("/")


def _host_from_url(url: str) -> str:
    try:
        from urllib.parse import urlparse

        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _home_feed_url(target: str) -> str:
    from urllib.parse import urlparse, urlunparse

    t = (target or DEFAULT_TARGET).strip() or DEFAULT_TARGET
    p = urlparse(t)
    if p.scheme and p.netloc:
        return urlunparse((p.scheme, p.netloc, "/", "", "", ""))
    return t.rstrip("/") + "/" if t else DEFAULT_TARGET


def _needs_goto_home_feed(current_url: str) -> bool:
    u = (current_url or "").lower()
    if "/my/" in u or "/me/" in u:
        return True
    if re.search(r"/(profile|account|wallet|settings)(/|$)", u):
        return True
    return False


async def _ensure_on_home_feed(
    page: Any, target_url: str, log: Callable[[str], None] | None
) -> None:
    home = _home_feed_url(target_url)
    try:
        cur = page.url or ""
    except Exception:
        cur = ""
    if not _needs_goto_home_feed(cur):
        if log:
            log(f"  [诊断] 已在非个人中心路径：{cur!r}")
        return
    if log:
        log(f"  [诊断] goto 大厅：{cur!r} → {home!r}")
    await page.goto(home, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(600)


async def _scroll_to_bottom(page: Any) -> None:
    try:
        h = await page.evaluate(
            "Math.max(document.body?.scrollHeight||0, document.documentElement.scrollHeight||0, "
            "document.scrollingElement?.scrollHeight||0)"
        )
        if h is not None:
            await page.evaluate("y => window.scrollTo(0, y)", max(0, int(h)))
        await page.wait_for_timeout(400)
    except Exception:
        pass
    try:
        await page.keyboard.press("End")
        await page.wait_for_timeout(350)
    except Exception:
        pass


VERDICT_ZH = {
    "PASS": "通过",
    "FAIL": "未通过",
    "SKIP": "跳过",
}


# 顺序：先首页滚底看「无数据」，再点 Party 测切换耗时（避免离开首页后找不到列表底文案）
CASE_DEFS: list[tuple[str, str, str]] = [
    ("ext_game_list", "P1", "列表完整性（主要游戏名可见）"),
    ("ext_images", "P1", "图片资源（img 裂图抽样）"),
    ("ext_no_more_data", "P1", "无数据提示（No More Data）"),
    ("ext_response_time", "P1", "响应时间（底栏 Party 切换耗时）"),
    ("ext_copy_light", "P2", "文案检查（轻量：Unicode 替换符）"),
    ("ext_layout_light", "P2", "样式检查（轻量：横向溢出提示）"),
    ("ext_scroll_light", "P2", "滚动加载（轻量：滚底后高度与稳定性）"),
    ("ext_static_console", "P1", "静态资源/模块加载（Console MIME/模块脚本抽样，置末以纳入全程）"),
    ("ext_browser_manual", "P2", "浏览器兼容（人工）"),
    ("ext_weak_net_manual", "P2", "弱网体验（人工/CDP 限速另测）"),
]

CASE_TITLE_ZH = {k: v for k, _, v in CASE_DEFS}


def _mime_console_failures(console_lines: list[str]) -> list[str]:
    keys = (
        "MIME type",
        "module script",
        "Failed to load module",
        "text/html",
        "Strict MIME",
    )
    return [x for x in console_lines if any(k in x for k in keys)]


async def _broken_images_report(page: Any) -> tuple[int, list[str]]:
    """主文档 + 各 frame 统计 complete 且 naturalWidth==0 的 img src（抽样）。"""
    js = """() => {
      const imgs = Array.from(document.querySelectorAll('img'));
      const bad = [];
      for (const im of imgs) {
        try {
          if (im.complete && im.naturalWidth === 0 && (im.src || im.currentSrc)) {
            const s = (im.currentSrc || im.src || '').slice(0, 160);
            if (s) bad.push(s);
          }
        } catch (e) {}
      }
      return { total: imgs.length, bad: bad.slice(0, 20) };
    }"""

    total = 0
    bad_all: list[str] = []
    for fr in page.frames:
        try:
            r = await fr.evaluate(js)
            total += int(r.get("total") or 0)
            for s in r.get("bad") or []:
                if s not in bad_all:
                    bad_all.append(s)
        except Exception:
            continue
    return total, bad_all[:25]


async def _visible_any(page: Any, pat: str, *, timeout_ms: float = 3500) -> bool:
    try:
        loc = page.get_by_text(re.compile(pat, re.I)).first
        await loc.wait_for(state="visible", timeout=int(timeout_ms))
        return True
    except Exception:
        return False


async def _run_ext_game_list(page: Any) -> tuple[str, str]:
    try:
        await page.mouse.wheel(0, 900)
        await page.wait_for_timeout(350)
    except Exception:
        pass
    patterns = [
        r"Tongits\s*King",
        r"Royal\s*Pusoy",
        r"Texas\s*Holdem",
        r"Bingo",
        r"Party",
    ]
    found: list[str] = []
    missing: list[str] = []
    for pat in patterns:
        ok = await _visible_any(page, pat, timeout_ms=2800)
        if ok:
            found.append(pat)
        else:
            missing.append(pat)
    if len(found) >= 3:
        return ("PASS", f"至少命中 {len(found)}/{len(patterns)} 组关键词：{', '.join(found)}")
    return (
        "FAIL",
        f"可见游戏/模块文案不足（需≥3）。命中：{found or '无'}；未命中：{', '.join(missing)}。"
        "请先在大厅首页并滚到游戏区。",
    )


async def _run_ext_images(page: Any) -> tuple[str, str]:
    total, bad = await _broken_images_report(page)
    if total == 0:
        return ("SKIP", "页面上未统计到 img 节点（可能图为 background 或 canvas）。")
    if bad:
        return (
            "FAIL",
            f"发现 {len(bad)} 个疑似裂图（complete 且 naturalWidth=0），共扫描约 {total} 个 img。"
            f" 示例：{bad[0][:120]}…" if bad else "",
        )
    return ("PASS", f"抽样检查：{total} 个 img 未发现典型裂图（naturalWidth=0）。")


async def _run_ext_static_console(console_bucket: list[str]) -> tuple[str, str]:
    bad = _mime_console_failures(console_bucket)
    if bad:
        return (
            "FAIL",
            "Console 存在与 MIME/模块脚本相关的错误（可能静态资源被 HTML 替代或缓存异常）："
            + " | ".join(bad[:3]),
        )
    if not console_bucket:
        return ("PASS", "抽样 Console error 中未见典型 MIME/模块脚本类报错。")
    return ("PASS", f"有 {len(console_bucket)} 条 Console error 抽样，但无 MIME/模块脚本关键字命中。")


async def _run_ext_response_time(page: Any, threshold_ms: float, log: Callable[[str], None]) -> tuple[str, str]:
    """底栏 Party：精确文案 .last（与 P1 脚本同源假设）。"""
    t0 = time.monotonic()
    clicked = False
    for fr in page.frames:
        try:
            n = await fr.get_by_text("Party", exact=True).count()
            if n < 1:
                continue
            loc = fr.get_by_text("Party", exact=True).last
            await loc.wait_for(state="visible", timeout=3000)
            await loc.click(timeout=5000)
            clicked = True
            break
        except Exception as e:
            if log:
                log(f"  [诊断·耗时] frame 点 Party：{type(e).__name__}")
            continue
    if not clicked:
        return ("SKIP", "未能点击底栏「Party」，跳过耗时统计（请先在大厅页）。")
    try:
        await page.wait_for_load_state("networkidle", timeout=12_000)
    except Exception:
        pass
    dt_ms = (time.monotonic() - t0) * 1000
    if dt_ms <= threshold_ms:
        return ("PASS", f"点击 Party 后至 networkidle（或超时）约 {dt_ms:.0f} ms（阈值 {threshold_ms:.0f} ms）。")
    return (
        "FAIL",
        f"切换耗时 {dt_ms:.0f} ms 超过阈值 {threshold_ms:.0f} ms（仅供参考，弱网可调高 --switch-ms）",
    )


async def _run_ext_no_more_data(page: Any) -> tuple[str, str]:
    try:
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(200)
    except Exception:
        pass
    await _scroll_to_bottom(page)
    ok = await _visible_any(page, r"No\s*More\s*Data", timeout_ms=4000)
    if ok:
        return ("PASS", "列表底部可见「No More Data」类文案。")
    ok2 = await _visible_any(page, r"没有更多|沒有更多|no\s+more", timeout_ms=1500)
    if ok2:
        return ("PASS", "列表底部可见「没有更多」类中文/变体文案。")
    return (
        "FAIL",
        "滚底后未见「No More Data」或常见中文无更多提示（若列表未分页到底则属环境差异）。",
    )


async def _run_ext_copy_light(page: Any) -> tuple[str, str]:
    try:
        txt = await page.evaluate(
            "() => (document.body && document.body.innerText) ? document.body.innerText.slice(0, 80000) : ''"
        )
    except Exception as e:
        return ("SKIP", f"无法读取正文：{e}")
    if not txt:
        return ("SKIP", "正文为空。")
    if "\ufffd" in txt or "\uFFFD" in txt:
        return ("FAIL", "正文出现 Unicode 替换字符 U+FFFD（可能编码/乱码）。")
    return ("PASS", "正文前 80k 字符未见 U+FFFD 替换符（非完整错别字审计）。")


async def _run_ext_layout_light(page: Any) -> tuple[str, str]:
    try:
        r = await page.evaluate(
            """() => {
              const de = document.documentElement;
              const b = document.body;
              const sw = Math.max(de.scrollWidth, b ? b.scrollWidth : 0);
              const cw = de.clientWidth;
              return { scrollWidth: sw, clientWidth: cw, ratio: cw ? sw / cw : 1 };
            }"""
        )
        ratio = float(r.get("ratio") or 1)
        if ratio > 1.35:
            return (
                "FAIL",
                f"主文档横向 scrollWidth/clientWidth 比 ≈ {ratio:.2f}，可能存在明显横向溢出（轻量启发式）。",
            )
        return ("PASS", f"横向比例 ≈ {ratio:.2f}（轻量，非视觉回归）。")
    except Exception as e:
        return ("SKIP", f"无法测量布局：{e}")


async def _run_ext_scroll_light(page: Any) -> tuple[str, str]:
    try:
        h0 = await page.evaluate(
            "Math.max(document.body?.scrollHeight||0, document.documentElement.scrollHeight||0)"
        )
        for _ in range(4):
            await page.mouse.wheel(0, 1400)
            await page.wait_for_timeout(350)
        h1 = await page.evaluate(
            "Math.max(document.body?.scrollHeight||0, document.documentElement.scrollHeight||0)"
        )
        await _scroll_to_bottom(page)
        h2 = await page.evaluate(
            "Math.max(document.body?.scrollHeight||0, document.documentElement.scrollHeight||0)"
        )
        if h2 < (h0 or 0) * 0.5:
            return ("FAIL", f"滚底后 scrollHeight 异常收缩（{h0} → {h2}），可能存在布局闪动。")
        return (
            "PASS",
            f"滚动后高度 {h0} → {h1} → {h2}（轻量：未检测剧烈收缩）。",
        )
    except Exception as e:
        return ("SKIP", f"滚动测试异常：{e}")


async def _run_case(
    case_id: str,
    page: Any,
    *,
    console_bucket: list[str],
    switch_ms: float,
    log: Callable[[str], None],
) -> tuple[str, str]:
    if case_id == "ext_game_list":
        return await _run_ext_game_list(page)
    if case_id == "ext_images":
        return await _run_ext_images(page)
    if case_id == "ext_static_console":
        return await _run_ext_static_console(console_bucket)
    if case_id == "ext_response_time":
        return await _run_ext_response_time(page, switch_ms, log)
    if case_id == "ext_no_more_data":
        return await _run_ext_no_more_data(page)
    if case_id == "ext_copy_light":
        return await _run_ext_copy_light(page)
    if case_id == "ext_layout_light":
        return await _run_ext_layout_light(page)
    if case_id == "ext_scroll_light":
        return await _run_ext_scroll_light(page)
    if case_id == "ext_browser_manual":
        return ("SKIP", "请在 Chrome / Edge 等目标浏览器人工回归（本脚本仅连当前 CDP 实例）。")
    if case_id == "ext_weak_net_manual":
        return ("SKIP", "弱网请用 DevTools 限速或 Playwright CDP 另设 network 条件（本脚本未内置限速）。")
    return ("BLOCKED", f"未知用例：{case_id}")


async def _async_main(args: argparse.Namespace) -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("请先安装：pip install playwright && playwright install chromium", file=sys.stderr)
        return 2

    cdp = _kalaroko_cdp(args.cdp_http or None)
    target_url = (args.target_url or DEFAULT_TARGET).strip()
    host = _host_from_url(target_url)
    case_ids = [c[0] for c in CASE_DEFS]

    def log(msg: str) -> None:
        if args.verbose or not args.quiet:
            print(msg, flush=True)

    log("———————— K11 平台冒烟 · 扩展（文档 48–58 行）————————")
    log(f"CDP：{cdp}  目标：{target_url}")
    log(f"分类切换阈值：{args.switch_ms} ms")
    log("")

    console_bucket: list[str] = []

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(cdp)
        if not browser.contexts:
            print("[失败] CDP 已连上但无 context", file=sys.stderr)
            return 2
        ctx = browser.contexts[0]
        pages = list(ctx.pages)
        if not pages:
            print("[失败] 无打开标签页", file=sys.stderr)
            return 2

        picked = None
        for pg in reversed(pages):
            try:
                u = pg.url or ""
            except Exception:
                u = ""
            if host and host in u.lower():
                picked = pg
                break
        if picked is None:
            if args.navigate_if_no_tab:
                picked = pages[-1]
                log(f"[nav] goto {target_url}")
                await picked.goto(target_url, wait_until="domcontentloaded", timeout=60_000)
            else:
                print(
                    f"[失败] 无含 {host!r} 的标签页。请打开站点或加 --navigate-if-no-tab",
                    file=sys.stderr,
                )
                return 2
        else:
            await picked.bring_to_front()

        page = picked

        def _on_console(msg: Any) -> None:
            try:
                if msg.type == "error":
                    console_bucket.append(f"{msg.type}: {msg.text[:600]}")
            except Exception:
                pass

        page.on("console", _on_console)

        log(f"当前 URL：{page.url}")
        log(f"标题：{await page.title()}")
        log("")
        log("准备：回到大厅首页（避免停在 /my/index）…")
        await _ensure_on_home_feed(page, target_url, log)
        log("轻微滚屏以便游戏区与列表露出…")
        await page.mouse.wheel(0, 400)
        await page.wait_for_timeout(400)

        results: list[dict[str, Any]] = []
        for i, cid in enumerate(case_ids, start=1):
            tier = next(t for k, t, _ in CASE_DEFS if k == cid)
            title = CASE_TITLE_ZH[cid]
            log(f"【{i}/{len(case_ids)}】[{tier}] {title}（{cid}）")
            v, detail = await _run_case(
                cid,
                page,
                console_bucket=console_bucket,
                switch_ms=float(args.switch_ms),
                log=log,
            )
            log(f"  观察说明：{detail}")
            log(f"  结论：{VERDICT_ZH.get(v, v)}（{v}）")
            log("")
            results.append(
                {
                    "case": cid,
                    "tier": tier,
                    "title_zh": title,
                    "verdict": v,
                    "detail": detail,
                }
            )

        mime_hits = _mime_console_failures(console_bucket)
        if mime_hits:
            log("———————— Console · MIME/模块脚本相关（供对照）————————")
            for s in mime_hits[:8]:
                log("  · " + s[:300])
            log("")

        out = {
            "schema": "k11_platform_smoke_extended/v1",
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "cdp": cdp,
            "target_url": target_url,
            "switch_ms_threshold": args.switch_ms,
            "page_url_final": page.url,
            "page_title_final": await page.title(),
            "console_errors_sample": console_bucket[:30],
            "results": results,
        }
        if args.json_out:
            outp = Path(args.json_out)
            outp.parent.mkdir(parents=True, exist_ok=True)
            outp.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            log(f"JSON：{outp.resolve()}")

        log("———————— 汇总 ————————")
        for r in results:
            m = "✓" if r["verdict"] == "PASS" else ("○" if r["verdict"] == "SKIP" else "✗")
            log(f"  {m} [{r['tier']}] {r['title_zh']} → {VERDICT_ZH.get(r['verdict'], r['verdict'])}")

        bad = {r["verdict"] for r in results}
        if "FAIL" in bad or "BLOCKED" in bad:
            log("\n最终结果：存在未通过项，退出码 1。")
            return 1
        log("\n最终结果：无 FAIL，退出码 0。")
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="K11 文档 48–58 行扩展冒烟（Playwright CDP）")
    ap.add_argument("--target-url", default=DEFAULT_TARGET, help="站点 URL（匹配标签页 host）")
    ap.add_argument("--cdp-http", default="", help="覆盖 KALAROKO_CDP_ENDPOINT")
    ap.add_argument("--navigate-if-no-tab", action="store_true")
    ap.add_argument(
        "--switch-ms",
        type=float,
        default=12000.0,
        help="P1 响应时间：底栏 Party 点击后至 networkidle 的上限毫秒（默认 12000）",
    )
    ap.add_argument("--json-out", type=Path, default=None)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    try:
        return asyncio.run(_async_main(args))
    except Exception as e:
        print(f"[失败] {type(e).__name__}: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
