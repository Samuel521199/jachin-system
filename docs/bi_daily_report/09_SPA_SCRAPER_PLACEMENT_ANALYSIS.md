# run_bi_scraper_spa 归属分析

## 一、现状

| 位置 | 文件 | 职责 |
|------|------|------|
| scripts/ | run_bi_scraper_spa.py | 批量抓取 BI 后台 30+ 张表，输出 raw/*.csv |
| l3_node/skills/bi/ | main_skill.py Step A | 单表抓取（调用 atom_web_scraper），可选 inline ingest |
| l3_node/mcp_tools/bi/ | tool_web_scraper.py | 通用 SPA/API 抓取，无 BI 业务知识 |

**典型流程**：`run_bi_scraper_spa` → `import_raw_to_duckdb`（手动/cron）→ `main_skill`（skip_collect=true 时直接用 DuckDB）

## 二、选项对比

### 选项 A：单独作为 MCP

| 维度 | 结论 |
|------|------|
| **通用性** | ❌ 强绑 bi-admin-web.heronpro.xin 菜单结构，非通用工具 |
| **耗时** | ❌ 30+ 表 sequential，单次 5–15 分钟，超出 MCP 常见响应时间 |
| **前置** | ❌ 需 Chrome debug 模式 + 手动登录，不适合无头 MCP 调用 |
| **复用** | ❌ 其他 Skill 几乎不会复用「BI 全表抓取」 |

**结论**：**不推荐**。不符合 MCP「通用、短耗时、可独立调用」的定位。

### 选项 B：集成到 main_skill 统筹

| 维度 | 结论 |
|------|------|
| **职责** | ✅ 数据收集是日报流程 Step A 的一部分 |
| **可配置** | ✅ 通过 collect_mode: single \| full_spa 区分单表/全表 |
| **统一入口** | ✅ 日报、定时任务、手动触发都走 main_skill |
| **执行时机** | ⚠️ full_spa 耗时长，适合独立定时（如 6:00）先跑，8:00 日报用 skip_collect |

**结论**：**推荐**。作为 main_skill Step A 的扩展模式，由配置控制。

## 三、最终方案

1. **提取核心逻辑** → `l3_node/mcp_tools/bi/spa_collector.py`
   - MENU_ITEMS、_build_leaf_actions、_discover_menu_items、run_full_spa_collect()

2. **scripts/run_bi_scraper_spa.py** → 薄封装
   - 调用 spa_collector.run_full_spa_collect()，保留 CLI/cron 用法

3. **main_skill Step A** → 支持 collect_mode
   - `single`（默认）：现有逻辑，单表 + atom_web_scraper
   - `full_spa`：调用 run_full_spa_collect()，可选 auto_ingest

4. **scripts 保留**：用于独立定时任务（如 6:00 全量抓取），main_skill 用于 8:00 日报时可直接使用 DuckDB。

## 四、配置扩展

```yaml
# bi_daily_report.yaml

# single: 单表抓取（现有逻辑，data_source 配置生效）
# full_spa: 批量抓取所有 MENU_ITEMS（需 Chrome 已登录）
collect_mode: single

# 仅当 collect_mode=full_spa 时，可限制 slugs（默认全部）
# full_spa_slugs: [daily_ops_summary, stats_retention_user, recharge_status]
```
