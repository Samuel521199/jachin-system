#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tongits UI 本地探针 — Fast Mode 阶段一：判断摸牌堆还是吃弃牌顶牌。

本游戏无独立 Special 按钮；摸牌/吃牌通过点击中央牌堆或弃牌堆顶牌（黄箭头提示）。
旧版在 (466,570) 探「Special 按钮」会误读桌布花纹，导致未摸牌就 Dump。
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache

import cv2
import numpy as np

logger = logging.getLogger("tongits_ui_probe")

# 1920×1080：中央牌堆 / 弃牌顶牌（与 tongits_turn_executor 默认一致）
_DEFAULT_DECK_CENTER = (859, 412)
_DEFAULT_DISCARD_CENTER = (1010, 415)
_DEFAULT_PILE_ROI = (100, 130)
_DEFAULT_CHALLENGE_CENTER = (835, 816)
_DEFAULT_FOLD_CENTER = (1126, 816)
_DEFAULT_DUEL_POINT_CENTER = (962, 828)
_DEFAULT_CONTINUE_CENTER = (650, 965)
_DEFAULT_DETAILS_CENTER = (1270, 965)
_DEFAULT_SETTLEMENT_TIMER_CENTER = (960, 944)
# 正常对局“四色动作栏”按钮中心（与 tongits_turn_executor 默认一致）。
# 结算页此处为面板/空桌，无四色按钮——用于把“正常画面”排除出结算判定。
_DEFAULT_DROP_CENTER = (518, 726)
_DEFAULT_GROUP_CENTER = (1104, 724)
_DEFAULT_DUMP_CENTER = (1399, 724)


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.environ.get(name) or str(default)).strip())
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or ("1" if default else "0")).strip().lower()
    return raw in ("1", "true", "yes", "on")


def _parse_center_env(name: str, default: tuple[int, int]) -> tuple[int, int]:
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


def _pile_roi_xywh(
    center_env: str,
    default_center: tuple[int, int],
    *,
    screen_shape: tuple[int, ...] | None = None,
) -> tuple[int, int, int, int]:
    cx, cy = _parse_center_env(center_env, default_center)
    w = _env_int("TONGITS_PILE_PROBE_WIDTH", _DEFAULT_PILE_ROI[0])
    h = _env_int("TONGITS_PILE_PROBE_HEIGHT", _DEFAULT_PILE_ROI[1])
    left = max(0, cx - w // 2)
    top = max(0, cy - h // 2)
    if screen_shape and len(screen_shape) >= 2:
        sh, sw = int(screen_shape[0]), int(screen_shape[1])
        w = min(w, sw - left)
        h = min(h, sh - top)
    return left, top, max(1, w), max(1, h)


def deck_pile_roi_xywh(screen_shape: tuple[int, ...] | None = None) -> tuple[int, int, int, int]:
    return _pile_roi_xywh("TONGITS_BUTTON_DECK_XY", _DEFAULT_DECK_CENTER, screen_shape=screen_shape)


def discard_pile_roi_xywh(screen_shape: tuple[int, ...] | None = None) -> tuple[int, int, int, int]:
    return _pile_roi_xywh("TONGITS_BUTTON_DISCARD_XY", _DEFAULT_DISCARD_CENTER, screen_shape=screen_shape)


def _yellow_arrow_mask(hsv: np.ndarray) -> np.ndarray:
    """UI 摸牌/吃牌提示用的黄色下箭头。"""
    return cv2.inRange(hsv, np.array([14, 140, 160], dtype=np.uint8), np.array([42, 255, 255], dtype=np.uint8))


def _card_face_mask(hsv: np.ndarray) -> np.ndarray:
    """弃牌顶牌牌面：高亮、中等饱和。"""
    return (hsv[:, :, 2] >= 145) & (hsv[:, :, 1] >= 25) & (hsv[:, :, 1] <= 200)


def probe_pile_stats(bgr: np.ndarray, roi: tuple[int, int, int, int]) -> dict[str, float]:
    left, top, w, h = roi
    crop = bgr[top : top + h, left : left + w]
    if crop.size == 0:
        return {"yellow_ratio": 0.0, "card_ratio": 0.0, "roi": roi}

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    yellow = _yellow_arrow_mask(hsv)
    card = _card_face_mask(hsv)
    n = max(1, yellow.size)
    return {
        "yellow_ratio": float(np.count_nonzero(yellow)) / n,
        "card_ratio": float(np.count_nonzero(card)) / n,
        "roi": roi,
    }


def probe_draw_phase_stats(bgr: np.ndarray) -> dict[str, object]:
    """统计牌堆 / 弃牌堆 ROI 内的黄箭头与牌面特征。"""
    deck_roi = deck_pile_roi_xywh(bgr.shape)
    discard_roi = discard_pile_roi_xywh(bgr.shape)
    deck = probe_pile_stats(bgr, deck_roi)
    discard = probe_pile_stats(bgr, discard_roi)
    return {"deck": deck, "discard": discard}


def is_chow_available(bgr: np.ndarray) -> bool:
    """
    弃牌堆是否可吃（可点顶牌 Chow）。

    须同时满足：
      - 弃牌 ROI 内有可见牌面（顶牌存在）
      - 弃牌 ROI 内有集中黄箭头（非整块桌布高饱和误报）
      - 黄箭头像素占比在合理区间（箭头较小，全 ROI 饱和视为误报）
    """
    if not _env_bool("TONGITS_AUTO_CHOW", True):
        return False

    stats = probe_draw_phase_stats(bgr)
    disc = stats["discard"]
    yellow_ratio = float(disc["yellow_ratio"])
    card_ratio = float(disc["card_ratio"])

    y_min = float(os.environ.get("TONGITS_CHOW_YELLOW_RATIO_MIN") or "0.012")
    y_max = float(os.environ.get("TONGITS_CHOW_YELLOW_RATIO_MAX") or "0.35")
    card_min = float(os.environ.get("TONGITS_CHOW_CARD_RATIO_MIN") or "0.08")

    available = card_ratio >= card_min and y_min <= yellow_ratio <= y_max
    logger.info(
        "[ui_probe] 弃牌堆 ROI=%s card_ratio=%.3f yellow_ratio=%.3f "
        "(need card>=%.2f yellow in [%.3f,%.3f]) → %s",
        disc.get("roi"),
        card_ratio,
        yellow_ratio,
        card_min,
        y_min,
        y_max,
        "可吃" if available else "默认摸牌堆",
    )
    return available


def is_draw_phase_hint(bgr: np.ndarray) -> bool:
    """中央牌堆区域是否出现摸牌黄箭头（确认处于摸牌阶段）。"""
    stats = probe_draw_phase_stats(bgr)
    deck = stats["deck"]
    yellow_ratio = float(deck["yellow_ratio"])
    thr = float(os.environ.get("TONGITS_DECK_YELLOW_RATIO_MIN") or "0.008")
    active = yellow_ratio >= thr
    logger.info(
        "[ui_probe] 牌堆 ROI=%s yellow_ratio=%.3f (thr=%.3f) → %s",
        deck.get("roi"),
        yellow_ratio,
        thr,
        "摸牌阶段" if active else "无黄箭头",
    )
    return active


def decide_blind_draw_action(bgr: np.ndarray) -> str:
    """
    阶段一：返回 'discard'（吃弃牌顶牌）或 'deck'（摸中央牌堆）。

    仅作 UI 辅助；自动出牌主路径以 tongits_rules.decide_draw_action 规则为准。
    """
    if not _env_bool("TONGITS_AUTO_CHOW", True):
        return "deck"
    if is_chow_available(bgr):
        return "discard"
    return "deck"


def pile_center_from_roi(roi: tuple[int, int, int, int]) -> tuple[int, int]:
    """牌堆 ROI 中心（略偏下，对准牌面/黄箭头区域）。"""
    left, top, w, h = roi
    cx = left + max(1, w) // 2
    cy = top + int(max(1, h) * 0.55)
    return cx, cy


def deck_click_xy(bgr: np.ndarray) -> tuple[int, int]:
    """
    暗牌堆点击坐标：优先 TONGITS_BUTTON_DECK_XY，否则取 deck ROI 中心。

    摸牌 = 点击橙色牌背暗牌堆（非 Group/Drop 按钮）。
    """
    env = (os.environ.get("TONGITS_BUTTON_DECK_XY") or "").strip()
    if env:
        parts = [p.strip() for p in env.split(",")]
        if len(parts) == 2:
            try:
                return int(parts[0]), int(parts[1])
            except ValueError:
                pass
    roi = deck_pile_roi_xywh(bgr.shape)
    cx, cy = pile_center_from_roi(roi)
    stats = probe_draw_phase_stats(bgr)
    deck = stats["deck"]
    logger.info(
        "[ui_probe] 暗牌堆点击 @(%d,%d) roi=%s yellow=%.3f",
        cx,
        cy,
        deck.get("roi"),
        float(deck.get("yellow_ratio") or 0),
    )
    return cx, cy


def discard_click_xy(bgr: np.ndarray) -> tuple[int, int]:
    """弃牌顶牌点击坐标（吃牌 / Chow）。"""
    env = (os.environ.get("TONGITS_BUTTON_DISCARD_XY") or "").strip()
    if env:
        parts = [p.strip() for p in env.split(",")]
        if len(parts) == 2:
            try:
                return int(parts[0]), int(parts[1])
            except ValueError:
                pass
    roi = discard_pile_roi_xywh(bgr.shape)
    cx, cy = pile_center_from_roi(roi)
    logger.info("[ui_probe] 弃牌顶点击 @(%d,%d) roi=%s", cx, cy, roi)
    return cx, cy


def _duel_button_roi_xywh(
    center_env: str,
    default_center: tuple[int, int],
    *,
    screen_shape: tuple[int, ...] | None = None,
) -> tuple[int, int, int, int]:
    cx, cy = _parse_center_env(center_env, default_center)
    w = _env_int("TONGITS_DUEL_BTN_PROBE_WIDTH", 240)
    h = _env_int("TONGITS_DUEL_BTN_PROBE_HEIGHT", 96)
    left = max(0, cx - w // 2)
    top = max(0, cy - h // 2)
    if screen_shape and len(screen_shape) >= 2:
        sh, sw = int(screen_shape[0]), int(screen_shape[1])
        w = min(w, sw - left)
        h = min(h, sh - top)
    return left, top, max(1, w), max(1, h)


def challenge_offer_roi_xywh(screen_shape: tuple[int, ...] | None = None) -> tuple[int, int, int, int]:
    return _duel_button_roi_xywh(
        "TONGITS_BUTTON_CHALLENGE_XY",
        _DEFAULT_CHALLENGE_CENTER,
        screen_shape=screen_shape,
    )


def fold_offer_roi_xywh(screen_shape: tuple[int, ...] | None = None) -> tuple[int, int, int, int]:
    return _duel_button_roi_xywh(
        "TONGITS_BUTTON_FOLD_XY",
        _DEFAULT_FOLD_CENTER,
        screen_shape=screen_shape,
    )


def challenge_offer_click_xy(bgr: np.ndarray) -> tuple[int, int]:
    roi = challenge_offer_roi_xywh(bgr.shape)
    return pile_center_from_roi(roi)


def fold_offer_click_xy(bgr: np.ndarray) -> tuple[int, int]:
    roi = fold_offer_roi_xywh(bgr.shape)
    return pile_center_from_roi(roi)


def duel_point_roi_xywh(screen_shape: tuple[int, ...] | None = None) -> tuple[int, int, int, int]:
    return _duel_button_roi_xywh(
        "TONGITS_DUEL_POINT_XY",
        _DEFAULT_DUEL_POINT_CENTER,
        screen_shape=screen_shape,
    )


@lru_cache(maxsize=1)
def _duel_digit_templates() -> dict[str, list[np.ndarray]]:
    """
    本地 OCR 数字模板（0-9），用于决斗 POINT 中间数字识别。
    """
    out: dict[str, list[np.ndarray]] = {str(i): [] for i in range(10)}
    canvas_size = (36, 26)  # h, w
    for d in range(10):
        txt = str(d)
        for scale, thick in ((1.0, 2), (0.9, 2), (0.8, 2)):
            img = np.zeros(canvas_size, dtype=np.uint8)
            cv2.putText(
                img,
                txt,
                (3, 29),
                cv2.FONT_HERSHEY_SIMPLEX,
                scale,
                255,
                thick,
                cv2.LINE_AA,
            )
            out[txt].append(img)
    return out


def duel_point_local_ocr(bgr: np.ndarray, *, log_details: bool = False) -> int | None:
    """
    决斗 POINT 本地 OCR（无云依赖）：
    - ROI 二值化
    - 连通域切分数字
    - 与内置模板匹配
    """
    roi = duel_point_roi_xywh(bgr.shape)
    left, top, w, h = roi
    crop = bgr[top : top + h, left : left + w]
    if crop.size == 0:
        return None

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    # 数字通常为深色，背景较亮
    _, bw = cv2.threshold(gray, 120, 255, cv2.THRESH_BINARY_INV)
    bw = cv2.medianBlur(bw, 3)

    n, labels, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
    boxes: list[tuple[int, int, int, int]] = []
    for i in range(1, n):
        x, y, ww, hh, area = stats[i]
        if area < 35:
            continue
        if hh < 10 or ww < 4:
            continue
        if hh > int(h * 0.95) or ww > int(w * 0.6):
            continue
        boxes.append((x, y, ww, hh))
    if not boxes:
        return None
    boxes.sort(key=lambda b: b[0])

    templates = _duel_digit_templates()
    digits: list[str] = []
    scores: list[float] = []
    for x, y, ww, hh in boxes[:3]:
        glyph = bw[y : y + hh, x : x + ww]
        glyph = cv2.resize(glyph, (26, 36), interpolation=cv2.INTER_AREA)
        best_d = ""
        best_s = -1.0
        for d, tmpls in templates.items():
            for tmpl in tmpls:
                s = cv2.matchTemplate(glyph, tmpl, cv2.TM_CCOEFF_NORMED)[0][0]
                if s > best_s:
                    best_s = float(s)
                    best_d = d
        if best_d:
            digits.append(best_d)
            scores.append(best_s)
    if not digits:
        return None

    avg_score = float(sum(scores) / max(1, len(scores)))
    thr = float(os.environ.get("TONGITS_DUEL_POINT_LOCAL_OCR_SCORE_MIN") or "0.34")
    if avg_score < thr:
        if log_details:
            logger.info("[ui_probe] 决斗POINT本地OCR置信低 score=%.3f thr=%.3f", avg_score, thr)
        return None
    try:
        val = int("".join(digits))
    except ValueError:
        return None
    if val < 0 or val > 200:
        return None
    if log_details:
        logger.info("[ui_probe] 决斗POINT本地OCR=%d score=%.3f", val, avg_score)
    return val


def _fight_offer_button_color_ratio(
    bgr: np.ndarray,
    roi: tuple[int, int, int, int],
    *,
    kind: str,
) -> float:
    left, top, w, h = roi
    crop = bgr[top : top + h, left : left + w]
    if crop.size == 0:
        return 0.0
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    if kind == "challenge":
        mask = cv2.inRange(hsv, (8, 120, 130), (36, 255, 255))
    else:
        mask = cv2.inRange(hsv, (90, 80, 95), (132, 255, 255))
    return float(np.count_nonzero(mask) / max(1, mask.size))


def probe_fight_offer_stats(bgr: np.ndarray) -> dict[str, float]:
    """决斗弹窗按钮色块检测：Challenge(橙黄) + Fold(蓝)。"""
    c_roi = challenge_offer_roi_xywh(bgr.shape)
    f_roi = fold_offer_roi_xywh(bgr.shape)
    c_ratio = _fight_offer_button_color_ratio(bgr, c_roi, kind="challenge")
    f_ratio = _fight_offer_button_color_ratio(bgr, f_roi, kind="fold")
    return {
        "challenge_ratio": c_ratio,
        "fold_ratio": f_ratio,
        "challenge_roi": c_roi,
        "fold_roi": f_roi,
    }


def is_fight_offer_overlay(bgr: np.ndarray, *, log_inactive: bool = False) -> bool:
    stats = probe_fight_offer_stats(bgr)
    c_thr = float(os.environ.get("TONGITS_FIGHT_OFFER_CHALLENGE_RATIO_MIN") or "0.06")
    f_thr = float(os.environ.get("TONGITS_FIGHT_OFFER_FOLD_RATIO_MIN") or "0.06")
    active = stats["challenge_ratio"] >= c_thr and stats["fold_ratio"] >= f_thr
    if active or log_inactive:
        logger.info(
            "[ui_probe] 决斗弹窗 challenge=%.3f fold=%.3f (thr=%.3f/%.3f) → %s",
            stats["challenge_ratio"],
            stats["fold_ratio"],
            c_thr,
            f_thr,
            "决斗中" if active else "无",
        )
    return active


def _settlement_button_roi_xywh(
    center_env: str,
    default_center: tuple[int, int],
    *,
    screen_shape: tuple[int, ...] | None = None,
) -> tuple[int, int, int, int]:
    cx, cy = _parse_center_env(center_env, default_center)
    w = _env_int("TONGITS_SETTLEMENT_BTN_PROBE_WIDTH", 290)
    h = _env_int("TONGITS_SETTLEMENT_BTN_PROBE_HEIGHT", 108)
    left = max(0, cx - w // 2)
    top = max(0, cy - h // 2)
    if screen_shape and len(screen_shape) >= 2:
        sh, sw = int(screen_shape[0]), int(screen_shape[1])
        w = min(w, sw - left)
        h = min(h, sh - top)
    return left, top, max(1, w), max(1, h)


def continue_button_roi_xywh(screen_shape: tuple[int, ...] | None = None) -> tuple[int, int, int, int]:
    return _settlement_button_roi_xywh(
        "TONGITS_BUTTON_CONTINUE_XY",
        _DEFAULT_CONTINUE_CENTER,
        screen_shape=screen_shape,
    )


def details_button_roi_xywh(screen_shape: tuple[int, ...] | None = None) -> tuple[int, int, int, int]:
    return _settlement_button_roi_xywh(
        "TONGITS_BUTTON_DETAILS_XY",
        _DEFAULT_DETAILS_CENTER,
        screen_shape=screen_shape,
    )


def settlement_timer_roi_xywh(screen_shape: tuple[int, ...] | None = None) -> tuple[int, int, int, int]:
    return _settlement_button_roi_xywh(
        "TONGITS_SETTLEMENT_TIMER_XY",
        _DEFAULT_SETTLEMENT_TIMER_CENTER,
        screen_shape=screen_shape,
    )


def continue_button_click_xy(bgr: np.ndarray) -> tuple[int, int]:
    roi = continue_button_roi_xywh(bgr.shape)
    return pile_center_from_roi(roi)


def continue_button_has_highlight_border(
    bgr: np.ndarray,
    *,
    log_details: bool = False,
) -> bool:
    """
    CONTINUE 按钮“高亮边框”保险校验：
    - 仅统计按钮外环(border)中的亮黄/亮白像素占比
    - 占比过低则视为非目标按钮，禁止点击
    """
    roi = continue_button_roi_xywh(bgr.shape)
    left, top, w, h = roi
    crop = bgr[top : top + h, left : left + w]
    if crop.size == 0:
        return False
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    yellow = cv2.inRange(hsv, (10, 90, 130), (42, 255, 255))
    white = cv2.inRange(hsv, (0, 0, 185), (180, 70, 255))
    hi = cv2.bitwise_or(yellow, white)

    hh, ww = hi.shape[:2]
    t = max(3, min(hh, ww) // 8)
    border = np.zeros_like(hi)
    border[:t, :] = 255
    border[-t:, :] = 255
    border[:, :t] = 255
    border[:, -t:] = 255
    border_n = max(1, int(np.count_nonzero(border)))
    border_hi = cv2.bitwise_and(hi, border)
    ratio = float(np.count_nonzero(border_hi)) / border_n
    thr = float(os.environ.get("TONGITS_SETTLEMENT_CONTINUE_BORDER_RATIO_MIN") or "0.085")
    ok = ratio >= thr
    if log_details:
        logger.info(
            "[ui_probe] CONTINUE 边框高亮 ratio=%.3f thr=%.3f → %s",
            ratio,
            thr,
            "通过" if ok else "拒绝点击",
        )
    return ok


def _color_ratio_for_roi(
    bgr: np.ndarray,
    roi: tuple[int, int, int, int],
    *,
    kind: str,
) -> float:
    left, top, w, h = roi
    crop = bgr[top : top + h, left : left + w]
    if crop.size == 0:
        return 0.0
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    if kind == "continue":
        mask = cv2.inRange(hsv, (10, 95, 120), (40, 255, 255))
    elif kind == "details":
        mask = cv2.inRange(hsv, (90, 70, 95), (132, 255, 255))
    else:
        # 倒计时圆环：棕橙底 + 绿色进度 + 亮色数字
        brown = cv2.inRange(hsv, (8, 70, 50), (30, 255, 230))
        green = cv2.inRange(hsv, (40, 70, 60), (95, 255, 255))
        white = cv2.inRange(hsv, (0, 0, 170), (180, 75, 255))
        mask = cv2.bitwise_or(cv2.bitwise_or(brown, green), white)
    return float(np.count_nonzero(mask) / max(1, mask.size))


def probe_round_settlement_stats(bgr: np.ndarray) -> dict[str, float]:
    c_roi = continue_button_roi_xywh(bgr.shape)
    d_roi = details_button_roi_xywh(bgr.shape)
    t_roi = settlement_timer_roi_xywh(bgr.shape)
    c_ratio = _color_ratio_for_roi(bgr, c_roi, kind="continue")
    d_ratio = _color_ratio_for_roi(bgr, d_roi, kind="details")
    t_ratio = _color_ratio_for_roi(bgr, t_roi, kind="timer")
    return {
        "continue_ratio": c_ratio,
        "details_ratio": d_ratio,
        "timer_ratio": t_ratio,
        "continue_roi": c_roi,
        "details_roi": d_roi,
        "timer_roi": t_roi,
    }


def _button_color_ratio(bgr: np.ndarray, center: tuple[int, int], kind: str) -> float:
    cx, cy = center
    w = _env_int("TONGITS_ACTION_BTN_PROBE_WIDTH", 150)
    h = _env_int("TONGITS_ACTION_BTN_PROBE_HEIGHT", 72)
    sh, sw = bgr.shape[:2]
    left = max(0, min(sw - 1, cx - w // 2))
    top = max(0, min(sh - 1, cy - h // 2))
    crop = bgr[top : top + h, left : left + w]
    if crop.size == 0:
        return 0.0
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    if kind == "red":
        mask = cv2.bitwise_or(
            cv2.inRange(hsv, (0, 120, 90), (10, 255, 255)),
            cv2.inRange(hsv, (160, 120, 90), (180, 255, 255)),
        )
    elif kind == "green":
        mask = cv2.inRange(hsv, (40, 80, 70), (90, 255, 255))
    else:  # blue
        mask = cv2.inRange(hsv, (95, 90, 80), (130, 255, 255))
    return float(np.count_nonzero(mask)) / max(1, mask.size)


def normal_action_bar_present(bgr: np.ndarray, *, log_details: bool = False) -> bool:
    """
    正常对局“四色动作栏”检测：Drop(红) + Group(绿) + Dump(蓝) 同时强命中。
    结算页底部是 CONTINUE/DETAILS（无红 Drop / 绿 Group / 蓝 Dump），故可据此判定“非结算”。
    """
    thr = float(os.environ.get("TONGITS_ACTION_BAR_RATIO_MIN") or "0.35")
    r = _button_color_ratio(bgr, _parse_center_env("TONGITS_BUTTON_DROP_XY", _DEFAULT_DROP_CENTER), "red")
    g = _button_color_ratio(bgr, _parse_center_env("TONGITS_BUTTON_GROUP_XY", _DEFAULT_GROUP_CENTER), "green")
    b = _button_color_ratio(bgr, _parse_center_env("TONGITS_BUTTON_DUMP_XY", _DEFAULT_DUMP_CENTER), "blue")
    present = (r >= thr) and (g >= thr) and (b >= thr)
    if log_details or present:
        logger.info(
            "[ui_probe] 动作栏 drop(R)=%.3f group(G)=%.3f dump(B)=%.3f thr=%.3f → %s",
            r, g, b, thr, "正常对局(非结算)" if present else "非动作栏",
        )
    return present


def is_round_settlement_overlay(bgr: np.ndarray, *, log_inactive: bool = False) -> bool:
    """
    回合结算弹窗（WIN/LOSE/DEFEAT）检测：
    - 左下 CONTINUE（黄橙）
    - 右下 DETAILS（蓝）
    - 中间倒计时圆环（棕橙）
    - 硬负向门：若“正常四色动作栏”在场（Drop红/Group绿/Dump蓝），直接判非结算。
    """
    # 先做负向排除：正常对局画面（含对手回合）会把底部 ROI 误判为结算，
    # 但其顶部一定有四色动作栏，而结算页没有。
    if normal_action_bar_present(bgr):
        if log_inactive:
            logger.info("[ui_probe] 结算弹窗 → 无（命中正常四色动作栏，判非结算）")
        return False
    stats = probe_round_settlement_stats(bgr)
    c_thr = float(os.environ.get("TONGITS_SETTLEMENT_CONTINUE_RATIO_MIN") or "0.06")
    d_thr = float(os.environ.get("TONGITS_SETTLEMENT_DETAILS_RATIO_MIN") or "0.06")
    t_thr = float(os.environ.get("TONGITS_SETTLEMENT_TIMER_RATIO_MIN") or "0.02")
    c_strong = float(os.environ.get("TONGITS_SETTLEMENT_CONTINUE_RATIO_STRONG") or "0.11")
    d_strong = float(os.environ.get("TONGITS_SETTLEMENT_DETAILS_RATIO_STRONG") or "0.11")
    with_timer = (
        stats["continue_ratio"] >= c_thr
        and stats["details_ratio"] >= d_thr
        and stats["timer_ratio"] >= t_thr
    )
    # 强按钮兜底也必须看到最小计时圈证据，避免把普通牌桌底部按钮误判为结算页。
    timer_floor = float(os.environ.get("TONGITS_SETTLEMENT_TIMER_RATIO_FLOOR") or "0.008")
    strong_buttons = (
        stats["continue_ratio"] >= c_strong
        and stats["details_ratio"] >= d_strong
        and stats["timer_ratio"] >= timer_floor
    )
    active = with_timer or strong_buttons
    if active or log_inactive:
        logger.info(
            "[ui_probe] 结算弹窗 continue=%.3f details=%.3f timer=%.3f "
            "(thr=%.3f/%.3f/%.3f strong=%.3f/%.3f) → %s",
            stats["continue_ratio"],
            stats["details_ratio"],
            stats["timer_ratio"],
            c_thr,
            d_thr,
            t_thr,
            c_strong,
            d_strong,
            "结算中" if active else "无",
        )
    return active


# 兼容旧探针 CLI
def special_button_roi_xywh(screen_shape: tuple[int, ...] | None = None) -> tuple[int, int, int, int]:
    return discard_pile_roi_xywh(screen_shape)


def probe_special_button_stats(bgr: np.ndarray) -> dict[str, float]:
    stats = probe_draw_phase_stats(bgr)
    disc = stats["discard"]
    return {
        "mean_s": disc["card_ratio"] * 100,
        "mean_v": disc["yellow_ratio"] * 100,
        "active_ratio": disc["yellow_ratio"],
        "roi": disc["roi"],
    }


def is_chow_button_active(bgr: np.ndarray) -> bool:
    return is_chow_available(bgr)


def capture_screen_bgr() -> np.ndarray:
    try:
        import pyautogui
    except ImportError as e:
        raise RuntimeError("请安装 pyautogui") from e
    shot = pyautogui.screenshot()
    return cv2.cvtColor(np.array(shot), cv2.COLOR_RGB2BGR)
