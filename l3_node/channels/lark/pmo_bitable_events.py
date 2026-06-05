"""
PMO — 飞书多维表记录变更事件（drive.file.bitable_record_changed_v1）

长连接接收 Lark 推送；收到后刷新防抖会话，由 debounce scheduler 在 idle 后分析推送。
文档：https://open.larksuite.com/document/server-docs/docs/drive-v1/event/list/bitable-record-changed
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

LARK_BITABLE_EVENT_TYPE = "drive.file.bitable_record_changed_v1"


def _dispatch_async(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, daemon=True).start()


def process_lark_bitable_record_changed(body: dict[str, Any]) -> dict[str, Any]:
    """解析并摄入 Lark 多维表变更事件（须在 3s 内返回，重活放后台）。"""
    from l3_node.tools.pmo_bitable_watch import handle_lark_bitable_record_changed

    return handle_lark_bitable_record_changed(body)


def start_pmo_bitable_long_connection(
    app_id: str,
    app_secret: str,
    *,
    domain: str | None = None,
    log_level: int | str = "INFO",
) -> None:
    """
    启动 Lark 长连接，订阅 drive.file.bitable_record_changed_v1。
    阻塞运行；须在开放平台将订阅方式设为「使用长连接接收事件」并添加该事件。
    """
    try:
        import lark_oapi as lark
    except ImportError as e:
        raise ImportError("请安装 lark-oapi: pip install lark-oapi") from e

    from l3_node.channels.lark.long_connection import (
        FEISHU_DOMAIN,
        LARK_DOMAIN,
        _patch_lark_oapi_ws_keepalive,
    )

    _patch_lark_oapi_ws_keepalive()

    def _on_p2(data: Any) -> None:
        try:
            body = lark.JSON.marshal(data)
            if not isinstance(body, dict):
                body = {}
        except Exception:
            body = {}
        if not body:
            logger.warning("[pmo_bitable_events] 无法序列化 P2 事件")
            return

        def _work() -> None:
            try:
                out = process_lark_bitable_record_changed(body)
                logger.info(
                    "[pmo_bitable_events] 长连接摄入 merged=%s skipped=%s",
                    out.get("merged"),
                    out.get("skipped"),
                )
            except Exception:
                logger.exception("[pmo_bitable_events] 处理事件失败")

        _dispatch_async(_work)

    builder = lark.EventDispatcherHandler.builder("", "")
    registered = False
    if hasattr(builder, "register_p2_drive_file_bitable_record_changed_v1"):
        builder = builder.register_p2_drive_file_bitable_record_changed_v1(_on_p2)
        registered = True
    else:
        logger.warning(
            "[pmo_bitable_events] SDK 无 register_p2_drive_file_bitable_record_changed_v1，"
            "尝试 register_p1_customized_event"
        )
        builder = builder.register_p1_customized_event(LARK_BITABLE_EVENT_TYPE, _on_p2)
        registered = True

    event_handler = builder.build()
    if not registered:
        raise RuntimeError("无法注册 bitable_record_changed 事件处理器")

    level_map = {
        "DEBUG": lark.LogLevel.DEBUG,
        "INFO": lark.LogLevel.INFO,
        "WARN": lark.LogLevel.WARNING,
        "WARNING": lark.LogLevel.WARNING,
    }
    ll = (
        level_map.get(str(log_level).upper(), lark.LogLevel.INFO)
        if isinstance(log_level, str)
        else log_level
    )
    dom = domain or LARK_DOMAIN

    cli = lark.ws.Client(
        app_id,
        app_secret,
        event_handler=event_handler,
        log_level=ll,
        domain=dom,
    )
    logger.info(
        "[pmo_bitable_events] 长连接启动中 event=%s domain=%s app_id=%s…",
        LARK_BITABLE_EVENT_TYPE,
        dom,
        (app_id or "")[:12],
    )
    cli.start()
