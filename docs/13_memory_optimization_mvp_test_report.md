# Jachin 记忆优化最小 MVP 测试报告

版本：v0.9.110

提交：`3aeea4e7`

日期：2026-07-14

## 1. 测试目标

本轮测试验证 Jachin 第一版记忆优化最小 MVP 是否已经形成可用闭环：

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
-> MemoryRecall 反哺下一轮
```

重点不是只验证“能写日志”，而是验证记忆是否能被结构化写入、复盘、提升、召回，并影响下一轮任务理解和恢复策略。

## 2. 本轮覆盖能力

### 2.1 意图和能力识别

验证点：

- Capability / Skill Manifest 能进入 Capability Registry。
- ReviewBoard 能输出 capability semantic candidates。
- `lock` 这类误识别文本能排序到 Lark 候选。
- 轻量语义候选不会绕过 Arbiter 门控。
- 用户确认后的纠错可写入统一记忆。

涉及模块：

```text
l3_node/capability_semantic_registry.py
l3_node/cognitive_kernel/semantic_intent_agent.py
l3_node/cognitive_kernel/review_board.py
l3_node/cognitive_kernel/entity_corrections.py
```

### 2.2 任务拆解

验证点：

- TaskDecomposer 能基于 capability metadata 生成 DAG。
- Manifest 中声明的 `decomposition.nodes` 能转成正式任务节点。
- DAG 节点保留 role_agent、tool/capability、inputs、risk_level、verification_criteria。

涉及模块：

```text
l3_node/cognitive_kernel/task_decomposer.py
l3_node/cognitive_kernel/task_dag.py
docs/12_task_decomposer_agent_architecture.md
```

### 2.3 任务经验写回

验证点：

- WorkOrder 执行结束后能生成任务经验。
- 成功任务写入 `historical_task_summary` 和 `tool_habit`。
- 失败任务写入 `failure_hint`。
- `close_turn` 在没有显式 TaskMemory 的旧入口下，也会兜底写入 `historical_task_summary`。
- MemoryWriteAgent 通过 WorkOrder 通道写入，不走旁路。

涉及模块：

```text
l3_node/cognitive_kernel/task_memory.py
l3_node/cognitive_kernel/runtime.py
l3_node/cognitive_kernel/direct_mainline.py
l3_node/cognitive_kernel/memory_lifecycle.py
l3_node/cognitive_kernel/closure_memory.py
```

### 2.4 Memory Growth 自生长链路

验证点：

- TurnClosure 能写入 raw evidence。
- DailyReview 能从 raw event 生成 concept / playbook / output patch。
- ConceptCurator 能提升稳定概念。
- PlaybookBuilder 能沉淀可复用流程。
- OutputReview 能沉淀最终输出。
- GrowthScheduler 能串起 full pipeline。

涉及模块：

```text
l3_node/cognitive_kernel/memory_growth.py
l3_node/cognitive_kernel/daily_review.py
l3_node/cognitive_kernel/concept_curator.py
l3_node/cognitive_kernel/playbook_builder.py
l3_node/cognitive_kernel/output_review.py
l3_node/cognitive_kernel/growth_scheduler.py
l3_node/cognitive_kernel/weekly_review.py
```

### 2.5 记忆召回

验证点：

- lifecycle memory 可以召回历史任务摘要。
- lifecycle memory 可以召回工具习惯。
- lifecycle memory 可以召回失败提示。
- MemoryRecallAgent 能同时返回 concepts、playbooks、tool_habits、failure_hints、historical_task_summaries。

涉及模块：

```text
l3_node/cognitive_kernel/memory_recall_agent.py
l3_node/cognitive_kernel/memory_growth_recall.py
l3_node/cognitive_kernel/memory_confidence.py
```

## 3. 已执行测试命令

### 3.1 核心记忆与认知内核测试

命令：

```powershell
python -m pytest -o addopts= tests\unit\test_memory_growth.py tests\unit\test_cognitive_kernel_architecture.py tests\unit\test_cognitive_kernel_runtime.py
```

结果：

```text
100 passed, 9 warnings
```

说明：

- `test_memory_growth.py` 覆盖 Memory Growth scaffold、raw event、DailyReview、ConceptCurator、PlaybookBuilder、OutputReview、WeeklyReview、GrowthScheduler。
- `test_cognitive_kernel_architecture.py` 覆盖 Capability semantic registry、ReviewBoard、Arbiter、TaskDecomposer、direct mainline。
- `test_cognitive_kernel_runtime.py` 覆盖 TurnClosure、MemoryWriteAgent、Recovery、MemoryRecall、RoleExecutor、pending confirmation 等 runtime 链路。

### 3.2 提交前格式检查

命令：

```powershell
git diff --cached --check
```

结果：

```text
通过
```

处理过的问题：

- 清理了 staged 文本文件中的行尾空格。
- 清理了部分文件 EOF 多余空行。

### 3.3 敏感文件检查

命令：

```powershell
git diff --cached --name-only | rg -n "(^|/)(\.env|.*\.env$|.*\.env\.|.*secret.*|.*token.*)"
```

结果：

```text
无 staged .env / token / secret 文件名命中
```

补充：

- `.env`
- `clients/desktop/.env`
- `cloud/nexus/.env.local`
- `dist_jachin_desktop/.env`

仍处于 ignored 状态，未进入提交。

## 4. 测试中发现并修复的问题

### 4.1 Growth pipeline concept 不提升

现象：

```text
test_growth_scheduler_runs_full_pipeline 失败
concept_result.promoted_count == 0
```

原因：

- 测试使用 `lark_message_send` 的假 observation。
- 新架构要求消息发送必须有发送后验证证据。
- 该假 observation 被正确判定为 verification failed。
- 失败任务只生成低置信失败候选，不应直接提升为稳定 concept。

修复：

- 将 pipeline 成功链测试改为可本地验证的 `core:fs_read`。
- 保留 Lark 消息发送必须验证的严格原则，避免再次出现虚假发送。

### 4.2 TurnClosure 只有短期动作记忆

现象：

- 部分旧入口只写 `short_term_action`。
- 如果上游没有显式 TaskMemory，DailyReview 缺少可提升的任务摘要。

修复：

- `close_turn` 增加兜底 `historical_task_summary` 写入。
- 如果上游已经传入更详细的 TaskMemory，则不重复生成。

效果：

- 任意执行过 WorkOrder 的 turn，至少会产生一条可复盘任务摘要。
- DailyReview 能稳定从 raw 中抽取候选概念。

### 4.3 MemoryWriteAgent 测试断言过旧

现象：

```text
test_turn_closure_memory_requests_execute_via_memory_agent
期望 1 条 MemoryWriteAgent 结果，实际 2 条
```

原因：

- 新增 `historical_task_summary` 后，TurnClosure memory writes 从 1 条变为 2 条。

修复：

- 测试改为断言：
  - `short_term_action`
  - `historical_task_summary`
- 并确认两条都通过 MemoryWriteAgent WorkOrder 执行。

## 5. 当前通过的核心测试项

| 测试类别 | 状态 | 说明 |
| -------- | ---- | ---- |
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
| Recovery 记忆基础 | 通过 | failure hint 可进入后续恢复证据 |

## 6. 仍需补的深度测试

### 6.1 真实桌面任务记忆回放

待测场景：

```text
打开浏览器
关闭
打开 Lark
发送消息给 Neil
再次输入“关闭”
验证是否能根据最近任务和状态判断关闭对象
```

验收：

- Evidence Console 能看到 DecisionContract。
- MemoryRecall 能引用最近 App / 最近动作。
- 用户纠错后再次输入相似内容不再重复询问。

### 6.2 Lark 真实发送记忆链路

待测场景：

```text
打开 Lark 给 Neil 发送“你好”
```

验收：

- 不允许虚假发送。
- 必须有 post-send verification。
- 成功后写入：
  - contact/entity usage
  - communication tool habit
  - historical_task_summary

### 6.3 RecoveryPlanner 使用失败经验

待测场景：

```text
路径 A 失败
系统记录失败原因
下一次类似任务先尝试路径 B
B 失败后结合 A+B 原因再尝试 C
```

验收：

- 不是预先写死 B/C/D。
- 每次新尝试都引用上一轮失败原因。
- 最多重试次数达到后输出失败总结和下一步建议。

### 6.4 Daily / Weekly Review 长周期测试

待测场景：

```text
连续生成 7 天 raw event
运行 DailyReview + WeeklyReview
```

验收：

- 生成 7/14/30 天趋势。
- 能识别重复失败模式。
- 能标记陈旧概念。
- 能产出需要用户确认的知识队列。

### 6.5 Concepts / Playbooks 对规划的实际影响

待测场景：

```text
先让系统沉淀一个 Lark 发送 playbook
再发起新的“发消息给 Neil”任务
```

验收：

- ReviewBoard / Arbiter 的 evidence 中出现相关 playbook 引用。
- TaskDecomposer 按 playbook 生成更稳定的 WorkOrder。
- Evidence Console 展示“为什么选择这条路径”。

## 7. 当前结论

v0.9.110 已经达到“记忆优化最小 MVP”的标准：

- 任务可以写入 raw。
- raw 可以被 DailyReview 消化。
- 稳定候选可以提升为 concepts。
- 成功/失败经验可以进入 playbooks 和 lifecycle memory。
- MemoryRecall 可以把这些经验带回下一轮任务。
- Capability/Skill metadata 已经参与意图识别和任务拆解。

但它还不是最终形态。下一阶段要重点做真实桌面任务和 Lark 任务的 live memory replay，让记忆不只在单测中闭环，而是在真实 OS 助手任务里稳定影响决策、恢复和解释。
