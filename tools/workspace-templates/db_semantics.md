# 业务语义层（Data Dictionary / Semantic Layer）

本文件放在 **`~/.jachin/workspace/db_semantics.md`**（或与网关嗅探相同的 workspace 根目录），
由 **Omni-Context Sniffer** 注入 `[ENVIRONMENT_REPORT]`，供模型将**自然语言指标**映射为 **SQL 条件**。

> 以下为示例；请按实际表名、列名修改。

## 表：inventory（示例）

| 业务用语 | 建议 SQL 条件片段 | 说明 |
|----------|-------------------|------|
| 缺货 | `WHERE quantity = 0` | 若列名为 `count`/`stock` 请改为对应列 |
| 低库存 | `WHERE quantity > 0 AND quantity < 10` | 阈值可改 |
| 有库存 | `WHERE quantity > 0` | |
| 热销（示例，需有 sales 列时） | `ORDER BY sales DESC LIMIT 5` | 无 sales 列则删除此行 |

## 使用约定

- 用户未定义时，**「缺货」默认数量字段为 0**；若实际列名不同，在此表写明。
- 复杂指标先在此定义再写 SQL，避免模型凭语料猜测。
