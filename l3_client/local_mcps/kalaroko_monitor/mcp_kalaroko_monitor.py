#!/usr/bin/env python3
"""
Kalaroko Web 性能自动化监控哨兵 — MCP Server（stdio / FastMCP）

契约：docs/KALAROKO_WEB_PERF_MONITOR_TDD.md

运行：
  python -m l3_client.local_mcps.kalaroko_monitor.mcp_kalaroko_monitor
  或
  python l3_client/local_mcps/kalaroko_monitor/mcp_kalaroko_monitor.py

依赖：mcp, playwright, httpx（需 `playwright install chromium`）。巡检浏览器：须设置 `KALAROKO_CDP_ENDPOINT` 并预先以远程调试端口启动 Chrome（`connect_over_cdp`）。
CDP 首次连接失败时，可经 ``KALAROKO_CDP_REVIVE_ON_CONNECT_FAIL`` 尝试 **OS 级拉起本机 Chrome** 绑定同一调试端口后再连；仍失败才回退 ``chromium.launch``。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

from core.kalaroko_e2e_jsonl_store import kalaroko_e2e_jsonl_lock

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("mcp_kalaroko_monitor")

# 供本机 E2E/脚本注入阶段性输出（stderr）；不参与 MCP JSON 工具模式，避免 Callable 进 schema。
_playwright_progress_cb: Callable[[str], None] | None = None


def set_playwright_progress_callback(cb: Callable[[str], None] | None) -> None:
    """设置/清除 Playwright 巡检阶段性回调；默认 None。异常在回调内吞掉，不影响采集结果。"""
    global _playwright_progress_cb
    _playwright_progress_cb = cb


_active_pw_browser: Any | None = None
_active_pw_context: Any | None = None
_active_pw_must_close_context: bool = False


def _kalaroko_register_playwright_session(
    browser: Any, context: Any, must_close_context: bool
) -> None:
    """供外层在超时/Cancelled 后 ``emergency_kalaroko_playwright_cleanup`` 仍能关到浏览器。"""
    global _active_pw_browser, _active_pw_context, _active_pw_must_close_context
    _active_pw_browser = browser
    _active_pw_context = context
    _active_pw_must_close_context = bool(must_close_context)


def _kalaroko_clear_playwright_session() -> None:
    global _active_pw_browser, _active_pw_context, _active_pw_must_close_context
    _active_pw_browser = None
    _active_pw_context = None
    _active_pw_must_close_context = False


async def emergency_kalaroko_playwright_cleanup() -> None:
    """E2E 外层 ``wait_for`` 熔断或任务取消时的二次关闭；与 ``execute_playwright_perf_test`` 内 ``finally`` 互补。"""
    ctx = _active_pw_context
    br = _active_pw_browser
    mcc = _active_pw_must_close_context
    _kalaroko_clear_playwright_session()
    try:
        if ctx is not None and mcc:
            await ctx.close()
    except BaseException as e:
        logger.warning("[kalaroko_monitor] emergency context.close: %s", str(e)[:300])
    try:
        if br is not None:
            await br.close()
    except BaseException as e:
        logger.warning("[kalaroko_monitor] emergency browser.close: %s", str(e)[:300])


class KalarokoE2EUserCancelled(Exception):
    """POST /api/v1/monitor/stop 已置位；应尽快结束 Playwright 场景采集。"""


def _e2e_user_cancel_requested() -> bool:
    """与 ``l3_node.kalaroko_e2e_control`` 共享标志；独立跑 MCP 时若无 L3 则始终 False。"""
    try:
        from l3_node.kalaroko_e2e_control import is_manual_run_cancel_requested

        return bool(is_manual_run_cancel_requested())
    except Exception:
        return False


def _make_timeline_mark(
    timeline: list[str], t_start_wall: float, game_id: str
) -> Callable[[str], None]:
    """毫秒级墙钟时间线（与 ``real_engine_load_ms`` 的 perf_counter 锚点分离，便于 SRE 读日志）。"""

    def mark_time(step_name: str) -> None:
        elapsed = time.time() - t_start_wall
        msg = f"[{elapsed:.2f}s] {step_name}"
        timeline.append(msg)
        logger.info("[Timeline] %s -> %s", game_id, msg)

    return mark_time


async def _abortable_wait_for_timeout(page: Any, total_ms: int, chunk_ms: int = 450) -> bool:
    """分段 ``wait_for_timeout``，其间轮询停止信号；返回 True 表示用户已请求中止。"""
    remain = max(0, int(total_ms))
    while remain > 0:
        if _e2e_user_cancel_requested():
            return True
        step = min(chunk_ms, remain)
        await page.wait_for_timeout(step)
        remain -= step
    return False


async def _abortable_wait_for_selector_attached(
    page: Any,
    selector: str,
    total_timeout_ms: int,
    *,
    chunk_ms: int = 400,
) -> tuple[bool, bool]:
    """
    分段 ``wait_for_selector(..., state='attached')``，其间可响应停止巡检。
    返回 ``(found, cancelled)``：``cancelled=True`` 时应抛出 ``KalarokoE2EUserCancelled``；
    ``found=True`` 表示已附着；二者均为 False 表示窗口内始终未命中（与原单次超时语义一致）。
    """
    deadline = time.perf_counter() + max(0.001, total_timeout_ms / 1000.0)
    while time.perf_counter() < deadline:
        if _e2e_user_cancel_requested():
            return False, True
        slice_ms = int(min(chunk_ms, max(50.0, (deadline - time.perf_counter()) * 1000.0)))
        try:
            await page.wait_for_selector(selector, state="attached", timeout=slice_ms)
            return True, False
        except Exception:
            continue
    return False, False


def _raise_if_e2e_cancelled() -> None:
    if _e2e_user_cancel_requested():
        raise KalarokoE2EUserCancelled()


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


async def _nudge_locator_clear_bottom_chrome(
    target: Any,
    *,
    scenario_name: str,
    reserve_px: int,
) -> None:
    """
    将入口节点滚入视口并必要时向上滚动，使元素底部与视口底边保留 reserve_px，
    降低 Playwright 命中固定底栏 ``_app_tabbar_*``（日志里表现为点击被 tabbar_item 接收）的概率。
    """
    try:
        await target.evaluate(
            """(el, reserve) => {
              const vh = window.innerHeight || document.documentElement.clientHeight || 640;
              if (!el || !reserve || reserve < 8) return;
              el.scrollIntoView({ block: 'center', inline: 'nearest' });
              for (let i = 0; i < 8; i++) {
                const r = el.getBoundingClientRect();
                const bottomGap = vh - r.bottom;
                if (bottomGap >= reserve) break;
                window.scrollBy({ top: Math.ceil(reserve - bottomGap + 12), left: 0, behavior: 'instant' });
              }
            }""",
            reserve_px,
            timeout=_env_int("KALAROKO_NUDGE_SCROLL_EVAL_MS", 2000, vmin=400, vmax=8000),
        )
    except Exception as ex:
        logger.debug(
            "[kalaroko_monitor] 【%s】底栏避让滚动跳过: %s",
            scenario_name,
            str(ex)[:180],
        )


async def _set_bottom_tabbar_pointer_events_enabled(page: Any, *, suppress: bool) -> None:
    """
    ``suppress=True``：注入样式，使底部 Tab 栏不接收指针事件（便于点到游戏卡片）。
    ``suppress=False``：移除注入样式，恢复页面默认行为。
    """
    try:
        if not suppress:
            await page.evaluate(
                """() => {
                  const n = document.getElementById('kalaroko-e2e-tabbar-pe');
                  if (n) n.remove();
                }"""
            )
            return
        await page.evaluate(
            """() => {
              let s = document.getElementById('kalaroko-e2e-tabbar-pe');
              if (!s) {
                s = document.createElement('style');
                s.id = 'kalaroko-e2e-tabbar-pe';
                s.textContent =
                  "[class*='app_tabbar'],[class*='AppTabbar']{pointer-events:none!important;}";
                document.documentElement.appendChild(s);
              }
            }"""
        )
    except Exception:
        pass


try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    logger.error("请安装 mcp: pip install mcp")
    sys.exit(1)

try:
    import httpx
except ImportError:
    logger.error("请安装 httpx: pip install httpx")
    sys.exit(1)

_SCHEMA_VERSION = "1.0.0"
_DEFAULT_BASE = "https://kalaroko.com"

# 默认监控任务：首页 + 四款游戏。
# 游戏入口改为「首页 start_url + UI 点击流」，避免带 partyId/token 的 game-frame 直链被 WAF/业务网关拦截。
# click_selector 须随前端 DOM 调整；可用 Playwright 文本选择器或 CSS（见 Playwright selector 语法）。
# UI 点击流默认 require_game_frame_url=True：采数结束须出现 /game-frame 主文档 URL，否则 load_status=failed（防未真开游戏壳仍 success）。
_DEFAULT_START = "https://kalaroko.com/"
KALAROKO_DEFAULT_SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "name": "homepage",
        "start_url": _DEFAULT_START,
        "wait_until": "domcontentloaded",
        "timeout_ms": 60000,
    },
    {
        "name": "tongits_king",
        "document_game_id": 5,
        "start_url": _DEFAULT_START,
        # 首页卡片常为「插图 + 底部标题」：精确 text='…' 易点到不可交互文案节点；正则略宽松
        "click_selector": r"text=/Tongits\s*King/i",
        "entry_wait_until": "domcontentloaded",
        "click_timeout_ms": 10000,
        # 游戏壳：goto 仅用 domcontentloaded（避免 BGM/WebM 等拖满 load）；就绪由仿生竞速（HTTP/WS/Canvas）判定
        "wait_until": "domcontentloaded",
        "timeout_ms": 90000,
    },
    {
        "name": "royal_pusoy",
        "document_game_id": 7,
        "start_url": _DEFAULT_START,
        "click_selector": r"text=/Royal\s*Pusoy/i",
        "entry_wait_until": "domcontentloaded",
        "click_timeout_ms": 10000,
        "wait_until": "domcontentloaded",
        "timeout_ms": 90000,
    },
    {
        "name": "color_blitz",
        "document_game_id": 6,
        # 文案常 2～3 处重复（横幅/列表）；与 Tongits 的「多命中取 .last」同理，避免点到不可导航层
        "prefer_last_on_ambiguous_entry": True,
        "start_url": _DEFAULT_START,
        "click_selector": r"text=/Color\s*Blitz\s*Social/i",
        "entry_wait_until": "domcontentloaded",
        "click_timeout_ms": 10000,
        "wait_until": "domcontentloaded",
        "timeout_ms": 90000,
    },
    {
        "name": "bingo_showdown",
        "document_game_id": 10,
        "prefer_last_on_ambiguous_entry": True,
        "start_url": _DEFAULT_START,
        "click_selector": r"text=/Bingo\s*Showdown/i",
        "entry_wait_until": "domcontentloaded",
        "click_timeout_ms": 10000,
        "wait_until": "domcontentloaded",
        "timeout_ms": 90000,
    },
)


def _href_anchor_selectors_for_document_game_id(g: int) -> tuple[str, ...]:
    """
    大厅游戏卡片入口 <a href> 的常见变体（编码 / 驼峰），与 gid 等待、resolve 同源，
    避免「仅含 game_id%3D 的链接」导致 wait 失败、退化为纯文案 .first 点错层。
    """
    return (
        f'a[href*="game_id={g}"]',
        f'a[href*="game_id%3D{g}"]',
        f'a[href*="game_id%253D{g}"]',
        f'a[href*="gameId={g}"]',
        f'a[href*="game-id={g}"]',
        f'a[href*="game_id%3d{g}"]',
    )


def _game_path_fallback(scenario: dict[str, Any], url: str) -> str:
    """游戏场景 path：优先 scenario.path，否则从 URL 解析（避免失败分支 path 为空）。"""
    raw = scenario.get("path")
    if raw is not None and str(raw).strip():
        return str(raw).strip()
    try:
        p = urlparse(url).path or ""
        return p if p else "/game-frame"
    except Exception:
        return "/game-frame"


def _extract_room_id_from_url(page_url: str | None) -> str:
    """从 game-frame 最终 URL 查询串解析房间/派对标识（尽力而为）。"""
    if not page_url or not str(page_url).strip():
        return "N/A"
    try:
        pu = urlparse(page_url.strip())
        q = parse_qs(pu.query)
        for key in ("partyId", "room_id", "roomId", "party_id"):
            vals = q.get(key)
            if vals and str(vals[0]).strip():
                return str(vals[0]).strip()
        frag = pu.fragment or ""
        if "?" in frag:
            fq = parse_qs(frag.split("?", 1)[1])
            for key in ("partyId", "room_id", "roomId", "party_id"):
                vals = fq.get(key)
                if vals and str(vals[0]).strip():
                    return str(vals[0]).strip()
    except Exception:
        pass
    return "N/A"


def _empty_online_players_dict() -> dict[str, str | None]:
    """online_players 双轨字段空壳（JSON 与类型一致）。"""
    return {"table": None, "lobby": None}


def _parse_lobby_count_token(tok: str) -> float | None:
    """将大厅徽章类数字 token 解析为可比较数值（支持 ``1.2k`` / ``1.5K``）。"""
    t = (tok or "").strip().lower().replace(",", "")
    if not t:
        return None
    m = re.match(r"^([0-9]+(?:\.[0-9]+)?)([km])?$", t)
    if not m:
        return None
    num = float(m.group(1))
    suf = (m.group(2) or "").lower()
    if suf == "k":
        num *= 1_000.0
    elif suf == "m":
        num *= 1_000_000.0
    return num


def _lobby_token_digit_len(tok: str) -> int:
    """用于与误匹配的单位数区分：徽章 ``53`` 为 2 位，碎片 ``3`` 常为 1 位。"""
    core = (tok or "").strip().split(".")[0]
    return len(re.sub(r"\D", "", core))


def _pick_best_lobby_from_tokens(tokens: list[str]) -> str | None:
    """
    从 ``fallback_match`` 候选里选出大厅在线人数。

    旧逻辑取 ``tokens[-1]``，DOM 拼接顺序下可能出现「53」在前、无关「3」在后而误报 3。
    规则：只要存在 **≥2 位数字** 的候选，则 **忽略所有单位数**；在保留集合内取数值最大者
    （典型徽章为「53」类并发数；单位数多为噪声）。
    """
    scored: list[tuple[float, str, int]] = []
    for raw in tokens:
        tok = raw.strip()
        v = _parse_lobby_count_token(tok)
        if v is None:
            continue
        if 2000 <= v <= 2099:
            continue
        if v <= 0 or v > 2_000_000:
            continue
        dl = _lobby_token_digit_len(tok)
        scored.append((v, tok, dl))
    if not scored:
        return None
    multi = [x for x in scored if x[2] >= 2]
    pool = multi if multi else scored
    best = max(pool, key=lambda x: x[0])
    return best[1]


def _extract_online_players_hint(text: str | None) -> dict[str, str | None]:
    """
    从**首页大厅入口卡片**聚合文案中双轨提取人数。

    返回 ``{"table": str|None, "lobby": str|None}``：
    - ``table``：备战/座位占比（如 ``3/4``）
    - ``lobby``：大厅徽章纯数字（如 ``49``、``1.5K``）；与 table 并联；提取 lobby 前会遮住已匹配的 table 片段，避免分子被重复计入 lobby。
    """
    result: dict[str, str | None] = _empty_online_players_dict()
    if not text:
        return result
    t = re.sub(r"\s+", " ", str(text).replace("\n", " ").strip())
    if not t:
        return result

    table_span: tuple[int, int] | None = None

    m = re.search(
        r"\d{1,3}(?:,\d{3})+\s*/\s*\d{1,3}(?:,\d{3})+(?:\s*/\s*\d+)?|\d+\s*/\s*\d+",
        t,
    )
    if m:
        result["table"] = re.sub(r"\s+", "", m.group(0))
        table_span = m.span()

    if result["table"] is None:
        patterns_slashed = (
            r"\d+\s+of\s+\d+",
            r"\d+\s*[-–]\s*\d+",
            r"(?:online|playing)\s*[:\s]\s*\d+\s*/\s*\d+",
            r"\(\s*\d+\s*/\s*\d+\s*\)",
        )
        for pat in patterns_slashed:
            mo = re.search(pat, t, re.I)
            if mo:
                frag = mo.group(0).strip()
                if "/" in frag:
                    result["table"] = re.sub(r"\s+", "", frag)
                    table_span = mo.span()
                break

    if result["table"] is None:
        mo2 = re.search(r"(\d+)\s*/\s*\d+", t)
        if mo2:
            result["table"] = re.sub(r"\s+", "", mo2.group(0))
            table_span = mo2.span()

    if result["table"] is None:
        mo3 = re.search(r"(\d+)\s*(?:/|人)", t)
        if mo3 and "/" in mo3.group(0):
            result["table"] = mo3.group(0).strip()
            table_span = mo3.span()

    if table_span:
        a, b = table_span
        t_lobby = t[:a] + " " * (b - a) + t[b:]
    else:
        t_lobby = t

    fallback_match = re.findall(
        r"(?:^|\s)([1-9]\d{0,3}(?:\.\d{1,2})?[kKmM]?)(?:\s|$)",
        t_lobby,
    )
    picked = _pick_best_lobby_from_tokens(fallback_match)
    if picked:
        result["lobby"] = picked

    return result


def _env_int(name: str, default: int, *, vmin: int, vmax: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(vmin, min(vmax, int(raw)))
    except ValueError:
        return default


async def _lobby_game_entry_text_for_players_hint(target: Any) -> str:
    """
    聚合入口节点及周边 DOM 文案，供人数正则匹配。

    失败根因常为：(1) 人数异步写入 —— 由调用方短超时 ``inner_text``/``evaluate`` 尽力读取；
    (2) ``innerText`` 不含隐藏/未排版节点 —— 补充 ``textContent``；
    (3) 人数在兄弟列/另一行 —— 向上遍历（最多 6 层）并合并父级 **所有子节点** 文案；
    (4) 仅在 ``data-*`` / ``title`` —— 一并拼接。
    向上遍历时若某层 ``innerText`` 长度超过 150，视为已进到游戏列表等大容器，停止爬升以免串台。
    """
    parts: list[str] = []
    _txt_ms = _env_int("KALAROKO_LOBBY_HINT_INNER_TEXT_MS", 1200, vmin=200, vmax=8000)
    try:
        parts.append((await target.inner_text(timeout=_txt_ms)).strip())
    except Exception:
        pass
    try:
        _ev_ms = _env_int("KALAROKO_LOBBY_HINT_EVALUATE_MS", 1800, vmin=300, vmax=10000)
        blob = await target.evaluate(
            r"""(el) => {
              if (!el) return '';
              const out = [];
              const push = (s) => {
                const t = (s || '').trim();
                if (t) out.push(t);
              };
              const attrsFrom = (root) => {
                if (!root || !root.querySelectorAll) return;
                ['data-online','data-players','data-player','data-count','data-seat',
                 'data-total','data-playing','data-cur','data-max'].forEach((name) => {
                  try {
                    root.querySelectorAll('[' + name + ']').forEach((node) => {
                      const v = node.getAttribute(name);
                      if (v && String(v).trim()) push(name + ':' + String(v).trim());
                    });
                  } catch (e) {}
                });
              };
              let n = el;
              // 深度 ≤6，避免跨卡片；单节点 innerText>150 视为已爬到游戏列表等大容器，熔断停止
              for (let depth = 0; depth < 6 && n; depth++) {
                const currentText = n.innerText || '';
                if (currentText.length > 150) break;
                push(currentText);
                push(n.textContent || '');
                try {
                  n.getAttributeNames().forEach((nm) => {
                    if (/^(aria-label|title)$/i.test(nm)) {
                      const v = n.getAttribute(nm);
                      if (v && v.trim()) push(nm + ':' + v.trim());
                    }
                  });
                } catch (e) {}
                attrsFrom(n);
                if (n.parentElement) {
                  const kids = n.parentElement.children || [];
                  for (let i = 0; i < kids.length; i++) {
                    const k = kids[i];
                    push(k.innerText || '');
                    push(k.textContent || '');
                  }
                }
                n = n.parentElement;
              }
              return [...new Set(out)].join('\n');
            }""",
            timeout=_ev_ms,
        )
        if blob:
            parts.append(str(blob).strip())
    except Exception:
        pass
    merged = "\n".join(p for p in parts if p)
    return merged


def _gweb_game_id_from_monitor_url(url: str) -> int | None:
    """从 game-frame 监控 URL 的 frameUrl（解码后的 gweb）查询串中解析 game_id。"""
    try:
        qs = parse_qs(urlparse(url).query)
        raw = (qs.get("frameUrl") or [None])[0]
        if not raw:
            return None
        inner = unquote(raw)
        iqs = parse_qs(urlparse(inner).query)
        g = (iqs.get("game_id") or [None])[0]
        return int(g) if g is not None else None
    except Exception:
        return None


def _game_id_snapshot_fields(scenario: dict[str, Any], url: str) -> dict[str, Any]:
    """游戏行附加：document_game_id（Word/BI 小节标题）；url_game_id（优先从 gweb frameUrl 解析）。"""
    extra: dict[str, Any] = {}
    raw_doc = scenario.get("document_game_id")
    ugi = _gweb_game_id_from_monitor_url(url)
    if ugi is not None:
        extra["url_game_id"] = ugi
    elif str(scenario.get("click_selector") or "").strip() and raw_doc is not None:
        # UI 点击流落地页未必带 game-frame 监控 URL；无法解析 gweb 时用 scenario 对齐 BI/Word
        try:
            extra["url_game_id"] = int(raw_doc)
        except Exception:
            pass
    if raw_doc is not None:
        try:
            extra["document_game_id"] = int(raw_doc)
        except Exception:
            pass
    return extra


def _goto_policy_chain(wait_until: str) -> list[str]:
    """
    goto 降级顺序。

    默认以 **domcontentloaded** 为先，避免 ``load`` 被跨国音视频等大资源拖死；进桌就绪由
    ``_game_deep_wait_after_goto`` 晚期竞速（**晚期 UI DOM** 与 **后置遥测 XHR** 均可停表）判定。显式 ``load`` / ``networkidle``
    仍先尝试 dcl，再 ``commit``，最后才回退 ``load``（兼容极少数壳页）。
    """
    p = (wait_until or "domcontentloaded").strip().lower()
    if p in ("networkidle", "load", "domcontentloaded"):
        return ["domcontentloaded", "commit", "load"]
    return [p, "domcontentloaded", "commit", "load"]


async def _kalaroko_ui_breathe(
    page: Any,
    pace_ms: int,
    *,
    progress: Callable[[str], None] | None = None,
    hint: str = "",
) -> None:
    """关键步骤间暂停并顶到前台，便于 CDP 附着时肉眼观察（不影响指标采集逻辑）。"""
    if pace_ms <= 0:
        return
    try:
        await page.bring_to_front()
    except Exception:
        pass
    if hint and progress:
        try:
            progress(hint)
        except Exception:
            pass
    await asyncio.sleep(min(30.0, pace_ms / 1000.0))


async def _kalaroko_ui_move_mouse_to_locator(page: Any, target: Any, pace_ms: int) -> None:
    """分步移动鼠标到目标中心，使真实 Chrome 里能看到指针轨迹（pace_ms 越大步数略多）。"""
    if pace_ms <= 0:
        return
    try:
        box = await target.bounding_box()
        if not box:
            return
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2
        steps = max(12, min(56, pace_ms // 18))
        await page.mouse.move(cx, cy, steps=steps)
    except Exception:
        pass


async def _goto_resilient(
    page: Any,
    url: str,
    wait_until: str,
    timeout_ms: int,
    policy_chain: list[str] | None = None,
) -> Any:
    """
    导航到 url：优先使用调用方 wait_until（游戏/大厅默认 ``domcontentloaded``）；若遇 net::ERR_ABORTED 等中断，按策略链降级。
    """
    policies = policy_chain if policy_chain is not None else _goto_policy_chain(wait_until)
    seen: list[str] = []
    last_err: Exception | None = None
    for policy in policies:
        if policy in seen:
            continue
        seen.append(policy)
        try:
            return await page.goto(url, wait_until=policy, timeout=timeout_ms)
        except Exception as e:
            last_err = e
            err_s = str(e).lower()
            retryable = (
                "err_aborted" in err_s
                or "net::err" in err_s
                or "timeout" in err_s
            )
            if not retryable or policy == "commit":
                raise
            logger.warning(
                "[kalaroko_monitor] goto retry url=%s policy=%s -> %s err=%s",
                url[:120],
                policy,
                "next policy",
                str(e)[:200],
            )
    assert last_err is not None
    raise last_err


def _regex_from_playwright_text_selector(sel: str) -> re.Pattern[str] | None:
    """
    从 Playwright 文本选择器 ``text=/pattern/flags`` 抽出正则；失败返回 None。
    （用于优先匹配 <a>/link role，避免纯 text= 命中轮播/插图内多处重复文案。）
    """
    s = sel.strip()
    if not s.startswith("text=/"):
        return None
    rest = s[6:]
    if not rest:
        return None
    idx = rest.rfind("/")
    if idx <= 0:
        return None
    pat = rest[:idx]
    flag_s = rest[idx + 1 :]
    cf = 0
    if "i" in flag_s:
        cf |= re.I
    if "m" in flag_s:
        cf |= re.M
    if "s" in flag_s:
        cf |= re.DOTALL
    try:
        return re.compile(pat, cf)
    except re.error:
        return None


async def _kalaroko_plain_text_hit_is_lobby_entry(cand: Any) -> bool:
    """
    排除 Party Hubs 等「同名游戏只读标题」节点：全局 text= 命中过多时 .last 常为
    ``_party_card_game_name_*`` span，无有效导航或点击不进门。
    """
    try:
        # 显式短超时：避免缺帧/离屏 nth 在部分 CDP 环境下 evaluate 长时间挂死
        return await cand.evaluate(
            """el => {
              if (!el || !el.closest) return false;
              if (el.closest('[class*="party_card"]')) return false;
              const nav = el.closest(
                'a[href], [role="link"], [role="button"], button, [data-href]'
              );
              if (!nav) return false;
              if (nav.tagName === 'A' || nav.getAttribute('role') === 'link') {
                const h = nav.getAttribute('href') || '';
                if (!h || h === '#' || h.toLowerCase().startsWith('javascript:')) return false;
              }
              return true;
            }""",
            timeout=_env_int(
                "KALAROKO_ENTRY_DISAMBIG_EVAL_MS", 2000, vmin=200, vmax=15000
            ),
        )
    except Exception:
        return False


def _skip_game_id_href_lazy_gate(
    scenario: dict[str, Any] | None, click_selector: str
) -> bool:
    """
    卡片入口未必带 ``game_id=`` 的 ``<a href>``（或懒加载极慢），却配置了 ``text=/…/`` +
    ``prefer_last_on_ambiguous_entry``（如 Color Blitz）时，盲等 href 会白烧 15s+8s。
    此时跳过 href 门闩，直接走文案 / link 角色解析。
    """
    if not scenario:
        return False
    pl = scenario.get("prefer_last_on_ambiguous_entry")
    if isinstance(pl, str):
        pl = pl.strip().lower() not in ("0", "false", "no", "off")
    if not bool(pl):
        return False
    return str(click_selector or "").strip().startswith("text=/")


async def _resolve_kalaroko_game_entry_locator(
    page: Any,
    *,
    click_selector: str,
    scenario_name: str,
    scenario: dict[str, Any] | None,
) -> tuple[Any, str]:
    """
    解析「要点的大厅入口」定位器：优先 document_game_id / 可导航链接，其次宽松 text=。
    多处命中时优先选用「非 Party 卡片 + 含可导航祖先」的项，避免 .last 点到 Party 只读标题。
    """
    scen = scenario or {}
    gid_raw = scen.get("document_game_id")
    g: int | None = None
    try:
        if gid_raw is not None:
            g = int(gid_raw)
    except (TypeError, ValueError):
        g = None

    ambig_last = bool(scen.get("prefer_last_on_ambiguous_entry"))

    if g is not None:
        href_selectors = _href_anchor_selectors_for_document_game_id(g)
        for hs in href_selectors:
            try:
                hl = page.locator(hs)
                c = await hl.count()
                if c >= 1:
                    return hl.first, f"{hs} (document_game_id={g})"
            except Exception:
                continue
        for attr_sel in (
            f'[data-game-id="{g}"]',
            f'[data-game_id="{g}"]',
            f'[data-gameid="{g}"]',
        ):
            try:
                hl = page.locator(attr_sel)
                c = await hl.count()
                if c >= 1:
                    return hl.first, f"{attr_sel} (document_game_id={g})"
            except Exception:
                continue

    rx = _regex_from_playwright_text_selector(click_selector)
    if rx is not None:
        try:
            by_role = page.get_by_role("link", name=rx)
            cr = await by_role.count()
            if cr >= 3 or (ambig_last and cr >= 2):
                logger.info(
                    "[kalaroko_monitor] 【%s】link+regex 命中 %s 处，改用 .last（避免轮播/横幅假入口）",
                    scenario_name,
                    cr,
                )
                return by_role.last, "get_by_role(link, name=regex).last"
            if cr >= 1:
                return by_role.first, "get_by_role(link, name=regex)"
        except Exception:
            pass
        try:
            al = page.locator("a").filter(has_text=rx)
            ca = await al.count()
            if ca >= 3 or (ambig_last and ca >= 2):
                logger.info(
                    "[kalaroko_monitor] 【%s】a.filter(regex) 命中 %s 处，改用 .last",
                    scenario_name,
                    ca,
                )
                return al.last, f"locator('a').filter(has_text=regex).last n={ca}"
            if ca >= 1:
                return al.first, f"locator('a').filter(has_text=regex) n={ca}"
        except Exception:
            pass

    loc = page.locator(click_selector)
    try:
        cnt = await loc.count()
    except Exception:
        cnt = -1

    if cnt == 0:
        msg = (
            f"选择器未匹配任何 DOM（前端改版或语言/文案不一致）。当前 selector={click_selector!r}"
        )
        logger.error("[kalaroko_monitor] 【%s】%s", scenario_name, msg)
        raise RuntimeError(f"[{scenario_name}] {msg}")

    # 命中多处：.first 易为轮播；原 .last 在「首页列表 + Party 同名」并存时会点到 Party 只读 span。
    if cnt >= 3 or (ambig_last and cnt >= 2):
        max_probe = min(
            cnt,
            _env_int("KALAROKO_ENTRY_DISAMBIG_MAX_NTH", 8, vmin=1, vmax=24),
        )
        for idx in range(max_probe):
            cand = loc.nth(idx)
            try:
                if await _kalaroko_plain_text_hit_is_lobby_entry(cand):
                    logger.info(
                        "[kalaroko_monitor] 【%s】文案命中 %s 处，跳过 Party/无导航项，选用 nth=%s",
                        scenario_name,
                        cnt,
                        idx,
                    )
                    return cand, f"text locator nth={idx} (feed-like, count={cnt})"
            except Exception:
                continue
        logger.info(
            "[kalaroko_monitor] 【%s】文案选择器命中 %s 处，无 feed-like 单项，回退 .last",
            scenario_name,
            cnt,
        )
        return loc.last, f"text locator .last (count={cnt})"
    return loc.first, f"text locator .first (count={cnt})"


async def _diagnose_and_click_kalaroko_game_entry(
    page: Any,
    *,
    click_selector: str,
    click_timeout_ms: int,
    scenario_name: str,
    scenario: dict[str, Any] | None = None,
    progress: Callable[[str], None] | None = None,
    ui_pace_ms: int = 0,
    ui_cursor_moves: bool = False,
) -> dict[str, str | None]:
    """
    大厅游戏入口点击：短 ``scroll_into_view`` + ``attached``（**不**等 visible/actionability），
    首击即 ``force=True`` + ``no_wait_after=True``；失败时重试与 JS 祖先兜底。
    人数文案在点击**之后**尽力读取，避免 ``inner_text`` 在点击前阻塞数十秒。

    Returns:
        ``online_players`` 双轨字典 ``{"table": ..., "lobby": ...}``（大厅卡片并联提取）；
        均未解析时为 ``{"table": None, "lobby": None}``。单房内上桌人数见 ``table_seat_players``（当前未接）。
    """
    online_hint: dict[str, str | None] = _empty_online_players_dict()
    try:
        cur = (page.url or "")[:320]
    except Exception:
        cur = "?"
    try:
        vw = page.viewport_size
        vp_s = f"{vw['width']}x{vw['height']}" if vw else "n/a"
    except Exception:
        vp_s = "n/a"
    logger.info(
        "[kalaroko_monitor] 【%s】入口点击诊断 | url=%s | viewport=%s | selector=%s",
        scenario_name,
        cur,
        vp_s,
        click_selector[:420],
    )
    if progress:
        progress(f"「{scenario_name}」入口：viewport={vp_s}，即将滚动并点击 …")

    _raise_if_e2e_cancelled()

    # 返回大厅后可能再次浮出 adm Enter 遮罩；入口点击前再扫一次（与 _prepare 内逻辑一致）
    try:
        await _dismiss_kalaroko_resume_session_popup(page, progress=progress)
    except Exception:
        pass

    _raise_if_e2e_cancelled()

    gid_wait: int | None = None
    try:
        dg = (scenario or {}).get("document_game_id")
        if dg is not None:
            gid_wait = int(dg)
    except (TypeError, ValueError):
        gid_wait = None
    skip_gid_href_gate = _skip_game_id_href_lazy_gate(scenario, click_selector)
    if gid_wait is not None and skip_gid_href_gate:
        logger.info(
            "[kalaroko_monitor] 【%s】跳过 game_id <a href> 懒加载门闩（prefer_last + text=，避免白等数十秒）",
            scenario_name,
        )
    if gid_wait is not None and not skip_gid_href_gate:
        href_union = ",".join(_href_anchor_selectors_for_document_game_id(gid_wait))
        href_ms = _env_int(
            "KALAROKO_GAME_ENTRY_HREF_WAIT_MS", 3500, vmin=0, vmax=30000
        )
        found_href, cancelled_href = await _abortable_wait_for_selector_attached(
            page, href_union, href_ms
        )
        if cancelled_href:
            raise KalarokoE2EUserCancelled()
        if not found_href:
            logger.debug(
                "[kalaroko_monitor] 【%s】未在 %sms 内等到含 game_id 的入口链接（列表可能懒加载），尝试滚动后再等 …",
                scenario_name,
                href_ms,
            )
            try:
                await page.evaluate(
                    "() => { try { window.scrollTo(0, document.body.scrollHeight); } catch (e) {} }"
                )
                scroll_settle = _env_int(
                    "KALAROKO_GAME_ENTRY_HREF_SCROLL_SETTLE_MS", 350, vmin=0, vmax=3000
                )
                if scroll_settle and await _abortable_wait_for_timeout(
                    page, scroll_settle
                ):
                    raise KalarokoE2EUserCancelled()
                href_retry_ms = _env_int(
                    "KALAROKO_GAME_ENTRY_HREF_SCROLL_RETRY_MS",
                    2500,
                    vmin=0,
                    vmax=30000,
                )
                found2, cancelled2 = await _abortable_wait_for_selector_attached(
                    page, href_union, href_retry_ms
                )
                if cancelled2:
                    raise KalarokoE2EUserCancelled()
                if not found2:
                    logger.debug(
                        "[kalaroko_monitor] 【%s】滚动后仍未等到入口链接，继续用文案解析",
                        scenario_name,
                    )
            except KalarokoE2EUserCancelled:
                raise
            except Exception:
                logger.debug(
                    "[kalaroko_monitor] 【%s】滚动后仍未等到入口链接，继续用文案解析",
                    scenario_name,
                )

    _raise_if_e2e_cancelled()

    target, resolve_note = await _resolve_kalaroko_game_entry_locator(
        page,
        click_selector=click_selector,
        scenario_name=scenario_name,
        scenario=scenario,
    )
    logger.info(
        "[kalaroko_monitor] 【%s】入口定位策略: %s",
        scenario_name,
        resolve_note,
    )

    try:
        cnt_raw = await page.locator(click_selector).count()
    except Exception:
        cnt_raw = -1
    logger.info("[kalaroko_monitor] 【%s】原始选择器命中节点数: %s", scenario_name, cnt_raw)

    _raise_if_e2e_cancelled()

    # 禁止 wait visible：Actionability 在动画/遮挡/轮播下可卡满默认级超时（数十秒）。
    scroll_cap = _env_int(
        "KALAROKO_GAME_ENTRY_SCROLL_MS", 2500, vmin=400, vmax=20000
    )
    await target.scroll_into_view_if_needed(timeout=scroll_cap)
    _raise_if_e2e_cancelled()
    attach_ms = _env_int("KALAROKO_GAME_ENTRY_ATTACH_MS", 900, vmin=0, vmax=8000)
    if attach_ms > 0:
        try:
            await target.wait_for(state="attached", timeout=attach_ms)
        except Exception:
            pass
    logger.info(
        "[kalaroko_monitor] 【%s】已完成 scroll_into_view + attached（无 visible 门闩）",
        scenario_name,
    )

    # 点击前人数文案：独立短超时 env，默认 0（不在此处硬等统计异步）
    stats_wait = _env_int(
        "KALAROKO_GAME_ENTRY_PRECLICK_STATS_MS", 0, vmin=0, vmax=12000
    )
    if stats_wait:
        if progress:
            progress(
                f"「{scenario_name}」等待大厅在线人数/统计延迟加载（{stats_wait}ms）…"
            )
        if await _abortable_wait_for_timeout(page, stats_wait):
            raise KalarokoE2EUserCancelled()

    bottom_reserve = _env_int("KALAROKO_BOTTOM_CHROME_RESERVE_PX", 112, vmin=56, vmax=240)
    suppress_tabbar = _env_bool("KALAROKO_SUPPRESS_TABBAR_PE", True)
    err_first: Exception | None = None
    tap_to = min(12000, max(1500, int(click_timeout_ms)))
    try:
        await _nudge_locator_clear_bottom_chrome(
            target, scenario_name=scenario_name, reserve_px=bottom_reserve
        )
        if suppress_tabbar:
            await _set_bottom_tabbar_pointer_events_enabled(page, suppress=True)

        _click_delay = 0
        if ui_pace_ms > 0:
            _click_delay = max(50, min(800, ui_pace_ms // 2))
            try:
                await page.bring_to_front()
            except Exception:
                pass
            if ui_cursor_moves:
                await _kalaroko_ui_move_mouse_to_locator(page, target, ui_pace_ms)
            await asyncio.sleep(min(1.2, ui_pace_ms / 1000.0 * 0.45))
        _clk_kw: dict[str, Any] = {
            "timeout": tap_to,
            "force": True,
            "no_wait_after": True,
        }
        if _click_delay > 0:
            _clk_kw["delay"] = _click_delay
        await target.click(**_clk_kw)
        logger.info(
            "[kalaroko_monitor] 【%s】Playwright force+no_wait_after click() 成功",
            scenario_name,
        )
    except Exception as e_click:
        err_first = e_click
        logger.warning(
            "[kalaroko_monitor] 【%s】首击 force+no_wait_after 仍失败，尝试更大避让后重试: %s",
            scenario_name,
            str(e_click)[:480],
        )
        try:
            await _nudge_locator_clear_bottom_chrome(
                target,
                scenario_name=scenario_name,
                reserve_px=min(240, bottom_reserve + 40),
            )
            _fto = max(3000, min(int(click_timeout_ms), 15000))
            _fcd = 0
            if ui_pace_ms > 0:
                _fcd = max(50, min(800, ui_pace_ms // 2))
                try:
                    await page.bring_to_front()
                except Exception:
                    pass
                if ui_cursor_moves:
                    await _kalaroko_ui_move_mouse_to_locator(page, target, ui_pace_ms)
                await asyncio.sleep(min(1.2, ui_pace_ms / 1000.0 * 0.45))
            _fkw: dict[str, Any] = {
                "timeout": min(_fto, tap_to),
                "force": True,
                "no_wait_after": True,
            }
            if _fcd > 0:
                _fkw["delay"] = _fcd
            await target.click(**_fkw)
            logger.info(
                "[kalaroko_monitor] 【%s】force click() 成功（绕开遮挡/命中语义）",
                scenario_name,
            )
        except Exception as e_force:
            logger.warning(
                "[kalaroko_monitor] 【%s】force click 仍失败: %s — JS 祖先兜底",
                scenario_name,
                str(e_force)[:320],
            )
            if progress:
                progress(f"「{scenario_name}」常规/force 点击失败，尝试卡片/链接层兜底 …")
            try:
                handle = await target.element_handle(
                    timeout=_env_int("KALAROKO_GAME_ENTRY_JS_FALLBACK_HANDLE_MS", 2000, vmin=400, vmax=15000)
                )
                if handle is None:
                    raise RuntimeError("element_handle 为空")
                await page.evaluate(
                    """(el) => {
                  const clickable = el.closest(
                    'a[href], button, [role="button"], [onclick], [data-href]'
                  );
                  let card = clickable;
                  if (!card) {
                    let n = el;
                    for (let i = 0; i < 12 && n; i++) {
                      const cls = (n.className && n.className.toString()) || '';
                      const tag = (n.tagName || '').toLowerCase();
                      if (tag === 'a' || tag === 'button') { card = n; break; }
                      if (/card|tile|game|item|banner|cover|wrap|cell|box/i.test(cls)) {
                        card = n;
                        break;
                      }
                      n = n.parentElement;
                    }
                  }
                  const target = card || el;
                  if (typeof target.click === 'function') target.click();
                  else target.dispatchEvent(
                    new MouseEvent('click', { bubbles: true, cancelable: true, view: window })
                  );
                }""",
                    handle,
                    timeout=_env_int(
                        "KALAROKO_GAME_ENTRY_JS_FALLBACK_EVAL_MS", 2500, vmin=400, vmax=12000
                    ),
                )
                logger.info(
                    "[kalaroko_monitor] 【%s】兜底：已在祖先/卡片节点触发 click",
                    scenario_name,
                )
            except Exception as e2:
                logger.error(
                    "[kalaroko_monitor] 【%s】兜底点击仍失败: %s",
                    scenario_name,
                    str(e2)[:480],
                )
                raise err_first from e2
    finally:
        if suppress_tabbar:
            await _set_bottom_tabbar_pointer_events_enabled(page, suppress=False)

    # 人数文案移到点击之后：避免 inner_text/evaluate 在点击前吃满 actionability 超时（曾达 ~26s）
    try:
        raw_combined = await _lobby_game_entry_text_for_players_hint(target)
        online_hint = _extract_online_players_hint(raw_combined)
        if not (online_hint.get("table") or online_hint.get("lobby")) and raw_combined:
            logger.debug(
                "[kalaroko_monitor] 【%s】人数文案未命中正则，卡片聚合文本前 240 字: %s",
                scenario_name,
                raw_combined[:240].replace("\n", " "),
            )
    except Exception:
        pass

    try:
        # 点击后不再 wait_for_timeout：竞速与指标采集侧承担 settle
        if _e2e_user_cancel_requested():
            raise KalarokoE2EUserCancelled()
        au = (page.url or "")[:320]
        logger.info("[kalaroko_monitor] 【%s】点击后短 settle，当前 url=%s", scenario_name, au)
        if progress:
            progress(f"「{scenario_name}」点击后 url: {au[:140]}…")
    except Exception:
        pass

    return online_hint


def _game_table_late_http_response_predicate(resp: Any) -> bool:
    """
    **晚期**牌桌/场景类 HTTP 命中（2xx）：排除大厅/登录/心跳等噪声，仅认更接近「上桌后」的 URL。
    """
    try:
        st = resp.status
        if st is None or int(st) < 200 or int(st) >= 300:
            return False
        u = (resp.url or "").lower()
        if not u:
            return False
        if any(
            x in u
            for x in (
                "google-analytics",
                "/g/collect",
                "googletagmanager",
                "doubleclick",
                "facebook.net",
            )
        ):
            return False
        # 排除：大厅 / 登录 / 鉴权 / 心跳 / ping 类（避免白屏阶段误命中）
        if "lobby" in u or "login" in u:
            return False
        if "heartbeat" in u:
            return False
        if "/auth" in u or "/oauth" in u or "auth/" in u or "/authorize" in u:
            return False
        if "/ping" in u or "ping?" in u or u.rstrip("/").endswith("/ping"):
            return False

        # 进桌后常见：URL 查询带 room_id（含 gweb iframe 内 game?...&room_id=）
        if "room_id=" in u or "room_id%3d" in u:
            return True
        # gweb 壳页：/game 或 game? 与 room 参数同现（避免仅靠早期空壳）
        if "gweb.kalaroko.com" in u and (
            "/game" in u or "game?" in u or "%2fgame" in u or "%2fgame%3f" in u
        ):
            return True

        positives = (
            "/table",
            "/seat",
            "/scene",
            "jointable",
            "join_table",
            "/game_info",
            "game_info",
            "room_info",
            "/room_info",
            "/enter",
            "/match",
            "/play",
            "/sync",
            "/room/enter",
            "/api/room",
            "room/enter",
            "/api/match",
            "/match/enter",
        )
        if any(p in u for p in positives):
            return True
        if "gweb.kalaroko.com" in u and any(p in u for p in positives):
            return True
        return False
    except Exception:
        return False


def _is_post_load_api(url: str) -> bool:
    """
    纯 Canvas 牌桌无可靠 DOM 时，用「牌桌渲染完毕后才出现」的 XHR/fetch 作为停表信号。

    刻意不包含 BI 埋点（如 ``event/batch``）：会在白屏/早期加载阶段触发，易误停表。
    仅认：声网语音上报、桌上成员列表等强业务晚期请求。
    """
    url_lower = (url or "").lower()
    post_load_keywords = (
        "agora.io/events/messages",
        "party/v1/party/member-list",
    )
    return any(kw in url_lower for kw in post_load_keywords)


# 游戏壳 iframe：优先 gweb / game-frame，再退回任意 iframe（穿透 DOM 嗅探）
_GAME_LATE_UI_IFRAME_SELECTOR = (
    "iframe[src*='gweb.kalaroko.com'], iframe[src*='gweb'], "
    "iframe[src*='game-frame'], iframe[src*='heronpro'], iframe"
)


async def _wait_for_late_game_ui(page: Any, *, deadline_perf: float | None) -> str:
    """
    晚期 DOM：优先「You can chat now」类聊天就绪文案，否则兜底聊天输入 / 设置 / 头像等壳上控件。

    **穿透 iframe**：依次在（1）gweb/game-frame 定向 ``frame_locator``、（2）**任意** ``iframe``、
    （3）主文档上查找，避免壳内 DOM 与大厅 DOM 混用导致永远不可见。
    ``deadline_perf``：perf_counter 上限，用于收缩各段 wait_for 的毫秒超时。
    """
    def _cap_ms(default_ms: int) -> int:
        if deadline_perf is None:
            return default_ms
        ms = int(max(250, (deadline_perf - time.perf_counter()) * 1000))
        return min(default_ms, ms)

    _fallback_sel = ".chat-input, .game-setting-btn, [class*='avatar']"
    # (label, frame_locator_first) — label 写入 Timeline；None 表示主文档
    _frame_tiers: list[tuple[str, Any]] = [
        ("iframe_gweb_shell", page.frame_locator(_GAME_LATE_UI_IFRAME_SELECTOR).first),
        ("iframe_any", page.frame_locator("iframe").first),
    ]

    for label, fl in _frame_tiers:
        try:
            tmo = _cap_ms(30_000)
            if tmo < 400:
                break
            await fl.get_by_text("You can chat now", exact=False).first.wait_for(
                state="visible", timeout=tmo
            )
            return f"ui_ready_{label}"
        except Exception:
            continue

    try:
        tmo = _cap_ms(30_000)
        if tmo >= 400:
            await page.get_by_text("You can chat now", exact=False).first.wait_for(
                state="visible", timeout=tmo
            )
            return "ui_ready_root"
    except Exception:
        pass

    for label, fl in _frame_tiers:
        try:
            tmo2 = _cap_ms(10_000)
            if tmo2 < 400:
                break
            await fl.locator(_fallback_sel).first.wait_for(state="visible", timeout=tmo2)
            return f"ui_ready_fallback_{label}"
        except Exception:
            continue

    try:
        tmo2 = _cap_ms(10_000)
        if tmo2 >= 400:
            await page.locator(_fallback_sel).first.wait_for(
                state="visible", timeout=tmo2
            )
            return "ui_ready_fallback_root"
    except Exception:
        pass

    raise RuntimeError("late_ui_not_found")


def _playwright_transient_eval_error(msg: str) -> bool:
    """子导航 / 切 frame 后常见的可恢复 evaluate 失败（应重试而非整轮失败）。"""
    es = (msg or "").lower()
    needles = (
        "execution context was destroyed",
        "cannot find context",
        "target closed",
        "frame was detached",
        "most likely because of a navigation",
        "navigation interrupted",
        "context was destroyed",
        "has been closed",
    )
    return any(n in es for n in needles)


async def _page_evaluate_nav_resilient(
    page: Any,
    expression: str,
    *,
    arg: Any | None = None,
    attempts: int | None = None,
) -> Any:
    """
    ``page.evaluate`` 与 SPA 导航竞态时上下文会被销毁；在 domcontentloaded settle 后多段退避重试。
    """
    n = attempts or _env_int("KALAROKO_PAGE_EVAL_NAV_RETRIES", 10, vmin=3, vmax=24)
    last: Exception | None = None
    for i in range(n):
        if _e2e_user_cancel_requested():
            raise KalarokoE2EUserCancelled()
        try:
            if i == 0:
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    pass
            if arg is not None:
                return await page.evaluate(expression, arg)
            return await page.evaluate(expression)
        except Exception as e:
            last = e
            if not _playwright_transient_eval_error(str(e)):
                raise
            await asyncio.sleep(min(2.0, 0.1 + 0.2 * i + 0.04 * (i * i)))
    assert last is not None
    raise last


async def _page_in_game_frame_shell(page: Any) -> bool:
    """
    是否已进入游戏壳上下文：主文档 URL 含 game-frame，或大厅内已挂载指向 game-frame 的 iframe
    （壳常先于主文档 URL 更新完成，避免死等 load / 仅靠 URL 轮询）。
    """
    try:
        if "game-frame" in (page.url or "").lower():
            return True
    except Exception:
        pass
    try:
        return bool(
            await _page_evaluate_nav_resilient(
                page,
                """() => {
          const hs = (s) => String(s || '').toLowerCase().includes('game-frame');
          if (hs(location.href)) return true;
          for (const el of document.querySelectorAll('iframe[src]')) {
            if (hs(el.getAttribute('src'))) return true;
          }
          return false;
        }""",
                attempts=_env_int("KALAROKO_SHELL_EVAL_RETRIES", 8, vmin=2, vmax=20),
            )
        )
    except Exception:
        return False


def _silently_detach_ui_race_task(task: asyncio.Task) -> None:
    """
    竞速 ``finally`` 中不 ``await`` UI Task 时，用回调取回 outcome，避免
    ``Task exception was never retrieved``；吞掉取消与 Playwright 侧残余异常。
    """
    try:
        if task.cancelled():
            return
        _ = task.exception()
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


async def _game_deep_wait_after_goto(
    page: Any,
    t_start: float,
    timeout_ms: int,
    ws_times: list[float],
    *,
    click_flow: bool = False,
    timeline_mark: Callable[[str], None] | None = None,
) -> tuple[float, bool, str]:
    """
    goto / 点击进游戏后：**晚期就绪竞速**（截取 ``t_end`` 用于 ``real_engine_load_ms``）。

    不再将「首条 WebSocket」「早期宽松 HTTP」「短时 Canvas」视为就绪，避免白屏/进度条阶段即结束。

    - **阶段 1（仅 click_flow）**：轮询直至进入 ``game-frame`` 壳或 ``KALAROKO_GAME_FRAME_POLL_MS`` 用尽。
    - **阶段 2**：**晚期 UI DOM**（聊天就绪文案或兜底控件）或 **后置遥测 XHR**（见 ``_is_post_load_api``，
      仅 2xx）任一先命中即截取 ``t_end``；其它 HTTP 仍仅写入排障列表（``_on_late_http_trace_only``），
      避免白屏阶段 ``game_info``/``room_info`` 等泛 URL 误停表。
    - **预算**：``min(t_start + timeout_ms, now + KALAROKO_GAME_LATE_READY_RACE_MS)``（默认 80s），可配。
    - **Timeout 排障**：XHR/Fetch 窃听 + 晚期 HTTP URL 样本写入 Timeline，便于对照真实进桌接口。
    - **事件循环**：阶段 2 使用 ``asyncio.wait(..., timeout=…)`` 轮询（默认片约 120ms，``KALAROKO_LATE_RACE_POLL_TICK_MS``），
      避免无 ``await`` 的紧轮询；``finally`` 内仅 ``cancel`` UI 子任务并 ``add_done_callback`` 静默收尾，
      **绝不** ``await`` 该 Task（否则 HTTP 先胜出时可能被 Playwright 内阻塞拖死整段协程）。

    ``ws_times`` 仍由调用方注册 websocket 监听，本函数**不再**以其作为竞速胜利条件。

    返回 ``(t_end_perf, canvas_seen, race_end_reason)``；``race_end_reason`` 为 ``post_load_api``、``late_ui`` 或 ``timeout``；
    ``canvas_seen`` 恒为 ``False``（保留签名兼容）。
    """
    _ = ws_times  # 保留参数：调用方仍注册 WS 用于排障，不再作为竞速胜利条件
    global_deadline = t_start + max(0.001, timeout_ms / 1000.0)
    race_cap_ms = _env_int(
        "KALAROKO_GAME_LATE_READY_RACE_MS", 80_000, vmin=15_000, vmax=120_000
    )
    race_deadline = min(global_deadline, time.perf_counter() + race_cap_ms / 1000.0)

    if _e2e_user_cancel_requested():
        raise KalarokoE2EUserCancelled()

    click_shell_seen = not click_flow
    phase1_cap_ms = _env_int(
        "KALAROKO_GAME_FRAME_POLL_MS", 8000, vmin=1500, vmax=30_000
    )
    shell_timeline_logged = False
    if click_flow:
        p1_end = min(race_deadline, time.perf_counter() + phase1_cap_ms / 1000.0)
        while time.perf_counter() < p1_end:
            if _e2e_user_cancel_requested():
                raise KalarokoE2EUserCancelled()
            try:
                if await _page_in_game_frame_shell(page):
                    click_shell_seen = True
                    if timeline_mark and not shell_timeline_logged:
                        timeline_mark("检测到 game-frame 壳 (主文档 URL 或 iframe src)")
                        shell_timeline_logged = True
                    break
            except Exception:
                pass
            await asyncio.sleep(0.05)

    late_http_trace: list[str] = []
    captured_apis: list[str] = []
    post_load_api_ready = asyncio.Event()
    post_load_hit_url: list[str] = []

    def _debug_on_response(response: Any) -> None:
        """记录业务 XHR/fetch；命中后置遥测白名单且 2xx 时用于竞速停表。"""
        try:
            req = response.request
            rt = (getattr(req, "resource_type", None) or "").lower()
            if rt not in ("fetch", "xhr"):
                return
            url = (response.url or "").strip()
            if not url:
                return
            if _is_post_load_api(url):
                ok = True
                try:
                    st = response.status
                    if st is not None and (int(st) < 200 or int(st) >= 300):
                        ok = False
                except Exception:
                    pass
                if ok:
                    if not post_load_hit_url:
                        post_load_hit_url.append(url[:800])
                    post_load_api_ready.set()
            low = url.lower()
            static_ext = (
                ".png",
                ".jpg",
                ".jpeg",
                ".mp3",
                ".webm",
                ".webp",
                ".woff",
                ".woff2",
                ".gif",
                ".svg",
                ".ico",
                ".m4a",
            )
            if any(ext in low for ext in static_ext):
                return
            captured_apis.append(url[:800])
            if len(captured_apis) > 500:
                del captured_apis[:250]
        except Exception:
            pass

    def _on_late_http_trace_only(resp: Any) -> None:
        """晚期 HTTP 仅排障记录，绝不触发停表。"""
        try:
            if not _game_table_late_http_response_predicate(resp):
                return
            u = (resp.url or "").strip()[:500]
            if not u:
                return
            if len(late_http_trace) < 12 and (not late_http_trace or late_http_trace[-1] != u):
                late_http_trace.append(u)
        except Exception:
            pass

    ui_task: asyncio.Task | None = None
    try:
        try:
            page.on("response", _debug_on_response)
            page.on("response", _on_late_http_trace_only)
        except Exception:
            pass

        if timeline_mark:
            timeline_mark(
                "进入晚期就绪竞速 (Race: **晚期 UI DOM** 或 **后置遥测 XHR 白名单** 可停表；其余 HTTP 仅排障)"
            )

        ui_task = asyncio.create_task(
            _wait_for_late_game_ui(page, deadline_perf=race_deadline)
        )
        ui_branch_exhausted = False
        # 每轮至少 ``await`` 一次，避免紧轮询占满 CPU；用 ``asyncio.wait`` 将「让出事件循环」与「等 UI Task」合一
        _tick_ms = _env_int(
            "KALAROKO_LATE_RACE_POLL_TICK_MS", 120, vmin=50, vmax=500
        )
        _tick_sec = max(0.05, min(0.5, _tick_ms / 1000.0))

        while time.perf_counter() < race_deadline:
            if _e2e_user_cancel_requested():
                raise KalarokoE2EUserCancelled()

            _rem = race_deadline - time.perf_counter()
            if _rem <= 0:
                break
            _slice = min(_tick_sec, max(0.02, _rem))
            await asyncio.wait(
                {ui_task},
                timeout=_slice,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if post_load_api_ready.is_set():
                t_hit = time.perf_counter()
                if timeline_mark:
                    extra = ""
                    if post_load_hit_url:
                        u0 = post_load_hit_url[0].replace("`", "'")
                        extra = f" | 首个命中: `{u0[:280]}`"
                    timeline_mark(
                        "竞速结束: 终极后置 API 命中 (Agora 语音 / 成员列表就绪，牌桌已展现)"
                        + extra
                    )
                return t_hit, False, "post_load_api"

            if ui_task.done() and not ui_branch_exhausted:
                try:
                    ui_reason = ui_task.result()
                    t_hit = time.perf_counter()
                    if timeline_mark:
                        timeline_mark(
                            f"竞速结束: 晚期 UI ({ui_reason})"
                        )
                    return t_hit, False, "late_ui"
                except asyncio.CancelledError:
                    ui_branch_exhausted = True
                except Exception:
                    ui_branch_exhausted = True
                    if timeline_mark:
                        timeline_mark(
                            "晚期 UI 未命中（主文案与兜底选择器均未就绪），继续等待 UI 或总超时"
                        )

            if click_flow and not click_shell_seen:
                try:
                    click_shell_seen = await _page_in_game_frame_shell(page)
                except Exception:
                    click_shell_seen = False
                if (
                    click_shell_seen
                    and timeline_mark
                    and not shell_timeline_logged
                ):
                    timeline_mark(
                        "检测到 game-frame 壳 (主文档 URL 或 iframe src)"
                    )
                    shell_timeline_logged = True

        t_out = min(time.perf_counter(), race_deadline)
        if timeline_mark:
            last_apis = captured_apis[-5:] if captured_apis else []
            apis_repr = repr(last_apis) if last_apis else "[]"
            if len(apis_repr) > 2000:
                apis_repr = apis_repr[:2000] + "…"
            http_tail = ""
            if late_http_trace:
                ht = repr(late_http_trace[-3:])
                if len(ht) > 900:
                    ht = ht[:900] + "…"
                http_tail = f" | 排障·晚期HTTP样本(≤3): {ht}"
            timeline_mark(
                "竞速结束: Timeout 兜底 (晚期 UI 与后置遥测 API 均未就绪). "
                f"末批 XHR/Fetch(≤5): {apis_repr}{http_tail}"
            )
        return t_out, False, "timeout"
    finally:
        for _fn in (_on_late_http_trace_only, _debug_on_response):
            try:
                page.remove_listener("response", _fn)
            except Exception:
                pass
        # Fire-and-forget：勿 await UI Task（post_load 先返回时，await 可能长时间阻塞 → 死锁）
        if ui_task is not None:
            if not ui_task.done():
                ui_task.cancel()
            ui_task.add_done_callback(_silently_detach_ui_race_task)


async def _evaluate_metrics_with_retry(page: Any) -> Any:
    """
    game-frame 等页面可能在 domcontentloaded 后仍发生子导航，导致 evaluate 时上下文被销毁；
    短暂 settle + ``_page_evaluate_nav_resilient`` 多段重试，避免单场景误报失败。
    """
    if await _abortable_wait_for_timeout(page, 450):
        raise KalarokoE2EUserCancelled()
    if _e2e_user_cancel_requested():
        raise KalarokoE2EUserCancelled()
    return await _page_evaluate_nav_resilient(page, _METRICS_JS)


def _coerce_scenarios(scenarios: Any) -> tuple[list[dict[str, Any]], bool]:
    """返回 (场景列表, 是否使用了内置默认)."""
    if scenarios is None or (isinstance(scenarios, list) and len(scenarios) == 0):
        return [dict(s) for s in KALAROKO_DEFAULT_SCENARIOS], True
    if not isinstance(scenarios, list):
        return [], False
    return [dict(x) if isinstance(x, dict) else {} for x in scenarios], False
_DEFAULT_JSONL = Path(os.environ.get("KALAROKO_PERF_HISTORY_PATH", "")) if os.environ.get(
    "KALAROKO_PERF_HISTORY_PATH"
) else (Path.home() / ".jachin" / "kalaroko_perf" / "history.jsonl")

_ALLOWED_HOSTS_ENV = "KALAROKO_MONITOR_ALLOWED_HOSTS"
_DEFAULT_ALLOWED = frozenset({"kalaroko.com", "www.kalaroko.com", "gwp.heronpro.xin"})


def _allowed_hosts() -> frozenset[str]:
    raw = os.environ.get(_ALLOWED_HOSTS_ENV, "")
    if not raw.strip():
        return _DEFAULT_ALLOWED
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


def _host_allowed(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host in _allowed_hosts()


def _page_is_kalaroko(page: Any) -> bool:
    try:
        host = (urlparse(page.url or "").hostname or "").lower()
        return bool(host and host.endswith("kalaroko.com"))
    except Exception:
        return False


async def _dismiss_kalaroko_blocking_promos(
    page: Any,
    *,
    progress: Callable[[str], None] | None = None,
    click_timeout_ms: int = 5000,
) -> int:
    """
    关闭挡在 Login / 游戏入口之上的推广层：
    - 「开启通知领奖励」类横条：优先点 **Cancel**（避免 Agree 触发系统通知权限框）。
    - subscribers-modal-*：优先点 Cancel / Later / Close，否则 Escape。
    无遮挡或找不到控件时静默返回 0。
    """
    if not _page_is_kalaroko(page):
        return 0
    clk = max(800, min(int(click_timeout_ms), 15000))
    dismissed = 0

    async def _try_click(loc: Any, label: str) -> bool:
        nonlocal dismissed
        try:
            if await loc.count() <= 0:
                return False
            await loc.first.click(timeout=clk, no_wait_after=True)
            dismissed += 1
            logger.info("[kalaroko_monitor] %s", label)
            if progress:
                progress(label)
            await page.wait_for_timeout(150)
            return True
        except Exception:
            return False

    # Escape 对部分模态/抽屉有效
    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(220)
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(180)
    except Exception:
        pass

    # --- 通知推广条（文案见产品 UI）：在包含文案的容器内点 Cancel，避免点到别处的 Cancel ---
    try:
        promo = page.locator("div").filter(
            has_text=re.compile(
                r"Turn\s+on\s+notifications|exclusive\s+bonus|notifications\s+to\s+claim",
                re.I,
            )
        ).first
        if await promo.count() > 0:
            try:
                await promo.wait_for(state="visible", timeout=1600)
                cancel_bar = promo.get_by_role(
                    "button", name=re.compile(r"^Cancel$", re.I)
                ).first
                await _try_click(
                    cancel_bar,
                    "已关闭通知推广条（Cancel，避免挡 Guest/游戏点击）",
                )
            except Exception:
                pass
    except Exception:
        pass

    # --- subscribers 订阅/转化模态（曾导致 intercepts pointer events）---
    try:
        overlay = page.locator(
            ".subscribers-modal-overlay, [class*='subscribers-modal-overlay']"
        ).first
        ov_vis = False
        try:
            ov_vis = await overlay.is_visible(timeout=900)
        except Exception:
            ov_vis = False
        if ov_vis:
            modal_root = page.locator("[class*='subscribers-modal']").first
            container: Any = (
                modal_root if await modal_root.count() > 0 else page
            )
            clicked = False
            for pat in (
                r"^Cancel$",
                r"Maybe\s+Later",
                r"Not\s+Now",
                r"^Skip$",
                r"^Close$",
                r"^No\s+thanks?$",
            ):
                try:
                    btn = container.get_by_role(
                        "button", name=re.compile(pat, re.I)
                    ).first
                    if await btn.count() > 0:
                        if await _try_click(
                            btn,
                            f"已关闭 subscribers 模态（{pat}）",
                        ):
                            clicked = True
                            break
                except Exception:
                    continue
            if not clicked:
                try:
                    await page.keyboard.press("Escape")
                    await page.wait_for_timeout(260)
                    dismissed += 1
                    logger.info("[kalaroko_monitor] subscribers 模态：已发送 Escape")
                    if progress:
                        progress("subscribers 遮挡：已尝试 Escape 关闭")
                except Exception:
                    pass
    except Exception:
        pass

    return dismissed


async def _dismiss_kalaroko_resume_session_popup(
    page: Any,
    *,
    progress: Callable[[str], None] | None = None,
    click_timeout_ms: int = 12000,
) -> bool:
    """
    从牌局返回大厅后，站点可能弹出「意外掉线 / 回来继续玩」类 **CenterPopup**（如 ``adm-center-popup``），
    需点 **Enter** 才会真正结束上一局会话，否则主 CTA 被 ``_popup_box_ssyfx_13`` 拦截，无法点下一款游戏。

    有则点一次 Enter 并打日志；无则快速返回 False（不当作错误）。
    """
    if not _page_is_kalaroko(page):
        return False
    clk = max(1000, min(int(click_timeout_ms), 30000))
    # 0) 宽匹配优先：任意可见 adm-center-popup 内出现 Enter/Continue 即点掉（文案变体未命中下面正则时仍挡点击）
    try:
        wide = page.locator("div.adm-center-popup").filter(
            has=page.get_by_role("button", name=re.compile(r"^(Enter|Continue)$", re.I))
        )
        nw = await wide.count()
        for wi in range(min(nw, 4)):
            shell_w = wide.nth(wi)
            try:
                vis = await shell_w.is_visible(timeout=900)
                if not vis:
                    continue
                enter_w = shell_w.get_by_role(
                    "button", name=re.compile(r"^(Enter|Continue)$", re.I)
                ).first
                await enter_w.wait_for(state="visible", timeout=1400)
                if progress:
                    progress("检测到遮挡弹窗（Enter/Continue），点击解除 …")
                await enter_w.click(timeout=clk, no_wait_after=True)
                await page.wait_for_timeout(200)
                logger.info(
                    "[kalaroko_monitor] 已关闭 adm-center-popup（Enter/Continue，宽匹配 #%s）",
                    wi,
                )
                return True
            except Exception:
                continue
    except Exception:
        pass

    # 1) 文案 + adm-center-popup；兜底：同弹层内必须有主按钮 Enter（避免点到别的 Enter）
    try:
        shell = page.locator("div.adm-center-popup").filter(
            has_text=re.compile(
                r"drop\s*out|come\s*back|accidentally|keep\s*playing|beat\s*me|preparing\s*:\s*\d",
                re.I,
            )
        )
        if await shell.count() < 1:
            shell = (
                page.locator(
                    "div.adm-center-popup",
                    has=page.get_by_role(
                        "button",
                        name=re.compile(r"^Enter$", re.I),
                    ),
                ).filter(
                    has_text=re.compile(
                        r"drop|accidentally|come\s+back|playing|session|beat|preparing",
                        re.I,
                    )
                )
            )
        if await shell.count() < 1:
            return False
        first = shell.first
        await first.wait_for(state="visible", timeout=2500)
        enter_btn = first.get_by_role(
            "button",
            name=re.compile(r"^(Enter|Continue)$", re.I),
        ).first
        try:
            await enter_btn.wait_for(state="visible", timeout=1500)
        except Exception:
            enter_btn = first.locator("button, [role='button']").filter(
                has_text=re.compile(r"^Enter$", re.I)
            ).first
            await enter_btn.wait_for(state="visible", timeout=1500)
        if progress:
            progress("检测到「继续上一局 / 掉线重连」弹窗，点击 Enter 解除遮挡 …")
        await enter_btn.click(timeout=clk, no_wait_after=True)
        await page.wait_for_timeout(220)
        logger.info("[kalaroko_monitor] 已关闭掉线重进弹窗（Enter），大厅恢复可点击")
        return True
    except Exception as e:
        if _is_locator_timeout_err(e):
            return False
        logger.debug("[kalaroko_monitor] resume-session popup 探测结束（非超时）: %s", str(e)[:280])
        return False


async def _dismiss_kalaroko_ghost_session_overlay(
    page: Any,
    *,
    progress: Callable[[str], None] | None = None,
) -> bool:
    """
    核弹撤离后主文档回到大厅时可能出现的「幽灵会话」断线遮罩：
    先物理/UI 关闭（Exit、边角点击、ESC），再用 JS **抑制**遮罩（仅 ``display/pointer-events``，
    **禁止** ``Node.remove()``）。后者会从 DOM 粗暴摘除节点，导致 React 下一帧在
    ``removeChild`` 时抛 ``NotFoundError``（节点已不在父树下）。

    Returns:
        - 首轮未检测到特征文案 → ``False``（确无此类弹窗）。
        - **只要检测到并已介入处理** → 固定 ``True``，通知外层清场循环继续复扫，
          避免因复检仍可见而过早 ``break``。
        - 顶层异常 → ``False``。
    """
    if not _page_is_kalaroko(page):
        return False
    try:
        reconnect_locator = page.locator(
            r"text=/drop\s*out|come\s*back|resume|drop out|accidentally|keep\s*playing/i"
        ).first

        try:
            if not await reconnect_locator.is_visible(timeout=1500):
                return False
        except Exception:
            return False

        if progress:
            progress("拦截到幽灵会话弹窗，启动物理与 DOM 双重抹除…")

        exit_btn = (
            page.locator("button, div[role='button']")
            .filter(has_text=re.compile(r"^Exit$", re.I))
            .first
        )
        try:
            if await exit_btn.is_visible(timeout=500):
                await exit_btn.click(force=True, no_wait_after=True)
                await page.wait_for_timeout(280)
        except Exception:
            pass

        try:
            if await reconnect_locator.is_visible(timeout=500):
                await page.mouse.click(5, 150)
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(500)
        except Exception:
            pass

        try:
            if await reconnect_locator.is_visible(timeout=500):
                if progress:
                    progress(
                        "常规点击失效，执行遮罩抑制（不卸载节点，避免 React removeChild 崩溃）…"
                    )
                await page.evaluate(
                    """() => {
                        const sel = (
                          '.adm-center-popup, .adm-mask, .adm-center-popup-wrap, '
                          + '.adm-popup, [class*="adm-mask"], '
                          + '[class*="center-popup"], [class*="CenterPopup"]'
                        );
                        document.querySelectorAll(sel).forEach((el) => {
                          try {
                            el.style.setProperty('display', 'none', 'important');
                            el.style.setProperty('visibility', 'hidden', 'important');
                            el.style.setProperty('pointer-events', 'none', 'important');
                            el.style.setProperty('opacity', '0', 'important');
                            el.setAttribute('data-kalaroko-monitor-overlay-suppressed', '1');
                          } catch (e) {}
                        });
                    }"""
                )
                await page.wait_for_timeout(500)
        except Exception:
            pass

        gone = False
        try:
            gone = not await reconnect_locator.is_visible(timeout=700)
        except Exception:
            gone = True
        if gone:
            logger.info("[kalaroko_monitor] 幽灵会话/断线遮罩：已关闭或已从视口移除")
        else:
            logger.warning(
                "[kalaroko_monitor] 幽灵会话/断线遮罩：抑制后文案仍可见（可能需产品侧关闭流程）"
            )

        return True
    except Exception as e:
        logger.debug(
            "[kalaroko_monitor] 处理幽灵会话异常: %s",
            str(e)[:320],
        )
        return False


async def _prepare_kalaroko_lobby_after_navigation(
    page: Any,
    *,
    progress: Callable[[str], None] | None = None,
) -> bool:
    """
    升级版大厅清场：使用多轮动态扫荡，对付延迟渲染和多层叠加的弹窗/遮罩。

    每轮顺序：推广/通知条与 subscribers → Guest 登录 → 掉线 Enter(Resume) → 幽灵会话遮罩；
    若本轮消除过任一障碍，短暂 settle 后复扫（最多 3 轮），最后再短等让 DOM 稳定。
    墙钟预算显著收紧（相对旧版 2.5s+ 多轮 1.5s），避免阻塞游戏入口竞速。

    返回：是否曾在任一轮成功点击过 Continue with Guest（无登录框时 False）。
    """
    if progress:
        progress("开始大厅环境动态清场…")

    await page.wait_for_timeout(
        _env_int("KALAROKO_LOBBY_PREP_INITIAL_MS", 900, vmin=200, vmax=3000)
    )

    guest_clicked = False
    max_sweeps = 3
    for sweep in range(max_sweeps):
        cleared_something = False

        promo_n = await _dismiss_kalaroko_blocking_promos(page, progress=progress)
        if promo_n > 0:
            cleared_something = True

        if await _dismiss_kalaroko_guest_login_modal(page, progress=progress):
            cleared_something = True
            guest_clicked = True

        if await _dismiss_kalaroko_resume_session_popup(page, progress=progress):
            cleared_something = True

        if await _dismiss_kalaroko_ghost_session_overlay(page, progress=progress):
            cleared_something = True

        if cleared_something:
            if progress:
                settle_ms = _env_int(
                    "KALAROKO_LOBBY_PREP_SWEEP_SETTLE_MS", 650, vmin=200, vmax=2500
                )
                progress(
                    f"第 {sweep + 1} 轮清场消灭了弹窗/遮罩，等待 {settle_ms}ms 后复扫…"
                )
            await page.wait_for_timeout(
                _env_int(
                    "KALAROKO_LOBBY_PREP_SWEEP_SETTLE_MS", 650, vmin=200, vmax=2500
                )
            )
        else:
            if progress:
                progress(f"第 {sweep + 1} 轮扫描大厅干净，清场完毕。")
            break

    await page.wait_for_timeout(
        _env_int("KALAROKO_LOBBY_PREP_FINAL_MS", 400, vmin=0, vmax=2000)
    )
    return guest_clicked


def _is_locator_timeout_err(e: BaseException) -> bool:
    """Playwright Locator.wait_for / click 超时（无 Guest 弹窗时为预期路径，勿当异常告警）。"""
    n = type(e).__name__
    if n == "TimeoutError":
        return True
    if "Timeout" in n:
        return True
    s = str(e).lower()
    return "timeout" in s and "exceeded" in s


async def _dismiss_kalaroko_guest_login_modal(
    page: Any,
    *,
    progress: Callable[[str], None] | None = None,
    visible_timeout_ms: int = 600,
    click_timeout_ms: int = 12000,
) -> bool:
    """
    未导出 auth.json / 无登录 Cookie 时，大厅常见 Login 遮罩。
    用 **短超时** 探测「Continue with Guest」；有则点击，无则立刻返回 False，
    交给后续流程直接点游戏入口与采集（不在此长时间阻塞）。
    """
    vis = max(200, min(int(visible_timeout_ms), 15000))
    clk = max(1000, min(int(click_timeout_ms), 60000))

    async def _try_click(locator: Any) -> bool:
        try:
            await locator.wait_for(state="visible", timeout=vis)
        except Exception:
            return False
        try:
            if progress:
                progress("检测到登录弹窗，正在点击 Continue with Guest …")
            await locator.click(timeout=clk, no_wait_after=True)
            await page.wait_for_timeout(200)
            if progress:
                progress("已以访客身份关闭登录弹窗，继续点击游戏入口 …")
            logger.info("[kalaroko_monitor] 已点击 Continue with Guest（访客入口）")
            return True
        except Exception as e:
            logger.warning(
                "[kalaroko_monitor] Continue with Guest 点击失败: %s",
                str(e)[:400],
            )
            return False

    if not _page_is_kalaroko(page):
        return False

    # 1) 无障碍语义：按钮名「Continue with Guest」（与线上 UI 一致）
    try:
        role_btn = page.get_by_role(
            "button",
            name=re.compile(r"Continue\s+with\s+Guest", re.I),
        ).first
        if await _try_click(role_btn):
            return True
    except Exception:
        pass

    # 2) 兜底：<button> 内含文案（含嵌套 span）
    try:
        txt_btn = page.locator("button").filter(
            has_text=re.compile(r"Continue\s+with\s+Guest", re.I),
        ).first
        if await _try_click(txt_btn):
            return True
    except Exception:
        pass

    # 3) Playwright text= 引擎匹配
    try:
        legacy = page.locator("text=/Continue\\s+with\\s+Guest/i").first
        if await _try_click(legacy):
            return True
    except Exception:
        pass

    # 4) get_by_text（覆盖 div span 堆叠、`button` 语义缺失的控件）
    try:
        gt = page.get_by_text(re.compile(r"Continue\s+with\s+Guest", re.I)).first
        if await _try_click(gt):
            return True
    except Exception:
        pass

    # 5) force：可见但被其它层短时间挡住时（同样用短超时 wait，避免无弹窗时耗满整段预算）
    try:
        legacy = page.locator("text=/Continue\\s+with\\s+Guest/i").first
        await legacy.wait_for(state="visible", timeout=vis)
        if progress:
            progress("Continue with Guest：尝试 force 点击 …")
        await legacy.click(timeout=clk, force=True, no_wait_after=True)
        await page.wait_for_timeout(200)
        if progress:
            progress("已以访客身份关闭登录弹窗（force），继续 …")
        logger.info("[kalaroko_monitor] 已点击 Continue with Guest（force）")
        return True
    except Exception as e:
        if _is_locator_timeout_err(e):
            logger.info(
                "[kalaroko_monitor] 未检测到 Continue with Guest（短超时），跳过访客入口，继续游戏点击/采集",
            )
        else:
            logger.warning(
                "[kalaroko_monitor] Continue with Guest force 点击失败: %s",
                str(e)[:400],
            )

    return False


async def _kalaroko_click_in_game_top_right_hud(
    page: Any,
    *,
    progress: Callable[[str], None] | None = None,
) -> bool:
    """
    牌桌/引擎内 HUD：**不在** ``<header>`` 里，而在 Canvas/React 浮层上的圆形按钮（齿轮下左箭头退出等）。
    用视口 **右上区域 + 小尺寸按钮** 的几何探测点击，避免旧逻辑只扫 ``header`` 导致 n=0 后直接只剩 ``goto``。
    """
    try:
        phase = await page.evaluate(
            """() => {
              const vw = window.innerWidth;
              const vh = window.innerHeight;
              const pick = () => Array.from(
                document.querySelectorAll('button, [role="button"], a[href]')
              ).filter(el => el.offsetParent !== null);
              const inTopRightBand = (el) => {
                const r = el.getBoundingClientRect();
                return r.right >= vw * 0.62 && r.top <= Math.min(320, vh * 0.42)
                  && r.width <= 110 && r.height <= 110;
              };
              const band = pick().filter(inTopRightBand);
              if (band.length === 0) return { ok: false, mode: 'empty' };
              band.sort(
                (a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top
              );
              // 纵向堆叠：上 = 齿轮/KK，下 = 左箭头退出（点最下更可能直接退房）
              if (band.length >= 2) {
                const topBtn = band[0];
                const bottomBtn = band[band.length - 1];
                topBtn.click();
                return {
                  ok: true,
                  mode: 'two-step-first',
                  stack: band.length,
                };
              }
              band[0].click();
              return { ok: true, mode: 'single', stack: 1 };
            }"""
        )
        if isinstance(phase, dict) and phase.get("ok"):
            logger.info(
                "[kalaroko_monitor] 战术撤离 HUD：右上小按钮簇 mode=%s stack=%s（已点首击/单机）",
                phase.get("mode"),
                phase.get("stack"),
            )
            if progress:
                progress("战术撤离：已点牌桌右上角控件（HUD 几何）…")
            await page.wait_for_timeout(520)
            # 两枚堆叠时补第二击：点簇内 **最靠下** 一枚（常为左箭头退出）
            if isinstance(phase, dict) and phase.get("mode") == "two-step-first":
                await page.evaluate(
                    """() => {
                      const vw = window.innerWidth;
                      const vh = window.innerHeight;
                      const pick = () => Array.from(
                        document.querySelectorAll('button, [role="button"], a[href]')
                      ).filter(el => el.offsetParent !== null);
                      const inTopRightBand = (el) => {
                        const r = el.getBoundingClientRect();
                        return r.right >= vw * 0.62 && r.top <= Math.min(360, vh * 0.5)
                          && r.width <= 110 && r.height <= 110;
                      };
                      const band = pick().filter(inTopRightBand);
                      if (!band.length) return;
                      band.sort(
                        (a, b) =>
                          b.getBoundingClientRect().top - a.getBoundingClientRect().top
                      );
                      band[0].click();
                    }"""
                )
                logger.info("[kalaroko_monitor] 战术撤离 HUD：已补第二击（簇内最下/退出向）")
                if progress:
                    progress("战术撤离：已点右上角第二枚控件（退出向）…")
                await page.wait_for_timeout(480)
            return True
    except Exception as e:
        logger.debug("[kalaroko_monitor] HUD 几何退出未执行: %s", str(e)[:200])
    return False


async def _kalaroko_exit_game_via_top_bar(
    page: Any,
    *,
    progress: Callable[[str], None] | None = None,
    step_timeout_ms: int = 4500,
) -> bool:
    """
    在游戏壳（URL 含 ``game-frame``）内结束对局会话，再回大厅；**禁止**仅依赖末尾 ``goto``（老方法）。

    顺序：
    1. **全页** 语义 / aria「退出、返回大厅、Back」等（不限 ``header``）；
    2. **牌桌 HUD**：右上区域小圆形按钮几何点击（齿轮/箭头；两枚时先上后下），覆盖 **不进 <header>** 的引擎 UI；
    3. **大厅壳** 兜底：``header`` / ``banner`` 最右与倒数第二钮（KK 条，与旧逻辑兼容）。

    可用 ``KALAROKO_GAME_EXIT_SKIP_UI=1`` 跳过（仅调试）。
    """
    raw_skip = (os.environ.get("KALAROKO_GAME_EXIT_SKIP_UI") or "").strip().lower()
    if raw_skip in ("1", "true", "yes", "on"):
        logger.info("[kalaroko_monitor] 已设置 KALAROKO_GAME_EXIT_SKIP_UI，跳过游戏内顶栏退出")
        return False

    if not _page_is_kalaroko(page):
        return False
    try:
        url_l = (page.url or "").lower()
    except Exception:
        url_l = ""
    if "game-frame" not in url_l:
        logger.debug("[kalaroko_monitor] 非 game-frame，跳过顶栏游戏退出 UI")
        return False

    clk = max(1200, min(int(step_timeout_ms), 15000))
    tried_exit = False

    try:
        # —— 1) 全页语义退出（牌桌内按钮常不在 header）——
        exit_chain = [
            page.get_by_role(
                "button",
                name=re.compile(
                    r"exit|quit|leave|logout|back|lobby|hall|home|返回|退出|离开|大厅",
                    re.I,
                ),
            ),
            page.locator(
                'button[aria-label*="exit" i], button[aria-label*="quit" i], '
                'button[aria-label*="leave" i], button[aria-label*="back" i], '
                '[role="button"][aria-label*="退出" i]'
            ),
            page.locator('a[aria-label*="exit" i], a[aria-label*="leave" i]'),
        ]
        if progress:
            progress("战术撤离：全页探测退出/返回按钮 …")
        for ex_loc in exit_chain:
            try:
                if await ex_loc.count() < 1:
                    continue
                await ex_loc.first.scroll_into_view_if_needed(timeout=clk)
                await ex_loc.first.click(timeout=clk, no_wait_after=True)
                tried_exit = True
                logger.info("[kalaroko_monitor] 战术撤离 UI：已通过全页语义/aria 命中退出")
                if progress:
                    progress("战术撤离：已点语义化退出控件 …")
                break
            except Exception:
                continue

        # —— 2) 牌桌右上 HUD 几何（第二张图：齿轮下左箭头；不在 header）——
        if not tried_exit:
            if progress:
                progress("战术撤离：尝试牌桌右上角图标簇（HUD 几何）…")
            if await _kalaroko_click_in_game_top_right_hud(page, progress=progress):
                tried_exit = True

        # —— 3) 大厅壳：legacy header / banner（KK 条）——
        hdr_btns = page.locator('header button, [role="banner"] button')
        n = await hdr_btns.count()
        logger.info(
            "[kalaroko_monitor] 战术撤离 UI：header/banner 内 button 数=%s（大厅壳兜底）",
            n,
        )
        if not tried_exit and n >= 1:
            try:
                await hdr_btns.nth(n - 1).scroll_into_view_if_needed(timeout=clk)
                await hdr_btns.nth(n - 1).click(timeout=clk, no_wait_after=True)
                logger.info("[kalaroko_monitor] 战术撤离 UI：已点顶栏最右侧按钮（大厅兜底 1）")
                if progress:
                    progress("战术撤离：已点 shell 顶栏最右 …")
                await page.wait_for_timeout(480)
                tried_exit = True
            except Exception as e:
                logger.warning(
                    "[kalaroko_monitor] 战术撤离 UI 大厅顶栏右端失败: %s",
                    str(e)[:280],
                )

        if not tried_exit:
            hdr_btns = page.locator('header button, [role="banner"] button')
            n2 = await hdr_btns.count()
            if n2 >= 2:
                try:
                    await hdr_btns.nth(n2 - 2).scroll_into_view_if_needed(timeout=clk)
                    await hdr_btns.nth(n2 - 2).click(timeout=clk, no_wait_after=True)
                    tried_exit = True
                    logger.info("[kalaroko_monitor] 战术撤离 UI：已点击顶栏倒数第二（大厅兜底 2）")
                    if progress:
                        progress("战术撤离：已点 shell 顶栏倒数第二钮 …")
                except Exception as e:
                    logger.warning(
                        "[kalaroko_monitor] 战术撤离 UI 顶栏倒数第二失败: %s",
                        str(e)[:280],
                    )
            elif n2 == 1 and not tried_exit:
                try:
                    await hdr_btns.nth(0).click(timeout=clk, no_wait_after=True)
                    tried_exit = True
                    logger.info("[kalaroko_monitor] 战术撤离 UI：仅一枚顶栏按钮，已点击")
                except Exception:
                    pass

        await page.wait_for_timeout(520)
        try:
            logger.info(
                "[kalaroko_monitor] 战术撤离 UI 结束瞬间 url=%s tried_exit=%s",
                (page.url or "")[:260],
                tried_exit,
            )
        except Exception:
            pass
        return tried_exit
    except Exception as e:
        logger.warning("[kalaroko_monitor] 战术撤离 UI 整体异常: %s", str(e)[:400])
        return False


async def _tactical_retreat_to_platform_home(
    page: Any,
    scenario: dict[str, Any],
    *,
    progress: Callable[[str], None] | None = None,
) -> None:
    """
    [战术撤离 · 核弹级] 不做 iframe 内温和点击退出（易找不到控件）；改为：

    1. 注册 ``dialog`` 监听，无脑 ``accept``，消解 ``beforeunload`` 等阻断导航的确认框；
    2. JS 注入 ``window.top.location.href``，强行突破 iframe，拉顶级窗口回到大厅 URL；
    3. 短 settle 后 ``page.goto(..., commit)`` 兜底，确保主文档锚定首页；
    4. 移除 dialog 监听后，再等 3s 让 SPA 挂载 DOM，便于下一场景的 ``click_selector``。
    """
    retreat_url = (scenario.get("start_url") or "").strip() or _DEFAULT_START
    if not _host_allowed(retreat_url):
        logger.warning(
            "[kalaroko_monitor] 战术撤离跳过：start_url 不在白名单: %s",
            retreat_url[:200],
        )
        return

    async def handle_dialog(dialog: Any) -> None:
        try:
            await dialog.accept()
        except Exception:
            pass

    page.on("dialog", handle_dialog)
    if progress:
        progress("战术撤离：已挂载 dialog 自动同意 + 顶层强跳首页 …")

    try:
        try:
            await page.evaluate(
                "(href) => { window.top.location.href = href; }",
                retreat_url,
            )
            await page.wait_for_timeout(2000)
            await page.goto(retreat_url, wait_until="commit", timeout=15000)
            logger.info(
                "[kalaroko_monitor] 核弹级撤离完成 → goto(commit) 首页 %s",
                retreat_url[:120],
            )
        except Exception as e:
            logger.warning(
                "[kalaroko_monitor] 核弹级撤离发生异常: %s",
                str(e)[:500],
            )
    finally:
        try:
            page.remove_listener("dialog", handle_dialog)
        except Exception:
            pass

    await page.wait_for_timeout(3000)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _err(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error_code": code, "message": message}


def _scenario_url(base_url: str, scenario: dict[str, Any]) -> str:
    """解析场景目标 URL：full_url > start_url > path+base（兼容旧版直链 game-frame）。"""
    fu = (scenario.get("full_url") or "").strip()
    if fu:
        return fu.rstrip("/")
    su = (scenario.get("start_url") or "").strip()
    if su:
        return su.rstrip("/")
    path = scenario.get("path")
    if path is None:
        path = "/"
    p = str(path).strip()
    if not p.startswith("/"):
        p = "/" + p
    base = base_url.strip().rstrip("/")
    return f"{base}{p}"


def _auth_storage_state_path() -> Path:
    """Playwright storage_state JSON；可由有头登录后导出。环境变量 KALAROKO_AUTH_STATE_PATH 覆盖默认路径。"""
    raw = (os.environ.get("KALAROKO_AUTH_STATE_PATH") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path(__file__).resolve().parent / "auth.json").resolve()


def _env_headless() -> bool:
    v = (os.environ.get("KALAROKO_HEADLESS") or "true").strip().lower()
    return v in ("1", "true", "yes", "on")


def _kalaroko_cdp_endpoint() -> str | None:
    """
    已由 CDP 连接的 Chrome WebSocket HTTP 入口，例如 ``http://127.0.0.1:9222``。
    Chrome 须预先以 ``--remote-debugging-port=9222``（或等价）启动；未设置时巡检无法连接浏览器。
    """
    raw = (os.environ.get("KALAROKO_CDP_ENDPOINT") or "").strip()
    if not raw:
        return None
    if not raw.startswith("http://") and not raw.startswith("https://"):
        raw = "http://" + raw.lstrip("/")
    return raw


def _chrome_user_data_dir() -> str | None:
    """
    接管本机 Chrome 用户数据目录（「战术三」）：复用真实登录态，减轻 Google「不安全浏览器」类拦截。

    环境变量 ``CHROME_USER_DATA_DIR``：默认留空；未设置时回退为 Playwright Chromium + ``storage_state``（见 ``_auth_storage_state_path``）。

    Windows（**推荐**，任意用户/机器通用，勿写死 ``C:\\Users\\某用户名\\...``）::

        %LOCALAPPDATA%\\Google\\Chrome\\User Data

    展开后等价于 ``C:\\Users\\<用户名>\\AppData\\Local\\Google\\Chrome\\User Data``（``<用户名>`` 随当前登录用户变化）。

    路径会先经 ``os.path.expandvars`` 再解析为绝对路径，故可使用 ``%LOCALAPPDATA%``、``%USERPROFILE%`` 等。
    """
    raw = (os.environ.get("CHROME_USER_DATA_DIR") or "").strip()
    if not raw:
        return None
    expanded = os.path.expandvars(raw)
    try:
        resolved = Path(expanded).expanduser().resolve()
    except OSError:
        resolved = Path(expanded).expanduser()
    return str(resolved)


def _chrome_executable_path() -> str | None:
    """
    可选：显式指定本机 **稳定版 Google Chrome** 的 ``chrome.exe``。

    Playwright 的 ``channel="chrome"`` 往往会优先拉起 **Chrome for Testing**（标题/关于里常带 Testing），
    与日常从 google.com 安装的零售 Chrome 不是同一份二进制。若希望与「真正浏览器」一致，请设置::

        CHROME_EXECUTABLE_PATH

    Windows 常见（64 位，勿写死用户名）::

        C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe

    支持 ``os.path.expandvars``（如含 ``%ProgramFiles%``）。
    """
    raw = (os.environ.get("CHROME_EXECUTABLE_PATH") or "").strip()
    if not raw:
        return None
    expanded = os.path.expandvars(raw)
    try:
        p = Path(expanded).expanduser().resolve()
    except OSError:
        p = Path(expanded).expanduser()
    if not p.is_file():
        logger.warning("[kalaroko_monitor] CHROME_EXECUTABLE_PATH 不是可执行文件: %s", p)
    return str(p)


def _cdp_http_port_from_endpoint(endpoint: str) -> int:
    """从 ``KALAROKO_CDP_ENDPOINT`` 解析 HTTP 端口；无端口时默认 9222。"""
    try:
        parsed = urlparse((endpoint or "").strip())
        if parsed.port is not None:
            return int(parsed.port)
    except (TypeError, ValueError):
        pass
    return 9222


def _resolve_host_chrome_executable_for_revive() -> str | None:
    """复活 OS Chrome 时的可执行路径：优先 ``CHROME_EXECUTABLE_PATH``，否则按平台探测。"""
    configured = _chrome_executable_path()
    if configured:
        try:
            if Path(configured).is_file():
                return configured
        except OSError:
            pass
    system = platform.system()
    if system == "Windows":
        for c in (
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        ):
            try:
                if Path(c).is_file():
                    return c
            except OSError:
                continue
        return None
    if system == "Darwin":
        p = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        return str(p) if p.is_file() else None
    for name in (
        "google-chrome-stable",
        "google-chrome",
        "chromium",
        "chromium-browser",
    ):
        found = shutil.which(name)
        if found:
            return found
    return None


async def _async_wait_cdp_http_json_version(
    endpoint: str, *, total_sec: float, tick: float
) -> bool:
    """
    轮询 Chrome DevTools HTTP 根（``/json/version``），直到可访问或超时。
    比单纯 ``sleep`` 可靠：冷启动 / 磁盘慢时 6s 内端口常仍未监听。
    """
    ep = (endpoint or "").strip() or "http://127.0.0.1:9222"
    parsed = urlparse(ep if "://" in ep else "http://" + ep.lstrip("/"))
    scheme = (parsed.scheme or "http").lower()
    if scheme not in ("http", "https"):
        scheme = "http"
    host = parsed.hostname or "127.0.0.1"
    port = int(parsed.port or _cdp_http_port_from_endpoint(ep))
    base = f"{scheme}://{host}:{port}".rstrip("/")
    url = base + "/json/version"
    deadline = time.perf_counter() + max(0.5, float(total_sec))
    tick = max(0.08, min(2.0, float(tick)))

    def _once() -> bool:
        try:
            from urllib.request import Request, urlopen

            req = Request(
                url,
                headers={"User-Agent": "Jachin-Kalaroko-CDP-Revive-Probe/1"},
            )
            with urlopen(req, timeout=min(3.0, tick * 4 + 1.0)) as resp:
                code = int(getattr(resp, "status", 200))
                return 200 <= code < 500
        except Exception:
            return False

    while time.perf_counter() < deadline:
        # Python 3.9+；避免阻塞 MCP 事件循环
        ok = await asyncio.to_thread(_once)
        if ok:
            return True
        await asyncio.sleep(tick)
    return False


def _isolated_kalaroko_cdp_revive_user_data_dir(port: int) -> str:
    """
    与「日常零售 Chrome」并存的 **独立** 用户数据目录，仅用于 CDP 复活拉起第二实例，
    避免与已打开的无调试 Chrome 争用同一 profile（SingletonLock → 进程秒退、9222 永不监听）。
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or tempfile.gettempdir()
    else:
        base = os.environ.get("TMPDIR") or tempfile.gettempdir()
    root = Path(os.path.expandvars(str(base)))
    d = root / "jachin-kalaroko-cdp-revive" / f"p{int(port)}"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return str(d.resolve())


def _popen_detached_chrome(argv: list[str]) -> None:
    """非阻塞拉起 Chrome GUI：Windows 用 DETACHED_PROCESS；Unix 用 start_new_session。"""
    popen_kw: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        # 勿用 shell=True；与当前 Python 进程解耦，避免阻塞 MCP stdio
        popen_kw["close_fds"] = False
        popen_kw["creationflags"] = int(
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        popen_kw["close_fds"] = True
        popen_kw["start_new_session"] = True
    subprocess.Popen(argv, **popen_kw)


async def _send_feishu_system_alert(
    title: str, message: str, *, is_critical: bool = False
) -> None:
    """
    飞书（开放平台）系统级告警：租户 token + 群聊发 **interactive** 卡片。

    - 环境变量：``FEISHU_APP_ID``、``FEISHU_APP_SECRET``、``FEISHU_CHAT_ID``（缺一则静默跳过）。
    - **Fire-and-forget**：仅 ``create_task`` 调度后台协程，本函数立即返回；任何网络/解析异常均吞掉，不影响 CDP 主路径。
    - 可用 ``KALAROKO_FEISHU_SYSTEM_ALERT=0`` 显式关闭（仍不要求配置飞书）。
    """
    if not _env_bool("KALAROKO_FEISHU_SYSTEM_ALERT", True):
        return

    app_id = (os.environ.get("FEISHU_APP_ID") or "").strip()
    app_secret = (os.environ.get("FEISHU_APP_SECRET") or "").strip()
    chat_id = (os.environ.get("FEISHU_CHAT_ID") or "").strip()
    if not (app_id and app_secret and chat_id):
        logger.debug(
            "[kalaroko_monitor] 飞书系统告警未配置（FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_CHAT_ID），跳过"
        )
        return

    tpl = "red" if is_critical else "orange"
    title_s = (title or "Kalaroko").strip()[:256]
    body_md = (message or "").strip()[:8000]

    def _sync_send() -> None:
        try:
            import httpx
        except ImportError:
            logger.warning(
                "[kalaroko_monitor] 未安装 httpx，无法发送飞书告警；请 pip install httpx"
            )
            return
        try:
            token_url = (
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
            )
            with httpx.Client(timeout=8.0) as client:
                tr = client.post(
                    token_url,
                    json={"app_id": app_id, "app_secret": app_secret},
                )
                tj = tr.json()
                if int(tj.get("code", -1)) != 0:
                    logger.warning(
                        "[kalaroko_monitor] 飞书 tenant_access_token 失败: %s",
                        str(tj)[:400],
                    )
                    return
                token = (tj.get("tenant_access_token") or "").strip()
                if not token:
                    logger.warning(
                        "[kalaroko_monitor] 飞书响应无 tenant_access_token: %s",
                        str(tj)[:400],
                    )
                    return

                card = {
                    "schema": "2.0",
                    "config": {"wide_screen_mode": True},
                    "header": {
                        "template": tpl,
                        "title": {"tag": "plain_text", "content": title_s},
                    },
                    "body": {
                        "elements": [
                            {
                                "tag": "div",
                                "text": {
                                    "tag": "lark_md",
                                    "content": body_md,
                                },
                            }
                        ]
                    },
                }
                send_url = (
                    "https://open.feishu.cn/open-apis/im/v1/messages"
                    "?receive_id_type=chat_id"
                )
                payload = {
                    "receive_id": chat_id,
                    "msg_type": "interactive",
                    "content": json.dumps(card, ensure_ascii=False),
                }
                sr = client.post(
                    send_url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json; charset=utf-8",
                    },
                    json=payload,
                )
                sj = sr.json()
                if int(sj.get("code", -1)) != 0:
                    logger.warning(
                        "[kalaroko_monitor] 飞书发消息失败: %s", str(sj)[:500]
                    )
        except Exception as e:
            logger.warning(
                "[kalaroko_monitor] 飞书系统告警发送异常（已吞）: %s", str(e)[:400]
            )

    async def _runner() -> None:
        try:
            await asyncio.to_thread(_sync_send)
        except Exception as e:
            logger.warning(
                "[kalaroko_monitor] 飞书告警后台任务异常（已吞）: %s", str(e)[:300]
            )

    try:
        asyncio.get_running_loop().create_task(_runner())
    except RuntimeError:
        try:
            _sync_send()
        except Exception:
            pass


async def _revive_external_chrome_on_9222(*, endpoint: str) -> tuple[bool, bool]:
    """
    当 CDP HTTP 端口不可连时，尝试用 **操作系统命令行** 拉起带 ``--remote-debugging-port`` 的 Google Chrome。

    返回 ``(spawned_any, cdp_http_ready)``：
    - ``spawned_any``：是否至少派发过一次 Popen（用于调用方区分「未尝试」与「尝试了但未就绪」）。
    - ``cdp_http_ready``：``/json/version`` 已可访问，调用方应再 ``connect_over_cdp``。

    **双档 user-data-dir（关键）**：若本机已有**未开远程调试**的 Chrome 占着默认/同一 profile，
    再带 ``--user-data-dir=同一目录`` 启动会 **SingletonLock 秒退**，9222 永不监听。
    因此默认先（可选）尝试 ``CHROME_USER_DATA_DIR``，失败再 **独立目录** ``…/jachin-kalaroko-cdp-revive/p<port>``，
    与零售实例并存，仅该副实例挂 CDP。

    - ``KALAROKO_CDP_REVIVE_TRY_CONFIGURED_PROFILE_FIRST``（默认 1）：有 ``CHROME_USER_DATA_DIR`` 时先尝试之。
    - ``KALAROKO_CDP_REVIVE_START_URL``：未设置环境变量时 **默认不打开站点**（尽快占端口）；显式设空字符串亦不加 URL。
    - ``KALAROKO_CDP_REVIVE_READY_TIMEOUT_SEC``：**每一档** 轮询 ``/json/version`` 的最长秒数（默认 18）。
    - ``KALAROKO_CDP_REVIVE_WAIT_SEC``：每档 Popen 后短睡再探测（默认 1s）。
    - 关闭复活：``KALAROKO_CDP_REVIVE_ON_CONNECT_FAIL=0`` → 返回 ``(False, False)``。
    - 飞书系统告警（可选）：``FEISHU_APP_ID`` / ``FEISHU_APP_SECRET`` / ``FEISHU_CHAT_ID``；
      关闭告警：``KALAROKO_FEISHU_SYSTEM_ALERT=0``。
    """
    if not _env_bool("KALAROKO_CDP_REVIVE_ON_CONNECT_FAIL", True):
        logger.info(
            "[kalaroko_monitor] CDP 复活已跳过（KALAROKO_CDP_REVIVE_ON_CONNECT_FAIL=0）"
        )
        return False, False

    logger.warning(
        "[kalaroko_monitor] CDP 无法连接（%s），外部 Chrome 可能已退出；尝试 OS 级复活（Daemon Reviver）…",
        (endpoint or "")[:120],
    )

    exe = _resolve_host_chrome_executable_for_revive()
    if not exe:
        logger.error(
            "[kalaroko_monitor] 未找到本机 Google Chrome 可执行文件；"
            "请设置 CHROME_EXECUTABLE_PATH 或将 chrome 加入 PATH"
        )
        return False, False

    port = _cdp_http_port_from_endpoint(endpoint)
    raw_su = os.environ.get("KALAROKO_CDP_REVIVE_START_URL")
    if raw_su is None:
        start_url = ""
    else:
        start_url = (raw_su or "").strip()

    initial_sleep = float(_env_int("KALAROKO_CDP_REVIVE_WAIT_SEC", 1, vmin=0, vmax=30))
    per_attempt = float(
        _env_int("KALAROKO_CDP_REVIVE_READY_TIMEOUT_SEC", 18, vmin=4, vmax=120)
    )
    poll_ms = _env_int("KALAROKO_CDP_REVIVE_READY_POLL_MS", 400, vmin=100, vmax=3000)
    tick = poll_ms / 1000.0

    await _send_feishu_system_alert(
        title="⚠️ Kalaroko 巡检节点异常",
        message=(
            "**状态**: 宿主 Chrome 进程崩溃或不可达，9222 / CDP 已丢失或不可连接！\n"
            f"**CDP 入口**: `{str(endpoint)[:220]}`\n"
            "**动作**: 正在执行操作系统级复活 (OS Spawn)…"
        ),
        is_critical=False,
    )

    spawned_any = False

    async def _spawn_tier(*, user_data_dir: str, tier_label: str) -> bool:
        nonlocal spawned_any
        argv: list[str] = [
            exe,
            f"--remote-debugging-port={port}",
            "--remote-allow-origins=*",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={user_data_dir}",
        ]
        if start_url:
            argv.append(start_url)
        logger.info(
            "[kalaroko_monitor] 复活 %s：user-data-dir=%s …",
            tier_label,
            user_data_dir[:160],
        )
        try:
            _popen_detached_chrome(argv)
        except Exception as e:
            logger.error(
                "[kalaroko_monitor] 复活 Popen 失败（%s）: %s",
                tier_label,
                str(e)[:400],
            )
            return False
        spawned_any = True
        if initial_sleep > 0:
            await asyncio.sleep(initial_sleep)
        logger.info(
            "[kalaroko_monitor] 复活 %s：轮询 /json/version（最多 %.0fs，间隔 %.2fs）…",
            tier_label,
            per_attempt,
            tick,
        )
        return await _async_wait_cdp_http_json_version(
            endpoint, total_sec=per_attempt, tick=tick
        )

    cfg = _chrome_user_data_dir()
    if cfg and _env_bool("KALAROKO_CDP_REVIVE_TRY_CONFIGURED_PROFILE_FIRST", True):
        if await _spawn_tier(user_data_dir=cfg, tier_label="tier-1（CHROME_USER_DATA_DIR）"):
            logger.info(
                "[kalaroko_monitor] CDP HTTP 已就绪（配置 profile），准备 connect_over_cdp"
            )
            await _send_feishu_system_alert(
                title="✅ Kalaroko 巡检节点恢复",
                message=(
                    "**状态**: 宿主 Chrome 死者苏生成功！\n"
                    f"**说明**: 端口 `{port}` 已重新绑定（/json/version 就绪），巡检将继续。"
                    "建议稍后检查本机内存与 H5 标签页数量。"
                ),
                is_critical=False,
            )
            return True, True
        logger.warning(
            "[kalaroko_monitor] tier-1 未在 %.0fs 内就绪（常被已开 Chrome 占 profile）；"
            "尝试 tier-2 独立 profile…",
            per_attempt,
        )

    iso = _isolated_kalaroko_cdp_revive_user_data_dir(port)
    if await _spawn_tier(user_data_dir=iso, tier_label="tier-2（独立 profile，可与零售 Chrome 并存）"):
        logger.info("[kalaroko_monitor] CDP HTTP 已就绪（独立 profile），准备 connect_over_cdp")
        await _send_feishu_system_alert(
            title="✅ Kalaroko 巡检节点恢复",
            message=(
                "**状态**: 宿主 Chrome 死者苏生成功！\n"
                f"**说明**: 端口 `{port}` 已重新绑定（独立 profile，/json/version 就绪），巡检将继续。"
                "建议稍后检查本机内存与 H5 标签页数量。"
            ),
            is_critical=False,
        )
        return True, True

    logger.error(
        "[kalaroko_monitor] 复活两档均未在 %.0fs 内使 /json/version 就绪（端口 %s）。"
        "若仍失败请检查杀毒/策略或手动启动带 --remote-debugging-port 的 Chrome。",
        per_attempt,
        port,
    )
    await _send_feishu_system_alert(
        title="🚨 Kalaroko 巡检致命故障",
        message=(
            "**状态**: Chrome 进程复活失败（/json/version 在时限内未就绪）！\n"
            f"**端口**: `{port}`\n"
            "**动作要求**: 巡检将回退至无状态临时 Chromium，**无法保持原登录态**。\n"
            "<at user_id=\"all\"></at> **请立即登录服务器进行人工干预排查！**"
        ),
        is_critical=True,
    )
    return spawned_any, False


_KALAROKO_REPO_DOTENV_TRIED = False


def _load_repo_dotenv_for_kalaroko_monitor() -> None:
    """
    合并仓库根 ``.env``，使 ``CHROME_*`` / ``KALAROKO_*`` 在 MCP 直启或外层未 ``load_dotenv`` 时仍可用。
    不覆盖 ``os.environ`` 中已有键（与 python-dotenv 默认行为一致）。
    """
    global _KALAROKO_REPO_DOTENV_TRIED
    if _KALAROKO_REPO_DOTENV_TRIED:
        return
    _KALAROKO_REPO_DOTENV_TRIED = True
    try:
        from dotenv import load_dotenv
    except ImportError:
        logger.warning(
            "[kalaroko_monitor] 未安装 python-dotenv，无法自动读取仓库根 .env；"
            "CHROME_EXECUTABLE_PATH / CHROME_USER_DATA_DIR 请写入系统环境变量或先 pip install python-dotenv"
        )
        return
    try:
        root = Path(__file__).resolve().parents[3]
        env_p = root / ".env"
        if env_p.is_file():
            load_dotenv(env_p, encoding="utf-8")
    except OSError as e:
        logger.debug("[kalaroko_monitor] load_dotenv: %s", e)


def _is_chrome_profile_in_use_error(exc: BaseException) -> bool:
    """Chrome 用户目录被占用（本机 Chrome 已开、SingletonLock 等）时的启发式判定。"""
    msg = str(exc).lower()
    if "browser instance is already running" in msg:
        return True
    if "singletonlock" in msg or "singleton lock" in msg:
        return True
    if "another chrome" in msg or "another process" in msg:
        return True
    if "profile" in msg and ("in use" in msg or "locked" in msg or "cannot be opened" in msg):
        return True
    return False


async def _apply_stealth_to_context(context: Any) -> None:
    """对 BrowserContext 挂接 playwright-stealth（异步 API），覆盖该上下文内已有及后续页面。"""
    try:
        from playwright_stealth import Stealth

        await Stealth().apply_stealth_async(context)
        logger.info("[kalaroko_monitor] playwright-stealth 已应用到 BrowserContext")
    except ImportError:
        logger.warning(
            "[kalaroko_monitor] 未安装 playwright-stealth，跳过隐身脚本；可选: pip install playwright-stealth"
        )
    except Exception as e:
        logger.warning("[kalaroko_monitor] playwright-stealth 应用失败: %s", str(e)[:300])


def _log_browser_launch_plan(
    headless: bool,
    viewport_width: int,
    viewport_height: int,
    device_scale_factor: float,
) -> None:
    """在连接浏览器前打出计划摘要。"""
    cdp = _kalaroko_cdp_endpoint()
    vw, vh = int(viewport_width), int(viewport_height)
    dsf = float(device_scale_factor)
    if cdp:
        logger.info(
            "[kalaroko_monitor] ===== 浏览器计划：CDP 连接已有 Chrome（connect_over_cdp）| %s =====",
            cdp,
        )
        logger.info(
            "[kalaroko_monitor] 说明: 不另起浏览器进程；headless=%s 对 CDP 无意义（由已打开的 Chrome 决定）。"
            " viewport=%sx%s | dsf=%s 仅在新建 context 时生效。",
            headless,
            vw,
            vh,
            dsf,
        )
        return

    logger.warning(
        "[kalaroko_monitor] KALAROKO_CDP_ENDPOINT 未设置；必须在环境或 .env 中配置，例如 "
        "KALAROKO_CDP_ENDPOINT=http://127.0.0.1:9222 并先启动带远程调试端口的 Chrome。"
    )


def _cdp_tab_url_driver_safe(url: str) -> bool:
    """排除 DevTools / 扩展页：与 K11 ``test_k11_unified_platform_smoke_playwright._cdp_tab_url_driver_safe`` 一致。"""
    u = (url or "").strip().lower()
    if u.startswith("devtools://") or u.startswith("chrome-devtools://"):
        return False
    if u.startswith("chrome-extension://") or u.startswith("moz-extension://"):
        return False
    if u.startswith("ms-browser-extension://"):
        return False
    return True


def _cdp_page_url_matches_preferred_host(page_url: str, preferred_host: str) -> bool:
    """
    是否与巡检 base_url 的主机「同属一站」：整串 host 或去掉 www. 后的根域出现在 URL 中
    （对齐 K11 ``host in u.lower()``，并兼容 ``www.`` / 无 www）。
    """
    u = (page_url or "").lower()
    ph = (preferred_host or "").strip().lower()
    if not ph or not u:
        return False
    if ph in u:
        return True
    core = ph[4:] if ph.startswith("www.") else ph
    return bool(core and core in u)


def _cdp_tab_probe_timeout_sec() -> float:
    """单标签 JS 探活超时（秒），用于斩杀 Aw Snap / 渲染器僵死页。"""
    ms = _env_int("KALAROKO_CDP_TAB_PROBE_MS", 3000, vmin=500, vmax=20_000)
    return max(0.5, ms / 1000.0)


async def _cdp_tab_health_probe_or_slaughter(pg: Any) -> bool:
    """
    CDP 下标签页 JS 探活；失败则视为僵尸/崩溃页（Aw Snap、Target closed 等）并 ``page.close()``，
    避免下一轮仍命中同一死签导致巡检瘫痪。
    """
    try:
        if pg.is_closed():
            return False
    except Exception:
        return False
    tmo = _cdp_tab_probe_timeout_sec()
    try:
        await asyncio.wait_for(pg.evaluate("() => (1 + 1)"), timeout=tmo)
        return True
    except Exception as e:
        logger.warning(
            "[kalaroko_monitor] CDP 标签探活失败，判定为僵尸/崩溃页并尝试关闭: %s",
            str(e)[:360],
        )
        try:
            await pg.close()
        except Exception:
            pass
        return False


async def _pick_kalaroko_cdp_context_and_page(
    browser: Any,
    *,
    preferred_host: str | None,
) -> tuple[Any, Any]:
    """
    选用 **BrowserContext + Page**（与 K11 ``_acquire_cdp_target_page`` 同思路）：

    - 扫描 **全部** ``browser.contexts``（不再只认 ``contexts[0]``，避免多窗口时绑错实例）。
    - **优先** URL 含 ``preferred_host``（来自本轮 ``base_url``）且可探活的标签（用户盯大厅时通常即此 Tab）。
    - 否则在可自动化 URL 上按 ``KALAROKO_CDP_PREFER_VISIBLE_TAB`` 做可见性/焦点排序（与旧逻辑一致）。
    - ``KALAROKO_CDP_NEW_TAB=1``：在首个 context 上 ``new_page()``。

    仅靠 ``is_closed()`` 不够：多标签下常有僵尸 Tab；须 **JS 探活**，失败则 **close** 斩杀以免反复命中死页。
    """
    raw = (os.environ.get("KALAROKO_CDP_NEW_TAB") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        if not getattr(browser, "contexts", None):
            raise RuntimeError("CDP 已连接但 browser.contexts 为空，无法 new_page")
        ctx0 = browser.contexts[0]
        pg = await ctx0.new_page()
        logger.info("[kalaroko_monitor] KALAROKO_CDP_NEW_TAB 已启用：新建专用于巡检的标签页")
        try:
            await asyncio.wait_for(pg.evaluate("() => 1"), timeout=10.0)
        except asyncio.TimeoutError:
            logger.error("[kalaroko_monitor] NEW_TAB 新建页 evaluate 超时，Chrome/CDP 可能卡住")
            raise
        except Exception as e:
            logger.error(
                "[kalaroko_monitor] NEW_TAB 新建页 evaluate 失败: %s", str(e)[:400]
            )
            raise
        return ctx0, pg

    ph = (preferred_host or "").strip().lower() or None
    prefer_visible = _env_bool("KALAROKO_CDP_PREFER_VISIBLE_TAB", True)

    # —— 阶段 A：与 K11 一致，优先 URL 命中目标域（多 context、从右往左扫标签）——
    if ph:
        for ctx in list(getattr(browser, "contexts", []) or []):
            pages = list(getattr(ctx, "pages", []) or [])
            for pg in reversed(pages):
                try:
                    u = str(getattr(pg, "url", "") or "").strip()
                except Exception:
                    u = ""
                if not _cdp_tab_url_driver_safe(u):
                    continue
                if not await _cdp_tab_health_probe_or_slaughter(pg):
                    continue
                if not _cdp_page_url_matches_preferred_host(u, ph):
                    continue
                logger.info(
                    "[kalaroko_monitor] CDP 选用标签页（URL 含目标域 %s，探活通过，对齐 K11 host 匹配）: %s",
                    ph,
                    u[:160],
                )
                return ctx, pg

    # —— 阶段 B：未命中目标域时，全部 context 的页签上做可见性排序 ——
    scored: list[tuple[int, int, int, int, int, Any, Any]] = []
    for ci, ctx in enumerate(list(getattr(browser, "contexts", []) or [])):
        pages = list(getattr(ctx, "pages", []) or [])
        if not prefer_visible:
            for idx in range(len(pages) - 1, -1, -1):
                pg = pages[idx]
                try:
                    u = str(getattr(pg, "url", "") or "").strip()
                except Exception:
                    u = ""
                if not _cdp_tab_url_driver_safe(u):
                    continue
                if not await _cdp_tab_health_probe_or_slaughter(pg):
                    continue
                logger.info(
                    "[kalaroko_monitor] CDP 复用标签（PREFER_VISIBLE_TAB=0）ctx=%s index=%s url=%s",
                    ci,
                    idx,
                    u[:120],
                )
                return ctx, pg
            continue

        for idx in range(len(pages) - 1, -1, -1):
            pg = pages[idx]
            try:
                u = str(getattr(pg, "url", "") or "").strip()
            except Exception:
                u = ""
            if not _cdp_tab_url_driver_safe(u):
                continue
            if not await _cdp_tab_health_probe_or_slaughter(pg):
                continue
            vis_st = "hidden"
            has_foc = False
            try:
                pair = await asyncio.wait_for(
                    pg.evaluate(
                        "() => [document.visibilityState, document.hasFocus()]"
                    ),
                    timeout=2.5,
                )
                if (
                    isinstance(pair, (list, tuple))
                    and len(pair) >= 2
                    and isinstance(pair[0], str)
                ):
                    vis_st = pair[0]
                    has_foc = bool(pair[1])
            except Exception:
                pass
            vis_tier = 0 if vis_st == "visible" else 1
            foc_tier = 0 if has_foc else 1
            scored.append((vis_tier, foc_tier, -ci, -idx, ci, ctx, pg))

    while scored:
        scored.sort()
        _vt, _ft, _nc, _ni, ci, ctx, pg = scored[0]
        if not await _cdp_tab_health_probe_or_slaughter(pg):
            logger.warning(
                "[kalaroko_monitor] CDP visibility 排序候选项探活失败（或已斩杀），跳过下一条"
            )
            scored.pop(0)
            continue
        try:
            _u = str(getattr(pg, "url", "") or "")[:120]
        except Exception:
            _u = ""
        logger.info(
            "[kalaroko_monitor] CDP 选用标签页 ctx=%s（探活 | visibility 优先）url=%s",
            ci,
            _u,
        )
        return ctx, pg

    # —— 阶段 C：new_page ——
    if not getattr(browser, "contexts", None):
        raise RuntimeError("CDP browser.contexts 为空，无法 new_page")
    ctx_fallback = browser.contexts[0]
    logger.warning(
        "[kalaroko_monitor] CDP 无可用复用页签，已 new_page；"
        "若常遇僵尸页可设 KALAROKO_CDP_NEW_TAB=1"
    )
    pg = await ctx_fallback.new_page()
    try:
        await asyncio.wait_for(pg.evaluate("() => 1"), timeout=8.0)
    except asyncio.TimeoutError:
        logger.error("[kalaroko_monitor] CDP new_page 后 evaluate 超时（8s），Chrome/CDP 可能卡住")
        raise
    except Exception as e:
        logger.error(
            "[kalaroko_monitor] CDP new_page 后仍无法 evaluate，Chrome/CDP 可能异常: %s",
            str(e)[:400],
        )
        raise
    return ctx_fallback, pg


async def _launch_kalaroko_browser_context(
    playwright: Any,
    *,
    viewport_width: int,
    viewport_height: int,
    device_scale_factor: float,
    headless: bool,
    preferred_host: str | None = None,
) -> tuple[Any, Any, Any, bool]:
    """
    优先 **CDP** 连接已有 Chrome；首次失败时可 **OS 级复活** 本机 Chrome 再连；仍失败才 **回退**
    ``chromium.launch(headless=...)`` 独立进程（会丢失 CDP 登录态，仅作最后手段）。

    ``preferred_host``：本轮巡检 ``base_url`` 的主机名（小写），用于与 K11 冒烟一致地 **优先绑定含该域的 Tab**，
    避免打在 DevTools/其他站/空白页上却仍能「出数」。

    返回 ``(browser, context, page, must_close_context)``：
    - ``must_close_context=True``：本轮在 ``finally`` 中必须 ``await context.close()``（自建 context 或 launch 实例），防泄漏。
    - CDP 复用用户默认 context 时为 ``False``（勿关用户标签页）。
    """
    vp = {"width": int(viewport_width), "height": int(viewport_height)}
    dsf = float(device_scale_factor)

    endpoint = (_kalaroko_cdp_endpoint() or "").strip() or "http://127.0.0.1:9222"
    connect_sec = float(
        _env_int("KALAROKO_CDP_CONNECT_TIMEOUT_SEC", 15, vmin=5, vmax=120)
    )

    async def _connect_over_cdp_once() -> Any:
        return await asyncio.wait_for(
            playwright.chromium.connect_over_cdp(endpoint),
            timeout=connect_sec,
        )

    browser: Any | None = None
    try:
        browser = await _connect_over_cdp_once()
    except Exception as e:
        logger.warning(
            "[kalaroko_monitor] CDP 首次 connect_over_cdp 失败（%s）: %s",
            endpoint,
            str(e)[:400],
        )
        spawned, http_ready = await _revive_external_chrome_on_9222(endpoint=endpoint)
        if spawned and http_ready:
            attempts = _env_int(
                "KALAROKO_CDP_POST_REVIVE_CONNECT_ATTEMPTS", 3, vmin=1, vmax=12
            )
            gap = float(
                _env_int("KALAROKO_CDP_POST_REVIVE_CONNECT_GAP_MS", 500, vmin=0, vmax=5000)
            )
            gap = gap / 1000.0
            last_e2: BaseException | None = None
            browser = None
            post_to = min(
                float(connect_sec),
                float(
                    _env_int(
                        "KALAROKO_CDP_POST_REVIVE_CONNECT_TIMEOUT_SEC",
                        20,
                        vmin=5,
                        vmax=120,
                    )
                ),
            )
            for att in range(attempts):
                try:
                    browser = await asyncio.wait_for(
                        playwright.chromium.connect_over_cdp(endpoint),
                        timeout=post_to,
                    )
                    last_e2 = None
                    break
                except BaseException as e2:
                    last_e2 = e2
                    if att + 1 < attempts and gap > 0:
                        await asyncio.sleep(gap)
            if browser is None and last_e2 is not None:
                logger.error(
                    "[kalaroko_monitor] Chrome 复活后 %s 次 connect_over_cdp 仍失败，回退 Playwright launch: %s",
                    attempts,
                    str(last_e2)[:400],
                )
        elif spawned and not http_ready:
            logger.error(
                "[kalaroko_monitor] 复活已派发但 /json/version 未就绪；"
                "跳过 connect_over_cdp 连打（避免长时间卡死），直接 Playwright launch 兜底"
            )
            browser = None
        else:
            browser = None

        if browser is None:
            browser = await playwright.chromium.launch(headless=headless)
            context = await browser.new_context(viewport=vp, device_scale_factor=dsf)
            await _apply_stealth_to_context(context)
            page = await context.new_page()
            logger.info(
                "[kalaroko_monitor] 已 launch 独立 Chromium（headless=%s），新建 context + page",
                headless,
            )
            return browser, context, page, True

    logger.info(
        "[kalaroko_monitor] 已连接 CDP → %s，context 数=%s",
        endpoint,
        len(browser.contexts),
    )

    context: Any
    page: Any
    must_close_context = False
    if browser.contexts and len(browser.contexts) > 0:
        context, page = await _pick_kalaroko_cdp_context_and_page(
            browser,
            preferred_host=preferred_host,
        )
    else:
        context = await browser.new_context(
            viewport=vp,
            device_scale_factor=dsf,
        )
        must_close_context = True
        logger.info("[kalaroko_monitor] CDP 下无现有 context，已新建 context（viewport 已应用）")
        page = await context.new_page()

    # stealth 必须落在 **实际驱动** 的 context 上（可能与旧版仅 contexts[0] 不一致）
    await _apply_stealth_to_context(context)

    # CDP 复用已有标签时，Chrome 当前焦点可能在**别的 Tab**；不 bring_to_front 会导致
    # 「终端有 JSON/指标但眼前窗口一动不动」。launch 新页路径下同样无害。
    try:
        await page.bring_to_front()
        logger.info(
            "[kalaroko_monitor] 已将本轮绑定的 Page bring_to_front（请确认 Chrome 窗口未最小化）"
        )
    except Exception as _bf_e:
        logger.debug(
            "[kalaroko_monitor] bring_to_front 跳过: %s", str(_bf_e)[:160]
        )

    return browser, context, page, must_close_context


# W3C Navigation Timing / Paint + ALPN（nextHopProtocol）；legacy performance.timing 作兜底
_METRICS_JS = """
() => {
  const nav = performance.getEntriesByType('navigation')[0];
  const paint = performance.getEntriesByType('paint');
  const fcpEntry = paint.find(e => e.name === 'first-contentful-paint');

  let ttfb_ms = nav ? Math.round(Math.max(0, nav.responseStart - nav.requestStart)) : null;
  let dom_content_loaded_ms = nav
    ? Math.round(nav.domContentLoadedEventEnd - nav.startTime)
    : null;
  let page_load_ms =
    nav && nav.loadEventEnd > 0
      ? Math.round(nav.loadEventEnd - nav.startTime)
      : null;
  let protocol = nav && nav.nextHopProtocol ? nav.nextHopProtocol : 'unknown';

  const w = window.performance && window.performance.timing;
  if (w && w.navigationStart) {
    if (ttfb_ms == null && w.responseStart != null && w.fetchStart != null) {
      ttfb_ms = Math.round(Math.max(0, w.responseStart - w.fetchStart));
    }
    if (dom_content_loaded_ms == null && w.domContentLoadedEventEnd) {
      dom_content_loaded_ms = Math.round(w.domContentLoadedEventEnd - w.navigationStart);
    }
    if (page_load_ms == null && w.loadEventEnd > 0) {
      page_load_ms = Math.round(w.loadEventEnd - w.navigationStart);
    }
  }

  return {
    ttfb_ms: ttfb_ms,
    fcp_ms: fcpEntry ? Math.round(fcpEntry.startTime) : null,
    dom_content_loaded_ms: dom_content_loaded_ms,
    page_load_ms: page_load_ms,
    protocol: protocol
  };
}
"""


def _homepage_payload_from_probe(
    *,
    url: str,
    load_st: str,
    metrics: dict[str, Any],
    total_requests: int,
    failed_requests: int,
) -> dict[str, Any]:
    """组装首页 JSON：metrics（QA 硬核指标）+ web_vitals/navigation_timing 兼容旧消费方。"""
    proto_raw = metrics.get("protocol")
    if proto_raw is None or str(proto_raw).strip() == "":
        proto_u = "UNKNOWN"
    else:
        proto_u = str(proto_raw).strip().upper()

    m = {
        "ttfb_ms": metrics.get("ttfb_ms"),
        "fcp_ms": metrics.get("fcp_ms"),
        "dom_content_loaded_ms": metrics.get("dom_content_loaded_ms"),
        "page_load_ms": metrics.get("page_load_ms"),
        "total_resources": int(total_requests),
        "failed_resources": int(failed_requests),
        "protocol": proto_u,
    }

    wv_ttfb = metrics.get("ttfb_ms")
    wv_fcp = metrics.get("fcp_ms")

    return {
        "url": url,
        "load_status": load_st,
        "metrics": m,
        "web_vitals": {
            "lcp_ms": None,
            "fid_ms": None,
            "cls": None,
            "inp_ms": None,
            "ttfb_ms": wv_ttfb,
            "fcp_ms": wv_fcp,
        },
        "navigation_timing": {
            "dom_content_loaded_ms": metrics.get("dom_content_loaded_ms"),
            "page_load_ms": metrics.get("page_load_ms"),
            "ttfb_ms": metrics.get("ttfb_ms"),
            "protocol": metrics.get("protocol"),
        },
    }


def _empty_homepage_metrics(url: str, load_st: str) -> dict[str, Any]:
    return _homepage_payload_from_probe(
        url=url,
        load_st=load_st,
        metrics={
            "ttfb_ms": None,
            "fcp_ms": None,
            "dom_content_loaded_ms": None,
            "page_load_ms": None,
            "protocol": "unknown",
        },
        total_requests=0,
        failed_requests=0,
    )


def _normalize_network_profile(raw: str | None) -> str:
    if not raw:
        return "wifi"
    v = str(raw).strip().lower()
    allowed = {"wifi", "4g", "3g", "offline_sim", "custom"}
    return v if v in allowed else "custom"


try:
    mcp = FastMCP(
        "mcp_kalaroko_monitor",
        description="Kalaroko Web 性能监控：Playwright 采集、API 健康探测、本地历史 JSONL/SQLite。",
    )
except TypeError:
    # 不同版本 FastMCP 对 description 支持不一，避免启动即 TypeError
    mcp = FastMCP("mcp_kalaroko_monitor")


@mcp.tool()
async def execute_playwright_perf_test(
    base_url: str | None = None,
    scenarios: list | None = None,
    run_id: str | None = None,
    network_profile: str | None = "wifi",
    viewport: dict | None = None,
    collect_console: bool = True,
    max_games: int | None = None,
    headless: bool = True,
    ui_pace_ms: int | None = None,
    ui_cursor_moves: bool = True,
) -> dict[str, Any]:
    """
    使用 Playwright 按场景采集首页 **W3C Navigation/Paint + ALPN** 与游戏入口性能，
    单场景失败不中断整轮；返回符合 TDD KalarokoPerfSnapshot 的载荷（api_health 为空数组）。

    **游戏场景深度感知（缓解 H5「假加载」）**：默认 ``wait_until=domcontentloaded``（不等待音视频等拖满 ``load``，亦不使用 ``networkidle`` 盲等）；
    导航前后监听 ``page.on("websocket")``（排障用，**不再**作为就绪判据）；进房后使用 **晚期竞速**
    （**仅**晚期 UI DOM 停表；牌桌类 HTTP 仅写入排障轨迹；预算默认 80s，``KALAROKO_GAME_LATE_READY_RACE_MS``）；
    墙钟 ``real_engine_load_ms`` 写入 JSON，并用其覆盖游戏条目的 **ttfb_ms**（原 Navigation Timing
    保留在 **shell_navigation_ttfb_ms** 供对照）。失败路径均有 try/except，避免无限阻塞。

    Args:
        base_url: 站点根 URL，默认 https://kalaroko.com
        scenarios: 场景列表（name、path 或 full_url、**start_url**、**click_selector**、wait_until、timeout_ms；
            点击流可选 **require_game_frame_url**（默认 True）：采数结束前主文档 URL 须含 ``game-frame``，
            否则本条 **load_status=failed**，避免未见游戏打开仍上报 success）。
            游戏场景可选 **document_game_id** 与 Word/BI 报告小节 game_id 对齐）。**传 null 或 [] 时自动使用**
            内置 ``KALAROKO_DEFAULT_SCENARIOS``（首页 + 四款游戏；游戏默认从首页点击进入）。浏览器：
            **必须**设置 ``KALAROKO_CDP_ENDPOINT``（如 ``http://127.0.0.1:9222``），并预先以 ``--remote-debugging-port``
            启动 Chrome（仓库 ``scripts/launch_chrome_debug.ps1``）；Playwright 仅 ``connect_over_cdp``，**不再**
            自动 ``launch`` 新浏览器。会话/Cookie 由该 Chrome 实例与用户数据目录决定。无登录态时若出现
            **Login** 遮罩：每次进入 ``start_url`` 后会依次尝试关闭 **通知推广条（Cancel）** / **subscribers 模态**、再点 **Continue with Guest**、最后再扫一层遮挡，然后再点游戏入口。
            **url_game_id** 优先从 gweb 解析，点击流无法解析时回退为 ``document_game_id``。
        run_id: 可选，本轮 UUID
        network_profile: wifi|4g|3g|offline_sim|custom（标注用）
        viewport: 可选 width/height/device_scale_factor
        collect_console: 是否采集 console / pageerror
        max_games: 限制「游戏」场景数量（首页后的场景）
        headless: 是否无头运行。``False`` 时在回退 ``chromium.launch`` 路径下显示浏览器窗口；CDP 连接已有 Chrome 时由该实例是否可见决定，此参数主要影响 launch 回退。
        ui_pace_ms: 大于 0 时在导航/准备/点击等步骤之间额外 ``sleep`` 并 ``bring_to_front``，便于肉眼观察。
            传 ``None`` 时读环境变量 ``KALAROKO_E2E_UI_PACE_MS``（默认 0，保持历史行为）。
        ui_cursor_moves: 当 ``ui_pace_ms>0`` 时，游戏入口点击前用 ``mouse.move(..., steps=…)`` 画出指针轨迹，并拉长 click 的 ``delay``。

    阶段性人可读输出（不影响返回 JSON）：本机脚本可调用 ``set_playwright_progress_callback`` 注册回调。
    """
    _load_repo_dotenv_for_kalaroko_monitor()
    from playwright.async_api import async_playwright

    def _progress(msg: str) -> None:
        cb = _playwright_progress_cb
        if not cb:
            return
        try:
            cb(msg)
        except Exception:
            pass

    scenarios, used_default_scenarios = _coerce_scenarios(scenarios)
    if not scenarios:
        return _err(
            "INVALID_SCENARIOS",
            "scenarios 须为数组；若需默认巡检请传 null 或 []（将使用 KALAROKO_DEFAULT_SCENARIOS）",
        )

    base = (base_url or _DEFAULT_BASE).strip()
    if not _host_allowed(base):
        return _err("HOST_NOT_ALLOWED", f"base_url 主机不在白名单: {sorted(_allowed_hosts())}")

    # 与 K11 统合冒烟 ``_acquire_cdp_target_page`` 一致：CDP 优先绑定 URL 含本站 host 的 Tab
    cdp_preferred_host = (urlparse(base).hostname or "").strip().lower() or None

    rid = (run_id or str(uuid.uuid4())).strip()
    net_label = _normalize_network_profile(network_profile)
    vw = viewport if isinstance(viewport, dict) else {}
    w = int(vw.get("width", 390))
    h = int(vw.get("height", 844))
    dsf = float(vw.get("device_scale_factor", 2))

    if ui_pace_ms is not None:
        pace = max(0, min(120_000, int(ui_pace_ms)))
    else:
        pace = _env_int("KALAROKO_E2E_UI_PACE_MS", 0, vmin=0, vmax=120_000)
    cursor_mv = bool(ui_cursor_moves)

    home_sc = scenarios[0]
    game_scenarios = list(scenarios[1:])
    if max_games is not None and max_games >= 0:
        game_scenarios = game_scenarios[: int(max_games)]

    _log_browser_launch_plan(headless, w, h, dsf)

    _progress(
        f"场景计划：1 个首页 + {len(game_scenarios)} 个游戏"
        + (f"（已 max_games 截断）" if max_games is not None else "")
    )

    browser_exceptions: list[dict[str, Any]] = []
    games_out: list[dict[str, Any]] = []
    homepage: dict[str, Any] | None = None
    req_fail_slot: list[int] = [0]

    async with async_playwright() as p:
        _hl = headless
        browser: Any | None = None
        context: Any | None = None
        page: Any | None = None
        must_close_context = False
        try:
            try:
                browser, context, page, must_close_context = (
                    await _launch_kalaroko_browser_context(
                        p,
                        viewport_width=w,
                        viewport_height=h,
                        device_scale_factor=dsf,
                        headless=_hl,
                        preferred_host=cdp_preferred_host,
                    )
                )
                _kalaroko_register_playwright_session(
                    browser, context, must_close_context
                )
            except Exception as e:
                if _is_chrome_profile_in_use_error(e):
                    return _err(
                        "CHROME_PROFILE_IN_USE",
                        "检测到 Chrome 用户数据目录已被占用（通常因 Chrome 正在运行）。请关闭所有 Chrome 窗口后重试。",
                    )
                raise

            def _on_console(msg) -> None:
                if not collect_console:
                    return
                try:
                    if msg.type == "error":
                        browser_exceptions.append(
                            {
                                "type": "error",
                                "message": msg.text()[:2000],
                                "source": None,
                                "line": None,
                            }
                        )
                except Exception:
                    pass

            def _on_page_error(exc) -> None:
                if not collect_console:
                    return
                try:
                    browser_exceptions.append(
                        {
                            "type": "pageerror",
                            "message": str(exc)[:2000],
                            "source": None,
                            "line": None,
                        }
                    )
                except Exception:
                    pass

            def _on_request_failed(request) -> None:
                try:
                    u = request.url
                    req_fail_slot[0] += 1
                    browser_exceptions.append(
                        {
                            "type": "requestfailed",
                            "message": (request.failure or "")[:500] + " " + u[:500],
                            "source": u,
                            "line": None,
                        }
                    )
                except Exception:
                    pass

            if collect_console:
                page.on("console", _on_console)
                page.on("pageerror", _on_page_error)
                page.on("requestfailed", _on_request_failed)

            _progress("已通过 CDP 绑定浏览器（未新起进程），页面对象就绪；即将采集首页…")
            await _kalaroko_ui_breathe(
                page,
                pace,
                progress=_progress,
                hint=f"[UI 节奏] 每步间隔约 {pace} ms（可设 KALAROKO_E2E_UI_PACE_MS 或传 ui_pace_ms）"
                if pace > 0
                else "",
            )
            if _e2e_user_cancel_requested():
                _progress("用户已请求停止巡检（采集首页前）…")
                return _err(
                    "USER_CANCELLED",
                    "巡检已由用户停止（请在停止后重新开始）",
                )

            async def _run_one(scenario: dict[str, Any], is_home: bool) -> None:
                nonlocal homepage
                req_fail_slot[0] = 0
                name = str(scenario.get("name") or "scenario")
                if _e2e_user_cancel_requested():
                    raise KalarokoE2EUserCancelled()
                # 多场景串联时用户可能中途切走 Tab；每场景前再聚焦一次，避免「有结果但界面像没动」
                try:
                    await page.bring_to_front()
                except Exception:
                    pass
                await _kalaroko_ui_breathe(
                    page,
                    pace,
                    progress=_progress,
                    hint=f"[UI 节奏] 场景「{name}」开始…" if pace > 0 else "",
                )
                wait_until = str(scenario.get("wait_until") or "domcontentloaded")
                timeout_ms = int(scenario.get("timeout_ms") or 60000)
                url = _scenario_url(base, scenario)
                if not _host_allowed(url):
                    browser_exceptions.append(
                        {
                            "type": "error",
                            "message": f"blocked non-allowlisted URL: {url}",
                            "source": None,
                            "line": None,
                        }
                    )
                    if is_home:
                        homepage = _empty_homepage_metrics(url, "failed")
                    else:
                        row = {
                            "game_id": name,
                            "path": _game_path_fallback(scenario, url),
                            "ttfb_ms": None,
                            "real_engine_load_ms": None,
                            "shell_navigation_ttfb_ms": None,
                            "load_status": "failed",
                            "total_requests": 0,
                            "resource_errors_count": 0,
                            "console_errors_count": 0,
                            "room_id": "N/A",
                            "online_players": _empty_online_players_dict(),
                            "table_seat_players": None,
                        }
                        row.update(_game_id_snapshot_fields(scenario, url))
                        games_out.append(row)
                    return

                try:
                    if is_home:
                        total_requests_count = {"count": 0}
                        failed_requests_count = {"count": 0}

                        def _on_home_request(_request: Any) -> None:
                            total_requests_count["count"] += 1

                        def _on_home_request_failed(_request: Any) -> None:
                            failed_requests_count["count"] += 1

                        page.on("request", _on_home_request)
                        page.on("requestfailed", _on_home_request_failed)
                        try:
                            response = await _goto_resilient(
                                page, url, wait_until, timeout_ms
                            )
                        finally:
                            try:
                                page.remove_listener("request", _on_home_request)
                                page.remove_listener(
                                    "requestfailed", _on_home_request_failed
                                )
                            except Exception:
                                pass

                        await _kalaroko_ui_breathe(
                            page,
                            pace,
                            progress=_progress,
                            hint="[UI 节奏] 首页导航完成…" if pace > 0 else "",
                        )
                        # 先访客登录并去掉通知/subscribers 遮挡，再采首页指标（与真实可玩大厅一致）
                        await _prepare_kalaroko_lobby_after_navigation(
                            page, progress=_progress
                        )
                        await _kalaroko_ui_breathe(
                            page,
                            pace,
                            progress=_progress,
                            hint="[UI 节奏] 大厅遮挡处理完毕…" if pace > 0 else "",
                        )
                        status = response.status if response else None
                        data = await _evaluate_metrics_with_retry(page)
                        metrics = data if isinstance(data, dict) else {}

                        if status and status >= 500:
                            load_st = "failed"
                        elif status and status >= 400:
                            load_st = "partial"
                        else:
                            load_st = "success"

                        homepage = _homepage_payload_from_probe(
                            url=url,
                            load_st=load_st,
                            metrics=metrics,
                            total_requests=total_requests_count["count"],
                            failed_requests=failed_requests_count["count"],
                        )
                    else:
                        _click_sel = str(scenario.get("click_selector") or "").strip()
                        if _click_sel:
                            # 游戏（UI 点击流）：goto(start_url) → 注册 WS → click → t0（点击后零点）→ 深度等待（避免 token 直链被 WAF 拦截）
                            entry_url = _scenario_url(base, scenario)
                            entry_wait = str(scenario.get("entry_wait_until") or "domcontentloaded")
                            click_to = int(scenario.get("click_timeout_ms") or 30000)
                            ws_times: list[float] = []
                            debug_timeline: list[str] = []
                            t_timeline0 = time.time()
                            mark = _make_timeline_mark(debug_timeline, t_timeline0, name)

                            def _on_ws_click(_ws: Any) -> None:
                                try:
                                    ws_times.append(time.perf_counter())
                                except Exception:
                                    pass

                            mark("点击流: 开始 goto 大厅 (start_url)")
                            response = await _goto_resilient(page, entry_url, entry_wait, timeout_ms)
                            mark("点击流: goto 大厅返回 (wait_until 策略完成)")
                            await _kalaroko_ui_breathe(
                                page,
                                pace,
                                progress=_progress,
                                hint=f"[UI 节奏] 「{name}」大厅 URL 已打开…" if pace > 0 else "",
                            )
                            # 通知条 → Guest → 再扫遮挡；再点游戏入口（无 auth.json 时）
                            await _prepare_kalaroko_lobby_after_navigation(
                                page, progress=_progress
                            )
                            mark("点击流: 大厅清场 (_prepare_kalaroko_lobby_after_navigation) 完成")
                            await _kalaroko_ui_breathe(
                                page,
                                pace,
                                progress=_progress,
                                hint=f"[UI 节奏] 即将点击「{name}」游戏入口…" if pace > 0 else "",
                            )
                            game_req_count = {"count": 0}
                            game_req_fail_count = {"count": 0}
                            game_console_err_count = {"count": 0}

                            def _on_game_req(_request: Any) -> None:
                                game_req_count["count"] += 1

                            def _on_game_req_failed(_request: Any) -> None:
                                game_req_fail_count["count"] += 1

                            def _on_game_console_err(msg: Any) -> None:
                                try:
                                    if msg.type == "error":
                                        game_console_err_count["count"] += 1
                                except Exception:
                                    pass

                            page.on("request", _on_game_req)
                            page.on("requestfailed", _on_game_req_failed)
                            page.on("console", _on_game_console_err)

                            page.on("websocket", _on_ws_click)
                            # 丢弃点击前大厅阶段误捕获的 WS（否则会立刻满足「已有 WS」导致墙钟≈0）
                            ws_times.clear()
                            online_players: dict[str, str | None] = _empty_online_players_dict()
                            try:
                                mark("点击流: 开始点击游戏入口 (Play / 卡片)")
                                online_players = await _diagnose_and_click_kalaroko_game_entry(
                                    page,
                                    click_selector=_click_sel,
                                    click_timeout_ms=click_to,
                                    scenario_name=name,
                                    scenario=scenario,
                                    progress=_progress,
                                    ui_pace_ms=pace,
                                    ui_cursor_moves=cursor_mv,
                                )
                                mark("点击流: 点击游戏入口 await 返回")
                                # 引擎加载零点：从「点击完成、即将进壳/iframe」起算，不含大厅寻址与点击耗时
                                t0 = time.perf_counter()
                                status = response.status if response else None
                                try:
                                    t_end, _canvas_seen, _race_reason = (
                                        await _game_deep_wait_after_goto(
                                            page,
                                            t0,
                                            timeout_ms,
                                            ws_times,
                                            click_flow=True,
                                            timeline_mark=mark,
                                        )
                                    )
                                except KalarokoE2EUserCancelled:
                                    raise
                                except Exception as e:
                                    logger.warning("[kalaroko_monitor] deep wait (click flow): %s", str(e)[:200])
                                    t_end = time.perf_counter()
                                    mark(
                                        "竞速等待异常: "
                                        + str(e).replace("\n", " ")[:160]
                                    )
                                final_url = page.url or ""
                                room_id = _extract_room_id_from_url(final_url)
                                real_engine_load_ms = max(0.0, (t_end - t0) * 1000.0)
                                real_i = int(round(real_engine_load_ms))
                                mark("_evaluate_metrics_with_retry 开始 (含短 settle / 重试)")
                                try:
                                    data = await _evaluate_metrics_with_retry(page)
                                except Exception as e:
                                    mark(
                                        "_evaluate_metrics_with_retry 异常: "
                                        + str(e).replace("\n", " ")[:120]
                                    )
                                    raise
                                mark("_evaluate_metrics_with_retry 完成")
                                metrics = data if isinstance(data, dict) else {}
                                shell_ttfb = metrics.get("ttfb_ms")
                                # UI 点击进游戏：默认要求真的进入 game-frame；否则视为「未打开游戏壳」，禁止 success + 几十毫秒误报
                                _req_gf = scenario.get("require_game_frame_url", True)
                                if isinstance(_req_gf, str):
                                    _req_gf = _req_gf.strip().lower() not in (
                                        "0",
                                        "false",
                                        "no",
                                        "off",
                                    )
                                shell_ok = "game-frame" in final_url.lower()
                                logger.info(
                                    "[kalaroko_monitor] 【%s】深度等待结束 | final_url=%s | "
                                    "game_frame_shell=%s | click后采集到的 WS 次数=%s",
                                    name,
                                    (final_url or "")[:320],
                                    shell_ok,
                                    len(ws_times),
                                )
                                if _req_gf and not shell_ok:
                                    load_st = "failed"
                                    logger.warning(
                                        "[kalaroko_monitor] UI 点击场景「%s」结束后 URL 仍未包含 game-frame，"
                                        "判定游戏壳未打开（与肉眼未见进游戏一致）：%s",
                                        name,
                                        final_url[:220],
                                    )
                                elif status and status >= 500:
                                    load_st = "failed"
                                elif status and status >= 400:
                                    load_st = "partial"
                                else:
                                    load_st = "success"
                                row = {
                                    "game_id": name,
                                    "path": _game_path_fallback(scenario, final_url),
                                    "ttfb_ms": (
                                        None
                                        if load_st == "failed" and _req_gf and not shell_ok
                                        else real_i
                                    ),
                                    "real_engine_load_ms": (
                                        None
                                        if load_st == "failed" and _req_gf and not shell_ok
                                        else round(real_engine_load_ms, 1)
                                    ),
                                    "shell_navigation_ttfb_ms": (
                                        None
                                        if load_st == "failed" and _req_gf and not shell_ok
                                        else shell_ttfb
                                    ),
                                    "load_status": load_st,
                                    "total_requests": int(game_req_count["count"]),
                                    "resource_errors_count": min(
                                        int(game_req_fail_count["count"]), 1_000_000
                                    ),
                                    "console_errors_count": min(
                                        int(game_console_err_count["count"]), 1_000_000
                                    ),
                                    "room_id": room_id,
                                    # 大厅卡片文案：全游戏/列表聚合口径，非牌桌内本局上桌人数
                                    "online_players": online_players,
                                    "table_seat_players": None,
                                }
                                row.update(_game_id_snapshot_fields(scenario, final_url))
                                row["debug_timeline"] = list(debug_timeline)
                                games_out.append(row)
                                # [战术撤离] 回平台首页，为下一游戏场景就位（非 about:blank）
                                mark("_tactical_retreat_to_platform_home 开始 (战术撤离→首页)")
                                try:
                                    await _tactical_retreat_to_platform_home(
                                        page, scenario, progress=_progress
                                    )
                                    mark("_tactical_retreat_to_platform_home 完成")
                                except Exception as e:
                                    mark(
                                        "_tactical_retreat_to_platform_home 异常: "
                                        + str(e).replace("\n", " ")[:160]
                                    )
                                    raise
                                finally:
                                    try:
                                        games_out[-1]["debug_timeline"] = list(
                                            debug_timeline
                                        )
                                    except Exception:
                                        pass
                                if _req_gf and not shell_ok:
                                    _wall = "N/A（未进入 game-frame，未计有效墙钟）"
                                else:
                                    _wall = f"墙钟≈{real_i}ms"
                                _progress(
                                    f"「{name}」采集结束：load_status={load_st}，{_wall}，"
                                    f"shell_navigation_ttfb_ms={row.get('shell_navigation_ttfb_ms')!s}，"
                                    f"total_requests={row.get('total_requests')!s}，"
                                    f"resource_errors_count={row.get('resource_errors_count')!s}，"
                                    f"console_errors_count={row.get('console_errors_count')!s}，"
                                    f"room_id={row.get('room_id')!s}，已战术撤离→首页"
                                )
                            finally:
                                try:
                                    page.remove_listener("websocket", _on_ws_click)
                                except Exception:
                                    pass
                                for _ev, _fn in (
                                    ("request", _on_game_req),
                                    ("requestfailed", _on_game_req_failed),
                                    ("console", _on_game_console_err),
                                ):
                                    try:
                                        page.remove_listener(_ev, _fn)
                                    except Exception:
                                        pass
                        else:
                            # 游戏（直链 goto）：domcontentloaded + 仿生竞速（WS / HTTP / Canvas）
                            ws_times = []
                            debug_timeline: list[str] = []
                            t_timeline0 = time.time()
                            mark = _make_timeline_mark(debug_timeline, t_timeline0, name)

                            def _on_ws(_ws: Any) -> None:
                                try:
                                    ws_times.append(time.perf_counter())
                                except Exception:
                                    pass

                            game_req_count = {"count": 0}
                            game_req_fail_count = {"count": 0}
                            game_console_err_count = {"count": 0}

                            def _on_game_req_g(_request: Any) -> None:
                                game_req_count["count"] += 1

                            def _on_game_req_failed_g(_request: Any) -> None:
                                game_req_fail_count["count"] += 1

                            def _on_game_console_err_g(msg: Any) -> None:
                                try:
                                    if msg.type == "error":
                                        game_console_err_count["count"] += 1
                                except Exception:
                                    pass

                            page.on("request", _on_game_req_g)
                            page.on("requestfailed", _on_game_req_failed_g)
                            page.on("console", _on_game_console_err_g)

                            page.on("websocket", _on_ws)
                            mark("直链: 即将 page.goto 游戏 URL (wait_until 策略)")
                            t0 = time.perf_counter()
                            try:
                                response = await _goto_resilient(page, url, wait_until, timeout_ms)
                            finally:
                                try:
                                    page.remove_listener("websocket", _on_ws)
                                except Exception:
                                    pass
                            mark("直链: goto 游戏 URL 返回")

                            try:
                                status = response.status if response else None
                                try:
                                    t_end, _canvas_seen, _race_reason = (
                                        await _game_deep_wait_after_goto(
                                            page,
                                            t0,
                                            timeout_ms,
                                            ws_times,
                                            timeline_mark=mark,
                                        )
                                    )
                                except KalarokoE2EUserCancelled:
                                    raise
                                except Exception as e:
                                    logger.warning("[kalaroko_monitor] deep wait: %s", str(e)[:200])
                                    t_end = time.perf_counter()
                                    mark(
                                        "竞速等待异常: "
                                        + str(e).replace("\n", " ")[:160]
                                    )

                                real_engine_load_ms = max(0.0, (t_end - t0) * 1000.0)
                                real_i = int(round(real_engine_load_ms))

                                mark("_evaluate_metrics_with_retry 开始 (含短 settle / 重试)")
                                try:
                                    data = await _evaluate_metrics_with_retry(page)
                                except Exception as e:
                                    mark(
                                        "_evaluate_metrics_with_retry 异常: "
                                        + str(e).replace("\n", " ")[:120]
                                    )
                                    raise
                                mark("_evaluate_metrics_with_retry 完成")
                                metrics = data if isinstance(data, dict) else {}
                                shell_ttfb = metrics.get("ttfb_ms")

                                final_url = page.url or ""
                                room_id = _extract_room_id_from_url(final_url)

                                if status and status >= 500:
                                    load_st = "failed"
                                elif status and status >= 400:
                                    load_st = "partial"
                                else:
                                    load_st = "success"

                                row = {
                                    "game_id": name,
                                    "path": _game_path_fallback(scenario, final_url or url),
                                    "ttfb_ms": real_i,
                                    "real_engine_load_ms": round(real_engine_load_ms, 1),
                                    "shell_navigation_ttfb_ms": shell_ttfb,
                                    "load_status": load_st,
                                    "total_requests": int(game_req_count["count"]),
                                    "resource_errors_count": min(
                                        int(game_req_fail_count["count"]), 1_000_000
                                    ),
                                    "console_errors_count": min(
                                        int(game_console_err_count["count"]), 1_000_000
                                    ),
                                    "room_id": room_id,
                                    "online_players": _empty_online_players_dict(),
                                    "table_seat_players": None,
                                }
                                row.update(_game_id_snapshot_fields(scenario, final_url or url))
                                row["debug_timeline"] = list(debug_timeline)
                                games_out.append(row)
                                # [战术撤离] 回平台首页，为下一游戏场景就位（非 about:blank）
                                mark("_tactical_retreat_to_platform_home 开始 (战术撤离→首页)")
                                try:
                                    await _tactical_retreat_to_platform_home(
                                        page, scenario, progress=_progress
                                    )
                                    mark("_tactical_retreat_to_platform_home 完成")
                                except Exception as e:
                                    mark(
                                        "_tactical_retreat_to_platform_home 异常: "
                                        + str(e).replace("\n", " ")[:160]
                                    )
                                    raise
                                finally:
                                    try:
                                        games_out[-1]["debug_timeline"] = list(
                                            debug_timeline
                                        )
                                    except Exception:
                                        pass
                                _progress(
                                    f"「{name}」采集结束：load_status={load_st}，墙钟≈{real_i}ms，"
                                    f"shell_navigation_ttfb_ms={shell_ttfb!s}，"
                                    f"total_requests={row.get('total_requests')!s}，"
                                    f"resource_errors_count={row.get('resource_errors_count')!s}，"
                                    f"console_errors_count={row.get('console_errors_count')!s}，"
                                    f"room_id={row.get('room_id')!s}，已战术撤离→首页"
                                )
                            finally:
                                for _ev, _fn in (
                                    ("request", _on_game_req_g),
                                    ("requestfailed", _on_game_req_failed_g),
                                    ("console", _on_game_console_err_g),
                                ):
                                    try:
                                        page.remove_listener(_ev, _fn)
                                    except Exception:
                                        pass
                except KalarokoE2EUserCancelled:
                    raise
                except asyncio.TimeoutError:
                    msg = f"timeout navigating to {url}"
                    browser_exceptions.append(
                        {"type": "error", "message": msg, "source": url, "line": None}
                    )
                    if is_home:
                        homepage = _empty_homepage_metrics(url, "timeout")
                    else:
                        row = {
                            "game_id": name,
                            "path": _game_path_fallback(scenario, url),
                            "ttfb_ms": None,
                            "real_engine_load_ms": None,
                            "shell_navigation_ttfb_ms": None,
                            "load_status": "timeout",
                            "total_requests": 0,
                            "resource_errors_count": 0,
                            "console_errors_count": 0,
                            "room_id": "N/A",
                            "online_players": _empty_online_players_dict(),
                            "table_seat_players": None,
                        }
                        row.update(_game_id_snapshot_fields(scenario, url))
                        games_out.append(row)
                except Exception as e:
                    logger.warning("scenario failed: %s: %s", name, str(e)[:500])
                    browser_exceptions.append(
                        {
                            "type": "error",
                            "message": str(e)[:2000],
                            "source": url,
                            "line": None,
                        }
                    )
                    if is_home:
                        homepage = _empty_homepage_metrics(url, "failed")
                    else:
                        row = {
                            "game_id": name,
                            "path": _game_path_fallback(scenario, url),
                            "ttfb_ms": None,
                            "real_engine_load_ms": None,
                            "shell_navigation_ttfb_ms": None,
                            "load_status": "failed",
                            "total_requests": 0,
                            "resource_errors_count": 0,
                            "console_errors_count": 0,
                            "room_id": "N/A",
                            "online_players": _empty_online_players_dict(),
                            "table_seat_players": None,
                        }
                        row.update(_game_id_snapshot_fields(scenario, url))
                        games_out.append(row)

            try:
                _progress("正在采集首页（W3C Navigation / Paint + 首屏网络计数）…")
                await _run_one(home_sc, is_home=True)
            except KalarokoE2EUserCancelled:
                raise
            except Exception as e:
                logger.exception("homepage fatal: %s", e)
                browser_exceptions.append(
                    {"type": "error", "message": str(e)[:2000], "source": None, "line": None}
                )
                if homepage is None:
                    homepage = _empty_homepage_metrics(
                        _scenario_url(base, home_sc), "failed"
                    )

            _ls = (homepage or {}).get("load_status")
            _progress(f"首页采集结束（load_status={_ls!r}）。")

            for idx, gs in enumerate(game_scenarios, start=1):
                if _e2e_user_cancel_requested():
                    raise KalarokoE2EUserCancelled()
                _gname = str(gs.get("name") or "game")
                _progress(f"游戏场景 [{idx}/{len(game_scenarios)}]：{_gname}（导航 + 深度等待）…")
                try:
                    await _run_one(gs, is_home=False)
                except KalarokoE2EUserCancelled:
                    raise
                except Exception as e:
                    logger.warning("game scenario wrapper: %s", str(e)[:500])
                    browser_exceptions.append(
                        {"type": "error", "message": str(e)[:2000], "source": None, "line": None}
                    )
                    _gu = _scenario_url(base, gs)
                    row = {
                        "game_id": str(gs.get("name") or "game"),
                        "path": _game_path_fallback(gs, _gu),
                        "ttfb_ms": None,
                        "real_engine_load_ms": None,
                        "shell_navigation_ttfb_ms": None,
                        "load_status": "failed",
                        "total_requests": 0,
                        "resource_errors_count": 0,
                        "console_errors_count": 0,
                        "room_id": "N/A",
                        "online_players": _empty_online_players_dict(),
                        "table_seat_players": None,
                    }
                    row.update(_game_id_snapshot_fields(gs, _gu))
                    games_out.append(row)

            if game_scenarios:
                _progress("全部游戏场景已遍历，正在读取 UA / 视口元数据…")

            if homepage is None:
                homepage = _empty_homepage_metrics(base, "failed")

            try:
                ua = await page.evaluate("() => navigator.userAgent")
            except Exception as e:
                en = type(e).__name__
                if "TargetClosed" in en or "closed" in str(e).lower():
                    logger.warning(
                        "[kalaroko_monitor] 读取 UA 失败（页或连接已关闭，可能已关闭相关标签）: %s",
                        str(e)[:240],
                    )
                    ua = "unavailable (page or browser closed)"
                else:
                    raise
            raw_meta = {
                "user_agent": ua,
                "playwright": True,
                "viewport": {"width": w, "height": h, "device_scale_factor": dsf},
            }

            out: dict[str, Any] = {
                "ok": True,
                "schema_version": _SCHEMA_VERSION,
                "run_id": rid,
                "captured_at": _utc_iso(),
                "network_profile": net_label,
                "homepage": homepage,
                "api_health": [],
                "games": games_out,
                "browser_exceptions": browser_exceptions,
                "aggregation_notes": (
                    ["使用内置默认场景 KALAROKO_DEFAULT_SCENARIOS（首页 + 4 款游戏）"]
                    if used_default_scenarios
                    else []
                ),
                "raw_meta": raw_meta,
            }
            return out
        except KalarokoE2EUserCancelled:
            _progress("用户已请求停止巡检，正在关闭浏览器会话…")
            return _err(
                "USER_CANCELLED",
                "巡检已由用户停止（下一检查点前已中止 Playwright）",
            )
        finally:
            # CDP 复用用户默认 context：可选「阅后即焚」关闭本轮巡检占用的标签，减轻 H5 长时内存泄漏
            #（会关掉该 Tab；仅无人值守/专用巡检 Tab 或已配合 KALAROKO_CDP_NEW_TAB 时建议开启）
            try:
                if (
                    not must_close_context
                    and page is not None
                    and _env_bool("KALAROKO_CDP_CLOSE_INSPECTION_TAB_AFTER_RUN", False)
                ):
                    try:
                        closed = bool(page.is_closed())
                    except Exception:
                        closed = True
                    if not closed:
                        await page.close()
                        logger.info(
                            "[kalaroko_monitor] CDP 阅后即焚：已关闭本轮巡检标签（"
                            "KALAROKO_CDP_CLOSE_INSPECTION_TAB_AFTER_RUN）"
                        )
            except Exception as e:
                logger.warning(
                    "[kalaroko_monitor] CDP 阅后即焚 page.close 失败: %s", str(e)[:300]
                )
            # 自建 / launch 的 context 必须显式 close，减轻 OOM；复用 CDP 用户默认 context 时不关
            try:
                if context is not None and must_close_context:
                    await context.close()
            except Exception as e:
                logger.warning("[kalaroko_monitor] context.close 失败: %s", str(e)[:300])
            try:
                if browser is not None:
                    await browser.close()
            except Exception as e:
                logger.warning("[kalaroko_monitor] browser.close 失败: %s", str(e)[:300])
            _kalaroko_clear_playwright_session()


def _summarize_fetch_api_health_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    """基于单次 fetch 的 items 生成 APM 风格聚合统计。"""
    total_calls = len(items)
    failed_calls = sum(1 for x in items if not x.get("healthy"))

    latencies: list[float] = []
    for x in items:
        if not x.get("healthy"):
            continue
        lm = x.get("latency_ms")
        try:
            v = float(lm) if lm is not None else 0.0
        except (TypeError, ValueError):
            v = 0.0
        if v > 0:
            latencies.append(v)

    avg_latency_ms: int | None = None
    min_latency_ms: int | None = None
    max_latency_ms: int | None = None
    if latencies:
        avg_latency_ms = int(round(sum(latencies) / len(latencies)))
        min_latency_ms = int(round(min(latencies)))
        max_latency_ms = int(round(max(latencies)))

    codes_set: set[Any] = set()
    for x in items:
        codes_set.add(x.get("status_code"))
    status_codes = sorted(codes_set, key=lambda z: (z is None, z if isinstance(z, int) else 0))

    return {
        "total_calls": total_calls,
        "failed_calls": failed_calls,
        "latencies": [round(v, 3) for v in latencies],
        "avg_latency_ms": avg_latency_ms,
        "min_latency_ms": min_latency_ms,
        "max_latency_ms": max_latency_ms,
        "status_codes": status_codes,
    }


@mcp.tool()
async def fetch_api_health(
    endpoints: list,
    run_id: str | None = None,
    parallel: bool = True,
) -> dict[str, Any]:
    """
    对 endpoints 并发发起 HTTP 探测，返回延迟、状态码与健康位；并附带 ``summary`` 聚合统计。
    """
    if not endpoints or not isinstance(endpoints, list):
        return _err("INVALID_ENDPOINTS", "endpoints 必须为非空数组")

    sem = asyncio.Semaphore(32)

    async def _one(ep: dict[str, Any]) -> dict[str, Any]:
        eid = str(ep.get("id") or "ep")
        url = str(ep.get("url") or "").strip()
        method = str(ep.get("method") or "GET").upper()
        if method not in ("GET", "HEAD"):
            method = "GET"
        exp = int(ep.get("expected_status") or 200)
        timeout_ms = int(ep.get("timeout_ms") or 15000)
        if not url or not _host_allowed(url):
            return {
                "id": eid,
                "url": url or "about:blank",
                "method": method,
                "status_code": None,
                "latency_ms": 0.0,
                "healthy": False,
                "error": "HOST_NOT_ALLOWED_OR_EMPTY",
            }
        async with sem:
            t0 = time.perf_counter()
            try:
                async with httpx.AsyncClient(
                    http2=False,
                    follow_redirects=True,
                    verify=True,
                ) as client:
                    req = client.head if method == "HEAD" else client.get
                    r = await req(url, timeout=timeout_ms / 1000.0)
                    ms = (time.perf_counter() - t0) * 1000.0
                    code = r.status_code
                    healthy = code == exp
                    return {
                        "id": eid,
                        "url": url,
                        "method": method,
                        "status_code": code,
                        "latency_ms": round(ms, 3),
                        "healthy": healthy,
                        "error": None,
                    }
            except Exception as e:
                ms = (time.perf_counter() - t0) * 1000.0
                return {
                    "id": eid,
                    "url": url,
                    "method": method,
                    "status_code": None,
                    "latency_ms": round(ms, 3),
                    "healthy": False,
                    "error": str(e)[:500],
                }

    if parallel:
        items = await asyncio.gather(*(_one(ep) for ep in endpoints))
    else:
        items = []
        for ep in endpoints:
            items.append(await _one(ep))

    item_list = list(items)
    summary = _summarize_fetch_api_health_items(item_list)

    return {
        "ok": True,
        "run_id": (run_id or "").strip() or None,
        "captured_at": _utc_iso(),
        "summary": summary,
        "items": item_list,
    }


def _default_storage_path(storage: str | None, path: str | None) -> Path:
    if path and str(path).strip():
        return Path(path).expanduser().resolve()
    st = (storage or "jsonl").lower()
    if st == "sqlite":
        return (Path.home() / ".jachin" / "kalaroko_perf" / "history.sqlite").resolve()
    return _DEFAULT_JSONL.resolve()


def _jsonl_append(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with kalaroko_e2e_jsonl_lock(path):
        with path.open("a", encoding="utf-8") as f:
            f.write(line)


def _jsonl_read_all(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with kalaroko_e2e_jsonl_lock(path):
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def _jsonl_write_all(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with kalaroko_e2e_jsonl_lock(path):
        with path.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")


@mcp.tool()
def manage_perf_history(
    operation: str,
    storage: str | None = None,
    path: str | None = None,
    record: dict | None = None,
    limit: int | None = None,
    older_than_days: int | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """
    本地历史：append / query_recent / prune / get_by_run_id（jsonl 或 sqlite）。

    Args:
        operation: append | query_recent | prune | get_by_run_id
        storage: jsonl | sqlite
        path: 存储文件路径；默认 ~/.jachin/kalaroko_perf/history.jsonl
        record: append 时的完整快照对象
        limit: query_recent 条数
        older_than_days: prune 时删除早于该天数的记录
        run_id: get_by_run_id 用
    """
    op = (operation or "").strip().lower()
    st = (storage or "jsonl").lower().strip()
    sp = _default_storage_path(st, path)

    if op == "append":
        if not record or not isinstance(record, dict):
            return _err("RECORD_REQUIRED", "append 需要非空 record")
        rid = str(record.get("run_id") or uuid.uuid4())
        record = {**record, "run_id": rid}
        if st == "sqlite":
            sp.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(sp))
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS perf_records (
                      run_id TEXT PRIMARY KEY,
                      captured_at TEXT NOT NULL,
                      payload_json TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    "INSERT OR REPLACE INTO perf_records (run_id, captured_at, payload_json) VALUES (?,?,?)",
                    (
                        rid,
                        str(record.get("captured_at") or _utc_iso()),
                        json.dumps(record, ensure_ascii=False),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        else:
            _jsonl_append(sp, record)
        return {"ok": True, "stored_run_id": rid, "storage_path": str(sp)}

    if op == "query_recent":
        lim = int(limit or 20)
        if lim < 1:
            lim = 1
        if st == "sqlite":
            if not sp.is_file():
                return {"ok": True, "records": []}
            conn = sqlite3.connect(str(sp))
            try:
                cur = conn.execute(
                    "SELECT payload_json FROM perf_records ORDER BY captured_at DESC LIMIT ?",
                    (lim,),
                )
                recs = []
                for row in cur.fetchall():
                    try:
                        recs.append(json.loads(row[0]))
                    except json.JSONDecodeError:
                        continue
                return {"ok": True, "records": recs}
            finally:
                conn.close()
        rows = _jsonl_read_all(sp)

        def _key(r: dict[str, Any]) -> str:
            return str(r.get("captured_at") or "")

        rows.sort(key=_key, reverse=True)
        return {"ok": True, "records": rows[:lim]}

    if op == "get_by_run_id":
        rid = (run_id or "").strip()
        if not rid:
            return _err("RUN_ID_REQUIRED", "get_by_run_id 需要 run_id")
        if st == "sqlite":
            if not sp.is_file():
                return {"ok": True, "record": None}
            conn = sqlite3.connect(str(sp))
            try:
                cur = conn.execute(
                    "SELECT payload_json FROM perf_records WHERE run_id = ? LIMIT 1",
                    (rid,),
                )
                row = cur.fetchone()
                if not row:
                    return {"ok": True, "record": None}
                try:
                    return {"ok": True, "record": json.loads(row[0])}
                except json.JSONDecodeError:
                    return {"ok": True, "record": None}
            finally:
                conn.close()
        for r in _jsonl_read_all(sp):
            if str(r.get("run_id")) == rid:
                return {"ok": True, "record": r}
        return {"ok": True, "record": None}

    if op == "prune":
        days = int(older_than_days or 30)
        cutoff = time.time() - days * 86400

        def _parse_ts(s: str | None) -> float | None:
            if not s:
                return None
            try:
                if s.endswith("Z"):
                    s = s[:-1] + "+00:00"
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
                return dt.timestamp()
            except Exception:
                return None

        if st == "sqlite":
            if not sp.is_file():
                return {"ok": True, "removed_count": 0}
            conn = sqlite3.connect(str(sp))
            removed = 0
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS perf_records (
                      run_id TEXT PRIMARY KEY,
                      captured_at TEXT NOT NULL,
                      payload_json TEXT NOT NULL
                    )
                    """
                )
                cur = conn.execute("SELECT run_id, captured_at FROM perf_records")
                to_del = []
                for rid_row, cap in cur.fetchall():
                    ts = _parse_ts(cap)
                    if ts is not None and ts < cutoff:
                        to_del.append(rid_row)
                for rid_row in to_del:
                    conn.execute("DELETE FROM perf_records WHERE run_id = ?", (rid_row,))
                    removed += 1
                conn.commit()
            finally:
                conn.close()
            return {"ok": True, "removed_count": removed}

        rows = _jsonl_read_all(sp)
        kept: list[dict[str, Any]] = []
        removed = 0
        for r in rows:
            ts = _parse_ts(str(r.get("captured_at")))
            if ts is not None and ts < cutoff:
                removed += 1
            else:
                kept.append(r)
        _jsonl_write_all(sp, kept)
        return {"ok": True, "removed_count": removed}

    return _err("UNKNOWN_OPERATION", f"未知 operation: {operation}")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
