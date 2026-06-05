#!/usr/bin/env python3
"""
PMO 变更预警：手动触发一次分析（模拟 Webhook / 会话变更事件）。

用法：
  # 从 JSON 文件读取 events（bitable diff 格式）
  python scripts/run_pmo_change_alert_once.py --events-file data/sample_change_events.json

  # 模拟 Gavin 麻将插单（与案例文档一致）
  python scripts/run_pmo_change_alert_once.py --demo mahjong

  # 分析 + 推送（dry-run 预览）
  python scripts/run_pmo_change_alert_once.py --demo mahjong --push --dry-run

  # 跑 bitable watch 一次 tick（含变更预警流水线）
  python scripts/run_pmo_change_alert_once.py --watch-tick
  python scripts/run_pmo_change_alert_once.py --watch-tick --force-finalize
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env", override=False)
    load_dotenv(Path.home() / ".jachin" / ".env", override=False)
except Exception:
    pass


def _demo_mahjong_event() -> list[dict]:
    return [
        {
            "change_type": "created",
            "record_id": "rec_demo_mahjong",
            "label": "麻将花色增加开发",
            "before": {},
            "after": {
                "Requirement": "麻将花色增加开发",
                "Person in charge/Participant": "Gavin",
                "Sprint": "2026/06/01-Sprint",
                "Start Date": "2026-06-05",
                "Expected Delivery Date": "2026-06-05",
                "Acceptable Delivery Date": "2026-06-06",
                "priority": "P1",
            },
            "changed_fields": {
                "Requirement": {"before": "", "after": "麻将花色增加开发"},
                "Person in charge/Participant": {"before": "", "after": "Gavin"},
                "Sprint": {"before": "", "after": "2026/06/01-Sprint"},
                "Start Date": {"before": "", "after": "2026-06-05"},
                "Expected Delivery Date": {"before": "", "after": "2026-06-05"},
            },
            "view_id": "vewpI8lyYw",
            "table_id": "tblfK9gk6vTQpJtB",
        }
    ]


def _demo_no_assignee_event() -> list[dict]:
    return [
        {
            "change_type": "created",
            "record_id": "rec_demo_incomplete",
            "label": "麻将大厅重做",
            "before": {},
            "after": {
                "Requirement": "麻将大厅重做",
                "Sprint": "2026/06/01-Sprint",
            },
            "changed_fields": {
                "Requirement": {"before": "", "after": "麻将大厅重做"},
                "Sprint": {"before": "", "after": "2026/06/01-Sprint"},
            },
        }
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description="PMO 变更预警：手动触发分析")
    ap.add_argument("--events-file", type=Path, help="JSON 文件，含 events 数组")
    ap.add_argument(
        "--demo",
        choices=("mahjong", "incomplete"),
        help="内置演示变更",
    )
    ap.add_argument("--webhook-file", type=Path, help="Webhook payload JSON")
    ap.add_argument("--push", action="store_true", help="有问题时推送 Lark")
    ap.add_argument("--dry-run", action="store_true", help="推送预览，不实际发送")
    ap.add_argument("--chat-id", default="", help="覆盖推送 chat_id")
    ap.add_argument("--watch-tick", action="store_true", help="运行 bitable watch 单次 tick")
    ap.add_argument(
        "--force-finalize",
        action="store_true",
        help="与 --watch-tick 联用：立即结束防抖会话",
    )
    args = ap.parse_args()

    if args.watch_tick:
        from l3_node.jobs.pmo_bitable_watch_scheduler import run_pmo_bitable_watch_once

        out = run_pmo_bitable_watch_once(force_finalize=args.force_finalize)
        print(json.dumps(out, ensure_ascii=True, indent=2))
        return 0 if str(out.get("status") or "").lower() in ("ok", "success") else 1

    events: list[dict] = []
    webhook_payload = None

    if args.events_file:
        raw = json.loads(args.events_file.read_text(encoding="utf-8"))
        events = raw if isinstance(raw, list) else list(raw.get("events") or [])
    elif args.webhook_file:
        webhook_payload = json.loads(args.webhook_file.read_text(encoding="utf-8"))
    elif args.demo == "mahjong":
        events = _demo_mahjong_event()
    elif args.demo == "incomplete":
        events = _demo_no_assignee_event()
    else:
        ap.error("请指定 --events-file、--webhook-file、--demo 或 --watch-tick")

    from l3_node.tools.pmo_change_alert import run_change_alert_analyze

    out = run_change_alert_analyze(
        events=events or None,
        webhook_payload=webhook_payload,
        push=args.push,
        dry_run=args.dry_run,
        chat_id=args.chat_id or None,
    )
    print(json.dumps(out, ensure_ascii=True, indent=2))
    result = str(out.get("change_alert_result") or "")
    if result == "all_clear" and not args.push:
        print("\n[change_alert] all_clear — 无需推送")
    return 0 if str(out.get("status") or "").lower() in ("ok", "success", "partial") else 1


if __name__ == "__main__":
    raise SystemExit(main())
