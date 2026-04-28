#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从已移除的 ``test_k11_p0_key_card_kalaroko_playwright.py`` 抽出的壳层/MCP 辅助函数，
供 ``test_k11_extra_games_shell_smoke_playwright.py`` 等引用；避免重复维护大脚本。
"""
from __future__ import annotations

import time
from typing import Any, Callable


def _mcp_imports() -> tuple:
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


async def _gather_frame_texts_for_end_probe(
    page: Any, max_total: int = 16000
) -> str:
    """聚合主文档 + 子 frame 的 innerText，供局终关键词/状态粗测。"""
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


async def _run_one_game_e2e_like(
    page: Any,
    *,
    scen: dict[str, Any],
    home: str,
    log: Callable[[str], None],
    shell_phase_timeout_ms: int | None = None,
) -> dict[str, Any]:
    """单游戏：goto 大厅 → prepare → 点卡 → deep_wait；与旧 P0 key 脚本同路径。"""
    (
        _diagnose_and_click_kalaroko_game_entry,
        _evaluate_metrics_with_retry,
        _game_deep_wait_after_goto,
        _goto_resilient,
        _prepare_kalaroko_lobby_after_navigation,
        _,
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
    if shell_phase_timeout_ms is not None:
        timeout_ms = max(60_000, int(shell_phase_timeout_ms), deep_ms)
    else:
        timeout_ms = max(60_000, deep_ms)

    try:
        await _goto_resilient(page, home, entry_wait, timeout_ms)
    except Exception as e:
        return {"game_id": name, "ok": False, "error": f"goto 失败: {e}"}

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
    return False, "未确认回到大厅（需人工检查）"
