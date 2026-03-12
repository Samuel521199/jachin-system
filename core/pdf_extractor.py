"""
Jachin - 统一 PDF 文本提取模块

可复用的 PDF 读取能力，供 MCP read_file、HR 透析镜、收网等场景使用。
与具体技能解耦，单一职责：从 PDF 提取纯文本（UTF-8）。
"""
from __future__ import annotations

import logging
import re
import sys
import traceback
from pathlib import Path

logger = logging.getLogger(__name__)

# 扫描件占位文案（无法提取文本时返回）
SCAN_PLACEHOLDER = "该 PDF 为扫描件或无法提取文本，请手动查看。"


def _clean_pdf_extracted_text(text: str) -> str:
    """清理 PDF 提取结果：去除招聘平台水印、重复行、多余空行。"""
    watermark_re = re.compile(r"^[a-zA-Z0-9+/=_-]{30,}(?:~~|~)?$")
    trailing_watermark_re = re.compile(r"\s+[a-zA-Z0-9+/=_-]{30,}(?:~~|~)?\s*$")
    platform_prompt_re = re.compile(r"^您可使用(BOSS直聘|智联|前程)APP.*联系Ta$")
    lines = []
    prev = None
    for line in text.splitlines():
        s = line.strip()
        s = trailing_watermark_re.sub("", s).strip()
        if watermark_re.match(s) or platform_prompt_re.match(s):
            continue
        if not s:
            if prev is not None and prev != "":
                lines.append("")
            prev = ""
            continue
        for u in ("\uf075", "\uf0b7", "\uf0a7", "\uf0d8", "\uf0b2"):
            s = s.replace(u, "•")
        lines.append(s)
        prev = s
    out = "\n".join(lines).strip()
    return re.sub(r"\n{3,}", "\n\n", out)


def _extract_with_pypdf(path_str: str) -> str:
    """使用 pypdf (pypdf 4.x) 提取文本。"""
    from pypdf import PdfReader

    reader = PdfReader(path_str)
    parts = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(parts).strip()


def _extract_with_pypdf2(path_str: str) -> str:
    """使用 PyPDF2 提取文本（兼容旧环境 pypdf2 包）。"""
    from PyPDF2 import PdfReader

    reader = PdfReader(path_str)
    parts = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(parts).strip()


def _extract_pdf_raw(p: Path) -> str:
    """核心：PyMuPDF → pypdf 兜底，与 extract_pdf_text 完全一致。"""
    path_str = str(p)
    raw = ""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(path_str)
        parts = [page.get_text() or "" for page in doc]
        doc.close()
        raw = "\n".join(parts).strip()
    except ImportError:
        pass
    except Exception as e:
        logger.warning("[PDF] PyMuPDF 提取失败 path=%s: %s", path_str, e)
    if not raw:
        for lib_name, get_parts in [
            ("pypdf", lambda: _extract_with_pypdf(path_str)),
            ("PyPDF2", lambda: _extract_with_pypdf2(path_str)),
        ]:
            try:
                raw = get_parts()
                if raw:
                    break
            except ImportError:
                continue
            except Exception as e:
                logger.warning("[PDF] %s 提取失败 path=%s: %s", lib_name, path_str, e)
        if not raw:
            return ""
    return raw


def extract_pdf_text(path: Path | str) -> str:
    """
    从 PDF 提取纯文本（UTF-8）。
    优先 PyMuPDF，失败回退 pypdf。
    供 MCP read_file、HR 透析镜、core_fs_read 等复用。

    Args:
        path: 文件路径（Path 或 str）

    Returns:
        提取的纯文本，失败或扫描件返回空字符串或占位文案
    """
    p = Path(path) if isinstance(path, str) else path
    path_str = str(p)
    _debug = __import__("os").environ.get("DEBUG_PDF_EXTRACT", "").lower() in ("1", "true", "yes")

    def _log(msg: str, *a: object) -> None:
        if _debug:
            out = msg % a if a else msg
            logger.info("[PDF DEBUG] %s", out)
            print(f"[PDF DEBUG] {out}", file=sys.stderr, flush=True)

    if not p.exists():
        logger.warning("[PDF] 文件不存在: %s", path_str)
        return ""

    raw = _extract_pdf_raw(p)
    _log("raw_len=%d preview=%s", len(raw), repr(raw[:100]) if raw else "(empty)")
    if not raw:
        return ""

    cleaned = _clean_pdf_extracted_text(raw)
    if not cleaned.strip() and raw.strip():
        _log("清理后为空但原始有内容（可能为水印/乱码），视为扫描件 raw_len=%d", len(raw))
    s = cleaned.strip()
    if len(s) < 50 and not any("\u4e00" <= c <= "\u9fff" for c in s):
        _log("清理后无有效中文内容（len=%d），尝试 OCR 兜底", len(s))
        ocr_text = _try_ocr_fallback(p)
        if ocr_text.strip():
            return ocr_text
        return ""
    return cleaned


def extract_pdf_text_debug(path: Path | str) -> dict:
    """
    调试入口：与 extract_pdf_text 使用完全相同的核心逻辑，返回各阶段结果供排查。
    Returns:
        {"raw": str, "cleaned": str, "final": str, "ocr_attempted": bool, "ocr_result": str, "ocr_error": str}
    """
    p = Path(path) if isinstance(path, str) else path
    result = {"raw": "", "cleaned": "", "final": "", "ocr_attempted": False, "ocr_result": "", "ocr_error": ""}
    if not p.exists():
        return result
    raw = _extract_pdf_raw(p)
    result["raw"] = raw
    cleaned = _clean_pdf_extracted_text(raw)
    result["cleaned"] = cleaned
    s = cleaned.strip()
    if len(s) < 50 and not any("\u4e00" <= c <= "\u9fff" for c in s):
        result["ocr_attempted"] = True
        ocr_text, ocr_err = _try_ocr_fallback_impl(p)
        result["ocr_result"] = ocr_text
        result["ocr_error"] = ocr_err
        result["final"] = ocr_text if ocr_text.strip() else ""
    else:
        result["final"] = cleaned
    return result


def _try_ocr_fallback(path: Path) -> str:
    """当文本提取无效时，尝试 OCR（纯 Python：PyMuPDF + RapidOCR，无需 poppler/tesseract）。"""
    text, _ = _try_ocr_fallback_impl(path)
    return text


def _pdf_pages_to_images(path: Path) -> list:
    """使用 PyMuPDF 将 PDF 页面渲染为 PIL Image 列表（2x 分辨率保证 OCR 精度）。"""
    import fitz  # PyMuPDF
    from PIL import Image
    images = []
    doc = fitz.open(str(path))
    try:
        for page in doc:
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            images.append(img)
    finally:
        doc.close()
    return images


def _try_ocr_fallback_impl(path: Path) -> tuple[str, str]:
    """OCR 实现：PyMuPDF 转图，优先 RapidOCR，失败时回退 EasyOCR（均 pip 即可，无需系统安装）。"""
    images = _pdf_pages_to_images(path)
    if not images:
        return "", "PDF 无有效页面"

    # 优先 RapidOCR（轻量，onnxruntime）
    text, err1 = _ocr_with_rapidocr(images, path)
    if text:
        return text, ""

    # RapidOCR 失败时回退 EasyOCR（PyTorch，pip 安装即可）
    text, err2 = _ocr_with_easyocr(images, path)
    if text:
        return text, ""
    errs = [e for e in (err1, err2) if e]
    return "", "; ".join(errs) if errs else "OCR 未就绪"


def _ocr_with_rapidocr(images: list, path: Path) -> tuple[str, str]:
    """使用 RapidOCR 识别，返回 (文本, 错误)。"""
    try:
        from rapidocr_onnxruntime import RapidOCR
        import numpy as np
    except (ImportError, OSError) as e:
        return "", f"rapidocr 不可用: {e}"
    try:
        ocr = RapidOCR()
        full_text = []
        for img in images:
            img_np = np.array(img)
            result, _ = ocr(img_np)
            if result:
                if isinstance(result, (list, tuple)):
                    page_text = "\n".join(
                        str(item[1]) for item in result
                        if item and len(item) >= 2 and item[1]
                    )
                elif hasattr(result, "txts"):
                    page_text = "\n".join(str(t) for t in result.txts if t)
                else:
                    page_text = ""
                if page_text.strip():
                    full_text.append(page_text.strip())
        out = "\n\n".join(full_text).strip()
        if out and any("\u4e00" <= c <= "\u9fff" for c in out):
            out = _clean_pdf_extracted_text(out)
            logger.info("[PDF] OCR 兜底成功 (RapidOCR) path=%s len=%d", path, len(out))
            return out, ""
    except Exception as e:
        return "", str(e)
    return "", ""


def _ocr_with_easyocr(images: list, path: Path) -> tuple[str, str]:
    """使用 EasyOCR 识别（pip install easyocr 即可，PyTorch 自包含），返回 (文本, 错误)。"""
    try:
        import easyocr
        import numpy as np
    except (ImportError, OSError) as e:
        return "", f"easyocr 不可用: {e}"
    try:
        reader = easyocr.Reader(["ch_sim", "en"], gpu=False)
        full_text = []
        for img in images:
            img_np = np.array(img)
            result = reader.readtext(img_np)
            if result:
                page_text = "\n".join(str(r[1]) for r in result if r and len(r) >= 2 and r[1])
                if page_text.strip():
                    full_text.append(page_text.strip())
        out = "\n\n".join(full_text).strip()
        if out and any("\u4e00" <= c <= "\u9fff" for c in out):
            out = _clean_pdf_extracted_text(out)
            logger.info("[PDF] OCR 兜底成功 (EasyOCR) path=%s len=%d", path, len(out))
            return out, ""
    except Exception as e:
        return "", str(e)
    return "", ""
