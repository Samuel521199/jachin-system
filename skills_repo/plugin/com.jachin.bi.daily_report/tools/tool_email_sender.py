"""
邮件推送工具 — mcp:atom_email_sender

使用 l3_node.channels.email 通道层实现。
"""
from __future__ import annotations

from typing import Any

from l3_node.channels.email.smtp import send_email_with_attachment


def atom_email_sender(
    smtp_config: dict | None = None,
    to_addrs: list | None = None,
    subject: str = "",
    body: str = "",
    attachment_paths: list | None = None,
) -> dict[str, Any]:
    """MCP 接口：发送带附件的邮件。"""
    return send_email_with_attachment(
        smtp_config=smtp_config or {},
        to_addrs=to_addrs or [],
        subject=subject,
        body=body,
        attachment_paths=attachment_paths or [],
    )
