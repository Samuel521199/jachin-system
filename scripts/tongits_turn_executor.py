#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tongits 回合执行。

Fast Mode（默认）：OpenCV 盲摸/吃 → 单次手牌 VLM → 本地规则 Dump。
Full Mode：多步 VLM 识阶段 + Group/Drop + Dump。
"""
from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("tongits_turn_executor")

# 1920×1080 固定按钮（可用环境变量覆盖）
_DEFAULT_BUTTONS: dict[str, tuple[int, int]] = {
    "deck": (859, 412),
    "discard": (1010, 415),  # 吃牌 = 点弃牌堆顶牌（无独立 Special 按钮）
    "special": (1010, 415),  # 与 discard 同坐标，兼容规则引擎
    "drop": (518, 726),
    "group": (1104, 724),
    "dump": (1399, 724),
    "fight": (813, 722),
}
_DEFAULT_SCREEN = (1920, 1080)
_POST_CLICK_WAIT_SEC = 1.2
_FAST_DRAW_WAIT_SEC = 0.5
_HAND_MIN_FOR_DUMP = 13
_MAX_TURN_STEPS = 10


def _fast_draw_wait_sec() -> float:
    try:
        return float(
            os.environ.get("TONGITS_FAST_DRAW_WAIT_SEC", str(_FAST_DRAW_WAIT_SEC))
        )
    except ValueError:
        return _FAST_DRAW_WAIT_SEC


def _hand_min_for_dump() -> int:
    try:
        return int(
            os.environ.get("TONGITS_HAND_MIN_FOR_DUMP", str(_HAND_MIN_FOR_DUMP))
        )
    except ValueError:
        return _HAND_MIN_FOR_DUMP


def _parse_xy_env(name: str, default: tuple[int, int]) -> tuple[int, int]:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 2:
        return default
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return default


def action_button_xy(action: str) -> tuple[int, int]:
    action = action.strip().lower()
    env_key = f"TONGITS_BUTTON_{action.upper()}_XY"
    default = _DEFAULT_BUTTONS.get(action, (0, 0))
    return _parse_xy_env(env_key, default)


def _screen_size() -> tuple[int, int]:
    sw = int(os.environ.get("TONGITS_SCREEN_WIDTH", str(_DEFAULT_SCREEN[0])))
    sh = int(os.environ.get("TONGITS_SCREEN_HEIGHT", str(_DEFAULT_SCREEN[1])))
    return sw, sh


def _rank_numeric(rank: str) -> int:
    from vision_proxy_qwen import _RANK_VALUE

    return _RANK_VALUE.get(str(rank).upper(), 99)


def _scatter_point(rank: str) -> int:
    from vision_proxy_qwen import _SCATTER_POINTS

    return _SCATTER_POINTS.get(str(rank).upper(), 10)


def _indices_in_sets(hand: list[dict[str, Any]]) -> set[int]:
    by_rank: dict[str, list[int]] = defaultdict(list)
    for i, c in enumerate(hand):
        by_rank[str(c.get("rank") or "").upper()].append(i)
    used: set[int] = set()
    for idxs in by_rank.values():
        if len(idxs) >= 3:
            used.update(idxs)
    return used


def _indices_in_sequences(hand: list[dict[str, Any]], *, min_len: int = 3) -> set[int]:
    by_suit: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for i, c in enumerate(hand):
        suit = str(c.get("suit") or "").upper()
        by_suit[suit].append((i, _rank_numeric(str(c.get("rank") or ""))))

    used: set[int] = set()
    for pairs in by_suit.values():
        pairs.sort(key=lambda x: x[1])
        if len(pairs) < min_len:
            continue
        run_start = 0
        for j in range(1, len(pairs) + 1):
            if j < len(pairs) and pairs[j][1] == pairs[j - 1][1] + 1:
                continue
            run = pairs[run_start:j]
            if len(run) >= min_len:
                used.update(i for i, _ in run)
            run_start = j
    return used


def pick_loose_dump_card(hand: list[dict[str, Any]]) -> dict[str, Any] | None:
    """在散牌中选散牌点数最高的一张用于 Dump。"""
    if not hand:
        return None
    in_meld = _indices_in_sets(hand) | _indices_in_sequences(hand)
    loose = [c for i, c in enumerate(hand) if i not in in_meld]
    pool = loose if loose else list(hand)
    return max(pool, key=lambda c: _scatter_point(str(c.get("rank") or "")))


def recognize_hand_from_screenshot(
    image_path: str | Path,
    *,
    log_fn: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    from vlm_board_analyzer import analyze_hand_vlm, vlm_config_summary

    steps: list[str] | None = [] if log_fn else None
    if log_fn:
        log_fn(f"[VLM] {vlm_config_summary()}")
    hand = analyze_hand_vlm(image_path, step_log=steps)
    if log_fn and steps:
        for step in steps:
            log_fn(f"[操作] {step}")
    return hand


def detect_turn_phase(
    image_path: str | Path,
    *,
    log_fn: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    from vlm_board_analyzer import analyze_turn_phase_vlm

    steps: list[str] | None = [] if log_fn else None
    phase = analyze_turn_phase_vlm(image_path, step_log=steps)
    if log_fn and steps:
        for step in steps:
            log_fn(f"[操作] {step}")
    return phase


def _build_vision_state(
    phase_overlay: dict[str, Any],
    hand: list[dict[str, Any]] | None,
):
    from tongits_rule_bot import TurnPhase, VisionState
    from vision_proxy_qwen import cards_to_cv_state_dict

    cards = [
        {"id": int(c.get("id") or 9001 + i), "suit": c["suit"], "rank": c["rank"]}
        for i, c in enumerate(hand or [])
    ]
    cv = cards_to_cv_state_dict(cards, phase_overlay=phase_overlay) if cards else {
        "turn_phase": phase_overlay.get("turn_phase") or "draw",
        "can_chow": phase_overlay.get("can_chow", False),
        "can_group": phase_overlay.get("can_group", False),
        "can_drop": phase_overlay.get("can_drop", False),
        "can_fight": phase_overlay.get("can_fight", False),
        "scatter_points": 99,
        "hand_cards": [],
    }
    phase_raw = str(cv.get("turn_phase") or phase_overlay.get("turn_phase") or "draw")
    try:
        phase = TurnPhase(phase_raw.lower())
    except ValueError:
        phase = TurnPhase.DRAW
    return VisionState(
        turn_phase=phase,
        elements_dict={},
        can_fight=bool(cv.get("can_fight")),
        should_fight=False,
        can_chow=bool(cv.get("can_chow")),
        can_group=bool(cv.get("can_group")),
        can_drop=bool(cv.get("can_drop")),
        scatter_points=int(cv.get("scatter_points") or 99),
        hand_cards=cards or None,
    )


def _click_action(
    action: str,
    *,
    dry_run: bool,
    log_fn: Callable[[str], None] | None,
    click_delay: float,
) -> dict[str, Any]:
    from tongits_rule_bot import physical_click_xy

    cx, cy = action_button_xy(action)
    label = {
        "deck": "中央牌堆",
        "discard": "弃牌顶牌",
        "special": "弃牌顶牌",
    }.get(action, f"{action.upper()} 按钮")
    if log_fn:
        log_fn(f"[操作] 点击 {label} @({cx},{cy}) …")
    sw, sh = _screen_size()
    res = physical_click_xy(
        cx,
        cy,
        skip_real=dry_run,
        label=action,
        screen_width=sw,
        screen_height=sh,
    )
    if not res.get("ok"):
        raise RuntimeError(f"点击 {action} 失败: {res.get('error')}")
    if click_delay > 0 and not dry_run:
        time.sleep(click_delay)
    return res


def _fresh_screenshot(log_fn: Callable[[str], None] | None) -> Path:
    from vlm_board_analyzer import board_screenshot_save_path, capture_board_screenshot

    save_path = board_screenshot_save_path()
    capture_board_screenshot(save_path=save_path)
    if log_fn:
        log_fn(f"[操作] 刷新截图 → {save_path}")
    return save_path


def _execute_dump(
    hand: list[dict[str, Any]],
    *,
    dry_run: bool,
    log_fn: Callable[[str], None] | None,
    click_delay: float,
) -> dict[str, Any]:
    from tongits_rule_bot import physical_click_xy

    target = pick_loose_dump_card(hand)
    if not target:
        raise RuntimeError("无法选择要打出的牌")

    rank = str(target.get("rank") or "")
    suit = str(target.get("suit") or "")
    label_cn = target.get("label_cn") or f"{suit}{rank}"
    cx, cy = int(target["center_x"]), int(target["center_y"])
    if log_fn:
        log_fn(
            f"[决策] Dump 散牌 {label_cn}（散牌点={_scatter_point(rank)}）"
            f" @({cx},{cy})"
        )

    sw, sh = _screen_size()
    if log_fn:
        log_fn(f"[操作] 点击手牌 {label_cn} …")
    card_res = physical_click_xy(
        cx,
        cy,
        skip_real=dry_run,
        label=f"card_{suit}{rank}",
        screen_width=sw,
        screen_height=sh,
    )
    if not card_res.get("ok"):
        raise RuntimeError(f"点击手牌失败: {card_res.get('error')}")

    if click_delay > 0 and not dry_run:
        time.sleep(click_delay)

    dump_res = _click_action("dump", dry_run=dry_run, log_fn=log_fn, click_delay=0)
    return {"target_card": target, "card_click": card_res, "dump_click": dump_res}


def execute_turn(
    image_path: str | Path,
    *,
    dry_run: bool = False,
    click_delay: float = 0.35,
    log_fn: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """
    完整回合：draw（摸牌/吃牌）→ meld（组牌/亮牌）→ dump（弃牌结束）。
    """
    from tongits_rule_bot import TongitsDecisionEngine

    def _log(msg: str) -> None:
        logger.info(msg)
        if log_fn:
            log_fn(msg)

    path = Path(image_path)
    t0 = time.perf_counter()
    engine = TongitsDecisionEngine()
    actions_taken: list[str] = []
    last_hand: list[dict[str, Any]] = []

    for step in range(1, _MAX_TURN_STEPS + 1):
        _log(f"[回合] 步骤 {step} 分析 …")
        phase_overlay = detect_turn_phase(path, log_fn=_log)
        phase = str(phase_overlay.get("turn_phase") or "draw").lower()

        if phase == "idle":
            _log("[回合] UI 阶段=idle，结束")
            break

        hand: list[dict[str, Any]] = []
        if phase in ("meld", "dump"):
            hand = recognize_hand_from_screenshot(path, log_fn=_log)
            last_hand = hand
            brief = ", ".join(c.get("label_cn") or c["label"] for c in hand)
            _log(f"[操作] 手牌 {len(hand)} 张: {brief}")

        state = _build_vision_state(phase_overlay, hand if hand else None)
        decision = engine.decide_action(state)
        if decision is None:
            _log("[回合] 规则引擎无动作，结束")
            break

        _log(f"[决策] 阶段={state.turn_phase.value} → {decision.action}（{decision.reason}）")

        if decision.action == "dump":
            if not hand:
                hand = recognize_hand_from_screenshot(path, log_fn=_log)
                last_hand = hand
            dump_result = _execute_dump(
                hand,
                dry_run=dry_run,
                log_fn=_log,
                click_delay=click_delay,
            )
            actions_taken.append("dump")
            elapsed_ms = (time.perf_counter() - t0) * 1000
            _log(f"[回合] 完成（{'dry-run' if dry_run else '已执行'}，{elapsed_ms:.0f}ms）")
            return {
                "ok": True,
                "actions": actions_taken,
                "hand": last_hand,
                "dump": dump_result,
                "elapsed_ms": elapsed_ms,
                "dry_run": dry_run,
            }

        if decision.action in ("deck", "discard", "special", "group", "drop", "fight"):
            _click_action(
                decision.action,
                dry_run=dry_run,
                log_fn=_log,
                click_delay=click_delay,
            )
            actions_taken.append(decision.action)
            if dry_run:
                _log("[dry-run] 无刷新截图，本回合演示到此为止")
                break
            time.sleep(_POST_CLICK_WAIT_SEC)
            path = _fresh_screenshot(_log)
            continue

        _log(f"[回合] 未处理动作 {decision.action}，停止")
        break

    elapsed_ms = (time.perf_counter() - t0) * 1000
    return {
        "ok": False,
        "actions": actions_taken,
        "hand": last_hand,
        "elapsed_ms": elapsed_ms,
        "dry_run": dry_run,
        "error": "回合未走完（未执行 Dump）",
    }


def execute_fast_turn(
    *,
    dry_run: bool = False,
    click_delay: float = 0.35,
    log_fn: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """
    极速单次 VLM 流水线：
      1. OpenCV 探中央牌堆/弃牌堆 → 默认点牌堆摸牌（0ms 决策）
      2. 等待摸牌动画 → 截图 → 仅手牌区 VLM 一次
      3. 手牌不足 13 张则补摸一次；本地规则选散牌 → 点牌 + Dump
    """
    from tongits_ui_probe import capture_screen_bgr, decide_blind_draw_action

    def _log(msg: str) -> None:
        if log_fn:
            log_fn(msg)
        else:
            logger.info(msg)

    def _do_draw(action: str) -> None:
        _click_action(action, dry_run=dry_run, log_fn=_log, click_delay=0)
        actions_taken.append(action)
        if dry_run:
            return
        wait_sec = _fast_draw_wait_sec()
        _log(f"[Fast] 等待摸牌动画 {wait_sec:.1f}s …")
        time.sleep(wait_sec)

    t0 = time.perf_counter()
    actions_taken: list[str] = []

    # ── 阶段一：零延迟盲摸 ──
    _log("[Fast] 阶段一：OpenCV 探牌堆/弃牌堆 …")
    screen_bgr = capture_screen_bgr()
    draw_action = decide_blind_draw_action(screen_bgr)
    _log(
        f"[Fast] 决策：{'吃弃牌顶牌' if draw_action == 'discard' else '摸中央牌堆'}"
    )
    _do_draw(draw_action)

    if dry_run:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        _log(f"[Fast] dry-run 停止于摸牌后（{elapsed_ms:.0f}ms）")
        return {
            "ok": True,
            "mode": "fast",
            "actions": actions_taken,
            "hand": [],
            "elapsed_ms": elapsed_ms,
            "dry_run": True,
        }

    # ── 阶段二：单次手牌 VLM ──
    _log("[Fast] 阶段二：截图 + 手牌 VLM（唯一一次 API）…")
    path = _fresh_screenshot(_log)
    hand = recognize_hand_from_screenshot(path, log_fn=_log)
    brief = ", ".join(c.get("label_cn") or c.get("label", "") for c in hand)
    _log(f"[Fast] 手牌 {len(hand)} 张: {brief}")

    min_hand = _hand_min_for_dump()
    if len(hand) < min_hand:
        _log(
            f"[Fast] 手牌仅 {len(hand)} 张（需 ≥{min_hand}），判定未摸牌 → 补点牌堆"
        )
        _do_draw("deck")
        path = _fresh_screenshot(_log)
        hand = recognize_hand_from_screenshot(path, log_fn=_log)
        brief = ", ".join(c.get("label_cn") or c.get("label", "") for c in hand)
        _log(f"[Fast] 补摸后手牌 {len(hand)} 张: {brief}")
        if len(hand) < min_hand:
            raise RuntimeError(
                f"摸牌后手牌仍仅 {len(hand)} 张，无法安全 Dump（需 ≥{min_hand}）"
            )

    # ── 阶段三：本地规则 Dump ──
    _log("[Fast] 阶段三：本地规则选散牌 → Dump …")
    dump_result = _execute_dump(
        hand,
        dry_run=dry_run,
        log_fn=_log,
        click_delay=click_delay,
    )
    actions_taken.append("dump")

    elapsed_ms = (time.perf_counter() - t0) * 1000
    _log(f"[Fast] 回合完成（{elapsed_ms:.0f}ms，VLM×1）")
    return {
        "ok": True,
        "mode": "fast",
        "actions": actions_taken,
        "hand": hand,
        "dump": dump_result,
        "screenshot_after_draw": str(path),
        "elapsed_ms": elapsed_ms,
        "dry_run": dry_run,
    }


def is_fast_mode() -> bool:
    raw = (os.environ.get("TONGITS_FAST_MODE") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def execute_auto_turn(
    image_path: str | Path | None = None,
    *,
    dry_run: bool = False,
    click_delay: float = 0.35,
    log_fn: Callable[[str], None] | None = None,
    force_full: bool = False,
) -> dict[str, Any]:
    """按 TONGITS_FAST_MODE 或 force_full 选择流水线。"""
    if is_fast_mode() and not force_full:
        return execute_fast_turn(
            dry_run=dry_run,
            click_delay=click_delay,
            log_fn=log_fn,
        )
    if image_path is None:
        image_path = _fresh_screenshot(log_fn)
    return execute_turn(
        image_path,
        dry_run=dry_run,
        click_delay=click_delay,
        log_fn=log_fn,
    )


# 兼容旧调用名
def execute_dump_turn(
    image_path: str | Path | None = None,
    *,
    dry_run: bool = False,
    click_delay: float = 0.35,
    log_fn: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    return execute_auto_turn(
        image_path,
        dry_run=dry_run,
        click_delay=click_delay,
        log_fn=log_fn,
    )
