#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K11 七款游戏冒烟（Agent-Executor 模式）

对齐《K11_平台冒烟测试用例》P0：各游戏可开局/完成一轮内容感知 + 大厅金币有结算变化感知。

- Executor（本脚本 + Playwright）：CDP 附加已有 Chrome、读金币、截图、跨 frame 点击、JS 穿透点击。
- Agent（L3 HTTP）：POST {JACHIN_L3_HTTP_BASE}/api/v3/agent/run，根据页面文本摘要（及可选截图）
  输出 **单行 JSON 指令**，由 Executor 执行；不识别的交互交给 Agent 决策。

默认站点：https://www.kalaroko.com/（须先用 scripts/launch_chrome_debug.ps1 打开调试 Chrome，
且 KALAROKO_CDP_ENDPOINT 指向同一 CDP；L3 须已启动且 engine 就绪）。

用法（仓库根）：
  python -m l3_node --ws-only   # 另开终端：保证 L3 HTTP 默认可用
  python scripts/test_k11_agent_executor_games_smoke_playwright.py
  python scripts/test_k11_agent_executor_games_smoke_playwright.py --games texas_holdem,mines_clash
  python scripts/test_k11_agent_executor_games_smoke_playwright.py --l3-base http://127.0.0.1:18991 --l3-timeout-sec 120

可选环境：
  K11_AGENT_EXEC_SKIP_DEEP_WAIT=1 — 跳过 MCP 晚期就绪竞速（调试用）
  K11_AGENT_EXEC_DIAG_DIR — 卡顿截图目录（默认 logs/k11_agent_executor）
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

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

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

DEFAULT_SITE = "https://www.kalaroko.com/"
DEFAULT_L3 = os.environ.get("JACHIN_L3_HTTP_BASE", "http://127.0.0.1:18991")

_DIAG_DIR = Path(os.environ.get("K11_AGENT_EXEC_DIAG_DIR") or (ROOT / "logs" / "k11_agent_executor"))


# —— 与状态机同源：多 frame 钱包嗅探 —— #
_LOBBY_WALLET_SNIFF_JS = r"""() => {
  const out = [];
  const push = (s) => {
    const t = (s || '').replace(/\s+/g, ' ').trim();
    if (t && /\d/.test(t)) out.push(t.slice(0, 140));
  };
  for (const sel of [
    '[class*="balance" i]', '[class*="coin" i]', '[class*="wallet" i]', '[class*="gold" i]',
    'header', 'nav', '[data-balance]', '[class*="user" i]', '[class*="profile" i]'
  ]) {
    try { document.querySelectorAll(sel).forEach((el) => push(el.innerText)); } catch (e) {}
  }
  try {
    if (document.body) {
      const t = (document.body.innerText || '').slice(0, 8000);
      const m = t.match(/(?:₱|PHP|JCoins?|Coins?)\s*[:\s]*[\d,]+(?:\.[\d]+)?/ig);
      if (m) m.slice(0,8).forEach((x) => push(x));
    }
  } catch (e) {}
  return { hints: [...new Set(out)].slice(0, 28), href: (location && location.href) || '' };
}"""

_BALANCE_HINT_PREF = re.compile(
    r"balance|wallet|coin|gold|chip|credit|₱|php|jc|jcoin|currency|余额|金币|钱包|财产",
    re.I,
)


def _log(fn: Callable[[str], None] | None, msg: str) -> None:
    if fn:
        fn(msg)
    else:
        print(msg, flush=True)


def _http_post_json(
    url: str, body: dict[str, Any], *, timeout: float
) -> tuple[int, dict[str, Any] | str]:
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
            return code, f"not_dict:{raw[:500]}"
        return code, parsed
    except json.JSONDecodeError:
        return code, raw


def _extract_json_object(text: str) -> dict[str, Any] | None:
    s = (text or "").strip()
    if not s:
        return None
    fence = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", s, re.I)
    if fence:
        try:
            o = json.loads(fence.group(1))
            return o if isinstance(o, dict) else None
        except json.JSONDecodeError:
            pass
    start = s.rfind("{")
    while start >= 0:
        depth = 0
        for i in range(start, len(s)):
            if s[i] == "{":
                depth += 1
            elif s[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        o = json.loads(s[start : i + 1])
                        if isinstance(o, dict) and o.get("action"):
                            return o
                    except json.JSONDecodeError:
                        break
        start = s.rfind("{", 0, start)
    return None


def _kalaroko_cdp_endpoint(cli: str | None) -> str | None:
    raw = (cli or "").strip() if cli else ""
    if not raw:
        raw = (os.environ.get("KALAROKO_CDP_ENDPOINT") or "").strip()
    if not raw:
        return None
    if not raw.startswith("http://") and not raw.startswith("https://"):
        raw = "http://" + raw.lstrip("/")
    return raw


def _first_number_from_hints(h: dict[str, Any]) -> tuple[float | None, str]:
    hints = h.get("hints")
    if not isinstance(hints, list) or not hints:
        return None, "无 hints 列表"
    num_re = re.compile(r"[\d,]+(?:\.\d+)?")
    scored: list[tuple[int, float, str]] = []
    for hint in hints:
        s = str(hint)
        flat = s.replace(",", "")
        for m in num_re.finditer(flat):
            raw = m.group(0).replace(",", "")
            try:
                val = float(raw)
            except ValueError:
                continue
            if val < 0 or val > 1e14:
                continue
            score = 0
            if _BALANCE_HINT_PREF.search(s):
                score += 200
            if val >= 1000:
                score += 80
            elif val >= 100:
                score += 40
            else:
                score += 1
            try:
                score += int(min(25, math.log10(max(val, 1.0)) * 5))
            except ValueError:
                pass
            scored.append((score, val, s[:72]))
    if not scored:
        return None, "文案中无数字"
    scored.sort(key=lambda x: (-x[0], -x[1]))
    best = scored[0]
    return best[1], f"score={best[0]} → {best[1]}"


async def _snapshot_lobby_wallet(page: Any) -> dict[str, Any]:
    merged: list[str] = []
    hrefs: list[str] = []
    errors: list[str] = []
    try:
        frames = [page.main_frame] + [f for f in page.frames if f != page.main_frame]
    except Exception:
        frames = []
    for fr in frames:
        try:
            raw = await fr.evaluate(_LOBBY_WALLET_SNIFF_JS)
            if isinstance(raw, dict):
                hs = raw.get("hints")
                if isinstance(hs, list):
                    merged.extend(str(x) for x in hs if x)
                h = raw.get("href")
                if isinstance(h, str) and h.strip():
                    hrefs.append(h.strip())
        except Exception as e:
            errors.append(str(e)[:100])
    out: dict[str, Any] = {
        "hints": list(dict.fromkeys(merged))[:36],
        "href": hrefs[0] if hrefs else "",
        "frames_sampled": len(frames),
    }
    if errors:
        out["_frame_errors"] = errors[:4]
    return out


async def _gather_dom_brief(page: Any, *, limit: int = 14000) -> str:
    chunks: list[str] = []
    try:
        u = page.url or ""
        chunks.append(f"MAIN_URL: {u}")
    except Exception:
        pass
    try:
        main = await page.evaluate(
            "() => (document.body && document.body.innerText) ? document.body.innerText : ''"
        )
        if isinstance(main, str) and main.strip():
            chunks.append("--- MAIN innerText ---\n" + main[:9000])
    except Exception as e:
        chunks.append(f"(main innerText err: {e})")
    fi = 0
    for fr in page.frames:
        if fr == page.main_frame:
            continue
        fi += 1
        try:
            u = fr.url or ""
        except Exception:
            u = ""
        try:
            t = await fr.evaluate(
                "() => (document.body && document.body.innerText) ? document.body.innerText : ''"
            )
            body = t if isinstance(t, str) else ""
        except Exception as e:
            body = f"(err: {e})"
        chunks.append(f"--- FRAME[{fi}] url={u[:200]} ---\n{(body or '')[:6000]}")
    return "\n".join(chunks)[:limit]


def _scenario_by_name(
    scenario_key: str, *, home: str
) -> dict[str, Any]:
    from l3_client.local_mcps.kalaroko_monitor.mcp_kalaroko_monitor import (
        kalaroko_scenario_dict_by_name,
    )

    d = kalaroko_scenario_dict_by_name(scenario_key)
    d["start_url"] = home
    return d


GAME_CONFIGS: dict[str, dict[str, Any]] = {
    "tongits_king": {
        "title": "Tongits King",
        "playbook_zh": "进入后多为自动对局；出现结算/确定/关闭时点击并回大厅；无把握时交给 Agent。",
        "prefer_agent_after_shell": True,
    },
    "texas_holdem": {
        "title": "Texas Holdem",
        "playbook_zh": "进入牌桌发牌后，尽快点击 Fold 认输以结束一局，再退回大厅。",
        "prefer_agent_after_shell": True,
    },
    "texas_holdem_plus": {
        "title": "Texas Holdem Plus",
        "playbook_zh": "同 Texas Holdem：发牌后点 Fold，再回大厅。",
        "prefer_agent_after_shell": True,
    },
    "mines_clash": {
        "title": "Mines Clash",
        "playbook_zh": "多为自动或可点击格子；观察结算与退出路径，必要时点确认。",
        "prefer_agent_after_shell": True,
    },
    "crazy_solitaire": {
        "title": "Crazy Solitaire",
        "playbook_zh": "开局需下注；可出现 Start / Deal / 自动结算；按提示确认退出。",
        "prefer_agent_after_shell": True,
    },
    "unleash_running": {
        "title": "Unleash Running",
        "playbook_zh": "下注后点击 Start Game；跑酷局结束常自动退出，否则确认弹窗。",
        "prefer_agent_after_shell": True,
    },
    "pinoy_monopoly": {
        "title": "Pinoy Monopoly",
        "playbook_zh": "点击 Start Game；局末自动退出或点确认回到平台。",
        "prefer_agent_after_shell": True,
    },
}

DEFAULT_GAME_ORDER: tuple[str, ...] = (
    "tongits_king",
    "texas_holdem",
    "texas_holdem_plus",
    "mines_clash",
    "crazy_solitaire",
    "unleash_running",
    "pinoy_monopoly",
)


@dataclass
class GameRunResult:
    scenario_key: str
    ok_entry: bool
    ok_loop: bool
    initial_gold: float | None
    final_gold: float | None
    is_settled: bool | None
    notes: list[str] = field(default_factory=list)


class L3DecisionClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_sec: float,
        max_iterations: int,
        include_screenshot: bool,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_sec
        self._max_it = max(1, min(int(max_iterations), 24))
        self._shot = include_screenshot

    def decide(
        self,
        *,
        phase: str,
        scenario_key: str,
        playbook: str,
        dom_brief: str,
        screenshot_png: bytes | None,
        extra: str = "",
    ) -> dict[str, Any]:
        att: list[dict[str, Any]] = []
        if self._shot and screenshot_png:
            b64 = base64.standard_b64encode(screenshot_png).decode("ascii")
            att.append(
                {
                    "name": "viewport.png",
                    "data_url": f"data:image/png;base64,{b64}",
                }
            )

        sys_prompt = (
            "你是 Kalaroko H5 游戏自动化决策模块。Playwright 执行器会严格执行你返回的 JSON。\n"
            "规则：\n"
            "1) **禁止**假设你看不到的信息；只依据提供的页面文本摘要与截图。\n"
            "2) **必须**只输出 **一个** JSON 对象（可包在 markdown 代码块里），"
            "字段：action, target_text(可选), wait_ms(可选), reason(可选)。\n"
            "3) action 取值：click_text | js_click_text | wait | finished | refresh_lobby | noop 。\n"
            "- click_text：在任意 frame 中点击包含 target_text 的可交互元素（Fold / Start Game / Join / OK / Confirm / Close 等）。\n"
            "- js_click_text：同上但优先用脚本合成点击（遮罩时可选用）。\n"
            "- wait：短暂等待 wait_ms（默认 800）再观察；不要长 sleep。\n"
            "- finished：本局可结束，执行器将回大厅。\n"
            "- refresh_lobby：卡住或异常，要求执行器导航回站点首页大厅。\n"
            "- noop：暂不操作，下一轮再观察。\n"
            "4) 不要调用任何外部工具；不要输出 Thought/Action 等 ReAct 格式。"
        )
        user_block = (
            f"【阶段】{phase}\n"
            f"【游戏】{scenario_key}\n"
            f"【玩法说明】{playbook}\n"
            f"{extra}\n"
            "【页面文本摘要】\n"
            f"{dom_brief}\n"
        )
        user_input = sys_prompt + "\n---\n" + user_block
        body: dict[str, Any] = {
            "user_input": user_input,
            "max_iterations": self._max_it,
            "implicit_attribution": {"channel": "http_k11_agent_executor"},
        }
        if att:
            body["attachments_metadata"] = att

        code, resp = _http_post_json(
            f"{self._base}/api/v3/agent/run", body, timeout=self._timeout
        )
        if isinstance(resp, str):
            return {"action": "noop", "reason": f"l3_http_err:{code}:{resp[:200]}"}
        if code == 503 or (
            isinstance(resp.get("error"), str) and "尚未就绪" in str(resp.get("error"))
        ):
            return {"action": "noop", "reason": "l3_engine_not_ready"}
        if code >= 400 or resp.get("error"):
            return {
                "action": "noop",
                "reason": f"l3_err:{code}:{resp.get('error', resp)!s}"[:300],
            }
        ans = (resp.get("answer") or "").strip()
        parsed = _extract_json_object(ans)
        if isinstance(parsed, dict) and parsed.get("action"):
            return parsed
        return {"action": "noop", "reason": f"unparsed answer tail={ans[-400:]!r}"}


class GameExecutor:
    def __init__(
        self,
        page: Any,
        *,
        home: str,
        log: Callable[[str], None] | None,
        stall_sec: float,
        l3: L3DecisionClient,
    ) -> None:
        self.page = page
        self.home = home.rstrip("/") + "/"
        self._log = log
        self._stall = float(stall_sec)
        self._l3 = l3
        self._last_progress = time.monotonic()

    def _touch_progress(self) -> None:
        self._last_progress = time.monotonic()

    def stall_exceeded(self) -> bool:
        return (time.monotonic() - self._last_progress) >= self._stall

    async def screenshot_png(self) -> bytes:
        return await self.page.screenshot(type="png", full_page=False)

    async def save_diag_shot(self, tag: str) -> Path:
        _DIAG_DIR.mkdir(parents=True, exist_ok=True)
        p = _DIAG_DIR / f"{int(time.time())}_{tag}.png"
        await self.page.screenshot(path=str(p), type="png", full_page=False)
        _log(self._log, f"[diag] screenshot → {p}")
        return p

    async def analyze_blockage(
        self, *, scenario_key: str, playbook: str, dom_brief: str
    ) -> dict[str, Any]:
        png = await self.screenshot_png()
        return await asyncio.to_thread(
            self._l3.decide,
            phase="stall_recovery",
            scenario_key=scenario_key,
            playbook=playbook,
            dom_brief=dom_brief,
            screenshot_png=png,
            extra="【异常】执行器已超过停滞阈值；请给出 refresh_lobby / click_text / finished 之一。",
        )

    async def smart_click(self, target_text: str, *, prefer_js: bool) -> bool:
        """
        遍历主页面与所有 frame，按文案定位并点击；失败则 JS 派发 click。
        """
        raw = (target_text or "").strip()
        if not raw:
            return False
        esc = re.escape(raw)
        pat = re.compile(esc, re.I)

        async def _try_on_frame(fr: Any) -> bool:
            try:
                loc = fr.get_by_text(pat)
                n = await loc.count()
            except Exception:
                return False
            if n < 1:
                return False
            last = loc.last if n > 1 else loc.first
            try:
                await last.scroll_into_view_if_needed(timeout=4_000)
            except Exception:
                pass
            if not prefer_js:
                try:
                    await last.click(timeout=6_000, force=True)
                    self._touch_progress()
                    return True
                except Exception:
                    pass
            try:
                h = await last.element_handle(timeout=4_000)
                if h:
                    await fr.evaluate(
                        """(el) => {
                          if (!el) return;
                          el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));
                          el.dispatchEvent(new MouseEvent('mouseup', {bubbles:true}));
                          el.dispatchEvent(new MouseEvent('click', {bubbles:true}));
                        }""",
                        h,
                    )
                    self._touch_progress()
                    return True
            except Exception:
                pass
            return False

        frames: list[Any]
        try:
            frames = [self.page.main_frame] + [
                f for f in self.page.frames if f != self.page.main_frame
            ]
        except Exception:
            frames = [self.page.main_frame]

        for fr in frames:
            if await _try_on_frame(fr):
                _log(self._log, f"[exec] smart_click 命中: {raw!r}")
                return True
        _log(self._log, f"[exec] smart_click 未命中: {raw!r}")
        return False

    async def wait_shell_game_frame(self, *, timeout_ms: int) -> bool:
        try:
            await self.page.wait_for_function(
                """() => (location.href || '').toLowerCase().includes('game-frame')""",
                timeout=timeout_ms,
            )
            self._touch_progress()
            return True
        except Exception:
            return False

    async def goto_home(self) -> None:
        from l3_client.local_mcps.kalaroko_monitor.mcp_kalaroko_monitor import (
            _goto_resilient,
        )

        await _goto_resilient(self.page, self.home, "domcontentloaded", 90_000)
        self._touch_progress()

    async def prepare_lobby(self) -> None:
        from l3_client.local_mcps.kalaroko_monitor.mcp_kalaroko_monitor import (
            _prepare_kalaroko_lobby_after_navigation,
        )

        await _prepare_kalaroko_lobby_after_navigation(self.page, progress=self._log)
        self._touch_progress()

    async def retreat_lobby(self, scen: dict[str, Any]) -> None:
        from l3_client.local_mcps.kalaroko_monitor.mcp_kalaroko_monitor import (
            _tactical_retreat_to_platform_home,
        )

        try:
            await _tactical_retreat_to_platform_home(
                self.page, scen, progress=self._log
            )
        except Exception as e:
            _log(self._log, f"[exec] tactical_retreat 异常: {e}")
        try:
            await self.goto_home()
            await self.prepare_lobby()
        except Exception:
            pass
        self._touch_progress()

    async def try_coin_join_modal(self) -> int:
        """Select Coins / One Round → Join。返回点击 Join 次数。"""
        coin_title = re.compile(r"Select\s+Coins", re.I)
        one_round = re.compile(r"One\s+Round", re.I)
        join_pat = re.compile(r"^\s*Join\s*$", re.I)
        clicks = 0
        try:
            frames = [self.page.main_frame] + [
                f for f in self.page.frames if f != self.page.main_frame
            ]
        except Exception:
            frames = [self.page.main_frame]
        for fr in frames:
            try:
                has_coin = await fr.get_by_text(coin_title).count()
                has_round = await fr.get_by_text(one_round).count()
                if has_coin < 1 and has_round < 1:
                    continue
                jn = fr.get_by_role("button", name=join_pat)
                if await jn.count() > 0:
                    await jn.last.click(timeout=5_000, force=True)
                    clicks += 1
                    self._touch_progress()
            except Exception:
                continue
        if clicks:
            _log(self._log, f"[exec] coin join 点击×{clicks}")
        return clicks


async def _read_gold(page: Any) -> tuple[float | None, str]:
    h = await _snapshot_lobby_wallet(page)
    return _first_number_from_hints(h)


async def _run_game_loop(
    ex: GameExecutor,
    *,
    scenario_key: str,
    playbook: str,
    max_rounds: int,
    log: Callable[[str], None] | None,
) -> tuple[bool, list[str]]:
    notes: list[str] = []
    for rnd in range(max_rounds):
        if ex.stall_exceeded():
            await ex.save_diag_shot(f"stall_{scenario_key}_{rnd}")
            brief = await _gather_dom_brief(ex.page)
            decision = await ex.analyze_blockage(
                scenario_key=scenario_key, playbook=playbook, dom_brief=brief
            )
            notes.append(f"stall_recovery: {decision!r}")
            act = str(decision.get("action") or "noop").lower()
            if act == "refresh_lobby":
                await ex.goto_home()
                await ex.prepare_lobby()
                return False, notes
            ex._last_progress = time.monotonic()

        brief = await _gather_dom_brief(ex.page)
        png = await ex.screenshot_png()
        decision = await asyncio.to_thread(
            ex._l3.decide,
            phase=f"round_{rnd}",
            scenario_key=scenario_key,
            playbook=playbook,
            dom_brief=brief,
            screenshot_png=png,
            extra=f"当前轮次={rnd}；若已在平台大厅且仍可见多款游戏卡片，可 finished。",
        )
        act = str(decision.get("action") or "noop").lower()
        tgt = str(decision.get("target_text") or "").strip()
        notes.append(f"r{rnd}:{decision!r}")

        if act == "finished":
            ex._touch_progress()
            return True, notes
        if act == "refresh_lobby":
            await ex.goto_home()
            await ex.prepare_lobby()
            return False, notes
        if act in ("click_text", "js_click_text") and tgt:
            ok = await ex.smart_click(tgt, prefer_js=(act == "js_click_text"))
            if not ok:
                notes.append(f"click_failed:{tgt}")
            try:
                await ex.page.wait_for_load_state("domcontentloaded", timeout=5_000)
            except Exception:
                pass
        elif act == "wait":
            try:
                w = int(decision.get("wait_ms") or 800)
            except (TypeError, ValueError):
                w = 800
            w = max(200, min(w, 8_000))
            try:
                await ex.page.wait_for_timeout(w)
            except Exception:
                await asyncio.sleep(w / 1000.0)
        elif act == "noop":
            try:
                await ex.page.wait_for_timeout(500)
            except Exception:
                await asyncio.sleep(0.5)

        # URL 已回非 game-frame → 视作完成
        try:
            u = (ex.page.url or "").lower()
            if "game-frame" not in u and rnd > 1:
                ex._touch_progress()
                return True, notes
        except Exception:
            pass

    notes.append("max_rounds_exceeded")
    return False, notes


async def _run_one_game(
    page: Any,
    *,
    scenario_key: str,
    home: str,
    l3: L3DecisionClient,
    log: Callable[[str], None] | None,
    loop_rounds: int,
    stall_sec: float,
) -> GameRunResult:
    notes: list[str] = []
    cfg = GAME_CONFIGS.get(scenario_key) or {}
    playbook = str(cfg.get("playbook_zh") or "")
    scen = _scenario_by_name(scenario_key, home=home)

    ex = GameExecutor(
        page,
        home=home,
        log=log,
        stall_sec=stall_sec,
        l3=l3,
    )

    await ex.goto_home()
    await ex.prepare_lobby()
    ini, ini_d = await _read_gold(page)
    _log(log, f"[gold] {scenario_key} 进场前 initial≈{ini} ({ini_d})")

    ok_entry = False
    try:
        from l3_client.local_mcps.kalaroko_monitor.mcp_kalaroko_monitor import (
            _diagnose_and_click_kalaroko_game_entry,
        )

        await _diagnose_and_click_kalaroko_game_entry(
            page,
            click_selector=str(scen["click_selector"]),
            click_timeout_ms=int(scen.get("click_timeout_ms") or 10_000),
            scenario_name=scenario_key,
            scenario=scen,
            progress=log,
        )
        ok_entry = await ex.wait_shell_game_frame(timeout_ms=95_000)
        notes.append(f"shell={'ok' if ok_entry else 'timeout'}")
    except Exception as e:
        notes.append(f"entry_err:{e!s}"[:240])

    ok_loop = False
    if ok_entry:
        await ex.try_coin_join_modal()
        skip_deep = (os.environ.get("K11_AGENT_EXEC_SKIP_DEEP_WAIT") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        if not skip_deep:
            from l3_client.local_mcps.kalaroko_monitor.mcp_kalaroko_monitor import (
                _game_deep_wait_after_goto,
            )

            t0 = time.perf_counter()
            ws_times: list[float] = []

            def _on_ws(_ws: Any) -> None:
                ws_times.append(time.perf_counter())

            page.on("websocket", _on_ws)
            try:
                await _game_deep_wait_after_goto(
                    page,
                    t0,
                    int(scen.get("timeout_ms") or 90_000),
                    ws_times,
                    click_flow=True,
                )
            finally:
                try:
                    page.remove_listener("websocket", _on_ws)
                except Exception:
                    pass
        await ex.try_coin_join_modal()
        ok_loop, loop_notes = await _run_game_loop(
            ex,
            scenario_key=scenario_key,
            playbook=playbook,
            max_rounds=loop_rounds,
            log=log,
        )
        notes.extend(loop_notes)
    else:
        _log(log, f"[warn] {scenario_key} 未进入 game-frame，跳过局内循环")

    await ex.retreat_lobby(scen)
    fin, fin_d = await _read_gold(page)
    _log(log, f"[gold] {scenario_key} 退场后 final≈{fin} ({fin_d})")

    settled: bool | None = None
    if ini is not None and fin is not None:
        settled = ini != fin
    elif ini is None and fin is None:
        settled = None
    else:
        settled = None

    return GameRunResult(
        scenario_key=scenario_key,
        ok_entry=ok_entry,
        ok_loop=ok_loop,
        initial_gold=ini,
        final_gold=fin,
        is_settled=settled,
        notes=notes,
    )


async def _async_main(args: argparse.Namespace) -> int:
    from playwright.async_api import async_playwright

    home = str(args.site).strip().rstrip("/") + "/"
    host = urlparse_host(home)
    cdp = _kalaroko_cdp_endpoint(args.cdp_http)
    if not cdp:
        print(
            "[失败] 未配置 KALAROKO_CDP_ENDPOINT（或 --cdp-http）。请先启动 launch_chrome_debug.ps1。",
            file=sys.stderr,
        )
        return 2

    games = [g.strip() for g in args.games.split(",") if g.strip()]
    if not games:
        games = list(DEFAULT_GAME_ORDER)

    def log(msg: str) -> None:
        print(msg, flush=True)

    l3 = L3DecisionClient(
        base_url=str(args.l3_base),
        timeout_sec=float(args.l3_timeout_sec),
        max_iterations=int(args.l3_max_iterations),
        include_screenshot=bool(args.l3_screenshot),
    )

    results: list[GameRunResult] = []
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(cdp)
        if not browser.contexts:
            print("[失败] CDP 无 context", file=sys.stderr)
            return 2
        ctx = browser.contexts[0]
        pages = list(ctx.pages)
        if not pages:
            print("[失败] 无打开标签页", file=sys.stderr)
            return 2

        picked = None
        for pg in reversed(pages):
            try:
                u = (pg.url or "").lower()
            except Exception:
                u = ""
            if host and host.lower() in u:
                picked = pg
                break
        if picked is None:
            if args.navigate_if_no_tab:
                picked = pages[-1]
                log(f"[nav] goto {home}")
                await picked.goto(home, wait_until="domcontentloaded", timeout=90_000)
            else:
                print(
                    f"[失败] 没有 URL 含 {host!r} 的标签；请打开站点或加 --navigate-if-no-tab",
                    file=sys.stderr,
                )
                return 2
        else:
            await picked.bring_to_front()
        page = picked

        for g in games:
            log(f"\n======== 开始: {g} ========")
            try:
                r = await _run_one_game(
                    page,
                    scenario_key=g,
                    home=home,
                    l3=l3,
                    log=log,
                    loop_rounds=int(args.loop_rounds),
                    stall_sec=float(args.stall_sec),
                )
                results.append(r)
                log(
                    f"  → entry={r.ok_entry} loop_ok={r.ok_loop} "
                    f"gold_ini={r.initial_gold} gold_fin={r.final_gold} settled={r.is_settled}"
                )
            except Exception as e:
                log(f"[错误] {g}: {e!s}")
                results.append(
                    GameRunResult(
                        scenario_key=g,
                        ok_entry=False,
                        ok_loop=False,
                        initial_gold=None,
                        final_gold=None,
                        is_settled=None,
                        notes=[f"fatal:{e!s}"[:300]],
                    )
                )

    # 汇总
    print("\n======== 汇总 ========", flush=True)
    runnable_ok = sum(1 for r in results if r.ok_entry and r.ok_loop)
    coin_pass = sum(
        1 for r in results if r.is_settled is True
    )
    coin_fail = sum(
        1 for r in results if r.is_settled is False
    )
    for r in results:
        print(
            f"  {r.scenario_key:20} entry+loop={r.ok_entry and r.ok_loop} "
            f"settled={r.is_settled} ini={r.initial_gold} fin={r.final_gold}",
            flush=True,
        )
    print(
        f"可运行闭环（入场+局内 agent 循环）: {runnable_ok}/{len(results)}；"
        f"金币有变化: {coin_pass}；金币未变化: {coin_fail}",
        flush=True,
    )
    rc = 0 if runnable_ok == len(results) and coin_fail == 0 else 1
    if coin_fail and runnable_ok == len(results):
        rc = 1
    return rc


def urlparse_host(url: str) -> str:
    from urllib.parse import urlparse

    try:
        p = urlparse(url)
        return (p.hostname or "").strip()
    except Exception:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser(description="K11 Agent-Executor 游戏冒烟")
    ap.add_argument("--site", default=DEFAULT_SITE, help="站点根 URL")
    ap.add_argument(
        "--games",
        default=",".join(DEFAULT_GAME_ORDER),
        help="逗号分隔 scenario key，默认七款全开",
    )
    ap.add_argument("--cdp-http", default=None, help="覆盖 KALAROKO_CDP_ENDPOINT")
    ap.add_argument(
        "--navigate-if-no-tab",
        action="store_true",
        help="找不到含 host 的标签时 goto 当前 last tab",
    )
    ap.add_argument("--l3-base", default=DEFAULT_L3)
    ap.add_argument("--l3-timeout-sec", type=float, default=180.0)
    ap.add_argument("--l3-max-iterations", type=int, default=6)
    ap.add_argument(
        "--l3-screenshot",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="将视口截图以 data_url 附件发给 L3（默认开）",
    )
    ap.add_argument("--loop-rounds", type=int, default=22, help="每款游戏 agent 决策最大轮数")
    ap.add_argument(
        "--stall-sec",
        type=float,
        default=20.0,
        help="单局无进展阈值（秒），超则截图并 analyze_blockage",
    )
    args = ap.parse_args()
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
