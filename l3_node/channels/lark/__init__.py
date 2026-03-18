"""
Lark/飞书 通道
"""
from __future__ import annotations

from l3_node.channels.lark.client import get_tenant_access_token
from l3_node.channels.lark.im import send_text as send_im_text
from l3_node.channels.lark.inbound_webhook import create_lark_webhook_app, parse_lark_im_message
from l3_node.channels.lark.plugin import LarkChannelPlugin, LarkWebhookChannelPlugin
from l3_node.channels.lark.webhook import send_markdown
from l3_node.channels.registry import register_channel_plugin

register_channel_plugin(LarkChannelPlugin())
register_channel_plugin(LarkWebhookChannelPlugin())

__all__ = [
    "get_tenant_access_token",
    "send_markdown",
    "send_im_text",
    "create_lark_webhook_app",
    "parse_lark_im_message",
    "LarkChannelPlugin",
    "LarkWebhookChannelPlugin",
]
