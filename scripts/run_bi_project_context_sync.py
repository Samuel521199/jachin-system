#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BI 项目知识库同步 — 单独测试 mcp:atom_bi_project_context

仅拉取 Lark Wiki/多维表/文档等到 docs/bi_daily_report/bi_project/，不跑完整 BI 日报。

前置:
  1. 配置 LARK_APP_ID / LARK_APP_SECRET（.env 或环境变量）
  2. 可选: 复制 config/mcps/atom_bi_project_context/config.yaml.example 为 config.yaml 并编辑 wiki_urls

用法:
  python scripts/run_bi_project_context_sync.py
  python scripts/run_bi_project_context_sync.py --pretty
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
try:
    from dotenv import load_dotenv

    load_dotenv(root / ".env", encoding="utf-8")
except ImportError:
    pass
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def main() -> int:
    p = argparse.ArgumentParser(description="同步 Lark 项目文档到 docs/bi_daily_report/bi_project/")
    p.add_argument("--pretty", action="store_true", help="美化 JSON 输出")
    args = p.parse_args()

    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import sync_bi_project_context

    result = sync_bi_project_context(project_root=root)
    indent = 2 if args.pretty else None
    print(json.dumps(result, ensure_ascii=False, indent=indent))
    return 0 if result.get("status") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
