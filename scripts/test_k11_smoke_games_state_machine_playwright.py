#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K11 平台冒烟 · 6 款游戏状态机轻量测（独立脚本，不依赖已移除的 P0 key 长测）

对齐《K11_平台冒烟测试用例》P0 行 42-43：各游戏可运行、金币可结算感知的粗测。

**思路**（粗测/冒烟，非强跑通）：基线金币 → 进壳（**多路**：主 URL、hash、子 frame
URL、gweb/iframe 等，避免「人已在局内仍报未进场」）→ 预载 → 各游戏**限时**点关键 UI
→ 游玩窗口 **cap + 无进展早退**（卡壳记 ``smoke_note``，回大厅后下一款）→ 回 Home
采金币。不追求复杂终局正则以硬条件；不对劲则记日志并进入下一项。

环境：KALAROKO_CDP_ENDPOINT、pip install playwright

**飞书卡片**（完成通知，与 ``test_k11_unified_platform_smoke_playwright`` 同 ``send_k11_smoke_lark_notification``；
**不写** Wiki/多维表）：``K11_SMOKE_LARK_APP_ID`` / ``K11_SMOKE_LARK_APP_SECRET``、
``K11_SMOKE_LARK_NOTIFY_CHAT_ID``；加 ``--no-lark-report`` 可关闭。

大厅进游戏点击由 ``l3_client/.../mcp_kalaroko_monitor._diagnose_and_click_kalaroko_game_entry`` 实现：
含 ``document_game_id`` 时走「ID 容器 / 多候选 / URL 前进 / 去遮罩」等策略（多游戏勿单押 ``.first`` 文案）。

用法：python scripts/test_k11_smoke_games_state_machine_playwright.py
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import math
import os
import platform
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TextIO

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
except Exception:
    pass

os.environ.setdefault(
    "KALAROKO_MONITOR_ALLOWED_HOSTS",
    "kalaroko.com,www.kalaroko.com,gweb.kalaroko.com,gwp.heronpro.xin,"
    "herontest.xin,www.herontest.xin,gweb.herontest.xin",
)

DEFAULT_TARGET = "https://www.herontest.xin/"

# 与 ``scripts/k11_lark_smoke_report.py`` / 统合冒烟脚本一致，用于飞书卡片内 Wiki 链接
K11_DEFAULT_LARK_WIKI_URL = (
    "https://ssgkm409t6q5.sg.larksuite.com/wiki/ZyWlwhdW1iNQuykvy7qlw93sgTe"
)

# 执行过程详细日志目录（可用环境变量 K11_SMOKE_EXEC_LOG_DIR 覆盖）
def _default_smoke_exec_journal_dir() -> Path:
    env = (os.environ.get("K11_SMOKE_EXEC_LOG_DIR") or "").strip()
    if env:
        return Path(env)
    return Path.home() / ".jachin" / "jachin_debug" / "冒烟测试"


class SmokeExecJournal:
    """
    冒烟脚本执行流水：阶段、异常、页态等追加写入 UTF-8 文本（行缓冲）。
    """

    def __init__(self, file_path: Path) -> None:
        self.path = file_path
        self._fp: TextIO = open(  # noqa: SIM115 — 脚本级长生命周期
            file_path,
            "a",
            encoding="utf-8",
            buffering=1,
            newline="\n",
        )

    def _ts(self) -> str:
        return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")

    def line(self, msg: str, *, level: str = "INFO") -> None:
        for raw in msg.splitlines() or [msg]:
            self._fp.write(f"{self._ts()} [{level}] {raw}\n")
        self._fp.flush()

    def phase(self, title: str, detail: str = "") -> None:
        self.line(f"━━ 阶段: {title}" + (f"  |  {detail}" if detail else ""), level="PHASE")

    def situation(self, what: str) -> None:
        self.line(f"当前情况: {what}", level="STATE")

    def problem(self, msg: str) -> None:
        self.line(f"问题/风险: {msg}", level="WARN")

    def exception(self, label: str, e: BaseException) -> None:
        self.line(f"异常 [{label}]: {_brief_exc_static(e)}", level="ERROR")

    async def snapshot_page(self, page: Any, label: str = "page") -> None:
        parts: list[str] = []
        try:
            parts.append(f"url={((page.url or '')[:500])!r}")
        except Exception as e:
            parts.append(f"url_err={e!s}")
        try:
            n = len(getattr(page, "frames", []) or [])
            parts.append(f"frames={n}")
        except Exception as e:
            parts.append(f"frames_err={e!s}")
        self.line(f"快照 [{label}]: " + " | ".join(parts), level="SNAPSHOT")

    def header_block(self, lines: list[str]) -> None:
        self.line("=" * 72, level="META")
        for ln in lines:
            self.line(ln, level="META")
        self.line("=" * 72, level="META")

    def close(self) -> None:
        try:
            self._fp.close()
        except Exception:
            pass


def _brief_exc_static(e: BaseException) -> str:
    return f"{type(e).__name__}: {e!s}"[:500]


def _open_smoke_journal(args: argparse.Namespace) -> SmokeExecJournal | None:
    if getattr(args, "no_exec_journal", False):
        return None
    base = _default_smoke_exec_journal_dir()
    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"[warn] 无法创建执行日志目录 {base}: {e}", file=sys.stderr)
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = base / f"k11_smoke_exec_{stamp}.log"
    try:
        j = SmokeExecJournal(path)
        j.header_block(
            [
                "K11 smoke state machine — 执行日志",
                f"文件: {path}",
                f"平台: {platform.platform()}  Python: {sys.version.split()[0]}",
                f"cwd: {os.getcwd()}",
                f"argv: {sys.argv}",
            ]
        )
        j.line(f"本机默认目录: {_default_smoke_exec_journal_dir()}", level="META")
        j.line(
            "说明: PHASE=阶段划分 STATE=当前情况 WARN=问题/异常 SNAPSHOT=URL/帧数",
            level="META",
        )
        print(f"[执行日志] 详细记录写入: {path}", flush=True)
        return j
    except Exception as e:
        print(f"[warn] 无法打开执行日志文件: {e}", file=sys.stderr)
        return None


def _journal_log_env_snippet(j: SmokeExecJournal | None) -> None:
    if not j:
        return
    keys = (
        "KALAROKO_CDP_ENDPOINT",
        "K11_SM_ENTRY_TIMEOUT",
        "K11_SM_PRE_WAIT",
        "K11_SM_PLAY_CAP",
        "K11_SM_NO_ENTRY_PLAY",
        "K11_SM_STALL_POLLS",
        "KALAROKO_MONITOR_ALLOWED_HOSTS",
    )
    lines = ["环境变量(摘录):"]
    for k in keys:
        v = os.environ.get(k)
        if v is not None and str(v).strip() != "":
            snip = str(v)[:200] + ("…" if len(str(v)) > 200 else "")
            lines.append(f"  {k}={snip!r}")
        else:
            lines.append(f"  {k}=<未设置>")
    j.line("\n".join(lines), level="META")

# —— 与 K11 粗采样同源：多 frame 拼 hints ——
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


def _load_p0() -> Any:
    path = ROOT / "scripts" / "test_k11_p0_platform_smoke_playwright.py"
    spec = importlib.util.spec_from_file_location("k11_p0_platform", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _scenario_for_game(name: str) -> dict[str, Any]:
    from l3_client.local_mcps.kalaroko_monitor.mcp_kalaroko_monitor import (
        KALAROKO_DEFAULT_SCENARIOS,
    )

    for s in KALAROKO_DEFAULT_SCENARIOS:
        if str(s.get("name") or "") == name:
            return dict(s)
    raise ValueError(f"未知游戏: {name}")


def _log_step(log: Callable[[str], None], msg: str) -> None:
    log(f"[步骤] {msg}")


# 每款游戏「预计游玩窗口」秒（含自动播片）；到时或早退后回大厅
GAME_DURATION_SEC: dict[str, int] = {
    "texas_holdem": 75,
    "texas_holdem_plus": 75,
    "mines_clash": 90,
    "crazy_solitaire": 90,
    "unleash_running": 100,
    "pinoy_monopoly": 100,
}

_GAME_ORDER = [
    "texas_holdem",
    "texas_holdem_plus",
    "mines_clash",
    "crazy_solitaire",
    "unleash_running",
    "pinoy_monopoly",
]

# herontest 大厅：游戏名在 `div` class 含 `_gameName_` 的单元格内，与全站裸 text= 比更易唯一定位
_GAME_LOBBY_DISPLAY: dict[str, str] = {
    "texas_holdem": "Texas Holdem",
    "texas_holdem_plus": "Texas Holdem Plus",
    "mines_clash": "Mines Clash",
    "crazy_solitaire": "Crazy Solitaire",
    "unleash_running": "Unleash Running",
    "pinoy_monopoly": "Pinoy Monopoly",
}


def _k11_lobby_click_selector_string(game: str) -> str | None:
    """与网格标题格 DOM 一致：div._gameName_* 下的文案；避免与页眉/搜索等其它 text 误匹配。"""
    label = _GAME_LOBBY_DISPLAY.get(game, "").strip()
    if not label:
        return None
    words = label.split()
    if not words:
        return None
    pat = r"\s+".join(re.escape(w) for w in words)
    return f'div[class*="_gameName_"] >> text=/{pat}/i'


def _k11_skip_lobby_home_all() -> bool:
    v = (os.environ.get("K11_SKIP_LOBBY_HOME_ALL") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


async def _k11_ensure_lobby_home_all_and_scroll_game(
    page: Any,
    game: str,
    log: Callable[[str], None],
    journal: SmokeExecJournal | None = None,
) -> None:
    """
    进入列表路径：点 Home 回大厅 → 点 All 展开展示全部游戏 → 将目标 `._gameName_` 行滚入视区。
    可通过 ``K11_SKIP_LOBBY_HOME_ALL=1`` 跳过（仅调试用）。
    """
    if _k11_skip_lobby_home_all():
        _log_step(log, "K11: 已设 K11_SKIP_LOBBY_HOME_ALL，跳过 Home/All/预滚动")
        return
    _log_step(log, "K11: 稳定大厅流（Home → All → 目标游戏入视区）…")
    if journal:
        journal.situation("K11 预检: Home、All 标签、游戏名滚入视区")
    settle = 500

    try:
        for alt in ("Home", "HOME", "home"):
            h = page.locator(f'img[alt="{alt}"]')
            if await h.count() < 1:
                continue
            u = h.first
            if await u.is_visible(timeout=1_800):
                await u.click(timeout=5_000)
                await page.wait_for_timeout(settle)
                if journal:
                    journal.line("K11: 已点 Home 图标", level="STATE")
                break
    except Exception as e:
        if journal:
            journal.problem(f"K11: Home: {e!s}"[:180])

    try:
        al = page.locator('span[class*="_tabTitle_"]').filter(
            has_text=re.compile(r"^All$", re.I)
        )
        if await al.count() > 0 and await al.first.is_visible(timeout=2_000):
            await al.first.click(timeout=5_000)
            await page.wait_for_timeout(700)
            if journal:
                journal.line("K11: 已点 All 标签", level="STATE")
    except Exception as e:
        if journal:
            journal.problem(f"K11: All 标签: {e!s}"[:180])

    label = _GAME_LOBBY_DISPLAY.get(game, "").strip()
    if not label:
        return
    try:
        frames: list[Any] = []
        try:
            frames = [page.main_frame] + [
                f for f in (page.frames or []) if f != page.main_frame
            ]
        except Exception:
            frames = [page.main_frame]
        seen = 0
        for fr in frames:
            try:
                gcell = fr.locator('div[class*="_gameName_"]').get_by_text(
                    label, exact=True
                )
            except Exception:
                continue
            n = await gcell.count()
            if n < 1:
                try:
                    gcell = fr.locator('div[class*="_gameName_"]').filter(
                        has_text=re.compile(
                            r"^\s*" + re.escape(label) + r"\s*$", re.I
                        )
                    )
                    n = await gcell.count()
                except Exception:
                    n = 0
            if n > 0:
                seen = n
                await gcell.first.scroll_into_view_if_needed(timeout=6_000)
                await page.wait_for_timeout(400)
                if journal:
                    journal.line(
                        f"K11: 游戏名已滚入视区 {label!r} (n={n})",
                        level="STATE",
                    )
                break
        if seen == 0 and journal:
            journal.problem(
                f"K11: 各 frame 未找到标题格 {label!r}，仍尝试 MCP 入口点击"
            )
    except Exception as e:
        if journal:
            journal.problem(f"K11: 预滚动: {e!s}"[:180])


def _k11_env_skip_coin_join() -> bool:
    v = (os.environ.get("K11_SKIP_COIN_JOIN") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


async def _k11_try_select_coins_join_modal(
    page: Any,
    log: Callable[[str], None],
    journal: SmokeExecJournal | None = None,
    *,
    phase: str = "",
    attempts: int = 3,
    between_ms: int = 500,
) -> int:
    """
    部分游戏点进后先出「Select Coins / One Round」再点蓝底 **Join** 才进房。
    多 frame 扫描；无弹层则 0 次；``attempts`` 轮短隔以覆盖晚渲染。
    设 ``K11_SKIP_COIN_JOIN=1`` 可关闭（调试用）。
    """
    if _k11_env_skip_coin_join():
        return 0
    coin_title = re.compile(r"Select\s+Coins", re.I)
    one_round = re.compile(r"One\s+Round", re.I)
    for attempt in range(max(1, attempts)):
        try:
            frames: list[Any] = [page.main_frame] + [
                f for f in (page.frames or []) if f != page.main_frame
            ]
        except Exception:
            frames = [page.main_frame]
        for fr in frames:
            try:
                has_coin = await fr.get_by_text(coin_title).count()
                has_round = await fr.get_by_text(one_round).count()
                if has_coin < 1 and has_round < 1:
                    continue
            except Exception:
                continue
            for mk_join in (
                lambda f: f.get_by_role(
                    "button", name=re.compile(r"^Join$", re.I)
                ),
                lambda f: f.get_by_text(re.compile(r"^Join$", re.I)),
            ):
                try:
                    jl = mk_join(fr)
                    n = await jl.count()
                    if n < 1:
                        continue
                    j = jl.last if n > 1 else jl.first
                    if not await j.is_visible(timeout=2_500):
                        continue
                    await j.scroll_into_view_if_needed(timeout=5_000)
                    tag = f"{phase} " if phase else ""
                    _log_step(
                        log,
                        f"K11: {tag}检测到下注/选币层 (try {attempt + 1})，点击 Join",
                    )
                    if journal:
                        journal.situation(
                            f"Select Coins / One Round → Join ({tag}#{attempt + 1})"
                        )
                    try:
                        await j.click(timeout=12_000, force=True)
                    except Exception:
                        await j.evaluate("e => e.click()")
                    log("  [action] 已点 Join（Select Coins 弹层）")
                    try:
                        await page.wait_for_timeout(800)
                    except Exception:
                        pass
                    return 1
                except Exception:
                    continue
        if attempt + 1 < attempts:
            try:
                await page.wait_for_timeout(max(0, between_ms))
            except Exception:
                pass
    return 0


def _mcp_imports() -> tuple:
    from l3_client.local_mcps.kalaroko_monitor.mcp_kalaroko_monitor import (
        _diagnose_and_click_kalaroko_game_entry,
        _game_deep_wait_after_goto,
        _goto_resilient,
        _prepare_kalaroko_lobby_after_navigation,
    )

    return (
        _diagnose_and_click_kalaroko_game_entry,
        _game_deep_wait_after_goto,
        _goto_resilient,
        _prepare_kalaroko_lobby_after_navigation,
    )


# 与 kalaroko 生产/测试站一致：进桌未必出现字面 ``game-frame``，可能是 gweb 子域 iframe
_JS_SMOKE_SHELL_SIGNAL = r"""() => {
  const h = (location && location.href) || "";
  const s = h.toLowerCase();
  if (s.includes("game-frame")) return "url:game-frame";
  if (/[?&](gameid|game_id)=\d+/i.test(h)) return "url:gameid_qs";
  const ha = (location && location.hash) || "";
  if (ha && /gameid|game_id|room|table|party/i.test(ha) && /\d{2,}/.test(ha))
    return "url:hash_game";
  if (s.includes("gweb.")) return "url:gweb_host";
  for (const el of document.querySelectorAll("iframe[src],frame[src]")) {
    const t = (el.getAttribute("src") || "").toLowerCase();
    if (!t) continue;
    if (t.includes("game-frame")) return "dom_iframe:game-frame";
    if (t.includes("gweb.")) return "dom_iframe:gweb";
    if (t.includes("gweb") && (t.includes("game") || t.includes("play") || t.includes("frame")))
      return "dom_iframe:gweb_game";
  }
  return "";
}"""


def _url_suggests_game_shell(url: str) -> bool:
    """子 frame 的 network URL 比主站更像「进壳」；与 ``_JS_SMOKE_SHELL_SIGNAL`` 对齐。"""
    u = (url or "").strip()
    if not u or u.lower().startswith("about:blank"):
        return False
    s = u.lower()
    if "game-frame" in s:
        return True
    if re.search(r"[?&](?:gameid|game_id)=\d+", s):
        return True
    if s.startswith("http") and "gweb." in s:
        return True
    if "gwp." in s and "game" in s:
        return True
    if "heronpro" in s and "game" in s:
        return True
    return False


async def _shell_signal_from_all_frames(
    page: Any,
) -> tuple[bool, str]:
    """用 Playwright 的 frame.url 补 DOM 嗅探（含晚挂载/跨子域）。"""
    for fr in getattr(page, "frames", None) or []:
        try:
            u = getattr(fr, "url", None) or ""
            if _url_suggests_game_shell(str(u)):
                return True, f"frame_url:{str(u)[:140]!r}"
        except Exception:
            continue
    return False, ""


def _format_frame_url_diag(page: Any, max_frames: int = 5) -> str:
    parts: list[str] = []
    for i, fr in enumerate(
        (getattr(page, "frames", None) or [])[:max_frames]
    ):
        try:
            u = (getattr(fr, "url", None) or "")[:160]
            parts.append(f"  f{i}: {u!r}")
        except Exception as e:
            parts.append(f"  f{i}: <err {e!s}>")
    if not parts:
        return "（无 frames 列表）"
    return "\n".join(parts)


async def _wait_entry_shell(
    page: Any,
    *,
    timeout_sec: float,
    log: Callable[[str], None],
) -> tuple[bool, str]:
    """
    多路进壳；返回 (ok, 首条命中原因)，避免仅认 ``game-frame`` 子串而误判未进场。
    """
    t_end = time.monotonic() + max(5.0, timeout_sec)
    first_reason = ""
    while time.monotonic() < t_end:
        try:
            u = page.url or ""
            if _url_suggests_game_shell(u):
                r = f"page_url:{(u or '')[:120]!r}"
                _log_step(log, f"进壳: 主 URL 符合壳特征 → {r}")
                return True, r
        except Exception:
            pass
        try:
            sig = await page.main_frame.evaluate(_JS_SMOKE_SHELL_SIGNAL)
            if isinstance(sig, str) and sig.strip():
                first_reason = first_reason or sig
                _log_step(log, f"进壳: 主文档探测 → {sig[:160]}")
                return True, str(sig)[:200]
        except Exception:
            pass
        try:
            ok, frs = await _shell_signal_from_all_frames(page)
            if ok:
                if not first_reason:
                    first_reason = frs
                _log_step(log, f"进壳: 子 frame URL → {frs[:200]}")
                return True, frs[:200]
        except Exception:
            pass
        try:
            await page.wait_for_timeout(320)
        except Exception:
            pass
    return False, first_reason


async def _sniff_entry_once(page: Any) -> tuple[bool, str]:
    """单次多路嗅探（供补检循环使用）。"""
    try:
        if _url_suggests_game_shell(page.url or ""):
            return True, f"page_url:{(page.url or '')[:120]!r}"
    except Exception:
        pass
    try:
        sig = await page.main_frame.evaluate(_JS_SMOKE_SHELL_SIGNAL)
        if isinstance(sig, str) and sig.strip():
            return True, str(sig)[:200]
    except Exception:
        pass
    return await _shell_signal_from_all_frames(page)


async def _entry_last_chance_reconfirm(
    page: Any, log: Callable[[str], None]
) -> tuple[bool, str]:
    """深等已结束但主检测未认：短轮询补认 gweb iframe 晚挂载等。"""
    for i in range(10):
        ok, s = await _sniff_entry_once(page)
        if ok:
            _log_step(log, f"进壳补检命中（第 {i + 1}/10）: {s[:160]}")
            return True, s
        try:
            await page.wait_for_timeout(400)
        except Exception:
            pass
    return False, ""


_JS_ROUND_FLAG = r"""() => {
  try {
    if (window.jachinRoundEnded === true) return "jachinRoundEnded";
    for (const k of ["jachinRoundEnded", "jachinGameEnded", "__JACHIN_GAME_STATUS__"]) {
      const v = window[k];
      if (v === true) return k;
      if (v && /^(finished|ended|complete|settled)$/i.test(String(v))) return k + "=" + v;
    }
  } catch (e) {}
  return "";
}"""


async def _probe_window_round_ended(page: Any) -> str:
    for fr in [page.main_frame] + [f for f in page.frames if f != page.main_frame]:
        try:
            s = await fr.evaluate(_JS_ROUND_FLAG)
            if isinstance(s, str) and s.strip():
                return s.strip()[:80]
        except Exception:
            continue
    return ""


_MINES_DOM_ROUND_END_PATTERNS: tuple[re.Pattern[str], ...] = (
    # 结算态；勿用单独「Cash Out」——局中主 CTA 常一直可见，易误判已结束
    re.compile(r"\bcashed\s*out\b", re.I),
    re.compile(r"\b(play\s+again|replay|next\s+round|new\s+round)\b", re.I),
    re.compile(r"\b(round|game)\s+(over|complete|finished|ended)\b", re.I),
    re.compile(r"\b(you('ve)?\s+(won|win|lost|lose))\b", re.I),
    re.compile(r"\bcongratulations\b", re.I),
    re.compile(r"\b(total\s*win(nings)?|net\s*(profit|win))\b", re.I),
    re.compile(r"\b(return\s*to\s*lobby|back\s*to\s*(lobby|home))\b", re.I),
    re.compile(r"\b(boom|exploded|mine\s*hit)\b", re.I),
    re.compile(r"再來一局|再玩一局|返回大廳|返回大厅|收集獎勵|收集奖励"),
    re.compile(r"結算|勝利|恭喜|很遗憾|敗北|(?:本|這)局[\s\S]{0,6}(?:結束|完成)"),
)


async def _probe_mines_dom_round_heuristic(page: Any) -> str:
    """Mines/gweb：子 frame DOM 文案启发式识别一局已结束或可进入下一轮。"""
    frames = [page.main_frame] + [f for f in page.frames if f != page.main_frame]
    blobs: list[str] = []
    for fr in frames:
        try:
            s: str = await fr.evaluate(
                r"""() => {
                  try {
                    const b = document.body;
                    if (!b) return '';
                    return (b.innerText || '').slice(0, 120000);
                  } catch (e) { return ''; }
                }"""
            )
        except Exception:
            continue
        if (s or "").strip():
            blobs.append(s.lower())
    if not blobs:
        return ""
    text = "\n".join(blobs)
    for rx in _MINES_DOM_ROUND_END_PATTERNS:
        m = rx.search(text)
        if m:
            hit = str(m.group(0))[:52]
            return f"mines_dom:{hit!r}"
    return ""


async def _probe_play_phase_round_ended(
    page: Any, game_key: str, *, phase_elapsed_sec: float = 0.0
) -> str:
    """游玩阶段：先测 __JACHIN__ 钩；再对 mines_clash 做 DOM 启发式（需已过最短局内时间，防教程文案）。"""
    w = await _probe_window_round_ended(page)
    if w:
        return w
    if (game_key or "").strip().lower() != "mines_clash":
        return ""
    min_sec = float(os.environ.get("K11_MINES_HEURISTIC_MIN_SEC", "6"))
    if phase_elapsed_sec < min_sec:
        return ""
    return await _probe_mines_dom_round_heuristic(page)


def _stall_progress_key(page: Any) -> str:
    """
    游玩阶段「无进展」判据用：进壳后主 document URL 常固定为 *game-frame*，但子 frame
    （gweb 内页）会导航；只比主 URL 会误触 bail:stall_*_same_url。此处合并子 frame
    URL 摘要，有任一子壳变化则视为有进展、重置 stable。
    """
    try:
        u = (page.url or "")[:600]
    except Exception:
        u = ""
    parts: list[str] = []
    try:
        for fr in list(getattr(page, "frames", None) or []):
            try:
                fu = (getattr(fr, "url", None) or "")[:280]
            except Exception:
                fu = ""
            if fu and fu not in ("about:blank", "chrome-error://chromewebdata/"):
                parts.append(fu)
    except Exception:
        pass
    parts.sort()
    return f"{u}|" + "§".join(parts)[:3000]


async def _page_looks_like_react_error(page: Any) -> bool:
    """主文档/子 frame 内是否像 React 白屏类异常（如 removeChild）。"""
    for fr in [page.main_frame] + [f for f in page.frames if f != page.main_frame]:
        try:
            t: str = await fr.evaluate(
                r"""() => {
                  try {
                    const b = document.body;
                    if (!b) return '';
                    return (b.innerText || '').slice(0, 20000);
                  } catch (e) { return ''; }
                }"""
            )
        except Exception:
            continue
        if not t:
            continue
        u = t.lower()
        if "unexpected application error" in u and "removechild" in u:
            return True
        if "not a child of this node" in u and "removechild" in u:
            return True
    return False


async def _soft_recover_react_error_page(
    page: Any, log: Callable[[str], None]
) -> bool:
    """
    若已出现错误页，尝试一次 reload；若仍失败仅记日志，由上层回大厅逻辑收尾。
    """
    if not await _page_looks_like_react_error(page):
        return True
    _log_step(
        log,
        "检测到应用错误白屏(如 removeChild)，尝试一次 DOM reload 以恢复可测状态…",
    )
    try:
        await page.reload(wait_until="domcontentloaded", timeout=90_000)
    except Exception as e:
        _log_step(log, f"  [warn] reload 未成功: {_brief_exc_static(e)}"[:200])
        return False
    try:
        await page.wait_for_timeout(1500)
    except Exception:
        pass
    ok = not await _page_looks_like_react_error(page)
    _log_step(
        log,
        f"  reload 后错误文案仍可见={not ok!r}（如仍白屏可人工刷新或关 tab）",
    )
    return ok


def _looks_like_lobby_url(url: str, home: str) -> bool:
    u = (url or "").lower()
    h = (home or "").lower().rstrip("/")
    if "game-frame" in u:
        return False
    if not u or u in ("about:blank",):
        return False
    if h and u.rstrip("/") == h:
        return True
    if (
        ("kalaroko.com" in u or "herontest.xin" in u)
        and "game-frame" not in u
        and "/game" not in u.split("?")[0]
    ):
        return True
    return False


async def _try_click_in_frames(
    page: Any,
    *,
    name_patterns: list[re.Pattern[str]],
    log: Callable[[str], None],
    settle_after_ms: int = 350,
    click_force: bool = True,
) -> int:
    """返回成功点击次数（用于统计）。"""
    hit = 0
    for fr in [page.main_frame] + [f for f in page.frames if f != page.main_frame]:
        for pat in name_patterns:
            try:
                b = fr.get_by_role("button", name=pat)  # type: ignore[call-overload]
                if await b.count() < 1:
                    continue
                el = b.first
                if not await el.is_visible(timeout=400):
                    continue
                await el.click(timeout=2500, force=click_force)
                log(f"  [action] 已点按钮: {pat.pattern!r}")
                hit += 1
                try:
                    await page.wait_for_timeout(max(0, settle_after_ms))
                except Exception:
                    pass
            except Exception:
                continue
    return hit


# Mines Clash 等 1v1 常为 div/Canvas 壳，无 role=button，仅可见「Start Game」
_JS_START_GAME_WALKER = r"""(reSrc) => {
  try {
    const t = new RegExp(reSrc, "i");
    if (!document.body) return "";
    const tw = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
    let node;
    while (node = tw.nextNode()) {
      const raw = (node.textContent || "").trim();
      if (!raw || raw.length > 120) continue;
      if (!t.test(raw)) continue;
      let el = node.parentElement;
      for (let d = 0; d < 18 && el; d++) {
        const st = window.getComputedStyle(el);
        if (st.display === "none" || st.visibility === "hidden" || (parseFloat(String(st.opacity)) < 0.04)) {
          el = el.parentElement;
          continue;
        }
        const r = el.getBoundingClientRect();
        if (r.width < 8 || r.height < 6) { el = el.parentElement; continue; }
        const h = (window.innerHeight || 0);
        const w0 = (window.innerWidth || 0);
        if (r.bottom < -4 || r.right < -4 || r.top > h + 6 || r.left > w0 + 4) { el = el.parentElement; continue; }
        try { el.scrollIntoView({ block: "center", inline: "center" }); } catch (e) {}
        const mopts = { bubbles: true, cancelable: true, view: window };
        try {
          el.dispatchEvent(new PointerEvent("pointerdown", { ...mopts, pointerId: 1, pointerType: "mouse" }));
          el.dispatchEvent(new PointerEvent("pointerup", { ...mopts, pointerId: 1, pointerType: "mouse" }));
        } catch (e) {
          el.dispatchEvent(new MouseEvent("mousedown", mopts));
          el.dispatchEvent(new MouseEvent("mouseup", mopts));
        }
        el.dispatchEvent(new MouseEvent("click", { ...mopts, detail: 1 }));
        return "walker:" + raw.slice(0, 40);
      }
    }
  } catch (e) {}
  return "";
}"""


async def _aggressive_click_visible_text(
    page: Any,
    *,
    patterns: list[re.Pattern[str]],
    log: Callable[[str], None],
    skip_tree_walker: bool = False,
    post_click_settle_ms: int = 0,
    click_force: bool = True,
) -> int:
    """
    不依赖 ARIA：get_by_text / filter(has_text) / 文本选择器，失败则 TreeWalker + 合成事件。
    用于 Mines 等仅 div/Canvas 上显示「Start Game」、get_by_role 全空的情况。

    ``skip_tree_walker``: 为 true 时不走 JS TreeWalker+合成点击；对 React SPA
    更温和，可减少「removeChild / 非子节点」类协调错误。
    """
    frames = [page.main_frame] + [f for f in page.frames if f != page.main_frame]

    async def _after_hit() -> int:
        if post_click_settle_ms > 0:
            try:
                await page.wait_for_timeout(post_click_settle_ms)
            except Exception:
                pass
        return 1

    for fr in frames:
        for pat in patterns:
            # 1) get_by_text(正则)
            try:
                gt = fr.get_by_text(pat)  # type: ignore[call-overload]
                nct = await gt.count()
                for i in range(min(nct, 5)):
                    el = gt.nth(i)
                    if await el.is_visible(timeout=700):
                        await el.scroll_into_view_if_needed(timeout=2000)
                        await el.click(timeout=4000, force=click_force)
                        log(
                            f"  [action] get_by_text 点中: {getattr(pat, 'pattern', str(pat))!r} (nth={i})"
                        )
                        return await _after_hit()
            except Exception:
                pass
            # 2) 宽标签 + 含文
            try:
                loc = fr.locator(
                    'button, [role="button"], a, [role="link"], [role="tab"], label, div, span, p, font'
                ).filter(has_text=pat)
                n2 = await loc.count()
                for i in range(min(n2, 8)):
                    el = loc.nth(i)
                    if await el.is_visible(timeout=500):
                        await el.scroll_into_view_if_needed(timeout=2000)
                        await el.click(timeout=4000, force=click_force)
                        log(
                            f"  [action] filter(has_text) 点中: {getattr(pat, 'pattern', str(pat))!r} (nth={i})"
                        )
                        return await _after_hit()
            except Exception:
                pass
            # 3) Playwright 文本=/
            ptxt = (pat.pattern or "").replace("\\", "\\\\")
            if "/" not in ptxt and "\n" not in ptxt:
                try:
                    al = fr.locator(f"text=/{ptxt}/i")
                    na = await al.count()
                    for k in range(min(na, 4)):
                        el = al.nth(k)
                        if await el.is_visible(timeout=500):
                            await el.scroll_into_view_if_needed(timeout=2000)
                            await el.click(timeout=4000, force=click_force)
                            log(
                                f"  [action] text=/ 点中: {ptxt[:40]!r} (nth={k})"
                            )
                            return await _after_hit()
                except Exception:
                    pass
        if not skip_tree_walker:
            for pat in patterns:
                # 4) JS：最小命中文本节点，沿祖先发 pointer/mouse/click
                try:
                    rs = (pat.pattern or "") if pat else ""
                    if not rs:
                        continue
                    r: Any = await fr.evaluate(_JS_START_GAME_WALKER, rs)
                    if isinstance(r, str) and r.strip():
                        log(f"  [action] TreeWalker+合成事件: {r[:120]}")
                        return await _after_hit()
                except Exception:
                    pass
    return 0


def _k11_ordered_game_frames(page: Any) -> list:
    """
    游戏内 DOM 多在 **gweb 等子域 iframe** 内；主文档 often 仅 game-frame 壳。
    优先把 gweb / game 相关 child frame 排在主 frame 前，再扫主文档，避免在门外空找。
    """
    try:
        all_f: list = list(getattr(page, "frames", None) or [])
    except Exception:
        all_f = []
    try:
        main = page.main_frame
    except Exception:
        main = None
    gweb_like: list = []
    other_child: list = []
    for f in all_f:
        if f is main:
            continue
        try:
            u = (f.url or "").lower()
        except Exception:
            u = ""
        if any(
            k in u
            for k in (
                "gweb.",
                "game-frame",
                "gwp.",
                "/game",
                "herontest",
                "heronpro",
            )
        ):
            gweb_like.append(f)
        else:
            other_child.append(f)
    out: list = gweb_like + ([main] if main is not None else []) + other_child
    if not out and main is not None:
        return [main]
    return out or [main]


def _k11_env_skip_start_game_geometry() -> bool:
    v = (os.environ.get("K11_SKIP_START_GAME_GEOM") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


async def _k11_frame_bring_to_front_and_focus(frame: Any) -> None:
    try:
        await frame.evaluate(
            r"""() => {
              try {
                window.focus();
                if (document.body && document.body.focus) document.body.focus();
              } catch (e) {}
            }"""
        )
    except Exception:
        pass


async def _k11_try_click_start_in_one_frame(
    fr: Any,
    page: Any,
    log: Callable[[str], None],
) -> int:
    """单 frame 内多文案/大小写/role/宽选器；返回 0/1。调用前可 focus。"""
    await _k11_frame_bring_to_front_and_focus(fr)

    def _ordered(loc: Any, n: int) -> list:
        if n > 1:
            return [loc.last, loc.first]
        return [loc.first]

    # 1) 文案：全大写 START GAME（Crazy Solitaire 等）与首字母大写
    for tpat in (
        re.compile(r"^START\s*GAME$", re.I),
        re.compile(r"^Start\s*Game$", re.I),
    ):
        try:
            loc = fr.get_by_text(tpat)
            n = await loc.count()
            if n < 1:
                continue
            for oi, el in enumerate(_ordered(loc, n)):
                try:
                    await el.scroll_into_view_if_needed(timeout=8_000)
                except Exception:
                    pass
                try:
                    await page.wait_for_timeout(200)
                except Exception:
                    pass
                try:
                    if await el.is_visible(timeout=2_000):
                        await el.click(timeout=12_000, force=True)
                        tag = "last" if oi == 0 and n > 1 else "first"
                        log(
                            f"  [action] 房间 CTA: get_by_text(START/Start Game) n={n} → {tag}"
                        )
                        return 1
                except Exception:
                    try:
                        await el.evaluate("e => e.click()")
                        log("  [action] 房间 CTA: dom.click START GAME 文案匹配")
                        return 1
                    except Exception:
                        continue
        except Exception:
            pass

    # 1b) 精确子串部分 UI
    try:
        loc2 = fr.get_by_text("Start Game", exact=True)
        n2 = await loc2.count()
        if n2 >= 1:
            for oi, el in enumerate(_ordered(loc2, n2)):
                try:
                    await el.scroll_into_view_if_needed(timeout=8_000)
                    if await el.is_visible(timeout=1_500):
                        await el.click(timeout=12_000, force=True)
                        return 1
                except Exception:
                    try:
                        await el.evaluate("e => e.click()")
                        return 1
                    except Exception:
                        continue
    except Exception:
        pass

    # 2) 无障碍
    try:
        br = fr.get_by_role("button", name=re.compile(r"^start\s*game$", re.I))
        nb = await br.count()
        if nb >= 1:
            b = br.last if nb > 1 else br.first
            await b.scroll_into_view_if_needed(timeout=8_000)
            await b.click(timeout=12_000, force=True)
            log("  [action] 房间 CTA: get_by_role(button, /start game/i)")
            return 1
    except Exception:
        pass

    # 3) 宽选器
    try:
        w = fr.locator(
            'button, [role="button"], a, [role="link"], div, span, p, font'
        ).filter(has_text=re.compile(r"^start\s*game$", re.I))
        nw = await w.count()
        if nw >= 1:
            el = w.last if nw > 1 else w.first
            await el.scroll_into_view_if_needed(timeout=8_000)
            try:
                await el.click(timeout=12_000, force=True)
            except Exception:
                await el.evaluate("e => e.click()")
            log("  [action] 房间 CTA: 宽选+filter ^start\\s*game$")
            return 1
    except Exception:
        pass
    return 0


async def _k11_start_game_via_frame_locator(page: Any, log: Callable[[str], None]) -> int:
    """
    显式 ``page.frame_locator('iframe').nth(i)`` 进入各子文档再 get_by_text，
    不依赖 ``page.frames`` 枚举顺序（跨域/OOPIF 时仍常可用）。
    """
    try:
        nif = await page.locator("iframe").count()
    except Exception:
        nif = 0
    for i in range(max(0, nif)):
        try:
            fl = page.frame_locator("iframe").nth(i)
        except Exception:
            continue
        for tpat in (
            re.compile(r"START\s*GAME", re.I),
            re.compile(r"Start\s*Game", re.I),
        ):
            try:
                loc = fl.get_by_text(tpat)
                c = await loc.count()
            except Exception:
                c = 0
            if c < 1:
                continue
            el = loc.last if c > 1 else loc.first
            try:
                await el.scroll_into_view_if_needed(timeout=8_000)
                await el.click(timeout=12_000, force=True)
                log(
                    f"  [action] frame_locator(iframe).nth({i}) get_by_text(START/Start Game)"
                )
                return 1
            except Exception:
                try:
                    await el.evaluate("e => e.click()")
                    log(
                        f"  [action] frame_locator(iframe).nth({i}) dom.click 兜底"
                    )
                    return 1
                except Exception:
                    continue
    return 0


async def _k11_start_game_geometry_on_game_iframe(
    page: Any,
    log: Callable[[str], None],
) -> int:
    """
    DOM/文本不可见（Canvas/离屏等）时，对 **游戏 iframe 可见框** 做相对坐标点击
   （默认中偏下，贴近常见 CTA 条位）。可配 ``K11_START_GAME_REL_X`` / ``_Y``。
    """
    if _k11_env_skip_start_game_geometry():
        return 0
    try:
        rx = float(os.environ.get("K11_START_GAME_REL_X", "0.5") or 0.5)
        ry = float(os.environ.get("K11_START_GAME_REL_Y", "0.64") or 0.64)
    except ValueError:
        rx, ry = 0.5, 0.64
    locators: list[tuple[str, Any]] = []
    try:
        g = page.locator('iframe[src*="gweb"]').first
        if await g.count() >= 1:
            locators.append(("iframe[gweb]", g))
    except Exception:
        pass
    try:
        a = page.locator("iframe").first
        if await a.count() >= 1:
            locators.append(("iframe[0]", a))
    except Exception:
        pass
    for label, eloc in locators:
        try:
            await eloc.scroll_into_view_if_needed(timeout=5_000)
        except Exception:
            pass
        try:
            await eloc.focus()
        except Exception:
            pass
        try:
            box = await eloc.bounding_box()
        except Exception:
            box = None
        if not box or box.get("width", 0) < 8 or box.get("height", 0) < 8:
            continue
        x = float(box["x"]) + float(box["width"]) * max(0.05, min(0.95, rx))
        y = float(box["y"]) + float(box["height"]) * max(0.05, min(0.95, ry))
        try:
            await page.mouse.click(x, y)
            log(
                f"  [action] START GAME 几何兜底 {label!r} rel=({rx:.2f},{ry:.2f}) "
                f"→ 页坐标 ({x:.0f},{y:.0f})"
            )
            try:
                await page.wait_for_timeout(500)
            except Exception:
                pass
            return 1
        except Exception:
            continue
    return 0


async def _try_click_start_game_room_cta(
    page: Any,
    log: Callable[[str], None],
) -> int:
    """
    进房后 **START GAME / Start Game** CTA。优先 **gweb 等子 frame** 再主文档；
    全大写文案；再 ``frame_locator``；再 iframe 内比例点击（Canvas/不可选中文本时）。
    """
    for fr in _k11_ordered_game_frames(page):
        if await _k11_try_click_start_in_one_frame(fr, page, log):
            return 1
    if await _k11_start_game_via_frame_locator(page, log):
        return 1
    if await _k11_start_game_geometry_on_game_iframe(page, log):
        return 1
    return 0


async def _orchestrate_start_deal_and_generic(
    page: Any,
    *,
    game_key: str,
    log: Callable[[str], None],
) -> None:
    """
    多轮重试：先「Start Game」类模糊点击，再 Deal/Play/Bet/Start，最后通用入桌键。

    ``pinoy_monopoly`` 使用更温和策略（少轮次、长间隔、禁用 TreeWalker、优先非
    force 点击 + 长 settle），减轻 React 与测试竞态导致的 removeChild/Unexpected
    Error。
    """
    gk = (game_key or "").lower()
    react_gentle = gk == "pinoy_monopoly"
    # Mines/Crazy/UR：文本常在 Canvas/不可点 div 上，TreeWalker+合成 易与 React
    # 竞态；先与 Pinoy 一样禁 TW，用 get_by_text/几何兜底为主。
    canvas_gentle = gk in (
        "mines_clash",
        "crazy_solitaire",
        "unleash_running",
    )
    start_patterns: list[re.Pattern[str]] = [
        re.compile(r"Start\s*Game", re.I),
        re.compile(r"^START\s*GAME$", re.I),
        re.compile(r"^Start$", re.I),
    ]
    deal_patterns: list[re.Pattern[str]] = [
        re.compile(r"^Deal$", re.I),
        re.compile(r"^Play$", re.I),
        re.compile(r"^Bet$", re.I),
        re.compile(r"^START$", re.I),
    ]
    generic = [
        re.compile(r"^Join$", re.I),
        re.compile(r"^OK$", re.I),
        re.compile(r"^Confirm$", re.I),
        re.compile(r"^Continue$", re.I),
        re.compile(r"^Got it$", re.I),
        re.compile(r"^好的$"),
        re.compile(r"^确定$"),
    ]
    # 轻测：不长时间空转，由上层游玩阶段 timeout 与 bail 收束
    long_batch = gk in (
        "mines_clash",
        "crazy_solitaire",
        "unleash_running",
        "pinoy_monopoly",
    )
    if react_gentle:
        rounds = 8
    else:
        rounds = 12 if long_batch else 9
    settle = 900 if react_gentle else (500 if canvas_gentle else 0)
    skip_tw = react_gentle or canvas_gentle
    cforce = True
    frame_settle = 850 if react_gentle else 400 if canvas_gentle else 350
    between = 1100 if react_gentle else 750 if canvas_gentle else 500
    if react_gentle:
        _log_step(
            log,
            "Pinoy Monopoly：每轮先扫房间 CTA（Start Game）→ 再 get_by_text；"
            "已禁 TreeWalker 以减轻白屏；轮次=8",
        )
    elif canvas_gentle and gk == "mines_clash":
        _log_step(
            log,
            "Mines：优先 gweb 几何点 Start（再扫文案）；已禁 TreeWalker 减轻 Canvas 壳竞态",
        )
        h0 = await _k11_start_game_geometry_on_game_iframe(page, log)
        if h0:
            try:
                await page.wait_for_timeout(950)
            except Exception:
                pass
            return
    for _ in range(rounds):
        h = await _try_click_start_game_room_cta(page, log)
        if h:
            try:
                await page.wait_for_timeout(1_000 if react_gentle else 500)
            except Exception:
                pass
            return
        h = await _aggressive_click_visible_text(
            page,
            patterns=start_patterns,
            log=log,
            skip_tree_walker=skip_tw,
            post_click_settle_ms=settle,
            click_force=cforce,
        )
        if h:
            try:
                await page.wait_for_timeout(1000 if react_gentle else 500)
            except Exception:
                pass
            return
        h = await _try_click_in_frames(
            page,
            name_patterns=[
                re.compile(r"Start\s*Game", re.I),
                re.compile(r"^Start$", re.I),
            ],
            log=log,
            settle_after_ms=frame_settle,
            click_force=cforce,
        )
        if h:
            return
        h = await _aggressive_click_visible_text(
            page,
            patterns=deal_patterns,
            log=log,
            skip_tree_walker=skip_tw,
            post_click_settle_ms=settle,
            click_force=cforce,
        )
        if h:
            try:
                await page.wait_for_timeout(950 if react_gentle else 450)
            except Exception:
                pass
            return
        h = await _try_click_in_frames(
            page,
            name_patterns=deal_patterns + generic,
            log=log,
            settle_after_ms=frame_settle,
            click_force=cforce,
        )
        if h:
            return
        try:
            await page.wait_for_timeout(between)
        except Exception:
            pass


async def _apply_game_hint_actions(
    page: Any,
    game_key: str,
    log: Callable[[str], None],
    journal: SmokeExecJournal | None = None,
) -> None:
    g = (game_key or "").lower()
    # 进房后先处理「选币/一局」再 Join；部分局型若晚于预载才出现，此处再点一次
    await _k11_try_select_coins_join_modal(
        page,
        log,
        journal,
        phase="轻量前",
        attempts=2,
        between_ms=450,
    )
    # 通用：遮罩/入桌
    generic = [
        re.compile(r"^Join$", re.I),
        re.compile(r"^OK$", re.I),
        re.compile(r"^Confirm$", re.I),
        re.compile(r"^Continue$", re.I),
        re.compile(r"^Got it$", re.I),
        re.compile(r"^好的$"),
        re.compile(r"^确定$"),
    ]
    if g in ("texas_holdem", "texas_holdem_plus"):
        _log_step(
            log,
            f"本游戏「{game_key}」：尝试在发牌后点 Fold 以快速结束本手",
        )
        folds = [
            re.compile(r"^Fold$", re.I),
            re.compile(r"^FOLD$"),
            re.compile(r"^fold$"),
        ]
        for _ in range(8):
            n = await _try_click_in_frames(page, name_patterns=folds, log=log)
            if n:
                return
            await _try_click_in_frames(page, name_patterns=generic, log=log)
            await page.wait_for_timeout(900)
        return
    if g in (
        "unleash_running",
        "pinoy_monopoly",
        "crazy_solitaire",
        "mines_clash",
    ):
        if g in ("unleash_running", "pinoy_monopoly"):
            _log_step(log, f"本游戏「{game_key}」：Start Game / 通用（get_by_text+TreeWalker+role）")
        else:
            _log_step(
                log,
                f"本游戏「{game_key}」：Start Game / 下注/开始（get_by_text+TreeWalker+role）",
            )
        await _orchestrate_start_deal_and_generic(
            page, game_key=game_key, log=log
        )
        if g == "pinoy_monopoly":
            if await _page_looks_like_react_error(page):
                await _soft_recover_react_error_page(page, log)
        return
    # 非 Start-Deal 系列：自动对局，只点通用
    for _ in range(6):
        await _try_click_in_frames(page, name_patterns=generic, log=log)
        await page.wait_for_timeout(500)


@dataclass
class GameResult:
    game: str
    p0_runnable: str
    p0_coin: str
    initial_gold: float | None
    final_gold: float | None
    entry_ok: bool
    back_to_home: bool
    wait_sec: float
    early_exit: str
    error: str
    initial_note: str
    final_note: str
    entry_signal: str = ""
    smoke_note: str = ""


async def _run_one_game(
    page: Any,
    *,
    p0: Any,
    game: str,
    home: str,
    target_url: str,
    entry_timeout_sec: float,
    pre_wait_sec: float,
    play_cap_sec: float | None,
    snap_dir: Path | None,
    log: Callable[[str], None],
    journal: SmokeExecJournal | None = None,
) -> GameResult:
    err_out = ""
    entry_ok = False
    back_to_home = False
    t_wait = 0.0
    early = ""
    entry_signal = ""
    smoke_note = ""
    ini: float | None = None
    fin: float | None = None
    ini_n = fin_n = ""
    (
        _diagnose,
        _deep,
        _goto,
        _prepare,
    ) = _mcp_imports()
    try:
        if journal:
            journal.phase(f"单款开始: {game}", "采场景、初始金币、进桌/探针/回厅")
            await journal.snapshot_page(page, "本局开始")
        scen = _scenario_for_game(game)
        scen["start_url"] = home
        k11_sel = _k11_lobby_click_selector_string(game)
        if k11_sel:
            prev_sel = str(scen.get("click_selector") or "")
            scen["click_selector"] = k11_sel
            _log_step(
                log,
                f"K11: 使用大厅网格标题格选择器: {k11_sel!r}（原 MCP: {prev_sel!r}）",
            )
        click_sel = str(scen.get("click_selector") or "").strip()
        deep_ms = int(scen.get("timeout_ms") or 90_000)
        w = await _snapshot_lobby_wallet(page)
        ini, ini_n = _first_number_from_hints(w)
        if journal:
            journal.situation(
                f"场景已加载: click_selector={click_sel!r} deep_ms={deep_ms} "
                f"document_game_id={scen.get('document_game_id')!r}"
            )
        _log_step(log, f"记录初始金币 initial_gold={ini}（{ini_n}）")
        if not click_sel:
            if journal:
                journal.problem("缺少 click_selector，本款立即失败")
            return GameResult(
                game,
                "FAIL",
                "SKIP",
                ini,
                None,
                False,
                False,
                0.0,
                "",
                "缺少 click_selector",
                ini_n,
                "",
                "",
                "",
            )
        exp_gid: int | None = None
        try:
            if scen.get("document_game_id") is not None:
                exp_gid = int(scen["document_game_id"])
        except (TypeError, ValueError):
            exp_gid = None
        skip_lobby_flow = False
        if exp_gid is not None:
            try:
                url_snips: list[str] = []
                try:
                    url_snips.append(page.url or "")
                except Exception:
                    pass
                for fr in page.frames or []:
                    try:
                        url_snips.append(str(getattr(fr, "url", "") or ""))
                    except Exception:
                        pass
                gid_m = None
                for ux in url_snips:
                    m1 = re.search(
                        r"(?:\?|&)(?:gameId|game_id)=(\d+)", str(ux), re.I
                    )
                    if m1:
                        gid_m = m1
                        break
                if not gid_m:
                    for ux in url_snips:
                        m1 = re.search(
                            r"game_id%3D(\d+)", str(ux), re.I
                        )
                        if m1:
                            gid_m = m1
                            break
                if gid_m and int(gid_m.group(1)) == exp_gid:
                    wok, wsig = await _wait_entry_shell(
                        page, timeout_sec=6.0, log=log
                    )
                    if wok:
                        if journal:
                            journal.situation(
                                f"预检命中: 已在 gameId={exp_gid} 且进壳 signal={wsig!r}，跳过大厅流程"
                            )
                        _log_step(
                            log,
                            f"状态预检：URL(s) 已含 gameId={exp_gid} 且进壳"
                            f"（{str(wsig)[:100]}），跳过大厅 goto/清场/入口与深等",
                        )
                        skip_lobby_flow = True
                        entry_signal = wsig
            except Exception:
                pass
        err_click: str | None = None
        if not skip_lobby_flow:
            if journal:
                journal.phase("大厅进游戏", "goto home → prepare → MCP 点卡 → deep_wait → 进壳探测")
            _log_step(log, f"从大厅进入「{game}」…")
            try:
                await _goto(
                    page, home, str(scen.get("entry_wait_until") or "load"), 60_000
                )
            except Exception as e:
                err_out = f"goto: {e}"[:200]
                if journal:
                    journal.exception("goto_resilient(home)", e)
                raise
            await _prepare(page, progress=lambda m: log(f"  {m}"))

            await _k11_ensure_lobby_home_all_and_scroll_game(
                page, game, log, journal=journal
            )

            t_click = time.perf_counter()
            try:
                if journal:
                    journal.situation(
                        "即将调用 MCP 入口点击（已做 K11 Home/All/预滚，与网格选择器）"
                    )
                await _diagnose(
                    page,
                    click_selector=click_sel,
                    click_timeout_ms=int(scen.get("click_timeout_ms") or 10_000),
                    scenario_name=game,
                    scenario=scen,
                    progress=lambda m: log(f"  {m}"),
                )
            except Exception as e:
                err_click = str(e)[:300]
                if journal:
                    journal.problem(f"入口点击抛错（仍继续深等/壳检测）: {err_click[:280]}")
                log(
                    f"  [warn] 入口点击异常（继续以壳检测为准）: {err_click[:120]}"
                )
            await _k11_try_select_coins_join_modal(
                page,
                log,
                journal,
                phase="点卡后",
                attempts=2,
                between_ms=400,
            )
            ws_times: list[float] = []
            try:
                await _deep(
                    page, t_click, max(60_000, deep_ms), ws_times, click_flow=True
                )
            except Exception as e:
                if not err_click:
                    err_click = f"deep: {e}"[:200]
                if journal:
                    journal.problem(f"_game_deep_wait_after_goto: {e!s}"[:400])
            await _k11_try_select_coins_join_modal(
                page,
                log,
                journal,
                phase="深等后",
                attempts=3,
                between_ms=500,
            )
            entry_ok, entry_signal = await _wait_entry_shell(
                page, timeout_sec=entry_timeout_sec, log=log
            )
            if journal:
                journal.situation(
                    f"进壳探测(首段): entry_ok={entry_ok} entry_signal={entry_signal!r}"
                )
                await journal.snapshot_page(page, "进壳探测后")
            if not entry_ok:
                ok2, sig2 = await _entry_last_chance_reconfirm(page, log)
                if ok2:
                    entry_ok = True
                    entry_signal = sig2
                    err_out = (err_out + "; 进壳由补检确认").strip("; ")[:500]
                    if journal:
                        journal.situation(f"进壳补检成功: {sig2!r}")
            if not entry_ok:
                err_out = (err_out + "; 未在时限内确认进壳").strip("; ")[:500]
                err_out = (
                    err_out + "\n[诊断·frames]\n" + _format_frame_url_diag(page)
                )[:1800]
                if journal:
                    journal.problem(
                        "未在时限内确认进壳；已附 frame 诊断到 err_out"
                    )
                    journal.line(_format_frame_url_diag(page)[:1200], level="STATE")
            if entry_ok and err_click:
                _log_step(log, "进壳已确认，忽略入口层点击异常")
                if journal:
                    journal.situation("进壳已确认，忽略入口层点击异常")
        else:
            entry_ok = True
            if not entry_signal:
                entry_signal = "skip_lobby_precheck"
            if journal:
                journal.situation(f"跳过大厅流程（预检） entry_signal={entry_signal!r}")
        t_pre = time.monotonic()
        _log_step(
            log,
            f"预载等待 {pre_wait_sec:.0f}s（游戏/资源）…",
        )
        if journal:
            journal.phase("预载与轻量 UI", f"pre_wait_sec={pre_wait_sec}")
        await page.wait_for_timeout(int(max(0, pre_wait_sec) * 1000))
        _log_step(log, f"开始「{game}」针对性轻量操作…")
        await _apply_game_hint_actions(page, game, log, journal)
        dur = float(GAME_DURATION_SEC.get(game, 90))
        cap0 = play_cap_sec
        if cap0 is None or cap0 <= 0:
            cap0 = float(os.environ.get("K11_SM_PLAY_CAP", "55"))
        play_budget = max(8.0, min(dur, cap0))
        if not entry_ok:
            nsec = float(os.environ.get("K11_SM_NO_ENTRY_PLAY", "18"))
            play_budget = min(play_budget, max(8.0, nsec))
            smoke_note = f"进壳未确认:游玩缩至{play_budget:.0f}s"
            _log_step(
                log,
                f"进壳未确认: 轻测将游玩/轮询缩至 {play_budget:.0f}s 后回厅（见 smoke_note）",
            )
        _log_step(
            log,
            f"智能等待 上限≈{play_budget:.0f}s（{dur:.0f}s 与 play_cap 取小），"
            f"每 {poll:.0f}s 探针：jachinRoundEnded / mines 结算文案；主·子 frame URL 无变会累计 stall",
        )
        t_end = time.monotonic() + play_budget
        poll = 5.0
        stall_n = int(os.environ.get("K11_SM_STALL_POLLS", "6"))
        if journal:
            journal.phase(
                "游玩窗口轮询",
                f"budget≈{play_budget:.0f}s poll={poll}s stall_n={stall_n}",
            )
        last_key: str | None = None
        stable = 0
        poll_i = 0
        t_play_phase = time.monotonic()
        while time.monotonic() < t_end:
            t_wait = time.monotonic() - t_pre
            phase_elapsed = time.monotonic() - t_play_phase
            wflag = await _probe_play_phase_round_ended(
                page, game, phase_elapsed_sec=phase_elapsed
            )
            if wflag:
                if wflag.startswith("mines_dom:"):
                    early = f"dom:{wflag}"
                else:
                    early = f"window:{wflag}"
                _log_step(log, f"探针早退: {early}")
                if journal:
                    journal.situation(f"轮询早退(玩法探针): {early}")
                break
            try:
                u = page.url or ""
            except Exception:
                u = ""
            if _looks_like_lobby_url(u, home):
                early = "url:returned-lobby-like"
                _log_step(log, "主文档已像大厅，提前结束等待")
                if journal:
                    journal.situation("轮询早退: 主文档已像大厅 URL")
                break
            prog_key = _stall_progress_key(page)
            if last_key is not None and prog_key == last_key and entry_ok:
                stable += 1
            else:
                stable = 0
            last_key = prog_key
            if (
                entry_ok
                and stable >= stall_n
                and not wflag
                and not _looks_like_lobby_url(u, home)
            ):
                early = f"bail:stall_{int(stable * poll)}s_same_url"
                smoke_note = (
                    f"{(smoke_note + ';') if smoke_note else ''}{early}"
                )[:500]
                _log_step(
                    log,
                    f"无进展早退: {early}（轻测不长期空等，见 smoke_note）",
                )
                if journal:
                    journal.problem(f"无进展 bail: {early} smoke_note={smoke_note!r}")
                break
            poll_i += 1
            if journal and poll_i % 3 == 1:
                journal.line(
                    f"轮询#{poll_i} t_wait≈{t_wait:.1f}s url={u[:200]!r} stable={stable}",
                    level="STATE",
                )
            await page.wait_for_timeout(int(poll * 1000))
        t_wait = time.monotonic() - t_pre
        if journal:
            journal.situation(
                f"轮询结束: early={early!r} t_wait≈{t_wait:.1f}s smoke_note={smoke_note!r}"
            )
            await journal.snapshot_page(page, "轮询结束后")
        _log_step(log, f"回大厅 home_url 以采最终金币…")
        try:
            await page.goto(home, wait_until="domcontentloaded", timeout=55_000)
            try:
                await p0._ensure_on_home_feed(page, target_url, log)  # type: ignore
            except Exception:
                pass
            await page.wait_for_timeout(900)
            back_to_home = True
            if journal:
                journal.situation("已执行 goto home + ensure_on_home_feed，视为回厅成功")
        except Exception as e:
            err_out = (err_out + f"; goto home: {e!s}").strip("; ")[:500]
            if journal:
                journal.exception("回大厅 goto/ensure", e)
        w2 = await _snapshot_lobby_wallet(page)
        fin, fin_n = _first_number_from_hints(w2)
        _log_step(log, f"记录最终金币 final_gold={fin}（{fin_n}）")
        if journal:
            journal.phase(
                "单款收尾",
                f"runnable={('PASS' if (entry_ok and back_to_home) else 'FAIL')} "
                f"coin侧需结合 ini/fin",
            )
    except Exception as e:
        err_out = (str(err_out) + str(e))[:500] if str(e) else err_out
        if journal:
            journal.exception(f"单款未处理异常 game={game}", e)
        if snap_dir:
            try:
                snap_dir.mkdir(parents=True, exist_ok=True)
                p = snap_dir / f"fail_{game}_{int(time.time())}.png"
                await page.screenshot(path=str(p), full_page=True)
                _log_step(log, f"已截图: {p}")
            except Exception as se:
                log(f"  [warn] 截图失败: {se!s}"[:200])
        try:
            _log_step(log, f"当前 URL: {(page.url or '')[:400]}")
        except Exception:
            pass
    # PASS 准则：结果导向 — 进壳 + 能回大厅采数（不要求点击回调成功）
    runnable = "PASS" if (entry_ok and back_to_home) else "FAIL"
    coin = "SKIP"
    if isinstance(ini, (int, float)) and isinstance(fin, (int, float)):
        if fin != ini:
            coin = "PASS"
        else:
            coin = "FAIL"
    elif ini is None and fin is None:
        coin = "SKIP"
    else:
        coin = "SKIP"
    gr = GameResult(
        game,
        runnable,
        coin,
        ini,
        fin,
        entry_ok,
        back_to_home,
        t_wait,
        early,
        err_out,
        ini_n,
        fin_n,
        entry_signal,
        smoke_note,
    )
    if journal:
        journal.line(
            f"单款结果摘要: {asdict(gr)}",
            level="META",
        )
    return gr


def _brief_exc(e: BaseException) -> str:
    return f"{type(e).__name__}: {e!s}"[:200]


def _resolve_k11_lark_smoke_report_path() -> Path:
    u = Path(__file__).resolve().parent / "k11_lark_smoke_report.py"
    if u.is_file():
        return u
    return ROOT / "scripts" / "k11_lark_smoke_report.py"


def _lark_log_line(msg: str) -> None:
    """飞书步骤默认走 stderr，避免与 ``--quiet`` 下 stdout 日志混淆。"""
    print(msg, file=sys.stderr, flush=True)


def _lark_rows_from_state_machine_results(
    results: list[GameResult],
    *,
    total_ok: bool,
    coin_label: str,
    p0_runnable: bool,
) -> list[dict[str, Any]]:
    """与 ``k11_lark_smoke_report.send_k11_smoke_lark_notification`` 所需行结构一致。"""
    n_game = len(results)
    top: dict[str, Any] = {
        "tier": "K11",
        "case_title_zh": "【整体验收】状态机轻测·可运行+金币",
        "verdict": "PASS" if total_ok else "FAIL",
        "verdict_zh": "通过" if total_ok else "未通过",
        "detail": (
            f"各局可运行全过={p0_runnable}；金币汇总={coin_label}；本跑 {n_game} 款"
        )[:500],
    }
    out: list[dict[str, Any]] = [top]
    for r in results:
        title = _GAME_LOBBY_DISPLAY.get(r.game, r.game)
        case_title_zh = f"状态机·{title}"
        if r.p0_runnable != "PASS":
            verdict, vzh = "FAIL", "未通过（进壳/回厅）"
        elif r.p0_coin == "FAIL":
            verdict, vzh = "FAIL", "未通过（金币无变化）"
        elif r.p0_coin == "PASS":
            verdict, vzh = "PASS", "通过"
        else:
            verdict, vzh = "SKIP", "跳过（金币未解析）"
        parts: list[str] = []
        if (r.error or "").strip():
            parts.append(f"err={str(r.error)[:200]}")
        if (r.smoke_note or "").strip():
            parts.append(f"note={str(r.smoke_note)[:120]}")
        if (r.entry_signal or "").strip():
            parts.append(f"entry={str(r.entry_signal)[:80]}")
        detail = "；".join(parts) if parts else "—"
        out.append(
            {
                "tier": "K11",
                "case_title_zh": case_title_zh,
                "verdict": verdict,
                "verdict_zh": vzh,
                "detail": detail[:500],
            }
        )
    return out


def _send_state_machine_lark_notification(
    *,
    results: list[GameResult],
    target_url: str,
    wiki_url: str,
    total_ok: bool,
    coin_label: str,
    p0_runnable: bool,
    app_id: str,
    app_secret: str,
    chat_id: str,
) -> None:
    """仅发飞书消息卡片（与统合脚本同 ``send_k11_smoke_lark_notification``），不同步表格。"""
    p = _resolve_k11_lark_smoke_report_path()
    if not p.is_file():
        _lark_log_line(
            f"  [lark] 未找到 {p.name}，跳过飞书；请将脚本置于仓库 scripts/ 或打包同目录"
        )
        return
    spec = importlib.util.spec_from_file_location("k11_lark_smoke_report", p)
    if spec is None or spec.loader is None:
        return
    k11_lark: Any = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(k11_lark)
    except Exception as e:
        _lark_log_line(f"  [lark] 加载 k11_lark_smoke_report 失败：{e!s}"[:400])
        return
    send = getattr(k11_lark, "send_k11_smoke_lark_notification", None)
    if not callable(send):
        return
    try:
        from l3_node.packaged_lark_env import apply_packaged_lark_to_os_environ

        apply_packaged_lark_to_os_environ()
    except Exception:
        pass
    lark_rows = _lark_rows_from_state_machine_results(
        results,
        total_ok=total_ok,
        coin_label=coin_label,
        p0_runnable=p0_runnable,
    )
    _lark_log_line("")
    _lark_log_line("—— 飞书：完成通知（状态机；未写多维表，lark_wrote=0）——")
    try:
        send(
            results=lark_rows,
            target_url=target_url,
            wiki_url=wiki_url,
            lark_wrote=0,
            app_id=app_id,
            app_secret=app_secret,
            chat_id=chat_id,
            log=_lark_log_line,
        )
    except Exception as e:
        _lark_log_line(
            f"  [lark] 发消息异常（不阻塞退出码）：{type(e).__name__}: {e!s}"[:480]
        )


async def _async_main(args: argparse.Namespace) -> int:
    journal: SmokeExecJournal | None = _open_smoke_journal(args)
    _journal_log_env_snippet(journal)
    if journal:
        journal.phase("脚本入口", "_async_main 已启动，即将加载 Playwright / P0")
    return_code: list[int] = [1]

    def _set_rc(c: int) -> None:
        return_code[0] = c

    def _log_to_journal(msg: str) -> None:
        if not journal:
            return
        for raw in (msg.splitlines() or [msg]):
            journal.line(raw, level="INFO")

    try:
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:
            if journal:
                journal.exception("依赖缺失: playwright 未安装或不可用", e)
                journal.problem("请执行: pip install playwright && playwright install chromium")
            print("pip install playwright && playwright install chromium", file=sys.stderr)
            _set_rc(2)
            return 2
        p0 = _load_p0()
        cdp = p0._kalaroko_cdp(args.cdp_http or None)
        target = (args.target_url or DEFAULT_TARGET).strip()
        home = p0._home_feed_url(target)
        host = p0._host_from_url(target)
        snap = Path(args.screenshot_dir) if args.screenshot_dir else None

        games = [args.game] if args.single else _GAME_ORDER
        for g in games:
            if g not in _GAME_ORDER:
                if journal:
                    journal.problem(f"未知 --game {g!r}，在 _GAME_ORDER 中无此键")
                print(f"[失败] 未知 --game {g!r}", file=sys.stderr)
                _set_rc(2)
                return 2

        out_path = Path(args.json_out) if args.json_out else None
        if out_path:
            out_path.parent.mkdir(parents=True, exist_ok=True)

        verbose = bool(getattr(args, "verbose", False))

        def log(msg: str) -> None:
            if (not args.quiet) or verbose:
                print(msg, flush=True)
            _log_to_journal(msg)

        if journal:
            journal.phase("参数与目标", f"cdp={cdp!r} home={home!r} target={target!r}")
            journal.situation(
                f"单测={args.single} require_existing_tab={getattr(args, 'require_existing_tab', False)} "
                f"游戏列表条数={len(games)}"
            )

        log("======== K11 状态机轻量测 · 6 款（P0 可运行 + 金币）========")
        log(f"CDP={cdp}  home={home}")
        log(f"游戏: {', '.join(games)}")
        log("")

        if journal:
            journal.phase("CDP/浏览器", "将 connect_over_cdp 并取目标页")

        results: list[GameResult] = []
        try:
            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(cdp)
                page, pick_err = await p0._acquire_cdp_target_page(
                    browser,
                    host=host,
                    target_url=target,
                    navigate_if_no_tab=not getattr(
                        args, "require_existing_tab", False
                    ),
                    log=log,
                )
                if page is None:
                    if journal:
                        journal.problem(f"无法取页: {pick_err!r}")
                    log(f"无法取页: {pick_err}")
                    _set_rc(2)
                    return 2
                ok, detail = await p0._ensure_target_page(
                    page,
                    target,
                    log=log,
                    navigate_if_no_tab=not getattr(
                        args, "require_existing_tab", False
                    ),
                    host=host,
                )
                if not ok:
                    if journal:
                        journal.problem(f"_ensure_target_page 未就绪: {detail!r}")
                    log(detail)
                    _set_rc(2)
                    return 2
                if journal:
                    journal.situation("已连接 CDP 且目标页 _ensure_target_page 通过")
                try:
                    await p0._ensure_on_home_feed(page, target, log)
                except Exception as e:
                    if journal:
                        journal.problem(
                            f"_ensure_on_home_feed 首遍异常(继续): {_brief_exc(e)}"
                        )
                    log(_brief_exc(e))
                for gi, game in enumerate(games):
                    if journal:
                        journal.phase(
                            f"第 {gi + 1}/{len(games)} 款: {game}",
                            "开始 _run_one_game",
                        )
                    log(f"—— [{gi+1}/{len(games)}] {game} ——")
                    try:
                        r = await _run_one_game(
                            page,
                            p0=p0,
                            game=game,
                            home=home,
                            target_url=target,
                            entry_timeout_sec=float(
                                args.entry_wait_sec
                                or os.environ.get("K11_SM_ENTRY_TIMEOUT", "90")
                            ),
                            pre_wait_sec=float(
                                args.pre_wait_sec
                                or os.environ.get("K11_SM_PRE_WAIT", "10")
                            ),
                            play_cap_sec=args.play_wait_sec,
                            snap_dir=snap,
                            log=log,
                            journal=journal,
                        )
                    except Exception as e:
                        if journal:
                            journal.exception(f"单局未捕获(将生成占位 GameResult) game={game}", e)
                        log(f"[错误] 单局未捕获: {_brief_exc(e)}")
                        if snap:
                            try:
                                sn = snap / f"crash_{game}_{int(time.time())}.png"
                                sn.parent.mkdir(parents=True, exist_ok=True)
                                await page.screenshot(path=str(sn), full_page=True)
                            except Exception as se:
                                if journal:
                                    journal.problem(
                                        f"单局异常后附加截图失败: {se!s}"[:200]
                                    )
                        r = GameResult(
                            game,
                            "FAIL",
                            "SKIP",
                            None,
                            None,
                            False,
                            False,
                            0.0,
                            "",
                            _brief_exc(e),
                            "",
                            "",
                            "",
                            "",
                        )
                    results.append(r)
                    m = (
                        f"  → 可运行: {r.p0_runnable}  金币: {r.p0_coin}  "
                        f"进场: {r.entry_ok}  回厅: {r.back_to_home}"
                    )
                    if getattr(r, "entry_signal", ""):
                        m += f"  进壳信号={str(r.entry_signal)[:80]!r}"
                    if getattr(r, "smoke_note", ""):
                        m += f"  备注={str(r.smoke_note)[:120]!r}"
                    if r.error:
                        m += f"  err={r.error[:120]!r}"
                    log(m)
                try:
                    await p0._ensure_on_home_feed(page, target, log)
                    await page.goto(
                        home, wait_until="domcontentloaded", timeout=50_000
                    )
                except Exception as e:
                    if journal:
                        journal.problem(
                            f"收尾 _ensure + goto home 可忽略: {_brief_exc(e)}"
                        )
        except Exception as e:
            if journal:
                journal.exception("async 主流程(连接/多局)致命异常", e)
            log(f"致命: {_brief_exc(e)}")
            _set_rc(3)
            return 3

        p0_runnable = all(x.p0_runnable == "PASS" for x in results)
        has_coin_pass = any(x.p0_coin == "PASS" for x in results)
        has_coin_fail = any(x.p0_coin == "FAIL" for x in results)
        if has_coin_fail:
            coin_label = "FAIL"
        elif has_coin_pass:
            coin_label = "PASS"
        else:
            coin_label = "SKIP"
        if journal:
            journal.phase(
                "全案汇总",
                f"p0_runnable={'PASS' if p0_runnable else 'FAIL'} "
                f"coin_label={coin_label} games_n={len(results)}",
            )
        log("")
        log("======== 汇总（对应 K11 行 42-43）========")
        log(
            f"  各游戏正常运行（进壳+等待+回大厅）: "
            f"{'PASS' if p0_runnable else 'FAIL'}"
        )
        log(
            f"  游戏金币同步（有变化且可解析为 PASS，无变化为 FAIL，缺数为 SKIP）: {coin_label}"
        )
        for r in results:
            log(
                f"    {r.game:22} run={r.p0_runnable:4} gold={r.p0_coin:4}  "
                f"Δ=({r.initial_gold}→{r.final_gold})"
            )
        # 全体：可运行全过 + 金币侧无「明确无变化」失败（全 SKIP 仍算可接受粗测）
        total_ok = p0_runnable and not has_coin_fail
        if out_path:
            doc = {
                "schema": "k11_smoke_state_machine/v2",
                "ts_utc": datetime.now(timezone.utc).isoformat(),
                "cdp": cdp,
                "target_url": target,
                "p0": {
                    "p0_all_games_normal": "PASS" if p0_runnable else "FAIL",
                    "p0_coin_sync": coin_label,
                    "total": "PASS" if total_ok else "FAIL",
                },
                "summary_note": "coin=SKIP 表示多数局未能解析数字，仅可运行性可作参考",
                "games": [asdict(x) for x in results],
            }
            out_path.write_text(
                json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            log(f"JSON: {out_path.resolve()}")
        out = 0 if total_ok else 1
        _set_rc(out)
        if journal:
            journal.header_block(
                [
                    "本次运行结束",
                    f"return_code={out}  total_ok={total_ok!r}  p0_runnable={p0_runnable!r}",
                    f"coin_label={coin_label}  输出 JSON={repr(str(out_path.resolve())) if out_path else '无'}",
                ]
            )
        if not getattr(args, "no_lark_report", False) and results:
            _wiki_l = (
                (getattr(args, "lark_wiki_url", None) or "").strip()
                or (os.environ.get("K11_SMOKE_LARK_WIKI_URL") or "").strip()
                or K11_DEFAULT_LARK_WIKI_URL
            )
            _send_state_machine_lark_notification(
                results=results,
                target_url=target,
                wiki_url=_wiki_l,
                total_ok=total_ok,
                coin_label=coin_label,
                p0_runnable=p0_runnable,
                app_id=(os.environ.get("K11_SMOKE_LARK_APP_ID") or "").strip(),
                app_secret=(os.environ.get("K11_SMOKE_LARK_APP_SECRET") or "").strip(),
                chat_id=(os.environ.get("K11_SMOKE_LARK_NOTIFY_CHAT_ID") or "").strip(),
            )
        return out
    except Exception as e:
        if journal:
            journal.exception("外层 _async_main 未预期异常", e)
        _set_rc(3)
        return 3
    finally:
        if journal:
            try:
                journal.line(
                    f"进程退出(期望 return_code={return_code[0]}) 见上层主进程。",
                    level="META",
                )
            except Exception:
                pass
            journal.close()


def main() -> int:
    ap = argparse.ArgumentParser(
        description="K11 状态机轻量 6 款游戏可运行 + 金币（非旧版 P0 长脚本）"
    )
    ap.add_argument("--target-url", default=DEFAULT_TARGET)
    ap.add_argument("--cdp-http", default="")
    ap.add_argument(
        "--game",
        default="texas_holdem",
        choices=_GAME_ORDER,
    )
    ap.add_argument("--single", action="store_true", help="只跑 --game 一款")
    ap.add_argument("--require-existing-tab", action="store_true")
    ap.add_argument("--json-out", default="", help="结果 JSON 路径")
    ap.add_argument(
        "--screenshot-dir",
        default="",
        help="失败/异常时截图目录，默认不保存",
    )
    ap.add_argument(
        "--entry-wait-sec",
        type=float,
        default=None,
        help="进壳（URL/iframe）最长等待，默认 90 或 K11_SM_ENTRY_TIMEOUT",
    )
    ap.add_argument(
        "--pre-wait-sec",
        type=float,
        default=None,
        help="进壳后预载秒数，默认 10 或 K11_SM_PRE_WAIT",
    )
    ap.add_argument(
        "--play-wait-sec",
        type=float,
        default=None,
        help="单款「游玩+轮询」硬上限（秒，与 K11_SM_PLAY_CAP 取小）；默认 55 或仅环境",
    )
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument(
        "--no-exec-journal",
        action="store_true",
        help="不写详细执行流水日志（默认写 K11_SMOKE_EXEC_LOG_DIR 或 .jachin/.../冒烟测试）",
    )
    ap.add_argument(
        "--no-lark-report",
        action="store_true",
        help="不发送飞书完成通知（默认发，与统合脚本一致；本脚本仅消息卡片，不同步表格）",
    )
    ap.add_argument(
        "--lark-wiki-url",
        default="",
        help="飞书 Wiki 链接（卡片摘要中；默认 K11_SMOKE_LARK_WIKI_URL 或内置 K11_DEFAULT_LARK_WIKI_URL）",
    )
    ap.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="更详细输出（L3 控制台「详细日志」/ query verbose=1 会传 -v，与统合冒烟子进程一致）",
    )
    return asyncio.run(_async_main(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
