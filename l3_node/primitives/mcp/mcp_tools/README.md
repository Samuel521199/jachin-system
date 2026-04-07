# BI 战报 MCP 工具 — 开发者指南

本目录为 A、B 协同开发区域。**禁止修改 mcp_registry.py**。

## 分工

| 开发者 | 文件 | MCP ID |
|--------|------|--------|
| **A** | `bi/tool_web_scraper.py` | mcp:atom_web_scraper |
| **B** | `bi/tool_lark_notifier.py` | mcp:atom_lark_notifier |
| **B** | `bi/tool_email_sender.py` | mcp:atom_email_sender |
| **BI** | `bi/tool_bi_project_context.py` | mcp:atom_bi_project_context |
| **PMO/BMO** | `pmo_bmo/tool_lark_doc.py` | mcp:atom_pmo_lark_doc |
| **PMO/BMO** | `pmo_bmo/tool_knowledge_base.py` | mcp:atom_pmo_knowledge_base |

飞书发送仍使用 `bi/tool_lark_notifier.py`（mcp:atom_lark_notifier）；PMO 目录内 `pmo_bmo/lark_notifier_bridge.py` 仅作说明与便捷引用。

## 依赖

```bash
pip install -r l3_node/requirements-bi.txt   # 开发者 A 需 beautifulsoup4
```

## 契约验收

完成实现后运行：

```bash
python scripts/test_bi_mcp_contract.py
```

全部 PASS 后再提交 PR。

## 路径

- 抓取输出: `l3_node.mcp_tools.bi.paths.get_bi_raw_dir()`
- 设计文档: `docs/bi_daily_report/`
- 目录布局与归属规范: [docs/bi_daily_report/08_BI_MCP_AND_SKILL_LAYOUT.md](../docs/bi_daily_report/08_BI_MCP_AND_SKILL_LAYOUT.md)
- 通道层: `l3_node.channels`（Lark/Email 实现抽离，支持多通道扩展）

## 生命周期与审批流程

本目录 MCP 为 **L3 本地执行**，开发期可直接使用，无需 L1/L2。  
若需走「上传→L1 审核→L2 下载→L3」完整流程，需扩展实现。详见 [MCP_LIFECYCLE_AND_APPROVAL_FLOW.md](./MCP_LIFECYCLE_AND_APPROVAL_FLOW.md)。
