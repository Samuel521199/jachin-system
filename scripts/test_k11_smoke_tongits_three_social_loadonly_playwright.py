#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K11 平台冒烟扩展：Tongits King（等一局终局 + 金币对比）+ 三款社交/休闲仅加载

对齐《K11_平台冒烟测试用例》P0 行 42-43（各游戏可运行、金币结算感知）：

- **Tongits King**：大厅采金币 → 进房 → 采「房内/局内」余额粗算台费 → 轮询直至终局探针
  → 记录结算时间戳 → 回大厅再采金币；输出并对比「开局前 / 房间花费 / 终局后」。
  进壳失败或加载报错时输出错误与 frame 诊断，不无限阻塞。

- **Color Blitz Social · Royal Pusoy · Bingo Showdown**：仅验证进壳与界面粗检后回大厅；
  不采金币，备注：因无法自动游玩，故而只检查游戏加载情况，暂时没有金币情况展示，后续会进行补充。

环境：``KALAROKO_CDP_ENDPOINT`` 或 ``--cdp-http``；``pip install playwright``

用法（仓库根）::

  python scripts/test_k11_smoke_tongits_three_social_loadonly_playwright.py
  python scripts/test_k11_smoke_tongits_three_social_loadonly_playwright.py --single tongits_king

退出码：0 全部 PASS；1 有 FAIL；2 环境/CDP；3 未捕获异常。
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import math
import os
import re
import sys
import time
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", encoding="utf-8")
except ImportError:
    pass
except OSError:
    pass

os.environ.setdefault(
    "KALAROKO_MONITOR_ALLOWED_HOSTS",
    "kalaroko.com,www.kalaroko.com,gweb.kalaroko.com,gwp.heronpro.xin,"
    "herontest.xin,www.herontest.xin,gweb.herontest.xin",
)

DEFAULT_TARGET = "https://www.kalaroko.com/"

LOAD_ONLY_COIN_NOTE_ZH = (
    "因无法自动游玩，故而只检查游戏加载情况，暂时没有金币情况展示，后续会进行补充"
)

_SCHEMA_TAG = "k11_smoke_tongits_three_social_loadonly/v1"

_SHELL_BAD_RE = re.compile(
    r"(?:\b404\b|page\s+not\s+found|access\s+denied|network\s*error|"
    r"failed\s+to\s+load|something\s+went\s+wrong|请稍后重试|加载失败|网络异常)",
    re.I,
)

# Tongits / 牌类终局：DOM 粗判（与 mines 探针类似；短局后启用，防教程误触）
_TONGITS_DOM_ROUND_END_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(play\s+again|next\s+round|new\s+round|back\s+to\s+lobby)\b", re.I),
    re.compile(r"\b(you('ve)?\s+(won|win|lost|lose))\b", re.I),
    re.compile(r"\b(round|game|hand)\s+(over|complete|finished|ended)\b", re.I),
    re.compile(r"\b(winner|congratulations|better\s+luck)\b", re.I),
    re.compile(r"\b(draw|tongits|sungka|draw\s+game)\b", re.I),
    re.compile(r"再來一局|再玩一局|返回大廳|返回大厅|下一局|本局|結算|结算|胜利|失敗|失败|平局"),
)


def _load_state_machine_module() -> Any:
    path = ROOT / "scripts" / "test_k11_smoke_games_state_machine_playwright.py"
    name = "k11_smoke_games_state_machine_for_mixed"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_p0() -> Any:
    path = ROOT / "scripts" / "test_k11_p0_platform_smoke_playwright.py"
    spec = importlib.util.spec_from_file_location("k11_p0_platform", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_shell_helpers() -> Any:
    path = ROOT / "scripts" / "k11_kalaroko_shell_helpers.py"
    spec = importlib.util.spec_from_file_location("k11_kalaroko_shell_helpers_ix", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _patch_lobby_display(sm: Any) -> None:
    sm._GAME_LOBBY_DISPLAY.update(
        {
            "tongits_king": "Tongits King",
            "color_blitz_social": "Color Blitz Social",
            "royal_pusoy": "Royal Pusoy",
            "bingo_showdown": "Bingo Showdown",
        }
    )


def _extra_loadonly_scenarios(home: str) -> dict[str, dict[str, Any]]:
    base = {
        "entry_wait_until": "domcontentloaded",
        "click_timeout_ms": 9000,
        "wait_until": "domcontentloaded",
        "timeout_ms": 22_000,
        "prefer_last_on_ambiguous_entry": True,
        "start_url": home,
    }
    return {
        "color_blitz_social": {
            "name": "color_blitz_social",
            "click_selector": r"text=/Color\s*Blitz\s*Social/i",
            **base,
        },
        "royal_pusoy": {
            "name": "royal_pusoy",
            "click_selector": r"text=/Royal\s*Pusoy/i",
            **base,
        },
        "bingo_showdown": {
            "name": "bingo_showdown",
            "click_selector": r"text=/Bingo\s*Showdown/i",
            **base,
        },
    }


def _wrap_scenario_for_game(sm: Any, home: str) -> None:
    extra = _extra_loadonly_scenarios(home)
    orig = sm._scenario_for_game

    def _sf(name: str) -> dict[str, Any]:
        n = (name or "").strip()
        if n in extra:
            s = dict(extra[n])
            s["start_url"] = home
            return s
        return dict(orig(n))

    sm._scenario_for_game = _sf


async def _count_canvas_heuristic(page: Any) -> int:
    try:
        n = await page.evaluate(
            """() => {
              let c = 0;
              try {
                document.querySelectorAll('canvas').forEach(() => { c++; });
              } catch (e) {}
              for (const fr of document.querySelectorAll('iframe')) {
                try {
                  const d = fr.contentDocument;
                  if (d) d.querySelectorAll('canvas').forEach(() => { c++; });
                } catch (e) {}
              }
              return c;
            }"""
        )
        return int(n) if isinstance(n, int) else 0
    except Exception:
        return 0


async def _shell_display_seems_ok(
    page: Any,
    p0k: Any,
    *,
    settle_ms: int,
    min_text_chars: int,
    log: Callable[[str], None],
) -> tuple[bool, str]:
    await page.wait_for_timeout(max(0, int(settle_ms)))
    blob = await p0k._gather_frame_texts_for_end_probe(page, max_total=16000)
    stripped = blob.strip()
    if _SHELL_BAD_RE.search(stripped[:4000]):
        return False, "命中疑似错误/失败类文案"
    if len(stripped) >= min_text_chars:
        return True, f"聚合正文约 {len(stripped)} 字"
    canv = await _count_canvas_heuristic(page)
    if canv >= 1:
        return True, f"正文较短({len(stripped)} 字)但检测到 {canv} 个 canvas（偏 Canvas 壳）"
    return False, f"正文过短({len(stripped)} 字)且无 canvas，疑似未渲染"


async def _back_to_lobby_budget(
    *,
    page: Any,
    p0: Any,
    p0k: Any,
    target_url: str,
    home: str,
    scen: dict[str, Any],
    log: Callable[[str], None],
    budget_sec: float,
) -> tuple[bool, str]:
    if budget_sec <= 0:
        return await p0k._back_to_lobby(
            page, p0=p0, target_url=target_url, home=home, scen=scen, log=log
        )
    try:
        return await asyncio.wait_for(
            p0k._back_to_lobby(
                page, p0=p0, target_url=target_url, home=home, scen=scen, log=log
            ),
            timeout=budget_sec,
        )
    except asyncio.TimeoutError:
        log(f"  [lobby] 常规退出超过 {budget_sec:.0f}s，强制战术撤离…")
        _tactical = p0k._mcp_imports()[5]
        _prepare = p0k._mcp_imports()[4]
        try:
            await _tactical(page, scen, progress=log)
        except Exception as e:
            return False, f"强制撤离异常: {e}"
        try:
            await p0._ensure_on_home_feed(page, target_url, log)
        except Exception:
            try:
                await page.goto(home, wait_until="domcontentloaded", timeout=25_000)
            except Exception:
                pass
        try:
            await _prepare(page, progress=log)
        except Exception:
            pass
        if await p0._p0_lobby_seems_visible(page):
            return True, "常规退出超时后战术撤离，大厅可见"
        return False, "常规退出超时且战术撤离后仍未确认大厅"


def _attach_page_harvest(page: Any) -> tuple[list[str], list[str]]:
    errs: list[str] = []
    logs: list[str] = []

    def on_pe(exc: BaseException) -> None:
        try:
            errs.append(f"pageerror: {type(exc).__name__}: {exc!s}"[:500])
        except Exception:
            pass

    def on_cons(msg: Any) -> None:
        try:
            if msg.type == "error":
                logs.append(f"console.{msg.type}: {msg.text!s}"[:400])
        except Exception:
            pass

    page.on("pageerror", on_pe)
    page.on("console", on_cons)
    return errs, logs


def _brief(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc!s}"[:280]


async def _probe_tongits_round_ended(
    page: Any,
    sm: Any,
    *,
    phase_elapsed_sec: float,
    min_dom_sec: float,
) -> str:
    w = await sm._probe_window_round_ended(page)
    if w:
        return w
    if phase_elapsed_sec < min_dom_sec:
        return ""
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
    for rx in _TONGITS_DOM_ROUND_END_PATTERNS:
        m = rx.search(text)
        if m:
            return f"tongits_dom:{str(m.group(0))[:48]!r}"
    return ""


async def _run_tongits_king(
    page: Any,
    *,
    sm: Any,
    p0: Any,
    home: str,
    target: str,
    entry_timeout_sec: float,
    pre_wait_sec: float,
    play_budget_sec: float,
    poll_sec: float,
    min_tongits_dom_sec: float,
    log: Callable[[str], None],
) -> dict[str, Any]:
    _goto, _deep, _diagnose, _prepare = sm._mcp_imports()
    game = "tongits_king"
    scen = sm._scenario_for_game(game)
    scen["start_url"] = home
    k11_sel = sm._k11_lobby_click_selector_string(game)
    if k11_sel:
        scen["click_selector"] = k11_sel
    click_sel = str(scen.get("click_selector") or "").strip()
    deep_ms = int(scen.get("timeout_ms") or 90_000)

    page_errs, cons_errs = _attach_page_harvest(page)

    out: dict[str, Any] = {
        "game_id": game,
        "mode": "tongits_full_round_coin",
        "gold_before": None,
        "gold_before_note": "",
        "gold_in_room": None,
        "gold_in_room_note": "",
        "room_fee_estimate": None,
        "settlement_detected": False,
        "settlement_signal": "",
        "settlement_time_utc": "",
        "gold_after": None,
        "gold_after_note": "",
        "coin_compare_note": "",
        "entry_ok": False,
        "entry_signal": "",
        "back_to_home": False,
        "error": "",
        "page_errors": page_errs,
        "console_errors_sample": cons_errs[:12],
    }

    err_click: str | None = None
    err_parts: list[str] = []

    try:
        w0 = await sm._snapshot_lobby_wallet(page)
        ini, ini_n = sm._first_number_from_hints(w0)
        out["gold_before"] = ini
        out["gold_before_note"] = ini_n
        sm._log_step(log, f"Tongits: 开局前大厅金币≈ {ini}（{ini_n}）")

        try:
            await _goto(
                page, home, str(scen.get("entry_wait_until") or "load"), 60_000
            )
        except Exception as e:
            out["error"] = _brief(e)
            return out

        await _prepare(page, progress=lambda m: log(f"  {m}"))
        await sm._k11_ensure_lobby_home_all_and_scroll_game(page, game, log)

        t_click = time.perf_counter()
        try:
            await _diagnose(
                page,
                click_selector=click_sel,
                click_timeout_ms=int(scen.get("click_timeout_ms") or 10_000),
                scenario_name=game,
                scenario=scen,
                progress=lambda m: log(f"  {m}"),
            )
        except Exception as e:
            err_click = str(e)[:400]
            log(f"  [warn] 入口点击异常: {err_click[:160]}")

        await sm._k11_try_select_coins_join_modal(
            page, log, None, phase="点卡后", attempts=2, between_ms=400
        )
        ws_times: list[float] = []
        try:
            await _deep(
                page, t_click, max(60_000, deep_ms), ws_times, click_flow=True
            )
        except Exception as e:
            if not err_click:
                err_click = f"deep: {e}"[:400]

        await sm._k11_try_select_coins_join_modal(
            page, log, None, phase="深等后", attempts=3, between_ms=500
        )

        entry_ok, entry_sig = await sm._wait_entry_shell(
            page, timeout_sec=entry_timeout_sec, log=log
        )
        if not entry_ok:
            ok2, sig2 = await sm._entry_last_chance_reconfirm(page, log)
            if ok2:
                entry_ok = True
                entry_sig = sig2
        out["entry_ok"] = entry_ok
        out["entry_signal"] = str(entry_sig)[:300]

        if not entry_ok:
            diag = sm._format_frame_url_diag(page)
            msg = (
                (err_click or "进壳超时/未识别")
                + "\n[frames]\n"
                + diag
                + "\n[pageerror]\n"
                + "\n".join(page_errs[-6:])
                + "\n[console error]\n"
                + "\n".join([x for x in cons_errs if x][-6:])
            )
            out["error"] = msg.strip()[:2400]
            return out

        if err_click:
            sm._log_step(log, "进壳已确认，入口层异常仅供参考")

        sm._log_step(
            log, f"预载 {pre_wait_sec:.0f}s 后做房内金币粗采样与轻量点按…"
        )
        await page.wait_for_timeout(int(max(0, pre_wait_sec) * 1000))
        await sm._apply_game_hint_actions(page, game, log, None)

        await page.wait_for_timeout(2000)
        w_room = await sm._snapshot_lobby_wallet(page)
        g_room, g_room_n = sm._first_number_from_hints(w_room)
        out["gold_in_room"] = g_room
        out["gold_in_room_note"] = g_room_n
        if isinstance(ini, (int, float)) and isinstance(g_room, (int, float)):
            out["room_fee_estimate"] = round(float(ini) - float(g_room), 6)
            sm._log_step(
                log,
                f"Tongits: 房内粗采样金币≈ {g_room}，估算台费 Δ≈ {out['room_fee_estimate']}",
            )
        else:
            sm._log_step(
                log,
                f"Tongits: 房内粗采样金币≈ {g_room}（{g_room_n}），台费无法数值估算",
            )

        t_budget_end = time.monotonic() + max(20.0, play_budget_sec)
        poll_i = 0
        t_play_phase = time.monotonic()
        settlement_iso = ""
        settlement_sig = ""
        stall_n = int(os.environ.get("K11_SM_STALL_POLLS", "6"))
        last_key: str | None = None
        stable = 0

        while time.monotonic() < t_budget_end:
            elapsed = time.monotonic() - t_play_phase
            sig = await _probe_tongits_round_ended(
                page,
                sm,
                phase_elapsed_sec=elapsed,
                min_dom_sec=min_tongits_dom_sec,
            )
            if sig:
                settlement_sig = sig
                settlement_iso = datetime.now(timezone.utc).isoformat()
                out["settlement_detected"] = True
                out["settlement_signal"] = sig
                out["settlement_time_utc"] = settlement_iso
                sm._log_step(log, f"Tongits: 捕获结算探针 @ {settlement_iso} → {sig}")
                await page.wait_for_timeout(1500)
                break

            try:
                u = page.url or ""
            except Exception:
                u = ""
            if sm._looks_like_lobby_url(u, home):
                settlement_sig = "url:returned-lobby-like"
                settlement_iso = datetime.now(timezone.utc).isoformat()
                out["settlement_detected"] = True
                out["settlement_signal"] = settlement_sig
                out["settlement_time_utc"] = settlement_iso
                sm._log_step(log, "Tongits: 主文档已似回大厅，视作结算窗口")
                break

            prog_key = sm._stall_progress_key(page)
            if last_key is not None and prog_key == last_key and entry_ok:
                stable += 1
            else:
                stable = 0
            last_key = prog_key
            if entry_ok and stable >= stall_n and not sm._looks_like_lobby_url(
                u, home
            ):
                err_parts.append(f"bail:stall_{int(stable * poll_sec)}s")
                sm._log_step(log, f"Tongits: 无进展早退 {err_parts[-1]}")
                break

            poll_i += 1
            await page.wait_for_timeout(int(max(2.0, poll_sec) * 1000))

        if not out["settlement_detected"]:
            err_parts.append(
                f"timeout:no_settlement_within_{play_budget_sec:.0f}s"
            )
            out["error"] = "; ".join(err_parts + ([err_click] if err_click else []))[
                :2000
            ]

        sm._log_step(log, "Tongits: 回大厅采终局金币…")
        try:
            await page.goto(home, wait_until="domcontentloaded", timeout=55_000)
            try:
                await p0._ensure_on_home_feed(page, target, log)
            except Exception:
                pass
            await page.wait_for_timeout(900)
            out["back_to_home"] = True
        except Exception as e:
            err_parts.append(f"goto_home:{_brief(e)}")

        w1 = await sm._snapshot_lobby_wallet(page)
        fin, fin_n = sm._first_number_from_hints(w1)
        out["gold_after"] = fin
        out["gold_after_note"] = fin_n
        sm._log_step(log, f"Tongits: 终局后大厅金币≈ {fin}（{fin_n}）")

        if isinstance(ini, (int, float)) and isinstance(fin, (int, float)):
            delta = float(fin) - float(ini)
            out["coin_compare_note"] = (
                f"终局相对开局 Δ={delta:.4f}（开局 {ini} → 终局 {fin}；"
                f"估算台费 {out['room_fee_estimate']!s}）"
            )
            if math.isclose(delta, 0.0, abs_tol=1e-3):
                out["coin_sync_verdict"] = "UNCHANGED"
            else:
                out["coin_sync_verdict"] = "CHANGED"
        else:
            out["coin_compare_note"] = "开局或终局金币未能解析为数字，仅作人工核对"
            out["coin_sync_verdict"] = "SKIP"

        ok_round = bool(out["settlement_detected"])
        ok_back = bool(out["back_to_home"])
        ok_coin = out.get("coin_sync_verdict") == "CHANGED"
        out["verdict"] = (
            "PASS"
            if (ok_round and ok_back and (ok_coin or out.get("coin_sync_verdict") == "SKIP"))
            else "FAIL"
        )
        if out.get("coin_sync_verdict") == "UNCHANGED" and ok_round:
            out["verdict"] = "FAIL"
            out["coin_compare_note"] += "；终局金币相对开局无变化，金币同步粗测记为未通过"
        if err_parts and not out["error"]:
            out["error"] = "; ".join(err_parts)[:1500]

    except Exception as e:
        out["error"] = _brief(e)
        try:
            out["error"] += "\n" + sm._format_frame_url_diag(page)
        except Exception:
            pass
        out["verdict"] = "FAIL"

    out["page_errors"] = list(page_errs)[-20:]
    out["console_errors_sample"] = list(cons_errs)[:20]
    return out


async def _run_load_only(
    page: Any,
    *,
    p0: Any,
    p0k: Any,
    sm: Any,
    game: str,
    home: str,
    target: str,
    deep_wait_ms: int,
    settle_ms: int,
    min_text: int,
    lobby_budget: float,
    log: Callable[[str], None],
) -> dict[str, Any]:
    scen = sm._scenario_for_game(game)
    scen["start_url"] = home
    scen["timeout_ms"] = max(5_000, int(deep_wait_ms))

    row: dict[str, Any] = {
        "game_id": game,
        "mode": "load_only",
        "coin_note_zh": LOAD_ONLY_COIN_NOTE_ZH,
        "display_ok": False,
        "display_note": "",
        "back_to_lobby": False,
        "back_note": "",
        "verdict": "FAIL",
        "click_error": None,
        "shell_game_frame": False,
    }

    progress = lambda m: log(f"  [mcp] {m}")
    r = await p0k._run_one_game_e2e_like(
        page, scen=scen, home=home, log=progress, shell_phase_timeout_ms=60_000
    )
    row["shell_game_frame"] = bool(r.get("shell_game_frame"))
    row["click_error"] = r.get("click_error")
    row["load_detail"] = {k: r.get(k) for k in ("final_url", "real_engine_load_ms", "metrics") if r.get(k) is not None}

    if row["shell_game_frame"] and not row.get("click_error"):
        ok, note = await _shell_display_seems_ok(
            page, p0k, settle_ms=settle_ms, min_text_chars=min_text, log=log
        )
        row["display_ok"] = ok
        row["display_note"] = note
    else:
        row["display_note"] = "未进壳或点击异常，跳过显示检测"

    b_ok, b_note = await _back_to_lobby_budget(
        page=page,
        p0=p0,
        p0k=p0k,
        target_url=target,
        home=home,
        scen=scen,
        log=log,
        budget_sec=lobby_budget,
    )
    row["back_to_lobby"] = b_ok
    row["back_note"] = b_note

    ok = (
        row["shell_game_frame"]
        and not row.get("click_error")
        and row["display_ok"]
        and row["back_to_lobby"]
    )
    row["verdict"] = "PASS" if ok else "FAIL"
    row["verdict_detail"] = "; ".join(
        [
            "已进 game-frame" if row["shell_game_frame"] else "未进 game-frame",
            f"显示:{row['display_note']}",
            "已回大厅" if row["back_to_lobby"] else f"回厅:{b_note}",
            LOAD_ONLY_COIN_NOTE_ZH,
        ]
    )
    if row.get("click_error"):
        row["error"] = str(row["click_error"])[:800]
    return row


GAME_ORDER_DEFAULT: tuple[str, ...] = (
    "tongits_king",
    "color_blitz_social",
    "royal_pusoy",
    "bingo_showdown",
)


async def _async_main(args: argparse.Namespace) -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print(
            "请先安装：pip install playwright && playwright install chromium",
            file=sys.stderr,
        )
        return 2

    sm = _load_state_machine_module()
    _patch_lobby_display(sm)
    p0 = _load_p0()
    p0k = _load_shell_helpers()

    target = (args.target_url or DEFAULT_TARGET).strip()
    home = p0._home_feed_url(target)
    host = p0._host_from_url(target)
    cdp = p0._kalaroko_cdp(args.cdp_http or None)

    _wrap_scenario_for_game(sm, home)

    games = [args.game] if args.single else list(GAME_ORDER_DEFAULT)
    valid = set(GAME_ORDER_DEFAULT)
    for g in games:
        if g not in valid:
            print(f"[失败] 未知 --game {g!r}，可选 {sorted(valid)}", file=sys.stderr)
            return 2

    def log(msg: str) -> None:
        if not args.quiet or args.verbose:
            print(msg, flush=True)

    log("======== K11 冒烟 · Tongits 全局金币 + 三款仅加载（社交）========")
    log(f"CDP={cdp} home={home}")
    log(f"游戏顺序: {' · '.join(games)}")
    log("")

    results: list[dict[str, Any]] = []

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(cdp)
        page, pick_err = await p0._acquire_cdp_target_page(
            browser,
            host=host,
            target_url=target,
            navigate_if_no_tab=not args.require_existing_tab,
            log=log,
        )
        if page is None:
            print(f"[失败] {pick_err or '无法获取页签'}", file=sys.stderr)
            return 2
        ok_env, detail = await p0._ensure_target_page(
            page,
            target,
            log=log,
            navigate_if_no_tab=not args.require_existing_tab,
            host=host,
        )
        if not ok_env:
            print(f"[失败] {detail}", file=sys.stderr)
            return 2

        await p0._ensure_on_home_feed(page, target, log)
        try:
            if (page.url or "").rstrip("/") != home.rstrip("/"):
                await page.goto(home, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(400)
        except Exception as e:
            log(f"  [warn] {p0._brief_exc(e)}")

        _prepare = p0k._mcp_imports()[4]
        await _prepare(page, progress=lambda m: log(f"  [mcp] {m}"))

        for gi, gid in enumerate(games):
            log(f"—— [{gi + 1}/{len(games)}] {gid} ——")
            if gid == "tongits_king":
                row = await _run_tongits_king(
                    page,
                    sm=sm,
                    p0=p0,
                    home=home,
                    target=target,
                    entry_timeout_sec=float(
                        args.entry_wait_sec
                        or os.environ.get("K11_SM_ENTRY_TIMEOUT", "90")
                    ),
                    pre_wait_sec=float(
                        args.pre_wait_sec
                        or os.environ.get("K11_SM_PRE_WAIT", "10")
                    ),
                    play_budget_sec=float(args.tongits_play_sec),
                    poll_sec=float(args.poll_sec),
                    min_tongits_dom_sec=float(args.tongits_min_dom_sec),
                    log=log,
                )
            else:
                row = await _run_load_only(
                    page,
                    p0=p0,
                    p0k=p0k,
                    sm=sm,
                    game=gid,
                    home=home,
                    target=target,
                    deep_wait_ms=int(args.deep_wait_ms),
                    settle_ms=int(args.settle_ms),
                    min_text=int(args.min_text_chars),
                    lobby_budget=float(args.lobby_exit_budget_sec),
                    log=log,
                )

            results.append(row)

            v = str(row.get("verdict", "FAIL"))
            mark = "[PASS]" if v == "PASS" else "[FAIL]"
            log(f"  {mark}  {gid}")
            if gid == "tongits_king":
                log(
                    f"      开局金币={row.get('gold_before')} | "
                    f"估算台费={row.get('room_fee_estimate')} | "
                    f"终局金币={row.get('gold_after')} | "
                    f"结算UTC={row.get('settlement_time_utc')!s} | "
                    f"探针={str(row.get('settlement_signal'))[:80]!r}"
                )
                if row.get("error"):
                    log(f"      错误摘录: {str(row.get('error'))[:360]!s}")
            else:
                log(f"      {row.get('verdict_detail', '')[:220]!s}")

            if gid != games[-1] and not row.get("back_to_lobby", True):
                if gid == "tongits_king" and not row.get("back_to_home"):
                    log("[WARN] 尝试恢复大厅…")
                elif gid != "tongits_king" and not row.get("back_to_lobby"):
                    log("[WARN] 尝试恢复大厅…")
                try:
                    await page.goto(home, wait_until="domcontentloaded", timeout=60_000)
                    await _prepare(page, progress=lambda m: log(f"  [mcp] {m}"))
                except Exception as e:
                    log(f"  [warn] 恢复: {e}")

        try:
            await page.goto(home, wait_until="domcontentloaded", timeout=50_000)
            await _prepare(page, progress=lambda m: log(f"  [mcp] {m}"))
        except Exception as e:
            log(f"  [warn] {e}")

    overall = all(str(r.get("verdict")) == "PASS" for r in results)

    doc: dict[str, Any] = {
        "schema": _SCHEMA_TAG,
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "cdp": cdp,
        "target_url": target,
        "home_url": home,
        "games": games,
        "load_only_coin_note_zh": LOAD_ONLY_COIN_NOTE_ZH,
        "results": results,
        "verdict": "PASS" if overall else "FAIL",
    }

    if args.json_out:
        outp = Path(args.json_out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(
            json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        log(f"JSON → {outp.resolve()}")

    print("", flush=True)
    print("========== 汇总 ==========", flush=True)
    for r in results:
        print(f"  {r.get('game_id')}: {r.get('verdict')}", flush=True)
    print(f"总评: {'PASS' if overall else 'FAIL'}", flush=True)
    return 0 if overall else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description="K11：Tongits 一局+金币 + Color Blitz / Royal Pusoy / Bingo 仅加载"
    )
    ap.add_argument("--target-url", default=DEFAULT_TARGET)
    ap.add_argument("--cdp-http", default="")
    ap.add_argument(
        "--game",
        default=GAME_ORDER_DEFAULT[0],
        choices=list(GAME_ORDER_DEFAULT),
    )
    ap.add_argument("--single", action="store_true")
    ap.add_argument("--require-existing-tab", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--json-out", default="", help="结果 JSON 路径")

    ap.add_argument(
        "--entry-wait-sec",
        type=float,
        default=None,
        help="进壳最长等待秒（默认同状态机 K11_SM_ENTRY_TIMEOUT 或 90）",
    )
    ap.add_argument(
        "--pre-wait-sec",
        type=float,
        default=None,
        help="进壳后预载秒（默认 10 或 K11_SM_PRE_WAIT）",
    )
    ap.add_argument(
        "--tongits-play-sec",
        type=float,
        default=150.0,
        help="Tongits 等待终局探针的上限秒数",
    )
    ap.add_argument(
        "--poll-sec",
        type=float,
        default=5.0,
        help="Tongits 轮询间隔秒",
    )
    ap.add_argument(
        "--tongits-min-dom-sec",
        type=float,
        default=12.0,
        help="Tongits 启用 DOM 结算文案探针前的最短局内秒数",
    )

    ap.add_argument(
        "--deep-wait-ms",
        type=int,
        default=12_000,
        help="三款仅加载：MCP 进壳晚期竞速上限(ms)",
    )
    ap.add_argument("--settle-ms", type=int, default=2200, help="仅加载：进壳后稳定等待")
    ap.add_argument(
        "--lobby-exit-budget-sec",
        type=float,
        default=28.0,
        help="仅加载：回大厅整段超时",
    )
    ap.add_argument("--min-text-chars", type=int, default=100)

    args = ap.parse_args()
    try:
        return asyncio.run(_async_main(args))
    except Exception as e:
        print(f"[失败] {type(e).__name__}: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
