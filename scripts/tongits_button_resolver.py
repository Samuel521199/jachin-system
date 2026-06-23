#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tongits 动作按钮解析：OmniParser 每帧 ID 会变，禁止写死 7/12/14/15/24。

策略（按优先级）：
  1. OCR content 精确匹配 Drop/Fight/Group/Dump（动作条 y 带过滤）
  2. 牌堆 Deck：牌桌中央区域启发式
  3. Special：左下 Chow 区或 VLM 映射（可选）
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger("tongits_resolver")

ACTION_NAMES = ("drop", "fight", "group", "dump", "deck", "special")

# 用户文档中的「参考 ID」仅作说明，运行时不得直接使用
LEGACY_REFERENCE_IDS: dict[str, int] = {
    "drop": 12,
    "fight": 13,
    "group": 14,
    "dump": 15,
    "deck": 7,
    "special": 24,
}

_LABEL_PATTERNS: dict[str, tuple[str, ...]] = {
    "drop": (r"^drop$",),
    "fight": (r"^fight$",),
    "group": (r"^group$",),
    "dump": (r"^dump$",),
    "special": (r"^special$", r"chow", r"sapaw"),
    "deck": (r"^deck$", r"draw", r"stock"),
}


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.environ.get(name) or str(default)).strip())
    except ValueError:
        return default


def _element_center(row: dict[str, Any]) -> tuple[int, int]:
    b = row.get("bbox_xyxy_pixels") or []
    if isinstance(b, (list, tuple)) and len(b) >= 4:
        return (int((int(b[0]) + int(b[2])) / 2), int((int(b[1]) + int(b[3])) / 2))
    cx = row.get("center_x")
    cy = row.get("center_y")
    if cx is not None and cy is not None:
        return int(cx), int(cy)
    cen = row.get("center_xy_pixels") or [0, 0]
    return int(cen[0]), int(cen[1])


def _bbox_span_x(row: dict[str, Any]) -> int:
    b = row.get("bbox_xyxy_pixels") or []
    if isinstance(b, (list, tuple)) and len(b) >= 4:
        return abs(int(b[2]) - int(b[0]))
    return 0


def _normalize_label(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").strip().lower())


def _match_action(content: str) -> str | None:
    norm = _normalize_label(content)
    if not norm:
        return None
    for action, patterns in _LABEL_PATTERNS.items():
        if action == "deck":
            continue
        for pat in patterns:
            if re.search(pat, norm, re.I):
                return action
    return None


def resolve_action_buttons(
    elements: list[dict[str, Any]],
    *,
    screen_height: int = 1080,
    hand_card_ids: set[int] | None = None,
) -> dict[str, int]:
    """
    从 OmniParser 全量元素（含 content）解析动作名 → 当前帧 element_id。
    """
    btn_min_y = _env_int("TONGITS_BUTTON_MIN_Y", int(screen_height * 0.58))
    btn_max_y = _env_int("TONGITS_BUTTON_MAX_Y", int(screen_height * 0.82))
    deck_y_min = _env_int("TONGITS_DECK_Y_MIN", int(screen_height * 0.28))
    deck_y_max = _env_int("TONGITS_DECK_Y_MAX", int(screen_height * 0.52))
    deck_x_min = _env_int("TONGITS_DECK_X_MIN", 550)
    deck_x_max = _env_int("TONGITS_DECK_X_MAX", 1200)
    hand_ids = hand_card_ids or set()

    found: dict[str, int] = {}
    deck_candidates: list[tuple[int, int, int]] = []  # score, eid, area

    for row in elements:
        try:
            eid = int(row["id"])
        except (TypeError, ValueError):
            continue
        if eid in hand_ids:
            continue
        cx, cy = _element_center(row)
        content = str(row.get("content") or "")
        action = _match_action(content)
        if action and action != "deck":
            if btn_min_y <= cy <= btn_max_y:
                found[action] = eid
                logger.info(
                    "[resolver] OCR 命中 %s → id=%s @(%s,%s) text=%r",
                    action,
                    eid,
                    cx,
                    cy,
                    content[:40],
                )
            else:
                logger.debug(
                    "[resolver] 忽略 %s id=%s y=%s 不在动作条 [%s,%s]",
                    action,
                    eid,
                    cy,
                    btn_min_y,
                    btn_max_y,
                )
            continue

        if deck_y_min <= cy <= deck_y_max and deck_x_min <= cx <= deck_x_max:
            span = _bbox_span_x(row)
            norm = _normalize_label(content)
            score = span
            if re.search(r"deck|draw|stock|\(\d+", norm, re.I):
                score += 500
            if re.search(r"tongit|ante|point", norm, re.I):
                score -= 800
            if len(norm) <= 4 and norm.isdigit():
                score += 200
            deck_candidates.append((score, eid, span))

    if "deck" not in found and deck_candidates:
        deck_candidates.sort(reverse=True)
        score, eid, span = deck_candidates[0]
        found["deck"] = eid
        cx, cy = _element_center(next(r for r in elements if int(r["id"]) == eid))
        logger.info(
            "[resolver] Deck 启发 → id=%s @(%s,%s) score=%s",
            eid,
            cx,
            cy,
            score,
        )

    missing = [a for a in ACTION_NAMES if a not in found]
    if missing:
        logger.warning("[resolver] 未解析到按钮: %s", missing)
    else:
        logger.info("[resolver] 完整映射: %s", found)

    return found


def resolve_action_element_id(
    action: str,
    action_id_map: dict[str, int],
    *,
    elements_dict: dict[int, dict[str, int]] | None = None,
) -> int:
    """执行前将语义动作名转为当前帧 element_id。"""
    key = action.strip().lower()
    if key in action_id_map:
        eid = int(action_id_map[key])
        if elements_dict is not None and eid not in elements_dict:
            raise RuntimeError(
                f"动作 {key} 映射 id={eid} 不在本帧 elements_dict 中"
            )
        return eid
    legacy = LEGACY_REFERENCE_IDS.get(key)
    hint = f"参考文档ID={legacy}（勿直接使用）" if legacy else ""
    raise RuntimeError(
        f"无法解析动作按钮 {key!r}；当前映射={action_id_map}。{hint} "
        "请确认 OmniParser 已识别 Drop/Fight/Group/Dump 文案，或设置 TONGITS_*_Y 阈值。"
    )

def log_legacy_id_trap(action: str, wrong_id: int, elements: list[dict[str, Any]]) -> None:
    """若误用写死 ID，打印该 ID 实际 OCR 内容（便于排错）。"""
    for row in elements:
        if int(row.get("id", -1)) == wrong_id:
            cx, cy = _element_center(row)
            logger.error(
                "[resolver] 写死 ID 陷阱: action=%s 误用 id=%s → (%s,%s) OCR=%r",
                action,
                wrong_id,
                cx,
                cy,
                row.get("content"),
            )
            return
