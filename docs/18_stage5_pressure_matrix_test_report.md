# Stage 5 压力测试矩阵报告

日期：2026-07-15

## 测试目标

本轮测试面向“失败学习 + 工具质量标准化 + 意图泛化”的工程底座，不做真实窗口点击、不做真实 Lark 发送，而是用单元级压力矩阵持续压测主链路。

核心目标：

1. 用大量自然语言表达验证意图识别是否泛化，而不是只命中固定句式。
2. 用异常工具输出验证质量门禁是否能阻断低质量内容和虚假成功。
3. 用失败原因矩阵验证 FailureLearningLoop 是否能分类失败并生成下一步策略。
4. 验证质量失败能进入 Verification Evidence，并继续写成 failure_hint MemoryWrite 请求。

## 新增测试文件

`tests/unit/test_stage5_pressure_matrix.py`

## 覆盖规模

1. 意图泛化压力矩阵：50 条自然语言表达。
2. 工具质量坏样本矩阵：30 条异常工具输出。
3. 失败学习分类矩阵：21 类失败原因。
4. 质量失败闭环：3 类关键质量失败从 Verification Evidence 进入 FailureLearning MemoryWrite。
5. dry-run WorkOrder DAG：坏搜索、坏 fetch、坏摘要真实进入 Dispatcher -> RoleExecutor -> Verification -> Recovery -> Evidence。

总计：103 条测试用例。

## 意图泛化覆盖

覆盖类型：

1. 普通 Lark 消息：给 Neil 发消息、通知 Neil、同步给 Neil、告诉 Neil。
2. Web Research Delivery：搜索/查一下/找找网上/看看最新资讯/检索新闻，然后发送给 Neil。
3. AppControl：打开、启动、运行、切换、关闭、退出。
4. Calculator：打开计算器并计算、calc 表达式、中文算式。
5. File Operation：读取文件、打开所在位置、复制、移动、重命名、删除、show in explorer。
6. 最近动作记忆：用户只说“关闭”时，使用 recent action 中最近打开的 App。

## 工具质量覆盖

覆盖工具：

1. `mcp:tavily_search`
2. `mcp:fetch`
3. `core:web_research_summarize`
4. `mcp:windows_lark_send_message`

异常类型：

1. 搜索结果为空。
2. 搜索结果无 URL。
3. 搜索结果无标题。
4. 工具主动返回失败。
5. fetch 页面为空。
6. fetch 页面内容太短。
7. fetch 返回登录墙、验证码、人机验证、403、Access Denied。
8. 摘要缺少来源链接。
9. 摘要仍是占位文本。
10. 摘要混入 CSS、HTML、JS、undefined 等网页噪声。
11. 摘要存在省略号截断。
12. 摘要句子不完整。
13. 摘要混入 Markdown 嵌套链接、代码块、表格残片。
14. Lark 发送缺少发送后验证。
15. Lark 发送 adapter 失败。
16. Lark 重复发送被跳过。

## 本轮发现并修复的问题

1. Web Research 意图泛化不足
   - 问题：“找找网上关于 Qwen 的新消息，发给 Neil”会被误判成普通 `message_delivery`。
   - 原因：Web Research 触发词缺少“网上 / 找找 / 搜一下 / 新消息”等自然表达。
   - 修复：扩展 ReviewBoard 的中文 Web Research 触发词。

2. 工具质量门禁对搜索结果过于宽松
   - 问题：搜索结果只有 URL、没有标题时没有任何质量提示。
   - 修复：新增 `search_result_titles_missing`，作为质量降级信号。

3. fetch 对登录墙和反爬页面识别不足
   - 问题：长文本的 Access Denied / CAPTCHA / login required 页面可能被当成可读内容。
   - 修复：新增 `fetch_access_or_bot_wall`，并作为阻断项。

4. 摘要对 Markdown/页面残片识别不足
   - 问题：`[### title]([url])`、代码块、表格分隔线等残片可能进入摘要。
   - 修复：新增 `summary_contains_markdown_artifact`，并作为摘要阻断项。

5. 测试口径修正
   - App 打开/关闭/切换在新架构中统一属于 `app_control`，`top_intent` 才是 `open_app / close_app / switch_app`。
   - 本轮测试已按新架构口径校正，不再使用旧任务类型口径。

6. RecoveryPlanner 没有充分吸收“失败发生在哪个工具”
   - 问题：fetch 拿到登录墙 / 人机验证页面后，系统可能优先选择 `regenerate_clean_summary`。
   - 原因：评分器对所有 `tool_quality` 失败都偏向重新生成摘要，没有区分失败工具是 search、fetch 还是 summarize。
   - 修复：恢复评分增加失败工具敏感度。`mcp:fetch` 遇到 `fetch_access_or_bot_wall` 时优先选择 `refetch_sources_for_summary`；只有摘要自身脏、截断或不完整时才优先 `regenerate_clean_summary`。

## dry-run WorkOrder DAG 压测

新增测试：`test_stage5_dry_run_dispatcher_dag_records_recovery_evidence_and_failure_memory`

压测链路：

1. `mcp:tavily_search`
   - 第一次返回空搜索结果。
   - Verification 标记 `search_results_missing`。
   - RecoveryPlanner 选择 `retry_search_with_clean_query`。
   - 第二次返回带标题和 URL 的搜索结果，通过。

2. `mcp:fetch`
   - 第一次返回 Access Denied / JavaScript 登录墙。
   - Verification 标记 `fetch_access_or_bot_wall`。
   - RecoveryPlanner 选择 `refetch_sources_for_summary`。
   - 第二次返回可读正文，通过。

3. `core:web_research_summarize`
   - 第一次返回 Markdown 嵌套链接和省略号截断。
   - Verification 标记 `summary_contains_markdown_artifact` / `summary_has_ellipsis_truncation`。
   - RecoveryPlanner 选择 `regenerate_clean_summary`。
   - 第二次返回完整句子和来源链接，通过。

该测试确认：

- 失败不是停在单函数判断，而是进入 Dispatcher。
- RoleExecutor 会写 `role_execution_started / role_execution_finished`。
- Verification 会写 `tool_quality` Evidence。
- RecoveryPlanner 会写 `recovery_attempt_planned / recovery_execution_started / recovery_execution_finished`。
- FailureLearningLoop 会写 `failure_learning_recorded`，并生成 `failure_hint` MemoryWrite。

## Evidence Console 回放接入

本轮继续把 dry-run DAG 产生的 ledger 接入控制台回放链路：

1. `os_evidence_list` 现在会从普通 OS evidence 和 Cognitive Kernel ledger 中递归提取 `failure_learning_records`。
2. Evidence Console 新增 `Failure Learning Memory` 区块，展示失败类别、失败工具、Role Agent、尝试次数、下一步策略、MemoryWrite 类型、置信度、TTL 和证据摘要。
3. 同一条 Evidence 详情页现在可以连续看到：
   - `Tool Quality Gate`：工具输出为什么被拦截。
   - `Adaptive Recovery Scorecard`：为什么选择某条恢复路径。
   - `Failure Learning Memory`：失败如何被写成后续可复用记忆。
   - `Cognitive Kernel Role Execution`：哪个 Role Agent 执行了什么。

这意味着 dry-run 压测不再只是测试文件通过，而是能在 Evidence Console 中回放“失败 -> 质量判定 -> 恢复选择 -> 失败学习”的完整因果链。

## Evidence Console 治理汇总

在单条详情回放之外，Evidence Console 已新增质量治理汇总视图：

1. 质量治理总览：展示质量报告数量、阻断率、恢复候选数、失败学习记录数。
2. 低质量工具 Top：统计哪些工具最常产生质量问题。
3. 质量问题 Top：统计最常见的质量问题，例如搜索结果缺标题、fetch 被登录墙阻断、摘要混入 Markdown 残片。
4. 失败学习类型 Top：统计失败如何被归类，例如 `tool_quality_failed`、`timeout_or_connection`、`target_not_found`。
5. 恢复策略 Top：统计 RecoveryPlanner 实际选择或评分过的恢复策略。
6. 记忆写入类型 Top：统计失败学习最终沉淀成哪些 MemoryWrite 类型。

该视图用于回答“系统整体哪里最弱”“哪个工具最不稳定”“哪类恢复策略最常被使用”，为后续工具治理和 Capability Manifest 质量标准化提供可视化依据。

## 7/14/30 天趋势与 Capability 筛选

Evidence Console 继续增加治理趋势能力：

1. Evidence 列表读取上限提升到 300 条，避免治理面板只看很小窗口。
2. 新增 7 天、14 天、30 天趋势卡，分别展示质量报告数、阻断率、恢复候选数、失败学习数。
3. 新增 Capability / Workflow 筛选，下拉项从 evidence 中自动提取：
   - `workflow_composition.selected_capability_id`
   - `capability_semantic.selected.id`
   - `workflow_id`
   - `intent.task_type`
4. 治理总览会显示当前统计口径：时间窗口、能力维度、参与统计的 evidence 数量。

这让后续排查不再只看“最近一次为什么失败”，而是可以看“某个能力在 7/14/30 天内是否长期低质量”。

## 持久化治理索引

治理数据已从“前端临时聚合”升级为“后端生成并落盘的治理索引”：

1. 新增 Tauri 命令 `os_evidence_governance_index`。
2. 该命令读取普通 OS Evidence 和 Cognitive Kernel ledger，统一生成治理索引。
3. 索引会写入 `output/os_evidence_governance_index.json`。
4. 索引内容包含：
   - 生成时间、来源 evidence 数量、读取上限。
   - Capability / Workflow 选项及对应数量。
   - 7/14/30 天窗口下的全局治理统计。
   - 7/14/30 天窗口下的每个 Capability / Workflow 治理统计。
5. Evidence Console 现在优先读取后端治理索引，失败时才退回本地临时聚合。

这一步的意义是：治理指标不再只是页面状态，而是可以被后续测试、脚本、周报和长期质量治理复用。

## 能力健康评分与自动治理建议

在持久化治理索引之上，本轮继续新增 Capability Health：

1. 后端治理索引新增 `health` 数组，为每个 7/14/30 天窗口和每个 Capability / Workflow 生成健康评分。
2. 评分范围为 0-100，核心依据包括：
   - 质量阻断率。
   - 是否存在恢复候选。
   - 是否写入失败学习记忆。
   - Evidence 样本量是否足够。
   - 是否存在重复高频质量问题。
3. 健康等级分为：
   - `healthy`：当前能力稳定。
   - `watch`：需要观察。
   - `degraded`：已降级，需要治理。
   - `critical`：高风险，需要优先修复。
   - `no_data`：样本不足，暂时无法评价。
4. 每个健康评分会附带自动治理建议，例如：
   - 阻断率高时，建议补充 verification_contract 或替换执行路径。
   - 有阻断失败但没有恢复候选时，建议补充 recovery_playbook。
   - 有失败但没有失败学习时，建议检查 VerificationReport -> FailureLearningLoop 链路。
   - 高频质量问题反复出现时，建议升级为专项门禁。
5. Evidence Console 已新增“能力健康评分”卡片：
   - 选择具体 Capability 时展示该能力的分数、等级、阻断率、恢复密度、学习密度和治理建议。
   - 选择“全部能力”时展示最低分能力和后续风险能力。

这一步让治理面板从“展示统计”升级为“告诉开发者下一步该修哪里”。

## 健康评分进入运行时策略

本轮继续把健康评分从“页面展示”推进到真实 Cognitive Kernel 主链路：

1. 新增 `CapabilityGovernancePolicy`，统一读取 `output/os_evidence_governance_index.json` 中的 Capability Health。
2. Arbiter 在生成 DecisionContract 后会消费治理策略：
   - `score < 50` 或 `critical` 时，进入 `manual_review`，需要用户确认后再执行。
   - `score 50-69` 时，进入 `degraded_auto`，仍可自动执行，但风险等级和恢复偏好会被调整。
   - `score 70-84` 时只观察。
   - `score >= 85` 正常执行。
3. TaskDecomposer 会把治理策略写入每个 WorkOrder 的 `inputs.governance_policy` 和 `recovery_policy.governance_policy`。
4. Dispatcher 在执行前再次检查 WorkOrder 治理策略，避免绕过 Arbiter 的低健康能力直接执行。
5. RecoveryPlanner 会把治理策略加入候选路径评分：
   - 低健康分时，对 `retry_same_path` 扣分。
   - 对 `switch / regenerate / clean / normalize / fallback / fetch` 等替代路径加分。
   - 极低健康分时偏向人工复核路径。

这一步让系统从“发现低质量”进入“运行时主动治理”：低健康能力会被降权、复核或优先切换路径。

## 发布 / 安装质量门禁

本轮继续把治理策略前移到 Capability 生命周期入口：

1. 安装中心后端新增 manifest contract 质量检查：
   - `id / version / decomposition / recovery_playbook` 等硬错误会阻断安装。
   - 缺少 decomposition、recovery_playbook、依赖声明、描述或 tier 会生成 warning，并降低质量分。
   - 安装结果和扫描列表都会返回 `quality_score`、`production_ready`、`governance_status` 和 warnings。
2. L1 catalog 若返回质量字段，安装中心会直接展示；若本机已安装能力只有本地 manifest，则从本地 manifest 重新计算。
3. Capability Install Center 新增“质量”列和“需治理”筛选：
   - `production_ready=false` 显示为“需治理”。
   - 低质量能力可以安装测试，但不会被误展示成生产级能力。
4. Capability Publish 页面强化生产级提示：
   - 质量分低于阈值时显示“需治理”和 warning。
   - 发布按钮文案变为“带警告发布”，避免开发者无感上架低质量 Skill/MCP。

这一步让治理链路覆盖“开发者发布 -> 用户安装 -> 运行时执行 -> Evidence/Recovery”的完整闭环。

## live-confirmed Dispatcher 治理压测

本轮继续把治理压测从 dry-run DAG 推进到真实 Dispatcher / RoleExecutor 链路，但底层 executor 使用可控模拟器，避免真实发送消息或操作桌面窗口造成副作用。

新增测试：`tests/unit/test_live_confirmed_governance_matrix.py`

覆盖场景：

1. 低健康 AppControl 能力：
   - WorkOrder 带 `governance_policy.score=42`、`execution_mode=manual_review`。
   - Dispatcher 在调用 executor 前阻断。
   - 不产生 `role_execution_started`。
   - 写入 `failure_learning_recorded`。
   - 返回需要用户确认的 DecisionContract。
2. degraded AppControl 能力：
   - 初始路径返回 `app_focus_failed`。
   - RecoveryPlanner 读取 inline capability recovery path。
   - 因治理分较低，优先选择 `switch_to_window_focus`，而不是继续盲目同路径重试。
   - 第二次尝试通过验证。
   - Ledger 写入 `recovery_attempt_planned`、`recovery_execution_started`、`recovery_execution_finished`。
3. Lark 消息发送缺少发送后证据：
   - executor 返回 `ok=true`，但没有 `message_id / screenshot / OCR / post_send_verified` 等证据。
   - Verification 判定 `message_post_send_verification_missing`。
   - Tool Quality 标记 `message_post_send_unverified`。
   - 写入失败学习，避免再次把“无证据成功”当成真成功。
4. RoleExecution evidence 已显式携带 `governance_policy`，Evidence Console 可直接展示治理分、等级和执行模式。

这一步验证了真实主链路中：

`WorkOrder -> RoleExecutor -> Verification -> Recovery -> FailureLearning -> Evidence`

已经能消费治理策略，而不是只在页面或单元规则里展示治理结果。

## 验证结果

运行命令：

```powershell
pytest -q -o addopts= tests\unit\test_stage5_pressure_matrix.py
```

结果：

```text
103 passed
```

## 当前结论

Stage 5 的单元级压力矩阵已经跑通。当前系统在以下方面明显增强：

1. 意图识别不再只靠少数固定句式，能覆盖更多自然表达。
2. Web Research Delivery 的识别更稳，尤其是“找找网上 / 最新消息 / 发给 Neil”这类真实办公表达。
3. 工具质量门禁更严格，能拦住搜索、fetch、摘要、Lark 发送中的低质量输出。
4. 质量失败不只停留在错误文本，会进入 Verification Evidence 和 FailureLearning MemoryWrite。
5. AppControl 的任务类型口径已经和新架构统一。
6. dry-run WorkOrder DAG 已能证明坏输出会真实进入 Dispatcher、触发 Recovery，并写入失败记忆。
7. 治理索引已能生成能力健康分和自动治理建议，帮助判断哪些能力应该补验证、补恢复、补失败学习或降低自动执行信任。
8. 健康分已经进入 Arbiter、TaskDecomposer、Dispatcher 和 RecoveryPlanner，低健康能力不再只是页面告警，而会影响确认、降权和恢复路径选择。
9. 发布台和安装中心已能显示生产级状态和治理 warning，低质量能力不会再静默上架或静默安装成“看起来正常”的能力。
10. live-confirmed 风格压测已证明治理策略进入真实 Dispatcher / RoleExecutor 链路：低健康能力会在执行前被阻断，degraded 能力会按失败原因切换路径，缺少发送后证据的消息不会被判定为成功。

## 仍需加强

1. 需要把 Evidence Console 回放从 dry-run ledger 继续扩展到真实 executor 的 live-confirmed 任务。
2. 需要为更多工具族建立质量门禁，例如文件 open/reveal、计算器视觉校验、浏览器窗口控制、微信/WPS/Mac App。
3. 当前质量规则主要是规则型，应逐步接入 Capability Manifest 的工具质量契约，让每个 MCP/Skill 自己声明质量标准。
4. 失败学习还需要结合历史失败记忆做长期统计，判断哪些工具长期低质量、哪些 recovery 路径最有效。
5. 当前已具备控制台治理汇总入口、7/14/30 天趋势、capability 维度筛选、持久化治理索引、能力健康评分、自动治理建议、运行时降权、发布/安装阶段质量提示，以及 Dispatcher live-confirmed 风格治理压测。下一步需要继续扩大到更多真实工具族：文件 open/reveal、浏览器 fetch/search、计算器视觉校验、Lark 实发 dry-run/白名单实发、Mac AppControl。
