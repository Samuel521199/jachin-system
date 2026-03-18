# 配置目录（规范 075）

配置仅分 `skills/` 与 `mcps/` 两类，与 `.cursor/rules/075-config-root-and-cloud-sync(1).mdc` 完全一致。

```
config/
├── skills/               # 按技能隔离
│   ├── com.jachin.bi.daily_report/
│   │   └── bi_daily_report.yaml
│   └── com.jachin.hr.analyzer4/
│       └── hr_jds/       # 该技能用的 JD 模板
│           └── *.md
└── mcps/                 # 按 MCP 隔离
    ├── atom_web_scraper/
    ├── atom_bi_metrics/
    ├── atom_lark_notifier/
    ├── atom_email_sender/
    └── local-hr-fs/
```

**L1/L2 配置**（如 cluster.yaml、skills_config）已移至 `core/config/`，随代码就近存放。

**运行时**：实际配置写入 `~/.jachin/config/`，可用 `python scripts/init_jachin_mcp_config.py` 初始化。
