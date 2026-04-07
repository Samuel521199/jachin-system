"""
PMO/BMO 插件 — L3 本地 MCP 工具

- mcp:atom_pmo_lark_doc — Lark 知识库/Wiki 拉取与节点浏览
- mcp:atom_pmo_knowledge_base — 文档分块、向量化、落盘 corpus
- tool_data_visualizer — PMO 仪表盘 VChart 交互卡片（飞书 Chart 组件，无静态图上传）

飞书发送复用 mcp:atom_lark_notifier（见 lark_notifier_bridge 说明）。
"""

from l3_node.primitives.mcp.mcp_tools.pmo_bmo.tool_lark_doc import run_pmo_lark_doc
from l3_node.primitives.mcp.mcp_tools.pmo_bmo.tool_knowledge_base import run_pmo_knowledge_base
from l3_node.primitives.mcp.mcp_tools.pmo_bmo.tool_pmo_bitable_export import run_pmo_bitable_export
from l3_node.primitives.mcp.mcp_tools.pmo_bmo.tool_data_visualizer import (
    build_k11_battle_report_card,
    build_pmo_chart_data_from_csv,
    build_vchart_bar_top10_spec,
    build_vchart_pie_spec,
    run_data_visualizer,
    send_pmo_k11_battle_report_card,
    send_pmo_three_dashboard_cards,
)

__all__ = [
    "run_pmo_lark_doc",
    "run_pmo_knowledge_base",
    "run_pmo_bitable_export",
    "build_pmo_chart_data_from_csv",
    "build_vchart_pie_spec",
    "build_vchart_bar_top10_spec",
    "build_k11_battle_report_card",
    "send_pmo_k11_battle_report_card",
    "send_pmo_three_dashboard_cards",
    "run_data_visualizer",
]
