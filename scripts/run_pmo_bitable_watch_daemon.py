#!/usr/bin/env python3
"""
PMO 多维表变更监控 — 独立守护进程（无需手跑 tick）

- 每 poll_interval_seconds（默认 15s）自动拉表 diff
- 编辑结束 idle_seconds（默认 20s）无新变更 → 推 Lark + 本机落盘
- 本机输出：~/.jachin/data/pmo_bitable_watch_callbacks/

用法：
  python scripts/run_pmo_bitable_watch_daemon.py
  python scripts/run_pmo_bitable_watch_daemon.py --once   # 只跑一轮 tick 后退出
"""
from __future__ import annotations

import argparse
import json
import logging
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

_LOG_PATH = Path.home() / ".jachin" / "data" / "pmo_bitable_watch_daemon.log"


def _setup_logging() -> None:
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(h, logging.FileHandler) and h.baseFilename == str(_LOG_PATH) for h in root.handlers):
        fh = logging.FileHandler(_LOG_PATH, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        root.addHandler(sh)


def main() -> int:
    ap = argparse.ArgumentParser(description="PMO 多维表变更监控守护进程")
    ap.add_argument("--once", action="store_true", help="只执行一轮 tick 后退出")
    args = ap.parse_args()
    _setup_logging()

    from l3_node.tools.pmo_bitable_watch import _load_watch_config, run_bitable_watch_status

    cfg = _load_watch_config()
    print("[pmo_bitable_watch_daemon] 配置已加载")
    print(f"  table={cfg.get('table_id')} view={cfg.get('view_id')}")
    print(f"  poll={cfg.get('poll_interval_seconds')}s idle={cfg.get('idle_seconds')}s dry_run={cfg.get('dry_run')}")
    print(f"  本机回调目录: {Path.home() / '.jachin' / 'data' / 'pmo_bitable_watch_callbacks'}")
    print(f"  日志: {_LOG_PATH}")

    if args.once:
        from l3_node.jobs.pmo_bitable_watch_scheduler import run_pmo_bitable_watch_once

        out = run_pmo_bitable_watch_once()
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if str(out.get("status") or "").lower() in ("ok", "partial") else 1

    from l3_node.jobs.pmo_bitable_watch_scheduler import run_pmo_bitable_watch_daemon

    st = run_bitable_watch_status()
    print(f"  当前基线记录数: {st.get('baseline_record_count')}")
    print("[pmo_bitable_watch_daemon] 守护进程已启动，改表后无需再跑命令…")

    out = run_pmo_bitable_watch_daemon(block=True)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
