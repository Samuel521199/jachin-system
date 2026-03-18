"""
Jachin 多通道通讯层

参考 OpenClaw 架构：通道插件注册到 registry，业务通过 get_channel_plugin(id) 调用。
当前支持：lark（飞书）、email（邮件）。
"""
from __future__ import annotations

from l3_node.channels.registry import get_channel_plugin, list_channel_plugins, register_channel_plugin

# 注册内置通道（导入时自动注册）
from l3_node.channels import lark  # noqa: F401
from l3_node.channels import email  # noqa: F401

__all__ = [
    "get_channel_plugin",
    "list_channel_plugins",
    "register_channel_plugin",
]
