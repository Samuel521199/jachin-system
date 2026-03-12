#!/usr/bin/env python3
"""调试 PDF 文本提取：设置 DEBUG_PDF_EXTRACT=1 并调用 _extract_pdf_text"""
import os
import sys

# 启用 PDF 提取调试
os.environ["DEBUG_PDF_EXTRACT"] = "1"

# 添加项目根到 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
from core.pdf_extractor import extract_pdf_text

def main():
    path = r"c:\Users\Legion\.jachin\client_volumes\pool_Java_杭州 10-15K\Java _ 杭州 10-15K\【Java _ 杭州 10-15K】李海兵 附件简历-苏江涛-后端开发-26年应届生_pdf_75ad91e8.pdf"
    if len(sys.argv) > 1:
        path = sys.argv[1]
    p = Path(path)
    print(f"测试路径: {p}")
    print(f"exists: {p.exists()}")
    print("-" * 60)
    result = extract_pdf_text(p)
    print("-" * 60)
    print(f"提取结果长度: {len(result)}")
    print(f"预览: {repr(result[:200]) if result else '(空)'}")

if __name__ == "__main__":
    main()
