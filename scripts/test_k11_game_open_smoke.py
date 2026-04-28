#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K11 极速开门探活（Smoke Test）

目标：
- 仅验证 5 款指定游戏能否快速成功加载进桌。
- 不进行对局操作、不做金币结算。
- 复用 mcp_kalaroko_monitor 的极速引擎探针，命中晚期 UI / 资源静默 / 后置 API 即可判定上桌。
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

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

TARGET_HOME = "https://www.herontest.xin/"
SCHEMA = "k11_game_open_smoke/v1"
K11_DEFAULT_LARK_WIKI_URL = (
    "https://ssgkm409t6q5.sg.larksuite.com/wiki/ZyWlwhdW1iNQuykvy7qlw93sgTe"
)
os.environ.setdefault(
    "KALAROKO_MONITOR_ALLOWED_HOSTS",
    "herontest.xin,www.herontest.xin,gweb.herontest.xin",
)


@dataclass(frozen=True)
class GameCase:
    game_id: str
    title: str
    click_selector: str
    extra_lobby_click: str | None = None


GAME_CASES: tuple[GameCase, ...] = (
    GameCase("bingo_showdown", "Bingo Showdown", "text=/Bingo\\s*Showdown/i >> xpath=.."),
    GameCase(
        "infinity_9_ball",
        "Infinity 9 Ball",
        "text=/Infinity\\s*9\\s*Ball/i >> xpath=..",
        extra_lobby_click="text=Join",
    ),
    GameCase("color_blitz_social", "Color Blitz Social", "text=/Color\\s*Blitz\\s*Social/i >> xpath=.."),
    GameCase("royal_pusoy", "Royal Pusoy", "text=/Royal\\s*Pusoy/i >> xpath=.."),
    GameCase("drama_crush", "Drama Crush", "text=/Drama\\s*Crush/i >> xpath=.."),
)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _gprint(msg: str, sink: Callable[[str], None] | None) -> None:
    """统一日志出口：统合脚本传入 log 时走控制台回调，否则 print。"""
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
    u = (url or "").lower()
    return ("game-frame" in u) or ("gameid=" in u)


def _resolve_k11_lark_smoke_report_path() -> Path:
    return ROOT / "scripts" / "k11_lark_smoke_report.py"


def _to_lark_results(per_game: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in per_game:
        v = str(row.get("verdict") or "FAIL").upper()
        if v == "PASS":
            vzh = "通过"
        elif v == "SKIP":
            vzh = "跳过"
        else:
            vzh = "失败"
        out.append(
            {
                "tier": "P0",
                "case": str(row.get("game_id") or ""),
                "case_title_zh": str(row.get("game_title") or row.get("game_id") or ""),
                "verdict": v,
                "verdict_zh": vzh,
                "detail": str(row.get("detail") or ""),
            }
        )
    return out


def _send_lark_notification_for_open_smoke(
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
        log(f"  [lark] 发送通知异常（已忽略，不阻断结果）：{e}")


async def _click_entry_with_fallback(
    page: Any,
    case: GameCase,
    *,
    progress: Any,
) -> tuple[bool, str]:
    """
    入口点击容错：
    1) 先用主选择器点击
    2) 若未命中，分段下滑后用标题宽松匹配重试
    """
    try:
        await mcp._diagnose_and_click_kalaroko_game_entry(
            page,
            click_selector=case.click_selector,
            click_timeout_ms=12_000,
            scenario_name=case.title,
            scenario={"name": case.game_id, "start_url": TARGET_HOME},
            progress=progress,
        )
        return True, ""
    except Exception as first_err:
        # 已进框架时立即判成功，避免 fallback 继续在大厅选择器上空转
        try:
            if _is_open_success(str(page.url or "")):
                return True, "primary click errored but URL already in game-frame"
        except Exception:
            pass
        title_pat = case.title.replace(" ", r"\s*")
        fallback_selectors = [
            f"text=/{title_pat}/i",
            f"text=/{case.title.split(' ')[0]}.*{case.title.split(' ')[-1]}/i",
        ]
        for step in range(4):
            try:
                if _is_open_success(str(page.url or "")):
                    return True, f"entered game-frame during fallback step {step + 1}"
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
                try:
                    await mcp._diagnose_and_click_kalaroko_game_entry(
                        page,
                        click_selector=sel,
                        click_timeout_ms=8_000,
                        scenario_name=f"{case.title}#fallback{step+1}",
                        scenario={"name": case.game_id, "start_url": TARGET_HOME},
                        progress=progress,
                    )
                    return True, f"fallback selector={sel}"
                except Exception:
                    try:
                        if _is_open_success(str(page.url or "")):
                            return True, f"entered game-frame after fallback selector={sel}"
                    except Exception:
                        pass
                    continue
        return False, f"{type(first_err).__name__}: {str(first_err)[:200]}"


async def _soft_click_start_or_play(page: Any, *, total_budget_sec: float = 2.6) -> tuple[bool, str]:
    """
    非阻塞盲狙 Start/Play：
    - 总预算极短（默认 2.6s）
    - 主文档 + 全部子 frame 尝试
    - 任意异常吞掉，绝不抛出影响主流程
    """
    selectors = (
        "text=/^(Start|Play|开始游戏|Tap\\s*to\\s*start)$/i",
        "text=/Tap\\s*to\\s*start/i",
        "button:has-text('Start')",
        "button:has-text('Play')",
        '[role="button"]:has-text("Start")',
        '[role="button"]:has-text("Play")',
        "text=/\\b(Start|Play)\\b/i",
    )
    deadline = time.perf_counter() + max(0.4, float(total_budget_sec))
    try:
        frames = [page.main_frame] + [f for f in page.frames if f != page.main_frame]
    except Exception:
        frames = [page.main_frame]
    for fr in frames:
        if time.perf_counter() >= deadline:
            break
        for sel in selectors:
            if time.perf_counter() >= deadline:
                break
            try:
                left_ms = int(max(120, (deadline - time.perf_counter()) * 1000))
                loc = fr.locator(sel).first
                if await loc.count() <= 0:
                    continue
                if not await loc.is_visible(timeout=min(700, left_ms)):
                    continue
                await loc.click(timeout=min(1200, left_ms), no_wait_after=True)
                return True, f"命中选择器 {sel!r}"
            except Exception:
                continue
    return False, "未发现明显 Start/Play 按钮（已跳过）"


async def _soft_click_join_if_present(
    page: Any, *, total_budget_sec: float = 6.0, sink: Callable[[str], None] | None = None
) -> tuple[bool, str]:
    """
    Join 饱和打击（稳定版）：
    1) 优先确保有可点击筹码（One Round 区域）
    2) 选取“面积最大且可见”的 Join 实体，避免命中文字子节点
    3) 物理微抖动 + JS 事件链双发，并带短周期重试
    """
    deadline = time.perf_counter() + max(1.0, float(total_budget_sec))
    _gprint(f"   [饱和打击] 启动全频谱侦察，预算: {total_budget_sec}s", sink)

    while time.perf_counter() < deadline:
        try:
            # 先扫可见的筹码按钮并点一个，避免 Join 进入 loading 但后端拒绝
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

            # 聚合 Join 候选，优先按钮语义，再退回精确文本
            candidates = [page.get_by_role("button", name="Join"), page.get_by_text("Join", exact=True)]
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

                # CDP mouse.* 在部分页面/帧上会长时间阻塞；整段用 asyncio.wait_for 卡死上限。
                strike_budget = min(4.2, max(0.6, deadline - time.perf_counter()))
                strike_end = time.perf_counter() + strike_budget

                async def _one_round_strike() -> None:
                    try:
                        await asyncio.wait_for(page.mouse.up(), timeout=0.4)
                    except Exception:
                        pass
                    try:
                        await asyncio.wait_for(
                            best_btn.click(
                                timeout=1800, force=True, no_wait_after=True
                            ),
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


async def _run_single_game(
    page: Any,
    case: GameCase,
    *,
    verbose: bool,
    sink: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    game_start = time.perf_counter()
    ws_times: list[float] = []
    race_reason = ""
    detail = ""
    verdict = "FAIL"

    def progress(msg: str) -> None:
        if verbose:
            _gprint(f"  [mcp] {msg}", sink)

    try:
        await mcp._goto_resilient(page, TARGET_HOME, "domcontentloaded", 20_000)
        await mcp._prepare_kalaroko_lobby_after_navigation(page, progress=progress)
        click_err = ""
        clicked, click_note = await _click_entry_with_fallback(page, case, progress=progress)
        if not clicked:
            click_err = click_note
            try:
                u_now = str(page.url or "")
            except Exception:
                u_now = ""
            if not _is_open_success(u_now):
                raise RuntimeError(click_note)
        elif click_note:
            click_err = click_note
        # 统一规则：只要出现 Join（如 Select Coins）就点，不区分具体游戏。
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
            timeout_ms=60_000,
            ws_times=ws_times,
            click_flow=True,
        )

        cur_url = ""
        try:
            cur_url = str(page.url or "")
        except Exception:
            cur_url = ""

        open_ok = _is_open_success(cur_url)
        if open_ok:
            verdict = "PASS"
            if race_reason == "timeout":
                detail = "进框架但停表超时（timeout 兜底结束）"
            else:
                detail = f"极速判定上桌 (停表依据: {race_reason or 'unknown'})"
            if click_err:
                detail += f" | 入口点击告警但已进框架: {click_err}"
            if clicked_join:
                detail += " | 已自动点击 [Join] 入场按钮"
            _gprint("   - 嗅探 [Start/Play] 按钮...", sink)
            clicked_start, click_note = await _soft_click_start_or_play(page, total_budget_sec=2.6)
            if clicked_start:
                detail += " | 已自动点击 [Start/Play] 按钮"
                if verbose:
                    _gprint(f"   -> 成功点击: {click_note}", sink)
            else:
                if verbose:
                    _gprint(f"   -> {click_note}", sink)
        else:
            verdict = "FAIL"
            detail = "未进入 game-frame/gameId 路径"
    except Exception as e:
        verdict = "FAIL"
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
                if verdict == "PASS":
                    detail = f"{detail}; 撤离异常后强刷失败: {type(refresh_err).__name__}"

    load_ms = round((time.perf_counter() - game_start) * 1000.0, 1)
    return {
        "game_id": case.game_id,
        "game_title": case.title,
        "verdict": verdict,
        "load_ms": load_ms,
        "detail": detail,
        "race_end_reason": race_reason or "unknown",
    }


async def run_game_open_smoke_on_existing_page(
    page: Any,
    *,
    verbose: bool = True,
    log: Callable[[str], None] | None = None,
    single_game: str = "",
    cases: list[GameCase] | None = None,
) -> list[dict[str, Any]]:
    """
    在已由 Playwright 打开的 ``page`` 上顺序跑游戏开门探活（不重连 CDP、不新建浏览器）。
    供 ``test_k11_unified_platform_smoke_playwright`` 在浏览器兼容之后并入同一次飞书卡片。

    ``cases`` 非空时直接使用（与已打印的用例列表一致）；否则按 ``single_game`` 解析。
    """
    if "kalaroko.com" in TARGET_HOME:
        _gprint("[失败] 目标域名配置错误：禁止使用 kalaroko.com", log)
        return []

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
        _gprint(f"========== 游戏探查 [{idx}/{total_games}] {case.title} ==========", log)
        row = await _run_single_game(page, case, verbose=verbose, sink=log)
        per_game.append(row)
        load_sec = float(row["load_ms"]) / 1000.0
        mark = "✓" if row["verdict"] == "PASS" else "✗"
        _gprint(
            f"[{mark}] {case.title} -> {row['verdict']} ({load_sec:.2f}s) | {row['detail']}",
            log,
        )
        _gprint(
            "  [探查明细] "
            f"game_id={row.get('game_id')} | "
            f"race_end_reason={row.get('race_end_reason')} | "
            f"load_ms={row.get('load_ms')}",
            log,
        )
        _gprint("===========================================================", log)
    return per_game


async def _async_main(args: argparse.Namespace) -> int:
    # 明确锁定测试服域名，避免误打生产域
    if "kalaroko.com" in TARGET_HOME:
        print("[失败] 目标域名配置错误：禁止使用 kalaroko.com", file=sys.stderr)
        return 2

    try:
        selected = _pick_cases(args.single_game)
    except ValueError as e:
        print(f"[失败] {e}", file=sys.stderr)
        return 2

    per_game: list[dict[str, Any]] = []
    browser: Any = None
    context: Any = None
    must_close_context = False

    print("———————— K11 极速开门探活（herontest）————————", flush=True)
    print(f"目标站点: {TARGET_HOME}", flush=True)
    print("测试游戏: " + ", ".join(c.title for c in selected), flush=True)
    print("", flush=True)

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("请先安装：pip install playwright && playwright install chromium", file=sys.stderr)
        return 2

    try:
        async with async_playwright() as p:
            preferred_host = "www.herontest.xin"
            browser, context, page, must_close_context = await mcp._launch_kalaroko_browser_context(
                p,
                viewport_width=459,
                viewport_height=851,
                device_scale_factor=2.0,
                headless=bool(args.headless),
                preferred_host=preferred_host,
            )

            per_game = await run_game_open_smoke_on_existing_page(
                page,
                verbose=bool(args.verbose),
                log=None,
                cases=selected,
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

    total_pass = all(x.get("verdict") == "PASS" for x in per_game) and bool(per_game)
    total_verdict = "PASS" if total_pass else "FAIL"
    total_mark = "✓" if total_pass else "✗"
    print("", flush=True)
    print(f"[大盘总评]: {total_mark} {total_verdict}", flush=True)

    out = {
        "schema": SCHEMA,
        "captured_at": _utc_iso(),
        "target_url": TARGET_HOME,
        "verdict": total_verdict,
        "verdict_summary": {
            "per_game": {x["game_id"]: x["verdict"] for x in per_game},
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
        _send_lark_notification_for_open_smoke(
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
    ap = argparse.ArgumentParser(description="K11 测试服 5 款游戏极速开门探活")
    ap.add_argument(
        "--single-game",
        default="",
        help="仅测试单款游戏（填写 game_id，如 royal_pusoy）",
    )
    ap.add_argument("--json-out", type=Path, default=None, help="写出 JSON 结果文件")
    ap.add_argument("--headless", action="store_true", help="使用无头模式")
    ap.add_argument(
        "--no-lark-report",
        action="store_true",
        help="只跑本地探活，不发飞书消息卡片",
    )
    ap.add_argument(
        "--lark-wiki-url",
        default="",
        help=f"飞书 Wiki 链接（卡片展示用；默认环境 K11_SMOKE_LARK_WIKI_URL 或 {K11_DEFAULT_LARK_WIKI_URL}）",
    )
    ap.add_argument("-v", "--verbose", action="store_true", help="输出 MCP 过程日志")
    return ap


def main() -> int:
    # 继承仓库已有 .env 约定（若存在）
    try:
        from dotenv import load_dotenv

        root = Path(__file__).resolve().parent.parent
        env_path = root / ".env"
        if env_path.exists():
            load_dotenv(env_path, encoding="utf-8")
    except Exception:
        pass

    args = _build_parser().parse_args()
    try:
        return asyncio.run(_async_main(args))
    except KeyboardInterrupt:
        print("\n[中断] 用户取消执行", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
