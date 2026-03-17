"""
Email 通道
"""
from __future__ import annotations

from l3_node.channels.email.plugin import EmailChannelPlugin
from l3_node.channels.email.smtp import send_email_with_attachment
from l3_node.channels.registry import register_channel_plugin

register_channel_plugin(EmailChannelPlugin())

__all__ = [
    "send_email_with_attachment",
    "EmailChannelPlugin",
]
