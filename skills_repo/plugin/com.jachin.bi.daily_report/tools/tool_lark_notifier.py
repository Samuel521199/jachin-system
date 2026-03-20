"""
飞书推送工具 — mcp:atom_lark_notifier

使用 l3_node.channels.lark 通道层实现。
"""
from __future__ import annotations

from typing import Any

from l3_node.channels.lark import send_markdown


def atom_lark_notifier(
    webhook_url: str = "",
    markdown_content: str = "",
    title: str = "",
) -> dict[str, Any]:
    """MCP 接口：通过飞书 Webhook 发送 Markdown 消息。"""
    return send_markdown(
        webhook_url=webhook_url,
        markdown_content=markdown_content,
        title=title or None,
    )
