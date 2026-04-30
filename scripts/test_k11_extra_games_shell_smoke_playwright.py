#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K11 · 扩展游戏壳层轻量冒烟（不进完整对局、不测金币）

仿照 P0 平台脚本 + ``k11_kalaroko_shell_helpers`` 的 CDP 连接、大厅准备、
MCP 同源点击进 ``game-frame``、KK/Exit 或战术撤离回大厅；**不**等待牌局自然结束，
**不**采样大厅余额。

对每款游戏：进入主文档 ``game-frame`` 后**极短**停留，用「主文档+子 frame 聚合正文长度」
或「存在 canvas」作粗判显示正常，然后**尽快**回大厅；不测金币、不等终局。

**及时退出**：本脚本把 MCP ``_game_deep_wait_after_goto`` 的预算压到 ``--deep-wait-ms``（默认 10s），
避免产品默认 ~80s 晚期竞速空转；回大厅对 ``_back_to_lobby`` 套 ``--lobby-exit-budget-sec``，超时则
直接战术撤离 + 回首页，避免卡在 KK/Exit。

默认游戏（大厅卡片文案，改版时可改 ``EXTRA_GAMES_SCENARIOS``）：
  Bingo Showdown · Infinity 9 Ball · Color Blitz Social · Royal Pusoy

前置：``KALAROKO_CDP_ENDPOINT`` 或 ``--cdp-http``；``pip install playwright``

用法（仓库根）::

  python scripts/test_k11_extra_games_shell_smoke_playwright.py
  python scripts/test_k11_extra_games_shell_smoke_playwright.py --single bingo_showdown
  python scripts/test_k11_extra_games_shell_smoke_playwright.py --deep-wait-ms 12000 --settle-ms 2500 -v

退出码：0 全部 PASS；1 有 FAIL；2 环境/CDP；3 未捕获异常。
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import sys
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

# 明显异常页文案（启发式）
_SHELL_BAD_RE = re.compile(
    r"(?:\b404\b|page\s+not\s+found|access\s+denied|network\s*error|"
    r"failed\s+to\s+load|something\s+went\s+wrong|请稍后重试|加载失败|网络异常)",
    re.I,
)


def _load_p0_key_module() -> Any:
    path = ROOT / "scripts" / "k11_kalaroko_shell_helpers.py"
    spec = importlib.util.spec_from_file_location("k11_kalaroko_shell_helpers", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _extra_game_ids() -> list[str]:
    return [str(s["name"]) for s in EXTRA_GAMES_SCENARIOS]


def _scenario_by_id(game_id: str) -> dict[str, Any]:
    for s in EXTRA_GAMES_SCENARIOS:
        if str(s.get("name")) == game_id:
            return dict(s)
    raise ValueError(
        f"未知游戏 {game_id!r}，可选：{', '.join(_extra_game_ids())}"
    )


# 与 MCP 游戏项同形；仅用于本脚本的扩展列表（未写入 KALAROKO_DEFAULT_SCENARIOS）
EXTRA_GAMES_SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "name": "bingo_showdown",
        "start_url": DEFAULT_TARGET,
        "click_selector": r"text=/Bingo\s*Showdown/i",
        "prefer_last_on_ambiguous_entry": True,
        "entry_wait_until": "domcontentloaded",
        "click_timeout_ms": 9000,
        "wait_until": "domcontentloaded",
        "timeout_ms": 22_000,
    },
    {
        "name": "infinity_9_ball",
        "start_url": DEFAULT_TARGET,
        "click_selector": r"text=/Infinity\s*9\s*Ball/i",
        "prefer_last_on_ambiguous_entry": True,
        "entry_wait_until": "domcontentloaded",
        "click_timeout_ms": 9000,
        "wait_until": "domcontentloaded",
        "timeout_ms": 22_000,
    },
    {
        "name": "color_blitz_social",
        "start_url": DEFAULT_TARGET,
        "click_selector": r"text=/Color\s*Blitz\s*Social/i",
        "prefer_last_on_ambiguous_entry": True,
        "entry_wait_until": "domcontentloaded",
        "click_timeout_ms": 9000,
        "wait_until": "domcontentloaded",
        "timeout_ms": 22_000,
    },
    {
        "name": "royal_pusoy",
        "start_url": DEFAULT_TARGET,
        "click_selector": r"text=/Royal\s*Pusoy/i",
        "prefer_last_on_ambiguous_entry": True,
        "entry_wait_until": "domcontentloaded",
        "click_timeout_ms": 9000,
        "wait_until": "domcontentloaded",
        "timeout_ms": 22_000,
    },
)


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


async def _back_to_lobby_with_budget(
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
    """
    先走 KK/Exit → 战术撤离；整段超过 budget_sec 则强制战术撤离，避免长时间卡在局内。
    """
    _prepare_kalaroko_lobby = p0k._mcp_imports()[4]
    _tactical_retreat_to_platform_home = p0k._mcp_imports()[5]
    if budget_sec <= 0:
        return await p0k._back_to_lobby(
            page,
            p0=p0,
            target_url=target_url,
            home=home,
            scen=scen,
            log=log,
        )
    try:
        return await asyncio.wait_for(
            p0k._back_to_lobby(
                page,
                p0=p0,
                target_url=target_url,
                home=home,
                scen=scen,
                log=log,
            ),
            timeout=budget_sec,
        )
    except asyncio.TimeoutError:
        log(
            f"  [lobby] 常规退出超过 {budget_sec:.0f}s，强制战术撤离…",
        )
        try:
            await _tactical_retreat_to_platform_home(page, scen, progress=log)
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
            await _prepare_kalaroko_lobby(page, progress=log)
        except Exception:
            pass
        if await p0._p0_lobby_seems_visible(page):
            return True, "常规退出超时后战术撤离，大厅可见"
        return False, "常规退出超时且战术撤离后仍未确认大厅"


def _finalize_row(row: dict[str, Any]) -> None:
    ok = (
        bool(row.get("shell_game_frame"))
        and bool(row.get("display_ok"))
        and bool(row.get("back_to_lobby"))
    )
    row["verdict"] = "PASS" if ok else "FAIL"
    parts = [
        "已进 game-frame" if row.get("shell_game_frame") else "未进 game-frame",
        "显示粗检通过" if row.get("display_ok") else f"显示未过: {row.get('display_note', '')}",
        "已回大厅" if row.get("back_to_lobby") else "未确认回大厅",
    ]
    row["verdict_detail"] = "；".join(parts)


async def _async_main(args: argparse.Namespace) -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print(
            "请先安装：pip install playwright && playwright install chromium",
            file=sys.stderr,
        )
        return 2

    p0 = _load_p0_smoke_module()
    p0k = _load_p0_key_module()

    cdp = p0._kalaroko_cdp(args.cdp_http or None)
    target_url = (args.target_url or DEFAULT_TARGET).strip()
    host = p0._host_from_url(target_url)
    home = p0._home_feed_url(target_url)

    ids = _extra_game_ids()
    to_run = [args.game] if args.single else list(ids)
    for g in to_run:
        if g not in ids:
            print(f"[失败] 未知 --game {g!r}，可选 {ids}", file=sys.stderr)
            return 2

    def log(msg: str) -> None:
        if args.verbose or not args.quiet:
            print(msg, flush=True)

    def progress(msg: str) -> None:
        log(f"  [mcp] {msg}")

    log("———————— K11 · 扩展游戏壳层轻量冒烟（无金币 / 无等终局 / 短等+快退）————————")
    log(f"CDP：{cdp}  站点：{home}  游戏：{', '.join(to_run)}")
    log(
        f"MCP 进壳晚期竞速上限 deep_wait_ms={args.deep_wait_ms}  "
        f"进壳后 settle_ms={args.settle_ms}  正文阈值≥{args.min_text_chars}  "
        f"回大厅预算 {args.lobby_exit_budget_sec:.0f}s"
    )
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

        _prepare_kalaroko_lobby = p0k._mcp_imports()[4]
        await _prepare_kalaroko_lobby(page, progress=progress)

        per_game: list[dict[str, Any]] = []
        for gi, gid in enumerate(to_run):
            log(f"—— 游戏 {gi + 1}/{len(to_run)}：{gid} ——")
            scen = _scenario_by_id(gid)
            scen["start_url"] = home
            # 压短 _game_deep_wait_after_goto 阶段 2（默认 race 可达 ~80s）；timeout_ms 与全局 deadline 对齐
            dw = max(5_000, int(args.deep_wait_ms))
            scen["timeout_ms"] = dw

            row = await p0k._run_one_game_e2e_like(page, scen=scen, home=home, log=progress)

            disp_ok = False
            disp_note = ""
            if row.get("shell_game_frame") and not row.get("click_error"):
                disp_ok, disp_note = await _shell_display_seems_ok(
                    page,
                    p0k,
                    settle_ms=int(args.settle_ms),
                    min_text_chars=int(args.min_text_chars),
                    log=progress,
                )
            else:
                disp_note = "未进壳或点击异常，跳过显示检测"

            row["display_ok"] = disp_ok
            row["display_note"] = disp_note

            back_ok, back_note = await _back_to_lobby_with_budget(
                page=page,
                p0=p0,
                p0k=p0k,
                target_url=target_url,
                home=home,
                scen=scen,
                log=progress,
                budget_sec=float(args.lobby_exit_budget_sec),
            )
            row["back_to_lobby"] = back_ok
            row["back_note"] = back_note

            _finalize_row(row)
            per_game.append(row)

            if not back_ok and gi < len(to_run) - 1:
                log("[WARN] 未稳回大厅，尝试 goto 首页后继续…")
                try:
                    await page.goto(home, wait_until="domcontentloaded", timeout=60_000)
                    await _prepare_kalaroko_lobby(page, progress=progress)
                except Exception as e:
                    log(f"  [warn] 恢复大厅：{e}")

        try:
            await page.goto(home, wait_until="domcontentloaded", timeout=50_000)
            await _prepare_kalaroko_lobby(page, progress=progress)
        except Exception as e:
            log(f"  [warn] 收尾 goto：{e}")

        overall = all(str(x.get("verdict")) == "PASS" for x in per_game)

        out: dict[str, Any] = {
            "schema": "k11_extra_games_shell_smoke_playwright/v2",
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "cdp": cdp,
            "target_url": target_url,
            "home_url": home,
            "timing_policy": {
                "deep_wait_ms": int(args.deep_wait_ms),
                "settle_ms": int(args.settle_ms),
                "lobby_exit_budget_sec": float(args.lobby_exit_budget_sec),
            },
            "mode": "single" if args.single else "all_extra_games",
            "games": to_run,
            "per_game": per_game,
            "verdict": "PASS" if overall else "FAIL",
            "verdict_summary": {
                str(x.get("game_id") or ""): str(x.get("verdict") or "FAIL")
                for x in per_game
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

        print("", flush=True)
        print("========== K11 · 扩展游戏壳层冒烟 ==========", flush=True)
        for x in per_game:
            g = str(x.get("game_id") or "")
            v = str(x.get("verdict") or "FAIL")
            mark = "[PASS]" if v == "PASS" else "[FAIL]"
            print(f"  {mark}  {g:22}  {x.get('verdict_detail', '')}", flush=True)
        print("", flush=True)
        tot = "PASS" if overall else "FAIL"
        print(f"总评: {tot}", flush=True)
        print("===========================================", flush=True)

        return 0 if overall else 1


def _load_p0_smoke_module() -> Any:
    path = ROOT / "scripts" / "test_k11_p0_platform_smoke_playwright.py"
    spec = importlib.util.spec_from_file_location("k11_p0_platform_smoke_playwright", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ids = _extra_game_ids()
    ap = argparse.ArgumentParser(
        description="K11：扩展游戏进壳 + 显示粗检 + 回大厅（不测金币、不等终局）"
    )
    ap.add_argument("--target-url", default=DEFAULT_TARGET)
    ap.add_argument("--cdp-http", default="")
    ap.add_argument(
        "--game",
        default=ids[0],
        choices=ids,
        help="配合 --single 只跑一款",
    )
    ap.add_argument("--single", action="store_true")
    ap.add_argument("--require-existing-tab", action="store_true")
    ap.add_argument(
        "--deep-wait-ms",
        type=int,
        default=10_000,
        help="MCP 点击后进壳的「晚期竞速」预算毫秒（愈小愈快进入显示检测与退出；过小可能偶发未就绪）",
    )
    ap.add_argument(
        "--settle-ms",
        type=int,
        default=2200,
        help="已进入 game-frame 后额外等待毫秒再聚合正文/canvas 检测（愈短愈快回大厅）",
    )
    ap.add_argument(
        "--lobby-exit-budget-sec",
        type=float,
        default=26.0,
        help="KK/Exit+战术撤离整段超时秒数，超时则强制战术撤离，避免卡在局内",
    )
    ap.add_argument(
        "--min-text-chars",
        type=int,
        default=100,
        help="主+子 frame 聚合正文至少多少字视为「有内容」（否则尝试 canvas 兜底）",
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
