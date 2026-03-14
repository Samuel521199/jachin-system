#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试「求简历」按钮流程（atom_request_resume 独立工具）

前置条件：
  1. 用 scripts\launch_chrome_debug.ps1 启动 Chrome
  2. 登录 Boss 直聘，打开「沟通」页
  3. 运行本脚本

流程：
  - 在「全部职位」中选择含 "java" 的选项
  - 找到「付华斌 Java」对话并点击进入
  - 若对方未发简历，点击「求简历」按钮
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "2-track-a-atomic-mcp"))

from tools.atom_request_resume import atom_request_resume

if __name__ == "__main__":
    result = atom_request_resume(
        cdp_url="http://127.0.0.1:9222",
        job_keyword="java",
        candidate_name="付华斌",
        candidate_skill="Java",
    )
    print("成功:", result.get("success"))
    print("已执行求简历:", result.get("request_sent"))
    if result.get("error"):
        print("错误:", result["error"])
