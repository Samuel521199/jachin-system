#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OmniParser 输出几何过滤 — 物理区域 + 长宽比/面积硬规则，挡掉上半屏杂讯。

用法：
  from vision_filter import clean_omniparser_output, parse_elements_from_json

环境变量（可选覆盖默认阈值）：
  VISION_FILTER_Y_MIN_RATIO      规则1：y1 下限比例，默认 0.4
  VISION_FILTER_CARD_AR_MIN/MAX  卡牌高宽比，默认 1.2 / 1.8
  VISION_FILTER_BTN_AR_MIN/MAX   按钮高宽比，默认 0.2 / 0.6
  VISION_FILTER_CARD_AREA_MIN/MAX  卡牌面积，默认 2000 / 30000
  VISION_FILTER_BTN_MIN_AREA       按钮最小面积，默认 4500（滤掉 15.00K 等小筹码块）
  VISION_FILTER_BTN_AREA_MAX       按钮最大面积，默认 30000
  VISION_FILTER_AREA_MIN/MAX       兼容旧名，等同卡牌面积
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("vision_filter")

# 默认几何阈值（与客户规范一致）
_DEFAULT_Y_MIN_RATIO = 0.4
_DEFAULT_CARD_AR = (1.2, 1.8)
_DEFAULT_BTN_AR = (0.2, 0.6)
_DEFAULT_CARD_AREA = (2000, 30000)
_DEFAULT_BTN_AREA = (4500, 30000)


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.environ.get(name) or str(default)).strip())
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.environ.get(name) or str(default)).strip())
    except ValueError:
        return default


def _filter_thresholds() -> dict[str, float | int | tuple[float, float]]:
    return {
        "y_min_ratio": _env_float("VISION_FILTER_Y_MIN_RATIO", _DEFAULT_Y_MIN_RATIO),
        "card_ar": (
            _env_float("VISION_FILTER_CARD_AR_MIN", _DEFAULT_CARD_AR[0]),
            _env_float("VISION_FILTER_CARD_AR_MAX", _DEFAULT_CARD_AR[1]),
        ),
        "btn_ar": (
            _env_float("VISION_FILTER_BTN_AR_MIN", _DEFAULT_BTN_AR[0]),
            _env_float("VISION_FILTER_BTN_AR_MAX", _DEFAULT_BTN_AR[1]),
        ),
        "card_area": (
            _env_int(
                "VISION_FILTER_CARD_AREA_MIN",
                _env_int("VISION_FILTER_AREA_MIN", _DEFAULT_CARD_AREA[0]),
            ),
            _env_int(
                "VISION_FILTER_CARD_AREA_MAX",
                _env_int("VISION_FILTER_AREA_MAX", _DEFAULT_CARD_AREA[1]),
            ),
        ),
        "btn_area": (
            _env_int("VISION_FILTER_BTN_MIN_AREA", _DEFAULT_BTN_AREA[0]),
            _env_int("VISION_FILTER_BTN_AREA_MAX", _DEFAULT_BTN_AREA[1]),
        ),
    }


def _parse_bbox(value: Any) -> tuple[int, int, int, int] | None:
    """解析 [x1,y1,x2,y2] 或 dict 内 bbox 字段。"""
    if value is None:
        return None
    if isinstance(value, dict):
        b = value.get("bbox_xyxy_pixels") or value.get("bbox") or value.get("bbox_xyxy")
        if b is None and all(k in value for k in ("x1", "y1", "x2", "y2")):
            b = [value["x1"], value["y1"], value["x2"], value["y2"]]
        return _parse_bbox(b)
    if isinstance(value, (list, tuple)) and len(value) >= 4:
        try:
            x1, y1, x2, y2 = (int(round(float(v))) for v in value[:4])
        except (TypeError, ValueError):
            return None
        if x2 < x1:
            x1, x2 = x2, x1
        if y2 < y1:
            y1, y2 = y2, y1
        if x2 <= x1 or y2 <= y1:
            return None
        return x1, y1, x2, y2
    return None


def normalize_elements_dict(
    raw_elements: Any,
) -> dict[int, list[int]]:
    """
    统一为 { element_id: [x1, y1, x2, y2] }。

    支持：
      - { id: [x1,y1,x2,y2], ... }
      - { id: {"bbox_xyxy_pixels": [...]}, ... }
      - [ {"id": 0, "bbox_xyxy_pixels": [...]}, ... ]  (OmniParser elements)
    """
    out: dict[int, list[int]] = {}

    if isinstance(raw_elements, list):
        for row in raw_elements:
            if not isinstance(row, dict):
                continue
            try:
                eid = int(row.get("id", -1))
            except (TypeError, ValueError):
                continue
            bbox = _parse_bbox(row)
            if bbox:
                out[eid] = list(bbox)
        return out

    if not isinstance(raw_elements, dict):
        return out

    for key, value in raw_elements.items():
        try:
            eid = int(key)
        except (TypeError, ValueError):
            continue
        bbox = _parse_bbox(value)
        if bbox:
            out[eid] = list(bbox)
    return out


def parse_elements_from_json(
    json_path: str | Path,
    *,
    key: str | None = None,
) -> tuple[dict[int, list[int]], int, int]:
    """
    从 OmniParser JSON 提取 bbox 字典与屏幕尺寸。

    Returns:
        (elements_dict, screen_width, screen_height)
    """
    path = Path(json_path)
    data = json.loads(path.read_text(encoding="utf-8"))

    if key and key in data:
        raw = data[key]
    elif "raw_elements_dict" in data:
        raw = data["raw_elements_dict"]
    elif "elements_dict" in data:
        raw = data["elements_dict"]
    elif "elements" in data:
        raw = data["elements"]
    else:
        raw = data

    elements = normalize_elements_dict(raw)

    sw, sh = 1920, 1080
    size = data.get("image_size") or {}
    try:
        if size.get("w"):
            sw = int(size["w"])
        if size.get("h"):
            sh = int(size["h"])
    except (TypeError, ValueError):
        pass
    return elements, sw, sh


def _bbox_metrics(
    bbox: list[int],
) -> tuple[int, int, int, int, int, float, int] | None:
    """返回 x1,y1,x2,y2, width, height, aspect_ratio(h/w), area。"""
    try:
        x1, y1, x2, y2 = (int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3]))
    except (IndexError, TypeError, ValueError):
        return None
    w = x2 - x1
    h = y2 - y1
    if w <= 0 or h <= 0:
        return None
    ar = h / float(w)
    area = w * h
    return x1, y1, x2, y2, w, h, ar, area


def _passes_physical_barrier(
    y1: int,
    screen_height: int,
    y_min_ratio: float,
) -> bool:
    """规则1：只保留 y1 > screen_height * ratio（画面中下区）。"""
    return y1 > int(screen_height * y_min_ratio)


def _passes_card_geometry(
    ar: float,
    area: int,
    card_ar: tuple[float, float],
    area_rng: tuple[int, int],
) -> bool:
    """规则2：竖向牌面。"""
    return card_ar[0] <= ar <= card_ar[1] and area_rng[0] <= area <= area_rng[1]


def _passes_button_geometry(
    ar: float,
    area: int,
    btn_ar: tuple[float, float],
    area_rng: tuple[int, int],
) -> bool:
    """规则3：横向按钮条。"""
    return btn_ar[0] <= ar <= btn_ar[1] and area_rng[0] <= area <= area_rng[1]


def classify_element_geometry(
    bbox: list[int],
    screen_width: int,
    screen_height: int,
    *,
    thresholds: dict[str, Any] | None = None,
) -> str | None:
    """
    判定单框是否通过过滤。

    Returns:
        "card" | "button" | None（丢弃）
    """
    _ = screen_width
    th = thresholds or _filter_thresholds()
    m = _bbox_metrics(bbox)
    if m is None:
        return None
    x1, y1, _x2, _y2, _w, _h, ar, area = m

    if not _passes_physical_barrier(y1, screen_height, float(th["y_min_ratio"])):
        return None

    if _passes_card_geometry(ar, area, th["card_ar"], th["card_area"]):  # type: ignore[arg-type]
        return "card"
    if _passes_button_geometry(ar, area, th["btn_ar"], th["btn_area"]):  # type: ignore[arg-type]
        return "button"
    return None


def clean_omniparser_output(
    raw_elements_dict: dict[Any, Any] | list[Any],
    screen_width: int,
    screen_height: int,
    *,
    include_meta: bool = False,
) -> dict[int, list[int]] | dict[int, dict[str, Any]]:
    """
    过滤 OmniParser 元素，仅保留通过物理结界 +（卡牌几何 或 按钮几何）的框。

    Args:
        raw_elements_dict: { id: [x1,y1,x2,y2] } 或带 bbox 的结构 / elements 列表
        screen_width, screen_height: 全屏像素尺寸
        include_meta: True 时值为 {"bbox": [...], "kind": "card"|"button"}

    Returns:
        cleaned_dict，键为 element_id
    """
    normalized = normalize_elements_dict(raw_elements_dict)
    original_count = len(normalized)

    th = _filter_thresholds()
    cleaned: dict[int, Any] = {}
    stats = {"card": 0, "button": 0}

    for eid, bbox in sorted(normalized.items()):
        try:
            kind = classify_element_geometry(
                bbox, screen_width, screen_height, thresholds=th
            )
            if kind is None:
                continue
            stats[kind] = stats.get(kind, 0) + 1
            if include_meta:
                cleaned[eid] = {"bbox": bbox, "kind": kind}
            else:
                cleaned[eid] = bbox
        except Exception as e:
            logger.debug("[vision_filter] 跳过 id=%s: %s", eid, e)
            continue

    removed = original_count - len(cleaned)
    logger.info(
        "[vision_filter] 原始框=%d 滤除=%d 保留=%d (牌=%d 按钮=%d) 屏=%dx%d",
        original_count,
        removed,
        len(cleaned),
        stats.get("card", 0),
        stats.get("button", 0),
        screen_width,
        screen_height,
    )
    return cleaned  # type: ignore[return-value]


def main() -> int:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="OmniParser 几何过滤（无绘图）")
    ap.add_argument("--json", required=True, help="omniparser_result.json")
    ap.add_argument("--width", type=int, default=0, help="屏宽，0=从 JSON 读取")
    ap.add_argument("--height", type=int, default=0, help="屏高，0=从 JSON 读取")
    ap.add_argument("--meta", action="store_true", help="输出含 kind 字段")
    args = ap.parse_args()

    raw, sw, sh = parse_elements_from_json(args.json)
    if args.width > 0:
        sw = args.width
    if args.height > 0:
        sh = args.height

    cleaned = clean_omniparser_output(
        raw, sw, sh, include_meta=args.meta
    )
    print(json.dumps(cleaned, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
