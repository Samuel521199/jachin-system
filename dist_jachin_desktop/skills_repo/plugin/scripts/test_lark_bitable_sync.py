#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试 atom_lark_bitable_sync：将多 Agent 评审结果 MD 导入 Lark 多维表格。

用法：
  1. 配置 .env：LARK_APP_ID=cli_xxx LARK_APP_SECRET=xxx
  2. 干跑（仅解析不写入）：python scripts\test_lark_bitable_sync.py --dry-run
  3. 正式写入：python scripts\test_lark_bitable_sync.py
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "2-track-a-atomic-mcp"))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from tools.atom_lark_bitable_sync import atom_lark_bitable_sync, list_bitable_fields

MOCK_MD = ROOT / "docs" / "MOCK_MULTI_AGENT_RESULT.md"
# 默认使用排行榜 Summary（若存在）；否则用 MOCK
DEFAULT_RANKING_MD = ROOT / "data" / "java工程师_杭州 10-15K" / "排行榜_Summary.md"

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--md", default="", help="MD 文档路径，不填则用 data/java工程师_杭州 10-15K/排行榜_Summary.md")
    p.add_argument("--dry-run", action="store_true", help="仅解析，不写入 Lark")
    p.add_argument("--no-notify", action="store_true", help="不向群聊发送通知")
    p.add_argument("--no-replace-entire", action="store_true", help="不清空全表，仅覆盖该职位记录（一表多职位时用）")
    p.add_argument("--list-fields", action="store_true", help="列出多维表列名，用于对照 field_mapping")
    args = p.parse_args()

    md_path = args.md or (str(DEFAULT_RANKING_MD) if DEFAULT_RANKING_MD.exists() else str(MOCK_MD))
    if args.list_fields:
        out = list_bitable_fields()
        print("success:", out.get("success"))
        if out.get("fields"):
            print("多维表列名:")
            for f in out["fields"]:
                print(f"  - {f.get('field_name')} ({f.get('type')})")
        if out.get("error"):
            print("error:", out["error"])
        sys.exit(0)

    out = atom_lark_bitable_sync(
        md_path=md_path,
        dry_run=args.dry_run,
        notify_group=not args.no_notify,
        replace_entire_table=not args.no_replace_entire,  # 默认清空全表，新数据从第一行写入
    )
    print("success:", out.get("success"))
    if out.get("parsed"):
        print("parsed:", out["parsed"])
    if out.get("fields_preview"):
        print("fields_preview:", out["fields_preview"])
    if out.get("record_id"):
        print("record_id:", out["record_id"])
    if out.get("notify_sent") is not None:
        print("群通知已发送:", out["notify_sent"])
    if out.get("error"):
        print("error:", out["error"])
    if out.get("message"):
        print("message:", out["message"])
