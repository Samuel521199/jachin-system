"""
Lark 入站通道 — 长连接优先，支持多机共享 chat_id 绑定
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from l3_node.channels.lark.client import _api_base_from_domain, get_tenant_access_token
from l3_node.channels.lark.im import send_text
from l3_node.im_channels.base import InboundIMChannel

logger = logging.getLogger(__name__)


def _resolve_lark_im_domain(config: dict[str, Any]) -> str:
    """
    入站长连接 / IM 文本回覆使用的开放平台 ``domain``。

    **勿**在未设置 ``LARK_USE_FEISHU`` 时退回到 ``FEISHU_DOMAIN``：巡检推送可走飞书中国 Open API
    （``FEISHU_*``），而机器人 WebSocket 仍可能是 Lark 国际版应用，误连 ``open.feishu.cn`` 会触发
    ``1000040351 Incorrect domain name``。
    """
    import os

    cd = (config.get("domain") or "").strip()
    if cd:
        return cd
    lark_dom = os.environ.get("LARK_DOMAIN", "").strip()
    if lark_dom:
        return lark_dom
    if os.environ.get("LARK_USE_FEISHU", "").lower() in ("1", "true", "yes"):
        return (os.environ.get("FEISHU_DOMAIN", "").strip() or "https://open.feishu.cn")
    return "https://open.larksuite.com"


class LarkInboundChannel(InboundIMChannel):
    """Lark 入站通道 — 长连接模式"""

    id = "lark"
    label = "Lark"

    def start(
        self,
        config: dict[str, Any],
        on_message: Callable[[str, str, str], None],
    ) -> None:
        mode = (config.get("mode") or "long_connection").lower()
        if mode != "long_connection":
            logger.info("[IM Lark] mode=%s 跳过（当前仅支持 long_connection）", mode)
            return

        import os
        app_id = (config.get("app_id") or os.environ.get("LARK_APP_ID") or os.environ.get("FEISHU_APP_ID") or "").strip()
        app_secret = (config.get("app_secret") or os.environ.get("LARK_APP_SECRET") or os.environ.get("FEISHU_APP_SECRET") or "").strip()
        if not app_id or not app_secret:
            logger.warning("[IM Lark] 未配置 app_id/app_secret（config 或 LARK_APP_ID/SECRET 环境变量），跳过")
            return

        chat_ids = config.get("chat_ids") or []
        if isinstance(chat_ids, list):
            allowed = [str(c).strip() for c in chat_ids if c]
        else:
            allowed = []

        def _on_raw(text: str, chat_id: str, user_id: str) -> None:
            logger.info("[IM Lark] 收到消息 chat_id=%s text=%s", (chat_id or "")[:20], (text or "")[:50])
            if not self.should_handle_chat(config, chat_id):
                logger.debug(
                    "[IM Lark] chat_id=%s 不在本节点 chat_ids 内，忽略（多机共享）",
                    (chat_id or "")[:20],
                )
                return
            try:
                on_message(text, chat_id, user_id)
            except Exception:
                logger.exception("[IM Lark] on_message 异常")

        domain = _resolve_lark_im_domain(config)
        try:
            from l3_node.channels.lark.long_connection import start_long_connection
        except ImportError as e:
            logger.error("[IM Lark] 长连接依赖缺失: %s，请安装 lark-oapi", e)
            return

        logger.info(
            "[IM Lark] 长连接启动中 app_id=%s domain=%s chat_ids=%s",
            app_id[:12] + "..." if len(app_id) > 12 else app_id,
            domain,
            allowed[:5] if len(allowed) > 5 else allowed,
        )
        start_long_connection(
            app_id,
            app_secret,
            _on_raw,
            domain=domain,
            log_level="INFO",
        )


def create_lark_send_reply(config: dict[str, Any]) -> Callable[[str, str], bool]:
    """创建 Lark 回复发送函数，使用配置中的 app_id/secret/domain"""
    import os
    app_id = (config.get("app_id") or os.environ.get("LARK_APP_ID") or os.environ.get("FEISHU_APP_ID") or "").strip()
    app_secret = (config.get("app_secret") or os.environ.get("LARK_APP_SECRET") or os.environ.get("FEISHU_APP_SECRET") or "").strip()
    domain = _resolve_lark_im_domain(config)
    api_base = _api_base_from_domain(domain)

    def send(chat_id: str, text: str) -> bool:
        if not chat_id or not text:
            return False
        try:
            token = get_tenant_access_token(
                app_id=app_id, app_secret=app_secret, api_base=api_base
            )
            res = send_text(
                chat_id,
                text,
                token=token,
                app_id=app_id,
                app_secret=app_secret,
                api_base=api_base,
            )
            return res.get("status") == "success"
        except Exception as e:
            logger.warning("[IM Lark] 回复发送失败: %s", e)
            return False

    return send
