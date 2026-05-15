#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Facebook Events Manager — 事件打分抓取脚本

抓取 Kalaroko-internal01 pixel 下所有事件的名称与 Event match quality 分数，
格式化后发送 Lark 文本消息。

用法
----
  python scripts/fb_events_quality.py            # 抓取并推送 Lark
  python scripts/fb_events_quality.py --dry-run  # 仅打印，不发 Lark

CDP 连接与 Lark 配置复用 scripts/fb_report_nexus.env（无需额外配置文件）：
  FB_REPORT_CDP_URL      本机直连推荐 http://127.0.0.1:9223（默认 Ngrok URL）
  LARK_APP_ID / LARK_APP_SECRET / LARK_RECEIVER_ID / LARK_RECEIVE_ID_TYPE

可选环境变量
  FB_EVENTS_TARGET_URL   覆盖默认 Events Manager URL
  FB_EVENTS_GOTO_TIMEOUT_MS  页面加载超时（毫秒），默认 120000
  FB_EVENTS_WAIT_SEC     等待 SPA 渲染完成的额外秒数，默认 8
  FB_EVENTS_DEBUG_SCREENSHOT  设为 1 时保存调试截图
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import traceback
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# 环境变量加载（复用 fb_report_nexus.env）
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).resolve().parent
_ENV_PATH = _SCRIPT_DIR / "fb_report_nexus.env"


def _load_env_file() -> None:
    if not _ENV_PATH.is_file():
        return
    try:
        text = _ENV_PATH.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, val)


_load_env_file()

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_CDP_URL = "https://nestor-gravelish-alix.ngrok-free.dev"
CDP_URL: str = (os.environ.get("FB_REPORT_CDP_URL") or DEFAULT_CDP_URL).strip().rstrip("/")

NGROK_HEADERS = {"ngrok-skip-browser-warning": "1"}
CONNECT_TIMEOUT_MS = 90_000
CONNECT_HTTP_TIMEOUT_SEC = 90

DEFAULT_TARGET_URL = (
    "https://eventsmanager.facebook.com/events_manager2/list/pixel/"
    "801922808971329/overview?"
    "business_id=1423598819502157&act=2117441032349622&nav_source=ads_ecosystem_nav"
)
TARGET_URL: str = (os.environ.get("FB_EVENTS_TARGET_URL") or DEFAULT_TARGET_URL).strip()

GOTO_TIMEOUT_MS: int = int(os.environ.get("FB_EVENTS_GOTO_TIMEOUT_MS") or "120000")
WAIT_AFTER_LOAD_SEC: float = float(os.environ.get("FB_EVENTS_WAIT_SEC") or "8")
DEBUG_SCREENSHOT: bool = (os.environ.get("FB_EVENTS_DEBUG_SCREENSHOT") or "").strip() in (
    "1", "true", "yes"
)

LARK_APP_ID = (os.environ.get("LARK_APP_ID") or "").strip()
LARK_APP_SECRET = (os.environ.get("LARK_APP_SECRET") or "").strip()
LARK_RECEIVE_ID_TYPE = (os.environ.get("LARK_RECEIVE_ID_TYPE") or "chat_id").strip()
LARK_RECEIVER_ID = (os.environ.get("LARK_RECEIVER_ID") or "").strip()
LARK_API_BASE = "https://open.larksuite.com"


def _lark_receivers() -> list[str]:
    raw = (os.environ.get("LARK_RECEIVER_IDS") or "").strip()
    if raw:
        return [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
    return [LARK_RECEIVER_ID] if LARK_RECEIVER_ID else []


def _is_local_cdp(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host in ("localhost", "127.0.0.1", "::1") or host.startswith("127.")


# ---------------------------------------------------------------------------
# CDP 连接辅助（与 fb_report_nexus.py 逻辑完全一致）
# ---------------------------------------------------------------------------


def _rewrite_wss_endpoint(http_base: str) -> str:
    """拉取 /json/version，将 webSocketDebuggerUrl 改写为正确的 wss:// 地址。"""
    base = http_base.strip().rstrip("/")
    pub = urlparse(base)
    headers = {**NGROK_HEADERS, "Accept": "application/json"}
    data: Optional[dict] = None
    last_exc: Optional[BaseException] = None
    for path in ("/json/version", "/json/version/"):
        req = urllib.request.Request(base + path, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=CONNECT_HTTP_TIMEOUT_SEC) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            break
        except Exception as e:
            last_exc = e
    if not data:
        raise RuntimeError("无法拉取 /json/version") from last_exc
    raw_ws = data.get("webSocketDebuggerUrl", "")
    if not raw_ws:
        raise RuntimeError(f"/json/version 缺少 webSocketDebuggerUrl：{data!r}")
    w = urlparse(raw_ws)
    suffix = w.path + (f"?{w.query}" if w.query else "")
    scheme = "wss" if pub.scheme == "https" else "ws"
    return f"{scheme}://{pub.netloc}{suffix}"


def _triage(exc: BaseException) -> None:
    local = _is_local_cdp(CDP_URL)
    port = urlparse(CDP_URL).port or 9223
    print(f"\n[分诊] CDP 连接失败：{exc!r}", file=sys.stderr)
    if local:
        print(
            f"  → Chrome 是否以 --remote-debugging-port={port} 启动？\n"
            f"  → 验证：curl -sS http://127.0.0.1:{port}/json/version",
            file=sys.stderr,
        )
    else:
        print(
            "  → Ngrok 是否在线？URL 是否过期？\n"
            f"  → 验证：curl -sS -H 'ngrok-skip-browser-warning: 1' {CDP_URL}/json/version",
            file=sys.stderr,
        )
    traceback.print_exc(file=sys.stderr)


# ---------------------------------------------------------------------------
# DOM 抓取（JavaScript 注入）
# ---------------------------------------------------------------------------

# 注入到页面的 JS：多策略提取 event name + match quality
_EXTRACT_JS = r"""
(function extractEventQuality() {
    const results = [];
    const seenEvents = new Set();

    // ── 策略 1：找到文本严格匹配 "X.X/10" 或 "X/10" 的叶节点，向上追溯行容器 ──
    const scorePattern = /^(\d+\.?\d*)\/10$/;

    function getScoreElements() {
        const candidates = [];
        // 遍历所有文本节点
        const walker = document.createTreeWalker(
            document.body,
            NodeFilter.SHOW_TEXT,
            null
        );
        let node;
        while ((node = walker.nextNode())) {
            const t = node.textContent.trim();
            if (scorePattern.test(t)) {
                candidates.push({ node, score: t });
            }
        }
        return candidates;
    }

    function findRowAncestor(el, maxDepth) {
        let cur = el;
        for (let i = 0; i < maxDepth; i++) {
            if (!cur) return null;
            const role = cur.getAttribute && cur.getAttribute('role');
            const tag = cur.tagName && cur.tagName.toLowerCase();
            if (role === 'row' || tag === 'tr' || tag === 'li') return cur;
            // 启发式：如果该元素 innerText 包含 Active/Inactive 且有足够内容，视为行
            const text = cur.innerText || '';
            if ((text.includes('Active') || text.includes('Inactive')) && text.length > 20 && text.length < 800) {
                return cur;
            }
            cur = cur.parentElement;
        }
        return null;
    }

    function extractEventNameFromRow(rowEl) {
        if (!rowEl) return null;
        const fullText = rowEl.innerText || '';
        const lines = fullText.split('\n').map(l => l.trim()).filter(Boolean);

        // 过滤掉已知的非事件名字符串
        const skipWords = new Set([
            'Active', 'Inactive', 'Server', 'Client', 'Update recommended',
            'Used by', 'Connection Method', 'Event match quality', 'Status',
            'Events', 'Total events', 'ad set', 'ad sets',
        ]);
        const skipPatterns = [
            /^\d+\.?\d*\/10$/,     // score
            /^\d[\d,.]*[KkMm]?$/,  // numbers
            /^Last seen/,
            /^\d+ (min|hour|day|week)/,
        ];

        for (const line of lines) {
            if (skipWords.has(line)) continue;
            if (skipPatterns.some(p => p.test(line))) continue;
            // 事件名通常以大写字母开头且不超过 60 字符
            if (line.length > 2 && line.length < 60) {
                return line;
            }
        }
        return null;
    }

    function hasUpdateRecommended(rowEl) {
        if (!rowEl) return false;
        return (rowEl.innerText || '').includes('Update recommended');
    }

    const scoreNodes = getScoreElements();
    for (const { node, score } of scoreNodes) {
        const rowEl = findRowAncestor(node.parentElement, 12);
        if (!rowEl) continue;
        const eventName = extractEventNameFromRow(rowEl);
        if (!eventName) continue;
        if (seenEvents.has(eventName)) continue;
        seenEvents.add(eventName);
        results.push({
            event: eventName,
            score: score,
            note: hasUpdateRecommended(rowEl) ? 'Update recommended' : ''
        });
    }

    // ── 策略 2：如果策略 1 没结果，尝试拿页面全文并返回给 Python 解析 ──
    if (results.length === 0) {
        return { fallback_text: (document.querySelector('main') || document.body).innerText };
    }

    return { results };
})()
"""

# ---------------------------------------------------------------------------
# 结果格式化 & Lark 卡片构建
# ---------------------------------------------------------------------------

EventRow = dict[str, str]


def _parse_score(score_str: str) -> float:
    try:
        return float(score_str.split("/")[0])
    except (ValueError, IndexError):
        return 0.0


def _row_status(row: EventRow) -> str:
    """生成单行状态标签。"""
    if row.get("note"):
        return "🔄 建议更新"
    score = _parse_score(row.get("score", ""))
    if score == 0:
        return "—"
    return "✅ 正常" if score >= 6.5 else "⚠️ 偏低"


def _build_lark_card(rows: list[EventRow], target_date: str) -> dict:
    """
    构建 Lark 互动卡片（Card Kit 2.0）。

    结构：
      Header  —— 标题 + 副标题
      Body    —— table 元素（列：事件名称 / 质量分 / 状态）
              —— markdown 元素（汇总警告 + 共 N 个事件）
    """
    low_quality = [r for r in rows if _parse_score(r.get("score", "")) < 6.5]
    needs_update = [r for r in rows if r.get("note")]

    # ── 表格行 ──
    table_rows = [
        {
            "event_name":     row.get("event", ""),
            "quality_score":  row.get("score", ""),
            "status":         _row_status(row),
        }
        for row in rows
    ]

    # ── 汇总 Markdown ──
    summary_lines = [f"共 **{len(rows)}** 个事件"]
    if low_quality:
        names = "、".join(r["event"] for r in low_quality)
        summary_lines.append(f"⚠️ 低于 6.5/10：{names}")
    if needs_update:
        names = "、".join(r["event"] for r in needs_update)
        summary_lines.append(f"🔄 建议更新：{names}")
    summary_md = "\n".join(summary_lines)

    card = {
        "schema": "2.0",
        "header": {
            "title": {
                "tag": "plain_text",
                "content": "📊 Facebook 事件质量报告",
            },
            "subtitle": {
                "tag": "plain_text",
                "content": f"Kalaroko-internal01 · {target_date}",
            },
            "template": "blue",
        },
        "body": {
            "elements": [
                # 表格
                {
                    "tag": "table",
                    "page_size": 30,
                    "row_height": "low",
                    "header_style": {
                        "background_style": "grey",
                        "bold": True,
                        "text_align": "left",
                    },
                    "columns": [
                        {
                            "name": "event_name",
                            "display_name": "事件名称",
                            "data_type": "text",
                            "width": "auto",
                            "horizontal_align": "left",
                        },
                        {
                            "name": "quality_score",
                            "display_name": "质量分",
                            "data_type": "text",
                            "width": "auto",
                            "horizontal_align": "center",
                        },
                        {
                            "name": "status",
                            "display_name": "状态",
                            "data_type": "text",
                            "width": "auto",
                            "horizontal_align": "left",
                        },
                    ],
                    "rows": table_rows,
                },
                # 汇总区
                {
                    "tag": "markdown",
                    "content": summary_md,
                },
            ]
        },
    }
    return card


def _format_fallback_text(rows: list[EventRow], target_date: str) -> str:
    """纯文本回退（卡片发送失败时使用）。"""
    lines = [
        f"📊 Facebook 事件质量报告 — Kalaroko-internal01",
        f"日期：{target_date}",
        "",
        f"{'事件名称':<32} {'质量分':>8}  {'状态'}",
        "─" * 55,
    ]
    for row in rows:
        lines.append(
            f"{row.get('event',''):<32} {row.get('score',''):>8}  {_row_status(row)}"
        )
    low_quality = [r for r in rows if _parse_score(r.get("score", "")) < 6.5]
    needs_update = [r for r in rows if r.get("note")]
    lines.append("")
    if low_quality:
        lines.append(f"⚠️ 低于 6.5/10：{', '.join(r['event'] for r in low_quality)}")
    if needs_update:
        lines.append(f"🔄 建议更新：{', '.join(r['event'] for r in needs_update)}")
    lines.append(f"\n共 {len(rows)} 个事件")
    return "\n".join(lines)


def _fallback_parse(text: str) -> list[EventRow]:
    """当 JS 策略失败时，用正则从页面全文提取。"""
    import re
    results = []
    # 尝试找到 "EventName ... X.X/10" 的组合
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    score_re = re.compile(r"^(\d+\.?\d*)\/10$")
    for i, line in enumerate(lines):
        if score_re.match(line):
            # 往前搜最近的事件名（非数字、非关键词）
            skip = {"Active", "Inactive", "Server", "Client", "Update recommended"}
            skip_re = re.compile(r"^\d[\d,.]*[KkMm]?$|^Last seen|^\d+ (min|hour|day)")
            for j in range(i - 1, max(i - 10, -1), -1):
                candidate = lines[j]
                if candidate in skip or skip_re.match(candidate):
                    continue
                if 2 < len(candidate) < 60:
                    note = "Update recommended" if i + 1 < len(lines) and "Update" in lines[i + 1] else ""
                    results.append({"event": candidate, "score": line, "note": note})
                    break
    seen = set()
    deduped = []
    for r in results:
        if r["event"] not in seen:
            seen.add(r["event"])
            deduped.append(r)
    return deduped


# ---------------------------------------------------------------------------
# Lark 发送（互动卡片 + 文本回退）
# ---------------------------------------------------------------------------


async def _get_lark_token(client: Any) -> str:
    """获取 tenant_access_token。"""
    headers_json = {"Content-Type": "application/json; charset=utf-8"}
    resp = await client.post(
        f"{LARK_API_BASE}/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": LARK_APP_ID, "app_secret": LARK_APP_SECRET},
        headers=headers_json,
    )
    resp.raise_for_status()
    rj = resp.json()
    if rj.get("code") != 0:
        raise RuntimeError(f"获取 Token 失败：{rj}")
    return rj["tenant_access_token"]


async def send_card_to_lark(rows: list[EventRow], target_date: str) -> None:
    """
    向所有收件人发送 Lark 互动卡片（Card Kit 2.0，含表格）。
    若卡片发送失败（API code 非 0），自动回退为纯文本消息。
    """
    try:
        import httpx
    except ImportError:
        print("[Lark] httpx 未安装（pip install httpx），跳过发送。", file=sys.stderr)
        return

    receivers = _lark_receivers()
    if not receivers:
        print("[Lark] 未配置收件人，跳过。", file=sys.stderr)
        return
    if not LARK_APP_ID or not LARK_APP_SECRET:
        print("[Lark] 未配置 LARK_APP_ID / LARK_APP_SECRET，跳过。", file=sys.stderr)
        return

    headers_json = {"Content-Type": "application/json; charset=utf-8"}
    card = _build_lark_card(rows, target_date)
    fallback_text = _format_fallback_text(rows, target_date)

    async with httpx.AsyncClient(timeout=60.0) as client:
        token = await _get_lark_token(client)
        auth = {"Authorization": f"Bearer {token}"}
        msg_url = f"{LARK_API_BASE}/open-apis/im/v1/messages"

        for rid in receivers:
            # 优先尝试互动卡片
            card_content = json.dumps(card, ensure_ascii=False)
            body = {
                "receive_id": rid,
                "msg_type": "interactive",
                "content": card_content,
            }
            resp = await client.post(
                msg_url,
                params={"receive_id_type": LARK_RECEIVE_ID_TYPE},
                headers={**auth, **headers_json},
                json=body,
            )
            resp.raise_for_status()
            rj = resp.json()

            if rj.get("code") == 0:
                print(f"[Lark] ✅ 卡片已发送至 {rid}")
                continue

            # 卡片失败 → 回退文本
            print(
                f"[Lark] 卡片发送失败（{rid}，code={rj.get('code')}，msg={rj.get('msg')}），"
                "回退为文本消息...",
                file=sys.stderr,
            )
            text_body = {
                "receive_id": rid,
                "msg_type": "text",
                "content": json.dumps({"text": fallback_text}, ensure_ascii=False),
            }
            text_resp = await client.post(
                msg_url,
                params={"receive_id_type": LARK_RECEIVE_ID_TYPE},
                headers={**auth, **headers_json},
                json=text_body,
            )
            text_resp.raise_for_status()
            trj = text_resp.json()
            if trj.get("code") == 0:
                print(f"[Lark] ✅ 文本已发送至 {rid}")
            else:
                print(f"[Lark] ❌ 文本发送也失败（{rid}）：{trj}", file=sys.stderr)


# 兼容旧调用（fb_report_scheduler 等调用 _send_lark_text_message）
async def _send_lark_text_message(text: str) -> None:
    """兼容接口：以纯文本发送告警消息。"""
    try:
        import httpx
    except ImportError:
        return
    receivers = _lark_receivers()
    if not receivers or not LARK_APP_ID or not LARK_APP_SECRET:
        return
    headers_json = {"Content-Type": "application/json; charset=utf-8"}
    async with httpx.AsyncClient(timeout=60.0) as client:
        token = await _get_lark_token(client)
        auth = {"Authorization": f"Bearer {token}"}
        for rid in receivers:
            await client.post(
                f"{LARK_API_BASE}/open-apis/im/v1/messages",
                params={"receive_id_type": LARK_RECEIVE_ID_TYPE},
                headers={**auth, **headers_json},
                json={
                    "receive_id": rid,
                    "msg_type": "text",
                    "content": json.dumps({"text": text}, ensure_ascii=False),
                },
            )


# ---------------------------------------------------------------------------
# 主抓取流程
# ---------------------------------------------------------------------------


async def scrape_events_quality() -> list[EventRow]:
    from playwright.async_api import async_playwright
    from playwright.async_api import Browser
    from playwright.async_api import Error as PlaywrightError
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    async with async_playwright() as p:
        browser: Optional[Browser] = None

        # 连接 Chrome
        for attempt in range(1, 4):
            try:
                wss = await asyncio.get_running_loop().run_in_executor(
                    None, _rewrite_wss_endpoint, CDP_URL
                )
                print(f"[CDP] [{attempt}/3] WSS: {wss}", file=sys.stderr)
                browser = await p.chromium.connect_over_cdp(
                    wss, headers=NGROK_HEADERS, timeout=CONNECT_TIMEOUT_MS
                )
                break
            except Exception as e:
                print(f"[CDP] 连接失败（{attempt}/3）：{e!r}", file=sys.stderr)
                if attempt >= 3:
                    _triage(e)
                    return []
                await asyncio.sleep(3)

        if browser is None:
            return []

        try:
            # 选取可用页面（优先找已打开的 Events Manager 标签）
            page = None
            for ctx in browser.contexts:
                for pg in ctx.pages:
                    if "eventsmanager" in (pg.url or ""):
                        page = pg
                        print(f"[CDP] 复用已有页面：{pg.url}", file=sys.stderr)
                        break
                if page:
                    break

            if page is None:
                # 没有现成页面，新建
                ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = await ctx.new_page()
                print(f"[CDP] 新建页面，导航至 Events Manager...", file=sys.stderr)

            # 导航到目标 URL
            print(f"[nav] goto {TARGET_URL[:80]}...", file=sys.stderr)
            await page.goto(
                TARGET_URL,
                wait_until="domcontentloaded",
                timeout=GOTO_TIMEOUT_MS,
            )

            # 等待 SPA 渲染（React 需要额外时间渲染表格）
            print(f"[wait] 等待 SPA 渲染 {WAIT_AFTER_LOAD_SEC}s ...", file=sys.stderr)
            await asyncio.sleep(WAIT_AFTER_LOAD_SEC)

            # 尝试等待质量分数出现
            try:
                await page.wait_for_selector(
                    "text=/\\d+\\.?\\d*\\/10/",
                    timeout=30_000,
                )
                print("[wait] 质量分已出现。", file=sys.stderr)
            except PlaywrightTimeoutError:
                print("[wait] 等待质量分超时，继续尝试提取...", file=sys.stderr)

            # 调试截图
            if DEBUG_SCREENSHOT:
                shot_path = _SCRIPT_DIR / f"debug_events_quality_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                try:
                    await page.screenshot(path=str(shot_path), full_page=True, timeout=15_000)
                    print(f"[debug] 截图已保存：{shot_path}", file=sys.stderr)
                except Exception as e:
                    print(f"[debug] 截图失败（忽略）：{e!r}", file=sys.stderr)

            # 注入 JS 提取数据
            print("[js] 注入提取脚本...", file=sys.stderr)
            raw: Any = await page.evaluate(_EXTRACT_JS)

            rows: list[EventRow] = []
            if isinstance(raw, dict):
                if "results" in raw and raw["results"]:
                    rows = raw["results"]
                    print(f"[js] 策略1成功，提取到 {len(rows)} 个事件。", file=sys.stderr)
                elif "fallback_text" in raw:
                    print("[js] 策略1无结果，尝试文本回退解析...", file=sys.stderr)
                    fallback_text = raw["fallback_text"]
                    rows = _fallback_parse(fallback_text)
                    print(f"[fallback] 回退解析得到 {len(rows)} 个事件。", file=sys.stderr)
                    if not rows:
                        # 打印前 2000 字符帮助调试
                        print("[fallback] 页面文本前 2000 字：\n" + fallback_text[:2000], file=sys.stderr)

            return rows

        finally:
            try:
                await browser.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


async def main(dry_run: bool = False) -> int:
    today_str = date.today().isoformat()
    print(f"[main] 开始抓取事件质量数据 | CDP: {CDP_URL}", file=sys.stderr)
    print(f"[main] 目标 URL: {TARGET_URL}", file=sys.stderr)

    rows = await scrape_events_quality()

    if not rows:
        print("❌ 未能提取到任何事件数据，请开启 FB_EVENTS_DEBUG_SCREENSHOT=1 查看页面截图。",
              file=sys.stderr)
        return 1

    # 打印到控制台
    print(f"\n{'事件名称':<30} {'质量分':>8}  {'备注'}")
    print("─" * 55)
    for row in rows:
        flag = " ⚠️  Update recommended" if row.get("note") else ""
        print(f"{row['event']:<30} {row['score']:>8}{flag}")
    print(f"\n共 {len(rows)} 个事件\n")

    # 发送 Lark（互动卡片表格，失败自动回退文本）
    if not dry_run:
        print("[lark] 准备发送互动卡片...", file=sys.stderr)
        await send_card_to_lark(rows, today_str)
    else:
        print("[dry-run] 跳过 Lark 发送。")
        print("\n--- 卡片预览（JSON）---")
        print(json.dumps(_build_lark_card(rows, today_str), ensure_ascii=False, indent=2))

    return 0


def _cli() -> None:
    parser = argparse.ArgumentParser(description="抓取 Facebook 事件质量分并推送 Lark")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="仅打印结果，不发送 Lark 消息（用于本地调试）"
    )
    parser.add_argument(
        "--screenshot", action="store_true",
        help="保存调试截图（等同于 FB_EVENTS_DEBUG_SCREENSHOT=1）"
    )
    args = parser.parse_args()

    if args.screenshot:
        os.environ["FB_EVENTS_DEBUG_SCREENSHOT"] = "1"
        global DEBUG_SCREENSHOT
        DEBUG_SCREENSHOT = True

    rc = asyncio.run(main(dry_run=args.dry_run))
    raise SystemExit(rc)


if __name__ == "__main__":
    _cli()
