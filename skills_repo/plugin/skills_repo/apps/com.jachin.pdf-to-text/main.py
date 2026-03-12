"""
com.jachin.pdf-to-text - PDF 转文本
将 PDF 文件转换为纯文本，供简历解析等后续处理
"""

import logging
from pathlib import Path
from typing import Dict, Any, Optional, List

try:
    from core.skills.base_skill import BaseSkill
except ImportError:
    BaseSkill = object  # 独立开发时无 core 依赖


logger = logging.getLogger(__name__)


class PdfToTextSkill(BaseSkill):
    """PDF 转文本技能"""

    def __init__(self, manifest: Dict[str, Any]):
        if BaseSkill is not object:
            super().__init__(manifest)
        else:
            self.manifest = manifest
            self.skill_id = manifest.get("id", "unknown")

    async def execute(self, capability: str, params: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if BaseSkill is not object:
            return await super().execute(capability, params, context)
        if capability == "pdf_to_text":
            return await self.pdf_to_text(params)
        return {"success": False, "error": f"Unknown capability: {capability}"}

    async def pdf_to_text(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """将 PDF 转换为纯文本"""
        try:
            path = params.get("path", "")
            page_range = params.get("page_range", "")
            if not path:
                return {"success": False, "error": "path is required"}

            file_path = Path(path).resolve()
            if not file_path.exists():
                return {"success": False, "error": f"File not found: {path}"}
            if file_path.suffix.lower() != ".pdf":
                return {"success": False, "error": "File must be PDF"}

            text_parts: List[str] = []
            page_count = 0

            try:
                import pdfplumber
                with pdfplumber.open(file_path) as pdf:
                    page_count = len(pdf.pages)
                    pages_to_read = self._parse_page_range(page_range, page_count)
                    for i in pages_to_read:
                        if 0 <= i < page_count:
                            t = pdf.pages[i].extract_text()
                            if t:
                                text_parts.append(t)
            except ImportError:
                try:
                    from PyPDF2 import PdfReader
                    reader = PdfReader(file_path)
                    page_count = len(reader.pages)
                    pages_to_read = self._parse_page_range(page_range, page_count)
                    for i in pages_to_read:
                        if 0 <= i < page_count:
                            t = reader.pages[i].extract_text()
                            if t:
                                text_parts.append(t)
                except ImportError as e:
                    return {"success": False, "error": f"PDF library not installed: {e}"}

            full_text = "\n\n".join(text_parts) if text_parts else ""
            return {
                "success": True,
                "text": full_text,
                "page_count": page_count,
                "chars_extracted": len(full_text),
            }
        except Exception as e:
            logger.error(f"pdf_to_text failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def _parse_page_range(self, page_range: str, total: int) -> List[int]:
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
