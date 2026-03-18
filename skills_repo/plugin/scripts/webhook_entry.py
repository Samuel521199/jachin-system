#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lark Webhook 独立 exe 入口

便携包部署时无需 Python，双击 webhook.exe 即可启动 Webhook 服务。
日志写入 exe 同目录的 webhook.log。
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# 最早确定 app_root 与 plugin_root（frozen 时 exe 在 dist_jachin_desktop）
if getattr(sys, "frozen", False):
    _app_root = Path(sys.executable).resolve().parent
    _plugin_root = _app_root / "skills_repo" / "plugin"
else:
    _plugin_root = Path(__file__).resolve().parent.parent
    _app_root = _plugin_root.parent.parent

# 路径：project root (l3_node)、plugin、2-track-a-atomic-mcp
sys.path.insert(0, str(_app_root))
sys.path.insert(0, str(_plugin_root))
sys.path.insert(0, str(_plugin_root / "2-track-a-atomic-mcp"))

for _env in [_plugin_root / ".env", _app_root / ".env"]:
    if _env.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(_env)
            break
        except ImportError:
            break

# 日志：webhook.log 在 exe 同目录
_log_path = _app_root / "webhook.log"
_handler = logging.FileHandler(_log_path, mode="a", encoding="utf-8")
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logging.root.addHandler(_handler)
logging.root.setLevel(logging.INFO)
# 抑制第三方刷屏
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("werkzeug").setLevel(logging.WARNING)


def main() -> None:
    import importlib
    logger = logging.getLogger("webhook")
    logger.info("Webhook 启动 app_root=%s plugin_root=%s log=%s", _app_root, _plugin_root, _log_path)
    try:
        mod = importlib.import_module("lark_bot")
        run_webhook_server = getattr(mod, "run_webhook_server")
    except ImportError as e:
        logger.exception("导入失败，请确认 skills_repo/plugin 与 exe 同目录: %s", e)
        sys.exit(1)
    port = int(os.environ.get("WEBHOOK_PORT", "5000"))
    logger.info("Webhook 监听端口 %d", port)
    run_webhook_server(port)


if __name__ == "__main__":
    main()
