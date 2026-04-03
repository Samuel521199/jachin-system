# BI 模块目录布局 — MCP 与 Skill 归属规范

**版本**: 1.0  
**更新日期**: 2026-03  
**定位**: BI 相关代码统一归属 `mcp_tools/bi/` 与 `skills/bi/`，不再单独建 `l3_node/bi/`

---

## 一、设计原则

| 原则 | 说明 |
|------|------|
| **MCP 归属** | BI 的 MCP 工具、数据层（paths、data_store、metrics）统一放在 `l3_node/primitives/mcp/mcp_tools/bi/` |
| **Skill 归属** | BI 的 Skill、调度逻辑统一放在 `l3_node/primitives/skills/bi/` |
| **文档集中** | 所有 BI 相关设计、契约、使用说明均放在 `docs/bi_daily_report/` |
| **新增规范** | 后续新增 BI 相关文件必须放入上述目录，并在此文档中登记 |

---

## 二、MCP 层：`l3_node/primitives/mcp/mcp_tools/bi/`

### 2.1 目录结构

```
l3_node/primitives/mcp/mcp_tools/bi/
├── __init__.py              # 模块说明
├── paths.py                 # 路径常量：get_bi_raw_dir, ensure_bi_dirs, get_bi_duckdb_path
├── data_store.py            # DuckDB 存储：ingest_csv, get_table, list_available_slugs 等
├── metrics/                 # 指标引擎
│   ├── __init__.py
│   ├── engine.py            # 执行引擎：run()
│   ├── registry.py          # 插件注册
│   └── plugins/
│       ├── __init__.py      # 内置插件注册
│       ├── base.py          # DataSource / Outputter 基类
│       ├── data_source_duckdb.py
│       ├── output_console.py
│       └── output_markdown.py
├── spa_collector.py         # BI 批量 SPA 抓取（供 scripts + main_skill 复用）
├── tool_web_scraper.py      # mcp:atom_web_scraper
├── tool_lark_notifier.py    # mcp:atom_lark_notifier
├── tool_email_sender.py    # mcp:atom_email_sender
└── tool_bi_project_context.py  # mcp:atom_bi_project_context
```

### 2.2 文件职责

| 文件 | 职责 | 导入路径 |
|------|------|----------|
| `spa_collector.py` | BI 后台批量 SPA 抓取，MENU_ITEMS、run_full_spa_collect | `l3_node.mcp_tools.bi.spa_collector` |
| `paths.py` | BI 数据目录、DuckDB 路径、ensure_bi_dirs | `l3_node.mcp_tools.bi.paths` |
| `data_store.py` | CSV 导入、表查询、schema 管理 | `l3_node.mcp_tools.bi.data_store` |
| `metrics/` | 指标配置加载、插件调度、同环比计算 | `l3_node.mcp_tools.bi.metrics` |
| `tool_web_scraper.py` | 网页/表格抓取，输出 CSV/JSON | MCP 路由到 `harvest_table_data` |
| `tool_lark_notifier.py` | 飞书 webhook 推送 | MCP 路由到 `send_lark_markdown` |
| `tool_email_sender.py` | SMTP 邮件发送 | MCP 路由到 `send_email` |
| `tool_bi_project_context.py` | Lark 知识库项目文档同步至 `docs/bi_daily_report/bi_project/` | MCP 路由到 `sync_bi_project_context` |

### 2.3 MCP 工具注册（mcp_registry）

| MCP ID | 实现模块 |
|--------|----------|
| `mcp:atom_web_scraper` | `l3_node.mcp_tools.bi.tool_web_scraper` |
| `mcp:atom_lark_notifier` | `l3_node.mcp_tools.bi.tool_lark_notifier` |
| `mcp:atom_email_sender` | `l3_node.mcp_tools.bi.tool_email_sender` |
| `mcp:atom_bi_project_context` | `l3_node.mcp_tools.bi.tool_bi_project_context` |

---

## 三、Skill 层：`l3_node/primitives/skills/bi/`

### 3.1 目录结构

```
l3_node/primitives/skills/bi/
├── __init__.py              # 模块说明
├── scheduler.py             # 定时调度：register_bi_daily_report_job
└── bi_daily_report/
    ├── __init__.py
    └── main_skill.py        # run_bi_daily_report()
```

### 3.2 文件职责

| 文件 | 职责 | 导入路径 |
|------|------|----------|
| `scheduler.py` | 注册 APScheduler 任务，按 config 执行 cron/interval | `l3_node.primitives.skills.bi.scheduler` |
| `bi_daily_report/main_skill.py` | 四步流程：收集→对比→LLM→分发 | `l3_node.primitives.skills.bi.bi_daily_report.main_skill` |

### 3.3 调度挂载

在 `l3_node/http_server.py` 启动时：

```python
from l3_node.primitives.skills.bi.scheduler import register_bi_daily_report_job
register_bi_daily_report_job()
```

---

## 四、文档归属：`docs/bi_daily_report/`

| 文档 | 说明 |
|------|------|
| [README.md](./README.md) | 文档索引、检查清单 |
| [01_PARALLEL_DEVELOPMENT_GUIDE.md](./01_PARALLEL_DEVELOPMENT_GUIDE.md) | 多兵种协同、契约、任务分发 |
| [02_PARALLEL_DEVELOPMENT_ANALYSIS.md](./02_PARALLEL_DEVELOPMENT_ANALYSIS.md) | 深度分析与风险控制 |
| [03_SKILL_DESIGN.md](./03_SKILL_DESIGN.md) | Skill 设计、MCP 参数 Schema |
| [04_WHITEPAPER.md](./04_WHITEPAPER.md) | 白皮书、业务价值、架构定位 |
| [05_DATA_CAPTURE_GUIDE.md](./05_DATA_CAPTURE_GUIDE.md) | 数据抓取、SPA/API 模式 |
| [06_DATA_STORE_D.md](./06_DATA_STORE_D.md) | DuckDB 数据层设计 |
| [07_BI_METRICS_PLUGINS.md](./07_BI_METRICS_PLUGINS.md) | 指标插件开发指南 |
| [08_BI_MCP_AND_SKILL_LAYOUT.md](./08_BI_MCP_AND_SKILL_LAYOUT.md) | **本文档** — 目录布局与归属规范 |
| [09_SPA_SCRAPER_PLACEMENT_ANALYSIS.md](./09_SPA_SCRAPER_PLACEMENT_ANALYSIS.md) | run_bi_scraper_spa 归属分析、spa_collector 集成方案 |
| [STRATEGIC_REPORT_ANALYSIS_SPEC.md](./STRATEGIC_REPORT_ANALYSIS_SPEC.md) | **大战报（Step 3.5）** v4.2 SSOT；文末「战报输出美学」由 `main_skill` 截取并注入 System 追加节 |
| [bi_project/](./bi_project/) | K11/项目背景等 Markdown，注入大战报 User 上下文 |

---

## 五、新增 BI 文件规范

后续新增 BI 相关功能时，请遵循：

1. **MCP 工具** → 放入 `l3_node/primitives/mcp/mcp_tools/bi/`，在 `mcp_registry` 中注册，并在本文档 2.1 节补充文件说明
2. **Skill 逻辑** → 放入 `l3_node/primitives/skills/bi/`（可新建子目录如 `bi_xxx/`），并在本文档 3.1 节补充
3. **设计/契约文档** → 放入 `docs/bi_daily_report/`，在 README 索引中登记
4. **配置** → 放入 `config/` 或 `~/.jachin/config/skills/com.jachin.bi.*/`

---

## 六、外部引用

- [MCP_SPEC.md](../MCP_SPEC.md) — MCP 接入规范
- [SKILL_MD_SPEC.md](../SKILL_MD_SPEC.md) — Skill 声明式规范
- [l3_node/primitives/mcp/mcp_tools/README.md](../../l3_node/primitives/mcp/mcp_tools/README.md) — MCP 工具目录说明
