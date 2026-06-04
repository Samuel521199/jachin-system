#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI：Work 总宏观看板推送（封装 core:pmo_macro_dashboard_push）。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_env_root = (os.environ.get("JACHIN_APP_ROOT") or "").strip()
ROOT = Path(_env_root).resolve() if _env_root else Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chat-id", default="")
    ap.add_argument("--monitor-chat-id", default="")
    ap.add_argument("--no-monitor", action="store_true")
    ap.add_argument("--app-id", default=os.environ.get("LARK_APP_ID", ""))
    ap.add_argument("--app-secret", default=os.environ.get("LARK_APP_SECRET", ""))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out-md", default="")
    args = ap.parse_args()

    from l3_node.tools.pmo_macro_dashboard import (
        build_polished_macro_dashboard_markdown,
        run_macro_dashboard_push,
    )

    if args.out_md:
        md, _, _ = build_polished_macro_dashboard_markdown()
        Path(args.out_md).write_text(md, encoding="utf-8")
        print(f"markdown -> {args.out_md}")

    if args.dry_run and not args.out_md:
        result = run_macro_dashboard_push(dry_run=True)
    else:
        result = run_macro_dashboard_push(
            chat_id=(args.chat_id or None),
            monitor_chat_id=(args.monitor_chat_id or None),
            push_monitor=not args.no_monitor,
            app_id=(args.app_id or None),
            app_secret=(args.app_secret or None),
            dry_run=args.dry_run,
            project_root=ROOT,
        )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if str(result.get("status") or "").lower() in ("success", "ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
