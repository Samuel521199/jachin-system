#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量诊断 pending 下简历的读取与分析状态。
- 检查每个 PDF 是否能被 extract_pdf_text 正确提取
- 对比 result 目录，列出未生成分析报告的简历
- 排查路径编码、大文件、扫描件等问题

用法: python scripts/debug_pending_resumes.py [岗位目录]
示例: python scripts/debug_pending_resumes.py 前端开发
"""
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PLUGIN_DATA = ROOT / "skills_repo" / "plugin" / "data"


def main():
    job_folder = sys.argv[1] if len(sys.argv) > 1 else "前端开发"
    pending_dir = PLUGIN_DATA / job_folder / "pending"
    result_dir = PLUGIN_DATA / job_folder / "result"

    if not pending_dir.exists():
        print(f"[ERR] 目录不存在: {pending_dir}")
        return 1

    pdfs = list(pending_dir.glob("*.pdf"))
    analyses = {p.stem.replace("_analysis", ""): p for p in result_dir.glob("*_analysis.md")} if result_dir.exists() else {}

    print("=" * 70)
    print(f"简历读取诊断 - 岗位: {job_folder}")
    print("=" * 70)
    print(f"pending PDF 数: {len(pdfs)}")
    print(f"已有分析数: {len(analyses)}")
    print()

    from core.pdf_extractor import extract_pdf_text, SCAN_PLACEHOLDER

    ok_count = 0
    fail_count = 0
    scan_count = 0
    no_analysis = []

    for pdf in sorted(pdfs):
        stem = pdf.stem
        has_analysis = stem in analyses
        if not has_analysis:
            no_analysis.append(pdf.name)

        # 模拟 wasm mcp_read_file 的路径（绝对路径 + 正斜杠）
        path_for_wasm = str(pdf.resolve()).replace("\\", "/")

        try:
            content = extract_pdf_text(pdf)
        except Exception as e:
            print(f"[提取异常] {pdf.name}")
            print(f"  错误: {e}")
            fail_count += 1
            continue

        if not content:
            print(f"[空内容] {pdf.name} (extract 返回空)")
            fail_count += 1
        elif content == SCAN_PLACEHOLDER or (len(content.strip()) < 30 and not any("\u4e00" <= c <= "\u9fff" for c in content)):
            print(f"[扫描件/无效] {pdf.name} (len={len(content)})")
            scan_count += 1
        else:
            ok_count += 1
            status = "✓ 已分析" if has_analysis else "⚠ 未分析"
            print(f"[可读] {pdf.name} len={len(content)} {status}")

    print()
    print("-" * 70)
    print("汇总")
    print("-" * 70)
    print(f"  可正常提取: {ok_count}")
    print(f"  扫描件/无效: {scan_count}")
    print(f"  提取失败/空: {fail_count}")

    if no_analysis:
        print()
        print("未生成分析报告的简历:")
        for nm in no_analysis:
            print(f"  - {nm}")

    # 大文件提示
    big = [p for p in pdfs if p.stat().st_size > 5 * 1024 * 1024]
    if big:
        print()
        print("大文件 (>5MB)，可能影响分析速度或内存:")
        for p in big:
            print(f"  - {p.name} ({p.stat().st_size / 1024 / 1024:.1f} MB)")

    print()
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
