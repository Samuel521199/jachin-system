#!/usr/bin/env python3
"""
Kalaroko 单页性能指标抓取（Playwright）

与产品侧「指标」表对齐，仅采集：
  - TTFB（首字节时间）
  - FCP（首次内容绘制）
  - DOMContentLoaded
  - 页面完全加载（load）
  - 总资源数
  - 失败资源数（navigation 期间 Playwright requestfailed）
  - 协议（主文档 nextHopProtocol + 子资源 protocol 计数）

浏览器：
  - 优先读取环境变量 ``KALAROKO_CDP_ENDPOINT``（如 ``http://127.0.0.1:9222``），连接已启动的 Chrome；
  - 若未设置 CDP，可用 ``--launch-chromium`` 临时拉起 Playwright Chromium（无需本机调试端口）。

依赖：
  pip install playwright
  playwright install chromium

用法（仓库根）：
  python scripts/kalaroko_capture_page_metrics.py
  python scripts/kalaroko_capture_page_metrics.py --url https://kalaroko.com/
  python scripts/kalaroko_capture_page_metrics.py --launch-chromium --wait-until load

输出：stdout 为一行 JSON（UTF-8）；stderr 为人可读摘要。
退出码：0 成功，非 0 失败。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", encoding="utf-8")
except ImportError:
    pass
except OSError:
    pass


# Performance API：主文档 + paint + resource；legacy timing 作兜底（与旧页面兼容）
_COLLECT_METRICS_JS = """
() => {
  const out = {
    ttfb_ms: null,
    fcp_ms: null,
    dom_content_loaded_ms: null,
    page_fully_loaded_ms: null,
    total_resources: 0,
    document_next_hop_protocol: null,
    resource_next_hop_protocol_counts: {},
    notes: [],
  };

  try {
    const nav = performance.getEntriesByType('navigation')[0];
    if (nav) {
      const rs = nav.responseStart;
      const rq = nav.requestStart;
      if (typeof rs === 'number' && typeof rq === 'number') {
        out.ttfb_ms = Math.round(Math.max(0, rs - rq));
      }
      out.dom_content_loaded_ms = Math.round(nav.domContentLoadedEventEnd - nav.startTime);
      const le = nav.loadEventEnd;
      out.page_fully_loaded_ms = (typeof le === 'number' && le > 0)
        ? Math.round(le - nav.startTime)
        : null;
      out.document_next_hop_protocol = nav.nextHopProtocol || null;
    }
  } catch (e) {
    out.notes.push('navigation_timing_v2:' + String(e));
  }

  try {
    const w = performance.timing;
    const ns = w && w.navigationStart;
    if (ns && w.responseStart != null && w.fetchStart != null) {
      const legacyTtfb = Math.max(0, w.responseStart - w.fetchStart);
      if (out.ttfb_ms == null) {
        out.ttfb_ms = Math.round(legacyTtfb);
      }
      if (out.dom_content_loaded_ms == null && w.domContentLoadedEventEnd) {
        out.dom_content_loaded_ms = Math.round(w.domContentLoadedEventEnd - ns);
      }
      if (
        out.page_fully_loaded_ms == null &&
        w.loadEventEnd != null &&
        w.loadEventEnd > 0
      ) {
        out.page_fully_loaded_ms = Math.round(w.loadEventEnd - ns);
      }
    }
  } catch (e) {
    out.notes.push('legacy_timing:' + String(e));
  }

  try {
    const paints = performance.getEntriesByType('paint');
    for (const p of paints) {
      if (p.name === 'first-contentful-paint') {
        out.fcp_ms = Math.round(p.startTime);
        break;
      }
    }
  } catch (e) {
    out.notes.push('paint:' + String(e));
  }

  try {
    const resources = performance.getEntriesByType('resource');
    out.total_resources = resources.length;
    const counts = {};
    for (const r of resources) {
      const proto = r.nextHopProtocol || 'unknown';
      counts[proto] = (counts[proto] || 0) + 1;
    }
    out.resource_next_hop_protocol_counts = counts;
  } catch (e) {
    out.notes.push('resource:' + String(e));
  }

  return out;
}
"""


def _cdp_endpoint() -> str | None:
    raw = (os.environ.get("KALAROKO_CDP_ENDPOINT") or "").strip()
    if not raw:
        return None
    if not raw.startswith("http://") and not raw.startswith("https://"):
        raw = "http://" + raw.lstrip("/")
    return raw


async def _probe_page(pg: Any) -> bool:
    try:
        if pg.is_closed():
            return False
        await asyncio.wait_for(pg.evaluate("() => 1"), timeout=2.5)
        return True
    except Exception:
        return False


async def _pick_cdp_page(context: Any) -> Any:
    raw = (os.environ.get("KALAROKO_CDP_NEW_TAB") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        pg = await context.new_page()
        await asyncio.wait_for(pg.evaluate("() => 1"), timeout=10.0)
        return pg

    pages = list(getattr(context, "pages", []) or [])
    for idx in range(len(pages) - 1, -1, -1):
        pg = pages[idx]
        if await _probe_page(pg):
            return pg

    pg = await context.new_page()
    await asyncio.wait_for(pg.evaluate("() => 1"), timeout=8.0)
    return pg


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    from playwright.async_api import async_playwright

    failed_urls: list[str] = []
    failed_events = 0

    async with async_playwright() as p:
        browser = None
        context = None
        page = None

        vp_w = max(320, int(args.viewport_width))
        vp_h = max(480, int(args.viewport_height))
        dsf = float(args.device_scale_factor)

        if args.launch_chromium:
            browser = await p.chromium.launch(headless=args.headless)
            context = await browser.new_context(
                viewport={"width": vp_w, "height": vp_h},
                device_scale_factor=dsf,
            )
            page = await context.new_page()
        else:
            endpoint = _cdp_endpoint()
            if not endpoint:
                raise RuntimeError(
                    "未设置 KALAROKO_CDP_ENDPOINT。请在 .env 中配置例如 "
                    "KALAROKO_CDP_ENDPOINT=http://127.0.0.1:9222 并启动远程调试 Chrome，"
                    "或改用 --launch-chromium"
                )
            browser = await p.chromium.connect_over_cdp(endpoint)
            if browser.contexts:
                context = browser.contexts[0]
            else:
                context = await browser.new_context(
                    viewport={"width": vp_w, "height": vp_h},
                    device_scale_factor=dsf,
                )
            page = await _pick_cdp_page(context)

        def _on_failed(request: Any) -> None:
            nonlocal failed_events
            try:
                failed_events += 1
                u = request.url or ""
                if u and len(failed_urls) < 200:
                    failed_urls.append(u)
            except Exception:
                pass

        page.on("requestfailed", _on_failed)

        timeout_ms = max(5000, int(args.timeout_ms))
        try:
            await page.goto(
                args.url,
                wait_until=args.wait_until,
                timeout=timeout_ms,
            )
            # 给 paint / resource 入账一点时间（networkidle 下通常已足够）
            await page.wait_for_timeout(int(args.post_settle_ms))
        finally:
            try:
                page.remove_listener("requestfailed", _on_failed)
            except Exception:
                pass

        raw_block = await page.evaluate(_COLLECT_METRICS_JS)
        if not isinstance(raw_block, dict):
            raw_block = {}

        failed_count = failed_events

        doc_proto = raw_block.get("document_next_hop_protocol")
        res_counts = raw_block.get("resource_next_hop_protocol_counts") or {}

        result: dict[str, Any] = {
            "ok": True,
            "url": args.url,
            "wait_until": args.wait_until,
            # 与 Excel「指标」列一一对应（便于对照）
            "metrics": {
                "TTFB_ms": raw_block.get("ttfb_ms"),
                "FCP_ms": raw_block.get("fcp_ms"),
                "DOMContentLoaded_ms": raw_block.get("dom_content_loaded_ms"),
                "page_fully_loaded_ms": raw_block.get("page_fully_loaded_ms"),
                "total_resources": raw_block.get("total_resources"),
                "failed_resources_count": failed_count,
                "document_protocol": doc_proto,
                "resource_protocol_counts": res_counts,
            },
            "metrics_zh_labels": {
                "首字节时间_TTFB_ms": raw_block.get("ttfb_ms"),
                "首次内容绘制_FCP_ms": raw_block.get("fcp_ms"),
                "DOMContentLoaded_ms": raw_block.get("dom_content_loaded_ms"),
                "页面完全加载_ms": raw_block.get("page_fully_loaded_ms"),
                "总资源数": raw_block.get("total_resources"),
                "失败资源数": failed_count,
                "主文档协议": doc_proto,
                "子资源协议分布": res_counts,
            },
            "failed_resource_urls_sample": failed_urls[:50],
            "collect_notes": raw_block.get("notes") or [],
            "browser": "cdp" if not args.launch_chromium else "launch_chromium",
        }

        try:
            await browser.close()
        except Exception:
            pass

        return result


def main() -> None:
    ap = argparse.ArgumentParser(description="抓取 Kalaroko 单页 Performance 指标（JSON 输出）")
    ap.add_argument("--url", default="https://kalaroko.com/", help="目标 URL")
    ap.add_argument(
        "--wait-until",
        default="networkidle",
        choices=("load", "domcontentloaded", "commit", "networkidle"),
        help="page.goto wait_until",
    )
    ap.add_argument("--timeout-ms", type=int, default=90000, help="goto 超时（毫秒）")
    ap.add_argument("--post-settle-ms", type=int, default=400, help="goto 后再等待毫秒，便于 paint 入账")
    ap.add_argument("--viewport-width", type=int, default=390)
    ap.add_argument("--viewport-height", type=int, default=844)
    ap.add_argument("--device-scale-factor", type=float, default=2.0)
    ap.add_argument(
        "--launch-chromium",
        action="store_true",
        help="不连 CDP，直接 launch Playwright Chromium",
    )
    ap.add_argument(
        "--headless",
        action="store_true",
        default=os.environ.get("KALAROKO_HEADLESS", "true").strip().lower()
        in ("1", "true", "yes", "on"),
        help="仅 --launch-chromium 时有效",
    )
    args = ap.parse_args()

    try:
        out = asyncio.run(_run(args))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False), flush=True)
        raise SystemExit(1) from e

    print(json.dumps(out, ensure_ascii=False, indent=2), flush=True)

    m = out.get("metrics") or {}
    sys.stderr.write(
        "[kalaroko_capture_page_metrics] TTFB={} ms | FCP={} ms | DCL={} ms | load={} ms | "
        "resources={} | failed={} | doc_proto={}\n".format(
            m.get("TTFB_ms"),
            m.get("FCP_ms"),
            m.get("DOMContentLoaded_ms"),
            m.get("page_fully_loaded_ms"),
            m.get("total_resources"),
            m.get("failed_resources_count"),
            m.get("document_protocol"),
        )
    )


if __name__ == "__main__":
    main()
