# Work Ledger Value Chain 开发测试与排障日志

## 1. 用途

本文件记录成果价值链功能的开发测试口径、日志位置、已知问题和回归结果。

运行时的每一次一致性诊断以及 Value Chain HTTP 异常，会自动写入用户数据目录中的动态日志：

- Markdown：`<JACHIN_WORK_LEDGER_HOME>/logs/work_ledger_value_chain_test_log.md`
- JSONL：`<JACHIN_WORK_LEDGER_HOME>/logs/work_ledger_value_chain.jsonl`
- 默认情况下，`JACHIN_WORK_LEDGER_HOME` 位于当前用户的 Jachin 工作账本目录。
- Jachin 控制台的开发测试 Tab 会显示实际绝对路径，并支持点击复制。

仓库中的本文件用于长期保留测试结论；动态日志用于记录每台机器、每次运行的具体错误。

## 2. 开发测试 Tab

- 控制台入口：侧边栏 `今日工作台`。
- 页面位置：`工作账本` 页面顶部的 `价值链测试` Tab。
- 不设置独立路由或独立侧边栏入口，避免把一个开发诊断工具做成单独产品页面。
- 页面能力：
  - 选择最近 Work Ledger Session。
  - 查看项目全部 Outcome Value 和 Value Event。
  - 查看完成、交付、采用、影响、续作和方法论复用统计。
  - 写入明确标记的真实测试事件。
  - 运行无副作用的一致性诊断。
  - 查看每项检查通过、警告或失败原因。
  - 查看最近诊断和 HTTP 异常。
  - 复制 Markdown / JSONL 日志绝对路径。
  - 展开原始 Value Chain JSON。

### 推荐测试流程

1. 在 `工作账本` Tab 点击右上角 `开始记录`，系统创建 Session，并采集初始 Git / 文件证据。
2. 正常进行一段开发工作，或者在工作账本中补充手动记录、采集证据并生成输出。
3. 切换到 `价值链测试` Tab，确认刚创建的 Session 已自动选中。
4. 选择事件类型并写入测试说明；需要关联成果的反馈或影响事件，应先选择一个 Outcome。
5. 点击 `运行一致性诊断`，查看每个检查项的通过、警告或失败原因。
6. 在 `排障日志` 区域复制 Markdown 或 JSONL 路径，检查本次测试是否已经落盘。

如果没有可用 Session，测试 Tab 会显示 `去开始记录`，点击后直接切回工作账本，不会跳转到其他页面。

## 3. 一致性检查项

| 检查项 | 目的 | 失败含义 |
| --- | --- | --- |
| `project_bound` | Session 必须绑定项目 | 无法确定价值账本归属 |
| `value_event_ids_unique` | Value Event ID 不重复 | 可能重复计算采用或影响 |
| `outcome_references_valid` | 事件引用的 Outcome 存在 | 价值事件出现悬空引用 |
| `impact_and_feedback_linked` | 影响和反馈必须关联成果 | 无法证明评价针对哪个成果 |
| `value_events_have_evidence` | 生产事件应有 Evidence | 事件可能由开发直写或缺少执行依据 |
| `value_summary_consistent` | 聚合数字等于明细重算结果 | 持久数据与展示统计不一致 |
| `continuation_usage_bounded` | 续作使用不超过续作机会 | 续作链可能被重复或错误记录 |
| `methodology_reuse_bounded` | 复用成功不超过复用尝试 | 方法论统计出现矛盾 |

`value_events_have_evidence` 缺失默认作为 warning。开发脚本可以直接调用 Value API，因此允许出现警告；正式 UI 和 HTTP 写入必须带 Evidence。

## 4. 排障顺序

1. 在价值链实验室选择发生问题的 Session。
2. 点击“运行一致性诊断”。
3. 先查看红色 error，再查看黄色 warning。
4. 展开原始 Value Chain JSON，确认 Outcome ID、Value Event 和 Evidence ID。
5. 复制页面中的 Markdown 日志路径，查看同一时间附近的接口错误。
6. JSONL 用于程序化过滤；Markdown 用于人工阅读和附加到测试报告。
7. 修复后对同一 Session 再运行一次诊断，日志应留下修复前后两条记录。

## 5. 初始验证记录

### 2026-07-23：Node 29 Value Chain

- Work Ledger 全量单元测试：44 项通过。
- 30 天价值链回放：9 项断言全部通过。
- 5 个成果按“影响、采用、交付、完成、低价值完成”排序。
- 负向反馈不会修改 Project Fact 的 `completed` 状态。
- 29 次续作机会与 29 次实际使用分别记录。
- 方法论复用 3 次、成功 2 次，成功率 66.7%。
- 前端 TypeScript 检查通过。
- Python 编译检查通过。

### 2026-07-23：诊断日志闭环

- 成功诊断会同时写入 JSONL 和 Markdown。
- 缺失 Session 导致的诊断异常也会写入日志。
- Markdown 包含 Log ID、Session、摘要和结构化诊断详情。
- JSONL 保持每行一个 JSON 对象，单条异常不会影响后续日志读取。

## 6. 后续测试记录模板

```text
时间：
版本 / Commit：
Session ID：
项目路径：
触发操作：
预期结果：
实际结果：
Diagnostic Log ID：
失败检查项：
错误摘要：
根因：
修复文件：
回归命令：
回归结果：
是否关闭：
```
