#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BI 分析 — 终端入口

用法:
  交互模式（输入「BI分析」触发）:
    python scripts/run_bi_analysis.py

  直接执行（免输入）:
    python scripts/run_bi_analysis.py --run
    python scripts/run_bi_analysis.py -y
"""
from __future__ import annotations

import sys
from pathlib import Path

# 确保项目根在 sys.path
root = Path(__file__).resolve().parents[1]
try:
    from dotenv import load_dotenv
    load_dotenv(root / ".env", encoding="utf-8")
except ImportError:
    pass
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

# Windows 控制台 UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _run_bi_flow() -> int:
    """执行 BI 完整流程"""
    from l3_node.skills.bi.bi_daily_report.main_skill import run_bi_daily_report

    print("\n[BI分析] 正在执行 BI 每日战报流程...")
    result = run_bi_daily_report()
    if result.get("success"):
        print("[BI分析] ✅ 完成")
        print(f"  - 输出文件: {len(result.get('output_paths', []))} 个")
        print(f"  - Lark 同步: {result.get('lark_sync_ok', 0)} 个表")
        if result.get("strategic_report_sent"):
            print("  - 战略分析已推送到 Lark")
        if result.get("email_ok"):
            print("  - 邮件已发送")
        if result.get("dashboard_automation"):
            da = result["dashboard_automation"]
            print(f"  - 仪表盘自动化: 成功 {da.get('done', 0)} / 失败 {da.get('failed', 0)}")
        if result.get("lark_sync_errors"):
            print(f"  - 同步错误: {result['lark_sync_errors']}")
        if result.get("lark_sync_skipped"):
            skipped = result["lark_sync_skipped"]
            print(f"  - 跳过表: {len(skipped)} 个（未配置 table_id）— {', '.join(n for n, _ in skipped)}")
    else:
        print(f"[BI分析] ❌ 失败: {result.get('error', '未知错误')}")
        return 1
    return 0


def main() -> int:
    if "--run" in sys.argv or "-y" in sys.argv or "--yes" in sys.argv:
        return _run_bi_flow()

    # 交互模式
    print("=" * 50)
    print("BI 分析 — 输入「BI分析」开始执行，输入「quit」或「q」退出")
    print("=" * 50)
    try:
        while True:
            try:
                line = input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见")
                break
            if not line:
                continue
            if line.lower() in ("quit", "q", "exit"):
                print("再见")
                break
            if "BI分析" in line or "bi分析" in line or line.strip().lower() == "bi分析":
                _run_bi_flow()
                print()
                continue
            print("提示: 输入「BI分析」开始分析")
    except Exception as e:
        print(f"异常: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
