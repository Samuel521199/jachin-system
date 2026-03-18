#!/usr/bin/env python3
"""
BI助手 — Lark 长连接监听脚本

专用于 BI助手 应用的 Lark 长连接，用于在飞书开放平台完成「使用长连接接收回调」的校验与保存。
连接成功后，可在 Lark 开发者后台 → 事件与回调 → 回调配置中启用并保存长连接模式。

Usage:
  python scripts/run_bi_lark_long_connection.py [--debug]

Env:
  BI_LARK_APP_ID / BI_LARK_APP_SECRET   BI助手应用凭证（推荐）
  LARK_APP_ID / LARK_APP_SECRET         备用通用凭证
  LARK_USE_FEISHU=1                     飞书中国版（open.feishu.cn）
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

# 加载凭证：优先从 config 中 bi_daily_report.yaml 的 lark_bitable 读取，否则从 .env
def _load_bi_lark_credentials() -> None:
    import os

    if os.environ.get("BI_LARK_APP_ID") or os.environ.get("LARK_APP_ID"):
        return
    try:
        import yaml

        cfg_path = _root / "config" / "skills" / "com.jachin.bi.daily_report" / "bi_daily_report.yaml"
        if cfg_path.exists():
            with open(cfg_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            lb = (cfg.get("lark_bitable") or {})
            aid = (lb.get("app_id") or "").strip()
            asec = (lb.get("app_secret") or "").strip()
            if aid and asec:
                os.environ["BI_LARK_APP_ID"] = aid
                os.environ["BI_LARK_APP_SECRET"] = asec
                os.environ.setdefault("LARK_APP_ID", aid)
                os.environ.setdefault("LARK_APP_SECRET", asec)
                return
    except Exception:
        pass
    try:
        from dotenv import load_dotenv

        _env = _root / ".env"
        if _env.exists():
            load_dotenv(_env, encoding="utf-8")
        else:
            load_dotenv()
    except ImportError:
        pass


_load_bi_lark_credentials()

from l3_node.channels.lark.long_connection import (
    FEISHU_DOMAIN,
    LARK_DOMAIN,
    start_long_connection,
)


def _on_message(text: str, chat_id: str, user_id: str) -> None:
    """收到消息时的回调，仅记录日志供调试与后续扩展"""
    log = logging.getLogger("bi_lark")
    log.info("[BI助手] 收到消息 | chat=%s user=%s text=%s", chat_id[:20], user_id or "-", text[:80])


def main() -> int:
    parser = argparse.ArgumentParser(description="BI助手 Lark 长连接监听")
    parser.add_argument("--debug", action="store_true", help="启用 DEBUG 日志")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    log = logging.getLogger("bi_lark")

    app_id = os.environ.get("BI_LARK_APP_ID") or os.environ.get("LARK_APP_ID")
    app_secret = os.environ.get("BI_LARK_APP_SECRET") or os.environ.get("LARK_APP_SECRET")

    if not app_id or not app_secret:
        log.error(
            "未配置凭证，请设置环境变量: BI_LARK_APP_ID / BI_LARK_APP_SECRET 或 LARK_APP_ID / LARK_APP_SECRET"
        )
        return 1

    use_feishu = os.environ.get("LARK_USE_FEISHU", "").lower() in ("1", "true", "yes")
    domain = FEISHU_DOMAIN if use_feishu else LARK_DOMAIN

    log.info("BI助手长连接启动中 | app_id=%s*** domain=%s", app_id[:8] if app_id else "-", domain)

    try:
        start_long_connection(
            app_id,
            app_secret,
            _on_message,
            domain=domain,
            log_level="DEBUG" if args.debug else "INFO",
        )
    except KeyboardInterrupt:
        log.info("已退出")
        return 0
    except Exception as e:
        log.exception("长连接异常: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
