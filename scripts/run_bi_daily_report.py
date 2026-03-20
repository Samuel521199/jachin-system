#!/usr/bin/env python3
"""
BI 每日战报 — 一键执行入口

用法:
  在项目根目录: python scripts/run_bi_daily_report.py
  或在 scripts 目录: python run_bi_daily_report.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# 确保项目根在 sys.path
root = Path(__file__).resolve().parents[1]
# 加载 .env（DASHSCOPE_API_KEY、BI_* 等）
try:
    from dotenv import load_dotenv
    load_dotenv(root / ".env", encoding="utf-8")
except ImportError:
    pass
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from l3_node.skills.bi.bi_daily_report.main_skill import run_bi_daily_report


def main() -> None:
    print("执行 BI 每日战报...")
    result = run_bi_daily_report()
    print(result)


if __name__ == "__main__":
    main()
