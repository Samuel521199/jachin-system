"""
Windows 11「标准」计算器按键网格（窗口客户区相对坐标）。

当 OmniParser YOLO 对无边框按键检出过少时，由 `build_keypad_probe_elements` 按实测比例
补全 elements_dict，并绘制带红框 ID 的标注图（坐标仍由探测管线标定，非 VLM 估算）。

标准模式键盘为 4 列 × 6 行（含 %/CE/C/⌫ 与 1/x/x²/√/÷ 两行），数字区从第 3 行起：
  行0: %  CE  C   ⌫
  行1: 1/x x² √x ÷
  行2: 7   8   9   ×
  行3: 4   5   6   -
  行4: 1   2   3   +
  行5: +/- 0   .   =  （= 为右下角蓝色大键，中心偏下）

坐标 SSOT：scripts/omnioutput/20260603_145817_589_raw.png（502×810）像素采样标定。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Sequence

# 列/行通用网格（其它键）
_COL_X: tuple[float, ...] = (0.131, 0.371, 0.622, 0.863)
_ROW_Y: dict[str, float] = {
    "fn0": 0.398,
    "fn1": 0.478,
    "d7": 0.558,
    "d4": 0.638,
    "d1": 0.717,
    "d0": 0.835,  # +/- 0 . 行中部；= 单独见下
}

# 125×4 与常用键：原图实测中心 (x_ratio, y_ratio)
_MEASURED_RATIO: dict[str, tuple[float, float]] = {
    "1": (0.131, 0.717),
    "2": (0.371, 0.717),
    "5": (0.371, 0.638),
    "×": (0.863, 0.558),
    "4": (0.131, 0.638),
    "=": (0.859, 0.910),
}

_BUTTON_CELL: dict[str, tuple[int, str]] = {
    "%": (0, "fn0"),
    "CE": (1, "fn0"),
    "C": (2, "fn0"),
    "7": (0, "d7"),
    "8": (1, "d7"),
    "9": (2, "d7"),
    "×": (3, "d7"),
    "*": (3, "d7"),
    "÷": (3, "fn1"),
    "4": (0, "d4"),
    "5": (1, "d4"),
    "6": (2, "d4"),
    "-": (3, "d4"),
    "1": (0, "d1"),
    "2": (1, "d1"),
    "3": (2, "d1"),
    "+": (3, "d1"),
    "0": (1, "d0"),
    ".": (2, "d0"),
}

_SEQUENCE_125_X_4: tuple[str, ...] = ("1", "2", "5", "×", "4", "=")

# 标准键盘探测顺序（与 Win11 标准模式布局一致，用于 keypad_probe 编号）
KEYPAD_PROBE_ORDER: tuple[str, ...] = (
    "%",
    "CE",
    "C",
    "÷",
    "7",
    "8",
    "9",
    "×",
    "4",
    "5",
    "6",
    "-",
    "1",
    "2",
    "3",
    "+",
    "0",
    ".",
    "=",
)


def _normalize_key(key: str) -> str:
    k = (key or "").strip()
    if k in ("×", "*", "x", "X"):
        return "×"
    return k


def button_ratio(label: str) -> tuple[float, float] | None:
    k = _normalize_key(label)
    if k in _MEASURED_RATIO:
        return _MEASURED_RATIO[k]
    if k == "=":
        return _MEASURED_RATIO["="]
    cell = _BUTTON_CELL.get(k)
    if not cell:
        return None
    col_i, row_key = cell
    if col_i >= len(_COL_X) or row_key not in _ROW_Y:
        return None
    return _COL_X[col_i], _ROW_Y[row_key]


def parse_window_region_from_note(note: str) -> tuple[int, int, int, int] | None:
    m = re.search(r"window_region=\((\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)", note or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))


def grid_coordinates_for_region(
    left: int,
    top: int,
    width: int,
    height: int,
    *,
    sequence: Sequence[str] = _SEQUENCE_125_X_4,
) -> dict[int, tuple[int, int]]:
    """返回 id 0..n-1 → 绝对屏幕 (x,y)，与 sequence 顺序一致。"""
    out: dict[int, tuple[int, int]] = {}
    for idx, key in enumerate(sequence):
        ratio = button_ratio(key)
        if not ratio:
            continue
        rx, ry = ratio
        out[idx] = (int(left + rx * width), int(top + ry * height))
    return out


def grid_elements_for_llm(
    coords: dict[int, tuple[int, int]],
    *,
    sequence: Sequence[str] = _SEQUENCE_125_X_4,
) -> list[dict]:
    rows: list[dict] = []
    for idx, key in enumerate(sequence):
        if idx not in coords:
            continue
        x, y = coords[idx]
        rows.append(
            {
                "id": idx,
                "center_x": x,
                "center_y": y,
                "content": _normalize_key(key),
                "source": "calculator_grid_fallback",
            }
        )
    return rows


def build_keypad_probe_elements(width: int, height: int) -> list[dict[str, Any]]:
    """
    按窗口客户区宽高生成按键表（center_x/y 为**窗口内**像素，与 OmniParser 一致）。
    id 从 0 起，与标注图红框编号一致。
    """
    rows: list[dict[str, Any]] = []
    eid = 0
    for key in KEYPAD_PROBE_ORDER:
        ratio = button_ratio(key)
        if not ratio:
            continue
        rx, ry = ratio
        cx = int(round(rx * width))
        cy = int(round(ry * height))
        half_w = max(20, width // 10)
        half_h = max(20, height // 16)
        x1, y1 = max(0, cx - half_w), max(0, cy - half_h)
        x2, y2 = min(width, cx + half_w), min(height, cy + half_h)
        rows.append(
            {
                "id": eid,
                "bbox_xyxy_pixels": [x1, y1, x2, y2],
                "center_xy_pixels": [float(cx), float(cy)],
                "center_x": cx,
                "center_y": cy,
                "content": _normalize_key(key),
                "type": "button",
                "source": "calculator_keypad_probe",
            }
        )
        eid += 1
    return rows


def render_keypad_annotated_image(
    image_path: Path | str,
    elements: list[dict[str, Any]],
    dest_path: Path | str,
) -> None:
    """在截屏上绘制红框 + 数字 ID（风格对齐 OmniParser 标注图）。"""
    from PIL import Image, ImageDraw

    src = Path(image_path)
    dst = Path(dest_path)
    img = Image.open(src).convert("RGB")
    dr = ImageDraw.Draw(img)
    for row in elements:
        try:
            eid = int(row["id"])
        except (TypeError, ValueError, KeyError):
            continue
        bbox = row.get("bbox_xyxy_pixels") or []
        if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
            x1, y1, x2, y2 = (int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3]))
        else:
            cx = int(row.get("center_x") or 0)
            cy = int(row.get("center_y") or 0)
            x1, y1, x2, y2 = cx - 25, cy - 25, cx + 25, cy + 25
        dr.rectangle([x1, y1, x2, y2], outline=(255, 0, 0), width=2)
        dr.text((x1 + 2, max(0, y1 + 2)), str(eid), fill=(255, 0, 0))
    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst, format="JPEG", quality=92)


def omniparser_misses_keypad(elements: list[dict], *, min_keys: int = 10) -> bool:
    """启发式：elements 过少或缺少数字/运算符文案 → 需网格兜底。"""
    if len(elements) < min_keys:
        return True
    digit_ops = set("0123456789.×÷+-=*/")
    hits = 0
    for row in elements:
        c = str(row.get("content") or "")
        if any(ch in c for ch in digit_ops):
            hits += 1
    return hits < 4


def save_grid_preview_image(
    left: int,
    top: int,
    width: int,
    height: int,
    coords: dict[int, tuple[int, int]],
    *,
    sequence: Sequence[str] = _SEQUENCE_125_X_4,
    dest_path: str,
) -> None:
    """在截屏上绘制网格点击点，便于校准（可选）。"""
    try:
        import pyautogui
        from PIL import ImageDraw
    except ImportError:
        return

    img = pyautogui.screenshot(region=(left, top, width, height))
    dr = ImageDraw.Draw(img)
    for idx, key in enumerate(sequence):
        if idx not in coords:
            continue
        ax, ay = coords[idx]
        lx, ly = ax - left, ay - top
        dr.ellipse([lx - 10, ly - 10, lx + 10, ly + 10], outline="red", width=3)
        dr.text((lx + 12, ly - 8), f"{idx}:{key}", fill="red")
    img.save(dest_path, format="JPEG", quality=92)
