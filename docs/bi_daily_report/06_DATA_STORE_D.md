# BI 数据持久化层 (D) — 设计说明

**版本**: 1.0
**状态**: 已实现
**定位**: A 抓取的数据全部存入 D（DuckDB），供 C 分析使用

---

## 零、插件化指标查询（长期架构）

指标查询采用**插件架构**，支持多数据源、可配置指标、多输出格式。

| 组件 | 说明 | 内置实现 |
|------|------|----------|
| **数据源** | 拉取原始数据 | `duckdb` |
| **指标** | 配置驱动，支持 formula 派生 | YAML metrics |
| **输出器** | 格式化输出 | `console`、`markdown` |

**配置**: `config/bi_metrics.yaml`
**扩展**: 在 `l3_node/bi/metrics/plugins/` 新增插件并注册到 `registry.py`

---

## 一、职责

| 职责 | 说明 |
|------|------|
| **CSV → DuckDB 导入** | 接收 A 的 `file_path`，将 CSV 导入 DuckDB |
| **Schema 管理** | 按 slug 建表，表名 `bi_{slug}`，自动附加 `_ingested_at`、`_ingested_date` |
| **查询接口** | `get_table()`、`query()`、`list_available_slugs()`、`list_available_dates()` |

---

## 二、数据流

```
A (抓取)  →  raw/{slug}.csv  →  D.ingest_csv()  →  DuckDB bi_{slug} 表
                                    ↓
C (分析)  ←  D.get_table(slug)  ←  按 _ingested_date 过滤
```

---

## 三、实现位置

| 文件 | 说明 |
|------|------|
| `l3_node/bi/data_store.py` | D 模块主实现 |
| `l3_node/bi/paths.py` | `get_bi_duckdb_path()`、`get_bi_duckdb_dir()` |
| `scripts/run_bi_scraper_spa.py` | 抓取成功后调用 `ingest_csv()` |

---

## 四、API

### 4.1 ingest_csv(file_path, slug, captured_at?)

```python
from l3_node.mcp_tools.bi.data_store import ingest_csv

r = ingest_csv("/path/to/daily_ops_summary.csv", "daily_ops_summary")
# {"status": "success", "slug": "daily_ops_summary", "rows": 100, "table": "bi_daily_ops_summary"}
```

### 4.2 get_table(slug, date_from?, date_to?)

```python
from l3_node.mcp_tools.bi.data_store import get_table

df = get_table("daily_ops_summary", date_from="2026-03-01", date_to="2026-03-09")
# pandas.DataFrame
```

### 4.3 一键查询核心指标（脚本）

```bash
# 查询最新日期指标（默认与上一日对比）
python scripts/query_bi_metrics.py

# 指定日期
python scripts/query_bi_metrics.py --date 2026-03-16

# 对比周期：day(上一日)|week(一周前)|month(一月前)
python scripts/query_bi_metrics.py --compare-period week
python scripts/query_bi_metrics.py --compare-period month

# 不显示环比
python scripts/query_bi_metrics.py --no-compare
```

输出示例：DAU、DNU、新增设备数、付费人数/金额、ARPU、ARPPU、游戏局数、胜率、RTP、GGR 等。

### 4.4 查看表结构（列名）

```bash
python scripts/inspect_bi_schema.py                    # 列出所有表
python scripts/inspect_bi_schema.py daily_ops_summary  # 查看某表列名
```

### 4.5 list_available_slugs() / list_available_dates(slug)

```python
from l3_node.mcp_tools.bi.data_store import list_available_slugs, list_available_dates

slugs = list_available_slugs()
dates = list_available_dates("daily_ops_summary")
```

---

## 五、存储路径

- **DuckDB 文件**: `~/.jachin/client_volumes/bi_data/duckdb/bi.duckdb`
- **Raw CSV**（A 输出）: `~/.jachin/client_volumes/bi_data/raw/{slug}.csv`

---

## 六、依赖

- `duckdb>=0.9.0`（见 `l3_node/requirements-bi.txt`）
