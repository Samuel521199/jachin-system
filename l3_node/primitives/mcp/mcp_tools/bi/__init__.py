"""
BI 战报 MCP 工具包

BI 每日战报相关：MCP 工具 + 数据层（paths、data_store、metrics）。
设计规范: docs/bi_daily_report/

  - mcp:atom_web_scraper   (tool_web_scraper)
  - mcp:atom_lark_notifier (tool_lark_notifier) -> channels.lark
  - mcp:atom_bi_project_context (tool_bi_project_context) -> Lark Wiki/多维表/文档同步
  - mcp:atom_email_sender  (tool_email_sender)   -> channels.email
  - paths, data_store, metrics（数据层，供 Skill 调用）
"""
