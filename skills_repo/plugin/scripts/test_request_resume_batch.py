#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试「批量求简历」：遍历沟通页左侧对话列表，对未发简历的对话点击求简历。

前置条件：
  1. 用 scripts\\launch_chrome_debug.ps1 启动 Chrome
  2. 登录 Boss 直聘，打开「沟通」页
  3. 确保职位下拉中有「资深Golang语言开发_杭州 25-40K」选项
  4. 运行本脚本
"""
import argparse
import sys
from pathlib import Path

# Windows 控制台 UTF-8 输出
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "2-track-a-atomic-mcp"))

from tools.atom_request_resume import atom_request_resume_batch

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--job", default="资深Golang语言开发_杭州 25-40K", help="职位文本，用于下拉匹配")
    p.add_argument("--max", type=int, default=50, help="最多处理几个对话")
    args = p.parse_args()

    result = atom_request_resume_batch(
        cdp_url="http://127.0.0.1:9222",
        job_text=args.job,
        max_items=args.max,
    )

    print("成功:", result.get("success"))
    print("求简历数:", result.get("requested_count", 0))
    print("已发简历跳过:", result.get("skipped_has_resume", 0))
    if result.get("processed"):
        print("\n处理明细:")
        for p in result["processed"]:
            print(f"  - {p.get('label')}: {p.get('action')}")
    if result.get("error"):
        print("错误:", result["error"])
