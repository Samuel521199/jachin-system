"""
通用广播工具 — mcp:atom_lark_notifier、mcp:atom_email_sender

契约: docs/bi_daily_report/01_PARALLEL_DEVELOPMENT_GUIDE.md
开发者 B 负责实现，本文件为占位 stub。
"""
from __future__ import annotations

from typing import Any


def send_lark_markdown(
    webhook_url: str,
    markdown_content: str,
    title: str | None = None,
) -> dict[str, Any]:
    """
    通过飞书 Webhook 发送 Markdown 消息。

    Returns:
        {"status": "success", "msg": "飞书已送达"} 或 {"status": "error", "error": "..."}
    """
    # TODO: 开发者 B 实现 — HTTP POST 飞书 Webhook
    return {
        "status": "error",
        "error": "[STUB] send_lark_markdown 占位实现，待开发者 B 完成",
    }


def send_email_with_attachment(
    smtp_config: dict[str, Any],
    to_addrs: list[str],
    subject: str,
    body: str,
    attachment_paths: list[str] | None = None,
) -> dict[str, Any]:
    """
    发送带附件的邮件。

    Args:
        smtp_config: {host, port, user, password}
        to_addrs: 收件人列表
        subject: 主题
        body: 正文（HTML 或纯文本）
        attachment_paths: 附件路径列表

    Returns:
        {"status": "success", "msg": "邮件已发送"} 或 {"status": "error", "error": "..."}
    """
    # TODO: 开发者 B 实现 — smtplib
    return {
        "status": "error",
        "error": "[STUB] send_email_with_attachment 占位实现，待开发者 B 完成",
    }


if __name__ == "__main__":
    # 开发者 B 本地测试入口
    r1 = send_lark_markdown("https://open.feishu.cn/...", "# test")
    r2 = send_email_with_attachment(
        {"host": "smtp.example.com", "user": "x", "password": "x"},
        ["a@b.com"],
        "test",
        "body",
    )
    print("lark:", r1)
    print("email:", r2)
