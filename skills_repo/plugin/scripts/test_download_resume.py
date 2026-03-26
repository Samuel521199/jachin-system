#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试「简历预览弹窗」下载 PDF 功能（使用 Chrome）

前置条件：
  1. 用 scripts\launch_chrome_debug.ps1 启动 Chrome（推荐，确保 9222 端口生效）
  2. 在 Chrome 中登录 Boss 直聘，点进候选人对话
  3. 点击「点击预览附件简历」→ 弹出简历预览弹窗
  4. 保持弹窗打开，运行本脚本

当 Boss 采用「本地探针」(ws://127.0.0.1:xxxx failed) 导致手动也无法下载时，
脚本会自动从 viewer iframe 提取 PDF URL 并直接请求下载，无需安装 Boss 客户端。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "com.jachin.hr.recruitment"))

from tools.atom_inbox_harvester import download_resume_from_preview_page

if __name__ == "__main__":
    result = download_resume_from_preview_page(
        cdp_url="http://127.0.0.1:9222",
        download_to_pending=True,
        candidate_name="preview",
    )
    print("成功:", result.get("success"))
    print("PDF 路径:", result.get("pdf_path", ""))
    if result.get("error"):
        print("错误:", result["error"])
