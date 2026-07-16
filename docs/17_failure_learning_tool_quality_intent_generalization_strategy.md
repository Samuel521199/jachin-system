# Jachin 失败学习、工具质量标准化、意图泛化工程战略

本文件定义 Jachin 下一阶段智能底座的三条主线：失败学习、工具质量标准化、意图泛化。目标不是增加更多状态机规则，而是把每次任务执行变成可验证、可学习、可泛化的工程闭环。

## 一、总体目标

1. 失败不再只是报错文本，而是结构化经验：失败类型、工具、证据、重试路径、下次规避策略都要沉淀到记忆。
2. 工具不再只看“有没有返回”，而是统一经过质量评分：证据是否完整、输出是否可信、是否存在截断/占位/噪声/虚假成功。
3. 意图不再只靠固定关键词，而是由用户目标、Capability Manifest、历史记忆、候选工具共同参与判断。
4. 新 Skill/MCP 接入后，优先通过 manifest 声明能力、验证方式和 recovery playbook，不要求每次改主流程代码。

## 二、Stage 1：工具质量门禁

状态：已启动。

代码落点：

- `l3_node/cognitive_kernel/tool_quality.py`
- `l3_node/cognitive_kernel/runtime.py`
- `l3_node/cognitive_kernel/failure_learning_loop.py`

核心动作：

1. 每个 WorkOrder 执行后生成 `ToolQualityReport`。
2. 对联网搜索、网页抓取、摘要生成、Lark 发送等高风险链路设置专属质量规则。
3. 如果摘要含有网页噪声、截断省略号、占位文本、缺少来源链接，直接阻断后续发送。
4. 如果消息发送缺少发送后验证证据，不允许报告“已发送”。
5. 工具质量失败会进入 FailureLearningLoop，形成 `tool_quality_failed` 类型记忆。

验收标准：

- “搜索最新消息并发给某人”不会发送 CSS/乱码/半截话。
- Lark 发送没有 OCR/API/截图证据时不能虚假成功。
- 失败原因中能看到明确的 `tool_quality:*`。

## 三、Stage 2：失败学习升级

目标：

1. 同类失败能被归类，例如窗口没找到、收件人缺失、权限失败、网络超时、工具质量不达标。
2. RecoveryPlanner 每次只选择下一条路径，且选择依据要吸收前面失败的原因。
3. 达到最大重试次数后输出结构化失败报告：尝试了哪些路径、每次失败原因、推荐下一步。

代码落点：

- `l3_node/cognitive_kernel/failure_learning_loop.py`
- `l3_node/cognitive_kernel/recovery_planner.py`
- `l3_node/cognitive_kernel/capability_recovery_registry.py`
- Skill/MCP manifest 的 `recovery_playbook`

验收标准：

- 路径 A 失败后不会机械固定 B/C/D，而是结合 A 的失败原因选择 B。
- B 再失败后，C 的选择会参考 A+B 的失败证据。
- 高风险操作不会自动重试，而是请求确认。

当前进展：

- `CapabilityRecoveryRegistry` 已为每个恢复候选生成 `adaptive_scorecard`，记录当前失败类型、历史失败类型、候选工具、候选策略和加减分原因。
- `RecoveryPlanner` 的候选选择已经使用 scorecard 分数排序，不再只是静态优先级。
- 内置 `web_research_delivery_quality` playbook 已覆盖搜索无来源、抓取不可读、摘要缺来源、摘要含噪声/截断/占位等路径。
- 已增加连续失败测试：摘要缺来源先切到 refetch，refetch 仍失败后再切到 regenerate clean summary。

## 四、Stage 3：意图泛化升级

目标：

1. 用户可以用自然表达触发复杂任务，例如“看看网上 AI 新消息整理发 Neil”。
2. Capability Manifest 参与意图判断：能力描述、输入槽位、例句、工具链、验证方式都能影响候选排序。
3. 用户确认后的纠错会写入统一记忆，下次直接命中。

代码落点：

- `l3_node/cognitive_kernel/review_board.py`
- `l3_node/cognitive_kernel/semantic_intent_agent.py`
- `l3_node/cognitive_kernel/task_decomposer.py`
- `l3_node/cognitive_kernel/memory_recall_agent.py`
- Capability manifest metadata

验收标准：

- “lock” 经用户确认是 Lark 后，下次直接映射 Lark。
- “发 Neil”“给 Neil”“同步给 Neil”都能识别为消息交付。
- “搜索/查/看看网上 + 发给某人”能拆成 web search -> fetch -> summarize -> message delivery。

## 五、Stage 4：统一质量报告与控制台可视化

目标：

1. Evidence Console 展示每个工具的质量评分。
2. 控制台显示失败类型排行、低质量工具排行、最常见 recovery 路径。
3. Capability Install/Publish 页面展示 manifest 质量分，提醒开发者补齐验证和恢复策略。

验收标准：

- 用户能看到“为什么这次没有继续执行”。
- 开发者能看到“这个 Skill/MCP 还不够生产级”的具体原因。

当前进展：

- `os_evidence_list` 已从普通 OS evidence 和 Cognitive Kernel ledger 中递归提取 `tool_quality_reports` 与 `recovery_scorecards`。
- Evidence Console 已新增 `Tool Quality Gate` 区块，展示工具名、质量分、质量等级、是否阻断、问题标签和证据。
- Evidence Console 已新增 `Adaptive Recovery Scorecard` 区块，展示失败类型、候选恢复策略、候选工具、历史失败类型和选择理由。
- `os_evidence_list` 已递归提取 `failure_learning_records`，Evidence Console 已新增 `Failure Learning Memory` 区块，展示失败如何被写成 `failure_hint` 记忆。
- 当前已能让用户在同一个 Evidence 详情里看到“工具为什么被拦截”“系统为什么选择下一条恢复路径”“这次失败沉淀成了什么记忆”。
- Evidence Console 已新增质量治理汇总视图，聚合质量报告数量、阻断率、低质量工具 Top、质量问题 Top、失败学习类型 Top、恢复策略 Top 和记忆写入类型 Top。
- Evidence Console 已新增 7/14/30 天趋势卡和 Capability / Workflow 筛选，可按能力维度观察质量报告、阻断率、恢复候选和失败学习数量。
- 已新增 `os_evidence_governance_index` 后端命令，生成并落盘 `output/os_evidence_governance_index.json`，Evidence Console 优先读取该持久化治理索引。
- 治理索引已新增 Capability Health 评分，按阻断率、恢复候选、失败学习、样本量和高频质量问题生成 0-100 分、健康等级和自动治理建议。
- Evidence Console 已新增“能力健康评分”卡片：选中具体能力时展示该能力治理建议；选择全部能力时展示最低分能力和后续高风险能力。
- 已新增运行时 `CapabilityGovernancePolicy`：Arbiter 会根据健康分调整 DecisionContract，TaskDecomposer 会把治理策略附加到 WorkOrder，Dispatcher 会阻断需要人工复核的低健康能力。
- RecoveryPlanner 已开始消费 WorkOrder 内的治理策略：低健康分能力会降低同路径重试权重，提高切换路径、重新生成、清洗、降级和人工复核路径的权重。
- Capability 发布台 / 安装中心已接入同一套质量语义：
  - 发布台展示质量分、生产级状态和治理警告，低质量能力不会再静默上架。
  - 安装中心展示 `production_ready`、`quality_score`、`governance_status` 和 warnings，可筛选“需治理”能力。
  - 安装包解包时会校验 manifest contract：硬错误阻断安装，缺少 decomposition / recovery_playbook / 依赖声明等问题作为治理 warning 保留。
  - 低质量能力可以安装用于测试，但运行时会被 Arbiter / Dispatcher / RecoveryPlanner 按治理策略降权、复核或切换路径。
- 已新增 live-confirmed 风格 Dispatcher 压测：
  - 低健康能力会在调用 executor 前被阻断并写失败学习。
  - degraded 能力失败后会结合失败原因选择下一条 recovery path。
  - 消息发送如果缺少发送后证据，即使工具返回 `ok=true` 也不会被判定为成功。
  - RoleExecution evidence 显式携带 governance policy，Evidence Console 可直接展示治理分和执行模式。

## 六、Stage 5：压力测试矩阵

目标：

1. 构造大量模糊意图、失败工具输出、低质量摘要、缺证据发送结果。
2. 测试系统是否能正确阻断、重试、学习、泛化。
3. 测试结果写入记忆测试报告。

验收标准：

- 意图泛化测试覆盖至少 50 类说法。
- 工具质量测试覆盖至少 30 类异常输出。
- 失败学习测试覆盖至少 20 类失败模式。
- 所有失败均有 Evidence 和 MemoryWrite 记录。

当前进展：

- 已新增 `tests/unit/test_stage5_pressure_matrix.py`。
- 已覆盖 50 条意图泛化表达、30 条异常工具输出、21 类失败学习分类、3 类质量失败 Evidence -> MemoryWrite 闭环。
- 压测发现并修复了“找找网上关于 Qwen 的新消息，发给 Neil”被误判为普通消息的问题。
- 工具质量门禁新增 `search_result_titles_missing`、`fetch_access_or_bot_wall`、`summary_contains_markdown_artifact`。
- 详细测试结果见 `docs/18_stage5_pressure_matrix_test_report.md`。

## 七、当前阶段结论

当前 Stage 1、Stage 2、Stage 3、Stage 4、Stage 5 均已形成最小可验证闭环：

1. 工具质量门禁已经能阻断低质量摘要、坏搜索结果、坏 fetch 页面和缺验证的 Lark 发送。
2. RecoveryPlanner 已能基于 capability recovery playbook 和连续失败证据生成自适应 scorecard。
3. 意图泛化已经覆盖 Web Research Delivery、Message Delivery、AppControl、Calculator、File Operation 等主线表达。
4. Evidence Console 已能展示工具质量报告和恢复评分依据。
5. 压力测试矩阵已经覆盖 102 条单元级场景。

下一阶段重点应从“规则是否能拦住”升级为“真实执行链路是否稳定”：把压力矩阵扩展到 dry-run / live-confirmed WorkOrder DAG，验证 Evidence Console、FailureLearningLoop、MemoryWrite、RecoveryPlanner 在真实 executor 结果上的端到端一致性。
