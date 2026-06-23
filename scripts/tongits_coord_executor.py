#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tongits 坐标回合执行器 — 接 qwen_full 侦察结果，自动摸/吃/亮牌/贴牌/弃牌。

物理映射（1920×1080，可用环境变量覆盖）::
  - 中央暗牌堆 ``deck``：点击抓牌入手
  - 弃牌顶牌 ``discard``：明牌，可吃（Chow）
  - 手牌 ``center_x/y``：点选要打出的牌
  - ``drop``：亮牌（Autosort 已分组；**点组内一张**即自动亮出，无需 Drop 按钮）
  - 吃牌：点中央**亮出的弃牌顶牌** → 客户端自动成组亮牌 → 再 Dump 散牌
  - Sapaw：点手牌 → 点桌面牌组目标位
  - ``dump``：弃一张散牌，结束回合

环境变量::
  TONGITS_AUTO_PLAY=1
  TONGITS_AUTO_PLAY_DRY_RUN=1
  TONGITS_AUTO_CHOW=1             弃牌顶可成组时优先吃牌（点中央亮出的顶牌）
  TONGITS_AUTO_DROP=1             自动 Drop 亮牌（不点 Group，游戏已自动分组）
  TONGITS_AUTO_SAPAW=1          自动 Sapaw 贴牌
  TONGITS_POST_DRAW_WAIT_SEC=1.2
  TONGITS_DRAW_RETRY_MAX=3        摸牌后仍须摸牌时重试点击暗牌堆
  TONGITS_HAND_MIN_BEFORE_DRAW=12 无黄箭头时的回退阈值（仅辅助，非固定目标张数）
  TONGITS_HAND_READY_COUNT=13      手牌≥此张数视为已摸够，跳过摸牌（庄家首出 13 张）
  TONGITS_DROP_PRE_CLICK_SEC=0.05  点选手牌后等待客户端自动亮组
  TONGITS_DROP_CLICK_BUTTON=0      1=额外点 Drop 按钮（默认不需要，点牌即亮）
  TONGITS_MELD_SELECT_DELAY_SEC=0.04  Sapaw 等须多次点牌时的间隔
  TONGITS_HAND_ONLY_RESCOUT=1     摸牌后仅 YOLO+VLM 手牌区
  TONGITS_MELD_FAST_RESCOUT=1     亮牌/贴牌后仅刷新手牌+my_melds（默认开）
  TONGITS_TURN_BUDGET_SEC=18        单回合出牌总时间预算
  TONGITS_TURN_DUMP_RESERVE_SEC=5   为 Dump+手牌快刷预留秒数
  TONGITS_TURN_MELD_STEP_EST_SEC=4  估计每步亮牌耗时（超时跳过可选亮牌）
  TONGITS_POST_MELD_WAIT_SEC=1.0
  TONGITS_MAX_MELD_STEPS=8
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable

from tongits_turn_guard import TurnAbortedError, TurnPlayContext

logger = logging.getLogger("tongits_coord_executor")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or ("1" if default else "0")).strip().lower()
    return raw in ("1", "true", "yes", "on")


def auto_play_enabled() -> bool:
    return _env_bool("TONGITS_AUTO_PLAY", False)


def auto_play_dry_run() -> bool:
    return _env_bool("TONGITS_AUTO_PLAY_DRY_RUN", True)


def auto_drop_enabled() -> bool:
    return _env_bool("TONGITS_AUTO_DROP", True)


def auto_sapaw_enabled() -> bool:
    return _env_bool("TONGITS_AUTO_SAPAW", True)


def auto_fight_enabled() -> bool:
    """低点数时可在摸牌前尝试 Fight。"""
    return _env_bool("TONGITS_AUTO_FIGHT", True)


def _fight_scatter_max() -> int:
    try:
        return max(0, int(os.environ.get("TONGITS_FIGHT_SCATTER_MAX", "7")))
    except ValueError:
        return 7


def _post_draw_wait_sec() -> float:
    try:
        return float(os.environ.get("TONGITS_POST_DRAW_WAIT_SEC", "1.2"))
    except ValueError:
        return 1.2


def _post_meld_wait_sec() -> float:
    try:
        return float(os.environ.get("TONGITS_POST_MELD_WAIT_SEC", "1.0"))
    except ValueError:
        return 1.0


def _max_meld_steps() -> int:
    try:
        return max(1, int(os.environ.get("TONGITS_MAX_MELD_STEPS", "8")))
    except ValueError:
        return 8


def _hand_min_before_draw() -> int:
    """
    回退启发：多数回合 Dump 后约 12 张，下回合须先摸牌。
    非固定目标——亮牌/贴牌后手牌可远少于或多于该值。
    """
    try:
        return int(os.environ.get("TONGITS_HAND_MIN_BEFORE_DRAW", "12"))
    except ValueError:
        return 12


def _hand_ready_count() -> int:
    """摸牌阶段结束 / 可亮牌弃牌的手牌张数（庄家首出 13，闲家摸后 13）。"""
    try:
        return int(os.environ.get("TONGITS_HAND_READY_COUNT", "13"))
    except ValueError:
        return 13


def _effective_hand_count(hand: list, by_zone: dict[str, list[Any]]) -> int:
    return max(len(hand), hand_label_count(by_zone))


def _click_delay_sec() -> float:
    try:
        return float(os.environ.get("TONGITS_CARD_CLICK_DELAY_SEC", "0.15"))
    except ValueError:
        return 0.15


def _draw_retry_max() -> int:
    try:
        return max(1, int(os.environ.get("TONGITS_DRAW_RETRY_MAX", "3")))
    except ValueError:
        return 3


def _hand_drop_guard_cards() -> int:
    """
    识别防抖：若快刷后手牌数骤降超过该阈值，认为本次快刷不可信，回退旧快照。
    """
    try:
        return max(1, int(os.environ.get("TONGITS_HAND_DROP_GUARD_CARDS", "2")))
    except ValueError:
        return 2


def _reserve_fast_drop_min_sec() -> float:
    """
    Dump 预留区执行“本地秒算 Drop”的最低剩余时间。
    太低时（例如 <=0）直接跳过，避免回合边界误点手牌。
    """
    try:
        return max(0.0, float(os.environ.get("TONGITS_RESERVE_FAST_DROP_MIN_SEC", "0.6")))
    except ValueError:
        return 0.6


def _abort_if_over_budget_enabled() -> bool:
    return _env_bool("TONGITS_ABORT_IF_OVER_BUDGET", True)


def _over_budget_abort_sec() -> float:
    try:
        return max(0.0, float(os.environ.get("TONGITS_OVER_BUDGET_ABORT_SEC", "0.5")))
    except ValueError:
        return 0.5


def _dump_min_center_y() -> int:
    """
    Dump 安全阈值：仅允许点击底部手牌带（避免误点到桌面分组牌）。
    """
    try:
        return max(0, int(os.environ.get("TONGITS_DUMP_MIN_CENTER_Y", "760")))
    except ValueError:
        return 760


def auto_chow_enabled() -> bool:
    """弃牌顶 VLM + 规则可成组时优先吃牌（默认开）。"""
    return (os.environ.get("TONGITS_AUTO_CHOW") or "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _hand_only_rescout_enabled() -> bool:
    """摸/吃牌后再侦察：默认仅刷新手牌（不重复跑五路 VLM）。"""
    return (os.environ.get("TONGITS_HAND_ONLY_RESCOUT") or "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _meld_fast_rescout_enabled() -> bool:
    """亮牌/贴牌后再侦察：默认仅手牌 + my_melds。"""
    return (os.environ.get("TONGITS_MELD_FAST_RESCOUT") or "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _draw_rescout_hand_timeout_sec(ctx: TurnPlayContext | None) -> float:
    """
    摸牌后手牌快刷的 VLM 超时（秒）。
    默认 2.2s；若回合剩余时间紧张，自动再压缩。
    """
    try:
        base = float(os.environ.get("TONGITS_DRAW_RESCOUT_HAND_TIMEOUT", "2.2"))
    except ValueError:
        base = 2.2
    base = max(0.6, min(8.0, base))
    if ctx is None:
        return base
    remain = max(0.0, ctx.remaining())
    # 给后续 Dump/点击至少留约 1.3s，避免手牌快刷吃光预算。
    cap = max(0.6, remain - 1.3)
    return max(0.6, min(base, cap))


def _draw_rescout_hand_no_retry() -> bool:
    return _env_bool("TONGITS_DRAW_RESCOUT_NO_RETRY", True)


def _drop_pre_click_wait_sec() -> float:
    """点选组内一张牌后等待客户端自动亮组。"""
    try:
        return max(0.0, float(os.environ.get("TONGITS_DROP_PRE_CLICK_SEC", "0.05")))
    except ValueError:
        return 0.05


def _drop_click_button_enabled() -> bool:
    """极少数客户端须再点 Drop；本游戏默认点牌即自动亮组。"""
    return (os.environ.get("TONGITS_DROP_CLICK_BUTTON") or "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _drop_confirm_before_click_enabled() -> bool:
    """兜底重试时，先确认 Drop 按钮处于可点击激活态。"""
    return (os.environ.get("TONGITS_DROP_CONFIRM_BEFORE_CLICK") or "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _drop_confirm_timeout_sec() -> float:
    try:
        return max(0.2, float(os.environ.get("TONGITS_DROP_CONFIRM_TIMEOUT_SEC", "1.0")))
    except ValueError:
        return 1.0


def _drop_dynamic_center_enabled() -> bool:
    return (os.environ.get("TONGITS_DROP_DYNAMIC_CENTER") or "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _drop_button_red_ratio(bgr: Any, cx: int, cy: int) -> float:
    import cv2
    import numpy as np

    if bgr is None or getattr(bgr, "size", 0) == 0:
        return 0.0
    sh, sw = bgr.shape[:2]
    half_w = max(48, int(round(sw * 0.05)))
    half_h = max(18, int(round(sh * 0.03)))
    x1, x2 = max(0, cx - half_w), min(sw, cx + half_w)
    y1, y2 = max(0, cy - half_h), min(sh, cy + half_h)
    patch = bgr[y1:y2, x1:x2]
    if patch.size == 0:
        return 0.0
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    red1 = cv2.inRange(hsv, (0, 80, 70), (10, 255, 255))
    red2 = cv2.inRange(hsv, (170, 80, 70), (180, 255, 255))
    red = cv2.bitwise_or(red1, red2)
    return float(np.count_nonzero(red) / max(1, red.size))


def _detect_drop_button_center_from_frame(bgr: Any) -> tuple[int, int] | None:
    import cv2
    import numpy as np
    from tongits_turn_executor import action_button_xy

    if bgr is None or getattr(bgr, "size", 0) == 0:
        return None
    sh, sw = bgr.shape[:2]
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    red1 = cv2.inRange(hsv, (0, 85, 65), (10, 255, 255))
    red2 = cv2.inRange(hsv, (170, 85, 65), (180, 255, 255))
    red = cv2.bitwise_or(red1, red2)
    kernel = np.ones((5, 5), dtype=np.uint8)
    red = cv2.morphologyEx(red, cv2.MORPH_OPEN, kernel)
    red = cv2.morphologyEx(red, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(red, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    base_cx, base_cy = action_button_xy("drop")
    best: tuple[float, tuple[int, int]] | None = None
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        area = float(w * h)
        if area < 1400:
            continue
        ar = float(w) / max(1.0, float(h))
        if ar < 1.6 or ar > 5.2:
            continue
        cy = y + h / 2.0
        if cy < sh * 0.50:
            continue
        cx = x + w / 2.0
        dist = ((cx - base_cx) ** 2 + (cy - base_cy) ** 2) ** 0.5
        score = area - dist * 6.0
        if best is None or score > best[0]:
            best = (score, (int(round(cx)), int(round(cy))))
    return best[1] if best is not None else None


def _wait_drop_button_active(
    *,
    log_fn: Callable[[str], None],
    timeout_sec: float,
    ctx: TurnPlayContext | None = None,
) -> tuple[bool, tuple[int, int]]:
    from tongits_turn_executor import action_button_xy
    from tongits_ui_probe import capture_screen_bgr

    cx, cy = action_button_xy("drop")
    best_center = (cx, cy)
    t0 = time.perf_counter()
    best = 0.0
    while time.perf_counter() - t0 <= timeout_sec:
        if ctx is not None:
            ctx.check_aborted("Drop确认")
        frame = capture_screen_bgr()
        dyn = _detect_drop_button_center_from_frame(frame)
        if dyn is not None:
            cx, cy = dyn
            best_center = dyn
        ratio = _drop_button_red_ratio(frame, cx, cy)
        best = max(best, ratio)
        if ratio >= 0.10:
            log_fn(f"[出牌] Drop 按钮已激活（red={ratio:.3f} @({cx},{cy})）")
            return True, (cx, cy)
        time.sleep(0.10)
    log_fn(
        f"[出牌] Drop 激活确认超时（best_red={best:.3f} @({best_center[0]},{best_center[1]})），执行兜底点击"
    )
    return False, best_center


def _meld_select_delay_sec() -> float:
    try:
        return max(0.0, float(os.environ.get("TONGITS_MELD_SELECT_DELAY_SEC", "0.04")))
    except ValueError:
        return 0.04


def _click_move_duration_sec(*, fast: bool = False) -> float:
    if fast:
        try:
            return max(0.03, float(os.environ.get("TONGITS_FAST_CLICK_MOVE_SEC", "0.06")))
        except ValueError:
            return 0.06
    return 0.15


def _infer_after_draw(
    scout: Any,
    bgr: Any,
    prev_scout_result: Any | None,
    *,
    ctx: TurnPlayContext | None = None,
    reason: str = "摸牌后",
    log_fn: Callable[[str], None] | None = None,
) -> Any:
    if (
        _hand_only_rescout_enabled()
        and prev_scout_result is not None
        and hasattr(scout, "infer_hand_only")
    ):
        timeout_sec = _draw_rescout_hand_timeout_sec(ctx)
        no_retry = _draw_rescout_hand_no_retry()
        if log_fn is not None:
            log_fn(
                f"[出牌] {reason}手牌快刷参数：timeout={timeout_sec:.1f}s no_retry={no_retry}"
            )
        try:
            return scout.infer_hand_only(
                bgr,
                prev=prev_scout_result,
                timeout_sec=timeout_sec,
                no_retry=no_retry,
            )
        except TypeError:
            return scout.infer_hand_only(bgr, prev=prev_scout_result)
    return scout.infer_turn_frame(bgr)


def _log_fn_default(msg: str) -> None:
    logger.info(msg)


def hand_label_count(by_zone: dict[str, list[Any]]) -> int:
    """侦察区 player_hand 标签张数（含无坐标项）。"""
    return len(by_zone.get("player_hand") or [])


def hand_from_scout_detections(detections: list[Any]) -> list:
    from tongits_rules import HandCard, label_to_hand_card

    with_xy: list = []
    without_xy: list = []
    for d in detections:
        hc = label_to_hand_card(
            str(getattr(d, "class_name", "") or ""),
            center_x=int(getattr(d, "center_x", 0) or 0),
            center_y=int(getattr(d, "center_y", 0) or 0),
        )
        if not hc:
            continue
        if hc.center_x > 0 and hc.center_y > 0:
            with_xy.append(hc)
        else:
            without_xy.append(hc)

    if without_xy and with_xy:
        with_xy.sort(key=lambda c: c.center_x)
        gap = 80
        if len(with_xy) >= 2:
            gaps = [
                with_xy[i + 1].center_x - with_xy[i].center_x
                for i in range(len(with_xy) - 1)
            ]
            gap = int(round(sum(gaps) / len(gaps)))
        last = with_xy[-1]
        mean_y = int(round(sum(c.center_y for c in with_xy) / len(with_xy)))
        for i, hc in enumerate(without_xy):
            cx = last.center_x + gap * (i + 1)
            with_xy.append(
                HandCard(
                    label=hc.label,
                    suit=hc.suit,
                    rank=hc.rank,
                    center_x=cx,
                    center_y=mean_y,
                )
            )
    elif without_xy:
        return []

    with_xy.sort(key=lambda c: c.center_x)
    return with_xy


def needs_draw_phase(
    bgr: Any,
    hand: list,
    by_zone: dict[str, list[Any]],
) -> tuple[bool, str]:
    """
    本回合是否仍处「须摸/吃」阶段。

    主信号：手牌未达可出牌张数（通常 13）且暗牌堆黄箭头。
    庄家首出已有 13 张时，黄箭头可能是牌背/UI 误报，必须跳过摸牌。
    """
    from tongits_ui_probe import is_draw_phase_hint

    n = _effective_hand_count(hand, by_zone)
    ready = _hand_ready_count()
    if n >= ready:
        return False, f"手牌 {n} 张（≥{ready}）→ 跳过摸牌，直接亮牌/贴牌/弃牌"

    if is_draw_phase_hint(bgr):
        return True, f"暗牌堆黄箭头且手牌 {n} 张<{ready}→须摸牌"

    min_pre = _hand_min_before_draw()
    if n > 0 and n <= min_pre:
        return True, f"无黄箭头但手牌仅 {n} 张（≤{min_pre}），推断尚未摸牌"

    return False, f"手牌 {n} 张且无摸牌黄箭头→跳过摸牌（可亮牌/贴牌/弃牌）"


def draw_phase_succeeded(
    bgr: Any,
    hand: list,
    by_zone: dict[str, list[Any]],
    *,
    before_labels: int,
    before_coords: int,
) -> tuple[bool, str]:
    """摸/吃是否生效：已达可出牌张数、黄箭头消失或手牌张数增加。"""
    from tongits_ui_probe import is_draw_phase_hint

    after_labels = hand_label_count(by_zone)
    after_coords = len(hand)
    ready = _hand_ready_count()

    if after_labels >= ready or after_coords >= ready:
        return True, f"手牌已达 {max(after_labels, after_coords)} 张（≥{ready}）"

    if not is_draw_phase_hint(bgr):
        return True, "黄箭头已消失，摸牌阶段结束"

    if after_labels > before_labels:
        return True, f"VLM 手牌 {before_labels}→{after_labels} 张"
    if after_coords > before_coords:
        return True, f"有坐标手牌 {before_coords}→{after_coords} 张"

    return False, (
        f"仍显示须摸牌且手牌未增加（VLM={after_labels} 坐标={after_coords}）"
    )


def discard_top_from_scout(by_zone: dict[str, list[Any]]) -> str | None:
    dets = by_zone.get("center_discard") or []
    if not dets:
        return None
    label = str(getattr(dets[0], "class_name", "") or "").strip()
    return label or None


def _my_melds_roi(sw: int, sh: int) -> tuple[int, int, int, int]:
    env_roi = (os.environ.get("TONGITS_ROI_MY_MELDS") or "").strip()
    if env_roi:
        parts = [p.strip() for p in env_roi.split(",")]
        if len(parts) == 4:
            try:
                return tuple(int(p) for p in parts)  # type: ignore[return-value]
            except ValueError:
                pass
    y1 = int(sh * float(os.environ.get("TONGITS_ROI_MY_MELD_Y1_RATIO", "0.50")))
    y2 = int(sh * float(os.environ.get("TONGITS_ROI_MY_MELD_Y2_RATIO", "0.60")))
    x1 = int(sw * float(os.environ.get("TONGITS_ROI_MY_MELD_X1_RATIO", "0.20")))
    x2 = int(sw * float(os.environ.get("TONGITS_ROI_MY_MELD_X2_RATIO", "0.80")))
    return (max(0, x1), max(0, y1), min(sw, x2), min(sh, y2))


def _resolve_zone_rois(
    scout_result: Any,
    bgr: Any,
) -> dict[str, tuple[int, int, int, int]]:
    zone_rois = getattr(scout_result, "zone_rois", None) or {}
    if zone_rois:
        return dict(zone_rois)

    sh, sw = bgr.shape[:2]
    from fast_card_recognizer import resolve_multi_zone_rois

    rois = resolve_multi_zone_rois(sw, sh)
    rois["my_melds"] = _my_melds_roi(sw, sh)
    return rois


def _zone_click_xy(
    roi: tuple[int, int, int, int],
    slot_index: int,
    total_slots: int,
) -> tuple[int, int]:
    """在战区 ROI 内按槽位估算点击坐标（左→右）。"""
    x1, y1, x2, y2 = roi
    n = max(total_slots, 1)
    slot = max(0, min(slot_index, n - 1))
    w = max(1, x2 - x1)
    h = max(1, y2 - y1)
    cx = int(x1 + w * (slot + 0.5) / n)
    cy = int(y1 + h * 0.55)
    return cx, cy


def _sapaw_target_xy(
    meld: Any,
    attach: str,
    zone_labels: list[str],
    zone_roi: tuple[int, int, int, int],
) -> tuple[int, int]:
    from tongits_rules import meld_attach_slot_index

    n = max(len(zone_labels), len(meld.cards), 1)
    slot = meld_attach_slot_index(meld, attach, n)
    if attach == "high":
        slot = min(n, slot + 1) - 1
        slot = min(n - 1, slot + 1) if n > 1 else slot
    return _zone_click_xy(zone_roi, slot, n)


def _click_xy(
    cx: int,
    cy: int,
    *,
    label: str,
    dry_run: bool,
    log_fn: Callable[[str], None],
    clicks: int = 1,
    move_duration: float | None = None,
    ctx: TurnPlayContext | None = None,
) -> dict[str, Any]:
    if ctx is not None:
        ctx.ensure_active(f"点击 {label}")

    from tongits_rule_bot import physical_click_xy
    from tongits_turn_executor import _screen_size

    sw, sh = _screen_size()
    click_note = f" x{clicks}" if clicks > 1 else ""
    log_fn(f"[出牌] 点击 {label}{click_note} @({cx},{cy}) …")
    if dry_run:
        return physical_click_xy(
            cx,
            cy,
            skip_real=True,
            label=label,
            screen_width=sw,
            screen_height=sh,
        )

    try:
        import pyautogui
    except ImportError as e:
        return {"ok": False, "error": f"pyautogui_not_installed:{e}"}

    from tongits_rule_bot import _screen_click_xy

    sx, sy = _screen_click_xy(cx, cy, screen_width=sw, screen_height=sh)
    dur = _click_move_duration_sec() if move_duration is None else move_duration
    pyautogui.FAILSAFE = True
    try:
        pyautogui.moveTo(sx, sy, duration=dur)
        pyautogui.click(clicks=clicks)
    except Exception as e:
        return {"ok": False, "error": repr(e), "x": sx, "y": sy}
    return {"ok": True, "x": sx, "y": sy, "label": label}


def _click_pile(
    pile: str,
    bgr: Any,
    *,
    dry_run: bool,
    log_fn: Callable[[str], None],
    clicks: int = 1,
    ctx: TurnPlayContext | None = None,
) -> dict[str, Any]:
    """点击中央暗牌堆 / 弃牌顶明牌（真实摸牌/吃牌入口）。"""
    from tongits_ui_probe import deck_click_xy, discard_click_xy, is_draw_phase_hint

    pile = pile.strip().lower()
    if pile == "deck":
        cx, cy = deck_click_xy(bgr)
        hint = is_draw_phase_hint(bgr)
        name = f"暗牌堆（摸牌，黄箭头={'有' if hint else '无'}）"
    else:
        cx, cy = discard_click_xy(bgr)
        name = "弃牌顶牌（吃牌）"
    return _click_xy(
        cx,
        cy,
        label=name,
        dry_run=dry_run,
        log_fn=log_fn,
        clicks=clicks,
        ctx=ctx,
    )


def _click_action(
    action: str,
    *,
    dry_run: bool,
    log_fn: Callable[[str], None],
    ctx: TurnPlayContext | None = None,
) -> dict[str, Any]:
    from tongits_turn_executor import action_button_xy

    action = action.strip().lower()
    cx, cy = action_button_xy(action)
    names = {
        "deck": "中央暗牌堆（摸牌）",
        "discard": "弃牌顶牌（吃牌）",
        "dump": "Dump 按钮",
        "group": "Group 按钮",
        "drop": "Drop 按钮",
        "fight": "Fight 按钮",
    }
    return _click_xy(cx, cy, label=names.get(action, action), dry_run=dry_run, log_fn=log_fn, ctx=ctx)


def _click_hand_cards(
    hand: list,
    indices: tuple[int, ...] | list[int],
    *,
    dry_run: bool,
    log_fn: Callable[[str], None],
    fast: bool = False,
    ctx: TurnPlayContext | None = None,
) -> None:
    delay = _meld_select_delay_sec() if fast else _click_delay_sec()
    move_dur = _click_move_duration_sec(fast=fast)
    cards = sorted(
        (hand[i] for i in indices),
        key=lambda c: c.center_x,
    )
    for card in cards:
        res = _click_xy(
            card.center_x,
            card.center_y,
            label=f"hand_{card.label}",
            dry_run=dry_run,
            log_fn=log_fn,
            move_duration=move_dur,
            ctx=ctx,
        )
        if not res.get("ok"):
            raise RuntimeError(f"点击手牌 {card.label} 失败: {res.get('error')}")
        if not dry_run and delay > 0:
            time.sleep(delay)


def _pick_meld_lead_card(hand: list, indices: tuple[int, ...] | list[int]):
    """Autosort 分组后点组内一张即整组抬起；取最左一张作为代表。"""
    return min((hand[i] for i in indices), key=lambda c: c.center_x)


def _drop_group_validation(hand: list, indices: tuple[int, ...] | list[int]) -> tuple[str, bool]:
    """Drop 前本地二次校验：set / sequence / invalid。"""
    cards = [hand[i] for i in indices]
    if len(cards) < 3:
        return "invalid", False

    ranks = [c.rank for c in cards]
    suits = [c.suit for c in cards]
    rank_values = [int(c.rank_value) for c in cards]

    # 刻子：同点数 + 花色互异
    is_set = len(set(ranks)) == 1 and len(set(suits)) == len(suits)
    if is_set:
        return "set", True

    # 顺子：同花色 + 点数严格连续且不重复
    sorted_vals = sorted(rank_values)
    is_sequence = (
        len(set(suits)) == 1
        and len(set(sorted_vals)) == len(sorted_vals)
        and all(sorted_vals[i + 1] == sorted_vals[i] + 1 for i in range(len(sorted_vals) - 1))
    )
    if is_sequence:
        return "sequence", True

    return "invalid", False


def _is_drop_group_left_side(hand: list, indices: tuple[int, ...] | list[int]) -> bool:
    """
    Drop 组位于左侧约束：
    - 组内牌按 x 排序后，其最右位置不应越过手牌中位附近。
    - 且组内牌应主要落在左侧前缀位（避免右侧单牌被误点成“假亮牌”）。
    """
    if len(indices) < 3:
        return False
    ordered = sorted(
        enumerate(hand),
        key=lambda kv: getattr(kv[1], "center_x", 0) or 0,
    )
    if len(ordered) < 3:
        return False
    pos_map = {orig_i: pos for pos, (orig_i, _c) in enumerate(ordered)}
    sel_pos = sorted(pos_map.get(i, 10**9) for i in indices)
    if not sel_pos or sel_pos[-1] >= 10**9:
        return False

    n = len(ordered)
    # 左侧阈值：不超过中位偏左；并限制在前缀位附近。
    left_cap = max(2, int(round((n - 1) * 0.55)))
    prefix_cap = min(n - 1, len(indices) + 1)
    return sel_pos[-1] <= left_cap and sel_pos[-1] <= prefix_cap


def _execute_drop_meld(
    hand: list,
    indices: tuple[int, ...] | list[int],
    *,
    dry_run: bool,
    log_fn: Callable[[str], None],
    force_drop_button: bool = False,
    ctx: TurnPlayContext | None = None,
) -> bool:
    """亮牌：点组内一张后，强制点击 Drop 按钮确认亮牌。"""
    labels = [hand[i].label for i in indices]
    drop_kind, is_valid = _drop_group_validation(hand, indices)
    is_left = _is_drop_group_left_side(hand, indices)
    log_fn(
        f"[出牌] Drop 二次校验: drop_labels={labels} drop_kind={drop_kind} "
        f"is_valid={is_valid} is_left={is_left}"
    )
    if not is_valid:
        log_fn("[出牌] 跳过 Drop：未形成合法成组（禁止单牌触发 Drop）")
        return False
    if not is_left:
        log_fn("[出牌] 跳过 Drop：成组不在左侧，避免误点导致后续 Dump 失败")
        return False
    lead = _pick_meld_lead_card(hand, indices)
    extra = " → Drop 按钮（强制）"
    log_fn(
        f"[出牌] 亮牌: {', '.join(labels)}"
        f"（点 {lead.label}{extra}）"
    )
    move_dur = _click_move_duration_sec(fast=True)
    res = _click_xy(
        lead.center_x,
        lead.center_y,
        label=f"hand_{lead.label}",
        dry_run=dry_run,
        log_fn=log_fn,
        move_duration=move_dur,
        ctx=ctx,
    )
    if not res.get("ok"):
        raise RuntimeError(f"点击手牌 {lead.label} 失败: {res.get('error')}")
    if not dry_run:
        wait = _drop_pre_click_wait_sec()
        if wait > 0:
            time.sleep(wait)

    drop_res = _click_action("drop", dry_run=dry_run, log_fn=log_fn, ctx=ctx)
    if not drop_res.get("ok"):
        raise RuntimeError(f"Drop 按钮失败: {drop_res.get('error')}")
    if not dry_run:
        time.sleep(_post_meld_wait_sec())
    return True


def _execute_sapaw(
    move: Any,
    zone_labels: list[str],
    zone_roi: tuple[int, int, int, int],
    *,
    dry_run: bool,
    log_fn: Callable[[str], None],
    ctx: TurnPlayContext | None = None,
) -> None:
    card = move.hand_card
    tx, ty = _sapaw_target_xy(move.meld, move.attach, zone_labels, zone_roi)
    log_fn(f"[出牌] Sapaw {move.reason} → 目标@({tx},{ty})")
    card_res = _click_xy(
        card.center_x,
        card.center_y,
        label=f"sapaw_{card.label}",
        dry_run=dry_run,
        log_fn=log_fn,
        ctx=ctx,
    )
    if not card_res.get("ok"):
        raise RuntimeError(f"Sapaw 选手牌失败: {card_res.get('error')}")
    if not dry_run:
        time.sleep(0.2)
    tgt_res = _click_xy(
        tx,
        ty,
        label=f"sapaw_target_{move.meld.zone}",
        dry_run=dry_run,
        log_fn=log_fn,
        ctx=ctx,
    )
    if not tgt_res.get("ok"):
        raise RuntimeError(f"Sapaw 点目标失败: {tgt_res.get('error')}")
    if not dry_run:
        time.sleep(_post_meld_wait_sec())


def _rescout_hand_after_draw(
    scout: Any,
    grab_frame: Callable[[], tuple[Any, str] | tuple[Any, str, Any]] | None,
    bgr: Any,
    *,
    dry_run: bool,
    log_fn: Callable[[str], None],
    reason: str = "摸牌后",
    wait_sec: float | None = None,
    prev_scout_result: Any | None = None,
    ctx: TurnPlayContext | None = None,
) -> tuple[Any, Any, dict[str, list[Any]], dict[str, tuple[int, int, int, int]], list] | None:
    """
    摸/吃牌后：等待动画 → 再截屏 → **仅手牌** YOLO+VLM 快刷（默认，见 TONGITS_HAND_ONLY_RESCOUT）。
    """
    if grab_frame is None:
        log_fn("[出牌] 无 grab_frame，无法在摸牌后再侦察手牌")
        return None

    if ctx is not None:
        ctx.check_aborted("摸牌后再侦察")

    if not dry_run:
        wait = _post_draw_wait_sec() if wait_sec is None else wait_sec
        log_fn(f"[出牌] 等待{reason}动画 {wait:.1f}s …")
        time.sleep(wait)
    else:
        log_fn(f"[出牌] dry-run：跳过真实点击/{reason}动画，仍快刷手牌 …")

    grabbed = grab_frame()
    bgr2 = grabbed[0] if grabbed else None
    if bgr2 is None:
        log_fn(f"[出牌] {reason}后再侦察截屏失败")
        return None

    hand_only = _hand_only_rescout_enabled() and prev_scout_result is not None
    if hand_only:
        log_fn(f"[出牌] {reason}后仅更新手牌（跳过明牌/对手/弃牌 VLM）…")
    scout_result2 = _infer_after_draw(
        scout,
        bgr2,
        prev_scout_result,
        ctx=ctx,
        reason=reason,
        log_fn=log_fn,
    )
    by_zone = scout_result2.by_zone
    zone_rois = _resolve_zone_rois(scout_result2, bgr2)
    hand = hand_from_scout_detections(by_zone.get("player_hand") or [])
    log_fn(
        f"[出牌] {reason}后再侦察: {len(hand)} 张: "
        + ", ".join(
            f"{c.label}@({c.center_x},{c.center_y})" if c.center_x else c.label
            for c in hand
        )
    )
    return bgr2, scout_result2, by_zone, zone_rois, hand


def _refresh_hand_only(
    scout: Any,
    grab_frame: Callable[[], tuple[Any, str] | tuple[Any, str, Any]] | None,
    *,
    prev_scout_result: Any | None,
    log_fn: Callable[[str], None],
    ctx: TurnPlayContext | None = None,
    reason: str = "手牌",
) -> tuple[Any, Any, dict[str, list[Any]], list] | None:
    """仅 YOLO+VLM 手牌快刷（Dump 前最多一次）。"""
    if grab_frame is None or prev_scout_result is None:
        return None
    if ctx is not None:
        ctx.check_aborted(f"{reason}快刷")
        if ctx.dump_hand_refreshed:
            log_fn(f"[出牌] {reason}：本回合已做过 hand-only 快刷，跳过")
            return None
        if not ctx.can_afford_hand_refresh():
            log_fn(
                f"[出牌] {reason}：剩余 {ctx.remaining():.1f}s 不足，跳过 hand-only 快刷"
            )
            return None

    grabbed = grab_frame()
    bgr = grabbed[0] if grabbed else None
    if bgr is None:
        log_fn(f"[出牌] {reason}快刷截屏失败")
        return None

    if not hasattr(scout, "infer_hand_only"):
        return None

    log_fn(f"[出牌] {reason}前 hand-only 快刷 …")
    timeout_sec = _draw_rescout_hand_timeout_sec(ctx)
    no_retry = _draw_rescout_hand_no_retry()
    log_fn(
        f"[出牌] {reason} hand-only 参数：timeout={timeout_sec:.1f}s no_retry={no_retry}"
    )
    try:
        scout_result = scout.infer_hand_only(
            bgr,
            prev=prev_scout_result,
            timeout_sec=timeout_sec,
            no_retry=no_retry,
        )
    except TypeError:
        scout_result = scout.infer_hand_only(bgr, prev=prev_scout_result)
    by_zone = scout_result.by_zone
    hand = hand_from_scout_detections(by_zone.get("player_hand") or [])
    log_fn(
        f"[出牌] {reason}后手牌 {len(hand)} 张: "
        + ", ".join(c.label for c in hand)
    )
    if ctx is not None:
        ctx.dump_hand_refreshed = True
    return bgr, scout_result, by_zone, hand


def _refresh_hand_yolo_only(
    scout: Any,
    grab_frame: Callable[[], tuple[Any, str] | tuple[Any, str, Any]] | None,
    *,
    prev_scout_result: Any | None,
    log_fn: Callable[[str], None],
    reason: str = "手牌",
) -> tuple[Any, Any, dict[str, list[Any]], list] | None:
    """仅 YOLO 手速重锚（无 VLM），用于 Drop 后手牌居中导致坐标漂移。"""
    if grab_frame is None or prev_scout_result is None:
        return None
    if not hasattr(scout, "infer_hand_yolo_only"):
        return None
    grabbed = grab_frame()
    bgr = grabbed[0] if grabbed else None
    if bgr is None:
        log_fn(f"[出牌] {reason} YOLO 重锚截屏失败")
        return None
    log_fn(f"[出牌] {reason}前执行 YOLO-only 手牌重锚 …")
    scout_result = scout.infer_hand_yolo_only(bgr, prev=prev_scout_result)
    by_zone = scout_result.by_zone
    hand = hand_from_scout_detections(by_zone.get("player_hand") or [])
    log_fn(f"[出牌] {reason}后 YOLO 重锚手牌 {len(hand)} 张")
    return bgr, scout_result, by_zone, hand


def _refresh_scout(
    scout: Any,
    grab_frame: Callable[[], tuple[Any, str] | tuple[Any, str, Any]] | None,
    *,
    dry_run: bool,
    log_fn: Callable[[str], None],
    prev_scout_result: Any | None = None,
    ctx: TurnPlayContext | None = None,
    hand_only: bool = False,
) -> tuple[Any, Any, dict[str, list[Any]]]:
    if dry_run or grab_frame is None:
        return None, None, {}
    if ctx is not None:
        ctx.check_aborted("亮牌后再侦察")
        if hand_only or not ctx.can_afford_meld_rescout():
            if not ctx.can_afford_meld_rescout():
                log_fn(
                    f"[出牌] 剩余 {ctx.remaining():.1f}s，亮牌后跳过 VLM 快刷"
                )
            refreshed = _refresh_hand_only(
                scout,
                grab_frame,
                prev_scout_result=prev_scout_result,
                log_fn=log_fn,
                ctx=None,
                reason="亮牌后",
            )
            if refreshed is None:
                return None, None, {}
            bgr, scout_result, by_zone, _hand = refreshed
            return bgr, scout_result, by_zone

    grabbed = grab_frame()
    bgr = grabbed[0]
    if (
        _meld_fast_rescout_enabled()
        and prev_scout_result is not None
        and hasattr(scout, "infer_hand_my_melds_only")
    ):
        log_fn("[出牌] 亮牌/贴牌后快刷：手牌 + my_melds …")
        scout_result = scout.infer_hand_my_melds_only(bgr, prev=prev_scout_result)
    elif (
        _hand_only_rescout_enabled()
        and prev_scout_result is not None
        and hasattr(scout, "infer_hand_only")
    ):
        scout_result = scout.infer_hand_only(bgr, prev=prev_scout_result)
    else:
        scout_result = scout.infer_turn_frame(bgr)
    by_zone = scout_result.by_zone
    hand = hand_from_scout_detections(by_zone.get("player_hand") or [])
    log_fn(
        f"[出牌] 亮牌/贴牌后再侦察: {len(hand)} 张: "
        + ", ".join(c.label for c in hand)
    )
    return bgr, scout_result, by_zone


def _labels_cover_meld(meld_labels: list[str], table_labels: list[str]) -> bool:
    """桌面标签是否已包含该牌组（亮牌成功检测）。"""
    from collections import Counter

    need = Counter(meld_labels)
    have = Counter(table_labels)
    return all(have.get(r, 0) >= c for r, c in need.items())


def _execute_meld_phase(
    hand: list,
    by_zone: dict[str, list[Any]],
    zone_rois: dict[str, tuple[int, int, int, int]],
    *,
    scout: Any,
    grab_frame: Callable[[], tuple[Any, str] | tuple[Any, str, Any]] | None,
    dry_run: bool,
    log_fn: Callable[[str], None],
    chow_label: str | None = None,
    prev_scout_result: Any | None = None,
    ctx: TurnPlayContext | None = None,
) -> tuple[list, dict[str, list[Any]], Any | None, Any | None, list[str]]:
    """
    可选阶段：吃牌后强制亮组 → 循环 Sapaw / Drop 亮牌。
    """
    from tongits_rules import (
        all_table_melds_from_zones,
        pick_next_meld_plan,
        zone_labels_from_detections,
    )

    actions: list[str] = []
    bgr_ref: Any | None = None
    scout_ref: Any | None = None
    scout_state: Any | None = prev_scout_result
    last_drop_key: tuple[str, ...] | None = None

    if chow_label:
        log_fn(
            f"[出牌] 已吃顶牌 {chow_label}，客户端自动亮组，跳过手牌/Drop → 直接弃散牌"
        )
        actions.append("chow")
        if not dry_run and grab_frame is not None:
            refreshed = _refresh_hand_only(
                scout,
                grab_frame,
                prev_scout_result=scout_state,
                log_fn=log_fn,
                ctx=None,
                reason="吃牌后",
            )
            if refreshed is not None:
                bgr_ref, scout_ref, by_zone, hand = refreshed
                scout_state = scout_ref
        return hand, by_zone, bgr_ref, scout_ref, actions

    if ctx is not None and ctx.must_dump_only():
        remain = ctx.remaining()
        did_fast_drop = False
        # 进入 Dump 预留区时，不再做重刷；但允许本地规则秒算并尝试一次 Drop。
        if auto_drop_enabled() and hand and remain > _reserve_fast_drop_min_sec():
            zone_label_map = zone_labels_from_detections(by_zone)
            table_melds = all_table_melds_from_zones(zone_label_map)
            plan = pick_next_meld_plan(
                hand,
                table_melds,
                auto_drop=True,
                auto_sapaw=False,
            )
            if plan is not None and plan.action == "drop" and plan.hand_indices:
                try:
                    log_fn(
                        f"[出牌] 剩余 {remain:.1f}s ≤ Dump 预留，执行一次本地秒算 Drop：{plan.reason}"
                    )
                    dropped = _execute_drop_meld(
                        hand,
                        plan.hand_indices,
                        dry_run=dry_run,
                        log_fn=log_fn,
                        ctx=ctx,
                    )
                    if dropped:
                        actions.append("drop_reserve")
                        did_fast_drop = True
                except Exception as e:
                    log_fn(f"[出牌] Dump 预留区 Drop 失败，回退直接 Dump: {e}")
        elif auto_drop_enabled() and hand:
            log_fn(
                f"[出牌] 剩余 {remain:.1f}s ≤ { _reserve_fast_drop_min_sec():.1f}s，"
                "跳过 Dump 预留区本地 Drop，避免回合边界误点"
            )
        if not did_fast_drop:
            log_fn(
                f"[出牌] 剩余 {remain:.1f}s ≤ Dump 预留，跳过可选亮牌/贴牌"
            )
        return hand, by_zone, bgr_ref, scout_ref, actions

    if not auto_drop_enabled() and not auto_sapaw_enabled():
        return hand, by_zone, bgr_ref, scout_ref, actions

    for step in range(1, _max_meld_steps() + 1):
        if ctx is not None:
            ctx.check_aborted("亮牌循环")
            if not ctx.can_do_optional_meld():
                log_fn(
                    f"[出牌] 剩余 {ctx.remaining():.1f}s 不足，停止可选亮牌（步骤 {step}）"
                )
                break

        zone_label_map = zone_labels_from_detections(by_zone)
        table_melds = all_table_melds_from_zones(zone_label_map)
        plan = pick_next_meld_plan(
            hand,
            table_melds,
            auto_drop=auto_drop_enabled(),
            auto_sapaw=auto_sapaw_enabled(),
        )
        if plan is None:
            log_fn(f"[出牌] 无更多亮牌/贴牌（步骤 {step}）")
            break

        log_fn(f"[出牌] 部署步骤 {step}: {plan.action} — {plan.reason}（score={plan.score}）")

        if plan.action == "drop" and plan.hand_indices:
            drop_labels = tuple(sorted(hand[i].label for i in plan.hand_indices))
            my_melds_labels = zone_label_map.get("my_melds") or []
            if _labels_cover_meld(list(drop_labels), my_melds_labels):
                log_fn(f"[出牌] 跳过 Drop：{drop_labels} 已在 my_melds")
                break
            if drop_labels == last_drop_key:
                log_fn(f"[出牌] 重复 Drop 兜底重试（强制点 Drop 按钮）: {drop_labels}")
                dropped = _execute_drop_meld(
                    hand,
                    plan.hand_indices,
                    dry_run=dry_run,
                    log_fn=log_fn,
                    force_drop_button=True,
                    ctx=ctx,
                )
                if dropped:
                    actions.append("drop_retry")
                    last_drop_key = None
                else:
                    break
            else:
                dropped = _execute_drop_meld(
                    hand, plan.hand_indices, dry_run=dry_run, log_fn=log_fn, ctx=ctx
                )
                if dropped:
                    actions.append("drop")
                    last_drop_key = drop_labels
                else:
                    break
        elif plan.action == "sapaw" and plan.sapaw is not None:
            move = plan.sapaw
            zone_key = move.meld.zone
            roi = zone_rois.get(zone_key)
            if not roi:
                log_fn(f"[出牌] 缺少 {zone_key} ROI，跳过 Sapaw")
                break
            _execute_sapaw(
                move,
                zone_label_map.get(zone_key, []),
                roi,
                dry_run=dry_run,
                log_fn=log_fn,
                ctx=ctx,
            )
            actions.append("sapaw")
        else:
            break

        if dry_run:
            log_fn("[出牌] dry-run：仅演示一步亮牌/贴牌")
            break

        if grab_frame is None:
            break
        bgr_ref, scout_ref, by_zone = _refresh_scout(
            scout,
            grab_frame,
            dry_run=dry_run,
            log_fn=log_fn,
            prev_scout_result=scout_state,
            ctx=ctx,
        )
        if scout_ref is not None:
            scout_state = scout_ref
        if not by_zone:
            break
        hand = hand_from_scout_detections(by_zone.get("player_hand") or [])
        if not hand:
            log_fn("[出牌] 再侦察后手牌无坐标，停止亮牌/贴牌")
            break

    return hand, by_zone, bgr_ref, scout_ref, actions


def _execute_draw(
    hand: list,
    discard_top: str | None,
    bgr: Any,
    *,
    dry_run: bool,
    log_fn: Callable[[str], None],
    clicks: int = 1,
    ctx: TurnPlayContext | None = None,
) -> str:
    from tongits_rules import decide_draw_action
    from tongits_ui_probe import is_chow_available

    ui_chow = is_chow_available(bgr)
    action, reason = decide_draw_action(
        hand,
        discard_top,
        auto_chow=auto_chow_enabled(),
        ui_chow_available=ui_chow,
    )
    log_fn(f"[出牌] 摸牌阶段 → {action}（{reason}；UI可吃={ui_chow}）")
    draw_key = "discard" if action == "discard" else "deck"
    res = _click_pile(draw_key, bgr, dry_run=dry_run, log_fn=log_fn, clicks=clicks, ctx=ctx)
    if not res.get("ok"):
        raise RuntimeError(f"摸牌/吃牌点击失败: {res.get('error')}")
    return draw_key


def _try_fight_before_draw(
    hand: list,
    *,
    dry_run: bool,
    log_fn: Callable[[str], None],
    ctx: TurnPlayContext | None = None,
) -> bool:
    """
    摸牌前可选 Fight：
    - 开关开启
    - 手牌散牌点数 <= 阈值
    """
    if not auto_fight_enabled() or not hand:
        return False
    from tongits_rules import loose_scatter_points

    scatter = int(loose_scatter_points(hand))
    thr = _fight_scatter_max()
    if scatter > thr:
        log_fn(f"[出牌] Fight 前置判定：loose_scatter={scatter} > {thr}，继续摸牌")
        return False
    log_fn(f"[出牌] Fight 前置判定：loose_scatter={scatter} <= {thr}，先尝试 Fight")
    res = _click_action("fight", dry_run=dry_run, log_fn=log_fn, ctx=ctx)
    if not res.get("ok"):
        log_fn(f"[出牌] Fight 点击失败，继续摸牌: {res.get('error')}")
        return False
    return True


def _draw_and_rescout(
    scout: Any,
    grab_frame: Callable[[], tuple[Any, str] | tuple[Any, str, Any]] | None,
    bgr: Any,
    scout_result: Any,
    hand: list,
    by_zone: dict[str, list[Any]],
    zone_rois: dict[str, tuple[int, int, int, int]],
    discard_top: str | None,
    *,
    dry_run: bool,
    log_fn: Callable[[str], None],
    ctx: TurnPlayContext | None = None,
) -> tuple[Any, Any, dict, dict, list, str | None, list[str], bool]:
    """
    摸/吃 → 等待 → 再侦察；仍须摸牌则重试点击暗牌堆。
    返回最后一项 draw_ok 表示摸牌阶段是否结束。
    """
    actions: list[str] = []
    chow_label: str | None = None
    before_labels = hand_label_count(by_zone)
    before_coords = len(hand)
    stable_bgr = bgr
    stable_scout_result = scout_result
    stable_by_zone = by_zone
    stable_zone_rois = zone_rois
    stable_hand = list(hand)
    draw_ok = False

    ready = _hand_ready_count()
    if _effective_hand_count(hand, by_zone) >= ready:
        log_fn(f"[出牌] 手牌已 {before_labels} 张（≥{ready}），跳过摸牌点击")
        return (
            bgr,
            scout_result,
            by_zone,
            zone_rois,
            hand,
            chow_label,
            actions,
            True,
        )

    for attempt in range(1, _draw_retry_max() + 1):
        if ctx is not None:
            ctx.check_aborted("摸牌")
            if ctx.must_dump_only() and attempt > 1:
                log_fn("[出牌] 时间不足，停止摸牌重试")
                break

        if attempt == 1:
            fought = _try_fight_before_draw(
                hand,
                dry_run=dry_run,
                log_fn=log_fn,
                ctx=ctx,
            )
            if fought:
                actions.append("fight")
                if not dry_run:
                    # 给按钮反馈/动画留极短时间，避免紧接着点牌堆吞点击。
                    time.sleep(0.25)

        clicks = 2 if attempt > 1 else 1
        if attempt > 1:
            log_fn(f"[出牌] 摸牌重试 {attempt}/{_draw_retry_max()}（clicks={clicks}）…")

        draw_key = _execute_draw(
            hand,
            discard_top,
            bgr,
            dry_run=dry_run,
            log_fn=log_fn,
            clicks=clicks,
            ctx=ctx,
        )
        if draw_key not in actions:
            actions.append(draw_key)
        if draw_key == "discard" and discard_top:
            chow_label = discard_top

        # 吃牌（点击弃牌顶）后，按规则直接进入出牌阶段，不再继续摸牌循环。
        if draw_key == "discard":
            refreshed = _rescout_hand_after_draw(
                scout,
                grab_frame,
                bgr,
                dry_run=dry_run,
                log_fn=log_fn,
                wait_sec=_post_draw_wait_sec() + 0.3 * (attempt - 1),
                prev_scout_result=scout_result,
                ctx=ctx,
                reason="吃牌后",
            )
            if refreshed is not None:
                bgr, scout_result, by_zone, zone_rois, hand = refreshed
            log_fn("[出牌] 已吃牌：跳过后续摸牌判定，直接进入出牌阶段")
            draw_ok = True
            break

        if dry_run:
            refreshed = _rescout_hand_after_draw(
                scout,
                grab_frame,
                bgr,
                dry_run=True,
                log_fn=log_fn,
                prev_scout_result=scout_result,
                ctx=ctx,
            )
            if refreshed is not None:
                bgr, scout_result, by_zone, zone_rois, hand = refreshed
            draw_ok = True
            break

        refreshed = _rescout_hand_after_draw(
            scout,
            grab_frame,
            bgr,
            dry_run=False,
            log_fn=log_fn,
            wait_sec=_post_draw_wait_sec() + 0.3 * (attempt - 1),
            prev_scout_result=scout_result,
            ctx=ctx,
        )
        if refreshed is None:
            log_fn("[出牌] 摸牌后再侦察失败")
            break
        bgr, scout_result, by_zone, zone_rois, hand = refreshed

        draw_ok, msg = draw_phase_succeeded(
            bgr,
            hand,
            by_zone,
            before_labels=before_labels,
            before_coords=before_coords,
        )
        log_fn(
            f"[出牌] 摸牌后手牌 VLM={hand_label_count(by_zone)} 有坐标={len(hand)}"
            f"（摸前 VLM={before_labels}）→ {msg}"
        )
        if _effective_hand_count(hand, by_zone) >= _effective_hand_count(stable_hand, stable_by_zone):
            stable_bgr = bgr
            stable_scout_result = scout_result
            stable_by_zone = by_zone
            stable_zone_rois = zone_rois
            stable_hand = list(hand)
        if draw_ok:
            break
        log_fn("[出牌] 暗牌堆点击可能未生效（请确认游戏窗口在最前）")

    if not draw_ok:
        guard = _hand_drop_guard_cards()
        cur_n = _effective_hand_count(hand, by_zone)
        stable_n = _effective_hand_count(stable_hand, stable_by_zone)
        if stable_n - cur_n >= guard and stable_hand:
            log_fn(
                f"[出牌] 识别防抖：摸牌后快刷手牌从 {stable_n} 降到 {cur_n}（阈值={guard}），"
                "回退到稳定快照"
            )
            bgr = stable_bgr
            scout_result = stable_scout_result
            by_zone = stable_by_zone
            zone_rois = stable_zone_rois
            hand = stable_hand

    return bgr, scout_result, by_zone, zone_rois, hand, chow_label, actions, draw_ok


def _execute_dump(
    hand: list,
    *,
    scout: Any | None = None,
    grab_frame: Callable[[], tuple[Any, str] | tuple[Any, str, Any]] | None = None,
    scout_result: Any | None = None,
    dropped_this_turn: bool = False,
    dry_run: bool,
    log_fn: Callable[[str], None],
    ctx: TurnPlayContext | None = None,
) -> dict[str, Any] | None:
    from tongits_rules import pick_dump_card, zone_labels_from_detections

    if ctx is not None:
        ctx.check_aborted("Dump")
        ctx.log_budget("Dump 前")

    if (
        scout is not None
        and grab_frame is not None
        and scout_result is not None
    ):
        hand_before_refresh = list(hand)
        before_n = len(hand_before_refresh)
        refreshed = _refresh_hand_only(
            scout,
            grab_frame,
            prev_scout_result=scout_result,
            log_fn=log_fn,
            ctx=ctx,
            reason="Dump 前",
        )
        if refreshed is not None:
            _, scout_result, _, hand = refreshed
            after_n = len(hand)
            guard = _hand_drop_guard_cards()
            if before_n - after_n >= guard:
                log_fn(
                    f"[出牌] 识别防抖：Dump 前快刷手牌从 {before_n} 降到 {after_n}（阈值={guard}），"
                    "忽略本次快刷，沿用旧手牌"
                )
                hand = hand_before_refresh
        elif dropped_this_turn and not dry_run:
            # Drop 后手牌会自动居中，预算紧张时补一次 YOLO-only 重锚，避免点到旧坐标。
            yolo_refreshed = _refresh_hand_yolo_only(
                scout,
                grab_frame,
                prev_scout_result=scout_result,
                log_fn=log_fn,
                reason="Dump 前(Drop后)",
            )
            if yolo_refreshed is not None:
                _, scout_result, _, hand = yolo_refreshed

    # 保护：避免把已亮明牌（my_melds）中的同标签误当手牌去点 Dump。
    # 理论上一副牌同标签唯一，若 hand 与 my_melds 重叠，通常是识别/分区抖动。
    my_meld_labels: set[str] = set()
    if scout_result is not None:
        by_zone_now = getattr(scout_result, "by_zone", {}) or {}
        if isinstance(by_zone_now, dict):
            zone_map = zone_labels_from_detections(by_zone_now)
            my_meld_labels = set(zone_map.get("my_melds") or [])
    before_filter_n = len(hand)
    hand_pick_pool = [c for c in hand if c.label not in my_meld_labels]
    safe_y = _dump_min_center_y()
    hand_pick_pool = [c for c in hand_pick_pool if int(getattr(c, "center_y", 0)) >= safe_y]
    if len(hand_pick_pool) < before_filter_n:
        log_fn(
            f"[出牌] Dump 候选过滤：排除 my_melds 重叠 + 非底部手牌区 "
            f"(min_y={safe_y})，候选 {len(hand_pick_pool)}/{before_filter_n}"
        )
    if not hand_pick_pool:
        raise RuntimeError(
            f"Dump 安全保护触发：候选牌均不在底部手牌区 (center_y<{safe_y})"
        )
    target = pick_dump_card(hand_pick_pool)
    if target is None:
        raise RuntimeError("无法选择要打出的牌")

    log_fn(
        f"[出牌] Dump 散牌 {target.label}（散牌点={target.scatter}）"
        f" @({target.center_x},{target.center_y})"
    )
    card_res = _click_xy(
        target.center_x,
        target.center_y,
        label=f"hand_{target.label}",
        dry_run=dry_run,
        log_fn=log_fn,
        ctx=ctx,
    )
    if not card_res.get("ok"):
        raise RuntimeError(f"点击手牌失败: {card_res.get('error')}")

    time.sleep(0.25 if not dry_run else 0.0)
    dump_res = _click_action("dump", dry_run=dry_run, log_fn=log_fn, ctx=ctx)
    if not dump_res.get("ok"):
        raise RuntimeError(f"Dump 按钮点击失败: {dump_res.get('error')}")
    return {"target": target, "card_click": card_res, "dump_click": dump_res}


def execute_scout_coord_turn(
    scout: Any,
    scout_result: Any,
    bgr: Any,
    *,
    grab_frame: Callable[[], tuple[Any, str] | tuple[Any, str, Any]] | None = None,
    dry_run: bool | None = None,
    log_fn: Callable[[str], None] | None = None,
    turn_started_at: float | None = None,
) -> dict[str, Any]:
    """
    完整回合：Draw → 再侦察 → Drop/Sapaw 循环 → Dump 弃牌。
    受 TurnPlayContext 约束：绿圈 abort、时间预算、Dump 前 hand-only 快刷。
    """
    _log = log_fn or _log_fn_default
    dry = auto_play_dry_run() if dry_run is None else dry_run
    actions: list[str] = []
    t0 = time.perf_counter()
    ctx = TurnPlayContext.create(
        grab_frame=grab_frame,
        dry_run=dry,
        log_fn=_log,
        started_at=turn_started_at,
    )
    ctx.log_budget("开局")

    try:
        return _execute_scout_coord_turn_body(
            scout,
            scout_result,
            bgr,
            grab_frame=grab_frame,
            dry=dry,
            _log=_log,
            actions=actions,
            t0=t0,
            ctx=ctx,
        )
    except TurnAbortedError as e:
        _log(f"[出牌] 回合中止: {e}")
        return {
            "ok": False,
            "aborted": True,
            "error": str(e),
            "actions": actions,
            "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
            "dry_run": dry,
        }


def _execute_scout_coord_turn_body(
    scout: Any,
    scout_result: Any,
    bgr: Any,
    *,
    grab_frame: Callable[[], tuple[Any, str] | tuple[Any, str, Any]] | None,
    dry: bool,
    _log: Callable[[str], None],
    actions: list[str],
    t0: float,
    ctx: TurnPlayContext,
) -> dict[str, Any]:
    by_zone = getattr(scout_result, "by_zone", {}) or {}
    zone_rois = _resolve_zone_rois(scout_result, bgr)
    hand = hand_from_scout_detections(by_zone.get("player_hand") or [])
    discard_top = discard_top_from_scout(by_zone)
    chow_label: str | None = None

    if not hand:
        return {
            "ok": False,
            "error": "手牌无有效坐标，无法自动出牌",
            "actions": actions,
            "dry_run": dry,
        }

    _log(
        f"[出牌] 开局手牌 {len(hand)} 张: "
        + ", ".join(f"{c.label}@({c.center_x},{c.center_y})" for c in hand)
    )
    if discard_top:
        _log(f"[出牌] 弃牌顶牌: {discard_top}")

    if _abort_if_over_budget_enabled() and ctx.remaining() <= -_over_budget_abort_sec():
        raise TurnAbortedError(
            f"时间预算已超时 {abs(ctx.remaining()):.1f}s，跳过本轮点击防误触"
        )

    need_draw, draw_reason = needs_draw_phase(bgr, hand, by_zone)
    draw_ok = True
    if need_draw:
        _log(f"[出牌] 进入摸牌阶段：{draw_reason}")
        (
            bgr,
            scout_result,
            by_zone,
            zone_rois,
            hand,
            chow_label,
            draw_actions,
            draw_ok,
        ) = _draw_and_rescout(
            scout,
            grab_frame,
            bgr,
            scout_result,
            hand,
            by_zone,
            zone_rois,
            discard_top,
            dry_run=dry,
            log_fn=_log,
            ctx=ctx,
        )
        actions.extend(draw_actions)
        if not draw_ok and not dry and not hand:
            return {
                "ok": False,
                "error": "摸牌后仍无有效手牌坐标",
                "actions": actions,
                "hand": [],
                "dry_run": dry,
            }
        if not draw_ok and not dry:
            _log("[出牌] 警告：摸牌可能未成功，仍按当前手牌尝试亮牌/弃牌")
    else:
        _log(f"[出牌] {draw_reason}")

    if not hand:
        return {
            "ok": False,
            "error": "无有效手牌坐标，无法弃牌",
            "actions": actions,
            "dry_run": dry,
        }

    hand, by_zone, bgr_m, scout_m, meld_actions = _execute_meld_phase(
        hand,
        by_zone,
        zone_rois,
        scout=scout,
        grab_frame=grab_frame,
        dry_run=dry,
        log_fn=_log,
        chow_label=chow_label,
        prev_scout_result=scout_result,
        ctx=ctx,
    )
    actions.extend(meld_actions)
    if bgr_m is not None:
        bgr = bgr_m
    if scout_m is not None:
        scout_result = scout_m

    ctx.check_aborted("Dump 阶段")
    if ctx.must_dump_only() and meld_actions:
        _log("[出牌] 时间紧张，已进入 Dump 预留段")

    dump_info = _execute_dump(
        hand,
        scout=scout,
        grab_frame=grab_frame,
        scout_result=scout_result,
        dropped_this_turn=any(a.startswith("drop") for a in meld_actions),
        dry_run=dry,
        log_fn=_log,
        ctx=ctx,
    )
    if dump_info is not None:
        actions.append("dump")

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    _log(f"[出牌] 回合完成（{'dry-run' if dry else '已执行'}，{elapsed_ms:.0f}ms）")
    return {
        "ok": True,
        "actions": actions,
        "hand": [c.label for c in hand],
        "dump": dump_info,
        "elapsed_ms": elapsed_ms,
        "dry_run": dry,
    }
