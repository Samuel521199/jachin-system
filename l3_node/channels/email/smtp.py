"""
Email 通道 — SMTP 邮件发送
"""
from __future__ import annotations

import ssl
import smtplib
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from pathlib import Path
from typing import Any


def _normalize_smtp_config(cfg: dict[str, Any]) -> tuple[str, int, str, str]:
    """从 smtp_config 提取 host, port, user, password。"""
    host = (cfg.get("host") or cfg.get("smtp_host") or "").strip()
    port_raw = cfg.get("port") or cfg.get("smtp_port") or 465
    port = int(port_raw) if port_raw is not None else 465
    user = (cfg.get("user") or cfg.get("smtp_user") or "").strip()
    password = (cfg.get("password") or cfg.get("smtp_password") or "").strip()
    return host, port, user, password


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
    attachment_paths = attachment_paths or []
    try:
        host, port, user, password = _normalize_smtp_config(smtp_config or {})
        if not host:
            return {"status": "error", "error": "smtp_config 缺少 host/smtp_host"}
        if not user:
            return {"status": "error", "error": "smtp_config 缺少 user/smtp_user"}
        if not password:
            return {"status": "error", "error": "smtp_config 缺少 password/smtp_password"}
        if not to_addrs:
            return {"status": "error", "error": "to_addrs 不能为空"}
        to_list = [a.strip() for a in to_addrs if (a or "").strip()]
        if not to_list:
            return {"status": "error", "error": "to_addrs 无有效收件人"}

        msg = MIMEMultipart()
        msg["From"] = user
        msg["To"] = ", ".join(to_list)
        msg["Subject"] = subject or "(无主题)"

        is_html = "<" in (body or "") and ">" in (body or "")
        msg.attach(MIMEText(body or "", "html" if is_html else "plain", "utf-8"))

        for path in attachment_paths:
            path_str = (path or "").strip()
            if not path_str:
                continue
            p = Path(path_str)
            if not p.exists() or not p.is_file():
                return {"status": "error", "error": f"附件不存在或非文件: {path_str}"}
            with open(p, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=p.name)
            msg.attach(part)

        if port == 465:
            ctx = ssl.create_default_context()
            server = smtplib.SMTP_SSL(host, port, context=ctx, timeout=15)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()

        server.login(user, password)
        server.sendmail(user, to_list, msg.as_string())
        server.quit()
        return {"status": "success", "msg": "邮件已发送"}
    except smtplib.SMTPAuthenticationError as e:
        return {"status": "error", "error": f"SMTP 认证失败: {e}"}
    except smtplib.SMTPRecipientsRefused as e:
        return {"status": "error", "error": f"收件人被拒: {e}"}
    except smtplib.SMTPException as e:
        return {"status": "error", "error": f"SMTP 错误: {e}"}
    except OSError as e:
        return {"status": "error", "error": f"网络/IO 错误: {e}"}
    except Exception as e:
        return {"status": "error", "error": str(e)}
