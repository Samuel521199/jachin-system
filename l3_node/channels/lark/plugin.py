"""
Lark 通道插件 — ChannelPlugin 实现
"""
from __future__ import annotations

from typing import Any


class LarkWebhookOutbound:
    """Webhook 出站适配器（无需 App 凭证）"""

    def send_text(
        self, target: str, text: str, *, title: str | None = None, chart_spec: dict | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        from l3_node.channels.lark.webhook import send_markdown

        return send_markdown(
            webhook_url=target,
            markdown_content=text,
            title=title,
            chart_spec=chart_spec,
        )


class LarkImOutbound:
    """IM 出站适配器（需 App 凭证）"""

    def send_text(
        self,
        target: str,
        text: str,
        *,
        receive_id_type: str = "chat_id",
        token: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        from l3_node.channels.lark.im import send_text as _send

        return _send(
            receive_id=target,
            text=text,
            receive_id_type=receive_id_type,
            token=token,
        )


class LarkChannelPlugin:
    """Lark/飞书 通道插件"""

    id = "lark"
    meta = {
        "label": "Lark",
        "aliases": ["feishu"],
        "order": 70,
    }
    outbound = LarkImOutbound()  # 默认使用 IM 出站


class LarkWebhookChannelPlugin:
    """Lark Webhook 通道（仅 Webhook 推送，无 App 凭证）"""

    id = "lark_webhook"
    meta = {
        "label": "Lark Webhook",
        "order": 71,
    }
    outbound = LarkWebhookOutbound()
