#!/usr/bin/env python3
"""
PMO 多维表变更监控 — 飞书长连接（事件驱动，非轮询拉表）

飞书在有人改表时主动推送 drive.file.bitable_record_changed_v1；
本脚本建立 WebSocket 长连接接收事件，并后台启动 debounce 检查器（idle 后分析推 Lark）。

开放平台配置（pmo_bitable_watch.yaml 的 app_id，当前为 cli_a9253a96…）：
  1. 事件与回调 → 订阅方式 → 「使用长连接接收事件」
  2. 添加事件 → 「多维表格记录变更」drive.file.bitable_record_changed_v1
  3. 在目标多维表上订阅变更（飞书文档内「订阅文档变更」）
  4. 保持本脚本运行且 connected 后再保存配置

用法：
  python scripts/run_pmo_bitable_watch_long_connection.py --domain lark
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
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

_LOG_PATH = Path.home() / ".jachin" / "data" / "pmo_bitable_watch_long_connection.log"


def _setup_logging(debug: bool) -> None:
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if debug else logging.INFO
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    fh = logging.FileHandler(_LOG_PATH, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(sh)


def _load_credentials() -> tuple[str, str, str]:
    from l3_node.tools.pmo_bitable_watch import _load_watch_config

    cfg = _load_watch_config()
    app_id = str(cfg.get("app_id") or "").strip()
    app_secret = str(cfg.get("app_secret") or "").strip()
    domain = "https://open.larksuite.com"
    return app_id, app_secret, domain


def main() -> int:
    ap = argparse.ArgumentParser(description="PMO 多维表变更 — Lark 长连接")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument(
        "--domain",
        choices=["feishu", "lark"],
        default="lark",
        help="lark=国际版 open.larksuite.com",
    )
    ap.add_argument(
        "--no-debounce-scheduler",
        action="store_true",
        help="不启动本地 debounce 检查器（仅收事件；若 L3 HTTP 已跑可省略）",
    )
    args = ap.parse_args()
    _setup_logging(args.debug)
    log = logging.getLogger("pmo_bitable_lc")

    app_id, app_secret, _ = _load_credentials()
    if not app_id or not app_secret:
        log.error("缺少 app_id/app_secret，请配置 ~/.jachin/config/skills/pmo-copilot/pmo_bitable_watch.yaml")
        return 1

    from l3_node.channels.lark.long_connection import FEISHU_DOMAIN, LARK_DOMAIN

    domain = FEISHU_DOMAIN if args.domain == "feishu" else LARK_DOMAIN

    if not args.no_debounce_scheduler:
        from l3_node.jobs.pmo_bitable_watch_scheduler import start_pmo_bitable_watch_scheduler

        st = start_pmo_bitable_watch_scheduler()
        log.info("debounce 检查器: %s", st)

    log.info("【请核对】应用 ID: %s", app_id)
    log.info("连接 %s · 等待飞书推送 bitable 变更…", domain)
    log.info("日志: %s", _LOG_PATH)

    from l3_node.channels.lark.pmo_bitable_events import start_pmo_bitable_long_connection

    start_pmo_bitable_long_connection(
        app_id,
        app_secret,
        domain=domain,
        log_level=logging.DEBUG if args.debug else logging.INFO,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
