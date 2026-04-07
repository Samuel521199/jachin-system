#!/usr/bin/env python3
"""
查询 BI 核心指标（插件化架构）

Usage: python scripts/query_bi_metrics.py [--date YYYY-MM-DD] [--no-compare] [--compare-period day|week|month] [--format console|markdown]
  --date: 指定日期，默认取最新
  --no-compare: 不显示环比
  --compare-period: 对比周期，day(上一日，默认)|week(一周前)|month(一月前)
  --format: 输出格式，console(默认) 或 markdown

Config: config/skills/com.jachin.bi.daily_report/bi_metrics.yaml（规范 075）
"""
from __future__ import annotations

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from l3_node.primitives.mcp.mcp_tools.bi.metrics.engine import run, main_cli


def main() -> int:
    return main_cli()


if __name__ == "__main__":
    sys.exit(main())
