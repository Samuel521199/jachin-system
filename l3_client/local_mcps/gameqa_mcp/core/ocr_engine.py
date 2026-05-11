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
