#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试完整流程：在「全部职位」中选择职位 → 遍历左侧所有候选人会话 →
若有「点击预览附件简历」则下载 PDF，否则跳过继续下一个。

前置条件：
  1. 用 scripts\launch_chrome_debug.ps1 启动 Chrome
  2. 在 Chrome 中登录 Boss 直聘，打开「沟通」页
  3. 运行: python scripts\test_harvest_full_flow.py --job "资深Golang语言开发_杭州 25-40K"
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "2-track-a-atomic-mcp"))

from tools.boss_harvest_orchestrator import harvest_resume_full_flow

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--job", default="资深Golang语言开发_杭州 25-40K", help="职位文本，用于在「全部职位」中匹配（如 Golang 或完整职位名）")
    p.add_argument("--max", type=int, default=50, help="最多遍历对话数")
    p.add_argument("--debug", action="store_true", help="调试模式")
    args = p.parse_args()

    result = harvest_resume_full_flow(
        cdp_url="http://127.0.0.1:9222",
        job_text=args.job,
        download_to_pending=True,
        max_items=args.max,
        debug=args.debug,
    )
    print("成功:", result.get("success"))
    print("下载数量:", result.get("downloaded", 0))
    print("PDF 路径列表:", result.get("pdf_paths", []))
    if result.get("processed"):
        print("\n处理明细:")
        for p in result["processed"]:
            action = p.get("action", "")
            extra = ""
            if action == "downloaded" and p.get("path"):
                extra = f" -> {p['path']}"
            elif action == "download_failed" and p.get("error"):
                extra = f" ({p['error']})"
            elif action == "error" and p.get("error"):
                extra = f" ({p['error']})"
            print(f"  - {p.get('label')}: {action}{extra}")
    if result.get("error"):
        print("错误:", result["error"])
