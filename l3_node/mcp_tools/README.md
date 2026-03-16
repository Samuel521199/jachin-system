# BI 战报 MCP 工具 — 开发者指南

本目录为 A、B 协同开发区域。**禁止修改 mcp_registry.py**。

## 分工

| 开发者 | 文件 | MCP ID |
|--------|------|--------|
| **A** | `tool_web_scraper.py` | mcp:atom_web_scraper |
| **B** | `tool_broadcaster.py` | mcp:atom_lark_notifier、mcp:atom_email_sender |

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

- 抓取输出: `l3_node.bi_paths.get_bi_raw_dir()`
- 设计文档: `docs/bi_daily_report/`
