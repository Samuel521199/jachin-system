# BI 战报 MCP 工具 — 开发者指南

本目录为 A、B 协同开发区域。**禁止修改 mcp_registry.py**。

## 分工

| 开发者 | 文件 | MCP ID |
|--------|------|--------|
| **A** | `tool_web_scraper.py` | mcp:atom_web_scraper |
| **B** | `tool_lark_notifier.py` | mcp:atom_lark_notifier |
| **B** | `tool_email_sender.py` | mcp:atom_email_sender |

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
- 通道层: `l3_node.channels`（Lark/Email 实现抽离，支持多通道扩展）

## 配置（规范 075，支持团队共享）

**读取顺序**：优先 `~/.jachin/config/mcps/`，若不存在则回退到项目 `config/mcps/`。

| MCP | 配置路径 | 说明 |
|-----|----------|------|
| atom_lark_notifier | `config/mcps/atom_lark_notifier/config.yaml` | default_webhook_url（已 .gitignore） |
| atom_email_sender | `config/mcps/atom_email_sender/config.yaml` | smtp、default_to_addrs（已 .gitignore） |

**团队共享**：项目内 `config.yaml` 含真实凭证，已加入 .gitignore 不提交。同事拉取后复制 `config.yaml.example` 为 `config.yaml` 并填入，或运行 `python scripts/init_jachin_mcp_config.py`。

## 生命周期与审批流程

本目录 MCP 为 **L3 本地执行**，开发期可直接使用，无需 L1/L2。  
若需走「上传→L1 审核→L2 下载→L3」完整流程，需扩展实现。详见 [MCP_LIFECYCLE_AND_APPROVAL_FLOW.md](./MCP_LIFECYCLE_AND_APPROVAL_FLOW.md)。
