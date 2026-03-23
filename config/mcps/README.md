# MCP 配置

规范: `.cursor/rules/075-config-root-and-cloud-sync.mdc`

**读取顺序**：优先 `~/.jachin/config/mcps/`，若不存在则从本目录读取（团队共享）。

## 团队共享

- `config.yaml`：真实凭证，已加入 .gitignore，**不提交**
- `config.yaml.example`：占位符模板，**可提交**，同事复制为 `config.yaml` 后填入

初始化：`python scripts/init_jachin_mcp_config.py`（将 .example 复制到 ~/.jachin）

## 目录结构

```
config/mcps/
├── atom_web_scraper/
├── atom_bi_metrics/
├── atom_lark_notifier/
│   ├── config.yaml         # 团队共享（gitignore）
│   └── config.yaml.example # 模板（提交）
├── atom_email_sender/
│   ├── config.yaml         # 团队共享（gitignore）
│   └── config.yaml.example # 模板（提交）
└── local-hr-fs/
```
