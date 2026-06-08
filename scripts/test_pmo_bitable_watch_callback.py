#!/usr/bin/env python3
"""
PMO 多维表变更监控 — 回调链路自测（无需真改飞书表）

用法：
  # 1) 模拟 Lark 推送一条 record_edited 事件
  python scripts/test_pmo_bitable_watch_callback.py --inject

  # 2) 查看当前监控状态
  python scripts/test_pmo_bitable_watch_callback.py --status

  # 3) 强制结束防抖会话并触发分析/推送（跳过 idle_seconds 等待）
  python scripts/test_pmo_bitable_watch_callback.py --finalize

  # 4) 三步一条龙：inject → 等 2s → finalize
  python scripts/test_pmo_bitable_watch_callback.py --e2e

真机验证（推荐开着监看脚本，改表后应立即有反应）：
  python scripts/watch_pmo_bitable_lark_events.py
  # 或 tail 长连接日志：
  Get-Content ~/.jachin/data/pmo_bitable_watch_long_connection.log -Wait -Tail 20
"""
from __future__ import annotations

import argparse
import json
import sys
import time
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

_SAMPLE_LARK_EVENT = {
    "schema": "2.0",
    "header": {
        "event_id": "test_pmo_watch_e2e",
        "event_type": "drive.file.bitable_record_changed_v1",
        "create_time": "1700000000000",
        "app_id": "cli_a9253a96b179deee",
    },
    "event": {
        "table_id": "tblfK9gk6vTQpJtB",
        "file_token": "",
        "file_type": "bitable",
        "action_list": [
            {
                "action": "record_edited",
                "record_id": "recTEST_CALLBACK_VERIFY",
                "before_value": [
                    {"field_id": "fld_req", "field_value": "【测试】变更前任务名"},
                ],
                "after_value": [
                    {"field_id": "fld_req", "field_value": "【测试】变更后任务名"},
                    {"field_id": "fld_pri", "field_value": "P0"},
                ],
            }
        ],
    },
}


def cmd_status() -> int:
    from l3_node.tools.pmo_bitable_watch import run_bitable_watch_status

    st = run_bitable_watch_status()
    print(json.dumps(st, ensure_ascii=False, indent=2))
    print("\n--- 判读 ---")
    print(f"  mode={st.get('mode')}  session_active={st.get('session_active')}")
    print(f"  session_event_count={st.get('session_event_count')}")
    print(f"  seconds_since_last_change={st.get('seconds_since_last_change')}")
    print(f"  callback_latest_md={st.get('callback_latest_md')}")
    return 0


def cmd_inject() -> int:
    from l3_node.tools.pmo_bitable_watch import handle_lark_bitable_record_changed

    print("[test] 注入模拟 Lark 事件 …")
    out = handle_lark_bitable_record_changed(_SAMPLE_LARK_EVENT)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if int(out.get("merged") or 0) < 1:
        print("\n[FAIL] merged=0，检查 table_id/app_token 是否与配置一致")
        return 1
    print("\n[OK] 系统已收到回调并入防抖会话。idle_seconds 无新事件后会推 Lark；或运行 --finalize")
    return 0


def cmd_finalize(*, dry_run: bool) -> int:
    from l3_node.jobs.pmo_bitable_watch_scheduler import run_pmo_bitable_watch_once

    print("[test] 强制 finalize 防抖会话 …")
    out = run_pmo_bitable_watch_once(force_finalize=True)
    if dry_run:
        from l3_node.tools.pmo_bitable_watch import _load_watch_config

        cfg = _load_watch_config()
        cfg["dry_run"] = True
    print(json.dumps(out, ensure_ascii=False, indent=2))
    action = out.get("action") or ""
    if action == "session_finalized_notify":
        paths = out.get("local_paths") or {}
        print("\n[OK] 会话已结束")
        if paths:
            print(f"  本机 latest.md → {paths.get('latest_md')}")
        print(f"  notified={out.get('notified')}")
        return 0
    if action == "session_finalized_notify" or out.get("event_count", 0) == 0:
        print("\n[WARN] 无累积变更或会话为空")
    return 0 if str(out.get("status") or "").lower() in ("ok", "partial") else 1


def cmd_e2e(dry_run: bool) -> int:
    if cmd_inject() != 0:
        return 1
    time.sleep(2)
    return cmd_finalize(dry_run=dry_run)


def main() -> int:
    ap = argparse.ArgumentParser(description="PMO bitable watch 回调自测")
    ap.add_argument("--status", action="store_true", help="查看监控状态")
    ap.add_argument("--inject", action="store_true", help="模拟 Lark 事件注入")
    ap.add_argument("--finalize", action="store_true", help="强制结束会话并推送")
    ap.add_argument("--e2e", action="store_true", help="inject + finalize 一条龙")
    ap.add_argument("--dry-run", action="store_true", help="finalize 时不真推 Lark")
    args = ap.parse_args()

    if args.status:
        return cmd_status()
    if args.inject:
        return cmd_inject()
    if args.finalize:
        return cmd_finalize(dry_run=args.dry_run)
    if args.e2e:
        return cmd_e2e(dry_run=args.dry_run)
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
