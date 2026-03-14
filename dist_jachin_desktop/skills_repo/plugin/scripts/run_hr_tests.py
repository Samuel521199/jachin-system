#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
在 skills_repo/plugin 下跑通 HR 两个功能测试。

前置条件：
  1. 用 scripts\\launch_chrome_debug.ps1 启动 Chrome
  2. 在 Chrome 中登录 Boss 直聘
  3. 编辑 data\\jd_to_publish.json 填写 JD 内容

用法：
  python scripts/run_hr_tests.py                    # 运行两个测试
  python scripts/run_hr_tests.py --post-only        # 仅发布 JD
  python scripts/run_hr_tests.py --greet-only       # 仅打招呼
  python scripts/run_hr_tests.py --greet-rule-only  # 打招呼时跳过 brain_filter，仅用规则
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "data" / "jd_to_publish.json"


def main():
    p = argparse.ArgumentParser(description="跑通发布 JD 与推荐牛人打招呼测试")
    p.add_argument("--config", default=str(CONFIG), help="JD 配置文件路径")
    p.add_argument("--post-only", action="store_true", help="仅运行发布 JD 测试")
    p.add_argument("--greet-only", action="store_true", help="仅运行打招呼测试")
    p.add_argument("--greet-rule-only", action="store_true", help="打招呼时 GREET_USE_RULE_ONLY=1，跳过 brain_filter")
    args = p.parse_args()

    if not CONFIG.exists():
        print(f"错误: 配置文件不存在 {CONFIG}")
        print("请复制 data/jd_to_publish.example.json 为 data/jd_to_publish.json 并编辑")
        sys.exit(1)

    run_post = not args.greet_only
    run_greet = not args.post_only

    env = os.environ.copy()
    if args.greet_rule_only:
        env["GREET_USE_RULE_ONLY"] = "1"
        print("已设置 GREET_USE_RULE_ONLY=1，打招呼将跳过 brain_filter API")

    print("=" * 60)
    print("HR 功能测试")
    print("=" * 60)
    print(f"配置: {args.config}")
    print("前置: Chrome 调试模式(9222) + 登录 Boss")
    print()

    failed = []

    if run_post:
        print(">>> 1. 发布 JD 测试")
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "test_post_job_boss.py"), "--config", args.config],
            cwd=str(ROOT),
            env=env,
        )
        if r.returncode != 0:
            failed.append("发布 JD")
        print()

    if run_greet:
        print(">>> 2. 推荐牛人自动打招呼测试")
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "test_greet_recommend_boss.py"), "--config", args.config],
            cwd=str(ROOT),
            env=env,
        )
        if r.returncode != 0:
            failed.append("打招呼")
        print()

    print("=" * 60)
    if failed:
        print(f"失败: {', '.join(failed)}")
        sys.exit(1)
    print("全部通过")
    sys.exit(0)


if __name__ == "__main__":
    main()
