"""
全屏截图 → 带 [1][2]… 编号的元素表 + 标注图。

优先 RapidOCR/EasyOCR（复用 gameqa ocr_engine）；可选 YOLO（VISION_UI_YOLO_MODEL / GAMEQA_YOLO_MODEL）。
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

logger = logging.getLogger("vision_ui.screen_parser")


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


@dataclass
class ParsedElement:
    element_id: str
    text: str
    x: float
    y: float
    score: float
    source: str  # ocr | yolo
    bbox: tuple[int, int, int, int] | None = None


@dataclass
class ParsedScreen:
    ok: bool
    elements: dict[str, dict[str, Any]]
    element_list: list[ParsedElement] = field(default_factory=list)
    annotated_png: bytes = b""
    annotated_path: str = ""
    raw_png_path: str = ""
    screen_width: int = 0
    screen_height: int = 0
    notes: str = ""
    error: str = ""


def capture_screen_png() -> tuple[bytes, int, int, str]:
    """返回 (png_bytes, width, height, error)。"""
    try:
        import pyautogui
    except ImportError as e:
        return b"", 0, 0, f"pyautogui_not_installed:{e}"
    try:
        img = pyautogui.screenshot()
        w, h = img.size
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue(), int(w), int(h), ""
    except Exception as e:
        return b"", 0, 0, f"screenshot_failed:{e!r}"


def _yolo_candidates(png: bytes) -> list[tuple[str, float, float, float]]:
    model = (os.environ.get("VISION_UI_YOLO_MODEL") or os.environ.get("GAMEQA_YOLO_MODEL") or "").strip()
    if not model:
        return []
    try:
        from l3_client.local_mcps.gameqa_mcp.core.vision_engine import VisionEngine

        vr = VisionEngine().analyze_sync(png)
        notes = (vr.raw_notes or "").lower()
        if "mock" in notes or not vr.elements:
            return []
        out: list[tuple[str, float, float, float]] = []
        for name, (x, y) in vr.elements.items():
            label = str(name or "").strip() or "yolo_obj"
            out.append((label, float(x), float(y), 0.9))
        return out
    except Exception as e:
        logger.debug("[vision_ui] YOLO skip: %s", e)
        return []


def _ocr_candidates(png: bytes) -> list[tuple[str, float, float, float]]:
    try:
        from l3_client.local_mcps.gameqa_mcp.core.ocr_engine import ocr_line_boxes_from_png
    except ImportError as e:
        return []
    lines, notes = ocr_line_boxes_from_png(png)
    if not lines:
        logger.debug("[vision_ui] OCR empty notes=%s", notes)
        return []
    min_score = _env_float("VISION_UI_OCR_MIN_SCORE", 0.45)
    min_len = _env_int("VISION_UI_OCR_MIN_TEXT_LEN", 1)
    out: list[tuple[str, float, float, float]] = []
    for ln in lines:
        if not isinstance(ln, dict):
            continue
        t = str(ln.get("text") or "").strip()
        if len(t) < min_len:
            continue
        try:
            sc = float(ln.get("score") or 1.0)
        except (TypeError, ValueError):
            sc = 1.0
        if sc < min_score:
            continue
        try:
            cx = float(ln["cx"])
            cy = float(ln["cy"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append((t, cx, cy, sc))
    return out


def _merge_and_number(
    ocr_rows: list[tuple[str, float, float, float]],
    yolo_rows: list[tuple[str, float, float, float]],
) -> list[ParsedElement]:
    """按屏幕位置排序后分配 1..N。"""
    combined: list[tuple[str, float, float, float, str]] = []
    for t, x, y, sc in ocr_rows:
        combined.append((t, x, y, sc, "ocr"))
    # YOLO：与 OCR 中心过近则跳过（同一控件）
    min_dist = _env_float("VISION_UI_MERGE_MIN_DIST_PX", 28.0)

    def _too_close(x: float, y: float) -> bool:
        for _, ox, oy, _, _ in combined:
            if (ox - x) ** 2 + (oy - y) ** 2 < min_dist**2:
                return True
        return False

    for t, x, y, sc in yolo_rows:
        if _too_close(x, y):
            continue
        combined.append((t, x, y, sc, "yolo"))

    combined.sort(key=lambda r: (round(r[2] / 40), r[1]))  # 行簇 + 左到右
    max_n = _env_int("VISION_UI_MAX_ELEMENTS", 48)
    elements: list[ParsedElement] = []
    for i, (t, x, y, sc, src) in enumerate(combined[:max_n], start=1):
        eid = str(i)
        elements.append(
            ParsedElement(
                element_id=eid,
                text=t[:200],
                x=round(x, 1),
                y=round(y, 1),
                score=round(sc, 3),
                source=src,
            )
        )
    return elements


def _annotate_png(png: bytes, elements: list[ParsedElement]) -> bytes:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return png
    try:
        im = Image.open(BytesIO(png)).convert("RGB")
    except Exception:
        return png
    draw = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("arial.ttf", 18)
    except Exception:
        font = ImageFont.load_default()
    for el in elements:
        x, y = int(el.x), int(el.y)
        label = f"[{el.element_id}]"
        tw, th = 24, 16
        try:
            bbox = draw.textbbox((0, 0), label, font=font)
            tw = bbox[2] - bbox[0] + 6
            th = bbox[3] - bbox[1] + 4
        except Exception:
            pass
        draw.rectangle((x - 2, y - th - 2, x + tw, y + 2), fill=(220, 20, 20))
        draw.text((x, y - th), label, fill=(255, 255, 255), font=font)
        draw.ellipse((x - 4, y - 4, x + 4, y + 4), outline=(255, 220, 0), width=2)
    out = BytesIO()
    im.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _vision_ui_data_dir() -> Path:
    base = Path.home() / ".jachin" / "vision_ui"
    base.mkdir(parents=True, exist_ok=True)
    return base


def parse_screen_from_png(png: bytes, *, screen_wh: tuple[int, int] | None = None) -> ParsedScreen:
    if not png or len(png) < 32:
        return ParsedScreen(ok=False, elements={}, error="empty_screenshot")
    sw, sh = screen_wh or (0, 0)
    if not sw or not sh:
        try:
            from PIL import Image

            im = Image.open(BytesIO(png))
            sw, sh = im.size
        except Exception:
            sw, sh = 0, 0

    ocr_rows = _ocr_candidates(png)
    yolo_rows = _yolo_candidates(png)
    numbered = _merge_and_number(ocr_rows, yolo_rows)
    if not numbered:
        return ParsedScreen(
            ok=False,
            elements={},
            screen_width=sw,
            screen_height=sh,
            notes="no_elements_detected",
            error=(
                "未识别到可点击文字/控件。请安装 rapidocr-onnxruntime（pip install rapidocr-onnxruntime）"
                " 或配置 VISION_UI_YOLO_MODEL；确保目标文字在屏幕上清晰可见。"
            ),
        )

    annotated = _annotate_png(png, numbered)
    ts = int(time.time() * 1000)
    dd = _vision_ui_data_dir()
    raw_path = dd / f"screen_raw_{ts}.png"
    ann_path = dd / f"screen_parsed_{ts}.png"
    try:
        raw_path.write_bytes(png)
        ann_path.write_bytes(annotated)
    except OSError as e:
        logger.warning("[vision_ui] 落盘失败: %s", e)
        ann_path = Path("")
        raw_path = Path("")

    pub: dict[str, dict[str, Any]] = {}
    for el in numbered:
        pub[el.element_id] = {
            "text": el.text,
            "x": el.x,
            "y": el.y,
            "score": el.score,
            "source": el.source,
        }

    return ParsedScreen(
        ok=True,
        elements=pub,
        element_list=numbered,
        annotated_png=annotated,
        annotated_path=str(ann_path) if ann_path else "",
        raw_png_path=str(raw_path) if raw_path else "",
        screen_width=sw,
        screen_height=sh,
        notes=f"ocr_lines={len(ocr_rows)} yolo={len(yolo_rows)} numbered={len(numbered)}",
    )
