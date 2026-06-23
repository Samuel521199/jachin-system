#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
扑克牌裁剪区过滤 — 供观战爬虫 / 认牌流水线共用。

避免 OmniParser 把筹码、路径、按钮 OCR 框当成「手牌」。
"""
from __future__ import annotations

import os
import re
from typing import Any

# 标准模板名：花色 + 点数（含 DK = 方块 K）
_LABEL_STEM_RE = re.compile(
    r"^([SHCD])(A|[2-9]|10|J|Q|K)$",
    re.IGNORECASE,
)

# OCR 内容命中则直接丢弃（非牌面）
_REJECT_CONTENT_RE = [
    re.compile(p, re.I)
    for p in (
        r"\\",  # 路径
        r"^https?",
        r"users?",
        r"drop|fight|group|dump|deck|special|sort|autosort",
        r"ante|point|guest|lark|tongit|image|ciuse|me\b",
        r"\.00\s*k\b",  # 9.00K 筹码
        r"^\d+\.\d+k$",
        r"^\d{1,2}:\d{2}",  # 时间
        r"^ai$",
        r"^az$",
        r"^0\s*[e:]",
        r"okr|poke|coin",
    )
]


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.environ.get(name) or str(default)).strip())
    except ValueError:
        return default


def _bbox_of(row: dict[str, Any]) -> tuple[int, int, int, int] | None:
    b = row.get("bbox_xyxy_pixels") or []
    if isinstance(b, (list, tuple)) and len(b) >= 4:
        return int(b[0]), int(b[1]), int(b[2]), int(b[3])
    return None


def _center_of(row: dict[str, Any]) -> tuple[int, int]:
    b = _bbox_of(row)
    if b:
        return (b[0] + b[2]) // 2, (b[1] + b[3]) // 2
    return int(row.get("center_x") or 0), int(row.get("center_y") or 0)


def is_rejected_ocr_content(content: str) -> bool:
    text = (content or "").strip()
    if not text:
        return False
    if len(text) > 14:
        return True
    for pat in _REJECT_CONTENT_RE:
        if pat.search(text):
            return True
    return False


def is_valid_label_stem(label: str) -> bool:
    return _LABEL_STEM_RE.match((label or "").strip().upper()) is not None


def looks_like_playing_card_crop(crop_bgr: Any) -> tuple[bool, str]:
    """
    轻量 CV 启发式：像一张竖向扑克牌，而非长条筹码/图标。
    返回 (通过, 原因)。
    """
    import cv2
    import numpy as np

    if crop_bgr is None or crop_bgr.size == 0:
        return False, "空裁剪"
    h, w = crop_bgr.shape[:2]
    if w < 22 or h < 30:
        return False, f"尺寸过小 {w}x{h}"
    if w > 180 or h > 220:
        return False, f"尺寸过大 {w}x{h}"

    aspect = w / max(h, 1)
    if aspect < 0.35 or aspect > 1.15:
        return False, f"宽高比异常 {aspect:.2f}"

    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    std = float(gray.std())
    if std < 12:
        return False, f"对比度过低 std={std:.1f}"

    # 牌面通常有浅色区域 + 部分饱和红/黑
    mean_brightness = float(gray.mean())
    if mean_brightness < 25 or mean_brightness > 245:
        return False, f"亮度异常 mean={mean_brightness:.0f}"

    hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
    # 红 suit 或深色边框
    red1 = cv2.inRange(hsv, (0, 50, 50), (12, 255, 255))
    red2 = cv2.inRange(hsv, (165, 50, 50), (180, 255, 255))
    red_ratio = (red1 > 0).sum() + (red2 > 0).sum()
    light_ratio = (gray > 160).sum()
    total = max(gray.size, 1)
    if red_ratio / total < 0.002 and light_ratio / total < 0.08:
        return False, "缺少牌面纹理（无红/高亮区）"

    return True, "ok"


def filter_playing_card_candidates(
    elements_dict: dict[int, dict[str, int]],
    elements: list[dict[str, Any]] | None,
    *,
    screen_width: int = 1920,
    screen_height: int = 1080,
    exclude_ids: set[int] | None = None,
) -> list[int]:
    """
    严格筛「像手牌」的 element_id（观战爬虫默认用此函数，不用纯 y 阈值）。
    """
    sw = screen_width or 1920
    sh = screen_height or 1080
    y_min = _env_float("SPECTATOR_CARD_Y_MIN_RATIO", 0.76)
    y_max = _env_float("SPECTATOR_CARD_Y_MAX_RATIO", 0.93)
    x_min = _env_float("SPECTATOR_CARD_X_MIN_RATIO", 0.28)
    x_max = _env_float("SPECTATOR_CARD_X_MAX_RATIO", 0.88)

    min_w = int(os.environ.get("SPECTATOR_CARD_MIN_W") or "24")
    min_h = int(os.environ.get("SPECTATOR_CARD_MIN_H") or "32")
    max_w = int(os.environ.get("SPECTATOR_CARD_MAX_W") or "140")
    max_h = int(os.environ.get("SPECTATOR_CARD_MAX_H") or "180")

    y0, y1 = int(sh * y_min), int(sh * y_max)
    x0, x1 = int(sw * x_min), int(sw * x_max)
    skip = exclude_ids or set()

    rows_by_id: dict[int, dict[str, Any]] = {}
    if elements:
        for row in elements:
            try:
                rows_by_id[int(row["id"])] = row
            except (TypeError, ValueError):
                pass

    out: list[int] = []
    for eid in sorted(elements_dict.keys()):
        if eid in skip:
            continue
        row = rows_by_id.get(eid, {})
        cx, cy = _center_of(row if row else {"center_x": elements_dict[eid]["center_x"], "center_y": elements_dict[eid]["center_y"]})
        if not (x0 <= cx <= x1 and y0 <= cy <= y1):
            continue

        content = str(row.get("content") or "")
        if is_rejected_ocr_content(content):
            continue

        bbox = _bbox_of(row) if row else None
        if bbox:
            x1b, y1b, x2b, y2b = bbox
            bw, bh = x2b - x1b, y2b - y1b
            if bw < min_w or bh < min_h or bw > max_w or bh > max_h:
                continue
            ar = bw / max(bh, 1)
            if ar < 0.35 or ar > 1.2:
                continue

        out.append(eid)
    return out
