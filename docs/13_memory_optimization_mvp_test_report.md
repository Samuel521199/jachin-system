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

## 9. OS Live Stress Matrix 补充验证

本轮新增 `scripts/os_live_stress_matrix.py`，用于把记忆能力放进更接近真实 OS 助手的链路中验证。默认模式不触碰桌面，只验证规划、学习、阻断、恢复和证据生成；`--live-safe` 模式会桥接安全的真实 Windows 能力探针，Lark 仍然保持 dry-run，不会真实发送消息。

### 9.1 新增脚本

```text
scripts/os_live_stress_matrix.py
tests/unit/test_os_live_stress_matrix.py
```

脚本覆盖：

- 常用 App 泛化：WeChat、Chrome、Edge、Excel、WPS、Cursor、VS Code。
- 用户指点后学习：`lock -> Lark` 被确认后，`lok` 这类相似误识别能直接命中 Lark，不再反复追问。
- 负反馈回滚：已学会的纠错如果连续执行失败，会重新进入 review_required，不再盲目自动执行。
- 省略指令记忆：用户只说“close”时，结合最近打开 App 记忆，优先关闭最新目标，而不是误关旧目标。
- 长历史压力：120 条 recent action 中仍能选中最新 App，而不是被旧历史污染。
- 计算器复合任务拆解：`open calculator and calculate 99+100` 拆成打开 Calculator + 执行计算两个 WorkOrder。
- Lark 消息任务拆解：`send to Neil: hello` 拆成打开 Lark + MessageExecutorAgent 发送。
- 缺槽阻断：`open L A R K send message` 没有收件人或内容时，不允许虚假执行，必须澄清。
- 文件任务规划：read / open / reveal 三类文件操作分别路由到对应工具。
- Recovery 极端场景：连续失败达到最大尝试次数后停止盲目重试，并输出失败摘要。
- 存储韧性：lifecycle memory JSONL 混入坏 JSON 行后仍能忽略坏行并召回有效记忆。

### 9.2 Dry-run 结果

命令：

```powershell
python scripts\os_live_stress_matrix.py
```

结果：

```text
11/11 passed
```

Evidence：

```text
output\os_live_stress_matrix\20260714_161329\os_live_stress_matrix_20260714_161329.evidence.json
```

### 9.3 Live-safe 结果

命令：

```powershell
python scripts\os_live_stress_matrix.py --live-safe
```

结果：

```text
12/12 passed
```

Evidence：

```text
output\os_live_stress_matrix\20260714_161342\os_live_stress_matrix_20260714_161342.evidence.json
```

关键通过项：

| 类别 | 场景 | 结果 |
| --- | --- | --- |
| planning | common_apps_generalize | 通过 |
| learning | guided_app_correction_generalizes | 通过 |
| learning | negative_feedback_reopens_review | 通过 |
| memory | close_uses_latest_recent_app | 通过 |
| memory | close_uses_latest_under_long_recent_history | 通过 |
| workflow | calculator_open_then_calculate_dag | 通过 |
| workflow | lark_message_open_then_send_slots | 通过 |
| safety | missing_message_slots_block_execution | 通过 |
| workflow | file_read_open_reveal_planning | 通过 |
| recovery | attempt_limit_and_failure_summary | 通过 |
| resilience | lifecycle_store_corrupt_line | 通过 |
| live_safe | capability_live_matrix_bridge | 通过 |

### 9.4 可学习泛化结论

本轮重点补上了“初期不会用，但被用户指点后会慢慢学会”的机制验证：

- 第一次误识别：系统可以提出候选确认。
- 用户确认：纠错写入 durable correction store 和 lifecycle memory。
- 相似输入：后续 `lok` 这类相似拼写/听写结果直接映射到 Lark。
- 成功反馈：纠错记忆会增加置信度。
- 失败反馈：如果相同纠错连续失败，会重新进入 review_required，避免错误记忆永久污染。
- 泛化边界：确认成功的相似拼写可以自动泛化；一旦该记忆被负反馈降权，相似拼写不再自动吞掉，直接回到谨慎确认路径。

这让 App 泛化不只是 alias 表，而是进入统一记忆闭环：用户指点 -> 记忆写入 -> ReviewBoard 召回 -> Arbiter 门控 -> WorkOrder 执行 -> Verification 反馈 -> Memory 再更新。

### 9.5 仍需继续的真实验收

下一阶段可以继续扩大 live-safe 到 live-confirmed：

- 真实打开/关闭更多 App，并验证前台窗口。
- 真实计算器输入表达式，截图/OCR 校验表达式和结果。
- Lark 真实发送只在明确用户授权下开启，并要求发送后 OCR/API 证据。
- 文件 open/reveal 可以在用户允许后真实触发 Explorer。
- 对同一任务连续制造失败，验证 Recovery 是否每次吸收上一轮失败原因后再选择下一步。

### 9.6 Live-confirmed 真实压测结果

本轮在用户明确授权后执行了真实桌面动作，并强制所有步骤进入 `DecisionContract -> WorkOrder -> RoleExecutor -> Verification -> TurnClosure -> MemoryWriteRequest` 链路。

命令：

```powershell
python scripts\os_live_stress_matrix.py --live-confirmed --confirmed-lark-recipients "Neil,测试备注冒烟草稿"
```

结果：

```text
14/14 passed
```

Evidence：

```text
output\os_live_stress_matrix\20260714_162500\os_live_stress_matrix_20260714_162500.evidence.json
```

关键真实动作：

| 类别 | 场景 | 结果 | 证据 |
| --- | --- | --- | --- |
| live_confirmed | Lark 真实发送给 Neil | 通过 | `output\os_live_stress_matrix\20260714_162500\live_confirmed\lark\Neil\` |
| live_confirmed | Lark 真实发送给测试备注冒烟草稿 | 通过 | `output\os_live_stress_matrix\20260714_162500\live_confirmed\lark\测试备注冒烟草稿\` |
| live_confirmed | 文件 reveal/open | 通过 | `output\os_live_stress_matrix\20260714_162500\os_live_stress_20260714_162500_live_confirmed_file_reveal_and_open.evidence.json` |
| live_confirmed | 计算器 91+9 视觉校验 | 通过 | `output\os_live_stress_matrix\20260714_162500\live_confirmed\calculator\20260714_162602_calculator_after_attempt_1.png` |

安全边界：

- `--live-confirmed` 下 Lark 真实发送只允许 `Neil` 和 `测试备注冒烟草稿`。
- 任何白名单外收件人会在工具调用前被脚本层拦截，不会进入 Lark 发送流程。
- 每个真实动作都会写入单独 evidence 文件；失败时会额外生成 `failure_hint` 记忆请求，供后续 RecoveryPlanner 和 MemoryRecallAgent 使用。

## 10. 当前记忆系统水平评估

基于本报告中的单元测试、压力测试、live-safe 和 live-confirmed 真实压测结果，当前 Jachin 记忆系统可以评定为：

```text
整体水平：可验证闭环 MVP / 准 Beta 级
完成度判断：约 70% - 75%
适合场景：内部研发验证、演示、低风险真实办公任务、持续积累失败经验
暂不适合：完全无人值守的高风险生产自动化、大规模多人多设备并发、长期无人维护知识治理
```

### 10.1 已达到的能力水平

1. 已经不是简单上下文缓存，而是具备 `TurnClosure -> MemoryWriteRequest -> lifecycle memory -> Recall -> Recovery` 的闭环。
2. 用户纠错可以被写入统一记忆，并在后续相似输入中影响 ReviewBoard / Arbiter 的判断。
3. 成功任务、失败任务、工具习惯、历史任务摘要、failure_hint 都已经有结构化写入路径。
4. MemoryRecallAgent 可以召回 concepts、playbooks、tool_habits、failure_hints、historical_task_summaries。
5. DailyReview 可以处理 raw evidence、重复数据和坏 JSON，不会因为局部损坏中断整轮复盘。
6. RecoveryPlanner 已经可以吸收失败历史，不再是一次性写死 B/C/D 路径。
7. live-confirmed 压测证明记忆链路可以进入真实 OS 任务，而不只是单元测试里的模拟对象。
8. Lark 真实发送、文件 reveal/open、计算器视觉校验都能产生 Evidence，并进入 TurnClosure。
9. 白名单和失败记忆机制已经具备基本安全边界，避免真实发送误扩散。

### 10.2 当前还不是生产满级的原因

1. 真实失败样本还不够多，Recovery 的策略质量更多来自设计和小样本验证，还没有被大量真实失败训练过。
2. 记忆质量治理还偏规则化，需要继续增强冲突合并、陈旧淘汰、置信度衰减和人工确认队列。
3. 记忆的跨任务迁移仍需扩大验证，例如从 Lark 纠错泛化到微信、浏览器、WPS、文件系统和 Mac App。
4. Evidence 已能落盘，但面向用户/管理员的可视化分析还不够强，需要把成功率、失败类型、记忆命中率、恢复收益做成趋势面板。
5. Memory Growth 已有 raw -> concept/playbook/output 的管线，但高价值知识沉淀还需要更强的质量评分和复盘节奏。
6. 多设备、多 L1 来源、多用户 profile 下的记忆隔离与同步还需要继续做压力测试。
7. 当前记忆能帮助任务执行，但距离“主动发现模式、主动提出改进、主动整理知识资产”的自生长系统还有距离。

### 10.3 后续优化空间

优先级一：记忆质量治理

- 增加记忆置信度衰减：长期未被命中、被失败反馈打脸、与新事实冲突的记忆自动降权。
- 增加冲突合并策略：同一对象出现多个说法时，不直接覆盖，而是进入冲突队列等待证据或用户确认。
- 增加陈旧记忆清理：项目路径、App 路径、联系人别名、窗口标题等容易变化的信息要有过期机制。
- 增加高价值记忆评分：能跨任务复用、能减少失败、能减少用户澄清的问题优先沉淀为 playbook。

优先级二：真实任务压力测试

- 扩大 live-confirmed 场景：App open/switch/close、文件 read/open/reveal/write、Lark 多对象发送、计算器视觉校验。
- 故意制造失败：关闭 Lark、改联系人名、移动文件、打开错误窗口、网络断开，验证 Recovery 是否逐步换路径。
- 增加连续任务压测：同一个用户连续 50-100 轮任务，看记忆是否膨胀、污染或错误泛化。
- 增加跨天测试：今天学到的纠错和工具习惯，明天是否还能正确召回，并能识别已经过期的信息。

优先级三：Memory Growth 自生长

- 将 raw evidence 按天自动复盘，生成稳定概念、失败模式、可复用方法论和输出模板。
- 把高频成功路径沉淀为 workflow playbook，把高频失败路径沉淀为 recovery playbook。
- 输出回流：把生成的报告、消息、总结、修复记录重新进入 raw evidence，形成知识循环。
- 给每条沉淀知识附带来源证据，避免“AI 自己编出来的经验”污染长期记忆。

优先级四：可视化和运营指标

- 增加记忆命中率：每个任务用了哪些记忆、命中后是否提高成功率。
- 增加恢复收益：哪些 failure_hint 真的帮助 Recovery 成功。
- 增加污染监控：哪些记忆被多次使用后导致失败，需要降权或删除。
- 增加 7/14/30 天趋势：记忆数量、有效率、冲突数、陈旧数、用户确认队列。

优先级五：安全与边界

- 对真实发送、删除、覆盖、批量移动等动作继续强制 Evidence 和白名单/确认策略。
- 让记忆写入也分风险等级：用户偏好、业务规则、联系人映射、危险操作习惯不能同等对待。
- 对“用户一次确认后永久自动执行”的记忆增加失效期，避免长期误操作。

### 10.4 结论

当前记忆系统已经达到“能真实参与任务执行，并能被 Evidence 验证”的水平，明显高于普通聊天上下文和简单 RAG。它已经具备自学习、自纠错、自复盘的基础骨架。

下一阶段的目标不再是证明“有没有记忆”，而是证明“记忆是否稳定、是否可靠、是否越用越强”。核心方向是：用更多真实任务和失败样本喂给系统，让 Memory Growth 从日志复盘升级为真正可持续维护的知识资产系统。
