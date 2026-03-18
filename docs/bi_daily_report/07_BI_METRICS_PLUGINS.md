# BI 指标查询 — 插件开发指南

**架构**: 长期方案，支持多数据源、多输出格式、自定义指标

---

## 一、架构概览

```
config/bi_metrics.yaml
        ↓
    Engine (engine.py)
        ↓
  ┌─────┴─────┬─────────────┐
  ↓           ↓             ↓
DataSource  Metrics     Outputter
(duckdb)   (config)    (console/markdown)
```

---

## 二、新增数据源插件

1. 在 `l3_node/bi/metrics/plugins/` 新建 `data_source_xxx.py`
2. 继承 `DataSource`，实现 `fetch(tables, date_col, date_value, config)`
3. 在 `plugins/__init__.py` 中 `register_data_source("xxx", XxxDataSource)`

```python
# data_source_api.py 示例
from l3_node.mcp_tools.bi.metrics.plugins.base import DataSource
from l3_node.mcp_tools.bi.metrics.registry import register_data_source

class APIDataSource(DataSource):
    def fetch(self, tables, date_col, date_value, config):
        # 从 API 拉取数据
        return {"table_name": {"col": value, ...}}
register_data_source("api", APIDataSource)
```

---

## 三、新增输出器插件

1. 新建 `output_xxx.py`
2. 继承 `Outputter`，实现 `format(metrics, config)`
3. 注册 `register_outputter("xxx", XxxOutputter)`

```python
# output_lark.py 示例
from l3_node.mcp_tools.bi.metrics.plugins.base import Outputter
from l3_node.mcp_tools.bi.metrics.registry import register_outputter

class LarkOutputter(Outputter):
    def format(self, metrics, config):
        # 格式化为飞书卡片
        return markdown_content
register_outputter("lark", LarkOutputter)
```

---

## 四、配置说明（config/bi_metrics.yaml）

### 4.1 指标定义

```yaml
metrics:
  - key: dau
    table: daily_ops_summary
    column_candidates: ["日活(DAU)", "日活", "DAU"]

  - key: dnu_per_device
    formula: "dnu / new_devices if new_devices else dnu"
```

| 字段 | 说明 |
|------|------|
| key | 指标键名，用于后续 formula 引用 |
| table | 数据表（slug） |
| column_candidates | 列名候选，按顺序匹配 |
| formula | 派生指标，支持 Python 表达式 |

**环比**：与上一日/周/月对比，自动计算 `(当前-上期)/上期*100%`。无上期数据时显示 0.00%。通过 `--compare-period day|week|month` 指定对比周期。

### 4.2 输出布局

```yaml
output:
  plugin: console
  console:
    layout:
      - - { key: dau, label: "DAU", compare: dau_pct, format: ".0f" }
        - { key: dnu, label: "DNU", compare: dnu_pct, format: ".0f" }
      - - { key: paid_count, label: "付费人数", format: ".0f" }
```

- `layout`: 二维数组，每行一行输出，每行内多项用空格分隔
- `compare`: 环比 key（如 dau_pct）
- `format`: Python 数值格式（.0f、.2f）

---

## 五、扩展方式

| 需求 | 做法 |
|------|------|
| 新增简单指标 | 在 YAML metrics 加一项 |
| 新增派生指标 | 加 formula 项 |
| 列名变更 | 在 column_candidates 追加 |
| 新数据源 | 写 DataSource 插件并注册 |
| 新输出格式 | 写 Outputter 插件并注册 |
| 复杂计算 | 写 Metric 插件（需扩展 engine 支持） |
