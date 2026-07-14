# Jachin 记忆优化 MVP 测试报告

版本：v0.9.110+

日期：2026-07-14

## 1. 测试目标

本轮测试验证 Jachin 第一版记忆优化 MVP 是否形成可用闭环，并补充极端压力场景，重点确认：

```text
用户输入
-> 语义候选 / Capability 候选
-> ReviewBoard / Arbiter
-> TaskDecomposer / WorkOrder
-> RoleExecutor / Verification
-> TurnClosure
-> MemoryWriteAgent
-> lifecycle memory
-> DailyReview
-> Concepts / Playbooks / Outputs
-> MemoryRecall 反哺下一轮任务
-> RecoveryPlanner 基于失败历史逐步选择下一条路径
```

本轮不只验证“能写日志”，还验证记忆是否能被结构化写入、复盘、提升、召回，并影响下一轮任务理解、恢复策略和执行解释。

## 2. 已覆盖能力

### 2.1 意图和能力识别

- Capability / Skill Manifest 能进入 Capability Registry。
- ReviewBoard 能输出 capability semantic candidates。
- `lock` 这类误识别文本能排序到 Lark 候选。
- 轻量语义候选不会绕过 Arbiter 门控。
- 用户确认后的纠错可写入统一记忆，下次直接命中。

涉及模块：

```text
l3_node/capability_semantic_registry.py
l3_node/cognitive_kernel/semantic_intent_agent.py
l3_node/cognitive_kernel/review_board.py
l3_node/cognitive_kernel/entity_corrections.py
```

### 2.2 任务拆解

- TaskDecomposer 能基于 capability metadata 生成 DAG。
- Manifest 中声明的 `decomposition.nodes` 能转成正式任务节点。
- DAG 节点保留 `goal`、`role_agent`、`tool/capability`、`inputs`、`risk_level`、`verification_criteria`。

### 2.3 任务经验写回

- WorkOrder 执行后能生成任务经验。
- 成功任务写入 `historical_task_summary` 和 `tool_habit`。
- 失败任务写入 `failure_hint`。
- `close_turn` 在没有显式 TaskMemory 的入口下，也会兜底写入 `historical_task_summary`。
- MemoryWriteAgent 通过 WorkOrder 通道写入，不走旁路。

### 2.4 Memory Growth 自生长链路

- TurnClosure 能写入 raw evidence。
- DailyReview 能从 raw event 生成 concept / playbook / output patch。
- ConceptCurator 能提升稳定概念。
- PlaybookBuilder 能沉淀可复用流程。
- OutputReview 能沉淀最终输出。
- GrowthScheduler 能串起 full pipeline。

### 2.5 记忆召回

- lifecycle memory 可以召回历史任务摘要、工具习惯、失败提示。
- MemoryRecallAgent 能同时返回 concepts、playbooks、tool_habits、failure_hints、historical_task_summaries。
- Memory Growth playbook 能从 Markdown Wiki 召回为 `failure_hint`。

### 2.6 Recovery 逐步自我纠正

- RecoveryPlanner 不再一次性写死 B/C/D 路径。
- 第一次失败后，结合失败原因和记忆选择下一条恢复路径。
- 第二次失败后，会结合 A+B 的失败原因再选择后续策略。
- 达到最大尝试次数后，会输出失败摘要、失败原因聚合和下一步建议。

## 3. 已执行测试命令

### 3.1 核心记忆与认知内核组合测试

命令：

```powershell
python -m pytest -o addopts= -q tests\unit\test_memory_stress_mvp.py tests\unit\test_memory_deep_mvp.py tests\unit\test_memory_growth.py tests\unit\test_cognitive_kernel_architecture.py tests\unit\test_cognitive_kernel_runtime.py
```

结果：

```text
105 passed in 30.27s
```

说明：

- 本次组合测试包含原有核心内核测试和新增压力测试。
- 已通过 `pytest.ini` 过滤外部依赖噪声 warning，避免测试报告被无关 warning 干扰。

### 3.2 压力测试单独运行

命令：

```powershell
python -m pytest -o addopts= -q tests\unit\test_memory_stress_mvp.py
```

结果：

```text
3 passed
```

## 4. 新增极端压力场景

### 4.1 生命周期记忆重复风暴

测试文件：

```text
tests/unit/test_memory_stress_mvp.py
```

场景：

- 对同一个 `tool_habit` 连续写入 150 次。
- 验证 dedupe 后仍然只有一个 memory id。
- 验证 `hit_count == 150`。
- 验证召回时不会出现 150 条重复记忆。

结论：通过。重复写入会合并为同一条长期记忆，不会污染召回结果。

### 4.2 短期记忆 TTL 过期压力

场景：

- 写入 40 条 `short_term_action`。
- TTL 设置为 `1ms`。
- 触发 `expire_lifecycle_memories()`。
- 验证全部过期后无法再召回。

结论：通过。短期记忆不会无限膨胀，生命周期清理可用。

### 4.3 DailyReview 大文件、重复行、坏 JSON 容错

场景：

- 构造 60 条有效 raw evidence。
- 追加 1 条重复 raw evidence。
- 追加 1 条非法 JSON 行。
- 运行 DailyReview。

验证：

- `raw_event_count == 61`。
- `passed_count == 60`。
- concept / playbook / output candidates 均能生成。
- 只记录一条坏 JSON 修复 warning。
- candidate id 去重后不重复。

结论：通过。DailyReview 可以处理大批量 raw、重复行和部分损坏数据，不会整轮崩溃。

### 4.4 Recovery 最大尝试次数和失败摘要

场景：

- 构造 AppControl focus timeout 失败。
- 注入 Memory Growth playbook 作为失败恢复证据。
- 设置 `max_attempts = 2`。
- 模拟两次恢复都失败。

验证：

- 第三次不会继续盲目重试。
- final failure report 聚合每次失败原因。
- report 保留 memory_context_refs。
- report 给出下一步建议。

结论：通过。Recovery 有尝试上限，也能解释为什么失败。

## 5. 本轮修复的问题

### 5.1 pytest warning 噪声

现象：

- fastembed 输出 mean pooling warning。
- psutil 在 Windows 下输出 `getargs` deprecation warning。

处理：

- 在 `pytest.ini` 增加 `filterwarnings`，过滤外部依赖噪声。
- 保留业务测试失败和真实 warning 的可见性。

### 5.2 DailyReview 压测中 output candidate 不生成

现象：

- 初版压测 raw 数据缺少完整输出提升线索，导致 `output_candidate_count == 0`。

处理：

- 压测 raw event 增加 `final_user_message_intent`、`verification_status`、`executed_work_orders` 和 `promotion_targets`。

结论：

- 修复后 concepts、playbooks、outputs 均能生成。

### 5.3 中文断言编码风险

现象：

- Windows 控制台容易把 UTF-8 中文显示为乱码。

处理：

- 压测断言从中文全文匹配改为结构化字段和关键数值判断。
- 测试报告保留 UTF-8 中文正文，便于人工阅读。

## 6. 当前通过的核心测试项

| 测试类别 | 状态 | 说明 |
| --- | --- | --- |
| Memory Growth scaffold | 通过 | 目录、模板、schema 可生成 |
| Raw Evidence 写入 | 通过 | TurnClosure 可写 raw event |
| Daily Review | 通过 | raw 可生成 review patch |
| Concept Curator | 通过 | 稳定候选可提升，低置信进入隔离 |
| Playbook Builder | 通过 | 成功/失败任务可形成 playbook |
| Output Review | 通过 | 输出成果可进入 outputs 层 |
| Growth Scheduler | 通过 | full pipeline 可串行运行 |
| Capability Semantic Registry | 通过 | manifest 进入候选能力池 |
| Semantic Intent Candidate | 通过 | 支持多候选排序 |
| TaskDecomposer | 通过 | capability metadata 可拆 DAG |
| TaskMemory | 通过 | 成功/失败经验可生成 |
| MemoryLifecycle | 通过 | 任务摘要、工具习惯、失败提示可写入 |
| MemoryRecallAgent | 通过 | 经验记忆可召回进 RelevantMemoryBundle |
| TurnClosure MemoryWriteAgent | 通过 | 记忆写入走 WorkOrder 通道 |
| Recovery 记忆基础 | 通过 | failure hint 可进入恢复证据 |
| 用户纠错记忆影响 ReviewBoard | 通过 | `lock -> Lark` 确认后，下次直接识别为 Lark |
| Memory Growth Playbook 影响 RecoveryPlanner | 通过 | playbook 召回后驱动恢复策略 |
| 重复记忆风暴 | 通过 | 150 次重复写入合并为一条 |
| 短期记忆过期压力 | 通过 | TTL 到期后可批量清理 |
| DailyReview 坏数据容错 | 通过 | 大量 raw、重复 raw、坏 JSON 不会中断整轮 |
| Recovery 最大尝试上限 | 通过 | 达到上限后输出失败摘要，不盲目重试 |

## 7. 仍需补充的真实压力测试

单元和组合测试已经覆盖记忆 MVP 的核心链路，但还需要真实桌面任务压力测试：

```text
打开浏览器 -> 关闭
打开 Lark -> 发送消息给 Neil
打开计算器 -> 输入表达式 -> 视觉校验结果
文件 read/open/reveal
多轮用户纠错后再次输入相似任务
```

验收重点：

- Evidence Console 能看到 DecisionContract、WorkOrder、Verification、Recovery、TurnClosure。
- 不允许虚假发送、虚假打开、虚假计算。
- 每个失败都必须写入 failure_hint。
- 重复失败后 RecoveryPlanner 必须逐步改变策略。

## 8. 当前结论

Jachin 记忆优化 MVP 已经通过更深层次压力测试：

- 记忆可写入。
- 记忆可去重。
- 记忆可过期。
- raw evidence 可容错复盘。
- concepts / playbooks / outputs 可提升。
- 用户纠错可反哺意图识别。
- 失败经验可反哺 Recovery。
- Recovery 有最大尝试上限和失败摘要。

当前结论：记忆 MVP 的单元级和组合级闭环已经成立。下一步应进入真实 OS 任务压力测试，把这些记忆能力放进桌面 App、Lark、文件系统、计算器等 live workflow 中验证。
