#!/usr/bin/env python3
"""
Kalaroko Web 性能自动化监控哨兵 — MCP Server（stdio / FastMCP）

契约：docs/KALAROKO_WEB_PERF_MONITOR_TDD.md

运行：
  python -m l3_client.local_mcps.kalaroko_monitor.mcp_kalaroko_monitor
  或
  python l3_client/local_mcps/kalaroko_monitor/mcp_kalaroko_monitor.py

依赖：mcp, playwright, httpx（需 `playwright install chromium`）。巡检浏览器：须设置 `KALAROKO_CDP_ENDPOINT` 并预先以远程调试端口启动 Chrome（`connect_over_cdp`，不自动 launch）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import sys
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

# 默认监控任务：首页 + 三款游戏。
# 游戏入口改为「首页 start_url + UI 点击流」，避免带 partyId/token 的 game-frame 直链被 WAF/业务网关拦截。
# click_selector 须随前端 DOM 调整；可用 Playwright 文本选择器或 CSS（见 Playwright selector 语法）。
# UI 点击流默认 require_game_frame_url=True：采数结束须出现 /game-frame 主文档 URL，否则 load_status=failed（防未真开游戏壳仍 success）。
_DEFAULT_START = "https://kalaroko.com/"
KALAROKO_DEFAULT_SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "name": "homepage",
        "start_url": _DEFAULT_START,
        "wait_until": "load",
        "timeout_ms": 60000,
    },
    {
        "name": "tongits_king",
        "document_game_id": 5,
        "start_url": _DEFAULT_START,
        # 首页卡片常为「插图 + 底部标题」：精确 text='…' 易点到不可交互文案节点；正则略宽松
        "click_selector": r"text=/Tongits\s*King/i",
        "entry_wait_until": "load",
        "click_timeout_ms": 10000,
        "wait_until": "networkidle",
        "timeout_ms": 90000,
    },
    {
        "name": "royal_pusoy",
        "document_game_id": 7,
        "start_url": _DEFAULT_START,
        "click_selector": r"text=/Royal\s*Pusoy/i",
        "entry_wait_until": "load",
        "click_timeout_ms": 10000,
        "wait_until": "networkidle",
        "timeout_ms": 90000,
    },
    {
        "name": "color_blitz",
        "document_game_id": 6,
        # 文案常 2～3 处重复（横幅/列表）；与 Tongits 的「多命中取 .last」同理，避免点到不可导航层
        "prefer_last_on_ambiguous_entry": True,
        "start_url": _DEFAULT_START,
        "click_selector": r"text=/Color\s*Blitz\s*Social/i",
        "entry_wait_until": "load",
        "click_timeout_ms": 10000,
        "wait_until": "networkidle",
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

    失败根因常为：(1) 人数异步写入 —— 由调用方在 visible 后追加等待；
    (2) ``innerText`` 不含隐藏/未排版节点 —— 补充 ``textContent``；
    (3) 人数在兄弟列/另一行 —— 向上遍历（最多 6 层）并合并父级 **所有子节点** 文案；
    (4) 仅在 ``data-*`` / ``title`` —— 一并拼接。
    向上遍历时若某层 ``innerText`` 长度超过 150，视为已进到游戏列表等大容器，停止爬升以免串台。
    """
    parts: list[str] = []
    try:
        parts.append((await target.inner_text()).strip())
    except Exception:
        pass
    try:
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
            }"""
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
    """goto 降级顺序：游戏默认 networkidle → load → domcontentloaded → commit。"""
    p = (wait_until or "load").strip().lower()
    if p == "networkidle":
        return ["networkidle", "load", "domcontentloaded", "commit"]
    if p == "load":
        return ["load", "domcontentloaded", "commit"]
    return [p, "domcontentloaded", "commit"]


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
    导航到 url：优先使用调用方 wait_until；若遇 net::ERR_ABORTED 等中断，按策略链降级。
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
            }"""
        )
    except Exception:
        return False


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
        for idx in range(cnt):
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
    大厅游戏入口点击：先打诊断日志，再 scroll_into_view + 常规 click；
    失败时用 JS 在「可点击祖先 / 卡片容器」上触发 click（应对标题 div 不接收点击、事件绑在外层的情况）。

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
    if gid_wait is not None:
        href_union = ",".join(_href_anchor_selectors_for_document_game_id(gid_wait))
        found_href, cancelled_href = await _abortable_wait_for_selector_attached(
            page, href_union, 15000
        )
        if cancelled_href:
            raise KalarokoE2EUserCancelled()
        if not found_href:
            logger.debug(
                "[kalaroko_monitor] 【%s】未在 15s 内等到含 game_id 的入口链接（列表可能懒加载），尝试滚动后再等 …",
                scenario_name,
            )
            try:
                await page.evaluate(
                    "() => { try { window.scrollTo(0, document.body.scrollHeight); } catch (e) {} }"
                )
                if await _abortable_wait_for_timeout(page, 600):
                    raise KalarokoE2EUserCancelled()
                found2, cancelled2 = await _abortable_wait_for_selector_attached(
                    page, href_union, 8000
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

    st_to = max(4000, min(int(click_timeout_ms), 120000))
    await target.scroll_into_view_if_needed(timeout=st_to)
    _raise_if_e2e_cancelled()
    await target.wait_for(state="visible", timeout=st_to)
    logger.info("[kalaroko_monitor] 【%s】已完成 scroll_into_view + wait visible", scenario_name)

    stats_wait = _env_int("KALAROKO_LOBBY_STATS_WAIT_MS", 2000, vmin=0, vmax=12000)
    if stats_wait:
        if progress:
            progress(
                f"「{scenario_name}」等待大厅在线人数/统计延迟加载（{stats_wait}ms）…"
            )
        if await _abortable_wait_for_timeout(page, stats_wait):
            raise KalarokoE2EUserCancelled()

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

    bottom_reserve = _env_int("KALAROKO_BOTTOM_CHROME_RESERVE_PX", 112, vmin=56, vmax=240)
    suppress_tabbar = _env_bool("KALAROKO_SUPPRESS_TABBAR_PE", True)
    err_first: Exception | None = None
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
        _clk_kw: dict[str, Any] = {"timeout": click_timeout_ms}
        if _click_delay > 0:
            _clk_kw["delay"] = _click_delay
        await target.click(**_clk_kw)
        logger.info("[kalaroko_monitor] 【%s】Playwright 常规 click() 成功", scenario_name)
    except Exception as e_click:
        err_first = e_click
        logger.warning(
            "[kalaroko_monitor] 【%s】常规 click 失败（常为底栏 _app_tabbar 截获命中或文案层不可点），"
            "尝试更大避让 + force 点击: %s",
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
            _fkw: dict[str, Any] = {"timeout": _fto, "force": True}
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
                handle = await target.element_handle(timeout=8000)
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

    try:
        if await _abortable_wait_for_timeout(page, 320):
            raise KalarokoE2EUserCancelled()
        au = (page.url or "")[:320]
        logger.info("[kalaroko_monitor] 【%s】点击后短 settle，当前 url=%s", scenario_name, au)
        if progress:
            progress(f"「{scenario_name}」点击后 url: {au[:140]}…")
    except Exception:
        pass

    return online_hint


async def _await_first_websocket_or_deadline(ws_times: list[float], deadline: float) -> None:
    """在 deadline 前轮询，直到 ws_times 非空（由 page.on('websocket') 填充）。"""
    try:
        while time.perf_counter() < deadline:
            if _e2e_user_cancel_requested():
                raise KalarokoE2EUserCancelled()
            if ws_times:
                return
            await asyncio.sleep(0.05)
    except KalarokoE2EUserCancelled:
        raise
    except Exception:
        pass


async def _game_deep_wait_after_goto(
    page: Any,
    t_start: float,
    timeout_ms: int,
    ws_times: list[float],
    *,
    click_flow: bool = False,
) -> tuple[float, bool]:
    """
    goto 返回后继续：可选等待首个 WebSocket、canvas。
    若 ``click_flow`` 为真：先短暂等待导航到 ``game-frame``（与大厅直链 SPA 对齐）；
    且当 URL 仍不含 ``game-frame`` 时**不**用全页第一个 ``canvas`` 当作引擎就绪（避免轮播/广告 canvas 导致 41ms 假阳性）。

    返回 (t_end_perf, canvas_seen)。
    """
    deadline = t_start + max(0.001, timeout_ms / 1000.0)
    t_nav = time.perf_counter()
    if _e2e_user_cancel_requested():
        raise KalarokoE2EUserCancelled()
    if click_flow:
        # 等到主文档 URL 进入 game-frame（与业务「游戏壳」一致）；分段轮询以便响应停止巡检
        try:
            rem_url = deadline - time.perf_counter()
            if rem_url > 0:
                cap_ms = int(min(float(timeout_ms), rem_url * 1000))
                if cap_ms >= 400:
                    nav_deadline = time.perf_counter() + cap_ms / 1000.0
                    while time.perf_counter() < nav_deadline:
                        if _e2e_user_cancel_requested():
                            raise KalarokoE2EUserCancelled()
                        try:
                            u = (page.url or "").lower()
                            if "game-frame" in u:
                                break
                        except Exception:
                            pass
                        await asyncio.sleep(0.2)
        except KalarokoE2EUserCancelled:
            raise
        except Exception:
            pass

    try:
        rem = deadline - time.perf_counter()
        if rem > 0 and not ws_times:
            cap = min(25.0, rem)
            await asyncio.wait_for(
                _await_first_websocket_or_deadline(ws_times, min(deadline, time.perf_counter() + cap)),
                timeout=cap,
            )
    except asyncio.TimeoutError:
        pass
    except Exception as e:
        logger.warning("[kalaroko_monitor] post-goto ws wait: %s", str(e)[:200])

    t_canvas: float | None = None
    canvas_seen = False
    try:
        rem_c = deadline - time.perf_counter()
        if rem_c > 0:
            ms = min(15000, int(rem_c * 1000))
            if ms >= 200:
                url_now = ""
                try:
                    url_now = page.url or ""
                except Exception:
                    pass
                skip_loose_canvas = click_flow and "game-frame" not in url_now
                if skip_loose_canvas:
                    pass
                else:
                    await asyncio.wait_for(
                        page.wait_for_selector("canvas", timeout=ms, state="attached"),
                        timeout=min(15.0, rem_c),
                    )
                    t_canvas = time.perf_counter()
                    canvas_seen = True
    except asyncio.TimeoutError:
        pass
    except Exception:
        pass

    t_ws = max(ws_times) if ws_times else t_nav
    t_end = max(t_nav, t_ws, t_canvas if t_canvas is not None else t_nav)
    return (t_end, canvas_seen)


async def _evaluate_metrics_with_retry(page: Any) -> Any:
    """
    game-frame 等页面可能在 domcontentloaded 后仍发生子导航，导致 evaluate 时上下文被销毁；
    短暂 settle + 有限次重试，避免单场景误报失败。
    """
    if await _abortable_wait_for_timeout(page, 600):
        raise KalarokoE2EUserCancelled()
    last_err: Exception | None = None
    for attempt in range(4):
        if _e2e_user_cancel_requested():
            raise KalarokoE2EUserCancelled()
        try:
            return await page.evaluate(_METRICS_JS)
        except Exception as e:
            last_err = e
            es = str(e).lower()
            if (
                "execution context was destroyed" not in es
                and "cannot find context" not in es
                and "target closed" not in es
            ):
                raise
            await page.wait_for_timeout(350 * (attempt + 1))
    assert last_err is not None
    raise last_err


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
            await loc.first.click(timeout=clk)
            dismissed += 1
            logger.info("[kalaroko_monitor] %s", label)
            if progress:
                progress(label)
            await page.wait_for_timeout(280)
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
                await enter_w.click(timeout=clk)
                await page.wait_for_timeout(380)
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
        await enter_btn.click(timeout=clk)
        await page.wait_for_timeout(420)
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
                await exit_btn.click(force=True)
                await page.wait_for_timeout(500)
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
    若本轮消除过任一障碍，等待 1.5s 后复扫（最多 3 轮），最后再等 1s 让 DOM 稳定。

    返回：是否曾在任一轮成功点击过 Continue with Guest（无登录框时 False）。
    """
    if progress:
        progress("开始大厅环境动态清场…")

    await page.wait_for_timeout(2500)

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
                progress(
                    f"第 {sweep + 1} 轮清场消灭了弹窗/遮罩，等待 1.5s 后复扫…"
                )
            await page.wait_for_timeout(1500)
        else:
            if progress:
                progress(f"第 {sweep + 1} 轮扫描大厅干净，清场完毕。")
            break

    await page.wait_for_timeout(1000)
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
            await locator.click(timeout=clk)
            await page.wait_for_timeout(450)
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
        await legacy.click(timeout=clk, force=True)
        await page.wait_for_timeout(450)
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
                await ex_loc.first.click(timeout=clk)
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
                await hdr_btns.nth(n - 1).click(timeout=clk)
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
                    await hdr_btns.nth(n2 - 2).click(timeout=clk)
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
                    await hdr_btns.nth(0).click(timeout=clk)
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


async def _probe_cdp_page_alive(pg: Any) -> bool:
    try:
        if pg.is_closed():
            return False
        await asyncio.wait_for(pg.evaluate("() => 1"), timeout=2.5)
        return True
    except (asyncio.TimeoutError, Exception):
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

    仅靠 ``is_closed()`` 不够：多标签下常有僵尸 Tab，须 evaluate 探活。
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
                if not await _probe_cdp_page_alive(pg):
                    continue
                if _cdp_page_url_matches_preferred_host(u, ph):
                    logger.info(
                        "[kalaroko_monitor] CDP 选用标签页（URL 含目标域 %s，对齐 K11 host 匹配）: %s",
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
                if not await _probe_cdp_page_alive(pg):
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
            if not await _probe_cdp_page_alive(pg):
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

    scored.sort()
    if scored:
        _vt, _ft, _nc, _ni, ci, ctx, pg = scored[0]
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
    优先 **CDP** 连接已有 Chrome；失败或超时则 **回退** ``chromium.launch(headless=True)`` 独立进程。

    ``preferred_host``：本轮巡检 ``base_url`` 的主机名（小写），用于与 K11 冒烟一致地 **优先绑定含该域的 Tab**，
    避免打在 DevTools/其他站/空白页上却仍能「出数」。

    返回 ``(browser, context, page, must_close_context)``：
    - ``must_close_context=True``：本轮在 ``finally`` 中必须 ``await context.close()``（自建 context 或 launch 实例），防泄漏。
    - CDP 复用用户默认 context 时为 ``False``（勿关用户标签页）。
    """
    vp = {"width": int(viewport_width), "height": int(viewport_height)}
    dsf = float(device_scale_factor)

    endpoint = (_kalaroko_cdp_endpoint() or "").strip() or "http://127.0.0.1:9222"

    try:
        browser = await asyncio.wait_for(
            playwright.chromium.connect_over_cdp(endpoint),
            timeout=30.0,
        )
    except Exception as e:
        logger.warning(
            "[kalaroko_monitor] CDP 连接失败或超时 (%s)，回退 Playwright launch：%s",
            endpoint,
            str(e)[:400],
        )
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

    **游戏场景深度感知（缓解 H5「假加载」）**：默认 ``wait_until=networkidle``；导航前后监听
    ``page.on("websocket")`` 记录首个 WebSocket；goto 后可选等待首个 WS（有界）与 ``canvas`` 出现；
    墙钟 ``real_engine_load_ms`` 写入 JSON，并用其覆盖游戏条目的 **ttfb_ms**（原 Navigation Timing
    保留在 **shell_navigation_ttfb_ms** 供对照）。失败路径均有 try/except，避免无限阻塞。

    Args:
        base_url: 站点根 URL，默认 https://kalaroko.com
        scenarios: 场景列表（name、path 或 full_url、**start_url**、**click_selector**、wait_until、timeout_ms；
            点击流可选 **require_game_frame_url**（默认 True）：采数结束前主文档 URL 须含 ``game-frame``，
            否则本条 **load_status=failed**，避免未见游戏打开仍上报 success）。
            游戏场景可选 **document_game_id** 与 Word/BI 报告小节 game_id 对齐）。**传 null 或 [] 时自动使用**
            内置 ``KALAROKO_DEFAULT_SCENARIOS``（首页 + 三款游戏；游戏默认从首页点击进入）。浏览器：
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
                wait_until = str(scenario.get("wait_until") or "load")
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
                            # 游戏（UI 点击流）：goto(start_url) → 注册 WS → t0 → click → 深度等待（避免 token 直链被 WAF 拦截）
                            entry_url = _scenario_url(base, scenario)
                            entry_wait = str(scenario.get("entry_wait_until") or "load")
                            click_to = int(scenario.get("click_timeout_ms") or 30000)
                            ws_times: list[float] = []

                            def _on_ws_click(_ws: Any) -> None:
                                try:
                                    ws_times.append(time.perf_counter())
                                except Exception:
                                    pass

                            response = await _goto_resilient(page, entry_url, entry_wait, timeout_ms)
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
                            t0 = time.perf_counter()
                            online_players: dict[str, str | None] = _empty_online_players_dict()
                            try:
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
                                status = response.status if response else None
                                try:
                                    t_end, _canvas_seen = await _game_deep_wait_after_goto(
                                        page,
                                        t0,
                                        timeout_ms,
                                        ws_times,
                                        click_flow=True,
                                    )
                                except KalarokoE2EUserCancelled:
                                    raise
                                except Exception as e:
                                    logger.warning("[kalaroko_monitor] deep wait (click flow): %s", str(e)[:200])
                                    t_end = time.perf_counter()
                                final_url = page.url or ""
                                room_id = _extract_room_id_from_url(final_url)
                                real_engine_load_ms = max(0.0, (t_end - t0) * 1000.0)
                                real_i = int(round(real_engine_load_ms))
                                data = await _evaluate_metrics_with_retry(page)
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
                                games_out.append(row)
                                # [战术撤离] 回平台首页，为下一游戏场景就位（非 about:blank）
                                await _tactical_retreat_to_platform_home(
                                    page, scenario, progress=_progress
                                )
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
                            # 游戏（直链 goto）：networkidle + WebSocket 嗅探 + 可选 canvas
                            ws_times = []

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
                            t0 = time.perf_counter()
                            try:
                                response = await _goto_resilient(page, url, wait_until, timeout_ms)
                            finally:
                                try:
                                    page.remove_listener("websocket", _on_ws)
                                except Exception:
                                    pass

                            try:
                                status = response.status if response else None
                                try:
                                    t_end, _canvas_seen = await _game_deep_wait_after_goto(
                                        page, t0, timeout_ms, ws_times
                                    )
                                except KalarokoE2EUserCancelled:
                                    raise
                                except Exception as e:
                                    logger.warning("[kalaroko_monitor] deep wait: %s", str(e)[:200])
                                    t_end = time.perf_counter()

                                real_engine_load_ms = max(0.0, (t_end - t0) * 1000.0)
                                real_i = int(round(real_engine_load_ms))

                                data = await _evaluate_metrics_with_retry(page)
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
                                games_out.append(row)
                                # [战术撤离] 回平台首页，为下一游戏场景就位（非 about:blank）
                                await _tactical_retreat_to_platform_home(
                                    page, scenario, progress=_progress
                                )
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
                    ["使用内置默认场景 KALAROKO_DEFAULT_SCENARIOS（首页 + 3 款游戏）"]
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
