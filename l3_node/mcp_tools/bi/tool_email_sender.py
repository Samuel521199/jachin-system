"""
邮件推送工具 — mcp:atom_email_sender

契约: docs/bi_daily_report/01_PARALLEL_DEVELOPMENT_GUIDE.md
使用 l3_node.channels.email 通道层实现。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from l3_node.channels.email.smtp import send_email_with_attachment

# 在此填写，保存一次即可，无需每次设置环境变量（环境变量优先覆盖）
BI_SMTP_USER = "your_email@qq.com"
BI_SMTP_PASSWORD = "your_smtp_auth_code"
BI_SMTP_TO = "vivian@herontech.net"

if __name__ == "__main__":
    smtp_user = os.environ.get("BI_SMTP_USER") or BI_SMTP_USER
    smtp_pass = os.environ.get("BI_SMTP_PASSWORD") or BI_SMTP_PASSWORD
    to_addr = os.environ.get("BI_SMTP_TO") or BI_SMTP_TO
    # QQ 邮箱：587+STARTTLS 比 465 更稳定，部分网络 465 会被关闭
    smtp_config = (
        {"host": "smtp.qq.com", "port": 587, "user": smtp_user, "password": smtp_pass}
        if smtp_user and smtp_pass
        else {"host": "smtp.example.com", "user": "x", "password": "x"}
    )
    if not smtp_user or not smtp_pass:
        print("提示: 在 tool_email_sender.py 中修改 BI_SMTP_USER、BI_SMTP_PASSWORD 为真实值")
    r = send_email_with_attachment(
        smtp_config,
        [to_addr],
        "BI 战报邮件测试 — tool_email_sender",
        "<p>本邮件由 <b>tool_email_sender</b> (mcp:atom_email_sender) 发送，用于验证邮件通道。</p><p>契约测试通过 ✓</p>",
        [],
    )
    print("email:", r)
