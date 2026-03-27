# BI 日报插件完整测试指引

## 一、前置环境

### 1. 依赖安装

```bash
pip install -r l3_node/requirements-bi.txt   # duckdb, requests, beautifulsoup4
pip install pyyaml                           # 配置解析
```

### 2.  Lark 多维表格权限

- 创建 Lark 应用，获取 `LARK_APP_ID`、`LARK_APP_SECRET`
- 为应用开通：`base:record:create`、`base:record:read`、`base:field:create`
- 多维表需对应用开放可编辑
- 配置环境变量或在 `~/.jachin/config/skills/com.jachin.bi.daily_report/` 下对应 `.env` 中设置

---

## 二、测试流程（按顺序）

### 步骤 1：抓取 BI 后台数据

**方式 A：批量抓取（推荐）**

1. 启动 Chrome 调试模式，并登录 BI 后台 `https://bi-admin-web.heronpro.xin`
2. 执行：

```bash
python scripts/run_bi_scraper_spa.py
```

3. 确认 `~/.jachin/client_volumes/bi_data/raw/` 下生成 `*.csv` 文件

**新增用户留存对比（`stats_retention_user_compare`）/ 付费留存对比**：抓取前会自动填入与 BI 周对比一致的双时间段——**时间段1** = 昨日往前共 7 天（含昨日），**时间段2** = 再往前 7 天（与「游戏数据统计对比」同一套日期算法）。若运行日为上文示例的次日，则对应 `2026-03-17~03-23` vs `2026-03-10~03-16`。

**方式 B：单表抓取**

- 配置 `bi_daily_report.yaml` 中 `skip_collect: false`、`data_source.url` 等
- 通过 main_skill 或 MCP 调用 `mcp:atom_web_scraper`

### 步骤 2：导入 DuckDB

```bash
python scripts/import_raw_to_duckdb.py
```

- 确认无报错，DuckDB 位于 `~/.jachin/client_volumes/bi_data/duckdb/bi.duckdb`

### 步骤 3：数据提纯

```bash
python scripts/run_bi_report_refiner.py
```

- 输出目录：`~/.jachin/client_volumes/bi_data/output/`
- 应生成 10 个 CSV，与 Lark 多维表格侧栏一一对应

### 步骤 4：同步到 Lark 多维表格

1. 在 Lark 中创建多维表，按产品需求建好各子表（DAU和DNU、渠道来源、周统计、次留表、周环比、消耗、游戏、付费人数、付费金额、新增设备、ARPU、ARPPU 等）
2. 打开每个子表，从 URL 复制 `table=tblXXX` 中的 `tblXXX`
3. 编辑 `config/skills/com.jachin.bi.daily_report/bi_daily_report.yaml`（或 `~/.jachin/config/skills/com.jachin.bi.daily_report/bi_daily_report.yaml`）：
   - `lark_bitable.enabled: true`
   - `lark_bitable.app_token`: 从多维表 URL `/base/XXX` 获取
   - `lark_bitable.tables`: 填入各 CSV 对应的 table_id

4. 执行：

```bash
python scripts/run_bi_report_refiner.py --sync-lark
```

- 或在配置中 `lark_bitable.enabled: true` 后，直接运行 `run_bi_report_refiner.py`（不带 `--sync-lark` 时也会根据配置同步）

### 步骤 5：完整日报流程（含 LLM、通知）

1. 配置 `bi_daily_report.yaml`：
   - `skip_collect: true`（若已完成抓取和 import）
   - `run_refiner: true`
   - `lark_bitable.enabled: true`（若需同步多维表）
   - `distribution.lark_webhook_url`（飞书 Webhook）
   - `distribution.email.to_addrs`（邮件收件人）

2. 执行主技能：

```bash
python -c "
from l3_node.skills.bi.bi_daily_report.main_skill import run_bi_daily_report
r = run_bi_daily_report()
print(r)
"
```

---

## 三、配置文件关键项

| 配置项 | 说明 | 示例 |
|--------|------|------|
| `skip_collect` | 是否跳过抓取 | `true`（直接用 DuckDB） |
| `run_refiner` | 是否运行提纯 | `true` |
| `storage.refiner_output_path` | 提纯 CSV 输出目录 | 空=默认 output |
| `lark_bitable.enabled` | 是否同步到 Lark | `true` |
| `lark_bitable.app_token` | 多维表 base ID | `HCvubvsubak2WPs2GbClYq68gjh` |
| `lark_bitable.tables` | CSV 与 table_id 映射 | 见下方映射表 |

**CSV → Lark 表 映射（与 Lark 多维表格侧栏一一对应）**

| 提纯 CSV | Lark 多维表格侧栏表名 |
|----------|----------------------|
| **用户登录活跃情况** | |
| 01_用户活跃_增幅表.csv | DAU和DNU |
| 03a_用户活跃_DAU渠道来源.csv | DAU渠道来源 |
| 02_用户活跃_日期数量表.csv | 周统计DAU和DNU数量 |
| 03b_用户活跃_DNU渠道来源.csv | DNU渠道来源 |
| 13_用户活跃_新增设备表.csv | 新增设备数、增幅、占比 |
| **平台留存情况** | |
| 04_留存_次留表.csv | 次留表 |
| 06_留存_周环比表.csv | 周环比 |
| **平台消耗情况** | |
| 08_消耗_每日表.csv | 每日金币产出、消耗 |
| 09_消耗_按游戏表.csv | 每个游戏的产出、消耗 |
| 10_充值_付费人数按SKU.csv | 付费人数表格（不同充值金人数） |
| 11_充值_付费金额按SKU.csv | 付费金额表格（不同充值金金额） |
| 14_充值_付费人数金额增幅表.csv | 付费人数、金额、增幅 |
| 15_充值_ARPU表.csv | ARPU |
| 16_充值_ARPPU表.csv | ARPPU |

---

## 四、常见问题

**Q: DuckDB 无数据**
- 先执行 `run_bi_scraper_spa` 和 `import_raw_to_duckdb`

**Q: Lark 同步失败**
- 检查 `LARK_APP_ID`、`LARK_APP_SECRET` 与多维表权限
- 确认 `lark_bitable.tables` 中 table_id 与 Lark 子表一一对应

**Q: 提纯 CSV 为空或列名不符**
- 用 `python scripts/inspect_bi_schema.py <slug>` 查看 BI 表结构
- 在 `main_skill.py` 中按需调整列名候选（`_find_col`）
