"""
飞书推送工具 — mcp:atom_lark_notifier

契约: docs/bi_daily_report/01_PARALLEL_DEVELOPMENT_GUIDE.md
使用 l3_node.channels.lark 通道层实现。
配置: ~/.jachin/config/mcps/atom_lark_notifier/config.yaml（规范 075）
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from l3_node.channels.lark import send_markdown
from l3_node.jachin_config import load_mcp_config


def _get_default_webhook_url() -> str:
    """从 ~/.jachin 或项目 config/ 读取默认 Webhook URL（团队共享）"""
    _proj = Path(__file__).resolve().parent.parent.parent
    cfg = load_mcp_config("atom_lark_notifier", project_root=_proj)
    return (cfg.get("default_webhook_url") or "").strip()


def send_lark_markdown(
    webhook_url: str,
    markdown_content: str,
    title: str | None = None,
    chart_spec: dict | None = None,
) -> dict[str, Any]:
    """
    通过飞书 Webhook 发送 Markdown 消息，可选附带统计图（Schema 2.0 图表组件）。

    Args:
        webhook_url: 飞书机器人 Webhook URL
        markdown_content: Markdown 正文
        title: 卡片标题（可选）
        chart_spec: 图表配置（可选），VChart 格式，如漏斗图、柱状图等。

    Returns:
        {"status": "success", "msg": "飞书已送达"} 或 {"status": "error", "error": "..."}
    """
    return send_markdown(
        webhook_url=webhook_url,
        markdown_content=markdown_content,
        title=title,
        chart_spec=chart_spec,
    )


if __name__ == "__main__":
    webhook_url = _get_default_webhook_url()
    if not webhook_url or webhook_url.startswith("${"):
        print("提示: 在 ~/.jachin/config/mcps/atom_lark_notifier/config.yaml 中配置 default_webhook_url")
        print("      或设置环境变量 BI_LARK_WEBHOOK_URL")
        webhook_url = ""

    SAMPLE_MD = """# 📊 每日 BI 深度分析战报 — Lark 通道测试

本消息由 **tool_lark_notifier** (mcp:atom_lark_notifier) 发送。
"""
    if webhook_url:
        r1 = send_lark_markdown(webhook_url, SAMPLE_MD, title="BI 战报 Lark 测试")
        print("lark (纯文):", r1)
    else:
        print("lark: 未配置 webhook_url，跳过发送")
