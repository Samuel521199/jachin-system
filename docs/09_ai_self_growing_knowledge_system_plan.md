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

## 11. 后续主线：从可验证闭环 MVP 走向稳定自生长记忆系统

基于当前测试报告，Jachin 记忆系统已经达到“可验证闭环 MVP / 准 Beta 级”：它已经高于普通上下文缓存和简单 RAG，具备 TurnClosure、MemoryWrite、Recall、DailyReview、Recovery、live-confirmed 等闭环能力。下一阶段主线不再是证明“有没有记忆”，而是证明“记忆是否稳定、可靠、越用越强”。

后续主线分为五条，后续开发按这五条持续推进，每一条都必须形成可测试、可回放、可量化的验收结果。

### 11.1 主线一：记忆质量治理

目标：

- 让长期记忆不只是越积越多，而是越用越准。
- 建立置信度、冲突、陈旧、污染、人工确认队列。
- 防止错误纠错、过期路径、错误联系人、失效项目路径污染后续任务。

核心任务：

1. 增加记忆置信度衰减机制。
2. 增加冲突检测和冲突合并队列。
3. 增加陈旧记忆清理，例如 App 路径、项目路径、联系人别名、窗口标题。
4. 增加高价值记忆评分：能减少失败、减少澄清、跨任务复用的记忆优先沉淀为 playbook。
5. 增加需要用户确认的知识队列，尤其是联系人、危险操作习惯、业务规则和项目路径。

验收标准：

- 相同错误纠错连续失败后，系统能自动降权并重新询问用户。
- 同一对象存在多个冲突事实时，不直接覆盖，而是进入冲突队列。
- 陈旧记忆不会继续驱动真实执行。
- Evidence 中能看到某条记忆为什么被使用、为什么被降权、为什么需要确认。

### 11.2 主线二：真实任务压力测试

目标：

- 把记忆能力放进真实 OS workflow 中验证，而不只停留在单元测试。
- 用真实成功和失败样本喂给 Memory Growth 和 RecoveryPlanner。
- 证明系统可以在多轮真实任务中保持稳定，不虚假执行，不污染记忆。

核心任务：

1. 扩大 live-confirmed 场景：App open/switch/close、文件 read/open/reveal/write、Lark 多对象发送、计算器视觉校验。
2. 故意制造失败：关闭 Lark、移动文件、改联系人名、打开错误窗口、断网、窗口被遮挡。
3. 增加连续任务压测：同一个用户连续 50-100 轮任务，观察记忆是否膨胀、污染或错误泛化。
4. 增加跨天测试：今天学到的纠错、工具习惯、最近任务，明天是否仍然能正确召回。
5. 每次真实任务都必须写 Evidence、Verification、TurnClosure 和必要的 failure_hint。

验收标准：

- live-confirmed 任务必须真实执行并有截图/OCR/API/文件证据。
- 失败不能被报告成成功。
- 连续失败后 RecoveryPlanner 必须逐步改变策略，而不是重复同一路径。
- 压测报告能统计成功率、失败原因、恢复次数和记忆命中情况。

### 11.3 主线三：Memory Growth 自生长

目标：

- 让 raw evidence 不只是日志，而是能定期消化为 Concepts、Playbooks、Outputs。
- 把成功经验沉淀为可复用流程，把失败经验沉淀为 recovery playbook。
- 形成“原始证据 -> 概念 -> 方法论 -> 输出 -> 回流”的知识循环。

核心任务：

1. DailyReview 按天扫描 raw evidence，生成 concepts patch、playbook patch、output review。
2. WeeklyReview 合并重复概念，降权陈旧事实，提升高价值 playbook。
3. 将高频成功路径沉淀为 workflow playbook。
4. 将高频失败路径沉淀为 recovery playbook。
5. 输出回流：Lark 消息、项目报告、PMO 战报、调试总结、工作记录都进入 outputs，并在复盘时反哺 Concepts 和 Playbooks。

验收标准：

- 每天能生成 review artifact。
- 每个 concept / playbook 都有 source_refs，可追溯到 raw evidence。
- 高质量输出能进入 outputs，并被后续任务召回。
- RecoveryPlanner 能显式引用从失败样本沉淀出的 recovery playbook。

### 11.4 主线四：可视化指标

目标：

- 让用户和开发者看得见记忆系统是否真的变强。
- 把 Evidence、ledger、memory growth、recovery 结果变成可观察指标。
- 从“跑完看 JSON”升级为“控制台能看趋势、风险和收益”。

核心任务：

1. 增加记忆命中率：每个任务用了哪些记忆，命中后是否提高成功率。
2. 增加恢复收益：哪些 failure_hint 或 playbook 真的帮助 Recovery 成功。
3. 增加污染监控：哪些记忆被多次使用后导致失败，需要降权或删除。
4. 增加 7/14/30 天趋势：记忆数量、有效率、冲突数、陈旧数、确认队列。
5. 增加治理动作归因：哪些治理动作最有效，哪些反复失败。

验收标准：

- 控制台能看到记忆增长趋势。
- 控制台能看到冲突、陈旧、失败模式聚合。
- 控制台能看到“需要用户确认的知识”队列。
- 控制台能看到每次 Weekly Review 的治理效果评分和趋势归因。

### 11.5 主线五：安全边界

目标：

- 让记忆越用越强，但不能因为记住用户习惯而越权执行危险动作。
- 对真实发送、删除、覆盖、批量移动、外部发布等动作保持明确安全边界。
- 让记忆写入本身也有风险等级，而不是所有记忆都同等可信。

核心任务：

1. 对真实发送、删除、覆盖、批量移动继续强制 Evidence 和白名单/确认策略。
2. 记忆写入分风险等级：用户偏好、联系人映射、业务规则、危险操作习惯不能同等对待。
3. 一次确认后自动执行的记忆必须有失效期。
4. 高风险记忆必须支持撤销、降权和人工确认。
5. 任何外部副作用任务必须保证 Verification 不通过就不能报告成功。

验收标准：

- Lark 真实发送只能在明确授权或白名单策略下执行。
- 删除、覆盖、批量移动等高危动作必须进入确认门控。
- 高风险记忆不会因为一次确认而永久生效。
- 如果工具没有产生真实证据，系统必须报告未通过，而不是虚假成功。

### 11.6 主线推进顺序

建议后续按以下顺序执行：

1. 先做记忆质量治理，避免错误记忆继续污染系统。
2. 再做真实任务压力测试，用真实成功/失败样本喂给系统。
3. 然后增强 Memory Growth 自生长，让样本沉淀为 Concepts 和 Playbooks。
4. 同步建设可视化指标，让每次升级都能被观察和评估。
5. 最后持续加强安全边界，确保系统越智能越可控。

阶段性目标：

```text
当前：可验证闭环 MVP / 准 Beta 级，约 70% - 75%
下一阶段：稳定闭环 Beta，目标 80% - 85%
再下一阶段：真实任务持续学习 Beta+，目标 90%
最终目标：可运营、可治理、可回放、可控的自生长记忆系统
```

### 11.7 执行记录：主线一第一阶段

执行日期：2026-07-14

本阶段已经开始落地主线一“记忆质量治理”，完成了可运行、可压测、可回放的第一版治理闭环。

已完成内容：

- `memory_lifecycle` 增加统一治理入口 `govern_lifecycle_memories`。
- 增加 `pending_lifecycle_review_items`，用于读取需要人工确认/治理的记忆队列。
- 增加 `memory_quality_snapshot`，用于控制台或后续指标层读取当前记忆质量状态。
- 治理覆盖低置信、失败压力、陈旧未验证、同对象冲突、过期记忆和损坏 JSONL 行统计。
- 治理结果写入 `memory_lifecycle_governance.json`，同时写入 ledger 事件 `memory_lifecycle_governance`。
- 新增单元测试 `tests/unit/test_memory_quality_governance.py`。
- 新增压测脚本 `scripts/memory_quality_governance_stress.py`。

压测结果：

```text
python -m pytest -o addopts= -q tests\unit\test_memory_quality_governance.py tests\unit\test_memory_stress_mvp.py
5 passed

python scripts\memory_quality_governance_stress.py
PASS, elapsed 11 ms
```

压测 Evidence：

```text
output\memory_quality_governance\20260714_172026\memory_quality_governance_stress.evidence.json
```

当前结论：

- 记忆系统已经具备“质量治理闸门”的第一版能力。
- 低质量、冲突和陈旧记忆不会再只靠人工排查，而是可以自动进入待确认队列。
- 后续主线二“真实任务压力测试”可以在这个治理基础上继续扩大真实 OS workflow 验证。

### 11.8 执行记录：主线二第一阶段

执行日期：2026-07-14

本阶段开始落地主线二“真实任务压力测试”。重点不是继续模拟，而是把记忆治理、失败学习、Verification 和 Evidence 放进真实 OS workflow 中验证。

已完成内容：

- `scripts/os_live_stress_matrix.py` 增加治理故障注入场景 `memory_governed_os_workflow_fault_injection`。
- 故障注入模拟 Lark 工具返回 `ok=true/status=queued` 但缺少发送后证据，验证系统不会把虚假成功报告给用户。
- `_dispatch_live_work_order` 增加 TurnClosure 记忆请求真实落盘，确保 `failure_hint` 不只是停留在返回对象。
- live-confirmed 执行前增加鼠标安全角预检，避免真实 UI 自动化被 PyAutoGUI fail-safe 误中断。
- Lark 长中文消息预览校验升级为中文冒号切分 + 重叠 compact anchors，避免输入框换行和 OCR 丢标点造成误判。
- live-confirmed observation 全量落盘到 `*_full_observation.json`，后续排查不再只依赖截断 preview。
- 新增回归测试 `test_lark_long_chinese_message_matches_wrapped_composer_lines`。

本阶段发现并修复的问题：

| 问题 | 影响 | 根因 | 修复 |
| --- | --- | --- | --- |
| 鼠标安全角触发 fail-safe | Neil Lark 真实发送被中断 | 鼠标位于屏幕安全角 | live WorkOrder 前移动鼠标到安全区域并记录 `pointer_preflight` |
| 长中文消息预览误判 | 消息已粘贴但 Verification 判失败 | OCR 换行、中文冒号和长 anchor 组合导致匹配失败 | 中文冒号切分 + 长文本重叠锚点 + 回归测试 |

验证命令：

```powershell
python -m pytest -o addopts= -q tests\unit\test_os_live_stress_matrix.py tests\unit\test_os_assistant_capability.py::test_lark_long_chinese_message_matches_wrapped_composer_lines
python scripts\os_live_stress_matrix.py
python scripts\os_live_stress_matrix.py --live-confirmed --confirmed-lark-recipients "Neil,测试备注冒烟草稿" --confirmed-message "Jachin 记忆治理真实压力测试复测2：验证长中文预览校验、真实发送、文件、计算器和治理链路。"
```

验证结果：

```text
5 passed
12/12 default matrix passed
15/15 live-confirmed matrix passed
```

最终 Evidence：

```text
output\os_live_stress_matrix\20260714_174233\os_live_stress_matrix_20260714_174233.evidence.json
```

本阶段结论：

- 记忆治理已经进入真实 OS workflow，而不是只停留在单元测试。
- 系统可以阻断缺少发送后证据的虚假成功。
- 真实失败可以沉淀为 `failure_hint`，并被后续 Recall/Recovery 使用。
- 陈旧/冲突记忆可以在真实 workflow 压测中进入治理队列。
- Lark 真实发送、文件 reveal/open、计算器视觉校验均能写出可回放 Evidence。

阶段性完成度：

```text
当前整体完成度：约 78% - 82%
主线一：记忆质量治理第一阶段完成
主线二：真实 OS workflow 压测第一阶段完成
```

下一步主线：

1. 扩大 live-confirmed 样本：连续 20-50 轮真实任务，观察记忆是否膨胀、污染或错误泛化。
2. 增加故障类型：窗口遮挡、Lark 未登录、目标联系人变化、文件移动/重命名、网络异常。
3. 让 RecoveryPlanner 显式读取本轮产生的 `failure_hint`，并在下一轮自动改变路径。
4. 把本轮压力测试结果接入控制台趋势指标：成功率、失败类型、恢复次数、记忆命中率、治理队列数量。
5. 将真实成功/失败样本纳入 Daily/Weekly Review，继续沉淀为 Concepts 和 Playbooks。


## 14. Million-scale Memory Recall: Three-layer Architecture

This stage upgrades MemoryRecall into a three-layer retrieval pipeline. The goal is to find useful memories quickly and explainably even when the local memory store contains hundreds of thousands or millions of records.

### 14.1 Layer 1: inverted-index keyword recall

Each memory is indexed by searchable terms from content, tags, memory_type, domain, owner, skill_id, and evidence fields such as governance_key, entity_key, app_key, and project_key.

A query first uses these terms to find candidate memories through the inverted index. This avoids scanning every memory record during user-facing recall.

### 14.2 Layer 2: rule-score coarse ranking

Candidate memories are then ranked with deterministic rules. Positive signals include keyword hit count, confidence, historical hit count, successful feedback, memory layer, and recency. Negative signals include failures, expired status, conflict markers, low confidence, and review_required.

This layer produces a small and reliable coarse-ranked candidate window.

### 14.3 Layer 3: normalized dot-product rerank

The top coarse candidates are converted into stable local normalized hash vectors. The query vector and candidate vectors are L2-normalized, so their dot product is equivalent to cosine similarity.

This rerank only runs on the coarse-ranked window, not on the full memory store. It improves final ordering without turning million-scale recall into a full vector scan.

### 14.4 Current implementation status

- `memory_lifecycle` now uses `inverted-index -> rule-score -> normalized-dot-rerank`.
- Recall ledger records candidate_count, active_candidate_count, rule_scored_count, rerank_window, and hit_count.
- Unit tests cover normalized dot-product math and Evidence reason markers.
- The million-scale recall report is `docs/15_memory_recall_precision_1m_report.md`.

### 14.5 Next steps

1. Move the in-process inverted index to persistent SQLite FTS / BM25.
2. Warm the recall index in the background after L3 startup.
3. Add incremental index updates for memory write, expire, and feedback events.
4. Show query terms, rule score, dot score, confidence, freshness, and governance state in Evidence Console.
5. Add polluted-memory stress tests for wrong paths, wrong contacts, wrong corrections, and bad tool habits.
