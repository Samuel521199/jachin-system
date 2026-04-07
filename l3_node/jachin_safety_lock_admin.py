"""
安全锁管理员 CLI：审批 / 拒绝 pending、触发维护扫描。

管理员密钥 **仅** 从本机环境变量读取，不经过大模型；用法见 docs/JACHIN_SAFETY_LOCK.md。

  set JACHIN_SAFETY_LOCK_ADMIN_TOKEN=你的密钥
  python -m l3_node.jachin_safety_lock_admin list
  python -m l3_node.jachin_safety_lock_admin approve <pending_id>
  python -m l3_node.jachin_safety_lock_admin reject <pending_id>
  python -m l3_node.jachin_safety_lock_admin maintenance
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def main() -> int:
    p = argparse.ArgumentParser(description="Jachin safety lock admin (human-in-the-loop)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="列出待审批条目")

    ap = sub.add_parser("approve", help="将 pending 刷入正式 JACHIN_SAFETY_LOCK.md")
    ap.add_argument("pending_id")
    ap.add_argument(
        "--token",
        default="",
        help="若未设置环境变量，可在此传入（仍仅应在本地 shell 使用，勿粘贴到聊天）",
    )

    rp = sub.add_parser("reject", help="删除 pending 文件")
    rp.add_argument("pending_id")
    rp.add_argument("--token", default="")

    sub.add_parser("maintenance", help="运行启发式维护扫描并写 audit 日志")

    args = p.parse_args()
    tok = os.environ.get("JACHIN_SAFETY_LOCK_ADMIN_TOKEN", "").strip() or getattr(args, "token", "")

    from l3_node.jachin_safety_lock import (
        approve_pending,
        list_pending_entries,
        reject_pending,
        run_maintenance_scan,
    )

    if args.cmd == "list":
        r = list_pending_entries()
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "approve":
        r = approve_pending(args.pending_id, tok)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r.get("ok") else 1
    if args.cmd == "reject":
        r = reject_pending(args.pending_id, tok)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r.get("ok") else 1
    if args.cmd == "maintenance":
        r = run_maintenance_scan()
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r.get("ok") else 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
