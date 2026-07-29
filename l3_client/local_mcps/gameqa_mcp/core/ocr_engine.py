"""
GameQA 截图 OCR：与 ``core/pdf_extractor`` 同策略，优先 RapidONNX，失败则 EasyOCR。
在 ``get_semantic_state`` 中与 YOLO 并行作用于**同一帧** PNG，不额外截屏。

环境变量::

    GAMEQA_OCR_ENABLED   默认 1；0/false/off 关闭 OCR（仅 YOLO）
    GAMEQA_OCR_MAX_CHARS  写入 state 的最大字符数，默认 6000；超出截断并标注 truncated
"""
from __future__ import annotations

import logging
import os
import threading
from io import BytesIO
from typing import Any

logger = logging.getLogger("gameqa.ocr_engine")

_rapid: Any = None
_rapid_lock = threading.Lock()
_easyocr_reader: Any = None
_easyocr_lock = threading.Lock()


def gameqa_ocr_enabled() -> bool:
    v = (os.environ.get("GAMEQA_OCR_ENABLED") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _ocr_max_chars() -> int:
    try:
        n = int((os.environ.get("GAMEQA_OCR_MAX_CHARS") or "6000").strip())
    except ValueError:
        n = 6000
    return max(256, min(n, 100_000))


def _get_rapid() -> Any:
    global _rapid
    with _rapid_lock:
        if _rapid is None:
            from rapidocr_onnxruntime import RapidOCR

            _rapid = RapidOCR()
        return _rapid


def _get_easyocr_reader() -> Any:
    global _easyocr_reader
    with _easyocr_lock:
        if _easyocr_reader is None:
            import easyocr

            _easyocr_reader = easyocr.Reader(["ch_sim", "en"], gpu=False)
        return _easyocr_reader


def _parse_rapid_result(result: Any) -> str:
    """与 pdf_extractor 一致地解析 RapidOCR 返回。"""
    if not result:
        return ""
    if isinstance(result, (list, tuple)):
        lines = []
        for item in result:
            if item and len(item) >= 2 and item[1]:
                lines.append(str(item[1]).strip())
        return "\n".join(x for x in lines if x)
    if hasattr(result, "txts"):
        return "\n".join(str(t) for t in result.txts if t)
    return ""


def _polygon_geometry(
    box: Any,
) -> tuple[float, float, float, float, float, float] | None:
    """Return center and bounds for polygon or flat xyxy OCR boxes."""
    try:
        import numpy as np

        arr = np.asarray(box, dtype=float)
        if arr.size < 4:
            return None
        if arr.ndim == 2 and arr.shape[0] >= 2 and arr.shape[1] >= 2:
            xs = arr[:, 0]
            ys = arr[:, 1]
            x1, x2 = float(xs.min()), float(xs.max())
            y1, y2 = float(ys.min()), float(ys.max())
            return float(xs.mean()), float(ys.mean()), x1, y1, x2, y2
        if arr.ndim == 1 and arr.shape[0] >= 4:
            x1, y1, x2, y2 = float(arr[0]), float(arr[1]), float(arr[2]), float(arr[3])
            return (x1 + x2) / 2.0, (y1 + y2) / 2.0, x1, y1, x2, y2
    except Exception:
        return None
    return None


def _rapid_boxes_list(
    result: Any,
) -> list[tuple[str, float, float, float, float, float, float, float]]:
    """Return text, center, score and bounds from RapidOCR raw results."""
    rows: list[
        tuple[str, float, float, float, float, float, float, float]
    ] = []
    if not result:
        return rows

    def _push(box: Any, text: str, score: float = 1.0) -> None:
        t = str(text or "").strip()
        if not t:
            return
        geometry = _polygon_geometry(box)
        if geometry is None:
            return
        cx, cy, x1, y1, x2, y2 = geometry
        rows.append((t, cx, cy, float(score), x1, y1, x2, y2))

    if isinstance(result, (list, tuple)):
        for item in result:
            if not item:
                continue
            if len(item) >= 3:
                box, text, sc = item[0], item[1], item[2]
                try:
                    scf = float(sc) if sc is not None else 1.0
                except (TypeError, ValueError):
                    scf = 1.0
                _push(box, str(text), scf)
            elif len(item) >= 2:
                _push(item[0], str(item[1]), 1.0)
        return rows

    if hasattr(result, "txts") and hasattr(result, "boxes"):
        txts = list(getattr(result, "txts", None) or [])
        boxes = list(getattr(result, "boxes") or [])
        scores = list(getattr(result, "scores") or [])
        for i, t in enumerate(txts):
            if not t:
                continue
            box = boxes[i] if i < len(boxes) else None
            sc = float(scores[i]) if i < len(scores) else 1.0
            _push(box, str(t), sc)
    return rows


def _easy_boxes_list(
    img_np: Any,
) -> list[tuple[str, float, float, float, float, float, float, float]]:
    rows: list[
        tuple[str, float, float, float, float, float, float, float]
    ] = []
    try:
        reader = _get_easyocr_reader()
        raw = reader.readtext(img_np)
    except Exception:
        return rows
    for r in raw or []:
        if not r or len(r) < 2:
            continue
        box, text = r[0], r[1]
        sc = 1.0
        if len(r) >= 3:
            try:
                sc = float(r[2])
            except (TypeError, ValueError):
                sc = 1.0
        t = str(text or "").strip()
        if not t:
            continue
        geometry = _polygon_geometry(box)
        if geometry is None:
            continue
        cx, cy, x1, y1, x2, y2 = geometry
        rows.append((t, cx, cy, sc, x1, y1, x2, y2))
    return rows


def ocr_line_boxes_from_png(png: bytes) -> tuple[list[dict[str, Any]], str]:
    """
    逐行 OCR（带框中心点），供 YOLO 无框时按关键词落锚点。

    返回 (lines, notes)；lines 每项含 text, cx, cy, score, source(rapid|easy)。
    """
    if not gameqa_ocr_enabled():
        return [], "ocr_disabled"
    if not png or len(png) < 32:
        return [], "empty_png"
    try:
        from PIL import Image
        import numpy as np
    except ImportError as e:
        return [], f"pillow_numpy:{e}"

    try:
        img = Image.open(BytesIO(png)).convert("RGB")
        img_np = np.array(img)
    except Exception as e:
        return [], f"decode_png:{e}"

    try:
        ocr = _get_rapid()
        result, _elapsed = ocr(img_np)
        rapid_rows = _rapid_boxes_list(result)
    except Exception as e:
        rapid_rows = []
        err_rapid = repr(e)
        return _rows_to_line_dicts(_easy_boxes_list(img_np), "easy", f"rapid_err:{err_rapid}")

    if rapid_rows:
        return _rows_to_line_dicts(rapid_rows, "rapid", "rapid_boxes")

    rows = _easy_boxes_list(img_np)
    return _rows_to_line_dicts(rows, "easy", "easy_boxes_after_rapid_empty")


def _rows_to_line_dicts(
    rows: list[
        tuple[str, float, float, float, float, float, float, float]
    ],
    source: str,
    notes: str,
) -> tuple[list[dict[str, Any]], str]:
    out: list[dict[str, Any]] = [
        {
            "text": text,
            "cx": cx,
            "cy": cy,
            "score": score,
            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2,
            "source": source,
        }
        for text, cx, cy, score, x1, y1, x2, y2 in rows
    ]
    if not out:
        return [], f"no_line_boxes:{notes}"
    return out, notes


def _ocr_numpy_rapid(img_np: Any) -> tuple[str, str]:
    try:
        ocr = _get_rapid()
        result, _ = ocr(img_np)
        text = _parse_rapid_result(result)
        if text.strip():
            return text.strip(), ""
    except Exception as e:
        return "", str(e)
    return "", ""


def _ocr_numpy_easy(img_np: Any) -> tuple[str, str]:
    try:
        reader = _get_easyocr_reader()
        raw = reader.readtext(img_np)
        if not raw:
            return "", ""
        lines = [str(r[1]).strip() for r in raw if r and len(r) >= 2 and r[1]]
        text = "\n".join(x for x in lines if x)
        return text.strip(), ""
    except Exception as e:
        return "", str(e)


def ocr_png_bytes(png: bytes) -> tuple[str, str, str]:
    """
    对整页截图跑 OCR。

    返回 (text, notes, backend)：
    - text：识别文本（调用方再截断）
    - notes：人类可读摘要
    - backend：rapidocr | easyocr | none
    """
    if not png or len(png) < 32:
        return "", "empty_png", "none"
    try:
        from PIL import Image
        import numpy as np
    except ImportError as e:
        return "", f"pillow_or_numpy:{e}", "none"

    try:
        img = Image.open(BytesIO(png)).convert("RGB")
        img_np = np.array(img)
    except Exception as e:
        return "", f"decode_png:{e}", "none"

    t1, e1 = _ocr_numpy_rapid(img_np)
    if t1:
        logger.debug("[gameqa][ocr] rapidocr chars=%d", len(t1))
        return t1, f"rapidocr chars={len(t1)}", "rapidocr"

    t2, e2 = _ocr_numpy_easy(img_np)
    if t2:
        logger.debug("[gameqa][ocr] easyocr chars=%d", len(t2))
        return t2, f"easyocr chars={len(t2)}", "easyocr"

    err = "; ".join(x for x in (e1, e2) if x) or "no_text"
    logger.warning("[gameqa][ocr] failed: %s", err)
    return "", err, "none"


def ocr_png_bytes_for_state(png: bytes) -> dict[str, Any]:
    """供 ``get_semantic_state`` 写入 state 的字典（统一截断与字段名）。"""
    if not gameqa_ocr_enabled():
        return {
            "ocr_enabled": False,
            "ocr_text": "",
            "ocr_notes": "disabled:GAMEQA_OCR_ENABLED=0",
            "ocr_backend": "none",
        }
    text, notes, backend = ocr_png_bytes(png)
    cap = _ocr_max_chars()
    truncated = False
    if text and len(text) > cap:
        text = text[:cap] + "\n...[truncated by GAMEQA_OCR_MAX_CHARS]"
        truncated = True
    out_notes = notes
    if truncated:
        out_notes = f"{notes}; truncated to {cap} chars"
    return {
        "ocr_enabled": True,
        "ocr_text": text,
        "ocr_notes": out_notes if text else f"no_text:{notes}",
        "ocr_backend": backend,
    }
