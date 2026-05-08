#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K11 游戏冒烟（L3 Agent 决策 + Playwright CDP 执行「手脚」）

验证项（对齐 docs/K11_平台冒烟测试用例 · P0）：
  - 各游戏正常运行：大厅 → 入局 →（自动过程）→ 结算 → 退出
  - 游戏金币同步：进场前/退场后 DOM 快照对比（与 Kalaroko 监控共用同一 CDP 会话思路）

架构：
  - 本脚本：采集多 frame 文本树、POST L3 ``/api/v3/agent/run``、解析 JSON 指令、``js_click``/``click``/``wait``、记录金币候选值。
  - ``docs/tests/game_test_skill.md``：领域知识与输出 JSON schema（注入每轮 user_input）。
  - **感知-反馈闭环**：同一 ``chat_id`` 下，解析失败或「点击无 DOM/URL 变化」时不中断整场测试——向前追加 ``<<< FEEDBACK_FROM_EXECUTOR >>>`` 并重 POST；RUNTIME 注入 ``last_action`` / ``last_execution_result`` / ``failure_streak`` 以锚定任务主轴。

约束：
  - 无截图；除 Agent 显式 ``wait`` 外不使用 ``time.sleep``（可用 ``asyncio.sleep`` 仅实现 wait 指令）。
  - 跨 iframe：遍历 ``page.frames`` 采集与执行点击。

前置：
  - L3 已启动（默认 ``http://127.0.0.1:18991``），Engine 就绪。
  - Chrome 远程调试（``KALAROKO_CDP_ENDPOINT`` 或 ``--cdp-http``），``pip install playwright``。

说明（金币）：
  ``mcp_kalaroko_monitor`` 未提供独立「读金币」工具；本脚本在同一 CDP 页面对 DOM 做与运营排查一致的文本采集。
  若需完全由 MCP 侧拉数，须在 L3 侧增加专用工具后再接 ``/api/v3/mcp/execute``（本脚本不内置）。

调试日志（默认开启）：
  每次 POST L3 会将 **完整 user_input** 与 **整包 HTTP JSON 响应**、**payload.answer**、**解析出的 action JSON** 追加写入：
  ``%USERPROFILE%\\.jachin\\jachin_debug\\健康skill\\k11_l3_agent_games_smoke_<UTC时间戳>_<随机>.log``
  可用环境变量 ``K11_SKILL_DEBUG_LOG_DIR`` 或参数 ``--skill-debug-log-dir`` 覆盖目录；``--no-skill-debug-log`` 关闭。

用法（仓库根）：
  python scripts/test_k11_l3_agent_games_smoke.py
  python scripts/test_k11_l3_agent_games_smoke.py --games "Tongits King" "Bato-Bato Pick"
  python scripts/test_k11_l3_agent_games_smoke.py --l3-base http://127.0.0.1:18991 --max-rounds 60
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import uuid
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

_env_root = (os.environ.get("JACHIN_APP_ROOT") or "").strip()
ROOT = Path(_env_root).resolve() if _env_root else Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", encoding="utf-8")
except ImportError:
    pass
except OSError:
    pass

DEFAULT_TARGET = os.environ.get("K11_BROWSER_CONTEXT_URL", "https://www.kalaroko.com/")
SKILL_PATH = ROOT / "docs" / "tests" / "game_test_skill.md"
DEFAULT_GAMES = ("Tongits King", "Bato-Bato Pick")

# 单轮外层迭代内，解析失败时向同一 chat_id 重复 POST 的最大次数（不含首次）
PARSE_ATTEMPTS_PER_ROUND = 6

_FEEDBACK_PARSE_INVALID = (
    "ERROR: 你的输出不符合 JSON 规范，且包含了解释性文字。"
    "请严格遵守 SKILL_DOCUMENT，立即重新输出 action 指令。"
)


def _default_skill_debug_log_dir() -> Path:
    """与约定一致：``~/.jachin/jachin_debug/健康skill``（可用 ``JACHIN_HOME`` 覆盖根目录）。"""
    jhome = os.environ.get("JACHIN_HOME", "").strip()
    root = Path(jhome).expanduser().resolve() if jhome else (Path.home() / ".jachin")
    return root / "jachin_debug" / "健康skill"


def _write_skill_debug_header(path: Path, *, meta: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "k11_l3_agent_games_smoke — L3 往返调试日志（完整 user_input + HTTP 响应）",
        f"started_utc={datetime.now(timezone.utc).isoformat()}",
        "",
    ]
    for k, v in meta.items():
        lines.append(f"{k}={v}")
    lines.extend(["", "=" * 96, ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def _append_skill_debug_round(
    path: Path,
    *,
    game: str,
    iteration: int,
    session_note: str,
    post_url: str,
    request_body: dict[str, Any],
    user_input_full: str,
    http_status: int,
    payload: dict[str, Any] | str,
    parsed_action: dict[str, Any] | None,
    raw_answer: str,
) -> None:
    """每次 POST L3 后追加一轮记录。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    utc = datetime.now(timezone.utc).isoformat()
    sep = "=" * 96

    def _safe_json(obj: Any, *, lim: int | None = None) -> str:
        try:
            s = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
        except Exception:
            s = repr(obj)
        if lim is not None and len(s) > lim:
            return s[:lim] + f"\n…（截断，共 {len(s)} 字符）"
        return s

    req_echo = {k: v for k, v in request_body.items() if k != "user_input"}
    req_echo["user_input_chars"] = len(user_input_full or "")
    blk = [
        "",
        sep,
        f"utc={utc}",
        f"game={game!r}",
        f"iteration={iteration}",
        f"session_note={session_note!r}",
        f"post_url={post_url}",
        "",
        "--- request_json（不含 user_input 正文，长度见 user_input_chars） ---",
        _safe_json(req_echo),
        "",
        "--- user_input（传给 L3 /api/v3/agent/run 的完整提示词） ---",
        user_input_full or "",
        "",
        f"--- http_status ---\n{http_status}",
        "",
        "--- response_payload（L3 返回 JSON 整包；answer 内为模型输出） ---",
        _safe_json(payload) if isinstance(payload, (dict, list)) else str(payload),
        "",
        "--- answer（payload.answer 原文，即模型对用户可见的最终字符串） ---",
        raw_answer or "",
        "",
        "--- parsed_action（Executor 从 answer 中解析出的 JSON 指令；若无则为 null） ---",
        "null" if parsed_action is None else _safe_json(parsed_action),
        "",
        sep,
        "",
    ]
    with path.open("a", encoding="utf-8") as fp:
        fp.write("\n".join(blk))


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


def _cdp_tab_url_driver_safe(url: str) -> bool:
    u = (url or "").strip().lower()
    if u.startswith("devtools://") or u.startswith("chrome-devtools://"):
        return False
    if u.startswith("chrome-extension://") or u.startswith("moz-extension://"):
        return False
    return True


async def _probe_page_alive(pg: Any) -> bool:
    try:
        if pg.is_closed():
            return False
        await asyncio.wait_for(pg.evaluate("() => 1"), timeout=3.0)
        return True
    except Exception:
        return False


async def _acquire_cdp_target_page(
    browser: Any,
    *,
    host: str,
    target_url: str,
    navigate_if_no_tab: bool,
    log: Callable[[str], None],
) -> tuple[Any | None, str | None]:
    if not browser.contexts:
        return None, "CDP 已连上但无 context"

    def _safe_url(pg: Any) -> str:
        try:
            return (pg.url or "").strip()
        except Exception:
            return ""

    for ctx in browser.contexts:
        for pg in reversed(list(getattr(ctx, "pages", []) or [])):
            u = _safe_url(pg)
            if not _cdp_tab_url_driver_safe(u):
                continue
            if not await _probe_page_alive(pg):
                continue
            if host and host in u.lower():
                try:
                    await pg.bring_to_front()
                except Exception:
                    pass
                return pg, None

    if not navigate_if_no_tab:
        return (
            None,
            f"无含 {host!r} 的标签页。请打开站点或去掉 --require-existing-tab",
        )

    for ctx in browser.contexts:
        for pg in reversed(list(getattr(ctx, "pages", []) or [])):
            u = _safe_url(pg)
            if not _cdp_tab_url_driver_safe(u):
                continue
            if not await _probe_page_alive(pg):
                continue
            log(f"[nav] 尝试 goto {target_url!r}（当前 {u[:96]!r}）")
            try:
                await pg.goto(target_url, wait_until="domcontentloaded", timeout=60_000)
                await pg.wait_for_timeout(400)
                try:
                    await pg.bring_to_front()
                except Exception:
                    pass
                return pg, None
            except Exception as e:
                log(f"[nav] goto 失败：{e!s}")

    ctx_new = browser.contexts[0]
    for ctx in browser.contexts:
        for pg in list(getattr(ctx, "pages", []) or []):
            if await _probe_page_alive(pg):
                ctx_new = ctx
                break
        else:
            continue
        break
    try:
        pg = await ctx_new.new_page()
        await pg.goto(target_url, wait_until="domcontentloaded", timeout=60_000)
        await pg.wait_for_timeout(400)
        return pg, None
    except Exception as e:
        return None, f"新开标签失败：{e!s}"


def _http_post_json(
    url: str, body: dict[str, Any], *, timeout: float
) -> tuple[int, dict[str, Any] | str]:
    try:
        import urllib.error
        import urllib.request
    except ImportError:
        return 0, "urllib_unavailable"

    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            code = resp.getcode()
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        code = e.code
    except Exception as e:
        return 0, f"request_failed:{e}"
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return code, f"not_dict:{raw[:800]}"
        return code, parsed
    except json.JSONDecodeError:
        return code, raw


def _balanced_brace_json_spans(text: str) -> list[str]:
    """从文本中提取顶层成对的 `{ ... }` 片段（用于模型在 JSON 前后夹杂中文说明时的兜底解析）。"""
    out: list[str] = []
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                out.append(text[start : i + 1])
                start = -1
            elif depth < 0:
                depth = 0
                start = -1
    return out


def _extract_json_object(text: str) -> dict[str, Any] | None:
    if not (text or "").strip():
        return None
    t = text.strip()
    candidates: list[str] = []
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", t)
    if m:
        candidates.append(m.group(1).strip())
    lb, rb = t.find("{"), t.rfind("}")
    if lb != -1 and rb > lb:
        candidates.append(t[lb : rb + 1])
    spans = _balanced_brace_json_spans(t)
    # 较长片段优先（更可能是完整指令对象）
    spans_sorted = sorted(set(spans), key=len, reverse=True)
    for sp in spans_sorted:
        if sp not in candidates:
            candidates.append(sp)
    for frag in candidates:
        try:
            o = json.loads(frag)
            if isinstance(o, dict) and "action" in o:
                return o
        except json.JSONDecodeError:
            continue
    return None


_COIN_JS = """
() => {
  const out = { walletText: "", candidates: [] };
  const trySel = [
    '[class*="wallet" i]', '[class*="coin" i]', '[class*="balance" i]',
    '[class*="gold" i]', '[class*="currency" i]', 'header'
  ];
  for (const s of trySel) {
    try {
      const el = document.querySelector(s);
      if (el) {
        const tx = (el.innerText || "").replace(/\\s+/g, " ").trim();
        if (tx.length > 2) out.walletText += tx.slice(0, 240) + " | ";
      }
    } catch (e) {}
  }
  const blob = (out.walletText + " " + ((document.body && document.body.innerText) || "")).slice(0, 12000);
  const rx = /\\d[\\d,\\s]{2,}\\d|\\d{4,}/g;
  let m;
  const seen = new Set();
  while ((m = rx.exec(blob)) !== null) {
    const n = parseInt(String(m[0]).replace(/[\\s,]/g, ""), 10);
    if (!Number.isNaN(n) && n > 0 && n < 1e12) seen.add(n);
  }
  out.candidates = Array.from(seen).sort((a, b) => b - a).slice(0, 12);
  const bestGuess = out.candidates.length ? out.candidates[0] : null;
  return { bestGuess, candidates: out.candidates, walletText: out.walletText.slice(0, 600), url: location.href };
}
"""

_CONTEXT_JS = """
() => {
  const hintFor = (el) => {
    const cls = (typeof el.className === 'string' ? el.className : String(el.className || '')).slice(0, 88);
    const id = el.id || '';
    let selector_hint = '';
    if (id && /^[a-zA-Z][\\w-]*$/.test(id)) selector_hint = '#' + id;
    else if (cls) {
      const c0 = cls.split(/\\s+/).filter(Boolean)[0];
      if (c0) selector_hint = '.' + c0.replace(/[^a-zA-Z0-9_-]/g, '');
    }
    if (!selector_hint) selector_hint = el.tagName ? el.tagName.toLowerCase() : '*';
    return selector_hint;
  };
  const rowFor = (el, idx) => {
    const txt = (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 140);
    const cls = (typeof el.className === 'string' ? el.className : String(el.className || '')).slice(0, 88);
    const id = el.id || '';
    return {
      idx,
      tag: el.tagName.toLowerCase(),
      id,
      cls,
      inner_text: txt,
      selector_hint: hintFor(el),
    };
  };
  const visibleOk = (el) => {
    try {
      const r = el.getBoundingClientRect();
      const st = window.getComputedStyle(el);
      return r.width > 1 && r.height > 1 && st.visibility !== 'hidden' && st.display !== 'none';
    } catch (e) { return false; }
  };
  const selPrimary = [
    'button', 'a[href]', '[role="button"]', '[role="tab"]',
    'input[type="submit"]', 'input[type="button"]', '[onclick]'
  ].join(', ');
  const rows = [];
  const seen = new Set();
  const pushUnique = (el, needPointer) => {
    if (!visibleOk(el)) return false;
    try {
      const r = el.getBoundingClientRect();
      const st = window.getComputedStyle(el);
      if (needPointer && st.cursor !== 'pointer' && st.cursor !== 'grab') return false;
      const o = rowFor(el, rows.length);
      const txt = o.inner_text || '';
      if (needPointer && txt.length < 6) return false;
      const key = txt.slice(0, 56) + '|' + o.selector_hint + '|' + Math.round(r.left) + '|' + Math.round(r.top);
      if (seen.has(key)) return false;
      seen.add(key);
      o.idx = rows.length;
      rows.push(o);
      return true;
    } catch (e) { return false; }
  };
  Array.from(document.querySelectorAll(selPrimary)).filter(visibleOk).slice(0, 80).forEach(el => {
    pushUnique(el, false);
  });
  const pointerCand = document.querySelectorAll('div, li, article, section');
  for (let i = 0; i < pointerCand.length && rows.length < 96; i++) {
    pushUnique(pointerCand[i], true);
  }
  return {
    url: location.href,
    title: document.title || '',
    snippet: ((document.body && document.body.innerText) || '').replace(/\\s+/g, ' ').trim().slice(0, 6500),
    interactive: rows.slice(0, 96),
  };
}
"""


async def _eval_frame(fr: Any, js: str) -> Any:
    return await fr.evaluate(js)


async def collect_coin_snapshot(page: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "frames": [],
        "bestGuess": None,
        "candidates_union": [],
    }
    union: set[int] = set()
    try:
        frames = list(page.frames)
    except Exception:
        frames = []
    for fr in frames:
        try:
            part = await _eval_frame(fr, _COIN_JS)
        except Exception as e:
            part = {"error": str(e)[:200], "url": ""}
        merged["frames"].append(part)
        if isinstance(part, dict):
            bg = part.get("bestGuess")
            if isinstance(bg, int):
                union.add(bg)
            for c in part.get("candidates") or []:
                if isinstance(c, int):
                    union.add(c)
    if union:
        sorted_c = sorted(union, reverse=True)
        merged["candidates_union"] = sorted_c[:16]
        merged["bestGuess"] = sorted_c[0]
    return merged


async def get_page_context(page: Any) -> str:
    lines: list[str] = []
    try:
        frames = list(page.frames)
    except Exception:
        frames = []
    for i, fr in enumerate(frames):
        try:
            u = fr.url or ""
        except Exception:
            u = ""
        try:
            data = await _eval_frame(fr, _CONTEXT_JS)
        except Exception as e:
            lines.append(f"--- frame[{i}] {u[:200]} ERROR: {e!s}")
            continue
        if not isinstance(data, dict):
            continue
        lines.append(f"=== frame[{i}] url={u[:300]} title={data.get('title','')!s}")
        sn = data.get("snippet") or ""
        lines.append(f"__body_snippet__: {sn[:8000]}")
        inter = data.get("interactive") or []
        lines.append("__clickable__(idx tag id class inner_text selector_hint):")
        for it in inter[:72]:
            if not isinstance(it, dict):
                continue
            lines.append(
                "  "
                + json.dumps(it, ensure_ascii=False)[:420]
            )
    return "\n".join(lines)


async def _try_js_click_selector(page: Any, selector: str) -> tuple[bool, str]:
    sel = (selector or "").strip()
    if not sel:
        return False, "empty_selector"
    for fr in list(page.frames):
        try:
            ok = await fr.evaluate(
                """(sel) => {
                  try {
                    const el = document.querySelector(sel);
                    if (!el) return false;
                    el.scrollIntoView({block:'center', inline:'nearest'});
                    el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true,cancelable:true,view:window}));
                    el.dispatchEvent(new MouseEvent('mouseup', {bubbles:true,cancelable:true,view:window}));
                    el.dispatchEvent(new MouseEvent('click', {bubbles:true,cancelable:true,view:window}));
                    if (typeof el.click === 'function') el.click();
                    return true;
                  } catch (e) { return false; }
                }""",
                sel,
            )
            if ok:
                return True, f"frame_ok:{fr.url[:120]!s}"
        except Exception as e:
            continue
    return False, "no_hit_any_frame"


async def _try_playwright_click(page: Any, selector: str) -> tuple[bool, str]:
    sel = (selector or "").strip()
    if not sel:
        return False, "empty_selector"
    for fr in list(page.frames):
        try:
            loc = fr.locator(sel).first
            if await loc.count() < 1:
                continue
            await loc.click(timeout=7000, force=True)
            return True, f"pw_ok:{fr.url[:120]!s}"
        except Exception:
            continue
    return False, "pw_miss"


async def _main_frame_signature(page: Any) -> tuple[str, str]:
    """返回 (url, body_innerText 长度前缀 + 片段)，用于检测点击后 DOM 是否变化。"""
    try:
        u = (page.url or "").strip()
        sig = await page.evaluate(
            """() => {
              try {
                const t = (document.body && document.body.innerText) || '';
                return String(t.length) + ':' + t.slice(0, 4096);
              } catch (e) { return ''; }
            }"""
        )
        return u, str(sig)
    except Exception:
        return "", ""


async def _poll_page_changed_after_action(
    page: Any,
    before: tuple[str, str],
    *,
    timeout_sec: float = 3.0,
    poll_sec: float = 0.2,
) -> bool:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_sec
    while loop.time() < deadline:
        await asyncio.sleep(poll_sec)
        after = await _main_frame_signature(page)
        if after[0] != before[0] or after[1] != before[1]:
            return True
    return False


async def execute_agent_action(page: Any, act: dict[str, Any]) -> dict[str, Any]:
    action = str(act.get("action") or "").strip().lower()
    selector = str(act.get("selector") or "").strip()
    if action == "wait":
        try:
            sec = float(act.get("seconds", 5))
        except (TypeError, ValueError):
            sec = 5.0
        sec = max(0.1, min(120.0, sec))
        await asyncio.sleep(sec)
        return {"ok": True, "detail": f"wait_{sec}s"}
    if action in ("click",):
        ok, detail = await _try_playwright_click(page, selector)
        if not ok:
            ok, detail = await _try_js_click_selector(page, selector)
        return {"ok": ok, "detail": detail}
    if action in ("js_click",):
        ok, detail = await _try_js_click_selector(page, selector)
        if not ok:
            ok, detail = await _try_playwright_click(page, selector)
        return {"ok": ok, "detail": detail}
    if action in ("done", "fail"):
        return {"ok": True, "detail": action}
    return {"ok": False, "detail": f"unknown_action:{action}"}


def _load_skill_text() -> str:
    if SKILL_PATH.is_file():
        return SKILL_PATH.read_text(encoding="utf-8")
    return "[missing docs/tests/game_test_skill.md]"


def _build_user_prompt(
    *,
    skill_md: str,
    game: str,
    iteration: int,
    coin_hint: str,
    page_context: str,
    last_action: str | None = None,
    last_execution_result: str | None = None,
    failure_streak: int = 0,
) -> str:
    la = (last_action or "").strip() or "(none)"
    ler = (last_execution_result or "").strip() or "(none)"
    streak_hint = ""
    if failure_streak >= 3:
        streak_hint = (
            "\nfailure_hint: 已连续多轮未达成有效进展；若同一策略反复失败，"
            "必须按 SKILL「故障排查指南」更换 action（如 click↔js_click、先 wait 再点、换 __clickable__ 条目）。"
        )
    reflection = ""
    if iteration > 1:
        reflection = f"""

<<< REFLECTION >>>
这是第 {iteration} 次外层尝试；请根据 last_execution_result 中的失败原因调整策略，确保完成进入「{game}」并完成冒烟测试目标。
"""
    return f"""<<< SKILL_DOCUMENT >>>
{skill_md}
<<< RUNTIME >>>
target_game: {game}
iteration: {iteration}
coin_hint: {coin_hint}
last_action: {la}
last_execution_result: {ler}
failure_streak: {failure_streak}{streak_hint}
{reflection}
<<< PAGE_CONTEXT >>>
{page_context}

<<< TASK >>>
基于 SKILL_DOCUMENT 与 PAGE_CONTEXT，产出 **下一个** 原子操作。

【输出格式 — 违反则 Executor 解析失败】
你必须 **只输出一行 JSON 对象**，从第一个字符 {{ 到最后一个字符 }}，中间不得有任何中文说明、前缀或后缀。
严禁输出「我已经分析了…」「根据技能文档…」等句子。**reason 字段内可以写中文**，但 JSON 外不允许任何字符。
PAGE_CONTEXT 里每条可交互项的 **inner_text** 是摘要字段，**不是** HTML 属性；禁止编造 `txt=`、`div[txt='…']` 之类选择器。
若无把握命中真实节点：优先 `wait`（seconds 3～8）或 `fail`，不要猜测 CSS。

若本轮 ``user_input`` 末尾出现 ``<<< FEEDBACK_FROM_EXECUTOR >>>``，必须先阅读其中的 ERROR，再给出纠正后的 JSON。

硬性要求：
1) **禁止**调用任何工具/MCP；只输出 JSON。
2) JSON 必须包含键：action, selector, seconds, reason, terminal_ok, coin_sync_ok（见 SKILL）。
3) selector 使用标准 CSS（见 docs/tests/game_test_skill.md「CSS 选择器约束」）；仅用 __clickable__ 中出现的 class/id/`selector_hint` 组合出的合法选择器，或文档示例形态。
"""


async def call_l3_agent(
    base: str,
    user_input: str,
    *,
    max_iterations: int,
    session_note: str,
    skill_debug_log_path: Path | None = None,
    skill_debug_game: str = "",
    skill_debug_iteration: int = 0,
) -> tuple[dict[str, Any] | None, dict[str, Any] | str, str]:
    """返回 (解析出的指令 JSON 或 None, 完整 HTTP JSON/错误串, answer 原文)。"""
    url = f"{base.rstrip('/')}/api/v3/agent/run"
    body: dict[str, Any] = {
        "user_input": user_input,
        "max_iterations": max_iterations,
        "implicit_attribution": {
            "channel": "http_k11_l3_agent_games_smoke",
            "session_note": session_note,
        },
        "chat_id": session_note,
    }
    code, payload = _http_post_json(url, body, timeout=600.0)

    def _flush_debug(
        *,
        payload_for_log: Any,
        parsed_out: dict[str, Any] | None,
        raw_ans: str,
    ) -> None:
        if not skill_debug_log_path:
            return
        try:
            _append_skill_debug_round(
                skill_debug_log_path,
                game=skill_debug_game or "?",
                iteration=skill_debug_iteration,
                session_note=session_note,
                post_url=url,
                request_body=body,
                user_input_full=user_input,
                http_status=code,
                payload=payload_for_log,
                parsed_action=parsed_out,
                raw_answer=raw_ans,
            )
        except Exception as ex:
            try:
                skill_debug_log_path.parent.mkdir(parents=True, exist_ok=True)
                with skill_debug_log_path.open("a", encoding="utf-8") as fp:
                    fp.write(f"\n[skill_debug_log ERROR] {ex!s}\n")
            except Exception:
                pass

    if isinstance(payload, str):
        _flush_debug(payload_for_log={"parse_error": payload}, parsed_out=None, raw_ans="")
        return None, payload, ""

    raw_answer = str(payload.get("answer") or "")
    if code >= 400 or payload.get("error"):
        _flush_debug(payload_for_log=payload, parsed_out=None, raw_ans=raw_answer)
        return None, payload, raw_answer

    parsed = _extract_json_object(raw_answer)
    _flush_debug(payload_for_log=payload, parsed_out=parsed, raw_ans=raw_answer)
    return parsed, payload, raw_answer


def _summarize_act(act: dict[str, Any]) -> str:
    try:
        return json.dumps(act, ensure_ascii=False, default=str)[:900]
    except Exception:
        return repr(act)[:900]


async def run_one_game(
    page: Any,
    *,
    game: str,
    l3_base: str,
    max_rounds: int,
    l3_iterations: int,
    parse_attempts_per_round: int,
    log: Callable[[str], None],
    skill_debug_log_path: Path | None = None,
) -> dict[str, Any]:
    session = f"k11-game-{game}-{uuid.uuid4().hex[:10]}"
    coin_before = await collect_coin_snapshot(page)
    bg0 = coin_before.get("bestGuess")

    trace: list[dict[str, Any]] = []
    last_answer = ""
    last_action_line = "(none)"
    last_exec_line = "(none)"
    failure_streak = 0

    max_parse_tries = max(1, int(parse_attempts_per_round))

    for it in range(1, max_rounds + 1):
        ctx = await get_page_context(page)
        coin_hint = f"before_bestGuess={bg0}; snapshot_frames={len(coin_before.get('frames') or [])}"
        skill = _load_skill_text()

        parse_feedback_suffix = ""
        act: dict[str, Any] | None = None
        raw_payload: Any = {}
        raw_ans = ""

        for ptry in range(max_parse_tries):
            prompt = _build_user_prompt(
                skill_md=skill,
                game=game,
                iteration=it,
                coin_hint=coin_hint,
                page_context=ctx,
                last_action=last_action_line,
                last_execution_result=last_exec_line,
                failure_streak=failure_streak,
            )
            full_prompt = prompt + parse_feedback_suffix

            log(f"[{game}] round {it}/{max_rounds} POST L3 (parse {ptry + 1}/{max_parse_tries}) …")
            act, raw_payload, raw_ans = await call_l3_agent(
                l3_base,
                full_prompt,
                max_iterations=l3_iterations,
                session_note=session,
                skill_debug_log_path=skill_debug_log_path,
                skill_debug_game=game,
                skill_debug_iteration=it * 1000 + ptry,
            )
            last_answer = raw_ans[:8000] if isinstance(raw_ans, str) else ""

            if isinstance(act, dict) and "action" in act:
                break

            trace.append(
                {
                    "iter": it,
                    "parse_try": ptry + 1,
                    "parse_miss": True,
                    "raw_answer_tail": last_answer[:2400],
                }
            )
            log(f"[{game}] answer 无法解析为 JSON，追加 FEEDBACK 后重试 …")
            parse_feedback_suffix = (
                "\n\n<<< FEEDBACK_FROM_EXECUTOR >>>\n"
                + _FEEDBACK_PARSE_INVALID
            )
            last_exec_line = (
                "PARSE_ERROR: answer 无法解析为含 action 的 JSON；请只输出一行合法 JSON。"
            )
            last_action_line = "(none)"

        if act is None or not isinstance(act, dict) or "action" not in act:
            failure_streak += 1
            last_exec_line = (
                "PARSE_ERROR: 本子回合内 "
                f"{max_parse_tries} 次 POST 仍无法解析 JSON。"
            )
            log(f"[{game}] 本子回合解析用尽，进入下一轮外层迭代（failure_streak={failure_streak}）…")
            continue

        trace.append({"iter": it, "action": act, "parse_path_ok": True})
        action = str(act.get("action") or "").lower()

        if action == "done":
            coin_after = await collect_coin_snapshot(page)
            bg1 = coin_after.get("bestGuess")
            delta = None
            if isinstance(bg0, int) and isinstance(bg1, int):
                delta = bg1 - bg0
            coin_ok = None
            if delta is not None:
                coin_ok = abs(delta) < 10**9
            return {
                "game": game,
                "status": "done",
                "terminal_ok": bool(act.get("terminal_ok")),
                "coin_before": bg0,
                "coin_after": bg1,
                "coin_delta": delta,
                "coin_sync_ok": coin_ok,
                "trace": trace,
                "last_answer_tail": last_answer,
            }
        if action == "fail":
            return {
                "game": game,
                "status": "fail",
                "terminal_ok": False,
                "coin_before": bg0,
                "trace": trace,
                "reason": act.get("reason"),
                "last_answer_tail": last_answer,
            }

        snap_before: tuple[str, str] | None = None
        if action in ("click", "js_click"):
            snap_before = await _main_frame_signature(page)

        exec_res = await execute_agent_action(page, act)
        trace[-1]["exec"] = exec_res
        log(f"[{game}] exec {action!r} ok={exec_res.get('ok')} {exec_res.get('detail')}")

        stale = False
        if action in ("click", "js_click") and exec_res.get("ok") and snap_before is not None:
            changed = await _poll_page_changed_after_action(page, snap_before, timeout_sec=3.0)
            if not changed:
                stale = True
                sel = str(act.get("selector") or "")
                exec_res = {
                    **exec_res,
                    "stale_click": True,
                    "detail": str(exec_res.get("detail") or "") + ";no_dom_or_url_change_3s",
                }
                trace[-1]["exec"] = exec_res
                failure_streak += 1
                last_exec_line = (
                    f"ERROR: 刚才尝试点击 {sel!r} 但页面没有任何反应，目标可能被遮挡或不可见。"
                    "请重新观察 __clickable__ 并尝试其他路径。"
                )
                last_action_line = _summarize_act(act)
                log(f"[{game}] 点击后 3s 内 URL/DOM 未变化，视为无效点击，进入下一轮 …")
                continue

        if not exec_res.get("ok"):
            failure_streak += 1
            last_exec_line = (
                f"EXEC_ERROR: {exec_res.get('detail')!s} — 请换 selector 或改用 wait/js_click。"
            )
            last_action_line = _summarize_act(act)
            continue

        failure_streak = 0
        last_exec_line = f"OK: {exec_res.get('detail')!s}"
        last_action_line = _summarize_act(act)

    coin_after = await collect_coin_snapshot(page)
    bg1 = coin_after.get("bestGuess")
    delta = None
    if isinstance(bg0, int) and isinstance(bg1, int):
        delta = bg1 - bg0
    return {
        "game": game,
        "status": "max_rounds",
        "terminal_ok": False,
        "coin_before": bg0,
        "coin_after": bg1,
        "coin_delta": delta,
        "trace": trace,
        "last_answer_tail": last_answer,
        "failure_streak_end": failure_streak,
    }


async def _async_main(args: argparse.Namespace) -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("请先：pip install playwright && playwright install chromium", file=sys.stderr)
        return 2

    if not SKILL_PATH.is_file():
        print(f"[WARN] 未找到技能文件 {SKILL_PATH}，将使用占位提示。", file=sys.stderr)

    target_url = (args.target_url or DEFAULT_TARGET).strip()
    host = _host_from_url(target_url)
    cdp = _kalaroko_cdp(args.cdp_http or None)
    games = args.games if args.games else list(DEFAULT_GAMES)

    def log(msg: str) -> None:
        if not args.quiet:
            print(msg, flush=True)

    log("———————— K11 L3 Agent 游戏冒烟 ————————————————")
    log(f"CDP={cdp} target={target_url} games={games}")

    skill_debug_log_path: Path | None = None
    if not args.no_skill_debug_log:
        dbg_explicit = (getattr(args, "skill_debug_log_dir", None) or "").strip()
        dbg_dir = (
            Path(dbg_explicit).expanduser().resolve()
            if dbg_explicit
            else _default_skill_debug_log_dir()
        )
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
        skill_debug_log_path = dbg_dir / f"k11_l3_agent_games_smoke_{stamp}.log"
        _write_skill_debug_header(
            skill_debug_log_path,
            meta={
                "l3_base": args.l3_base,
                "target_url": target_url,
                "cdp": cdp,
                "games": ",".join(games),
                "skill_md": str(SKILL_PATH.resolve()),
                "log_dir": str(dbg_dir.resolve()),
            },
        )
        log(f"[debug] L3 往返日志（完整提示词/响应）: {skill_debug_log_path.resolve()}")

    results: list[dict[str, Any]] = []

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(cdp)
        navigate_if_no_tab = not args.require_existing_tab
        page, pick_err = await _acquire_cdp_target_page(
            browser,
            host=host,
            target_url=_home_feed_url(target_url),
            navigate_if_no_tab=navigate_if_no_tab,
            log=log,
        )
        if page is None:
            print(f"[FAIL] {pick_err}", file=sys.stderr)
            return 2

        try:
            await page.goto(_home_feed_url(target_url), wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(400)
        except Exception as e:
            log(f"[WARN] goto 首页：{e!s}")

        for game in games:
            log(f"\n======== 游戏：{game} ========")
            r = await run_one_game(
                page,
                game=game,
                l3_base=args.l3_base,
                max_rounds=args.max_rounds,
                l3_iterations=args.l3_iterations,
                parse_attempts_per_round=args.parse_attempts_per_round,
                log=log,
                skill_debug_log_path=skill_debug_log_path,
            )
            results.append(r)
            st = r.get("status")
            log(json.dumps(r, ensure_ascii=False, indent=2)[:12000])

            if st != "done" and not args.continue_on_fail:
                log("[停止] 上一局未正常 done，且未加 --continue-on-fail")
                break
            try:
                await page.goto(_home_feed_url(target_url), wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(500)
            except Exception as e:
                log(f"[WARN] 回大厅 {e!s}")

    summary = {
        "ok": bool(results)
        and all(
            x.get("status") == "done" and x.get("terminal_ok") is True for x in results
        ),
        "games": results,
    }
    out_path = args.json_out
    if out_path:
        outp = Path(out_path)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"\n已写入 {outp.resolve()}")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="K11 L3 Agent 游戏冒烟（DOM + CDP）")
    ap.add_argument(
        "--l3-base",
        default=os.environ.get("JACHIN_L3_HTTP_BASE", "http://127.0.0.1:18991"),
        help="L3 HTTP 根",
    )
    ap.add_argument("--cdp-http", default=None, help="覆盖 KALAROKO_CDP_ENDPOINT")
    ap.add_argument("--target-url", default=DEFAULT_TARGET, help="站点根 URL")
    ap.add_argument(
        "--games",
        nargs="*",
        default=None,
        help="游戏名称列表（默认 Tongits King / Bato-Bato Pick）",
    )
    ap.add_argument("--max-rounds", type=int, default=72, help="单游戏最大外层感知-反馈回合数")
    ap.add_argument(
        "--parse-attempts-per-round",
        type=int,
        default=PARSE_ATTEMPTS_PER_ROUND,
        metavar="N",
        help=f"同一外层回合内 JSON 解析失败时追加 FEEDBACK 并重 POST 的次数（不含首次，默认 {PARSE_ATTEMPTS_PER_ROUND}）",
    )
    ap.add_argument(
        "--l3-iterations",
        type=int,
        default=4,
        help="每次 POST agent/run 的 max_iterations（模型内部 ReAct 上限）",
    )
    ap.add_argument("--require-existing-tab", action="store_true", help="禁止自动新开页签/goto")
    ap.add_argument(
        "--continue-on-fail",
        action="store_true",
        help="一局失败后仍尝试下一游戏",
    )
    ap.add_argument("--json-out", default=None, help="写入汇总 JSON 路径")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument(
        "--skill-debug-log-dir",
        default=os.environ.get("K11_SKILL_DEBUG_LOG_DIR", "").strip() or None,
        help="Skill 往返调试日志目录（默认 ~/.jachin/jachin_debug/健康skill）",
    )
    ap.add_argument(
        "--no-skill-debug-log",
        action="store_true",
        help="禁用写入上述调试日志",
    )
    args = ap.parse_args()
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
