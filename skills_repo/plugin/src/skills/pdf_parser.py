"""PDF 转文本"""
import logging
from pathlib import Path
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


def _parse_page_range(page_range: str, total: int) -> List[int]:
    if not page_range or not total:
        return list(range(total))
    result: List[int] = []
    for part in page_range.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                start, end = int(a.strip()) - 1, int(b.strip()) - 1
                result.extend(range(max(0, start), min(end + 1, total)))
            except ValueError:
                pass
        else:
            try:
                n = int(part) - 1
                if 0 <= n < total:
                    result.append(n)
            except ValueError:
                pass
    return result if result else list(range(total))


async def pdf_to_text(path: str, page_range: str = "") -> Dict[str, Any]:
    """将 PDF 转为纯文本"""
    try:
        file_path = Path(path).resolve()
        if not file_path.exists():
            return {"success": False, "error": f"文件不存在: {path}", "text": ""}
        if file_path.suffix.lower() != ".pdf":
            return {"success": False, "error": "仅支持 PDF 文件", "text": ""}

        text_parts: List[str] = []
        page_count = 0

        try:
            import pdfplumber
            with pdfplumber.open(file_path) as pdf:
                page_count = len(pdf.pages)
                for i in _parse_page_range(page_range, page_count):
                    if 0 <= i < page_count:
                        t = pdf.pages[i].extract_text()
                        if t:
                            text_parts.append(t)
        except ImportError:
            try:
                from PyPDF2 import PdfReader
                reader = PdfReader(file_path)
                page_count = len(reader.pages)
                for i in _parse_page_range(page_range, page_count):
                    if 0 <= i < page_count:
                        t = reader.pages[i].extract_text()
                        if t:
                            text_parts.append(t)
            except ImportError as e:
                return {"success": False, "error": f"PDF 库未安装: {e}", "text": ""}

        full_text = "\n\n".join(text_parts) if text_parts else ""
        return {"success": True, "text": full_text, "page_count": page_count}
    except Exception as e:
        logger.error(f"pdf_to_text failed: {e}", exc_info=True)
        return {"success": False, "error": str(e), "text": ""}
