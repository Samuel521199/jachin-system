"""
PMO 飞书发送 — 复用 BI 的 atom_lark_notifier（不要复制实现）

本模块**不注册为新 MCP**。PMO Skill / Agent 应直接调用：

    mcp:atom_lark_notifier  →  l3_node.primitives.mcp.mcp_tools.bi.tool_lark_notifier.send_lark_markdown

原因：
- `tool_lark_notifier.py` 仅为通用 Markdown / 卡片投递，无 BI 业务逻辑；
- 凭证统一走 `config/mcps/atom_lark_notifier/config.yaml`（或 ~/.jachin 下同名）。

若 PMO 需独立机器人，请复制一份 config 目录并改 app_id/chat_id，无需 fork 代码。

K11 战报（VChart 图表交互卡片）走 ``l3_node.channels.lark.im.send_interactive_card``，由 ``tool_data_visualizer.send_pmo_k11_battle_report_card`` 调用，凭证来源同上。
"""

from __future__ import annotations

# 便于静态分析与文档生成时找到符号
def get_send_lark_markdown():
    from l3_node.primitives.mcp.mcp_tools.bi.tool_lark_notifier import send_lark_markdown

    return send_lark_markdown
