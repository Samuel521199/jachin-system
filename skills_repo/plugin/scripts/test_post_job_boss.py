#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试 Boss 直聘自动发布职位。

前置条件：
  1. 用 scripts\\launch_chrome_debug.ps1 启动 Chrome
  2. 在 Chrome 中登录 Boss 直聘
  3. 编辑 data\\jd_to_publish.json 填写 JD 内容
  4. 运行本脚本
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "2-track-a-atomic-mcp"))

from tools.atom_post_job_boss import atom_post_job_boss, load_jd_config

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="", help="JD 配置文件路径，默认 data/jd_to_publish.json")
    args = p.parse_args()

    jd = load_jd_config(args.config)
    if not jd:
        print("错误: 未加载到 JD 配置，请检查 data/jd_to_publish.json")
        sys.exit(1)
    print("已加载 JD:", jd.get("job_title", ""), jd.get("recruitment_type", ""))

    result = atom_post_job_boss(
        cdp_url="http://127.0.0.1:9222",
        jd_config_path=args.config or "",
    )

    print("成功:", result.get("success"))
    print("已发布:", result.get("posted"))
    if result.get("error"):
        print("错误:", result["error"])
