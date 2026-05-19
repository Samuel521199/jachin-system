#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通过 CDP（经 Ngrok）附加远端 Chrome，在 Facebook 广告管理后台 **广告（Ads）** 层级导出表格为 CSV。

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
  FB_REPORT_DISPATCH_TIMEOUT_SEC — 导出点击链总预算（秒），默认 240（Ngrok/CDP 慢时勿过小；过短会导致兜底未执行就报预算用尽）
  FB_REPORT_EVALUATE_TIMEOUT_SEC — 单段 page.evaluate 上限（秒），默认 25
  FB_REPORT_DEBUG_SCREENSHOT_MS — 调试截图超时（毫秒），默认 12000（Ngrok CDP 截屏易超时，与是否「封面图」无关）
  FB_REPORT_SKIP_DEBUG_SCREENSHOT — 设为 1/true 时跳过调试截图（加快失败重试）
  FB_REPORT_DEBUG_MIN_REMAINING_SEC — 剩余点击链预算低于该秒数时跳过全息快照（优先留给兜底点击），默认 50
  FB_REPORT_SKIP_HOLOGRAM — 设为 1/true 时始终跳过全息快照基因序列
  FB_REPORT_FALLBACK_LOCATOR_WAIT_MS — 视觉兜底单次 locator 等待（毫秒），默认 20000
  FB_REPORT_EXPORT_FOLLOWUP_SEC — 导出菜单二次点击搜索时长（秒），默认 18（首次点击常只开菜单）
  FB_REPORT_PRESET — 报表区间：相对暗号或绝对单日 ``YYYY-MM-DD``（URL 中为 ``该日_次日``，无逗号后缀）；写入 ``date`` / ``insights_date``

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

# Windows GBK 终端兼容：强制 stdout/stderr 使用 UTF-8，避免 emoji 导致 UnicodeEncodeError
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlencode, urlparse

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
    "https://adsmanager.facebook.com/adsmanager/manage/ads?"
    "act=2117441032349622&business_id=1423598819502157&"
    "nav_entry_point=ads_ecosystem_navigation_menu&"
    "columns=name%2Cdelivery%2Crecommendations_guidance%2Cresults%2Ccost_per_result%2Cbudget%2Cspend%2Cimpressions%2Creach%2Cfrequency%2Ccpm%2Cactions%3Aomni_purchase%2Cschedule%2Cend_time%2Cattribution_setting%2Cbid%2Clast_significant_edit%2Cquality_score_organic%2Cquality_score_ectr%2Cquality_score_ecvr%2Ccampaign_name%2Cpurchase_roas%3Aomni_purchase&"
    "attribution_windows=default&"
    "column_preset=122134082511140527&"
    "comparison_date=&insights_comparison_date=&"
    "nav_source=ads_manager"
)

NGROK_HEADERS = {"ngrok-skip-browser-warning": "1"}

# 默认超时 90s（跨境公网 / 大表渲染）
DEFAULT_TIMEOUT_MS = 90_000
CONNECT_TIMEOUT_MS = 90_000
CONNECT_HTTP_TIMEOUT_SEC = 90
# Ads Manager：跨国 + 重页面；domcontentloaded + 长超时；可用 FB_REPORT_GOTO_TIMEOUT_MS 覆盖
FB_ADS_GOTO_TIMEOUT_MS_DEFAULT = 300_000

# FB_REPORT_PRESET（解析后暗号）→ URL 中 date / insights_date 的区间与 preset 字段
DEFAULT_FB_REPORT_PRESET = "last_7d"

_FB_NATIVE_DATE_PRESETS: frozenset[str] = frozenset(
    {
        "today",
        "yesterday",
        "last_7d",
        "last_14d",
        "last_30d",
        "this_month",
        "last_month",
        "maximum",
    }
)

# 直观配置 / 简写 -> FB 原生 date_preset
FB_REPORT_DATE_PRESET_ALIASES: dict[str, str] = {
    "today": "today",
    "yesterday": "yesterday",
    "last_7d": "last_7d",
    "last7d": "last_7d",
    "7d": "last_7d",
    "last_14d": "last_14d",
    "last14d": "last_14d",
    "14d": "last_14d",
    "last_30d": "last_30d",
    "last30d": "last_30d",
    "30d": "last_30d",
    "this_month": "this_month",
    "thismonth": "this_month",
    "last_month": "last_month",
    "lastmonth": "last_month",
    "maximum": "maximum",
    "max": "maximum",
}


def build_fb_deep_link(base_url: str, preset: str = "last_7d") -> str:
    """
    基于 Ads Manager 路由：覆写 ``date``、``insights_date``。

    协议对齐：
    - **绝对日期**（``YYYY-MM-DD``）：``起始日_结束日``，结束日为起始日的次日；**无任何逗号或 preset 后缀**。
    - **相对预设**：``起始_结束,preset``（如 ``,yesterday``、``,last_7d``）。
    """
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", preset):
        target_date = datetime.strptime(preset, "%Y-%m-%d").date()
        next_day = target_date + timedelta(days=1)
        date_param = (
            f"{target_date.strftime('%Y-%m-%d')}_{next_day.strftime('%Y-%m-%d')}"
        )
    else:
        today = datetime.now().date()

        if preset == "yesterday":
            start_d = today - timedelta(days=1)
            end_d = today
            out_preset = "yesterday"
        elif preset == "today":
            start_d = today
            end_d = today + timedelta(days=1)
            out_preset = "today"
        elif preset == "last_30d":
            start_d = today - timedelta(days=31)
            end_d = today - timedelta(days=1)
            out_preset = "last_30d"
        elif preset == "last_14d":
            start_d = today - timedelta(days=15)
            end_d = today - timedelta(days=1)
            out_preset = "last_14d"
        elif preset == "last_7d":
            start_d = today - timedelta(days=8)
            end_d = today - timedelta(days=1)
            out_preset = "last_7d"
        elif preset == "this_month":
            start_d = today.replace(day=1)
            end_d = today
            out_preset = "this_month"
        elif preset == "last_month":
            first_this = today.replace(day=1)
            last_prev = first_this - timedelta(days=1)
            start_d = last_prev.replace(day=1)
            end_d = last_prev
            out_preset = "last_month"
        elif preset == "maximum":
            start_d = today - timedelta(days=365 * 3)
            end_d = today
            out_preset = "maximum"
        else:
            start_d = today - timedelta(days=8)
            end_d = today - timedelta(days=1)
            out_preset = "last_7d"

        start = start_d.strftime("%Y-%m-%d")
        end = end_d.strftime("%Y-%m-%d")
        date_param = f"{start}_{end},{out_preset}"

    u = urlparse(base_url)
    query = parse_qs(u.query, keep_blank_values=True)
    for k in ("time_range", "date_preset", "date", "insights_date",
              "comparison_date", "insights_comparison_date"):
        query.pop(k, None)
    query["date"] = [date_param]
    query["insights_date"] = [date_param]
    # 对比区间始终置空（单日/单区间抓取，不需要同比）
    query["comparison_date"] = [""]
    query["insights_comparison_date"] = [""]
    new_query = urlencode(query, doseq=True)
    return u._replace(query=new_query).geturl()


def resolve_fb_report_preset(raw: str) -> str:
    """将环境变量或别名解析为 FB 区间暗号；未知值回退 DEFAULT_FB_REPORT_PRESET。
    ``YYYY-MM-DD`` 原样返回，供 ``build_fb_deep_link`` 作绝对单日（无后缀，结束日为次日）。
    """
    s = (raw or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    key = s.lower().replace("-", "_")
    if not key:
        return DEFAULT_FB_REPORT_PRESET
    if key in FB_REPORT_DATE_PRESET_ALIASES:
        return FB_REPORT_DATE_PRESET_ALIASES[key]
    if key in _FB_NATIVE_DATE_PRESETS:
        return key
    return DEFAULT_FB_REPORT_PRESET


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


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


CDP_URL = _env_url("FB_REPORT_CDP_URL", DEFAULT_CDP_URL)
FB_REPORT_PRESET = resolve_fb_report_preset((os.environ.get("FB_REPORT_PRESET") or "").strip())
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


def _is_local_cdp(url: str) -> bool:
    """判断 CDP URL 是否为本机直连（localhost / 127.x.x.x / ::1）。"""
    host = urlparse(url).hostname or ""
    return host in ("localhost", "127.0.0.1", "::1") or host.startswith("127.")


def _print_wss_resolve_triage(exc: BaseException) -> None:
    if _is_local_cdp(CDP_URL):
        print(
            "\n[分诊] 拉取本机 /json/version 失败：\n"
            f"  {exc!r}\n"
            f"建议：确认 Chrome 已以 --remote-debugging-port={urlparse(CDP_URL).port or 9223} 启动；\n"
            f"  curl -sS '{CDP_URL.rstrip('/')}/json/version'\n",
            file=sys.stderr,
        )
    else:
        print(
            "\n[分诊] 拉取 /json/version 或解析 webSocketDebuggerUrl 失败：\n"
            f"  {exc!r}\n"
            "建议：确认 Ngrok 在线；\n"
            f"  curl -sS -H 'ngrok-skip-browser-warning: 1' '{CDP_URL.rstrip('/')}/json/version'\n",
            file=sys.stderr,
        )
    traceback.print_exc(file=sys.stderr)


def _print_connection_triage(exc: BaseException) -> None:
    local = _is_local_cdp(CDP_URL)
    port = urlparse(CDP_URL).port or 9223
    print(
        "\n[分诊] CDP 连接在 "
        f"{CONNECT_TIMEOUT_MS // 1000} 秒内失败：{exc!r}\n"
        "建议逐项排查：\n",
        file=sys.stderr,
    )
    if local:
        print(
            f"  1) 本机 Chrome：是否以 --remote-debugging-port={port} 启动？\n"
            f"     启动示例：chrome.exe --remote-debugging-port={port} --user-data-dir=\"C:\\ChromeProfile_FB\"\n"
            f"  2) 防火墙 / 杀毒：是否拦截了 127.0.0.1:{port}？\n"
            f"  3) 验证：curl -sS http://127.0.0.1:{port}/json/version\n"
            "  4) Playwright 版本是否与本机 Chrome 主版本相差过大（必要时升级 Playwright）。\n",
            file=sys.stderr,
        )
    else:
        print(
            "  1) Ngrok：控制台是否在线、该隧道 URL 是否与脚本一致、免费域名是否过期。\n"
            "  2) 物理机：Chrome/Chromium 是否以 --remote-debugging-port 启动且未被防火墙拦截。\n"
            f"  3) 验证：curl -sS -H 'ngrok-skip-browser-warning: 1' {CDP_URL.rstrip('/')}/json/version\n"
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
    # 跨境网络不稳定时的重试参数
    _LARK_MAX_RETRIES = 4
    _LARK_RETRY_DELAYS = [5, 15, 30, 60]  # 每次重试等待秒数

    last_exc: Optional[BaseException] = None
    for attempt in range(1, _LARK_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                # 1) tenant_access_token（每次重试重新取，避免 token 过期）
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
                if attempt == 1:
                    print("[飞书] 获取 Token 成功")
                else:
                    print(f"[飞书] 重试 {attempt}/{_LARK_MAX_RETRIES}：获取 Token 成功")

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
            return  # 成功，退出重试循环

        except Exception as exc:
            last_exc = exc
            if attempt < _LARK_MAX_RETRIES:
                wait = _LARK_RETRY_DELAYS[attempt - 1]
                print(
                    f"[飞书] 网络请求失败（{attempt}/{_LARK_MAX_RETRIES}）：{exc!r}，"
                    f"{wait}s 后重试...",
                    file=sys.stderr,
                )
                await asyncio.sleep(wait)
            else:
                print(
                    f"[飞书] 已重试 {_LARK_MAX_RETRIES} 次仍失败，放弃发送。最终错误：{exc!r}",
                    file=sys.stderr,
                )

    if last_exc is not None:
        raise last_exc


async def _debug_screenshot(page: Page, path: str) -> None:
    """尽力生成调试截图；Ngrok/CDP 慢时常超时——默认 12s 快速失败（可调 FB_REPORT_DEBUG_SCREENSHOT_MS）；不设封面图也会超时。"""
    if _truthy_env("FB_REPORT_SKIP_DEBUG_SCREENSHOT"):
        return
    ms = _env_int("FB_REPORT_DEBUG_SCREENSHOT_MS", 12_000)
    ms = max(1_000, ms)
    try:
        await page.screenshot(
            path=path,
            timeout=ms,
            full_page=False,
            animations="disabled",
        )
    except Exception as e:
        print(
            f"⚠️ [熔断] 调试截图 ({path}) 超时或失败，已跳过: {e}",
            file=sys.stderr,
        )


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


# 导出阶段：防止 CDP 慢导致 page.evaluate / expect_download 长时间无响应
_EXPORT_DISPATCH_TIMEOUT_SEC = _env_float("FB_REPORT_DISPATCH_TIMEOUT_SEC", 240.0)
_EXPORT_DOWNLOAD_TIMEOUT_MS = _env_int("FB_REPORT_DOWNLOAD_TIMEOUT_MS", 90_000)
_EXPORT_EVALUATE_TIMEOUT_SEC = _env_float("FB_REPORT_EVALUATE_TIMEOUT_SEC", 25.0)
_EXPORT_DEBUG_EVALUATE_TIMEOUT_SEC = _env_float(
    "FB_REPORT_DEBUG_EVALUATE_TIMEOUT_SEC", 15.0
)
_EXPORT_FALLBACK_LOCATOR_WAIT_MS = _env_int(
    "FB_REPORT_FALLBACK_LOCATOR_WAIT_MS", 20_000
)
# 剩余预算低于此时跳过「全息快照」：避免长_evaluate 塞满点击链预算后兜底无时间执行
_EXPORT_DEBUG_MIN_REMAINING_SEC = _env_float(
    "FB_REPORT_DEBUG_MIN_REMAINING_SEC", 50.0
)


def _truthy_env(key: str) -> bool:
    return (os.environ.get(key) or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


# 不要用 asyncio.wait_for 包裹整条点击链：超时会 CancelledError，打断 Playwright 的 locator 等待。
_DISPATCH_DEADLINE_EXCEEDED_MSG = (
    "导出点击链已超过预算时间（CDP 慢或导出未触发）；请确认 Ads Manager 工具栏可见且 Export 可点击。"
)


def _cap_wait_seconds(dispatch_deadline: Optional[float], cap_sec: float) -> float:
    """收紧 asyncio.wait_for；预算用尽时抛 RuntimeError（不取消正在进行的 Playwright IO）。"""
    if dispatch_deadline is None:
        return cap_sec
    rem = dispatch_deadline - asyncio.get_running_loop().time()
    if rem <= 0:
        raise RuntimeError(_DISPATCH_DEADLINE_EXCEEDED_MSG)
    return min(cap_sec, max(0.25, rem))


def _cap_locator_wait_ms(
    dispatch_deadline: Optional[float], preferred_ms: int
) -> int:
    """locator.wait_for timeout：不超过剩余点击链预算。"""
    if dispatch_deadline is None:
        return preferred_ms
    rem = dispatch_deadline - asyncio.get_running_loop().time()
    if rem <= 0:
        raise RuntimeError(_DISPATCH_DEADLINE_EXCEEDED_MSG)
    cap_ms = int(rem * 1000)
    return max(300, min(preferred_ms, cap_ms))


async def _evaluate_wait_cap(
    page: Page,
    js: str,
    dispatch_deadline: Optional[float],
    cap_sec: float,
) -> Any:
    """
    先调用 _cap_wait_seconds，再 asyncio.wait_for(page.evaluate(js))。
    若把 _cap_wait_seconds 与 evaluate 写在同一层函数参数里，Python 会先求值 evaluate 产生协程，
    再求第二参数；预算用尽时第二参数抛错会导致 evaluate 协程从未被 await（RuntimeWarning）。
    """
    timeout_sec = _cap_wait_seconds(dispatch_deadline, cap_sec)
    return await asyncio.wait_for(page.evaluate(js), timeout_sec)


# Ads Manager 工具栏：Columns → Breakdown → [Reports, Download, Chart]。
# 必须在页面 **顶部窄带** 内解析 Breakdown，并排除 `right > ~88vw` 的全局导航/头像区，否则会点到右上角头像。
_TOOLBAR_EXPORT_CLICK_JS = r"""() => {
    const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
    const visible = (el) => {
        const st = window.getComputedStyle(el);
        return (
            el.offsetParent !== null &&
            st.visibility !== "hidden" &&
            st.display !== "none"
        );
    };
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const toolbarTopMax = vh * 0.26;
    const iconRightMax = vw * 0.88;

    const all = Array.from(
        document.querySelectorAll('[role="button"],button,a[role="button"]')
    ).filter(visible);

    const bdCandidates = all.filter((el) => {
        const t = norm(el.innerText);
        const al = norm(el.getAttribute("aria-label") || "");
        if (t.length >= 120) return false;
        if (!/^breakdown\b/i.test(t) && !/^breakdown\b/i.test(al)) return false;
        const r = el.getBoundingClientRect();
        return (
            r.top < toolbarTopMax &&
            r.left < vw * 0.62 &&
            r.width >= 36 &&
            r.height >= 20
        );
    });

    if (!bdCandidates.length) return false;

    bdCandidates.sort((a, b) => {
        const ra = a.getBoundingClientRect();
        const rb = b.getBoundingClientRect();
        if (Math.abs(ra.top - rb.top) > 28) return ra.top - rb.top;
        return ra.left - rb.left;
    });
    const bd = bdCandidates[0];
    const br = bd.getBoundingClientRect();

    const icons = all
        .filter((el) => el !== bd && el.querySelector("svg"))
        .filter((el) => {
            const r = el.getBoundingClientRect();
            if (r.width < 8 || r.height < 8) return false;
            if (r.right > iconRightMax) return false;
            if (r.top > toolbarTopMax + 40) return false;
            return (
                r.left >= br.right - 24 &&
                Math.abs(
                    r.top + r.height / 2 - (br.top + br.height / 2)
                ) < 52
            );
        })
        .sort(
            (a, b) =>
                a.getBoundingClientRect().left - b.getBoundingClientRect().left
        );

    const tip = (el) =>
        ((el.getAttribute("aria-label") || "") +
            " " +
            (el.getAttribute("data-tooltip-content") || "") +
            " " +
            (el.getAttribute("title") || "")).toLowerCase();

    for (const el of icons) {
        const t = tip(el);
        if (
            /download|export|导出|下载/.test(t) &&
            !/chart|report\\s*run|analytics/i.test(t)
        ) {
            el.click();
            return true;
        }
    }

    if (icons.length >= 3) {
        icons[1].click();
        return true;
    }
    if (icons.length === 2) {
        icons[1].click();
        return true;
    }
    if (icons.length === 1) {
        icons[0].click();
        return true;
    }
    return false;
}"""


async def _try_click_export_via_playwright_aria(
    page: Page, *, dispatch_deadline: Optional[float] = None
) -> bool:
    """
    用 Playwright **无障碍名**（等价于 aria-label 等可访问名称）锁定工具栏「导出/下载」，
    避免仅靠几何 nth 点到 Reports。
    """
    vw = 1920.0
    try:
        vw = float(
            await asyncio.wait_for(
                page.evaluate("() => window.innerWidth"),
                5.0,
            )
        )
    except Exception:
        pass
    # 可访问名称匹配（含英文 Export table / Download 等变体）
    name_re = re.compile(
        r"(download|export|导出|下载)",
        re.I,
    )
    try:
        cand = page.get_by_role("button", name=name_re)
        count = await cand.count()
    except Exception:
        return False
    for i in range(min(count, 40)):
        loc = cand.nth(i)
        try:
            await loc.wait_for(
                state="visible",
                timeout=_cap_locator_wait_ms(dispatch_deadline, 4_000),
            )
            box = await loc.bounding_box()
            if not box:
                continue
            if box["y"] > 360:
                continue
            if box["x"] > vw * 0.88:
                continue
            await loc.click(force=True)
            return True
        except Exception:
            continue
    return False


async def _try_click_export_via_breakdown_toolbar_js(
    page: Page, *, dispatch_deadline: Optional[float] = None
) -> bool:
    """
    工具栏几何：在页面顶部带状区域内定位「Breakdown」按钮，取其右侧同列 SVG 图标序列。
    英文 Ads Manager 常见顺序：Reports → Download（导出）→ Chart；Download 为索引 1（至少 3 枚图标时）。
    """
    try:
        return await _evaluate_wait_cap(
            page,
            _TOOLBAR_EXPORT_CLICK_JS,
            dispatch_deadline,
            _EXPORT_EVALUATE_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        print(
            f"⚠️ [工具栏拓扑] evaluate 超时（>{_EXPORT_EVALUATE_TIMEOUT_SEC}s），改用后续策略",
            flush=True,
        )
        return False


async def _fallback_click_export_icon(
    page: Page, *, dispatch_deadline: Optional[float] = None
) -> None:
    """
    仅针对 **Ads 表格上方工具栏**：Breakdown 右侧图标列为 Reports → Download → Chart。
    先用 Breakdown 的 CSS :right-of 点击 Download（通常为 nth=1），再用与主路径相同的
    ``_TOOLBAR_EXPORT_CLICK_JS`` 二次尝试（避免首轮 evaluate 超时）。
    已移除表格「Segment/细分」列锚与整页叶子文本几何，防止误点表格或右上角全局头像。
    """
    last_exc: Optional[BaseException] = None

    breakdown_sel = (
        'div[role="button"]:right-of(:has-text("Breakdown")), '
        'button:right-of(:has-text("Breakdown")), '
        'span[role="button"]:right-of(:has-text("Breakdown"))'
    )

    for label, sel in (("Breakdown工具栏", breakdown_sel),):
        # nth=0 为 Reports，会触发「点了但无下载」；仅尝试 Download(1) 与 Chart 旁备选(2)。
        for nth in (1, 2):
            loc = page.locator(sel).filter(has=page.locator("svg")).nth(nth)
            try:
                await loc.wait_for(
                    state="attached",
                    timeout=_cap_locator_wait_ms(
                        dispatch_deadline, _EXPORT_FALLBACK_LOCATOR_WAIT_MS
                    ),
                )
                print(
                    f"🎯 [视觉雷达·CSS:{label}·nth={nth}] 击发下载图标",
                    flush=True,
                )
                await loc.click(force=True)
                return
            except Exception as e:
                last_exc = e

    try:
        clicked = await _evaluate_wait_cap(
            page,
            _TOOLBAR_EXPORT_CLICK_JS,
            dispatch_deadline,
            min(12.0, _EXPORT_EVALUATE_TIMEOUT_SEC),
        )
    except asyncio.TimeoutError:
        clicked = False
        last_exc = last_exc or TimeoutError("工具栏兜底 evaluate 超时")
    if clicked:
        print(
            "🎯 [工具栏兜底] 已用顶部带状几何脚本二次点击 Download",
            flush=True,
        )
        return

    if last_exc:
        raise RuntimeError(
            "导出图标兜底失败：未在工具栏 Breakdown 右侧点到 Download（下载箭头）。"
            "请确认当前为「广告」列表页且顶部 Reports→Download→Chart 可见；"
            "Ngrok/CDP 慢时可增大 FB_REPORT_FALLBACK_LOCATOR_WAIT_MS、"
            "FB_REPORT_DISPATCH_TIMEOUT_SEC。"
            f" 最后一次底层错误：{last_exc!r}"
        ) from last_exc
    raise RuntimeError("导出按钮兜底失败：工具栏未匹配到 Breakdown 右侧下载箭头")


async def _dispatch_export_click_sequence(
    page: Page, *, dispatch_deadline: Optional[float] = None
) -> None:
    """
    导出点击链：
    1) Playwright ``get_by_role(button, name=…)`` 锁定下载/导出（优先）；
    2) 工具栏 JS：Breakdown 右侧图标带 aria 匹配，否则几何索引 1；
    3) 全局 aria 雷达；
    4) CSS Breakdown :right-of（仅 nth 1/2，跳过 Reports）+ 同 JS 兜底。
    """
    if await _try_click_export_via_playwright_aria(
        page, dispatch_deadline=dispatch_deadline
    ):
        print(
            "🎯 [Playwright] 已通过无障碍名称（get_by_role）锁定并点击导出控件",
            flush=True,
        )
        return

    if await _try_click_export_via_breakdown_toolbar_js(
        page, dispatch_deadline=dispatch_deadline
    ):
        print(
            "🎯 [工具栏拓扑] Breakdown 右侧 Download（Reports→Download→Chart 之中项）已点击",
            flush=True,
        )
        return

    export_btn = page.locator(
        '[aria-label*="Export"], [aria-label*="export"], '
        '[aria-label*="Download"], [aria-label*="download"], '
        '[aria-label*="导出"], [aria-label*="下载"], '
        '[data-tooltip-content*="Export"], [data-tooltip-content*="export"], '
        '[data-tooltip-content*="Download"], [data-tooltip-content*="download"], '
        '[data-testid*="export"], [data-testid*="download"]'
    ).first
    try:
        await export_btn.wait_for(
            state="attached",
            timeout=_cap_locator_wait_ms(dispatch_deadline, 8_000),
        )
        print("🎯 [常规雷达] 锁定导出控件，强制击发！", flush=True)
        await export_btn.click(force=True)
        return
    except Exception:
        pass

    print(
        "⚠️ [常规雷达失效] 遭遇纯图标 / 无属性。启动【全息快照】与拓扑兜底...",
        flush=True,
    )

    loop = asyncio.get_running_loop()
    rem_budget = (
        (dispatch_deadline - loop.time())
        if dispatch_deadline is not None
        else 1e9
    )
    skip_hologram = _truthy_env("FB_REPORT_SKIP_HOLOGRAM")
    debug_info = ""
    if skip_hologram:
        print(
            "⚠️ [全息快照] 已按 FB_REPORT_SKIP_HOLOGRAM 跳过基因序列（节省时间给兜底）",
            flush=True,
        )
    elif rem_budget < _EXPORT_DEBUG_MIN_REMAINING_SEC:
        print(
            f"⚠️ [全息快照] 剩余点击链预算仅 {rem_budget:.1f}s，"
            f"低于 {_EXPORT_DEBUG_MIN_REMAINING_SEC:.0f}s，跳过基因序列（优先兜底点击）",
            flush=True,
        )
    else:
        try:
            debug_info = await _evaluate_wait_cap(
                page,
                """() => {
            return Array.from(
                document.querySelectorAll('div[role="button"], button')
            )
                .map((el) => {
                    const label = el.getAttribute("aria-label") || "";
                    const tooltip =
                        el.getAttribute("data-tooltip-content") || "";
                    const testid = el.getAttribute("data-testid") || "";
                    const text = el.innerText.trim();
                    return label || tooltip || testid
                        ? `[Label:${label}|Tip:${tooltip}|ID:${testid}|Txt:${text}]`
                        : null;
                })
                .filter(Boolean)
                .join(String.fromCharCode(10));
        }""",
                dispatch_deadline,
                _EXPORT_DEBUG_EVALUATE_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            debug_info = ""
            print(
                f"⚠️ [全息快照] evaluate 超时（>{_EXPORT_DEBUG_EVALUATE_TIMEOUT_SEC}s），跳过基因序列调试输出",
                flush=True,
            )
    snippet = (debug_info or "")[:800]
    print(f"🕵️ 战场真实组件基因序列 (前800字符):\n{snippet}...", flush=True)

    print(
        "👁️ 启动视觉制导：Breakdown 工具栏 CSS（nth 1/2）与几何兜底…",
        flush=True,
    )

    await _fallback_click_export_icon(page, dispatch_deadline=dispatch_deadline)


async def _fb_export_followup_menu_for_download(page: Page) -> None:
    """
    Ads Manager 常见交互：第一次点「导出」只打开菜单 / 选格式，
    再选「Comma-separated values」「CSV」「Export table」等才真正触发 Chromium download。
    若不跟进，界面看起来像已开始导出，但 ``expect_download`` 永远等不到事件。
    """
    await asyncio.sleep(0.55)
    total_sec = _env_float("FB_REPORT_EXPORT_FOLLOWUP_SEC", 18.0)
    deadline = asyncio.get_running_loop().time() + max(2.0, total_sec)
    patterns = (
        re.compile(r"(comma[- ]?separated|\.csv|^csv$)", re.I),
        re.compile(r"(export\s+table|download\s+csv|table\s+data)", re.I),
    )
    roles = ("menuitem", "option", "menuitemradio", "button", "link")
    while asyncio.get_running_loop().time() < deadline:
        for pat in patterns:
            for role in roles:
                try:
                    loc = page.get_by_role(role, name=pat)
                    if await loc.count() == 0:
                        continue
                    await loc.first.click(timeout=3_000)
                    print(
                        f"🎯 [导出跟进] 二次点击（{role}）以触发浏览器下载事件",
                        flush=True,
                    )
                    return
                except Exception:
                    continue
        await asyncio.sleep(0.4)


async def _export_ads_csv(page: Page) -> Path:
    """在已打开 Ads Manager **广告**层级页的 Page 上执行导出并保存 CSV。"""
    page.set_default_timeout(DEFAULT_TIMEOUT_MS)
    page.set_default_navigation_timeout(FB_ADS_GOTO_TIMEOUT_MS)

    nav_attempts = 3
    for nav_i in range(nav_attempts):
        try:
            print(
                "🚀 正在发起跨洋导航，目标：Ads Manager "
                "(DOMContentLoaded + date/insights_date 深度链接)..."
            )
            nav_url = build_fb_deep_link(
                _env_url("FB_REPORT_TARGET_URL", DEFAULT_TARGET_URL),
                FB_REPORT_PRESET,
            )
            await page.goto(
                nav_url,
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
            await _debug_screenshot(page, "debug_nav_timeout.png")
            if nav_i == nav_attempts - 1:
                raise
            await asyncio.sleep(5)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"fb_ads_report_{FB_REPORT_PRESET}_{ts}.csv"
    dest = Path(__file__).resolve().parent.parent / filename
    print(f"📄 目标文件名已校准: {filename}")

    print("📸 拍摄战地快照 (当前视口，debug_before_click.png)...")
    await _debug_screenshot(page, "debug_before_click.png")

    print(
        "🎯 执行一击必杀（仅截获 URL，不依赖物理硬盘）；"
        f"下载监听最长 {_EXPORT_DOWNLOAD_TIMEOUT_MS}ms，点击链最长 {_EXPORT_DISPATCH_TIMEOUT_SEC}s…",
        flush=True,
    )
    hacker_page: Optional[Page] = None
    try:
        # Playwright Python 仅在 Page 上提供 expect_download（BrowserContext 无同名 API）。
        # 远端 CSV 导出仍由当前 Ads Manager 页触发，用 page 监听即可。
        async with page.expect_download(
            timeout=_EXPORT_DOWNLOAD_TIMEOUT_MS
        ) as download_info:
            loop = asyncio.get_running_loop()
            dispatch_deadline = loop.time() + _EXPORT_DISPATCH_TIMEOUT_SEC
            await _dispatch_export_click_sequence(
                page, dispatch_deadline=dispatch_deadline
            )
            await _fb_export_followup_menu_for_download(page)

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
        await _debug_screenshot(page, "debug_download_failed.png")
        if hacker_page is not None:
            try:
                await hacker_page.close()
            except Exception:
                pass
        raise

    return dest


async def main() -> int:
    async with async_playwright() as p:
        browser: Optional[Browser] = None
        connect_attempts = 3
        for attempt in range(1, connect_attempts + 1):
            try:
                wss_endpoint = await asyncio.get_running_loop().run_in_executor(
                    None, _rewrite_wss_endpoint_from_json_version, CDP_URL
                )
            except Exception as e:  # noqa: BLE001 — /json/version 拉取失败
                print(
                    f"⚠️ [CDP] /json/version 失败 ({attempt}/{connect_attempts})：{e!r}",
                    file=sys.stderr,
                )
                if attempt >= connect_attempts:
                    _print_wss_resolve_triage(e)
                    return 2
                await asyncio.sleep(3)
                continue

            try:
                print(
                    f"🔗 [{attempt}/{connect_attempts}] 路由篡改成功，WSS: {wss_endpoint}",
                    file=sys.stderr,
                )
                browser = await p.chromium.connect_over_cdp(
                    wss_endpoint,
                    headers=NGROK_HEADERS,
                    timeout=CONNECT_TIMEOUT_MS,
                )
                break
            except (
                PlaywrightError,
                PlaywrightTimeoutError,
                asyncio.TimeoutError,
                OSError,
            ) as e:
                print(
                    f"⚠️ [CDP] WebSocket 连接失败 ({attempt}/{connect_attempts})：{e!r}",
                    file=sys.stderr,
                )
                if attempt >= connect_attempts:
                    _print_connection_triage(e)
                    return 2
                await asyncio.sleep(3)
            except Exception as e:  # noqa: BLE001
                print(
                    f"⚠️ [CDP] 连接异常 ({attempt}/{connect_attempts})：{e!r}",
                    file=sys.stderr,
                )
                if attempt >= connect_attempts:
                    _print_connection_triage(e)
                    return 2
                await asyncio.sleep(3)

        if browser is None:
            return 2

        try:
            page = await _pick_working_page(browser)
            out_path = await _export_ads_csv(page)
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
