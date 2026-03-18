"""
飞书推送工具 — mcp:atom_lark_notifier

契约: docs/bi_daily_report/01_PARALLEL_DEVELOPMENT_GUIDE.md
使用 l3_node.channels.lark 通道层实现。
支持两种模式：Webhook URL（群自定义机器人）或 chat_id + App 凭证（应用机器人）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_root = Path(__file__).resolve().parent.parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from l3_node.channels.lark import send_markdown
from l3_node.channels.lark.im import send_markdown_card


def send_lark_markdown(
    webhook_url: str,
    markdown_content: str,
    title: str | None = None,
    chart_spec: dict | None = None,
    chat_id: str | None = None,
) -> dict[str, Any]:
    """
    通过飞书发送 Markdown 消息。
    优先 Webhook URL；若无有效 Webhook（空或含占位符），则用 chat_id + App 凭证（IM API）。

    Args:
        webhook_url: 飞书机器人 Webhook URL，或空
        markdown_content: Markdown 正文
        title: 卡片标题（可选）
        chart_spec: 图表配置（可选），仅 Webhook 模式支持
        chat_id: 群 chat_id（如 oc_xxx），无 Webhook 时用于 IM API 推送

    Returns:
        {"status": "success", "msg": "飞书已送达"} 或 {"status": "error", "error": "..."}
    """
    # 有效 Webhook：非空且非占位符（不以 ${ 开头）
    has_webhook = (webhook_url or "").strip() and not str(webhook_url).strip().startswith("${")
    if has_webhook and not chart_spec:
        return send_markdown(
            webhook_url=webhook_url.strip(),
            markdown_content=markdown_content,
            title=title,
            chart_spec=chart_spec,
        )
    # 有 chat_id 时用 IM API（应用机器人需在群内且有发消息权限）
    _chat_id = (chat_id or "").strip()
    if _chat_id:
        return send_markdown_card(
            receive_id=_chat_id,
            markdown_content=markdown_content,
            title=title,
            receive_id_type="chat_id",
        )
    if has_webhook and chart_spec:
        return send_markdown(
            webhook_url=webhook_url.strip(),
            markdown_content=markdown_content,
            title=title,
            chart_spec=chart_spec,
        )
    return {"status": "error", "error": "请配置 BI_LARK_WEBHOOK_URL 或 BI_LARK_CHAT_ID"}


if __name__ == "__main__":
    # 与 config/skills/com.jachin.bi.daily_report/bi_daily_report.yaml 保持一致
    WEBHOOK_URL = "https://open.larksuite.com/open-apis/bot/v2/hook/bdb86a38-6ce7-4bb3-ae42-3f6c0f7535ac"

    SAMPLE_MD = """# 📊 每日 BI 深度分析战报 — Lark 通道测试

本消息由 **tool_lark_notifier** (mcp:atom_lark_notifier) 发送。
"""
    r1 = send_lark_markdown(WEBHOOK_URL, SAMPLE_MD, title="BI 战报 Lark 测试")
    print("lark (纯文):", r1)
