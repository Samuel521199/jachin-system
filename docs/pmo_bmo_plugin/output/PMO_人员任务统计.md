# PMO 人员任务统计（2026-05-06 · 本周 2026-05-04～2026-05-10）

> **输出说明**：固定文件 `docs/pmo_bmo_plugin/output/PMO_人员任务统计.md`，每次运行 **覆盖**；raw 快照 **snapshot_date=2026-05-06**；**本周** 以生成日 **2026-05-06** 所在自然周为准。
> 由 `l3_node.primitives.skills.pmo_bmo.main_skill` 根据 PMO 导出 JSON **自动生成**（规则归并，非 NL 推理）。
> **筛选**：仅列出「开始日期/交付日期」与本周有交集的产品/开发/美术任务；无日期行不纳入。
> 干系人部门来自 `docs/bi_daily_report/bi_project/K11_需求池_干系人.md`（按名称小写匹配）。
> 每人每来源最多展示 **150** 条，超出部分请直接查 raw JSON。
> **关联**：同目录 `PMO_领导视图与周负荷摘要.md` 含本周负荷汇总、全量细需求表与卡片摘录（本任务一并生成）。

## 数据源

- 产品：`2026-05-06_req_march_fine.json`（责任人 / 开发执行人 / 美术执行人）
- 开发：`2026-05-06_dev_tasks_view_core.json`（任务执行人）
- 美术：`2026-05-06_art_tasks_completed.json`（设计责任人）
