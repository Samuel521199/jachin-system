"""
Lark 通道 — 入站长连接（WebSocket）

使用 lark-oapi WebSocket 客户端与飞书建立长连接，接收 im.message.receive_v1 事件。
无需公网 IP/ngrok，适合本地开发与部署。
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable

logger = logging.getLogger(__name__)


def resolve_lark_ws_log_level(log_level: int | str | object) -> object:
    """
    lark.ws.Client 需要 ``lark.LogLevel`` 枚举；调用方常传 ``logging.INFO``（int）会触发
    ``AttributeError: 'int' object has no attribute 'value'``。
    """
    try:
        import lark_oapi as lark
    except ImportError as e:
        raise ImportError("请安装 lark-oapi: pip install lark-oapi") from e

    if hasattr(log_level, "value"):
        return log_level

    level_map = {
        "DEBUG": lark.LogLevel.DEBUG,
        "INFO": lark.LogLevel.INFO,
        "WARN": lark.LogLevel.WARNING,
        "WARNING": lark.LogLevel.WARNING,
    }
    if isinstance(log_level, str):
        return level_map.get(log_level.strip().upper(), lark.LogLevel.INFO)

    if isinstance(log_level, int):
        if log_level <= logging.DEBUG:
            return lark.LogLevel.DEBUG
        if log_level <= logging.INFO:
            return lark.LogLevel.INFO
        return lark.LogLevel.WARNING

    return lark.LogLevel.INFO


def _patch_lark_oapi_ws_keepalive() -> None:
    """
    lark-oapi 内部使用 ``websockets.connect(conn_url)`` 且未传 ping 参数；
    websockets 16 默认 ping_interval/ping_timeout=20s。与 L3 run_agent、神盾 Compaction
    等**共用同一 asyncio 事件循环**时，若循环被同步计算（如 tiktoken）短时占用，协议层
    pong 可能逾时，触发 ``1011 (internal error) keepalive ping timeout`` 断连。

    仅替换 ``lark_oapi.ws.client`` 模块内对已 import 的 ``websockets.connect`` 的引用，
    不影响本进程其它 WebSocket 客户端的默认行为。
    """
    try:
        import lark_oapi.ws.client as lark_ws_client
    except ImportError:
        return
    if getattr(lark_ws_client, "_jachin_keepalive_patched", False):
        return
    _real = lark_ws_client.websockets.connect
    ping_iv = float((os.environ.get("LARK_WS_PING_INTERVAL") or "30").strip() or "30")
    ping_to = float((os.environ.get("LARK_WS_PING_TIMEOUT") or "120").strip() or "120")

    async def _connect_with_keepalive(uri, *args, **kwargs):
        kwargs.setdefault("ping_interval", ping_iv)
        kwargs.setdefault("ping_timeout", ping_to)
        return await _real(uri, *args, **kwargs)

    lark_ws_client.websockets.connect = _connect_with_keepalive  # type: ignore[method-assign]
    setattr(lark_ws_client, "_jachin_keepalive_patched", True)
    logger.info(
        "[Lark] WS 协议层 keepalive 已放宽：ping_interval=%ss ping_timeout=%ss "
        "（环境变量 LARK_WS_PING_INTERVAL / LARK_WS_PING_TIMEOUT 可覆盖）",
        ping_iv,
        ping_to,
    )


def _extract_from_p2_event(data: object) -> tuple[str, str, str] | None:
    """
    从 P2ImMessageReceiveV1 事件数据提取 (text, chat_id, user_id)。

    **第二项 chat_id 即飞书「会话 ID」**（单聊与机器人会话多为 oc_ 开头），与 HR 插件、
    im 发消息默认 receive_id_type=chat_id、环境变量 **LARK_CHAT_ID** 使用同一字段。
    第三项为发送方租户 user_id（非会话 ID）；若需用户 open_id(ou_) 见日志中的 sender.open_id。
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
        open_id_log = ""
        if sender:
            sid = getattr(sender, "sender_id", None)
            if sid:
                user_id = str(getattr(sid, "user_id", "") or "").strip()
                open_id_log = str(getattr(sid, "open_id", "") or "").strip()
        _cid_show = (chat_id[:36] + "…") if len(chat_id) > 36 else chat_id
        _txt_show = (text[:48] + "…") if len(text) > 48 else text
        logger.info(
            "[Lark 入站] 会话ID(chat_id)→可配 LARK_CHAT_ID / 发消息用 receive_id_type=chat_id | %s | "
            "sender.open_id=%s sender.user_id=%s | text=%s",
            _cid_show,
            open_id_log or "(empty)",
            user_id or "(empty)",
            _txt_show,
        )
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
    :param on_message: 回调 (text, chat_id, user_id)；**chat_id 为会话 ID**（常配 LARK_CHAT_ID）
    :param domain: 开放平台域名，默认 LARK_DOMAIN（国际版），飞书中国版用 FEISHU_DOMAIN
    :param log_level: lark.LogLevel.DEBUG / INFO / WARN 或 "DEBUG"/"INFO"
    """
    try:
        import lark_oapi as lark
    except ImportError:
        raise ImportError("请安装 lark-oapi: pip install lark-oapi") from None

    _patch_lark_oapi_ws_keepalive()

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

    ll = resolve_lark_ws_log_level(log_level)
    dom = domain or LARK_DOMAIN

    cli = lark.ws.Client(
        app_id, app_secret,
        event_handler=event_handler,
        log_level=ll,
        domain=dom,
    )
    logger.info("Lark 长连接启动中，连接成功后可在 Lark 后台切换订阅方式为「使用长连接接收回调」")
    try:
        cli.start()
    except Exception as e:
        err_msg = str(e)
        if "1000040351" in err_msg or "Incorrect domain name" in err_msg:
            other = LARK_DOMAIN if "feishu.cn" in (dom or "") else FEISHU_DOMAIN
            logger.error(
                "[Lark] 1000040351 Incorrect domain name：应用创建平台与 domain 不匹配。"
                "当前 domain=%s，请尝试改为 %s（im_channels.yaml 的 lark.domain 或环境变量 LARK_DOMAIN）。"
                "若应用确为飞书中国版且走长连接，请设 LARK_USE_FEISHU=1 后再用 open.feishu.cn；"
                "巡检卡片 Open API 与机器人 WS 域名独立，勿仅凭 FEISHU_DOMAIN 推断 WS。",
                dom,
                other,
            )
        raise
