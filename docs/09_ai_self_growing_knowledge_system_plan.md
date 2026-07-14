# Jachin AI 自生长知识系统升级方案

版本基线：v0.9.109

目标：把 Jachin 的记忆从“记录和检索”升级为“持续消化、泛化、沉淀、反哺”的自生长知识系统。

## 1. 核心定义

Jachin 的长期记忆不应该只是收藏夹、聊天历史或向量库，而应该是一个持续运行的知识生长循环：

```text
A 原始证据
  -> B 高价值概念
  -> C 可复用方法论
  -> D 输出成果
  -> 复盘回流
  -> 新一轮生长
```

这套系统的本质是：

- 原始资料统一进入证据池，不直接污染长期记忆。
- AI 定期消化原始资料，抽取稳定事实和高价值概念。
- 高价值概念进一步泛化成可复用 playbook。
- playbook 参与未来任务规划、工具选择、失败恢复和输出风格。
- 每次任务输出、失败报告、用户反馈再次回流为原始资料。

## 2. 四层知识结构

### A. Raw Evidence 原始证据层

目录建议：

```text
memory_growth/raw/
  conversations/
  evidence/
  files/
  app_activity/
  skill_runs/
  mcp_runs/
  lark_messages/
  reports/
```

职责：

- 只追加，不覆盖。
- 保存事实原貌。
- 保留来源、时间、任务、会话、截图、OCR、工具结果。
- 任何长期结论必须能回溯到这里。

典型内容：

- 用户原始输入
- Jachin 回复
- WorkOrder 执行记录
- Evidence JSON
- 截图和 OCR
- Lark dry-run / real-run 证据
- App 打开、关闭、切换记录
- 文件读写和项目变更
- PMO / 英语 / 桌面 Agent 的运行结果

### B. Concepts 高价值概念层

目录建议：

```text
memory_growth/concepts/
  people/
  projects/
  apps/
  skills/
  mcps/
  workflows/
  preferences/
  problems/
  decisions/
```

职责：

- 从 raw 中抽取稳定、有复用价值的知识。
- 每个概念页面必须有人类可读的 Markdown 版本。
- 每条关键结论必须带来源、置信度、更新时间。

示例：

```text
concepts/projects/Jachin.md
concepts/people/Neil.md
concepts/apps/Lark.md
concepts/skills/PMO.md
concepts/problems/WindowsFocusFailure.md
concepts/preferences/ConfirmationPolicy.md
```

页面字段建议：

```yaml
id:
type:
summary:
source_refs:
confidence:
last_verified:
valid_from:
valid_until:
conflicts:
```

### C. Playbooks 可复用方法论层

目录建议：

```text
memory_growth/playbooks/
  os_app_control.md
  lark_message_delivery.md
  codex_project_briefing.md
  pmo_dry_run_report.md
  skill_publish_install_validate.md
  english_vocab_quality_loop.md
  recovery_path_selection.md
```

职责：

- 把重复成功经验和失败恢复路径沉淀成方法论。
- 未来任务规划时优先读取 playbook，而不是每次重新想。
- 每个 playbook 需要定义触发条件、步骤、验证标准、失败路径。

标准结构：

```text
# Playbook 名称

## 适用场景
## 触发条件
## 必要上下文
## 推荐流程
## WorkOrder 拆分
## 可用 Skill / MCP
## 验证标准
## 失败路径
## 用户确认边界
## Evidence 要求
## 历史有效案例
## 禁止事项
```

### D. Outputs 输出成果层

目录建议：

```text
memory_growth/outputs/
  reports/
  lark_messages/
  work_records/
  pmo_reports/
  debug_summaries/
  user_docs/
```

职责：

- 保存 Jachin 对外输出的最终成果。
- 成果会在复盘时被评估，优秀输出可以反哺 concepts 和 playbooks。
- 失败输出也要保存，用于生成反模式和恢复策略。

## 3. 自生长循环

### 3.1 实时写入

每一轮对话或任务结束时，TurnClosureAgent 只做轻量写入：

- 写入 raw event
- 写入 output artifact 引用
- 标记是否值得复盘
- 标记是否涉及用户偏好、项目、App、Skill、MCP

不在实时链路里做重型总结，避免拖慢用户体验。

### 3.2 定时消化

DailyReviewAgent 每天运行：

- 扫描当天 raw
- 聚合同一任务链
- 提取候选概念
- 找出失败模式
- 生成 daily review
- 给 concepts/playbooks 提出 patch

WeeklyReviewAgent 每周运行：

- 合并重复概念
- 更新项目、用户、Skill、App 页面
- 发现高频任务并升级为 playbook
- 删除或降权过期结论
- 生成本周知识增量报告

### 3.3 反哺执行

任务执行前，MemoryRecallAgent 按顺序读取：

1. 当前会话短期上下文
2. Graphiti 时间记忆
3. Concepts 稳定概念
4. Playbooks 方法论
5. Cognee 结构化知识图谱
6. Raw Evidence 必要证据

ReviewBoard / Arbiter 生成 DecisionContract 时必须引用相关 concepts/playbooks。

## 4. Cognee / Graphiti / Karpathy Wiki 的分工

### Karpathy-style Markdown Wiki

负责显性知识：

- 人可读
- AI 可直接读
- 可审计
- 可版本管理
- 可手工修正

对应目录：

```text
memory_growth/concepts/
memory_growth/playbooks/
memory_growth/outputs/
memory_growth/reviews/
```

### Graphiti

负责时间记忆：

- 谁在什么时候做了什么
- 最近打开的 App
- 最近操作的项目和文件
- 事实有效期
- 旧事实过期
- 指代理解

适合查询：

```text
用户说“关闭”时最近打开的是谁？
Jachin 路径最近是否改过？
Neil 最近是否作为 Lark 收件人出现？
这个 PMO 配置什么时候最后验证过？
```

### Cognee

负责结构化知识图谱：

- 项目、文档、Skill、MCP、模型、用户、App 的实体关系
- 大规模文档和业务资料语义检索
- 从 raw/concepts 中抽取关系
- 支持多跳知识查询

适合查询：

```text
PMO Skill 依赖哪些 MCP 和模型？
英语助手和哪些模型、缓存、页面有关？
Jachin 的 OS assistant workflow 涉及哪些模块？
```

## 5. Agent 职责升级

### MemoryWriteAgent

新增职责：

- 把 TurnClosure 写入 raw。
- 标记复盘价值。
- 抽取低成本候选 fact。
- 不直接覆盖长期概念。

### MemoryRecallAgent

新增职责：

- 按任务类型组合读取 raw/concepts/playbooks/Graphiti/Cognee。
- 输出带来源的 MemoryContext。
- 标记冲突和过期事实。

### ReviewAgent

新增职责：

- 定期消化 raw。
- 生成 concepts patch。
- 生成 playbooks patch。
- 维护 review log。

### ConceptCuratorAgent

新增职责：

- 合并重复概念。
- 维护置信度。
- 标记 valid_until。
- 处理冲突事实。

### PlaybookBuilderAgent

新增职责：

- 从高频成功/失败任务中归纳方法论。
- 为 RecoveryPlanner 生成可执行 failure path。
- 为 Skill/MCP 生成推荐调用策略。

### OutputReviewAgent

新增职责：

- 评估输出质量。
- 发现可复用模板。
- 把优秀输出升级为 examples 或 playbook section。

## 6. 升级阶段

建议分 8 步完成。

### Step 1：建立 Memory Growth 目录和文档规范

目标：

- 新增 `memory_growth/` 目录结构。
- 定义 raw/concepts/playbooks/outputs/reviews/indexes 的 schema。
- 定义 Markdown frontmatter 规范。

验收：

- 每个目录有 README。
- 每种页面有模板。
- 不接入执行链路，只建立标准。

### Step 2：TurnClosure 写入 Raw Evidence

目标：

- 每个任务结束后自动写入 raw event。
- raw event 关联 DecisionContract、WorkOrder、Evidence、输出文件。
- 保证 append-only。

验收：

- 聊天、AppControl、File、Message、Skill/MCP 调用都会产生 raw entry。
- raw entry 可追溯到 evidence。

### Step 3：Daily Review Agent

目标：

- 每天从 raw 生成 review。
- 抽取候选 concepts。
- 标记候选 playbook。
- 不自动覆盖旧概念，先生成 patch。

验收：

- `memory_growth/reviews/YYYY-MM-DD.md`
- `memory_growth/reviews/patches/*.json`

### Step 4：Concepts 自动生成和合并

目标：

- 将 review patch 合并到 concepts。
- 支持 source_refs、confidence、last_verified。
- 支持冲突事实进入 conflicts。

验收：

- 项目、用户、App、Skill、MCP、偏好都能生成概念页。
- 用户显式更正时旧事实会降权或过期。

### Step 5：Playbook 自动沉淀

目标：

- 从重复任务和失败恢复中生成 playbook。
- RecoveryPlanner 可读取 playbook failure path。
- Arbiter 可读取 playbook 执行流程。

验收：

- 至少生成 5 个核心 playbook：
  - App 打开/关闭/切换
  - Lark 消息发送
  - 项目总结发 Lark
  - Skill 发布安装验证
  - 英语助手质量闭环

### Step 6：MemoryRecallAgent 使用 Concepts + Playbooks

目标：

- 任务规划前读取相关 concepts/playbooks。
- DecisionContract 记录使用了哪些记忆。
- Evidence Console 展示记忆依据。

验收：

- 用户说“关闭”时能引用最近 App 记忆。
- 用户说“总结 Jachin 发给 Neil”时能引用项目、联系人、工作流 playbook。

### Step 7：接入 Graphiti 时间记忆

目标：

- 把 raw event 同步成 temporal graph。
- 支持最近 App、最近文件、最近收件人、事实有效期查询。
- 支持事实过期和冲突。

验收：

- 指代理解走 Graphiti。
- “最近一次”“刚才那个”“发给他”可解释可回放。

### Step 8：接入 Cognee 知识图谱

目标：

- 将 concepts、outputs、文档、Skill/MCP manifest 摄入 Cognee。
- 建立实体关系和语义检索。
- 大规模项目/业务资料走 Cognee 查询。

验收：

- 查询 Skill/MCP/model 依赖关系。
- 查询项目模块关系。
- 查询业务知识和历史决策。

## 7. 核心原则

1. Raw 永远 append-only。
2. 长期记忆必须可追溯。
3. 实时链路只轻写，重消化放后台。
4. 输出必须回流。
5. 成功和失败都要沉淀为方法论。
6. 概念页必须人类可读。
7. 数据库是引擎，Markdown Wiki 是显性大脑。
8. Graphiti 解决时间，Cognee 解决关系，Wiki 解决复盘沉淀。

## 8. 第一阶段最小闭环

最小可用闭环不需要先接 Cognee/Graphiti。

先实现：

```text
TurnClosure -> raw
DailyReview -> concepts patch
ConceptCurator -> concepts markdown
PlaybookBuilder -> playbooks markdown
MemoryRecall -> 读取 concepts/playbooks
```

完成后，Jachin 就会从“记日志”变成“会复盘并长出方法论”。

## 9. 预期效果

升级后 Jachin 应该具备：

- 记得用户项目、路径、联系人和偏好。
- 记得最近 App / 文件 / 任务上下文。
- 能解释为什么选择某个 workflow。
- 能从历史失败中选择更好的路径。
- 能把成功任务沉淀成 playbook。
- 能把输出成果继续反哺知识系统。
- 随着使用时间增长，执行更稳、更符合用户习惯。

## 10. 最新实现进展：意图、能力和任务经验进入自生长闭环

更新时间：2026-07-14

本轮更新把“用户输入 -> 能力选择 -> 任务拆解 -> 执行结果 -> 经验记忆”的链路继续向自生长知识系统靠拢。重点不是新增一套孤立记忆，而是把任务经验、工具习惯、失败模式和用户纠错统一接入现有 Cognitive Kernel 与 MemoryWriteAgent。

### 10.1 Capability / Skill Manifest 参与意图识别

已实现能力：

- `Capability Registry` 不再只依赖内置画像或 `review_board.py` 规则。
- 系统会扫描 Skill / MCP / Model 的 `plugin.json`，把 manifest 中的名称、描述、关键词、任务类型、依赖、风险等级、decomposition 等 metadata 注册为可检索能力。
- 用户输入进入 `ReviewBoard` 后，会同时生成规则候选、语义候选和 capability 候选。
- 最终是否采用候选能力仍由 Arbiter / DecisionContract 门控，避免模型自由乱选工具。

当前落点：

```text
l3_node/capability_semantic_registry.py
l3_node/cognitive_kernel/review_board.py
l3_node/cognitive_kernel/contracts.py
```

对应自生长意义：

- Skill / MCP 自己声明“我能做什么”，主系统只负责理解、排序和授权。
- 后续新增能力时，不需要继续把大量 if-else 写进主流程。

### 10.2 轻量语义解析和多候选排序

已实现能力：

- 新增轻量 `SemanticIntentAgent`。
- 规则解析仍然保留作为稳定底座。
- 可选轻量 LLM 只输出候选 intent、target、confidence 和 reason。
- 多候选会统一排序，例如：
  - `lock` 可以被排序为 `Lark` 候选。
  - “浏览器”可以同时生成 Chrome / Edge / Browser 候选。
  - “发消息”可以候选 Lark / WeChat / Email 等通讯能力。
- 高置信候选也不能直接越权执行，仍要进入 Arbiter。

当前落点：

```text
l3_node/cognitive_kernel/semantic_intent_agent.py
l3_node/cognitive_kernel/review_board.py
```

对应自生长意义：

- 系统开始从“固定状态机”升级为“候选理解 + 证据排序 + 门控执行”。
- 错误识别可以被用户纠正，并在下一轮通过统一记忆反哺候选排序。

### 10.3 用户确认后的纠错写入统一记忆

已实现能力：

- 低置信或歧义实体不会静默乱执行，而是生成候选澄清。
- 用户确认后，纠错会写入统一记忆体系，而不是只存在当前对话。
- 下次遇到相似输入时，纠错记忆会成为高优先级证据。

适用例子：

```text
用户说：打开 lock
系统候选：你是不是要打开 Lark？
用户回复：是
系统执行：打开 Lark
记忆写入：在类似上下文中 lock 更可能指 Lark
```

对应自生长意义：

- 用户纠错成为系统生长的燃料。
- 不是简单 alias 表补丁，而是进入 MemoryRecall / ReviewBoard / Arbiter 的统一证据链。

### 10.4 TaskDecomposer 使用 capability metadata 自动拆 DAG

已实现能力：

- 新增正式 `TaskDecomposerAgent` 路径。
- TaskDecomposer 不再只能靠固定代码拆任务。
- 如果 Skill / MCP manifest 提供 `decomposition.nodes`，系统会按 metadata 自动生成 Task DAG。
- DAG 节点包含 goal、role_agent、tool/capability、inputs、depends_on、risk_level、verification_criteria、recovery_policy。
- 没有 metadata 的能力仍可走保守 fallback。

当前落点：

```text
l3_node/cognitive_kernel/task_decomposer.py
docs/12_task_decomposer_agent_architecture.md
```

对应自生长意义：

- 每个能力可以自描述“我应该如何被拆成任务”。
- 后续新增 Skill/MCP 时，主流程不需要反复新增硬编码拆解逻辑。

### 10.5 任务经验、工具习惯和失败模式自动回流

已实现能力：

- 新增 `TaskMemory` 构建器。
- 每个直接执行的 WorkOrder 结束后，会根据 DecisionContract、WorkOrder、VerificationReport 和最终回复生成经验记忆。
- 经验记忆通过 `TurnClosure -> MemoryWriteAgent -> memory_lifecycle` 进入统一记忆系统。

写回类型：

| 类型 | memory_type | 触发条件 | 用途 |
| ---- | ----------- | -------- | ---- |
| 任务摘要 | `historical_task_summary` | 每个真实任务结束 | 支持继续任务和历史回放 |
| 工具习惯 | `tool_habit` / `capability_usage` | 工具执行并验证成功 | 下次类似任务优先选择已验证路径 |
| 失败经验 | `failure_hint` | WorkOrder 或验证失败 | RecoveryPlanner 避免重复失败 |

当前落点：

```text
l3_node/cognitive_kernel/task_memory.py
l3_node/cognitive_kernel/direct_mainline.py
l3_node/cognitive_kernel/runtime.py
l3_node/cognitive_kernel/memory_recall_agent.py
l3_node/cognitive_kernel/memory_confidence.py
```

对应自生长意义：

- 成功不是只返回“完成了”，而是沉淀为工具经验。
- 失败不是只返回“失败了”，而是沉淀为下次恢复路径的反证。
- 历史任务摘要可以反哺“继续刚才”“上次做到哪一步”这类长期任务。

### 10.6 当前验证状态

已通过核心测试：

```powershell
python -m pytest -o addopts= tests\unit\test_cognitive_kernel_architecture.py tests\unit\test_cognitive_kernel_runtime.py
```

结果：

```text
66 passed, 4 warnings
```

覆盖内容：

- Capability manifest 进入语义候选。
- lock -> Lark 等多候选排序。
- manifest metadata 驱动任务 DAG。
- TaskMemory 成功/失败经验生成。
- lifecycle memory 中召回 `historical_task_summary`、`tool_habit`、`failure_hint`。

### 10.7 仍需继续升级的部分

下一步应继续补齐：

1. **Memory Growth UI**
   - 在控制台展示任务经验、工具成功率、失败模式、用户纠错、最近任务摘要。

2. **Daily / Weekly Review 与 TaskMemory 打通**
   - 当前任务经验已经进入 lifecycle memory。
   - 下一步要让 DailyReviewAgent 扫描这些经验，自动生成 concepts patch 和 playbook patch。

3. **RecoveryPlanner 更深使用失败经验**
   - 失败经验已经可写入和召回。
   - 下一步要让 RecoveryPlanner 每次选择 B/C/D 路径时显式引用历史失败原因。

4. **Capability Manifest 质量门槛**
   - 发布 Skill/MCP 时应校验 intent metadata、decomposition metadata、recovery playbook metadata。
   - 不合格能力不应进入 L1 或本地安装中心。

5. **输出回流**
   - Lark 消息、报告、PMO 战报、调试总结等输出还要稳定写入 `memory_growth/outputs/`。
   - 优秀输出应进入 examples 或 playbook section。

本轮完成后，Jachin 的自生长知识系统已经从“记录事件”进一步推进到“任务经验可写回、可召回、可参与下一轮决策”。下一阶段重点是把这些经验从 lifecycle memory 继续消化为 Concepts 和 Playbooks。
