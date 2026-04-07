"""
邮件推送工具 — mcp:atom_email_sender

契约: docs/bi_daily_report/01_PARALLEL_DEVELOPMENT_GUIDE.md
使用 l3_node.channels.email 通道层实现。
配置: ~/.jachin/config/mcps/atom_email_sender/config.yaml（规范 075）
"""
from __future__ import annotations

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from l3_node.channels.email.smtp import send_email_with_attachment
from l3_node.jachin_config import load_mcp_config


def _get_default_smtp_config() -> tuple[dict[str, object], list[str]]:
    """从 ~/.jachin 或项目 config/ 读取 SMTP 与默认收件人（团队共享）"""
    _proj = Path(__file__).resolve().parent.parent.parent
    cfg = load_mcp_config("atom_email_sender", project_root=_proj)
    smtp = cfg.get("smtp") or {}
    if isinstance(smtp, dict):
        smtp_config = {
            "host": smtp.get("host") or "smtp.qq.com",
            "port": int(smtp.get("port") or 587),
            "user": (smtp.get("user") or "").strip(),
            "password": (smtp.get("password") or "").strip(),
        }
    else:
        smtp_config = {"host": "smtp.example.com", "user": "", "password": ""}
    to_addrs = cfg.get("default_to_addrs") or []
    if isinstance(to_addrs, list):
        to_list = [str(a).strip() for a in to_addrs if str(a).strip()]
    else:
        to_list = []
    return smtp_config, to_list


if __name__ == "__main__":
    smtp_config, to_addrs = _get_default_smtp_config()
    user = (smtp_config.get("user") or "").strip()
    pwd = (smtp_config.get("password") or "").strip()
    if not user or not pwd or user.startswith("${") or pwd.startswith("${"):
        print("提示: 在 ~/.jachin/config/mcps/atom_email_sender/config.yaml 中配置 smtp.user、smtp.password")
        print("      或设置环境变量 BI_SMTP_USER、BI_SMTP_PASSWORD")
    if not to_addrs or (to_addrs and str(to_addrs[0]).startswith("${")):
        to_addrs = ["vivian@herontech.net"]  # 兜底，避免空列表
    if user and pwd and not str(user).startswith("${"):
        r = send_email_with_attachment(
            smtp_config,
            to_addrs,
            "BI 战报邮件测试 — tool_email_sender",
            "<p>本邮件由 <b>tool_email_sender</b> (mcp:atom_email_sender) 发送，用于验证邮件通道。</p><p>契约测试通过 ✓</p>",
            [],
        )
        print("email:", r)
    else:
        print("email: 未配置 SMTP 凭证，跳过发送")
