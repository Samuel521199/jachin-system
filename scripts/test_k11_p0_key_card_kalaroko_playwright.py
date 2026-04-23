#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K11 · P0 关键卡片 + 多游戏可玩 + 金币（与 Kalaroko E2E / MCP 同源流程）

**对齐文档**《K11_平台冒烟测试用例》：

- **关键卡片点击**：至少 1 个核心游戏卡片能进入下一层（game-frame 壳）。
- **各游戏正常运行**：在进 **game-frame** 后，**不操作牌面**，在时限内**轮询主文档+子 frame 可见文案**，
  直到匹配「对局终局/结算/胜负/继续」等**启发式**（或可选 **UI 长稳**作为 Canvas 局兜底）；
  再 **KK/Exit 或战术撤离** 回大厅。纯 Canvas、无文案例可能判 **FAIL/超时**（需调大
  ``--round-max-wait-sec`` 或接视觉模型）。
- **游戏金币同步**：**每局进房前**采大厅余额；**终局后、回大厅前**在壳内再采一次；**回大厅后**再采。逐局比对可解析数字，
  失败条件：该局已完局但前后数字缺失等。

与 ``scripts/test_kalaroko_default_scenarios_e2e.py`` 一致：复用
``KALAROKO_DEFAULT_SCENARIOS``、``_diagnose_and_click_kalaroko_game_entry``、
``_game_deep_wait_after_goto`` 等（``l3_client/.../mcp_kalaroko_monitor.py``）。

**与云端 E2E 的差异**：本脚本不跑 ``fetch_api_health`` / ``manage_perf_history``，专注浏览器侧三条 P0。

前置：
  - ``KALAROKO_CDP_ENDPOINT`` 或 ``--cdp-http``
  - ``pip install playwright``

用法（仓库根）::

  python scripts/test_k11_p0_key_card_kalaroko_playwright.py
  python scripts/test_k11_p0_key_card_kalaroko_playwright.py --single --game tongits_king
  python scripts/test_k11_p0_key_card_kalaroko_playwright.py -v --json-out out/k11_p0_key.json
  set K11_P0_ROUND_MAX_WAIT_SEC=900
  python scripts/test_k11_p0_key_card_kalaroko_playwright.py --round-max-wait-sec 900

输出：每款游戏有独立 **PASS/FAIL**；与《K11_平台冒烟测试用例》对应的三条 P0
（关键卡片点击 / 各游戏正常运行 / 游戏金币同步）在控制台与 **JSON** 中分项列出。  
**总评**仅作汇总，不以单行代替分项。

退出码：0 全部项满足脚本规则；1 有 FAIL；2 环境/CDP；3 未捕获异常。
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

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
    "kalaroko.com,www.kalaroko.com,gweb.kalaroko.com,gwp.heronpro.xin",
)

DEFAULT_TARGET = "https://www.kalaroko.com/"

# 与 MCP 内 ``_METRICS_JS`` 无关：仅用于大厅「余额/金币」粗采样（勿当财务审计）
_LOBBY_WALLET_SNIFF_JS = r"""() => {
  const out = [];
  const push = (s) => {
    const t = (s || '').replace(/\s+/g, ' ').trim();
    if (t && /\d/.test(t)) out.push(t.slice(0, 120));
  };
  for (const sel of [
    '[class*="balance" i]', '[class*="coin" i]', '[class*="wallet" i]',
    '[class*="Currency" i]', 'header [class*="gold" i]', '[data-balance]', '[data-coin]'
  ]) {
    try {
      document.querySelectorAll(sel).forEach((el) => push(el.innerText));
    } catch (e) {}
  }
  try {
    if (document.body) {
      const t = (document.body.innerText || '').slice(0, 4000);
      const m = t.match(/(?:₱|PHP|JCoins?|Coins?)\s*[:\s]*[\d,]+(?:\.[\d]+)?/ig);
      if (m) m.slice(0, 6).forEach((x) => push(x));
    }
  } catch (e) {}
  return { hints: [...new Set(out)].slice(0, 20), href: (location && location.href) || '' };
}"""

# 对局自然结束：多语言启发式；纯 Canvas 可辅以正文「长稳」兜底（见 _wait_for_natural_round_end）
_ROUND_END_RE = re.compile(
    r"(?:game\s*over|you\s*lose|you\s*win|defeat|victory|round\s*over|match\s*(?:over|end)|"
    r"settlement|play\s*again|back\s*to\s*lobby|return\s*to\s*lobby|"
    r"结算|胜利|失败|对局结束|牌局结束|本局|再玩|继续|返回|离开|重开|"
    r"\b(?:continue|confirm|claim|tap\s*to)\b)",
    re.I,
)


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _load_p0_smoke_module() -> Any:
    path = ROOT / "scripts" / "test_k11_p0_platform_smoke_playwright.py"
    spec = importlib.util.spec_from_file_location("k11_p0_platform_smoke_playwright", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _game_names_from_mcp_defaults() -> list[str]:
    from l3_client.local_mcps.kalaroko_monitor.mcp_kalaroko_monitor import (
        KALAROKO_DEFAULT_SCENARIOS,
    )

    out: list[str] = []
    for s in KALAROKO_DEFAULT_SCENARIOS:
        n = str(s.get("name") or "").strip()
        if n and n != "homepage":
            out.append(n)
    return out


def _scenario_copy_for_game(game: str) -> dict[str, Any]:
    from l3_client.local_mcps.kalaroko_monitor.mcp_kalaroko_monitor import (
        KALAROKO_DEFAULT_SCENARIOS,
    )

    for s in KALAROKO_DEFAULT_SCENARIOS:
        if str(s.get("name") or "") == game:
            return dict(s)
    raise ValueError(f"未知游戏场景 {game!r}，可选：{', '.join(_game_names_from_mcp_defaults())}")


def _first_number_from_hints(h: dict[str, Any]) -> tuple[float | None, str]:
    """从 sniff 结果中尽量抽一个数字（PHP 区隔逗号）。"""
    hints = h.get("hints")
    if not isinstance(hints, list):
        return None, "无 hints 列表"
    blob = " | ".join(str(x) for x in hints if x)
    m = re.search(r"[\d,]+(?:\.\d+)?", blob.replace(",", ""))
    if not m:
        return None, "文案中无数字"
    try:
        return float(m.group(0).replace(",", "")), f"自 hints 提取 ≈{m.group(0)}"
    except ValueError:
        return None, "数字解析失败"


async def _snapshot_lobby_wallet(
    page: Any,
) -> dict[str, Any]:
    try:
        raw = await page.evaluate(_LOBBY_WALLET_SNIFF_JS)
        if isinstance(raw, dict):
            return raw
    except Exception as e:
        return {"error": str(e)[:200], "hints": []}
    return {"hints": []}


async def _gather_frame_texts_for_end_probe(page: Any, max_total: int = 16000) -> str:
    """聚合主文档 + 子 frame 的 innerText，供终局关键词/稳态检测。"""
    chunks: list[str] = []
    try:
        main = await page.evaluate(
            "() => (document.body && document.body.innerText) ? document.body.innerText : ''"
        )
        if isinstance(main, str) and main.strip():
            chunks.append(main[:8000])
    except Exception:
        pass
    try:
        for fr in page.frames:
            if fr == page.main_frame:
                continue
            try:
                t = await fr.evaluate(
                    "() => (document.body && document.body.innerText) ? document.body.innerText : ''"
                )
                if isinstance(t, str) and t.strip():
                    chunks.append(t[:4000])
            except Exception:
                continue
    except Exception:
        pass
    return "\n".join(chunks)[:max_total]


async def _wait_for_natural_round_end(
    page: Any,
    *,
    log: Callable[[str], None],
    min_wait_sec: float,
    max_wait_sec: float,
    poll_sec: float = 2.8,
    stable_polls_needed: int = 6,
    min_blob_for_stable: int = 400,
) -> tuple[bool, str, float]:
    """
    在已进 game-frame 后等待：用户不操作，依赖对局自行结束。
    返回 (是否认为已终局, 说明, 墙钟秒)。
    """
    t0 = time.monotonic()
    await page.wait_for_timeout(int(max(0.0, min_wait_sec) * 1000))
    deadline = t0 + max(1.0, max_wait_sec)
    last_hash: str | None = None
    stable_run = 0
    last_heartbeat = t0
    while time.monotonic() < deadline:
        blob = await _gather_frame_texts_for_end_probe(page)
        if _ROUND_END_RE.search(blob):
            elapsed = time.monotonic() - t0
            preview = blob.replace("\n", " ")[:96]
            return (
                True,
                f"终局/结算类文案命中（probe 摘要: {preview!r}…）",
                elapsed,
            )
        digest = hashlib.md5(blob.encode("utf-8", errors="ignore")).hexdigest()
        if len(blob) >= min_blob_for_stable and digest == last_hash:
            stable_run += 1
        else:
            stable_run = 0
        last_hash = digest
        if stable_run >= stable_polls_needed:
            elapsed = time.monotonic() - t0
            return (
                True,
                f"正文长稳兜底（≥{min_blob_for_stable} 字且连续 {stable_polls_needed} 次 poll 哈希相同，偏 Canvas 局）",
                elapsed,
            )
        now = time.monotonic()
        if now - last_heartbeat >= 30.0:
            log(
                f"  [round-wait] 仍在等待对局自然结束… {now - t0:.0f}s / {max_wait_sec:.0f}s"
            )
            last_heartbeat = now
        await page.wait_for_timeout(int(poll_sec * 1000))
    elapsed = time.monotonic() - t0
    return (
        False,
        f"{max_wait_sec:.0f}s 内未匹配终局文案且无长稳兜底（可调大 K11_P0_ROUND_MAX_WAIT_SEC 或 --round-max-wait-sec）",
        elapsed,
    )


def _finalize_per_game_verdict(row: dict[str, Any]) -> None:
    """
    单款：进壳 + 等到终局启发式 + 回大厅 → PASS。
    """
    ok = (
        bool(row.get("shell_game_frame"))
        and bool(row.get("round_natural_end"))
        and bool(row.get("back_to_lobby"))
    )
    row["verdict"] = "PASS" if ok else "FAIL"
    bits: list[str] = []
    bits.append("主文档已含 game-frame" if row.get("shell_game_frame") else "主文档未进 game-frame")
    bits.append(
        "已观测对局自然结束/终局启发式"
        if row.get("round_natural_end")
        else "未观测到终局（超时/无文案）"
    )
    bits.append("已回大厅" if row.get("back_to_lobby") else "未确认回大厅")
    row["verdict_detail"] = "；".join(bits)


def _coin_line_verdict(
    before_num: float | None,
    after_num: float | None,
    *,
    round_ok: bool,
    lobby_ok: bool,
) -> str:
    """单局大厅余额：仅当该局已完局且回厅时，才要求可解析的前后数字（否则 SKIP）。"""
    if not (round_ok and lobby_ok):
        return "SKIP"
    if before_num is not None and after_num is not None:
        return "PASS"
    if before_num is None and after_num is None:
        return "SKIP"
    return "FAIL"


def _print_k11_p0_report(
    *,
    per_game: list[dict[str, Any]],
    p0_key_card: bool,
    p0_all_games: bool,
    coin_verdict: str,
    coin_detail: str,
    overall: bool,
) -> None:
    """分项打印《K11_平台冒烟测试用例》三列 P0 + 每游戏 verdict。"""
    print("", flush=True)
    print("========== K11 平台冒烟 P0（本脚本分项结果）==========", flush=True)
    print("【每款游戏·独立 verdict】", flush=True)
    for x in per_game:
        gid = str(x.get("game_id") or "")
        v = str(x.get("verdict") or "FAIL")
        vd = (x.get("verdict_detail") or "").strip()
        ce = str(x.get("coin_line_verdict") or "SKIP")
        rw = x.get("round_wait_wall_sec")
        rw_s = f"{float(rw):.0f}s" if isinstance(rw, (int, float)) else "—"
        mark = "[PASS]" if v == "PASS" else "[FAIL]"
        line = f"  {mark}  {gid:20}  总评:{v}  金币行:{ce}  等终局:{rw_s}"
        if vd:
            line += f"  | {vd}"
        print(line, flush=True)
    print("", flush=True)
    print("【文档测评项 · 与 K11_平台冒烟测试用例.md 对应】", flush=True)
    kc = "PASS" if p0_key_card else "FAIL"
    print(
        f"  1) 关键卡片点击 — 至少 1 个核心游戏卡片可进入下一层/流程  : {kc}",
        flush=True,
    )
    if not p0_key_card:
        print("      （本项要求：任一款进主文档 game-frame）", flush=True)
    ag = "PASS" if p0_all_games else "FAIL"
    print(
        f"  2) 各游戏正常运行（进壳+等终局+回厅）                    : {ag}",
        flush=True,
    )
    if not p0_all_games:
        print("      （本项要求：每款进壳、终局、回厅均 PASS）", flush=True)
    co = str(coin_verdict).upper()
    print(
        f"  3) 游戏金币同步（大厅余额粗采样；无稳定数字时 SKIP）    : {co}",
        flush=True,
    )
    if coin_detail:
        print(f"      说明: {coin_detail[:220]}{'…' if len(coin_detail) > 220 else ''}", flush=True)
    print("", flush=True)
    tot = "PASS" if overall else "FAIL"
    print(f"总评（三项均满足规则且金币非 FAIL 时为 PASS）: {tot}", flush=True)
    print("=======================================================", flush=True)


def _mcp_imports():
    from l3_client.local_mcps.kalaroko_monitor.mcp_kalaroko_monitor import (
        _diagnose_and_click_kalaroko_game_entry,
        _evaluate_metrics_with_retry,
        _game_deep_wait_after_goto,
        _goto_resilient,
        _prepare_kalaroko_lobby_after_navigation,
        _tactical_retreat_to_platform_home,
    )

    return (
        _diagnose_and_click_kalaroko_game_entry,
        _evaluate_metrics_with_retry,
        _game_deep_wait_after_goto,
        _goto_resilient,
        _prepare_kalaroko_lobby_after_navigation,
        _tactical_retreat_to_platform_home,
    )


async def _run_one_game_e2e_like(
    page: Any,
    *,
    scen: dict[str, Any],
    home: str,
    log: Callable[[str], None],
) -> dict[str, Any]:
    """
    单款游戏：与 ``execute_playwright_perf_test`` 内非首页场景同一套路（无独立 browser launch）。
    """
    (
        _diagnose_and_click_kalaroko_game_entry,
        _evaluate_metrics_with_retry,
        _game_deep_wait_after_goto,
        _goto_resilient,
        _prepare_kalaroko_lobby_after_navigation,
        _tactical_retreat_to_platform_home,
    ) = _mcp_imports()

    scen = dict(scen)
    scen["start_url"] = home
    name = str(scen.get("name") or "game")
    click_sel = str(scen.get("click_selector") or "").strip()
    if not click_sel:
        return {
            "game_id": name,
            "ok": False,
            "error": "缺少 click_selector",
        }

    entry_wait = str(scen.get("entry_wait_until") or "load")
    click_to = int(scen.get("click_timeout_ms") or 10_000)
    deep_ms = int(scen.get("timeout_ms") or 90_000)
    timeout_ms = max(60_000, deep_ms)

    try:
        await _goto_resilient(page, home, entry_wait, timeout_ms)
    except Exception as e:
        return {"game_id": name, "ok": False, "error": f"goto 大厅: {e}"}

    await _prepare_kalaroko_lobby_after_navigation(page, progress=log)

    ws_times: list[float] = []

    def _on_ws(_ws: Any) -> None:
        try:
            ws_times.append(time.perf_counter())
        except Exception:
            pass

    page.on("websocket", _on_ws)
    ws_times.clear()
    t0 = time.perf_counter()
    err_click = None
    try:
        await _diagnose_and_click_kalaroko_game_entry(
            page,
            click_selector=click_sel,
            click_timeout_ms=click_to,
            scenario_name=name,
            scenario=scen,
            progress=log,
        )
    except Exception as e:
        err_click = str(e)[:500]
    try:
        await _game_deep_wait_after_goto(
            page,
            t0,
            timeout_ms,
            ws_times,
            click_flow=True,
        )
    except Exception as e:
        if not err_click:
            err_click = f"deep_wait: {e}"[:500]

    try:
        page.remove_listener("websocket", _on_ws)
    except Exception:
        pass

    final_url = ""
    try:
        final_url = page.url or ""
    except Exception:
        pass
    shell_ok = "game-frame" in final_url.lower()
    t_end = time.perf_counter()
    real_ms = max(0.0, (t_end - t0) * 1000.0)

    metrics: dict[str, Any] = {}
    if shell_ok and not err_click:
        try:
            m = await _evaluate_metrics_with_retry(page)
            if isinstance(m, dict):
                metrics = {
                    "ttfb_ms": m.get("ttfb_ms"),
                    "fcp_ms": m.get("fcp_ms"),
                }
        except Exception as ex:
            metrics = {"_evaluate_error": str(ex)[:200]}

    entered = bool(shell_ok and not err_click)
    out: dict[str, Any] = {
        "game_id": name,
        "ok": entered,
        "load_status": "success" if entered else "failed",
        "shell_game_frame": shell_ok,
        "final_url": final_url[:400],
        "real_engine_load_ms": round(real_ms, 1),
        "click_error": err_click,
        "metrics": metrics,
    }
    if not shell_ok and not err_click:
        out["error"] = "主文档 URL 未含 game-frame（与 MCP require_game_frame_url 一致）"
    return out


async def _back_to_lobby(
    page: Any,
    *,
    p0: Any,
    target_url: str,
    home: str,
    scen: dict[str, Any],
    log: Callable[[str], None],
) -> tuple[bool, str]:
    _tactical_retreat_to_platform_home = _mcp_imports()[5]
    ok_x, x_note = await p0._p0_exit_game_via_kk_then_exit(page)
    if ok_x:
        t_end = time.monotonic() + 22.0
        while time.monotonic() < t_end:
            if await p0._p0_lobby_seems_visible(page):
                return True, f"KK/Exit: {x_note}"
            await page.wait_for_timeout(450)
    log("  [lobby] KK/Exit 未确认大厅，尝试 MCP 战术撤离…")
    try:
        await _tactical_retreat_to_platform_home(page, scen, progress=log)
    except Exception as e:
        return False, f"撤离失败: {e}"
    try:
        await p0._ensure_on_home_feed(page, target_url, log)  # type: ignore[attr-defined]
    except Exception:
        try:
            await page.goto(home, wait_until="domcontentloaded", timeout=50_000)
        except Exception:
            pass
    if await p0._p0_lobby_seems_visible(page):
        return True, "战术撤离后可见大厅"
    return False, "未确认回大厅（请人工看屏）"


async def _async_main(args: argparse.Namespace) -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("请先安装：pip install playwright && playwright install chromium", file=sys.stderr)
        return 2

    p0 = _load_p0_smoke_module()

    cdp = p0._kalaroko_cdp(args.cdp_http or None)
    target_url = (args.target_url or DEFAULT_TARGET).strip()
    host = p0._host_from_url(target_url)
    home = p0._home_feed_url(target_url)
    all_names = _game_names_from_mcp_defaults()
    to_run = [args.game] if args.single else all_names
    for g in to_run:
        if g not in all_names:
            print(f"[失败] 未知 --game {g!r}", file=sys.stderr)
            return 2

    def log(msg: str) -> None:
        if args.verbose or not args.quiet:
            print(msg, flush=True)

    def progress(msg: str) -> None:
        log(f"  [mcp] {msg}")

    rmin = (
        float(args.round_min_wait_sec)
        if args.round_min_wait_sec is not None
        else _env_float("K11_P0_ROUND_MIN_WAIT_SEC", 20.0)
    )
    rmax = (
        float(args.round_max_wait_sec)
        if args.round_max_wait_sec is not None
        else _env_float("K11_P0_ROUND_MAX_WAIT_SEC", 600.0)
    )
    rmax = max(60.0, rmax)
    log("———————— K11 P0 · 关键卡片 / 多游戏 / 金币（进壳 + 等终局 + 金币）————————")
    log(f"CDP：{cdp}  站点：{home}  本轮游戏：{', '.join(to_run)}")
    log(f"单局等待终局：min={rmin:.0f}s max={rmax:.0f}s poll={args.round_poll_sec:.1f}s")
    log("")

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(cdp)
        navigate_if_no_tab = not getattr(args, "require_existing_tab", False)
        page, pick_err = await p0._acquire_cdp_target_page(
            browser,
            host=host,
            target_url=target_url,
            navigate_if_no_tab=navigate_if_no_tab,
            log=log,
        )
        if page is None:
            print(f"[失败] {pick_err or '无法获取目标页签'}", file=sys.stderr)
            return 2

        ok_env, env_detail = await p0._ensure_target_page(
            page,
            target_url,
            log=log,
            navigate_if_no_tab=navigate_if_no_tab,
            host=host,
        )
        if not ok_env:
            print(f"[失败] {env_detail}", file=sys.stderr)
            return 2

        log("准备：锚定大厅首页…")
        await p0._ensure_on_home_feed(page, target_url, log)
        try:
            if (page.url or "").rstrip("/") != home.rstrip("/"):
                await page.goto(home, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(500)
        except Exception as e:
            log(f"  [warn] {p0._brief_exc(e)}")

        (
            _unused_click,
            _unused_eval,
            _unused_deep,
            _unused_goto,
            _prepare_kalaroko_lobby,
            _unused_tac,
        ) = _mcp_imports()
        await _prepare_kalaroko_lobby(page, progress=progress)

        log("基线：大厅钱包/余额粗采样（金币同步用）…")
        baseline_wallet = await _snapshot_lobby_wallet(page)
        b_num, b_note = _first_number_from_hints(baseline_wallet)

        per_game: list[dict[str, Any]] = []
        any_shell = False
        for gi, gname in enumerate(to_run):
            log(f"—— 游戏 {gi + 1}/{len(to_run)}：{gname} ——")
            scen = _scenario_copy_for_game(gname)
            scen["start_url"] = home

            w_lobby_before = await _snapshot_lobby_wallet(page)
            wb_num, wb_note = _first_number_from_hints(w_lobby_before)

            row = await _run_one_game_e2e_like(
                page, scen=scen, home=home, log=progress
            )
            row["wallet_lobby_before"] = w_lobby_before
            row["wallet_lobby_before_num"] = wb_num
            row["wallet_lobby_before_parse_note"] = wb_note

            if row.get("shell_game_frame") and not row.get("click_error"):
                any_shell = True
                ok_end, end_detail, wall_sec = await _wait_for_natural_round_end(
                    page,
                    log=progress,
                    min_wait_sec=rmin,
                    max_wait_sec=rmax,
                    poll_sec=float(args.round_poll_sec),
                    stable_polls_needed=int(args.round_stable_polls),
                    min_blob_for_stable=int(args.round_min_blob_stable),
                )
                row["round_natural_end"] = ok_end
                row["round_end_detail"] = end_detail
                row["round_wait_wall_sec"] = round(wall_sec, 1)
                try:
                    w_shell = await _snapshot_lobby_wallet(page)
                except Exception as e:
                    w_shell = {"error": str(e)[:200], "hints": []}
                row["wallet_in_shell_after_round"] = w_shell
                ws_num, ws_note = _first_number_from_hints(w_shell)
                row["wallet_in_shell_num"] = ws_num
                row["wallet_in_shell_parse_note"] = ws_note
            else:
                row["round_natural_end"] = False
                row["round_end_detail"] = (
                    "未进 game-frame 或点击阶段异常，跳过等待终局"
                )
                row["round_wait_wall_sec"] = 0.0
                row["wallet_in_shell_after_round"] = None
                row["wallet_in_shell_num"] = None
                row["wallet_in_shell_parse_note"] = ""

            back_ok, back_note = await _back_to_lobby(
                page,
                p0=p0,
                target_url=target_url,
                home=home,
                scen=scen,
                log=progress,
            )

            w_after = await _snapshot_lobby_wallet(page)
            a_num, a_note = _first_number_from_hints(w_after)
            row["back_to_lobby"] = back_ok
            row["back_note"] = back_note
            row["wallet_after_lobby"] = w_after
            row["wallet_number_after_lobby"] = a_num
            row["wallet_number_after_parse_note"] = a_note
            # 兼容旧键名
            row["wallet_after"] = w_after
            row["wallet_number_after"] = a_num

            clv = _coin_line_verdict(
                wb_num,
                a_num,
                round_ok=bool(row.get("round_natural_end")),
                lobby_ok=bool(back_ok),
            )
            if (
                clv == "PASS"
                and wb_num is not None
                and a_num is not None
            ):
                row["coin_delta_lobby_after_game"] = round(a_num - wb_num, 4)
            else:
                row["coin_delta_lobby_after_game"] = None
            row["coin_line_verdict"] = clv
            _finalize_per_game_verdict(row)
            per_game.append(row)
            if not back_ok and gi < len(to_run) - 1:
                log("[WARN] 未稳回大厅，尝试 goto 首页后继续…")
                try:
                    await page.goto(home, wait_until="domcontentloaded", timeout=60_000)
                    await _prepare_kalaroko_lobby(page, progress=progress)
                except Exception as e:
                    log(f"  [warn] 恢复大厅：{e}")

        log("收尾：整轮结束后再采样一次大厅（金币）…")
        try:
            await page.goto(home, wait_until="domcontentloaded", timeout=50_000)
            await _prepare_kalaroko_lobby(page, progress=progress)
        except Exception as e:
            log(f"  [warn] 收尾 goto：{e}")
        final_wallet = await _snapshot_lobby_wallet(page)
        f_num, f_note = _first_number_from_hints(final_wallet)

        # —— 三条 P0 用例结论（与表头对齐） ——
        p0_key_card = any_shell
        p0_all_games = all(
            bool(x.get("shell_game_frame"))
            and bool(x.get("round_natural_end"))
            and bool(x.get("back_to_lobby"))
            for x in per_game
        )

        line_vs = [str(x.get("coin_line_verdict") or "SKIP") for x in per_game]
        if "FAIL" in line_vs:
            coin_verdict = "FAIL"
            coin_detail = "；".join(
                f"{x.get('game_id')!s}:{x.get('coin_line_verdict')!s}" for x in per_game
            )
        elif all(v == "SKIP" for v in line_vs):
            coin_verdict = "SKIP"
            coin_detail = "逐局大厅余额均未能解析为数字，或该局未完局（见 per_game.coin_line_verdict）"
        else:
            coin_verdict = "PASS"
            parts = []
            for x in per_game:
                d = x.get("coin_delta_lobby_after_game")
                gid = x.get("game_id")
                if d is not None:
                    parts.append(f"{gid} Δ={d}")
            whole_delta = None
            if b_num is not None and f_num is not None:
                whole_delta = round(f_num - b_num, 4)
            coin_detail = (
                "逐局（本局后大厅-本局前大厅）：" + ("；".join(parts) if parts else "无 delta")
            )
            if whole_delta is not None:
                coin_detail += f"。整轮基线→收尾大厅 Δ={whole_delta}"

        if b_num is not None and f_num is None and coin_verdict != "FAIL":
            coin_verdict = "FAIL"
            coin_detail = f"基线有数字 {b_num} 但整轮结束仍无法解析大厅余额"

        overall = p0_key_card and p0_all_games and (coin_verdict in ("PASS", "SKIP"))
        if coin_verdict == "FAIL":
            overall = False

        k11_p0 = {
            "p0_key_card_click": {
                "id": "p0_key_card_click",
                "source_row_md": "P0 关键卡片点击",
                "check": "至少 1 个核心游戏卡片点击后可正常进入下一层页面/流程",
                "verdict": "PASS" if p0_key_card else "FAIL",
                "detail": "任一款主文档 URL 已含 game-frame" if p0_key_card else "无任何一款进入 game-frame",
            },
            "p0_all_games_normal": {
                "id": "p0_all_games_normal",
                "source_row_md": "P0 各游戏正常运行",
                "check": "应保证每个游戏正常能够开局和完成一局的游戏内容",
                "automation_scope": (
                    "进 game-frame 后轮询主/子 frame 终局类文案，或长稳兜底；不操作手牌。超时调 "
                    "K11_P0_ROUND_MAX_WAIT_SEC / --round-max-wait-sec。"
                ),
                "verdict": "PASS" if p0_all_games else "FAIL",
                "detail": f"本轮 {len(to_run)} 款均进壳+终局+回厅"
                if p0_all_games
                else "存在进壳/终局/回厅任一项失败",
                "per_game_pass": {str(x.get("game_id") or ""): str(x.get("verdict") or "FAIL") for x in per_game},
            },
            "p0_coin_sync": {
                "id": "p0_coin_sync",
                "source_row_md": "P0 游戏金币同步",
                "check": "保证每个游戏游玩后，金币的扣除和获取没有异常",
                "automation_scope": "每局前后在大厅采余额；能解析则比对 Δ；否则 SKIP/人工。",
                "verdict": coin_verdict,
                "detail": coin_detail,
                "per_game_coin": {
                    str(x.get("game_id") or ""): str(x.get("coin_line_verdict") or "SKIP")
                    for x in per_game
                },
            },
        }

        out: dict[str, Any] = {
            "schema": "k11_p0_key_card_kalaroko_playwright/v4",
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "cdp": cdp,
            "target_url": target_url,
            "home_url": home,
            "mode": "single" if args.single else "all_default_games",
            "games": to_run,
            "k11_p0_smoke": k11_p0,
            "mvp_docs": {
                "p0_key_card_click": {
                    "verdict": "PASS" if p0_key_card else "FAIL",
                    "detail": "至少 1 款进入主文档 game-frame" if p0_key_card else "无 game-frame",
                },
                "p0_all_games_runnable": {
                    "verdict": "PASS" if p0_all_games else "FAIL",
                    "detail": f"本轮 {len(to_run)} 款：进壳+终局+回厅全成功"
                    if p0_all_games
                    else "存在进壳/终局/回厅未全部成功",
                },
                "p0_coin_sync_smoke": {
                    "verdict": coin_verdict,
                    "detail": coin_detail,
                },
            },
            "per_game": per_game,
            "wallet_baseline": baseline_wallet,
            "wallet_baseline_number": b_num,
            "wallet_final": final_wallet,
            "wallet_final_number": f_num,
            "verdict": "PASS" if overall else "FAIL",
            "verdict_summary": {
                "per_game": {str(x.get("game_id") or ""): str(x.get("verdict") or "FAIL") for x in per_game},
                "k11_p0": {k: v.get("verdict") for k, v in k11_p0.items()},
                "total": "PASS" if overall else "FAIL",
            },
        }

        if args.json_out:
            outp = Path(args.json_out)
            outp.parent.mkdir(parents=True, exist_ok=True)
            outp.write_text(
                json.dumps(out, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            log(f"JSON：{outp.resolve()}")

        _print_k11_p0_report(
            per_game=per_game,
            p0_key_card=p0_key_card,
            p0_all_games=p0_all_games,
            coin_verdict=coin_verdict,
            coin_detail=coin_detail,
            overall=overall,
        )
        if overall:
            return 0
        return 1


def main() -> int:
    games = _game_names_from_mcp_defaults()
    ap = argparse.ArgumentParser(
        description="K11 P0：MCP 同源 E2E 子流程（关键卡片+多游戏+金币粗样）"
    )
    ap.add_argument("--target-url", default=DEFAULT_TARGET, help="站点根，用于 host 与大厅根路径")
    ap.add_argument("--cdp-http", default="", help="覆盖 KALAROKO_CDP_ENDPOINT")
    ap.add_argument(
        "--game",
        default="tongits_king",
        choices=games,
        help="与 KALAROKO_DEFAULT_SCENARIOS 一致；配合 --single 只跑一款",
    )
    ap.add_argument(
        "--single",
        action="store_true",
        help="只跑 --game 指定的一款（调试用）；默认 false=顺序跑满三款",
    )
    ap.add_argument(
        "--require-existing-tab",
        action="store_true",
        help="必须已有含目标域页签；默认允许自动 goto",
    )
    ap.add_argument(
        "--round-min-wait-sec",
        type=float,
        default=None,
        help="进壳后最短等待再轮询终局（默认 env K11_P0_ROUND_MIN_WAIT_SEC 或 20）",
    )
    ap.add_argument(
        "--round-max-wait-sec",
        type=float,
        default=None,
        help="单局等待对局自然结束的最长时间秒（默认 env K11_P0_ROUND_MAX_WAIT_SEC 或 600）",
    )
    ap.add_argument(
        "--round-poll-sec",
        type=float,
        default=2.8,
        help="终局轮询间隔秒",
    )
    ap.add_argument(
        "--round-stable-polls",
        type=int,
        default=8,
        help="正文哈希连续相同多少次视为 Canvas 长稳兜底（防误报可加大）",
    )
    ap.add_argument(
        "--round-min-blob-stable",
        type=int,
        default=600,
        help="长稳兜底要求的最小聚合正文字数",
    )
    ap.add_argument("--json-out", type=Path, default=None)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    try:
        return asyncio.run(_async_main(args))
    except Exception as e:
        print(f"[失败] {type(e).__name__}: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
