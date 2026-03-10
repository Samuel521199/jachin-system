#!/usr/bin/env python3
"""从 PDF 简历提取纯文本，测试 HR 透析镜 PDF 能力。"""
import re
import sys
from pathlib import Path

# 项目根
PROJ = Path(__file__).resolve().parent.parent

# 招聘平台水印/ID 模式（base64 风格，30+ 字符）
_WATERMARK_RE = re.compile(r"^[a-zA-Z0-9+/=_-]{30,}(?:~~)?$")
_TRAILING_WATERMARK_RE = re.compile(r"\s+[a-zA-Z0-9+/=_-]{30,}(?:~~)?\s*$")


def _clean_extracted_text(text: str) -> str:
    """清理提取结果：去除水印、重复行、多余空行，规范化符号。"""
    lines = []
    prev = None
    for line in text.splitlines():
        s = line.strip()
        s = _TRAILING_WATERMARK_RE.sub("", s).strip()
        # 跳过水印/ID 行
        if _WATERMARK_RE.match(s):
            continue
        # 跳过空行（合并连续空行为一个）
        if not s:
            if prev is not None and prev != "":
                lines.append("")
            prev = ""
            continue
        # 规范化私有区 bullet 为 ASCII（招聘平台 PDF 常用 U+F075/F0B7/F0A7/F0D8/F0B2 等）
        for u in ("\uf075", "\uf0b7", "\uf0a7", "\uf0d8", "\uf0b2"):
            s = s.replace(u, "•")
        lines.append(s)
        prev = s
    # 去除首尾空行，合并连续空行为单个
    out = "\n".join(lines).strip()
    return re.sub(r"\n{3,}", "\n\n", out)


def extract_pdf_text(path: Path) -> str:
    """使用 pypdf 提取 PDF 纯文本。"""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        parts = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                parts.append(t)
        raw = "\n".join(parts)
        return _clean_extracted_text(raw)
    except Exception as e:
        return f"[提取失败] {e}"


def main() -> None:
    pdf_path = PROJ / "data" / "hr_resumes" / "【资深Golang语言开发_杭州 25-40K】赵晨凯 5年.pdf"
    if len(sys.argv) > 1:
        pdf_path = Path(sys.argv[1]).resolve()
    if not pdf_path.exists():
        print(f"文件不存在: {pdf_path}")
        sys.exit(1)
    text = extract_pdf_text(pdf_path)
    out_path = pdf_path.with_suffix(".txt")
    out_path.write_text(text, encoding="utf-8")
    print(f"提取完成: {len(text)} 字符 -> {out_path}")


if __name__ == "__main__":
    main()
