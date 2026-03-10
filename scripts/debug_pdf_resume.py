#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
调试 PDF 简历提取：调用 core.pdf_extractor 的 extract_pdf_text_debug，
与生产环境使用完全相同的解析逻辑，逐层打印 raw/cleaned/final 便于排查。
用法: python scripts/debug_pdf_resume.py [pdf_path]
"""
import sys
from pathlib import Path

# Windows 控制台 UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_PDF = r"c:\Users\Legion\.jachin\client_volumes\pool_Java_杭州 10-15K\Java _ 杭州 10-15K\【Java _ 杭州 10-15K】李海兵 附件简历-苏江涛-后端开发-26年应届生_pdf_75ad91e8.pdf"


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PDF
    p = Path(path)
    print("=" * 70)
    print("PDF 简历提取调试（调用 core.pdf_extractor.extract_pdf_text_debug）")
    print("=" * 70)
    print(f"路径: {p}")
    print(f"存在: {p.exists()}")
    if not p.exists():
        print("[ERR] 文件不存在")
        return 1
    print()

    # 调用核心代码的调试入口（与 extract_pdf_text 完全相同的解析逻辑）
    from core.pdf_extractor import extract_pdf_text_debug
    debug = extract_pdf_text_debug(p)

    print("-" * 70)
    print("[1] raw（PyMuPDF/pypdf 原始提取，与 _extract_pdf_raw 一致）")
    print("-" * 70)
    raw = debug["raw"]
    print(f"len: {len(raw)}")
    print(">>> 内容:")
    print(raw[:2000] if raw else "(空)")
    if len(raw) > 2000:
        print(f"\n... [省略 {len(raw) - 2000} 字符]")
    print()

    print("-" * 70)
    print("[2] cleaned（_clean_pdf_extracted_text 去水印后）")
    print("-" * 70)
    cleaned = debug["cleaned"]
    print(f"len: {len(cleaned)}")
    print(">>> 内容:")
    print(cleaned[:2000] if cleaned else "(空)")
    if len(cleaned) > 2000:
        print(f"\n... [省略 {len(cleaned) - 2000} 字符]")
    print()

    print("-" * 70)
    print("[3] final（extract_pdf_text 最终返回值）")
    print("-" * 70)
    final = debug["final"]
    print(f"len: {len(final)}")
    has_chinese = any("\u4e00" <= c <= "\u9fff" for c in final)
    print(f"含中文: {has_chinese}")
    print(">>> 内容:")
    print(final if final else "(空，wasm_runner 将使用 SCAN_PLACEHOLDER)")
    print()

    if debug["ocr_attempted"]:
        print("-" * 70)
        print("[4] OCR 兜底（_try_ocr_fallback）")
        print("-" * 70)
        ocr = debug["ocr_result"]
        err = debug.get("ocr_error", "")
        if ocr:
            print(f"OCR 成功 len={len(ocr)}")
            print(ocr[:1500])
        else:
            print("OCR 未安装或失败 (需 rapidocr-onnxruntime 或 easyocr)")
            if err:
                print(f">>> 具体错误: {err}")
        print()

    print("=" * 70)
    print("调试完成")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
