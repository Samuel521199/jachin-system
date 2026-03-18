# 每日 BI 深度分析战报 — 文档中心

本目录为 **BI 每日战报 Skill** 的专属文档区，与项目其他文档保持独立。

**代码归属**：BI 相关代码统一放在 `l3_node/mcp_tools/bi/`（MCP 与数据层）和 `l3_node/skills/bi/`（Skill 与调度），后续新增 BI 文件也需遵循此规范，并在 [08_BI_MCP_AND_SKILL_LAYOUT.md](./08_BI_MCP_AND_SKILL_LAYOUT.md) 中登记。

---

## 文档索引

| 文档 | 说明 |
|------|------|
| [01_PARALLEL_DEVELOPMENT_GUIDE.md](./01_PARALLEL_DEVELOPMENT_GUIDE.md) | **多兵种协同作战指南** — 契约、任务分发、技能调用规则 |
| [02_PARALLEL_DEVELOPMENT_ANALYSIS.md](./02_PARALLEL_DEVELOPMENT_ANALYSIS.md) | **深度分析与风险控制** — 冲突、流程、规范 |
| [03_SKILL_DESIGN.md](./03_SKILL_DESIGN.md) | **Skill 设计文档** — 接口、参数、流程 |
| [04_WHITEPAPER.md](./04_WHITEPAPER.md) | **白皮书** — 业务价值、架构定位、部署配置 |
| [05_DATA_CAPTURE_GUIDE.md](./05_DATA_CAPTURE_GUIDE.md) | **数据抓取使用指南** — SPA/API 模式、Chrome 调试、MCP 调用 |
| [06_DATA_STORE_D.md](./06_DATA_STORE_D.md) | **DuckDB 数据层设计** — ingest、schema、查询 |
| [07_BI_METRICS_PLUGINS.md](./07_BI_METRICS_PLUGINS.md) | **指标插件开发指南** — DataSource、Outputter |
| [08_BI_MCP_AND_SKILL_LAYOUT.md](./08_BI_MCP_AND_SKILL_LAYOUT.md) | **MCP 与 Skill 目录布局** — 归属规范、新增文件约定 |
| [09_SPA_SCRAPER_PLACEMENT_ANALYSIS.md](./09_SPA_SCRAPER_PLACEMENT_ANALYSIS.md) | **run_bi_scraper_spa 归属分析** — spa_collector 集成、collect_mode |
| [10_BI_PLUGIN_TEST_GUIDE.md](./10_BI_PLUGIN_TEST_GUIDE.md) | **BI 日报插件完整测试指引** — 抓取、提纯、Lark 同步、常见问题 |
| [11_LARK_TABLE_SCHEMA.md](./11_LARK_TABLE_SCHEMA.md) | **Lark 多维表格字段结构规范** — 各子表字段名、类型、常见错误速查 |
| [12_PRODUCT_REQUIREMENTS.md](./12_PRODUCT_REQUIREMENTS.md) | **BI 数据日报产品需求** — 日报接收人、用户活跃、留存、消耗、充值需求 |

---

## 推荐阅读顺序

1. **04_WHITEPAPER.md** — 了解业务价值与架构定位
2. **03_SKILL_DESIGN.md** — 掌握详细设计规范
3. **08_BI_MCP_AND_SKILL_LAYOUT.md** — 了解 MCP/Skill 目录归属与新增文件约定
4. **01_PARALLEL_DEVELOPMENT_GUIDE.md** — 多开发者协作指南
5. **02_PARALLEL_DEVELOPMENT_ANALYSIS.md** — 风险与流程深度分析

---

## 协同开发就绪检查清单（A/B 可开工）

| 序号 | 检查项 | 状态 |
|------|--------|------|
| 1 | `l3_node/mcp_tools/bi/` 目录及 `__init__.py` | ✅ |
| 2 | `bi/tool_web_scraper.py` 占位 stub（A 替换实现） | ✅ |
| 3 | `tool_broadcaster.py` 占位 stub（B 替换实现） | ✅ |
| 4 | `l3_node.mcp_tools.bi.paths` 路径常量 | ✅ |
| 5 | `mcp_registry` 中 BI 工具注册与路由 | ✅ |
| 6 | `config/bi_daily_report.yaml.example` | ✅ |
| 7 | `l3_node/requirements-bi.txt`（A 需 beautifulsoup4） | ✅ |
| 8 | `scripts/test_bi_mcp_contract.py` 契约验收 | ✅ |

**A 分支**: `feat/bi-scraper`，仅修改 `bi/tool_web_scraper.py`  
**B 分支**: `feat/bi-broadcaster`，仅修改 `tool_broadcaster.py`

---

## 外部规范引用

- [MCP_SPEC.md](../MCP_SPEC.md) — MCP 接入规范
- [SKILL_MD_SPEC.md](../SKILL_MD_SPEC.md) — Skill 声明式规范
- [whitepaper/07_LAYER3_TERMINAL.md](../whitepaper/07_LAYER3_TERMINAL.md) — Layer 3 单体架构
