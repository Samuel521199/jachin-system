"""
Lark 通道 — 入站长连接（WebSocket）

使用 lark-oapi WebSocket 客户端与飞书建立长连接，接收 im.message.receive_v1 事件。
无需公网 IP/ngrok，适合本地开发与部署。
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


def _extract_from_p2_event(data: object) -> tuple[str, str, str] | None:
    """
    从 P2ImMessageReceiveV1 事件数据提取 (text, chat_id, user_id)。
    lark-oapi 结构: data.event.message (chat_id, content), data.event.sender (sender_id.user_id).
    """
    try:
        ev = getattr(data, "event", None)
        if not ev:
            return None
        sender = getattr(ev, "sender", None)
        if sender and getattr(sender, "sender_type", "") == "app":
            return None
        msg = getattr(ev, "message", None)
        if not msg:
            return None
        chat_id = str(getattr(msg, "chat_id", "") or "")
        content_raw = str(getattr(msg, "content", "") or "")
        try:
            content = json.loads(content_raw) if content_raw else {}
        except json.JSONDecodeError:
            content = {}
        text = (content.get("text", "") or "").strip()
        if not text:
            return None
        user_id = ""
        if sender:
            sid = getattr(sender, "sender_id", None)
            if sid:
                user_id = str(getattr(sid, "user_id", "") or "")
        return text, chat_id, user_id
    except Exception as e:
        logger.warning("解析 P2ImMessageReceiveV1 失败: %s", e)
        return None


# 飞书中国版 vs Lark 国际版
FEISHU_DOMAIN = "https://open.feishu.cn"
LARK_DOMAIN = "https://open.larksuite.com"


def start_long_connection(
    app_id: str,
    app_secret: str,
    on_message: Callable[[str, str, str], None],
    *,
    domain: str | None = None,
    log_level: int | str = "INFO",
) -> None:
    """
    启动 Lark 长连接，收到 im.message.receive_v1 时调用 on_message(text, chat_id, user_id)。
    阻塞运行，直到进程退出。

    :param app_id: LARK_APP_ID
    :param app_secret: LARK_APP_SECRET
    :param on_message: 回调 (text, chat_id, user_id)
    :param domain: 开放平台域名，默认 LARK_DOMAIN（国际版），飞书中国版用 FEISHU_DOMAIN
    :param log_level: lark.LogLevel.DEBUG / INFO / WARN 或 "DEBUG"/"INFO"
    """
    try:
        import lark_oapi as lark
    except ImportError:
        raise ImportError("请安装 lark-oapi: pip install lark-oapi") from None

    def _handler(data) -> None:
        parsed = _extract_from_p2_event(data)
        if parsed:
            text, chat_id, user_id = parsed
            try:
                on_message(text, chat_id, user_id)
            except Exception:
                logger.exception("on_message 回调异常")

    event_handler = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(
        _handler
    ).build()

    level_map = {
        "DEBUG": lark.LogLevel.DEBUG,
        "INFO": lark.LogLevel.INFO,
        "WARN": lark.LogLevel.WARNING,
        "WARNING": lark.LogLevel.WARNING,
    }
    ll = level_map.get(str(log_level).upper(), lark.LogLevel.INFO) if isinstance(log_level, str) else log_level
    dom = domain or LARK_DOMAIN

    cli = lark.ws.Client(
        app_id, app_secret,
        event_handler=event_handler,
        log_level=ll,
        domain=dom,
    )
    logger.info("Lark 长连接启动中，连接成功后可在 Lark 后台切换订阅方式为「使用长连接接收回调」")
    cli.start()
