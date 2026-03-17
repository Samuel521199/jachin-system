"""
Email 通道插件 — ChannelPlugin 实现
"""
from __future__ import annotations

from typing import Any


class EmailOutbound:
    """SMTP 出站适配器"""

    def send_text(
        self,
        target: str,
        text: str,
        *,
        smtp_config: dict[str, Any] | None = None,
        to_addrs: list[str] | None = None,
        subject: str = "",
        attachment_paths: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        from l3_node.channels.email.smtp import send_email_with_attachment

        recipients = to_addrs if to_addrs is not None else ([target] if target else [])
        return send_email_with_attachment(
            smtp_config=smtp_config or {},
            to_addrs=recipients,
            subject=subject or "(无主题)",
            body=text,
            attachment_paths=attachment_paths or [],
        )


class EmailChannelPlugin:
    """Email/SMTP 通道插件"""

    id = "email"
    meta = {
        "label": "Email",
        "order": 80,
    }
    outbound = EmailOutbound()
