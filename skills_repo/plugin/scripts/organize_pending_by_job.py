#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将 data/pending/ 根目录下的 PDF 按职位整理到子文件夹。
对已有【职位】在文件名中的 PDF，移动到 data/pending/<职位>/
其他移动到 data/pending/未分类/
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "2-track-a-atomic-mcp"))

from tools.local_archiver import PENDING_DIR, _extract_job_folder, _sanitize_filename

DEFAULT_JOB = "未分类"


def organize_pending() -> dict:
    """整理 data/pending 根目录下的 PDF 到职位子文件夹"""
    if not PENDING_DIR.exists():
        return {"success": True, "moved": 0, "skipped": 0, "error": ""}

    moved = 0
    skipped = 0
    for pdf in PENDING_DIR.glob("*.pdf"):
        if not pdf.is_file():
            continue
        file_label = pdf.stem
        job_folder = _extract_job_folder(file_label, "")
        target_dir = PENDING_DIR / job_folder
        if target_dir == pdf.parent:
            skipped += 1
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        dest = target_dir / pdf.name
        if dest.exists() and dest.resolve() != pdf.resolve():
            skipped += 1
            continue
        try:
            pdf.rename(dest)
            moved += 1
        except Exception as e:
            pass
    return {"success": True, "moved": moved, "skipped": skipped, "error": ""}


if __name__ == "__main__":
    r = organize_pending()
    print("整理完成:", r)
    print("移动:", r.get("moved", 0), "跳过:", r.get("skipped", 0))
