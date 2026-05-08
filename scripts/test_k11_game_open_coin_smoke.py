#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K11 三款自玩游戏 · 开门 + 一局窗口 + 大厅金币粗测（仿 ``test_k11_game_open_smoke.py``）

对齐 ``docs/K11_平台冒烟测试用例.md`` P0 行 42～43：
- 各游戏可正常进壳并完成一段自动对局窗口（限时等待，不手搓出牌）；
- 回大厅后解析金币，与进房前对比（双端可解析且数值变化为 PASS，相同为 FAIL，缺数为 SKIP）。

默认游戏：Tongits King、Mines Clash、Bato-Bato Pick。
Tongits 进桌即开，**不**做 Start/Play；局内停表依赖 VICTORY / CONTINUE / DETAILS 等结算文案
（见 ``GameCase.try_soft_start_play`` / ``use_pre_match_lobby_resume``）。

**进场 / 撤离**：与 ``test_k11_game_open_smoke.py`` 完全一致——
``_diagnose_and_click_kalaroko_game_entry`` 的 ``scenario`` 仅为
``{"name": case.game_id, "start_url": TARGET_HOME}``（不附加 ``document_game_id`` 等），
**进壳判定**：在开门脚本的 URL 判据之外，增加主文档 ``iframe`` 的 ``src`` 嗅探与子 ``frame.url``
检测（SPA 主栏 URL 不变、仅内嵌 gweb 时仍可视为已进场）。

用法：
  python scripts/test_k11_game_open_coin_smoke.py
  python scripts/test_k11_game_open_coin_smoke.py --play-wait-sec 75
  python scripts/test_k11_game_open_coin_smoke.py --single-game mines_clash -v

环境：``pip install playwright``；默认与开门脚本相同启动 Chromium（非 CDP）。

进场并行嗅探：``K11_GAME_ENTRY_POLL_MS``（默认 500）控制壳 / 大尺寸 canvas 轮询间隔；
判定见 ``_shell_or_canvas_present``。

局内退出：默认在检测到「一局结束」文案、**同一预备室再次出现**（如 Mines 回到 Start
Game）、**相对锚点稳定的大厅金币变化**（纯 canvas 局内）、或离开 gweb 壳后提前结束等待，
再以战术撤离回站点大厅采金币；``--play-wait-sec`` 为**上限**秒数。可调环境变量：
``K11_ROUND_END_POLL_SEC``、``K11_ROUND_TEXT_MIN_SEC``、``K11_LOBBY_LEAVE_MIN_SEC``、
``K11_MATCH_LOBBY_MIN_ELAPSED``、``K11_MATCH_LOBBY_LEAVE_POLLS``、
``K11_MATCH_LOBBY_RETURN_POLLS``、``K11_WALLET_ANCHOR_AFTER_SEC``、
``K11_WALLET_DELTA_MIN_AFTER_ANCHOR``、``K11_WALLET_SETTLE_POLLS`` 等。

纯 ``#GameCanvas``：``_heatmap_click_game_canvas_start`` 在热区内网格点击并以 **PNG 哈希**比对是否换屏；
``_attach_k11_game_network_sniffers`` 监听 **fetch/xhr JSON** 与 **WS 帧** 中含余额字段的 payload 及粗略回合关键字。
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from l3_client.local_mcps.kalaroko_monitor import mcp_kalaroko_monitor as mcp

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

TARGET_HOME = "https://www.kalaroko.com/"
SCHEMA = "k11_game_open_coin_smoke/v1"
K11_DEFAULT_LARK_WIKI_URL = (
    "https://ssgkm409t6q5.sg.larksuite.com/wiki/ZyWlwhdW1iNQuykvy7qlw93sgTe"
)
os.environ.setdefault(
    "KALAROKO_MONITOR_ALLOWED_HOSTS",
    "kalaroko.com,www.kalaroko.com,gweb.kalaroko.com,gwp.heronpro.xin",
)

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


@dataclass(frozen=True)
class GameCase:
    """与 ``test_k11_game_open_smoke.GameCase`` 字段一致。"""

    game_id: str
    title: str
    click_selector: str
    extra_lobby_click: str | None = None
    #: 进场嗅探：id/class 子串（非空时在各 frame 上做轻量匹配，配合 canvas / 壳 URL）
    surface_dom_substrings: tuple[str, ...] = ()
    #: 进壳后是否尝试点 Start/Play（Tongits 等进桌即开，应 False）
    try_soft_start_play: bool = True
    #: 是否启用「同一 Start/Game 预备室再现」停表（Mines 系 True；Tongits 结算屏不同）
    use_pre_match_lobby_resume: bool = True


GAME_CASES: tuple[GameCase, ...] = (
    GameCase(
        "tongits_king",
        "Tongits King",
        "text=/Tongits\\s*King/i >> xpath=..",
        try_soft_start_play=False,
        use_pre_match_lobby_resume=False,
    ),
    GameCase("mines_clash", "Mines Clash", "text=/Mines\\s*Clash/i >> xpath=.."),
    GameCase(
        "bato_bato_pick",
        "Bato-Bato Pick",
        "text=/Bato-Bato\\s*Pick/i >> xpath=..",
    ),
)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _gprint(msg: str, sink: Callable[[str], None] | None) -> None:
    if sink:
        sink(msg)
    else:
        print(msg, flush=True)


def _pick_cases(single_game: str | None) -> list[GameCase]:
    if not single_game:
        return list(GAME_CASES)
    g = single_game.strip().lower()
    for c in GAME_CASES:
        if c.game_id == g:
            return [c]
    valid = ", ".join(x.game_id for x in GAME_CASES)
    raise ValueError(f"未知 --single-game={single_game!r}，可选: {valid}")


def _is_open_success(url: str) -> bool:
    """主文档 URL 粗判（保留兼容）；SPA 进局可能不变，须配合 ``_page_suggests_in_game``。"""
    u = (url or "").lower()
    if "game-frame" in u or "gameid=" in u or "game_id=" in u:
        return True
    ha = ""
    try:
        frag = (urlparse(url or "").fragment or "").lower()
        ha = frag
    except Exception:
        ha = ""
    if ha and re.search(r"(?:gameid|game_id|room|table|party)=?\d+", ha):
        return True
    return False


def _url_suggests_game_shell(url: str) -> bool:
    """子 frame / 导航 URL 是否像游戏壳（与 K11 状态机嗅探对齐）。"""
    u = (url or "").strip()
    if not u or u.lower().startswith("about:blank"):
        return False
    s = u.lower()
    if "game-frame" in s:
        return True
    if re.search(r"[?&](?:gameid|game_id)=\d+", s, re.I):
        return True
    if s.startswith("http") and "gweb." in s:
        return True
    if "gwp." in s and "game" in s:
        return True
    if "heronpro" in s and "game" in s:
        return True
    return False


_JS_SHELL_SIGNAL = r"""() => {
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


async def _page_suggests_in_game(page: Any) -> bool:
    """多路进壳：主 URL、主文档 iframe 嗅探、各子 frame URL（适应 SPA + 内嵌 gweb）。"""
    try:
        pu = str(page.url or "")
    except Exception:
        pu = ""
    if _is_open_success(pu) or _url_suggests_game_shell(pu):
        return True
    try:
        sig = await page.main_frame.evaluate(_JS_SHELL_SIGNAL)
        if isinstance(sig, str) and sig.strip():
            return True
    except Exception:
        pass
    try:
        for fr in list(getattr(page, "frames", None) or []):
            try:
                fu = str(getattr(fr, "url", None) or "")
            except Exception:
                fu = ""
            if _url_suggests_game_shell(fu):
                return True
    except Exception:
        pass
    return False


_CANVAS_SURFACE_JS = r"""() => {
  for (const c of document.querySelectorAll("canvas")) {
    try {
      const r = c.getBoundingClientRect();
      if (r.width >= 180 && r.height >= 120) return true;
    } catch (e) {}
  }
  return false;
}"""


def _surface_hints_js_payload(hints: tuple[str, ...]) -> str:
    """转义为可在浏览器 evaluate 中嵌入的 JSON 数组字面量。"""
    safe: list[str] = []
    for h in hints[:6]:
        t = str(h).strip()
        if not t:
            continue
        safe.append(t[:48])
    return json.dumps(safe, ensure_ascii=False)


def _surface_dom_hints_eval_js(hints: tuple[str, ...]) -> str:
    """各 frame 内：id/class 是否包含配置的子串（ hints 为空则恒为 false）。"""
    payload = _surface_hints_js_payload(hints)
    return f"""(function() {{
  const hints = {payload};
  if (!hints.length) return false;
  for (const sub of hints) {{
    const L = String(sub).toLowerCase();
    if (!L) continue;
    try {{
      const nodes = document.querySelectorAll("[id],[class]");
      const lim = Math.min(nodes.length, 800);
      for (let i = 0; i < lim; i++) {{
        const el = nodes[i];
        const id = String(el.id || "").toLowerCase();
        const cl = (typeof el.className === "string" ? el.className : "").toLowerCase();
        if (id.includes(L) || cl.includes(L)) return true;
      }}
    }} catch (e) {{}}
  }}
  return false;
}})()"""


async def _large_canvas_present(page: Any) -> bool:
    try:
        frames: list[Any] = [page.main_frame] + [
            f for f in page.frames if f != page.main_frame
        ]
    except Exception:
        try:
            frames = [page.main_frame]
        except Exception:
            return False
    for fr in frames:
        try:
            if await fr.evaluate(_CANVAS_SURFACE_JS):
                return True
        except Exception:
            continue
    return False


async def _surface_dom_hints_match(page: Any, hints: tuple[str, ...]) -> bool:
    if not hints:
        return False
    js = _surface_dom_hints_eval_js(hints)
    try:
        frames: list[Any] = [page.main_frame] + [
            f for f in page.frames if f != page.main_frame
        ]
    except Exception:
        try:
            frames = [page.main_frame]
        except Exception:
            return False
    for fr in frames:
        try:
            if await fr.evaluate(js):
                return True
        except Exception:
            continue
    return False


async def _shell_or_canvas_present(
    page: Any, case: GameCase | None = None
) -> bool:
    """
    进场成功信号：URL/iframe 壳判定，或可见大尺寸 canvas，或（可选）游戏 DOM 子串。
    用于在 Playwright click 超时/遮罩时仍判定“已在局内”。
    """
    if await _page_suggests_in_game(page):
        return True
    if await _large_canvas_present(page):
        return True
    if case and case.surface_dom_substrings:
        if await _surface_dom_hints_match(page, case.surface_dom_substrings):
            return True
    return False


def _entry_probe_interval_sec() -> float:
    raw = (os.environ.get("K11_GAME_ENTRY_POLL_MS") or "500").strip()
    try:
        ms = float(raw)
    except ValueError:
        ms = 500.0
    sec = max(0.05, min(ms / 1000.0, 5.0))
    return sec


async def _race_diagnose_and_surface_probe(
    page: Any,
    *,
    case: GameCase,
    diagnose: Callable[[], Awaitable[None]],
    poll_sec: float | None = None,
) -> tuple[bool, str]:
    """
    与 ``_diagnose_and_click_kalaroko_game_entry`` 并行：每隔约 ``poll_sec`` 嗅探
    ``_shell_or_canvas_present``。探测器先成立则取消 diagnose 并判进场成功；
    diagnose 抛错后若页面已出现壳/canvas，仍判成功。
    """
    interval = float(poll_sec) if poll_sec is not None else _entry_probe_interval_sec()
    interval = max(0.05, min(interval, 5.0))

    async def _runner() -> None:
        await diagnose()

    task = asyncio.create_task(_runner())
    early = False
    try:
        while not task.done():
            try:
                if await _shell_or_canvas_present(page, case):
                    early = True
                    task.cancel()
                    break
            except Exception:
                pass
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        task.cancel()
        raise

    if early:
        try:
            await task
        except BaseException:
            pass
        return True, (
            "entry surface detected during diagnose (shell/url/iframe/canvas; "
            "primary click may have timed out)"
        )

    exc = task.exception()
    if exc is not None:
        try:
            if await _shell_or_canvas_present(page, case):
                return True, (
                    "diagnose failed but entry surface present: "
                    f"{type(exc).__name__}: {str(exc)[:160]}"
                )
        except Exception:
            pass
        return False, f"{type(exc).__name__}: {str(exc)[:200]}"
    return True, ""


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


def _coin_verdict(
    before: float | None, after: float | None
) -> tuple[str, str | None]:
    if isinstance(before, (int, float)) and isinstance(after, (int, float)):
        if after != before:
            return "PASS", None
        return "FAIL", "大厅金币解析相同"
    return "SKIP", "开局或结束未能解析金币"


def _resolve_k11_lark_smoke_report_path() -> Path:
    return ROOT / "scripts" / "k11_lark_smoke_report.py"


def _to_lark_results(per_game: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in per_game:
        ov = str(row.get("open_verdict") or row.get("verdict") or "FAIL").upper()
        cv = str(row.get("coin_verdict") or "SKIP").upper()
        if ov != "PASS":
            v, vzh = "FAIL", "未通过（进壳）"
        elif cv == "FAIL":
            v, vzh = "FAIL", "未通过（金币无变化）"
        elif cv == "PASS":
            v, vzh = "PASS", "通过"
        else:
            v, vzh = "SKIP", "跳过（金币未解析）"
        detail = str(row.get("detail") or "")
        if row.get("coin_before") is not None or row.get("coin_after") is not None:
            detail += (
                f" | 金币 {row.get('coin_before')}→{row.get('coin_after')} ({cv})"
            )
        out.append(
            {
                "tier": "P0",
                "case": str(row.get("game_id") or ""),
                "case_title_zh": str(row.get("game_title") or row.get("game_id") or ""),
                "verdict": v,
                "verdict_zh": vzh,
                "detail": detail[:500],
            }
        )
    return out


def _send_lark_notification(
    *,
    per_game: list[dict[str, Any]],
    target_url: str,
    lark_wiki_url: str,
    log: Any,
) -> None:
    _lark_path = _resolve_k11_lark_smoke_report_path()
    if not _lark_path.is_file():
        log(f"  [lark] 未找到脚本：{_lark_path}，跳过通知。")
        return
    try:
        spec = importlib.util.spec_from_file_location("k11_lark_smoke_report", _lark_path)
        if spec is None or spec.loader is None:
            log("  [lark] 加载器创建失败，跳过通知。")
            return
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:
        log(f"  [lark] 加载 k11_lark_smoke_report 失败：{e}")
        return
    try:
        from l3_node.packaged_lark_env import apply_packaged_lark_to_os_environ

        apply_packaged_lark_to_os_environ()
    except Exception:
        pass

    app_id = (os.environ.get("K11_SMOKE_LARK_APP_ID") or "").strip()
    app_secret = (os.environ.get("K11_SMOKE_LARK_APP_SECRET") or "").strip()
    chat_id = (os.environ.get("K11_SMOKE_LARK_NOTIFY_CHAT_ID") or "").strip()

    try:
        mod.send_k11_smoke_lark_notification(  # type: ignore[attr-defined]
            results=_to_lark_results(per_game),
            target_url=target_url,
            wiki_url=lark_wiki_url,
            lark_wrote=0,
            app_id=app_id,
            app_secret=app_secret,
            chat_id=chat_id,
            log=log,
        )
    except Exception as e:
        log(f"  [lark] 发送通知异常（已忽略）：{e}")


async def _click_entry_with_fallback(
    page: Any,
    case: GameCase,
    *,
    progress: Any,
) -> tuple[bool, str]:
    """
    与 ``test_k11_game_open_smoke._click_entry_with_fallback`` 同步：
    scenario 仅 ``name`` + ``start_url``，无其它键。

    进门诊断与 ``_shell_or_canvas_present`` 并行轮询（默认 500ms，环境变量
    ``K11_GAME_ENTRY_POLL_MS``）：探测先成立则取消仍在进行的 diagnose，不要求
    ``click()`` 成功返回。
    """
    async def _primary() -> None:
        await mcp._diagnose_and_click_kalaroko_game_entry(
            page,
            click_selector=case.click_selector,
            click_timeout_ms=12_000,
            scenario_name=case.title,
            scenario={"name": case.game_id, "start_url": TARGET_HOME},
            progress=progress,
        )

    ok, note = await _race_diagnose_and_surface_probe(page, case=case, diagnose=_primary)
    if ok:
        return True, note

    first_err = note
    title_pat = case.title.replace(" ", r"\s*")
    fallback_selectors = [
        f"text=/{title_pat}/i",
        f"text=/{case.title.split(' ')[0]}.*{case.title.split(' ')[-1]}/i",
    ]
    for step in range(4):
        step_i = step
        try:
            if await _shell_or_canvas_present(page, case):
                return True, f"entered game surface during fallback step {step_i + 1}"
        except Exception:
            pass
        try:
            await page.mouse.wheel(0, 900)
        except Exception:
            pass
        try:
            await page.wait_for_timeout(320)
        except Exception:
            pass
        for sel in fallback_selectors:
            async def _fb(
                s: str = sel, _step: int = step_i
            ) -> None:
                await mcp._diagnose_and_click_kalaroko_game_entry(
                    page,
                    click_selector=s,
                    click_timeout_ms=8_000,
                    scenario_name=f"{case.title}#fallback{_step + 1}",
                    scenario={"name": case.game_id, "start_url": TARGET_HOME},
                    progress=progress,
                )

            ok_fb, note_fb = await _race_diagnose_and_surface_probe(
                page, case=case, diagnose=_fb
            )
            if ok_fb:
                return True, (note_fb or f"fallback selector={sel}")
    return False, first_err


async def _soft_click_start_or_play(page: Any, *, total_budget_sec: float = 18.0) -> tuple[bool, str]:
    """
    开局「Start / Play / Start Game」等：多 frame、role 语义与文案正则，尽量贴近 Mines 等「Start Game」大按钮。

    若 UI 为纯 ``canvas`` 绘制（DOM 无文案），最后使用 **canvas 盒内比例 + 视口比例** 的 ``mouse.click`` 兜底。
    """

    async def _coordinate_fallback_start_game() -> tuple[bool, str]:
        """纯 canvas：热区网格 + PNG 哈希；失败再视口竖条兜底。"""
        ok_h, note_h = await _heatmap_click_game_canvas_start(
            page, deadline=deadline
        )
        if ok_h:
            return True, note_h
        pts: list[tuple[float, float, str]] = []
        try:
            vs = page.viewport_size
            vw = float(vs["width"]) if vs else 390.0
            vh = float(vs["height"]) if vs else 844.0
        except Exception:
            vw, vh = 390.0, 844.0
        for y_ratio in (0.74, 0.78, 0.70, 0.82, 0.86, 0.65, 0.68):
            pts.append((vw * 0.5, vh * y_ratio, f"viewport y≈{y_ratio:.0%}"))
        taps_ok = 0
        last_tag = ""
        for cx, cy, tag in pts[:14]:
            if time.perf_counter() >= deadline:
                break
            try:
                await page.mouse.click(float(cx), float(cy), delay=35)
                taps_ok += 1
                last_tag = tag
                await page.wait_for_timeout(220)
            except Exception:
                continue
        if taps_ok <= 0:
            return False, ""
        return True, (
            f"viewport_fallback ({taps_ok} 次 {last_tag} 等；无 GameCanvas 像素佐证)"
        )

    deadline = time.perf_counter() + max(1.0, float(total_budget_sec))
    try:
        frames_fwd: list[Any] = [page.main_frame] + [
            f for f in page.frames if f != page.main_frame
        ]
    except Exception:
        frames_fwd = [page.main_frame]
    frames: list[Any] = list(frames_fwd)
    try:
        frames_rev = list(reversed(frames_fwd))
        if frames_rev != frames_fwd:
            frames = frames_fwd + frames_rev[1:]
    except Exception:
        pass

    name_pat = re.compile(r"start|play|开始", re.I)
    text_pat = re.compile(r"start\s+game|\bstart\b|\bplay\b|开始游戏|开始", re.I)

    async def _click_nth_visible(
        loc: Any, *, max_nodes: int = 14
    ) -> bool:
        try:
            n = await loc.count()
        except Exception:
            return False
        if n <= 0:
            return False
        for i in range(min(int(n), max_nodes)):
            if time.perf_counter() >= deadline:
                return False
            node = loc.nth(i)
            try:
                left_ms = int(max(400, (deadline - time.perf_counter()) * 1000))
            except Exception:
                left_ms = 1200
            try:
                await node.wait_for(state="attached", timeout=min(800, left_ms))
            except Exception:
                pass
            try:
                vis = await node.is_visible(timeout=min(1200, left_ms))
            except Exception:
                vis = False
            try:
                # force：Mine 类全屏层/canvas 边缘仍常见可点父层
                await node.click(
                    timeout=min(3500, left_ms),
                    force=True,
                    no_wait_after=True,
                )
                return True
            except Exception:
                if vis:
                    try:
                        await node.dispatch_event("click")
                        return True
                    except Exception:
                        pass
                continue
        return False

    for fr in frames:
        if time.perf_counter() >= deadline:
            break
        try:
            loc_btn = fr.get_by_role("button", name=name_pat)
            if await _click_nth_visible(loc_btn):
                return True, "get_by_role(button, name~=start|play|开始)"
        except Exception:
            pass
        try:
            loc_link = fr.get_by_role("link", name=name_pat)
            if await _click_nth_visible(loc_link):
                return True, "get_by_role(link, name~=start|play)"
        except Exception:
            pass
        try:
            loc_txt = fr.get_by_text(text_pat)
            if await _click_nth_visible(loc_txt):
                return True, "get_by_text(~Start Game|Start|Play|开始)"
        except Exception:
            pass

    selectors = (
        "text=/Start\\s+Game/i",
        "text=/^Start\\s+Game$/i",
        "[role='button']:has-text('Start Game')",
        "button:has-text('Start Game')",
        "text=/^(Start|Play|开始游戏|Tap\\s*to\\s*start)$/i",
        "text=/Tap\\s*to\\s*start/i",
        "button:has-text('Start')",
        "button:has-text('Play')",
        '[role="button"]:has-text("Start")',
        '[role="button"]:has-text("Play")',
        "text=/\\b(Start|Play)\\b/i",
    )
    for fr in frames:
        if time.perf_counter() >= deadline:
            break
        for sel in selectors:
            if time.perf_counter() >= deadline:
                break
            try:
                left_ms = int(max(300, (deadline - time.perf_counter()) * 1000))
                loc = fr.locator(sel)
                if await _click_nth_visible(loc):
                    return True, f"locator {sel!r}"
            except Exception:
                continue

    ok_coord, note_coord = await _coordinate_fallback_start_game()
    if ok_coord:
        return True, note_coord
    return False, "未发现可点的 Start/Play/Start Game（DOM 与 canvas 坐标兜底均未触发）"


async def _soft_dismiss_round_overlay(
    page: Any,
    *,
    sink: Callable[[str], None] | None,
    budget_sec: float = 4.5,
) -> None:
    """结算层上的 Continue / 确定 等（Tongits VICTORy 弹层），便于随后战术撤离采币。"""
    deadline = time.perf_counter() + max(0.4, float(budget_sec))
    pat = re.compile(r"continue|next|ok|confirm|确定|关闭|知道了", re.I)
    try:
        frames: list[Any] = [page.main_frame] + [
            f for f in page.frames if f != page.main_frame
        ]
    except Exception:
        frames = [page.main_frame]
    for fr in frames:
        if time.perf_counter() >= deadline:
            break
        for role in ("button", "link"):
            if time.perf_counter() >= deadline:
                break
            try:
                loc = fr.get_by_role(role, name=pat)
            except Exception:
                continue
            try:
                n = await loc.count()
            except Exception:
                continue
            for i in range(min(int(n), 8)):
                try:
                    await loc.nth(i).click(
                        timeout=1400, force=True, no_wait_after=True
                    )
                    _gprint(
                        f"   [结算] 已点 {role}（Continue/确定 类）以关闭弹层",
                        sink,
                    )
                    try:
                        await page.wait_for_timeout(450)
                    except Exception:
                        pass
                    return
                except Exception:
                    continue


async def _soft_click_join_if_present(
    page: Any, *, total_budget_sec: float = 6.0, sink: Callable[[str], None] | None = None
) -> tuple[bool, str]:
    deadline = time.perf_counter() + max(1.0, float(total_budget_sec))
    _gprint(f"   [饱和打击] 启动全频谱侦察，预算: {total_budget_sec}s", sink)

    while time.perf_counter() < deadline:
        try:
            chip_candidates = page.locator(
                "text=/^(100|200|500|1000|2000|5000|10000|20000|50000)$/"
            )
            chip_cnt = await chip_candidates.count()
            for i in range(min(chip_cnt, 8)):
                try:
                    chip = chip_candidates.nth(i)
                    if await chip.is_visible(timeout=120):
                        await chip.click(timeout=500, force=True)
                        await page.wait_for_timeout(120)
                        break
                except Exception:
                    continue

            candidates = [
                page.get_by_role("button", name="Join"),
                page.get_by_text("Join", exact=True),
            ]
            best_btn = None
            best_box = None
            best_area = -1.0
            for group in candidates:
                try:
                    cnt = await group.count()
                except Exception:
                    cnt = 0
                for i in range(min(cnt, 8)):
                    try:
                        node = group.nth(i)
                        if not await node.is_visible(timeout=120):
                            continue
                        box = await node.bounding_box()
                        if not box:
                            continue
                        area = float(box.get("width", 0.0)) * float(box.get("height", 0.0))
                        if area > best_area:
                            best_area = area
                            best_btn = node
                            best_box = box
                    except Exception:
                        continue

            if best_btn and best_box:
                cx = float(best_box["x"]) + float(best_box["width"]) / 2.0
                cy = float(best_box["y"]) + float(best_box["height"]) / 2.0
                _gprint(
                    f"   [饱和打击] 锁定 Join 实体(面积={best_area:.0f}) 坐标 ({cx:.1f}, {cy:.1f})，准备投弹...",
                    sink,
                )

                strike_budget = min(4.2, max(0.6, deadline - time.perf_counter()))
                strike_end = time.perf_counter() + strike_budget

                async def _one_round_strike() -> None:
                    try:
                        await asyncio.wait_for(page.mouse.up(), timeout=0.4)
                    except Exception:
                        pass
                    try:
                        await asyncio.wait_for(
                            best_btn.click(timeout=1800, force=True, no_wait_after=True),
                            timeout=2.2,
                        )
                    except Exception:
                        pass
                    try:
                        await asyncio.wait_for(page.wait_for_timeout(50), timeout=0.6)
                    except Exception:
                        pass
                    try:
                        await asyncio.wait_for(page.mouse.move(cx, cy), timeout=1.0)
                        await asyncio.wait_for(page.mouse.down(), timeout=0.8)
                        await asyncio.wait_for(page.wait_for_timeout(35), timeout=0.5)
                        await asyncio.wait_for(
                            page.mouse.move(cx + 1.0, cy + 1.0), timeout=0.8
                        )
                        await asyncio.wait_for(page.mouse.up(), timeout=0.8)
                    except Exception:
                        try:
                            await asyncio.wait_for(page.mouse.up(), timeout=0.3)
                        except Exception:
                            pass
                    try:
                        await asyncio.wait_for(
                            page.mouse.click(cx, cy, delay=25), timeout=2.0
                        )
                    except Exception:
                        pass
                    try:
                        await asyncio.wait_for(
                            best_btn.evaluate(
                                """(el) => {
                                  ['pointerdown','mousedown','pointerup','mouseup','click'].forEach((evt) => {
                                    try {
                                      el.dispatchEvent(new MouseEvent(evt, {
                                        bubbles: true, cancelable: true, view: window, buttons: 1
                                      }));
                                    } catch (e) {}
                                  });
                                }"""
                            ),
                            timeout=1.2,
                        )
                    except Exception:
                        pass

                for round_i in range(2):
                    if time.perf_counter() >= strike_end:
                        break
                    per_round = min(3.2, max(0.35, strike_end - time.perf_counter()))
                    try:
                        await asyncio.wait_for(_one_round_strike(), timeout=per_round)
                    except asyncio.TimeoutError:
                        _gprint(
                            f"   [饱和打击] 第 {round_i + 1} 轮操作超时（>{per_round:.1f}s），"
                            "放弃本段 CDP 鼠标以免卡死",
                            sink,
                        )
                    try:
                        await asyncio.wait_for(page.wait_for_timeout(180), timeout=0.5)
                    except Exception:
                        pass

                _gprint("   [饱和打击] 双轮打击已完成，观察反馈...", sink)
                try:
                    await asyncio.wait_for(page.wait_for_timeout(800), timeout=1.2)
                except Exception:
                    pass

                url_now = str(page.url or "").lower()
                if "game-frame" in url_now or "gameid=" in url_now:
                    return True, "饱和打击成功：游戏已引导进入框架"
                return True, "饱和打击已执行（页面仍在大厅，交由 deep_wait 后续判定）"
        except Exception:
            pass

        try:
            await page.wait_for_timeout(500)
        except Exception:
            break

    return False, "打击结束，未达成进场条件"


_ROUND_END_TEXT_RE = re.compile(
    r"you\s+('?ve\s+)?won\b|you\s+lost\b|you\s+lose\b|game\s+over\b|"
    r"\bvictory\b|\bdefeat\b|\bwinner\b|\bloser\b|"
    r"play\s+again\b|back\s+to\s+lobby\b|leave\s+table\b|exit\s+table\b|"
    r"table\s+(closed|ended)\b|round\s+(over|ended)\b|"
    r"tap\s+to\s+continue\b|\bcontinue\b|\bdetails\b|\bclaim\b|\bcollect\b|"
    r"胜利|失败|平局|结算|再来一局|返回大厅|离开牌桌|对局结束|本局结束",
    re.I | re.S,
)


def _pre_match_lobby_blob_match(blob: str) -> bool:
    """
    Mines 等：自动玩一局后回到**同一预备界面**（Start Game / VS 1v1 / Cost 500），
    DOM 里常有可采样正文（与纯 canvas 按钮不同）。
    """
    if not blob or len(blob.strip()) < 8:
        return False
    s = blob.lower()
    if re.search(r"start\s+game", s):
        return True
    vs_ok = bool(
        re.search(r"\bvs\b", s)
        and (
            "1v1" in re.sub(r"\s+", "", s)
            or re.search(r"\b1\s*v\s*1\b", s) is not None
        )
    )
    cost_ok = ("cost" in s or "₱" in blob) and re.search(r"\b500\b", s)
    if vs_ok and cost_ok:
        return True
    if "mines clash" in s and cost_ok:
        return True
    return False


def _env_int(name: str, default: int, *, vmin: int, vmax: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        v = int(float(raw))
    except ValueError:
        return default
    return max(vmin, min(vmax, v))


def _balance_like_numbers_from_json(obj: Any, sink: list[float]) -> None:
    """从 JSON 中抽取带「余额语义词根」键下的数字（不含整树遍历以免噪声）。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            lk = str(k).lower()
            if any(
                s in lk
                for s in (
                    "balance",
                    "coin",
                    "gold",
                    "wallet",
                    "money",
                    "chip",
                    "credit",
                    "jcoin",
                    "currency",
                    "score",
                )
            ):
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    fv = float(v)
                    if 0 < fv < 1e15:
                        sink.append(fv)
                elif isinstance(v, str):
                    t = v.replace(",", "").strip()
                    if t and re.match(r"^\d+\.?\d*$", t):
                        try:
                            sink.append(float(t))
                        except ValueError:
                            pass
            _balance_like_numbers_from_json(v, sink)
    elif isinstance(obj, list):
        for x in obj[:400]:
            _balance_like_numbers_from_json(x, sink)


def _attach_k11_game_network_sniffers(
    page: Any,
) -> tuple[dict[str, Any], Callable[[], None]]:
    """
    挂载 fetch/xhr 与 WebSocket 帧的轻量嗅探：记录疑似余额字段、回合/结算关键字时刻。
    返回 ``(state, detach)``；务必在 ``finally`` 里调用 ``detach()``。
    """
    state: dict[str, Any] = {
        "balance_events": [],
        "ws_snip": [],
        "last_round_signal_ts": 0.0,
    }

    def _record_balance(v: float, src: str) -> None:
        try:
            fv = float(v)
            if fv != fv or fv <= 0 or fv >= 1e15:
                return
        except Exception:
            return
        state["balance_events"].append(
            {"v": fv, "t": time.perf_counter(), "src": src}
        )
        state["balance_events"] = state["balance_events"][-120:]

    def _parse_maybe_json_text(text: str, src: str) -> None:
        t = (text or "").strip()
        if len(t) < 2 or t[0] not in "{[":
            return
        try:
            obj = json.loads(t)
        except Exception:
            return
        got: list[float] = []
        _balance_like_numbers_from_json(obj, got)
        for x in got[-4:]:
            _record_balance(x, src)

    def _sniff_round_kw(raw: str) -> None:
        low = raw.lower()
        if any(
            k in low
            for k in (
                "game_end",
                "gameend",
                "round_end",
                "roundend",
                "settle",
                "settlement",
                "gameover",
                "match_end",
                "battle_end",
            )
        ):
            state["last_round_signal_ts"] = time.perf_counter()

    def _on_ws(ws: Any) -> None:
        def _frm(payload: Any) -> None:
            raw = ""
            try:
                if hasattr(payload, "text") and getattr(payload, "text", None):
                    raw = str(payload.text)
                elif hasattr(payload, "body"):
                    b = getattr(payload, "body", None)
                    if isinstance(b, str):
                        raw = b
                    elif isinstance(b, (bytes, bytearray)):
                        raw = bytes(b).decode("utf-8", errors="ignore")
            except Exception:
                raw = ""
            if not raw and payload is not None:
                try:
                    raw = str(payload)
                except Exception:
                    raw = ""
            if not raw:
                return
            if len(raw) > 900:
                raw = raw[:900]
            state["ws_snip"] = (state["ws_snip"] + [raw[:280]])[-14:]
            _sniff_round_kw(raw)
            _parse_maybe_json_text(raw, "ws")

        try:
            ws.on("framereceived", _frm)
        except Exception:
            pass

    def _on_response(resp: Any) -> None:
        async def _work() -> None:
            try:
                req = getattr(resp, "request", None)
                rtype = getattr(req, "resource_type", "") if req else ""
                if rtype not in ("xhr", "fetch"):
                    return
                st = getattr(resp, "status", 0)
                if st is None or int(st) < 200 or int(st) >= 300:
                    return
                txt = await resp.text()
                if not txt or len(txt) > 1_800_000:
                    return
                _sniff_round_kw(txt)
                _parse_maybe_json_text(txt, "http")
            except Exception:
                return

        try:
            asyncio.create_task(_work())
        except Exception:
            pass

    page.on("websocket", _on_ws)
    page.on("response", _on_response)

    def _detach() -> None:
        try:
            page.remove_listener("websocket", _on_ws)
        except Exception:
            pass
        try:
            page.remove_listener("response", _on_response)
        except Exception:
            pass

    return state, _detach


async def _locator_png_digest(loc: Any) -> str:
    try:
        buf = await loc.screenshot(timeout=4000, type="png")
    except Exception:
        return ""
    return hashlib.sha256(buf).hexdigest()[:32]


async def _heatmap_click_game_canvas_start(
    page: Any, *, deadline: float
) -> tuple[bool, str]:
    """
    ``#GameCanvas`` 或最大 canvas：在热区 (x≈40–60%, y≈60–74%) 网格点击；
    每次点击前后对同一 locator 截图做 **SHA256** 摘要比对，摘要变化即视为换屏/生效成功。
    """
    x_steps = [0.40, 0.42, 0.45, 0.48, 0.50, 0.52, 0.55, 0.58, 0.60]
    y_steps = [0.60, 0.62, 0.64, 0.65, 0.66, 0.68, 0.70, 0.72, 0.74]
    locators: list[Any] = []
    try:
        gc = page.locator("#GameCanvas")
        if await gc.count() > 0:
            locators.append(gc.first)
    except Exception:
        pass
    try:
        frames_cf: list[Any] = [page.main_frame] + [
            f for f in page.frames if f != page.main_frame
        ]
    except Exception:
        frames_cf = [page.main_frame]
    scored: list[tuple[float, Any]] = []
    for loc in locators:
        try:
            box = await loc.bounding_box()
            if box and float(box.get("width", 0) or 0) >= 100:
                scored.append(
                    (float(box["width"]) * float(box["height"]), loc)
                )
        except Exception:
            continue
    for fr in frames_cf:
        try:
            sub = fr.locator("#GameCanvas")
            if await sub.count() > 0:
                loc = sub.first
                box = await loc.bounding_box()
                if box and float(box.get("width", 0) or 0) >= 100:
                    scored.append(
                        (float(box["width"]) * float(box["height"]), loc)
                    )
        except Exception:
            continue
    if not scored:
        for fr in frames_cf:
            try:
                cnv = fr.locator("canvas")
                n = await cnv.count()
                for i in range(min(int(n), 8)):
                    try:
                        loc = cnv.nth(i)
                        box = await loc.bounding_box()
                        if (
                            box
                            and float(box.get("width", 0) or 0) >= 140
                        ):
                            scored.append(
                                (
                                    float(box["width"]) * float(box["height"]),
                                    loc,
                                )
                            )
                    except Exception:
                        continue
            except Exception:
                continue
    scored.sort(key=lambda x: -x[0])
    tried = 0
    for _, loc in scored[:3]:
        try:
            box = await loc.bounding_box()
        except Exception:
            continue
        if not box:
            continue
        bx = float(box["x"])
        by = float(box["y"])
        w = float(box["width"])
        h = float(box["height"])
        for yr in y_steps:
            for xr in x_steps:
                if time.perf_counter() >= deadline:
                    if tried > 0:
                        return True, f"heatmap_partial_no_yet_diff({tried} taps)"
                    return False, "heatmap_deadline"
                cx = bx + w * xr
                cy = by + h * yr
                tag = f"cell({xr:.2f},{yr:.2f})"
                hb = await _locator_png_digest(loc)
                try:
                    await page.mouse.click(cx, cy, delay=28)
                except Exception:
                    continue
                tried += 1
                try:
                    await page.wait_for_timeout(280)
                except Exception:
                    pass
                ha = await _locator_png_digest(loc)
                if hb and ha and hb != ha:
                    return True, f"heatmap+canvas_pixel_diff({tag})"
    if tried > 0:
        return True, f"heatmap_exhausted_no_digest_delta({tried} taps)"
    return False, ""


async def _gather_frame_inner_text_sample(page: Any, *, max_chars: int = 14_000) -> str:
    """合并各 frame 正文片段（截断），用于一局结束文案嗅探。"""
    parts_dedup: list[str] = []
    seen: set[str] = set()
    try:
        frames: list[Any] = [page.main_frame] + [
            f for f in page.frames if f != page.main_frame
        ]
    except Exception:
        try:
            frames = [page.main_frame]
        except Exception:
            return ""
    cap = max(2000, int(max_chars))
    js = (
        "() => typeof document !== 'undefined' && document.body ? "
        "(document.body.innerText || '').slice(0, "
        + str(cap)
        + ") : ''"
    )
    for fr in frames:
        try:
            raw = await fr.evaluate(js)
            if not isinstance(raw, str) or not raw.strip():
                continue
            h = raw[: min(2000, len(raw))]
            if h in seen:
                continue
            seen.add(h)
            parts_dedup.append(raw)
        except Exception:
            continue
        if sum(len(p) for p in parts_dedup) >= cap:
            break
    return "\n".join(parts_dedup)[:cap]


def _round_end_text_hit(blob: str) -> str:
    if not blob:
        return ""
    m = _ROUND_END_TEXT_RE.search(blob)
    if not m:
        return ""
    return (m.group(0) or "").strip()[:72]


def _env_float(name: str, default: float, *, vmin: float, vmax: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        v = float(raw)
    except ValueError:
        return default
    return max(vmin, min(vmax, v))


async def _wait_autoplay_until_round_end(
    page: Any,
    *,
    case: GameCase,
    max_sec: float,
    sink: Callable[[str], None] | None,
    seen_in_game_initial: bool,
    network_state: dict[str, Any] | None = None,
) -> tuple[str, float]:
    """
    局内等待：优先等到「一局结束」可见文案、**同一预备室界面再次出现**（如 Mines 回到
    Start Game / VS 1v1），或**大厅金币相对锚点稳定变化**（适配纯 canvas、DOM 无按钮文案），
    再退出到站点大厅采金币；若始终未命中则用 ``max_sec`` 兜底。

    可选 ``network_state``：由 ``_attach_k11_game_network_sniffers`` 提供，用于 JSON 余额事件与
    WS 中结算类关键字提前停表。

    返回 ``(结束原因简述, 已等待秒数)``。
    """
    total = max(15.0, float(max_sec))
    poll = _env_float(
        "K11_ROUND_END_POLL_SEC", 0.6, vmin=0.25, vmax=3.0
    )
    min_round_text_sec = _env_float(
        "K11_ROUND_TEXT_MIN_SEC", 8.0, vmin=3.0, vmax=120.0
    )
    min_lobby_signal_sec = _env_float(
        "K11_LOBBY_LEAVE_MIN_SEC", 25.0, vmin=10.0, vmax=600.0
    )
    text_hits_need = int(
        _env_float("K11_ROUND_END_CONFIRM_POLLS", 2.0, vmin=1.0, vmax=6.0)
    )
    lobby_hits_need = int(
        _env_float("K11_LOBBY_LEAVE_CONFIRM_POLLS", 3.0, vmin=2.0, vmax=8.0)
    )
    match_lobby_min_elapsed = _env_float(
        "K11_MATCH_LOBBY_MIN_ELAPSED", 16.0, vmin=8.0, vmax=120.0
    )
    match_leave_need = _env_int(
        "K11_MATCH_LOBBY_LEAVE_POLLS", 2, vmin=2, vmax=8
    )
    match_return_need = _env_int(
        "K11_MATCH_LOBBY_RETURN_POLLS", 2, vmin=2, vmax=8
    )
    wallet_anchor_sec = _env_float(
        "K11_WALLET_ANCHOR_AFTER_SEC", 14.0, vmin=6.0, vmax=90.0
    )
    wallet_after_anchor = _env_float(
        "K11_WALLET_DELTA_MIN_AFTER_ANCHOR", 10.0, vmin=4.0, vmax=120.0
    )
    wallet_settle_need = _env_int(
        "K11_WALLET_SETTLE_POLLS", 2, vmin=2, vmax=6
    )

    t0 = time.perf_counter()
    seen_in_game = bool(seen_in_game_initial)
    text_hits = 0
    lobby_hits = 0
    last_log = t0
    reason = ""

    saw_pre_match_lobby_text = False
    consec_away_from_pre_lobby = 0
    left_pre_match_lobby = False
    consec_pre_lobby_return = 0

    anchor_coin: float | None = None
    anchor_elapsed_mark = 0.0
    last_diff_coin: float | None = None
    diff_stable_hits = 0

    net_sig_hits = 0
    net_last_bal_v: float | None = None
    net_bal_stable = 0

    while time.perf_counter() - t0 < total:
        elapsed = time.perf_counter() - t0
        rest = max(0.0, total - elapsed)
        try:
            ingame_url = await _page_suggests_in_game(page)
        except Exception:
            ingame_url = False
        if ingame_url:
            seen_in_game = True
            lobby_hits = 0

        blob = ""
        try:
            blob = await _gather_frame_inner_text_sample(page)
        except Exception:
            blob = ""

        here_pre = False
        if case.use_pre_match_lobby_resume:
            here_pre = _pre_match_lobby_blob_match(blob)
            if here_pre and elapsed < 40.0:
                saw_pre_match_lobby_text = True

        min_txt_sec = min_round_text_sec
        if not case.use_pre_match_lobby_resume:
            min_txt_sec = min(min_round_text_sec, 5.0)

        rhit = _round_end_text_hit(blob)
        if rhit and elapsed >= min_txt_sec:
            text_hits += 1
            if text_hits >= text_hits_need:
                reason = f"round_end_text:{rhit!r}"
                break
        else:
            text_hits = 0

        if network_state is not None and elapsed >= 12.0:
            ts = float(network_state.get("last_round_signal_ts") or 0.0)
            if ts > 0.0 and (time.perf_counter() - ts) < 3.5:
                net_sig_hits += 1
            else:
                net_sig_hits = 0
            if net_sig_hits >= 3:
                reason = "network_ws_or_http_settle_hint"
                break

        if case.use_pre_match_lobby_resume:
            if (
                saw_pre_match_lobby_text
                and seen_in_game
                and elapsed >= 10.0
            ):
                if not here_pre:
                    consec_away_from_pre_lobby += 1
                    if consec_away_from_pre_lobby >= match_leave_need:
                        left_pre_match_lobby = True
                else:
                    consec_away_from_pre_lobby = 0

            if (
                left_pre_match_lobby
                and here_pre
                and elapsed >= match_lobby_min_elapsed
            ):
                consec_pre_lobby_return += 1
                if consec_pre_lobby_return >= match_return_need:
                    reason = "same_pre_match_lobby_resumed(start_game/vs/cost)"
                    break
            else:
                if not (left_pre_match_lobby and here_pre):
                    consec_pre_lobby_return = 0

        if (
            seen_in_game
            and elapsed >= min_lobby_signal_sec
            and not ingame_url
        ):
            lobby_hits += 1
            if lobby_hits >= lobby_hits_need:
                reason = "lobby_shell_left_stable"
                break
        elif ingame_url:
            pass
        else:
            lobby_hits = 0

        if anchor_coin is None and elapsed >= wallet_anchor_sec:
            try:
                w_a = await _snapshot_lobby_wallet(page)
                c_a, n_a = _first_number_from_hints(w_a)
                if isinstance(c_a, (int, float)):
                    anchor_coin = float(c_a)
                    anchor_elapsed_mark = elapsed
                    if not saw_pre_match_lobby_text:
                        _gprint(
                            f"   [局内等待] 金币锚点≈{anchor_coin}（{n_a}，供纯 canvas 结算嗅探）",
                            sink,
                        )
            except Exception:
                pass

        if (
            anchor_coin is not None
            and elapsed >= anchor_elapsed_mark + wallet_after_anchor
        ):
            try:
                w_n = await _snapshot_lobby_wallet(page)
                c_n, n_n = _first_number_from_hints(w_n)
                if isinstance(c_n, (int, float)):
                    cn = float(c_n)
                    if abs(cn - anchor_coin) >= 0.5:
                        if (
                            last_diff_coin is not None
                            and abs(cn - last_diff_coin) < 0.05
                        ):
                            diff_stable_hits += 1
                        else:
                            diff_stable_hits = 1
                        last_diff_coin = cn
                        if diff_stable_hits >= wallet_settle_need:
                            reason = (
                                f"post_round_wallet_stable "
                                f"({anchor_coin:.0f}→{cn:.0f} {n_n})"
                            )
                            break
                    else:
                        diff_stable_hits = 0
                        last_diff_coin = None
            except Exception:
                pass

        if (
            not reason
            and network_state is not None
            and anchor_coin is not None
            and elapsed
            >= anchor_elapsed_mark
            + _env_float("K11_NET_BALANCE_AFTER_ANCHOR", 7.0, vmin=3.0, vmax=60.0)
        ):
            evs = network_state.get("balance_events") or []
            best: float | None = None
            for e in reversed(evs[-40:]):
                try:
                    et = float(e.get("t", 0.0))
                    if et < t0:
                        continue
                    v = float(e.get("v", 0.0))
                except Exception:
                    continue
                if abs(v - float(anchor_coin)) < 0.5:
                    continue
                best = v
                break
            if best is not None:
                if (
                    net_last_bal_v is not None
                    and abs(best - net_last_bal_v) < 0.05
                ):
                    net_bal_stable += 1
                else:
                    net_bal_stable = 1
                net_last_bal_v = best
                if net_bal_stable >= 2:
                    reason = (
                        f"network_balance_payload "
                        f"({float(anchor_coin):.0f}->{best:.0f})"
                    )
            else:
                net_bal_stable = 0
                net_last_bal_v = None

        if reason:
            break

        if time.perf_counter() - last_log >= 5.0:
            last_log = time.perf_counter()
            _gprint(
                f"   [局内等待] 已≈{elapsed:.1f}s / 上限剩余≈{rest:.1f}s "
                f"(一局结束/预备室/ DOM 金币/网络余额 · WS 结算关键字/退壳)",
                sink,
            )
        try:
            await page.wait_for_timeout(int(poll * 1000))
        except Exception:
            break

    elapsed_fin = time.perf_counter() - t0
    if not reason:
        reason = "timeout_max_sec"
    return reason, elapsed_fin


async def _run_single_game(
    page: Any,
    case: GameCase,
    *,
    verbose: bool,
    play_wait_sec: float,
    deep_timeout_ms: int,
    sink: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    game_start = time.perf_counter()
    ws_times: list[float] = []
    race_reason = ""
    detail = ""
    open_verdict = "FAIL"
    coin_before: float | None = None
    coin_after: float | None = None
    coin_before_note = ""
    coin_after_note = ""
    coin_verdict = "SKIP"
    coin_note: str | None = None
    wait_note = ""
    wait_elapsed = 0.0

    def progress(msg: str) -> None:
        if verbose:
            _gprint(f"  [mcp] {msg}", sink)

    try:
        await mcp._goto_resilient(page, TARGET_HOME, "domcontentloaded", 20_000)
        await mcp._prepare_kalaroko_lobby_after_navigation(page, progress=progress)

        w0 = await _snapshot_lobby_wallet(page)
        coin_before, coin_before_note = _first_number_from_hints(w0)
        _gprint(
            f"   [金币] 进房前大厅≈ {coin_before}（{coin_before_note}）",
            sink,
        )

        click_err = ""
        clicked, click_note = await _click_entry_with_fallback(page, case, progress=progress)
        if not clicked:
            click_err = click_note
            if not await _shell_or_canvas_present(page, case):
                raise RuntimeError(click_note)
        elif click_note:
            click_err = click_note

        _gprint("   - 嗅探二次入场弹窗: Join", sink)
        clicked_join, join_note = await _soft_click_join_if_present(
            page, total_budget_sec=5.0, sink=sink
        )
        if clicked_join:
            _gprint(f"   -> 成功点击 [Join] 按钮，准备进入游戏！({join_note})", sink)
        else:
            _gprint(f"   [提示] Join 未生效：{join_note}", sink)

        t0 = time.perf_counter()
        _, _canvas_seen, race_reason = await mcp._game_deep_wait_after_goto(
            page,
            t0,
            timeout_ms=int(deep_timeout_ms),
            ws_times=ws_times,
            click_flow=True,
        )

        cur_url = ""
        try:
            cur_url = str(page.url or "")
        except Exception:
            cur_url = ""

        open_ok = await _shell_or_canvas_present(page, case)
        if open_ok:
            open_verdict = "PASS"
            if race_reason == "timeout":
                detail = "进框架但停表超时（timeout 兜底结束）"
            else:
                detail = f"极速判定上桌 (停表依据: {race_reason or 'unknown'})"
            if not (_is_open_success(cur_url) or _url_suggests_game_shell(cur_url)):
                detail += " | 进壳依据: 主文档 iframe/frame URL（SPA 主栏 URL 可能未变）"
            if click_err:
                detail += f" | 入口点击告警但已进框架: {click_err}"
            if clicked_join:
                detail += " | 已自动点击 [Join] 入场按钮"
            net_st: dict[str, Any] | None = None
            net_detach: Callable[[], None] | None = None
            try:
                net_st, net_detach = _attach_k11_game_network_sniffers(page)
                _gprint(
                    "   [网络嗅探] fetch/xhr JSON + WebSocket（余额键/结算关键字）",
                    sink,
                )
                if case.try_soft_start_play:
                    _gprint("   - 嗅探 [Start/Play] 按钮...", sink)
                    clicked_start, start_note = await _soft_click_start_or_play(
                        page, total_budget_sec=22.0
                    )
                    if clicked_start:
                        detail += " | 已自动点击 [Start/Play] 按钮"
                        if verbose:
                            _gprint(f"   -> 成功点击: {start_note}", sink)
                    else:
                        _gprint(f"   [提示] Start/Play 未点到：{start_note}", sink)
                else:
                    _gprint(
                        f"   - 本游戏（{case.title}）进局后自动开局，跳过 Start/Play",
                        sink,
                    )

                _gprint(
                    f"   [局内] 等待一局结束或稳定回大厅（上限 {play_wait_sec:.0f}s）...",
                    sink,
                )
                try:
                    s_shell = await _page_suggests_in_game(page)
                except Exception:
                    s_shell = False
                try:
                    s_surf = await _shell_or_canvas_present(page, case)
                except Exception:
                    s_surf = False
                seen0 = bool(s_shell or s_surf)
                wait_note, wait_elapsed = await _wait_autoplay_until_round_end(
                    page,
                    case=case,
                    max_sec=play_wait_sec,
                    sink=sink,
                    seen_in_game_initial=seen0,
                    network_state=net_st,
                )
                detail += f" | 局内停表: {wait_note} ({wait_elapsed:.1f}s)"
                if not case.try_soft_start_play:
                    await _soft_dismiss_round_overlay(
                        page, sink=sink, budget_sec=5.0
                    )
            finally:
                if net_detach is not None:
                    try:
                        net_detach()
                    except Exception:
                        pass
        else:
            open_verdict = "FAIL"
            detail = "未进入 game-frame/gameId 路径"
    except Exception as e:
        open_verdict = "FAIL"
        detail = f"{type(e).__name__}: {str(e)[:220]}"
    finally:
        try:
            await asyncio.wait_for(
                mcp._tactical_retreat_to_platform_home(
                    page,
                    scenario={"name": case.game_id, "start_url": TARGET_HOME},
                ),
                timeout=15.0,
            )
        except Exception:
            try:
                await mcp._goto_resilient(page, TARGET_HOME, "domcontentloaded", 15_000)
            except Exception as refresh_err:
                if open_verdict == "PASS":
                    detail = f"{detail}; 撤离异常后强刷失败: {type(refresh_err).__name__}"

        try:
            await page.wait_for_timeout(800)
        except Exception:
            pass
        w1 = await _snapshot_lobby_wallet(page)
        coin_after, coin_after_note = _first_number_from_hints(w1)
        coin_verdict, coin_note = _coin_verdict(coin_before, coin_after)
        _gprint(
            f"   [金币] 回厅后大厅≈ {coin_after}（{coin_after_note}） 判定: {coin_verdict}",
            sink,
        )

    load_ms = round((time.perf_counter() - game_start) * 1000.0, 1)
    row_verdict = (
        "PASS" if open_verdict == "PASS" and coin_verdict != "FAIL" else "FAIL"
    )
    return {
        "game_id": case.game_id,
        "game_title": case.title,
        "verdict": row_verdict,
        "open_verdict": open_verdict,
        "coin_verdict": coin_verdict,
        "coin_before": coin_before,
        "coin_after": coin_after,
        "coin_before_note": coin_before_note,
        "coin_after_note": coin_after_note,
        "coin_note": coin_note,
        "load_ms": load_ms,
        "detail": detail,
        "race_end_reason": race_reason or "unknown",
        "play_wait_end": wait_note if open_verdict == "PASS" else "",
        "play_wait_elapsed_sec": round(wait_elapsed, 2)
        if open_verdict == "PASS"
        else 0.0,
    }


async def run_coin_smoke_on_existing_page(
    page: Any,
    *,
    verbose: bool = True,
    log: Callable[[str], None] | None = None,
    single_game: str = "",
    cases: list[GameCase] | None = None,
    play_wait_sec: float = 600.0,
    deep_timeout_ms: int = 60_000,
) -> list[dict[str, Any]]:
    if cases is not None:
        selected = cases
    else:
        try:
            selected = _pick_cases(single_game.strip() or None)
        except ValueError as e:
            _gprint(f"[失败] {e}", log)
            return []

    per_game: list[dict[str, Any]] = []
    await mcp._goto_resilient(page, TARGET_HOME, "domcontentloaded", 30_000)
    total_games = len(selected)
    for idx, case in enumerate(selected, start=1):
        _gprint("", log)
        _gprint(f"========== [{idx}/{total_games}] {case.title} · 开门+金币 ==========", log)
        row = await _run_single_game(
            page,
            case,
            verbose=verbose,
            play_wait_sec=play_wait_sec,
            deep_timeout_ms=deep_timeout_ms,
            sink=log,
        )
        per_game.append(row)
        load_sec = float(row["load_ms"]) / 1000.0
        mark = "✓" if row.get("verdict") == "PASS" else "✗"
        _gprint(
            f"[{mark}] {case.title} -> 总评 {row['verdict']} | 进壳 {row['open_verdict']} | "
            f"金币 {row['coin_verdict']} ({load_sec:.2f}s)",
            log,
        )
        _gprint(
            f"   Δ币={row.get('coin_before')}→{row.get('coin_after')} | {row.get('detail')}",
            log,
        )
        _gprint("===========================================================", log)
    return per_game


async def _async_main(args: argparse.Namespace) -> int:
    try:
        selected = _pick_cases(args.single_game)
    except ValueError as e:
        print(f"[失败] {e}", file=sys.stderr)
        return 2

    per_game: list[dict[str, Any]] = []
    browser: Any = None
    context: Any = None
    must_close_context = False
    play_sec = float(args.play_wait_sec)
    deep_ms = int(args.deep_timeout_ms)

    print("———————— K11 三款自玩游戏 · 开门 + 局内等待 + 大厅金币 ————————", flush=True)
    print(f"目标站点: {TARGET_HOME}", flush=True)
    print("测试游戏: " + ", ".join(c.title for c in selected), flush=True)
    print(f"局内等待: {play_sec:.0f}s | deep_wait: {deep_ms}ms", flush=True)
    print("", flush=True)

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("请先安装：pip install playwright && playwright install chromium", file=sys.stderr)
        return 2

    try:
        async with async_playwright() as p:
            h = (urlparse(TARGET_HOME).netloc or "").strip().lower()
            preferred_host = h if h else "www.kalaroko.com"
            browser, context, page, must_close_context = await mcp._launch_kalaroko_browser_context(
                p,
                viewport_width=459,
                viewport_height=851,
                device_scale_factor=2.0,
                headless=bool(args.headless),
                preferred_host=preferred_host,
            )

            per_game = await run_coin_smoke_on_existing_page(
                page,
                verbose=bool(args.verbose),
                log=None,
                cases=selected,
                play_wait_sec=play_sec,
                deep_timeout_ms=deep_ms,
            )

    except Exception as e:
        print(f"[失败] 执行异常: {type(e).__name__}: {e}", file=sys.stderr)
        return 3
    finally:
        if must_close_context and context is not None:
            try:
                await context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass

    open_ok = all(x.get("open_verdict") == "PASS" for x in per_game) and bool(per_game)
    coin_fail = any(x.get("coin_verdict") == "FAIL" for x in per_game)
    total_pass = open_ok and not coin_fail and bool(per_game)
    total_verdict = "PASS" if total_pass else "FAIL"
    total_mark = "✓" if total_pass else "✗"
    print("", flush=True)
    print(f"[大盘总评]: {total_mark} {total_verdict}（进壳全过且无 coin=FAIL）", flush=True)

    out = {
        "schema": SCHEMA,
        "captured_at": _utc_iso(),
        "target_url": TARGET_HOME,
        "verdict": total_verdict,
        "play_wait_sec": play_sec,
        "verdict_summary": {
            "per_game": {
                x["game_id"]: {
                    "open": x["open_verdict"],
                    "coin": x["coin_verdict"],
                    "overall": x["verdict"],
                }
                for x in per_game
            },
            "total": total_verdict,
        },
        "per_game": per_game,
    }

    if not bool(getattr(args, "no_lark_report", False)):
        wiki_url = (
            (getattr(args, "lark_wiki_url", "") or "").strip()
            or (os.environ.get("K11_SMOKE_LARK_WIKI_URL") or "").strip()
            or K11_DEFAULT_LARK_WIKI_URL
        )
        _send_lark_notification(
            per_game=per_game,
            target_url=TARGET_HOME,
            lark_wiki_url=wiki_url,
            log=lambda m: print(m, flush=True),
        )

    if args.json_out:
        p = Path(args.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[JSON] {p.resolve()}", flush=True)

    return 0 if total_pass else 1


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="K11 Tongits/Mines/Bato：开门 + 自动对局窗口 + 大厅金币粗测"
    )
    ap.add_argument("--target-url", default="https://www.kalaroko.com/", help="站点根 URL")
    ap.add_argument(
        "--single-game",
        default="",
        help="仅测一款：tongits_king | mines_clash | bato_bato_pick",
    )
    ap.add_argument("--json-out", type=Path, default=None, help="写出 JSON")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--no-lark-report", action="store_true")
    ap.add_argument(
        "--lark-wiki-url",
        default="",
        help="飞书 Wiki 链接（卡片用）",
    )
    ap.add_argument(
        "--play-wait-sec",
        type=float,
        default=600.0,
        help="局内最长等待（秒）：直到一局结束特征/稳定回大厅或达到该上限后再撤离采币",
    )
    ap.add_argument(
        "--deep-timeout-ms",
        type=int,
        default=60_000,
        help="_game_deep_wait_after_goto 超时（毫秒）",
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap


def main() -> int:
    try:
        from dotenv import load_dotenv

        env_path = ROOT / ".env"
        if env_path.exists():
            load_dotenv(env_path, encoding="utf-8")
    except Exception:
        pass

    args = _build_parser().parse_args()
    global TARGET_HOME
    u = (getattr(args, "target_url", None) or "").strip()
    if u:
        t = u.rstrip("/")
        TARGET_HOME = (t + "/") if t else TARGET_HOME
    try:
        return asyncio.run(_async_main(args))
    except KeyboardInterrupt:
        print("\n[中断] 用户取消执行", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
