#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
以 MCP 形式跑通 HR 两个功能：发布 JD、推荐牛人打招呼。

前置条件：
  1. 用 scripts\\launch_chrome_debug.ps1 启动 Chrome
  2. 在 Chrome 中登录 Boss 直聘
  3. 编辑 data\\jd_to_publish.json 填写 JD 内容

用法：
  python scripts/run_mcp_hr_tests.py                    # 运行两个 MCP 测试
  python scripts/run_mcp_hr_tests.py --post-only        # 仅 MCP 发布 JD
  python scripts/run_mcp_hr_tests.py --greet-only        # 仅 MCP 打招呼
  python scripts/run_mcp_hr_tests.py --greet-rule-only   # 打招呼时 GREET_USE_RULE_ONLY=1
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "data" / "jd_to_publish.json"


def main():
    p = argparse.ArgumentParser(description="以 MCP 形式跑通发布 JD 与打招呼")
    p.add_argument("--config", default=str(CONFIG), help="JD 配置文件路径")
    p.add_argument("--post-only", action="store_true", help="仅运行 MCP 发布 JD 测试")
    p.add_argument("--greet-only", action="store_true", help="仅运行 MCP 打招呼测试")
    p.add_argument("--greet-rule-only", action="store_true", help="打招呼时 GREET_USE_RULE_ONLY=1")
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
        print()

    print("=" * 60)
    print("HR 功能 MCP 测试")
    print("=" * 60)
    print(f"配置: {args.config}")
    print("前置: Chrome 调试模式(9222) + 登录 Boss")
    print("模式: MCP 协议（非 --direct 直接调用）")
    print()

    failed = []

    if run_post:
        print(">>> 1. MCP 发布 JD 测试 (atom_post_job_boss)")
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "test_mcp_post_job_boss.py"), "--config", args.config],
            cwd=str(ROOT),
            env=env,
        )
        if r.returncode != 0:
            failed.append("MCP 发布 JD")
        print()

    if run_greet:
        print(">>> 2. MCP 推荐牛人打招呼测试 (atom_greet_recommend_boss)")
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "test_mcp_greet_recommend_boss.py"), "--config", args.config],
            cwd=str(ROOT),
            env=env,
        )
        if r.returncode != 0:
            failed.append("MCP 打招呼")
        print()

    print("=" * 60)
    if failed:
        print(f"失败: {', '.join(failed)}")
        sys.exit(1)
    print("全部通过")
    sys.exit(0)


if __name__ == "__main__":
    main()
