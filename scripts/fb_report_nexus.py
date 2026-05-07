#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通过 CDP（经 Ngrok）附加远端 Chrome，在 Facebook 广告管理后台导出 Campaign 表为 CSV。

依赖：
  pip install playwright httpx
  （connect_over_cdp 仅需 Python 绑定；通常无需 playwright install chromium）

用法：
  python scripts/fb_report_nexus.py

可选环境变量：
  FB_REPORT_CDP_URL — 覆盖默认 Ngrok CDP HTTP 端点
  FB_REPORT_TARGET_URL — 覆盖默认 Ads Manager URL
  LARK_* — Lark 海外（详见 scripts/fb_report_nexus.env.example；API 根域见代码内 LARK_API_BASE）

  FB_REPORT_GOTO_TIMEOUT_MS — Ads Manager 首次 goto 超时（毫秒），默认 300000

连接说明：
  Chrome 经 Ngrok（含 host-header=localhost）时，``webSocketDebuggerUrl`` 常指向 ``ws://localhost/...``，
  客户端会误连本机。脚本会先 GET ``/json/version``，再按 Ngrok 公网域名拼接路径得到 ``wss://.../devtools/...``。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import traceback
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from playwright.async_api import Browser
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

# 与脚本同目录的本地配置（不提交仓库；见 fb_report_nexus.env.example）
_SCRIPT_DIR = Path(__file__).resolve().parent
_LARK_ENV_PATH = _SCRIPT_DIR / "fb_report_nexus.env"


def _load_fb_report_env_file() -> None:
    """将 fb_report_nexus.env 注入 os.environ（不覆盖已存在的环境变量）。"""
    if not _LARK_ENV_PATH.is_file():
        return
    try:
        text = _LARK_ENV_PATH.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, val)


_load_fb_report_env_file()

# ---------------------------------------------------------------------------
# 常量（可用环境变量覆盖）
# ---------------------------------------------------------------------------

DEFAULT_CDP_URL = "https://nestor-gravelish-alix.ngrok-free.dev"
DEFAULT_TARGET_URL = (
    "https://adsmanager.facebook.com/adsmanager/manage/campaigns?"
    "act=2117441032349622&business_id=1423598819502157&"
    "nav_entry_point=ads_ecosystem_navigation_menu&"
    "columns=name%2Cdelivery%2Crecommendations_guidance%2Cresults%2Ccost_per_result%2Cbudget%2Cspend%2Cimpressions%2Creach%2Cfrequency%2Ccpm%2Cactions%3Aomni_purchase%2Cschedule%2Cend_time%2Cattribution_setting%2Cbid%2Clast_significant_edit%2Cquality_score_organic%2Cquality_score_ectr%2Cquality_score_ecvr%2Ccampaign_name%2Cpurchase_roas%3Aomni_purchase&"
    "attribution_windows=default&nav_source=ads_manager"
)

NGROK_HEADERS = {"ngrok-skip-browser-warning": "1"}

# 默认超时 90s（跨境公网 / 大表渲染）
DEFAULT_TIMEOUT_MS = 90_000
CONNECT_TIMEOUT_MS = 90_000
CONNECT_HTTP_TIMEOUT_SEC = 90
# Ads Manager：跨国 + 重页面；domcontentloaded + 长超时；可用 FB_REPORT_GOTO_TIMEOUT_MS 覆盖
FB_ADS_GOTO_TIMEOUT_MS_DEFAULT = 300_000


def _env_url(key: str, default: str) -> str:
    v = os.environ.get(key, "").strip()
    return v if v else default


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


CDP_URL = _env_url("FB_REPORT_CDP_URL", DEFAULT_CDP_URL)
TARGET_URL = _env_url("FB_REPORT_TARGET_URL", DEFAULT_TARGET_URL)
FB_ADS_GOTO_TIMEOUT_MS = _env_int(
    "FB_REPORT_GOTO_TIMEOUT_MS", FB_ADS_GOTO_TIMEOUT_MS_DEFAULT
)

# ---------------------------------------------------------------------------
# Lark 机器人（海外 open.larksuite.com；优先环境变量；否则读取 scripts/fb_report_nexus.env）
# ---------------------------------------------------------------------------

LARK_APP_ID = (os.environ.get("LARK_APP_ID") or "").strip()
LARK_APP_SECRET = (os.environ.get("LARK_APP_SECRET") or "").strip()
# 默认使用群会话 chat_id；可通过环境变量或 fb_report_nexus.env 覆盖
LARK_RECEIVE_ID_TYPE = (os.environ.get("LARK_RECEIVE_ID_TYPE") or "chat_id").strip()
LARK_RECEIVER_ID = (
    os.environ.get("LARK_RECEIVER_ID") or "oc_5741b50fec626633a76f98e0510f8cab"
).strip()
LARK_API_BASE = "https://open.larksuite.com"


def _lark_receiver_ids() -> list[str]:
    """支持多人：LARK_RECEIVER_IDS 为英文逗号（或分号）分隔；否则使用单一 LARK_RECEIVER_ID。"""
    raw = (os.environ.get("LARK_RECEIVER_IDS") or "").strip()
    if raw:
        parts = raw.replace(";", ",").split(",")
        return [p.strip() for p in parts if p.strip()]
    return [LARK_RECEIVER_ID] if LARK_RECEIVER_ID else []


def _rewrite_wss_endpoint_from_json_version(public_http_base: str) -> str:
    """
    读取 Chrome /json/version，将 webSocketDebuggerUrl 的路径（及 query）拼到 Ngrok 的 WSS 源上，
    避免 Playwright 按 ws://localhost 连到本地。
    """
    base = public_http_base.strip().rstrip("/")
    pub = urlparse(base)
    if not pub.scheme or not pub.netloc:
        raise ValueError(f"无效的 CDP 根 URL：{public_http_base!r}")

    headers = {**NGROK_HEADERS, "Accept": "application/json"}
    data: Optional[dict] = None
    last_exc: Optional[BaseException] = None
    for path in ("/json/version", "/json/version/"):
        url = base + path
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=CONNECT_HTTP_TIMEOUT_SEC) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            break
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:4000]
            last_exc = RuntimeError(
                f"HTTP {e.code} GET {url}\n响应片段：{body}"
            )
            continue
        except Exception as e:
            last_exc = e
            continue

    if not data:
        raise RuntimeError(
            "无法通过 urllib 拉取 /json/version（已尝试带 ngrok-skip-browser-warning）。"
        ) from last_exc

    raw_ws = data.get("webSocketDebuggerUrl")
    if not raw_ws or not isinstance(raw_ws, str):
        raise RuntimeError(f"/json/version 缺少 webSocketDebuggerUrl：{data!r}")

    w = urlparse(raw_ws)
    if not w.path:
        raise RuntimeError(f"webSocketDebuggerUrl 缺少路径：{raw_ws!r}")
    suffix = w.path
    if w.query:
        suffix = f"{suffix}?{w.query}"

    scheme = "wss" if pub.scheme == "https" else "ws"
    return f"{scheme}://{pub.netloc}{suffix}"


def _print_wss_resolve_triage(exc: BaseException) -> None:
    print(
        "\n[分诊] 拉取 /json/version 或解析 webSocketDebuggerUrl 失败：\n"
        f"  {exc!r}\n"
        "建议：确认 Ngrok 在线；curl -sS -H 'ngrok-skip-browser-warning: 1' "
        f"'{CDP_URL.rstrip('/')}/json/version'\n",
        file=sys.stderr,
    )
    traceback.print_exc(file=sys.stderr)


def _print_connection_triage(exc: BaseException) -> None:
    print(
        "\n[分诊] CDP 连接在 "
        f"{CONNECT_TIMEOUT_MS // 1000} 秒内失败：{exc!r}\n"
        "建议逐项排查：\n"
        "  1) Ngrok：控制台是否在线、该隧道 URL 是否与脚本一致、免费域名是否过期。\n"
        "  2) 物理机：Chrome/Chromium 是否以 --remote-debugging-port 启动且未被防火墙拦截。\n"
        "  3) 本机：能否访问 "
        f"{CDP_URL.rstrip('/')}/json/version（需带 Header ngrok-skip-browser-warning: 1）。\n"
        "  4) Playwright 版本是否与远端 Chrome 主版本相差过大（必要时升级 Playwright）。\n",
        file=sys.stderr,
    )
    traceback.print_exc(file=sys.stderr)


async def upload_to_lark(file_path: str) -> None:
    """获取 tenant_access_token → 上传 im 文件 → 向所有配置收件人发送私聊文件消息。"""
    p = Path(file_path).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"[飞书] 本地文件不存在：{p}")

    receivers = _lark_receiver_ids()
    if not receivers:
        raise ValueError(
            "[飞书] 未配置收件人：请在 scripts/fb_report_nexus.env 中设置 "
            "LARK_RECEIVER_IDS（多人用英文逗号分隔），或设置环境变量同名项。"
        )
    if not LARK_APP_ID or not LARK_APP_SECRET:
        raise ValueError(
            "[飞书] 未配置 LARK_APP_ID / LARK_APP_SECRET：请编辑 scripts/fb_report_nexus.env "
            "或注入环境变量。"
        )

    print(f"[飞书] 作战开始，准备投递：{p}")
    print(f"[飞书] 收件人 ({len(receivers)} 人): {', '.join(receivers)}")

    headers_json = {"Content-Type": "application/json; charset=utf-8"}

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            # 1) tenant_access_token
            tok_resp = await client.post(
                f"{LARK_API_BASE}/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": LARK_APP_ID, "app_secret": LARK_APP_SECRET},
                headers=headers_json,
            )
            tok_resp.raise_for_status()
            tok_json = tok_resp.json()
            if tok_json.get("code") != 0:
                raise RuntimeError(
                    f"获取 tenant_access_token 失败：{tok_json}"
                )
            token = tok_json["tenant_access_token"]
            print("[飞书] 获取 Token 成功")

            auth = {"Authorization": f"Bearer {token}"}

            # 2) 上传文件（multipart，一次上传多次投递）
            file_bytes = p.read_bytes()
            upload_url = f"{LARK_API_BASE}/open-apis/im/v1/files"
            up_resp = await client.post(
                upload_url,
                headers=auth,
                data={
                    "file_type": "stream",
                    "file_name": p.name,
                },
                files={
                    "file": (
                        p.name,
                        file_bytes,
                        "text/csv",
                    )
                },
            )
            up_resp.raise_for_status()
            up_json = up_resp.json()
            if up_json.get("code") != 0:
                raise RuntimeError(f"上传文件失败：{up_json}")
            file_key = (up_json.get("data") or {}).get("file_key")
            if not file_key:
                raise RuntimeError(f"响应缺少 file_key：{up_json}")
            print(f"[飞书] 文件上传成功，file_key: {file_key}")

            # 3) 逐人发送（同一 file_key 复用）
            msg_url = f"{LARK_API_BASE}/open-apis/im/v1/messages"
            file_payload = json.dumps(
                {"file_key": file_key},
                ensure_ascii=False,
            )
            for rid in receivers:
                body = {
                    "receive_id": rid,
                    "msg_type": "file",
                    "content": file_payload,
                }
                msg_resp = await client.post(
                    msg_url,
                    headers={**auth, **headers_json},
                    params={"receive_id_type": LARK_RECEIVE_ID_TYPE},
                    json=body,
                )
                msg_resp.raise_for_status()
                msg_json = msg_resp.json()
                if msg_json.get("code") != 0:
                    raise RuntimeError(
                        f"向 {rid} 发送消息失败：{msg_json}"
                    )
                print(f"[飞书] 已向 {rid} 投递成功")

            print("[飞书] 飞书发送全部完成！")

    except httpx.HTTPStatusError as e:
        txt = ""
        try:
            txt = e.response.text[:800]
        except Exception:
            pass
        print(
            f"[飞书] HTTP 状态异常 {e.response.status_code}：{txt}",
            file=sys.stderr,
        )
        raise
    except httpx.RequestError as e:
        print(f"[飞书] 网络请求失败：{e!r}", file=sys.stderr)
        raise
    except Exception as e:
        print(f"[飞书] 作战失败：{e!r}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        raise


async def _pick_working_page(browser: Browser) -> Page:
    """在已连接的 Browser 上选取可用 Page（优先已有前台页）。"""
    for ctx in browser.contexts:
        for pg in ctx.pages:
            if not pg.is_closed():
                return pg
    if browser.contexts:
        return await browser.contexts[0].new_page()
    ctx = await browser.new_context()
    return await ctx.new_page()


async def _export_campaign_csv(page: Page) -> Path:
    """在已打开 Ads Manager 活动页的 Page 上执行导出并保存 CSV。"""
    page.set_default_timeout(DEFAULT_TIMEOUT_MS)
    page.set_default_navigation_timeout(FB_ADS_GOTO_TIMEOUT_MS)

    nav_attempts = 3
    for nav_i in range(nav_attempts):
        try:
            print(
                "🚀 正在发起跨洋导航，目标：Ads Manager "
                "(放宽等待至 DOMContentLoaded)..."
            )
            await page.goto(
                TARGET_URL,
                wait_until="domcontentloaded",
                timeout=FB_ADS_GOTO_TIMEOUT_MS,
            )
            print(
                "⏳ 基础框架已就位，正在等待 FB 渲染内部组件 (10s 预热)..."
            )
            await asyncio.sleep(10)
            break
        except Exception as e:
            print(
                f"⚠️ 导航超时或网络波动（第 {nav_i + 1}/{nav_attempts} 次）：{e!r}"
            )
            print("⚠️ 尝试拍摄现场快照 (debug_nav_timeout.png)...")
            try:
                await page.screenshot(path="debug_nav_timeout.png")
            except Exception:
                pass
            if nav_i == nav_attempts - 1:
                raise
            await asyncio.sleep(5)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = Path.cwd() / f"fb_campaign_report_{ts}.csv"

    print("📸 拍摄战地快照 (debug_before_click.png)...")
    await page.screenshot(path="debug_before_click.png", full_page=True)

    print("🎯 执行一击必杀（仅截获 URL，不依赖物理硬盘）...")
    hacker_page: Optional[Page] = None
    try:
        async with page.expect_download(timeout=60_000) as download_info:
            export_btn = page.get_by_role(
                "button", name=re.compile(r"export|download", re.IGNORECASE)
            ).first
            await export_btn.wait_for(state="visible", timeout=30_000)
            # force=True：穿透引导遮罩等 pointer-events 拦截，强行派发点击
            await export_btn.click(force=True)

        download = await download_info.value
        target_url = download.url
        print(f"🔗 成功截获数据节点 URL: {target_url[:80]}...")

        print("🕵️ 开启幽灵标签页，准备绕过 GFW 和跨域限制...")
        hacker_page = await page.context.new_page()

        root = urlparse(target_url)
        origin = f"{root.scheme}://{root.netloc}/"
        await hacker_page.goto(origin, wait_until="commit", timeout=30_000)

        print("💉 正在同源无干扰环境下执行内存抽取...")
        csv_content = await hacker_page.evaluate(
            """async (url) => {
                const res = await window.fetch(url, { credentials: 'include' });
                if (!res.ok) throw new Error('HTTP Status ' + res.status);
                return await res.text();
            }""",
            target_url,
        )

        await hacker_page.close()
        hacker_page = None

        print("📥 数据流已穿透海底光缆抵达，正在本地重组...")
        with open(dest, "w", encoding="utf-8-sig") as f:
            f.write(csv_content)

        file_size = os.path.getsize(dest)
        if file_size > 0:
            print(
                f"🎉 终极突围！幽灵行动大获全胜，数据已成功拉回: {dest} ({file_size} bytes)"
            )
        else:
            raise RuntimeError("❌ 拦截到的数据包为空！")

    except Exception:
        print("❌ 幽灵行动崩溃...", file=sys.stderr)
        await page.screenshot(path="debug_download_failed.png", full_page=True)
        if hacker_page is not None:
            try:
                await hacker_page.close()
            except Exception:
                pass
        raise

    return dest


async def main() -> int:
    try:
        wss_endpoint = await asyncio.get_running_loop().run_in_executor(
            None, _rewrite_wss_endpoint_from_json_version, CDP_URL
        )
    except Exception as e:  # noqa: BLE001
        _print_wss_resolve_triage(e)
        return 2

    print(
        f"🔗 路由篡改成功，准备连接 WSS 通道: {wss_endpoint}",
        file=sys.stderr,
    )

    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(
                wss_endpoint,
                headers=NGROK_HEADERS,
                timeout=CONNECT_TIMEOUT_MS,
            )
        except (PlaywrightError, PlaywrightTimeoutError, asyncio.TimeoutError, OSError) as e:
            _print_connection_triage(e)
            return 2
        except Exception as e:  # noqa: BLE001 — 连接阶段兜底分诊
            _print_connection_triage(e)
            return 2

        try:
            page = await _pick_working_page(browser)
            out_path = await _export_campaign_csv(page)
            print(f"已保存：{out_path.resolve()}")
            await upload_to_lark(str(out_path.resolve()))
        finally:
            try:
                await browser.close()
            except Exception:
                pass

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
