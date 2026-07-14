# AI 自生长知识系统执行日志

本文件记录 `docs/09_ai_self_growing_knowledge_system_plan.md` 的分阶段落地情况。每完成一个主线节点，就在这里追加实现状态、验证方式和下一步。

## Node 1: Memory Growth 目录规范

状态：已完成

实现内容：
- 新增运行时 `memory_growth/` 脚手架。
- 标准化 `raw/`、`concepts/`、`playbooks/`、`outputs/`、`reviews/`、`indexes/`、`conflicts/`。
- 新增文档模板目录 `docs/memory_growth_templates/`。
- 新增 raw event schema、concept 模板、playbook 模板、review patch schema。

核心文件：
- `l3_node/cognitive_kernel/memory_growth.py`
- `docs/memory_growth_templates/README.md`
- `docs/memory_growth_templates/raw_event_schema.json`
- `docs/memory_growth_templates/concept_template.md`
- `docs/memory_growth_templates/playbook_template.md`
- `docs/memory_growth_templates/review_patch_schema.json`

验证：
- `python -m py_compile l3_node\cognitive_kernel\memory_growth.py`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py`

## Node 2: TurnClosure 写入 Raw Evidence

状态：已完成

实现内容：
- `TurnClosure` 结束时自动写入 append-only raw JSONL。
- raw event 保留 `turn_id`、closure、WorkOrder 引用、verification 状态和 promotion hints。
- 写入失败不阻断用户任务闭环。

核心文件：
- `l3_node/cognitive_kernel/runtime.py`
- `l3_node/cognitive_kernel/memory_growth.py`
- `tests/unit/test_memory_growth.py`

验证：
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py tests\unit\test_cognitive_kernel_runtime.py`
- `python scripts\smoke_cognitive_kernel_stage_e.py`

真实 smoke 结果：
- raw evidence 生成路径：`output/cognitive_kernel_stage_e_smoke/memory_growth/raw/evidence/20260710.turn_closure.jsonl`
- 已验证 raw evidence 包含 5 条真实任务闭环事件。

## Node 3: Daily Review Agent

状态：已完成第一版

实现内容：
- 新增 `DailyReviewAgent` 确定性消化逻辑。
- 扫描当天 raw JSONL。
- 按 `turn_id` 聚合任务链。
- 统计通过、失败、等待用户、无效 raw 行。
- 从 `memory_write_requests`、verification 状态和 WorkOrder 链抽取候选：
  - `concept_candidates`
  - `playbook_candidates`
  - `output_candidates`
- 只生成 review patch，不直接覆盖 concepts/playbooks。

核心文件：
- `l3_node/cognitive_kernel/daily_review.py`
- `tests/unit/test_memory_growth.py`

验证：
- `python -m py_compile l3_node\cognitive_kernel\daily_review.py`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py tests\unit\test_cognitive_kernel_runtime.py`
- 使用 Stage E smoke 产物运行 `run_daily_review()`。

真实 smoke 结果：
- raw_event_count：5
- task_count：5
- passed_count：5
- failed_count：0
- concept_candidate_count：5
- playbook_candidate_count：5
- output_candidate_count：5
- review 输出：`output/cognitive_kernel_stage_e_smoke/memory_growth/reviews/2026-07-10.md`
- patch 输出：`output/cognitive_kernel_stage_e_smoke/memory_growth/reviews/patches/2026-07-10.daily_review.patch.json`

下一步：
- Node 4：Concept Curator Agent。
- 将 Daily Review patch 合并到 `concepts/`，支持 source refs、confidence、last_verified、conflicts 和降权/过期策略。
- 仍然保留保守边界：低置信度、冲突、需要用户确认的内容进入 `conflicts/` 或 pending，不直接写入稳定概念。

## Node 4: Concept Curator Agent

状态：已完成第一版

实现内容：
- 新增 `ConceptCuratorAgent` 合并逻辑。
- 输入为 Daily Review 生成的 `*.daily_review.patch.json`。
- 高置信、无需用户确认、无冲突的候选写入 `memory_growth/concepts/<type>/*.md`。
- 低置信、需要用户确认、与现有概念冲突的候选写入 `memory_growth/conflicts/<type>/*.json`。
- 每次合并生成 `memory_growth/reviews/concept_merges/*.json` 报告。
- 自动生成 `memory_growth/indexes/concepts.json`。
- 已支持重复合并时追加 stable facts、source evidence 和 update log。

核心文件：
- `l3_node/cognitive_kernel/concept_curator.py`
- `tests/unit/test_memory_growth.py`

验证：
- `python -m py_compile l3_node\cognitive_kernel\concept_curator.py`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py tests\unit\test_cognitive_kernel_runtime.py`
- `python scripts\smoke_cognitive_kernel_stage_e.py`
- 使用 Stage E smoke 产物执行 `run_daily_review()` 后执行 `apply_concept_patch()`。

真实 smoke 结果：
- patch 输入：`output/cognitive_kernel_stage_e_smoke/memory_growth/reviews/patches/2026-07-10.daily_review.patch.json`
- promoted_count：15
- quarantined_count：0
- report 输出：`output/cognitive_kernel_stage_e_smoke/memory_growth/reviews/concept_merges/concept_merge_20260710_094223_1bcd7217.json`

下一步：
- Node 5：Playbook Builder Agent。
- 将 Daily Review patch 中的 `playbook_candidates` 合并到 `memory_growth/playbooks/`。
- 支持 trigger、recommended_flow、verification criteria、failure paths、source refs。
- RecoveryPlanner 和 Arbiter 后续可以读取这些 playbook，而不是每次只靠内置规则。

## Node 5: Playbook Builder Agent

状态：已完成第一版

实现内容：
- 新增 `PlaybookBuilderAgent` 合并逻辑。
- 输入为 Daily Review 生成的 `*.daily_review.patch.json`。
- 高置信、具备 recommended flow 的候选写入 `memory_growth/playbooks/*.md`。
- 低置信、缺少 recommended flow 的候选写入 `memory_growth/conflicts/playbooks/*.json`。
- 每次构建生成 `memory_growth/reviews/playbook_builds/*.json` 报告。
- 自动生成 `memory_growth/indexes/playbooks.json`。
- 已支持重复构建时追加 recommended flow、evidence requirements 和 historical effective cases。

核心文件：
- `l3_node/cognitive_kernel/playbook_builder.py`
- `tests/unit/test_memory_growth.py`

验证：
- `python -m py_compile l3_node\cognitive_kernel\playbook_builder.py`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py tests\unit\test_cognitive_kernel_runtime.py`
- `python scripts\smoke_cognitive_kernel_stage_e.py`
- 使用 Stage E smoke 产物执行 `run_daily_review()` 后执行 `apply_playbook_patch()`。

真实 smoke 结果：
- patch 输入：`output/cognitive_kernel_stage_e_smoke/memory_growth/reviews/patches/2026-07-10.daily_review.patch.json`
- promoted_count：5
- quarantined_count：0
- report 输出：`output/cognitive_kernel_stage_e_smoke/memory_growth/reviews/playbook_builds/playbook_build_20260710_094736_1d991a5f.json`

下一步：
- Node 6：Memory Recall 反哺执行链路。
- 让 MemoryRecallAgent 在任务开始前读取 `concepts/indexes/playbooks`，把高相关 concepts/playbooks 注入 MemoryContext。
- Arbiter / RecoveryPlanner 后续可以引用这些 playbook 生成 DecisionContract 和 RecoveryPlan。

## Node 6: Memory Recall 反哺执行链路

状态：已完成第一版

实现内容：
- 新增 Memory Growth recall adapter。
- 读取 `memory_growth/indexes/concepts.json` 和 `memory_growth/indexes/playbooks.json`。
- 在索引缺失时回退扫描 `concepts/**/*.md` 与 `playbooks/*.md`。
- 将 Concepts 转为 `MemoryEvidence`：
  - project facts 进入 `project_facts`
  - problems/failures 进入 `failure_hints`
  - actions/tools 进入 `tool_habits`
  - 其它稳定概念进入 `historical_task_summaries`
- 将 Playbooks 转为 `MemoryEvidence`：
  - 普通流程进入 `tool_habits`
  - 明确 recovery/failure 类型进入 `failure_hints`
- `MemoryRecallAgent` 新增 retrieval channels：
  - `memory_growth_concept_memory`
  - `memory_growth_playbook_memory`
- 修复旧分类规则：不再把普通 `path=...` 误判为 alias；显式按 `memory_type` 优先分类。
- 对 Memory Growth 来源增加轻量 ranking boost，让本系统沉淀过的经验优先进入上下文。

核心文件：
- `l3_node/cognitive_kernel/memory_growth_recall.py`
- `l3_node/cognitive_kernel/memory_recall_agent.py`
- `tests/unit/test_cognitive_kernel_runtime.py`

验证：
- `python -m py_compile l3_node\cognitive_kernel\memory_growth_recall.py l3_node\cognitive_kernel\memory_recall_agent.py`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py tests\unit\test_cognitive_kernel_runtime.py`
- 使用 Stage E smoke 产物执行真实 recall 抽样。

真实 recall 结果：
- 输入：`send message to Neil and verify the delivery evidence`
- `memory_growth=10`
- `tool_habits=5`
- `failure_hints=5`
- 已召回 `Memory Growth Concepts` 中 message send 相关经验。

下一步：
- Node 7：Arbiter / RecoveryPlanner 使用 Memory Growth playbooks。
- Arbiter 生成 DecisionContract 时引用相关 playbook。
- RecoveryPlanner 失败重试时读取 playbook failure paths，并把每次失败后的原因反馈给下一轮路径选择。

## Node 7: Arbiter / RecoveryPlanner 使用 Memory Growth Playbooks

状态：已完成第一版

实现内容：
- `ReviewBoard` 在 `MemoryRecallAgent` 审查结果中提取 Memory Growth concept/playbook 引用，并写入 `memory_growth_refs` evidence。
- `DecisionContract` 新增 `memory_context_refs`，用于保存本轮决策引用过的自生长知识、方法论和失败处理经验。
- `Arbiter` 汇总 ReviewSummary 时会把 Memory Growth refs 纳入 DecisionContract rationale，避免“记忆被召回但决策不知道依据是什么”。
- `build_work_order_from_decision()` 会把 `memory_context_refs` 注入 WorkOrder inputs，让后续 RoleExecutor、Verification 和 Evidence 都能追踪这次操作参考了哪些经验。
- `pending_confirmation` 已支持序列化/反序列化 `memory_context_refs`，确认/取消流程不会丢失记忆依据。
- `RecoveryPlanner` 在 capability recovery manifest 没有给出可用候选路径时，会读取 DecisionContract 中的 Memory Growth playbook / failure hint。
- RecoveryPlanner 的下一步策略不是预先固定 B/C/D，而是结合当前失败原因和已尝试记录动态选择：
  - timeout / slow / longer timeout -> `memory_growth_longer_timeout`
  - retry / focus / foreground / window -> `memory_growth_retry_same_path`
- `candidate_paths()` 会把 Memory Growth 生成的候选路径纳入快照，Evidence 可以看到“为什么下一次这样重试”。
- `final_failure_report()` 会带上 `memory_context_refs`，方便五次失败后回看所有使用过的经验依据。

核心文件：
- `l3_node/cognitive_kernel/contracts.py`
- `l3_node/cognitive_kernel/review_board.py`
- `l3_node/cognitive_kernel/arbiter.py`
- `l3_node/cognitive_kernel/recovery_planner.py`
- `l3_node/cognitive_kernel/pending_confirmation.py`
- `tests/unit/test_cognitive_kernel_runtime.py`

验证：
- `python -m py_compile l3_node\cognitive_kernel\arbiter.py l3_node\cognitive_kernel\review_board.py l3_node\cognitive_kernel\recovery_planner.py l3_node\cognitive_kernel\contracts.py l3_node\cognitive_kernel\pending_confirmation.py`
- `python -m pytest -o addopts= tests\unit\test_cognitive_kernel_runtime.py::test_arbiter_carries_memory_growth_refs_into_contract_and_work_order tests\unit\test_cognitive_kernel_runtime.py::test_recovery_planner_uses_memory_growth_playbook_when_manifest_has_no_candidate`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py tests\unit\test_cognitive_kernel_runtime.py`

验证结果：
- 新增 Node 7 专项测试：2 passed
- Memory Growth + Cognitive Kernel 回归：33 passed, 5 warnings

下一步：
- Node 8：Output Review / 输出回流与调度闭环。
- 目标是把任务产出的报告、Lark 消息、文件、失败报告重新送回 Growth Inbox，让 AI 自生长系统从“执行证据”继续沉淀为“高价值知识”和“可复用方法论”。
- 同时补一个 Growth Scheduler，把 Daily Review -> Concept Curator -> Playbook Builder -> Recall Feedback 串成可手动触发、可定时触发的后台管线。

## Node 8: Output Review / 输出回流与调度闭环

状态：已完成第一版

实现内容：
- `DailyReviewAgent` 的 `output_candidates` 不再只是“有输出证据”的空壳，已补充：
  - `content`：TurnClosure 最终给用户的输出文本。
  - `verification_status`：执行验证状态。
  - `closure_type`：任务闭环类型。
  - `target_type`：根据任务内容粗分为 `lark_messages`、`reports`、`debug_summaries`、`work_records` 等。
- 新增 `OutputReviewAgent`：
  - 读取 Daily Review patch 中的 `output_candidates`。
  - 将高于阈值的输出写入 `memory_growth/outputs/<category>/*.md`。
  - 空输出跳过，低置信输出写入 `memory_growth/conflicts/outputs/*.json`。
  - 生成 `memory_growth/reviews/output_reviews/*.json` 报告。
  - 自动更新 `memory_growth/indexes/outputs.json`。
- 新增 `GrowthScheduler`：
  - 串联 `DailyReview -> ConceptCurator -> PlaybookBuilder -> OutputReview`。
  - 支持手动触发或未来后台定时触发。
  - 每次运行生成 `memory_growth/reviews/pipeline_runs/*.json`，记录四个阶段的输入、输出、计数和 warning。
- Node 8 之后，Jachin 的输出内容已经能进入 D 层，并在下一轮复盘时重新作为 raw/evidence/output 被消化，形成“输出回流”闭环。

核心文件：
- `l3_node/cognitive_kernel/daily_review.py`
- `l3_node/cognitive_kernel/output_review.py`
- `l3_node/cognitive_kernel/growth_scheduler.py`
- `tests/unit/test_memory_growth.py`

验证：
- `python -m py_compile l3_node\cognitive_kernel\daily_review.py l3_node\cognitive_kernel\output_review.py l3_node\cognitive_kernel\growth_scheduler.py`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py::test_output_review_promotes_user_facing_outputs tests\unit\test_memory_growth.py::test_growth_scheduler_runs_full_pipeline`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py tests\unit\test_cognitive_kernel_runtime.py`

验证结果：
- 新增 Node 8 专项测试：2 passed
- Memory Growth + Cognitive Kernel 回归：35 passed, 5 warnings

下一步：
- Node 9：Weekly Review / 生命周期治理。
- 目标是把每日产出的 concepts、playbooks、outputs 做周级合并、降权、冲突复核和索引质量检查。
- 重点包括：重复概念合并、过期事实降权、失败反模式升级、输出质量评分、以及给 MemoryRecallAgent 提供更干净的长期索引。

## Node 9: Weekly Review / 生命周期治理

状态：已完成第一版

实现内容：
- 新增 `WeeklyReviewAgent`：
  - 扫描 `memory_growth/concepts/`、`playbooks/`、`outputs/`、`conflicts/` 和 `indexes/`。
  - 生成周级生命周期治理报告 `memory_growth/reviews/weekly/<week>.md`。
  - 生成机器可读报告 `memory_growth/reviews/weekly/<week>.weekly_lifecycle.json`。
  - 生成最新生命周期索引 `memory_growth/indexes/weekly_lifecycle.json`。
- 生命周期治理覆盖：
  - 重复概念簇：按 summary fingerprint 找出需要合并或互链的 concepts。
  - 过期事实：根据 `last_verified` / `valid_until` 标记需要复核或降权的概念。
  - 低质量输出：识别缺 source refs、失败状态、内容过短、低置信度的 outputs。
  - 冲突队列：汇总 `conflicts/**/*.json`，发现高频 conflict reason。
  - 失败反模式：把低置信、失败输出、recovery playbook 等汇总成可升级的 failure patterns。
  - 索引质量：检查 concepts/playbooks/outputs 索引缺失、JSON 无效、计数不一致等问题。
- `GrowthScheduler` 已支持 `weekly_lifecycle_review=True`，可以在日级消化后追加周级治理。
- `cognitive_kernel.__init__` 已导出 `run_weekly_review` 和 `run_growth_pipeline`，后续 UI、脚本、任务调度器可以直接调用。

核心文件：
- `l3_node/cognitive_kernel/weekly_review.py`
- `l3_node/cognitive_kernel/growth_scheduler.py`
- `l3_node/cognitive_kernel/__init__.py`
- `tests/unit/test_memory_growth.py`

验证：
- `python -m py_compile l3_node\cognitive_kernel\weekly_review.py l3_node\cognitive_kernel\growth_scheduler.py l3_node\cognitive_kernel\__init__.py`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py::test_weekly_review_detects_lifecycle_issues tests\unit\test_memory_growth.py::test_growth_scheduler_can_include_weekly_lifecycle_review`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py tests\unit\test_cognitive_kernel_runtime.py`

验证结果：
- 新增 Node 9 专项测试：2 passed
- Memory Growth + Cognitive Kernel 回归：37 passed, 6 warnings

下一步：
- Node 10：Graph / Wiki 双向索引适配层。
- 目标是为后续 Cognee / Graphiti 接入预留稳定接口：Markdown Wiki 仍是人可读主存储，Graph/Temporal Index 作为可替换的加速与推理层。
- 第一版不强依赖外部服务，先实现 `GraphSyncAdapter` 的本地 JSONL 事件协议和实体/关系抽取草图，之后再接 Cognee/Graphiti。

## Node 10: Graph / Wiki 双向索引适配层

状态：已完成第一版本地协议

实现内容：
- `memory_growth/` 脚手架新增 `graph/` 目录，用于保存本地图谱同步事件。
- 新增 `GraphSyncAdapter`：
  - Markdown Wiki 仍然是源头：`concepts/`、`playbooks/`、`outputs/`。
  - 从 Wiki 页面 frontmatter、正文、source_refs 中派生 graph nodes / graph edges。
  - 节点类型包括：`concept`、`playbook`、`output`、`category`、`source_ref`。
  - 边类型包括：
    - `BELONGS_TO_CATEGORY`
    - `DERIVED_FROM`
    - `RELATED_BY_KEYWORDS`
  - 写入本地事件流：`memory_growth/graph/events/<date>.graph_sync.jsonl`。
  - 写入索引：
    - `memory_growth/indexes/graph_nodes.json`
    - `memory_growth/indexes/graph_edges.json`
- `GrowthScheduler` 已支持 `sync_graph=True`，可以在日级消化、输出回流后生成图谱同步事件。
- `cognitive_kernel.__init__` 已导出 `sync_memory_growth_graph` 和 `GraphSyncResult`。
- 这一版不绑定 Cognee / Graphiti，先保证 Jachin 自己的 Wiki -> Graph 协议稳定。后续接任意图谱引擎都消费这个事件流。

核心文件：
- `l3_node/cognitive_kernel/graph_sync_adapter.py`
- `l3_node/cognitive_kernel/growth_scheduler.py`
- `l3_node/cognitive_kernel/memory_growth.py`
- `l3_node/cognitive_kernel/__init__.py`
- `tests/unit/test_memory_growth.py`

验证：
- `python -m py_compile l3_node\cognitive_kernel\graph_sync_adapter.py l3_node\cognitive_kernel\growth_scheduler.py l3_node\cognitive_kernel\memory_growth.py l3_node\cognitive_kernel\__init__.py`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py::test_graph_sync_adapter_derives_nodes_and_edges tests\unit\test_memory_growth.py::test_growth_scheduler_can_sync_graph`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py tests\unit\test_cognitive_kernel_runtime.py`

验证结果：
- 新增 Node 10 专项测试：2 passed
- Memory Growth + Cognitive Kernel 回归：39 passed, 5 warnings

下一步：
- Node 11：外部 Graph Engine Connector 抽象。
- 目标是把本地 `graph_sync` 事件流对接到可插拔 connector：
  - `LocalJsonGraphConnector`：当前本地 JSON 索引。
  - `CogneeConnector`：面向长期知识图谱和文档知识库。
  - `GraphitiConnector`：面向时间记忆、事实有效期和“最近发生了什么”。
- 仍然保持 Markdown Wiki 为主存储，外部图谱只做索引、检索和推理增强。

## Node 11: 外部 Graph Engine Connector 抽象

状态：已完成第一版可插拔连接器层

实现内容：
- 新增 `GraphEngineConnector` 协议层。
- 新增 `LocalJsonGraphConnector`：
  - 消费 `graph_nodes.json` / `graph_edges.json`。
  - 写入 `memory_growth/graph/connectors/local_json_graph/latest_snapshot.json`。
  - 作为默认可运行图谱后端，保证无外部服务也能完成闭环。
- 新增 `CogneeConnector`：
  - 检查 `JACHIN_COGNEE_ENDPOINT` / `JACHIN_COGNEE_API_KEY`。
  - 未配置时写 `not_configured` 报告，不阻塞主线。
  - 已配置时进入 `connector_stub_ready`，为后续 HTTP/SDK transport 留接口。
- 新增 `GraphitiConnector`：
  - 检查 `JACHIN_GRAPHITI_ENDPOINT` / `JACHIN_GRAPHITI_API_KEY`。
  - 未配置时写 `not_configured` 报告，不阻塞主线。
  - 已配置时进入 `connector_stub_ready`，为后续 temporal graph transport 留接口。
- 新增 `sync_graph_engine_connectors()`：
  - 自动读取最新本地图谱索引。
  - 如果图谱索引不存在，会先触发 `sync_memory_growth_graph()`。
  - 写入 `memory_growth/indexes/graph_connectors.json`。
- `GrowthScheduler` 已支持：
  - `sync_graph=True`
  - `graph_connector_ids=["local_json_graph", "cognee", "graphiti"]`
  - pipeline report 中记录 connector 成功数、总数和每个 connector 的报告路径。
- `cognitive_kernel.__init__` 已导出：
  - `GraphConnectorResult`
  - `sync_graph_engine_connectors`

核心文件：
- `l3_node/cognitive_kernel/graph_connectors.py`
- `l3_node/cognitive_kernel/growth_scheduler.py`
- `l3_node/cognitive_kernel/__init__.py`
- `tests/unit/test_memory_growth.py`

验证：
- `python -m py_compile l3_node\cognitive_kernel\graph_connectors.py l3_node\cognitive_kernel\growth_scheduler.py l3_node\cognitive_kernel\__init__.py`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py::test_graph_connectors_sync_local_and_report_unconfigured_external_connectors tests\unit\test_memory_growth.py::test_growth_scheduler_can_run_graph_connectors`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py tests\unit\test_cognitive_kernel_runtime.py`

验证结果：
- 新增 Node 11 专项测试：2 passed
- Memory Growth + Cognitive Kernel 回归：41 passed, 4 warnings

下一步：
- Node 12：Memory Growth 控制台 / API 入口。
- 目标是让这套自生长系统不只停在 Python API：增加可调用命令/API，支持手动触发 Daily Pipeline、Weekly Review、Graph Sync、Connector Sync，并能返回报告路径和关键计数。
- 后续控制台页面可以基于这些 API 展示“今天消化了多少原始证据、沉淀了多少概念、生成了多少方法论、图谱同步是否成功”。

## Node 12: Memory Growth 控制台 / API 入口

状态：已完成第一版 L3 HTTP API

实现内容：
- 新增 `memory_growth_http` API 层，避免前端直接导入 Python 内部模块。
- 新增状态查询：
  - `GET /api/v1/memory-growth/status`
  - 返回 `memory_growth/` 根目录、raw evidence、concepts、playbooks、outputs、conflicts、graph nodes、graph edges 等计数。
  - 返回最新 pipeline report、weekly report、graph event、connector index 路径。
- 新增手动触发入口：
  - `POST /api/v1/memory-growth/pipeline`
  - `POST /api/v1/memory-growth/weekly-review`
  - `POST /api/v1/memory-growth/graph-sync`
  - `POST /api/v1/memory-growth/connector-sync`
- Pipeline API 支持参数：
  - `date`
  - `promote_concepts`
  - `build_playbooks`
  - `review_outputs`
  - `weekly_lifecycle_review`
  - `sync_graph`
  - `graph_connector_ids`
- L3 HTTP server 已注册这些 route，后续控制台页面可以直接调用，不再走 Tauri ACL 命令，避免 `command not allowed` 类问题。

核心文件：
- `l3_node/memory_growth_http.py`
- `l3_node/http_server.py`
- `tests/unit/test_memory_growth.py`

验证：
- `python -m py_compile l3_node\memory_growth_http.py l3_node\http_server.py`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py::test_memory_growth_http_registers_routes tests\unit\test_memory_growth.py::test_memory_growth_http_pipeline_endpoint`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py tests\unit\test_cognitive_kernel_runtime.py`

验证结果：
- 新增 Node 12 专项测试：2 passed
- Memory Growth + Cognitive Kernel 回归：43 passed, 5 warnings

下一步：
- Node 13：Memory Growth 控制台页面。
- 目标是在 Jachin 控制台中新增“AI 自生长知识系统 / Memory Growth”页面：
  - 展示 raw evidence、concepts、playbooks、outputs、conflicts、graph nodes、graph edges 统计卡片。
  - 展示最新 pipeline、weekly、graph、connector 报告路径。
  - 提供按钮触发 Daily Pipeline、Weekly Review、Graph Sync、Connector Sync。
  - 后续再接折线图、质量分、失败模式、知识生命周期视图。

## Node 13: Memory Growth 控制台页面

状态：已完成第一版可操作页面

实现内容：
- 新增 `MemoryGrowthPanel` 控制台页面。
- 页面直接调用 Node 12 的 L3 HTTP API：
  - `GET /api/v1/memory-growth/status`
  - `POST /api/v1/memory-growth/pipeline`
  - `POST /api/v1/memory-growth/weekly-review`
  - `POST /api/v1/memory-growth/graph-sync`
  - `POST /api/v1/memory-growth/connector-sync`
- 页面展示统计卡：
  - 原始证据
  - 高价值概念
  - 方法论
  - 输出回流
  - 冲突待审
  - 图谱节点
  - 图谱边
- 页面展示最新报告路径：
  - Pipeline Report
  - Weekly Review
  - Graph Event
  - Connector Index
- 页面提供四个动作按钮：
  - 运行消化管线
  - 周复盘治理
  - 同步本地图谱
  - 同步图谱连接器
- 控制台路由新增：
  - `#/memory-growth`
- 侧边栏新增：
  - `自生长知识`
  - 放入核心中枢分组，作为 Memory Nexus 之后的长期知识治理入口。
- 前端复用现有 `getL3SkillsBaseUrl()`，和 BI、巡检等页面保持同一套 L3 端口探测和 dev proxy 策略。

核心文件：
- `clients/desktop/src/console/pages/MemoryGrowthPanel.tsx`
- `clients/desktop/src/console/routes.tsx`
- `clients/desktop/src/console/Sidebar.tsx`

验证：
- `npx tsc --noEmit`

验证结果：
- TypeScript 校验通过。

下一步：
- Node 14：Memory Growth 可视化与质量监控。
- 目标：
  - 增加最近 7/14/30 天 raw evidence、concepts、playbooks、outputs 趋势。
  - 增加冲突类型、陈旧概念、失败模式聚合视图。
  - 增加“可复用方法论推荐”和“需要用户确认的知识”队列。
  - 让控制台不只是触发按钮，而是能看见知识系统是否真的在自我成长。

## Node 14: Memory Growth 可视化与质量监控

状态：已完成第一版质量监控闭环

实现内容：
- `GET /api/v1/memory-growth/status` 新增 `monitoring` 字段。
- 后端直接扫描 `memory_growth/` 文件系统，生成真实监控数据：
  - `trends.days_7`
  - `trends.days_14`
  - `trends.days_30`
  - `conflict_types`
  - `stale_concepts`
  - `failure_patterns`
  - `pending_confirmation_queue`
  - `health`
- 趋势统计来源：
  - `raw/**/*.jsonl` 按事件 `date` / `ts_ms` / 文件名 / mtime 归档。
  - `concepts/**/*.md`
  - `playbooks/**/*.md`
  - `outputs/**/*.md`
  - `conflicts/**/*.json`
- 冲突类型统计：
  - 聚合 `conflicts/**/*.json` 的 `reason`。
  - 返回 count、latest_path、latest_date。
- 陈旧概念队列：
  - 解析 Markdown frontmatter。
  - 检查 `valid_until` 过期。
  - 检查 `last_verified` 超过 30 天。
  - 无验证字段时使用 mtime 兜底。
- 失败模式聚合：
  - 从 conflict reason 派生失败模式。
  - 从 raw turn closure 中 `verification_status=failed` 派生失败模式。
- 待用户确认队列：
  - 从 `requires_user_confirmation` conflict 派生。
  - 从 raw turn closure 的 `pending_decision` 派生。
- 控制台页面升级：
  - 增加 7/14/30 天趋势折线图。
  - 增加冲突类型列表。
  - 增加失败模式列表。
  - 增加风险等级、陈旧数、待确认数。
  - 增加待确认知识队列。
  - 增加陈旧概念队列。

核心文件：
- `l3_node/memory_growth_http.py`
- `clients/desktop/src/console/pages/MemoryGrowthPanel.tsx`
- `tests/unit/test_memory_growth.py`

验证：
- `python -m py_compile l3_node\memory_growth_http.py`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py::test_memory_growth_http_status_includes_quality_monitoring`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py tests\unit\test_cognitive_kernel_runtime.py`
- `npx tsc --noEmit`

验证结果：
- Node 14 专项测试：1 passed
- Memory Growth + Cognitive Kernel 回归：44 passed, 6 warnings
- 前端 TypeScript 校验通过

下一步：
- Node 15：Memory Growth 人机协同治理动作。
- 目标：
  - 在控制台中为“待用户确认知识”提供确认、拒绝、稍后处理动作。
  - 为“陈旧概念”提供重新验证、归档、保留并延长有效期动作。
  - 为“失败模式”生成可执行 recovery/playbook 改进建议。
  - 所有治理动作都必须写 raw evidence 和 review report，形成“治理行为也进入记忆”的闭环。

## Node 15: Memory Growth 人机协同治理动作

状态：已完成第一版可操作治理闭环

实现内容：
- 新增治理动作 API：
  - `POST /api/v1/memory-growth/governance`
- 治理动作统一由 `apply_memory_growth_governance` 处理，避免前端直接改文件。
- 支持的治理动作：
  - `confirm_pending`
  - `reject_pending`
  - `defer_pending`
  - `revalidate_stale`
  - `archive_stale`
  - `generate_failure_playbook`
- 待确认知识治理：
  - 确认后会在原 conflict JSON 中写入 `governance.status=confirmed`。
  - 确认后会生成 confirmed concept，进入 `memory_growth/concepts/confirmed/`。
  - 拒绝后会写入 `governance.status=rejected`，后续不再进入待确认队列。
  - 稍后处理会写入 `defer_until`，在延期时间内不再打扰用户。
- 陈旧概念治理：
  - 重新验证会更新 Markdown frontmatter 中的 `last_verified` 和 `verification_status`。
  - 归档会把概念移动到 `memory_growth/archive/`，保留历史痕迹但不再作为活跃知识。
- 失败模式治理：
  - 可从失败模式列表生成 recovery playbook。
  - playbook 写入 `memory_growth/playbooks/recovery/`。
  - playbook 包含失败模式、原因、建议恢复路径和适用上下文。
- 所有治理动作都会写入：
  - `memory_growth/reviews/governance/*.json`
  - `memory_growth/raw/evidence/*.governance.jsonl`
- `memory_growth_status()` 已过滤已确认、已拒绝和未到期的 defer 项，避免控制台重复显示已经处理过的知识。
- 控制台页面新增治理按钮：
  - 待用户确认：确认、拒绝、稍后
  - 陈旧概念：重新验证、归档
  - 失败模式：生成 Playbook
- 治理完成后页面会重新拉取 status，并显示操作结果提示。

核心文件：
- `l3_node/memory_growth_http.py`
- `clients/desktop/src/console/pages/MemoryGrowthPanel.tsx`
- `tests/unit/test_memory_growth.py`

验证：
- `python -m py_compile l3_node\memory_growth_http.py`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py::test_memory_growth_governance_confirms_pending_and_writes_evidence tests\unit\test_memory_growth.py::test_memory_growth_governance_generates_failure_playbook`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py tests\unit\test_cognitive_kernel_runtime.py`
- `npx tsc --noEmit`

验证结果：
- Node 15 专项测试：2 passed
- Memory Growth + Cognitive Kernel 回归：46 passed, 5 warnings
- 前端 TypeScript 校验通过

下一步：
- Node 16：Memory Growth 治理审计与主动建议。
- 目标：
  - 在控制台展示治理历史时间线，能回看每一次用户确认、拒绝、归档、生成 playbook 的证据。
  - 增加批量治理能力，例如批量确认低风险知识、批量归档长期未被引用概念。
  - 增加主动治理建议，让系统根据风险分、冲突频率、失败模式聚合，推荐“今天最该处理的 3 件知识治理任务”。
  - 把治理行为纳入 weekly review，让系统定期复盘哪些确认有效、哪些拒绝避免了污染、哪些 playbook 后续真的降低了失败率。

## Node 16: Memory Growth 治理审计与主动建议

状态：已完成第一版治理历史与主动建议

实现内容：
- `GET /api/v1/memory-growth/status` 的 `monitoring` 新增：
  - `governance_history`
  - `governance_recommendations`
  - `health.governance_history_count`
  - `health.recommendation_count`
- 治理历史来自真实文件：
  - 扫描 `memory_growth/reviews/governance/*.json`
  - 按 mtime 倒序返回最近治理动作
  - 返回 action、created_at、note、summary、item_path、item_pattern、side_effect_count、report_path
- 主动建议来自真实风险队列：
  - 待确认知识 -> 推荐 `confirm_pending`
  - 高频失败模式 -> 推荐 `generate_failure_playbook`
  - 陈旧概念 -> 推荐 `revalidate_stale`
  - 重要冲突类型 -> 推荐 `generate_failure_playbook`
- 建议生成会读取最近 7 天治理历史，避免同一个 path/pattern 被重复推荐。
- 控制台页面新增：
  - “今日治理建议”
  - “治理历史”
- 今日治理建议支持直接执行治理动作，执行后刷新 status。
- 治理历史展示最近确认、拒绝、归档、重新验证和 playbook 生成记录，方便回放。

核心文件：
- `l3_node/memory_growth_http.py`
- `clients/desktop/src/console/pages/MemoryGrowthPanel.tsx`
- `tests/unit/test_memory_growth.py`

验证：
- `python -m py_compile l3_node\memory_growth_http.py`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py::test_memory_growth_status_includes_governance_history_and_recommendations`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py tests\unit\test_cognitive_kernel_runtime.py`
- `npx tsc --noEmit`

验证结果：
- Node 16 专项测试：1 passed
- Memory Growth + Cognitive Kernel 回归：47 passed, 5 warnings
- 前端 TypeScript 校验通过

下一步：
- Node 17：Memory Growth 批量治理与周复盘闭环。
- 目标：
  - 支持批量确认低风险知识、批量归档长期未引用概念、批量生成失败模式 playbook。
  - Weekly Review 读取治理历史，统计哪些确认后来被引用、哪些拒绝避免了污染、哪些 playbook 后续降低了失败率。
  - 形成“治理动作 -> 后续效果 -> 周复盘评价 -> 新建议”的闭环。
  - 给控制台增加批量选择与批量执行入口，但高风险动作仍保留显式确认。

## Node 17: Memory Growth 批量治理与周复盘闭环

状态：已完成第一版批量治理与周复盘接入

实现内容：
- 新增批量治理 API：
  - `POST /api/v1/memory-growth/batch-governance`
- 批量治理支持两种输入：
  - `operations=[{action,item,note}]`
  - `action + items`
- 批量治理会逐条调用已有 `apply_memory_growth_governance`，复用单条治理的安全校验和证据写入逻辑。
- 批量治理会额外写入：
  - `memory_growth/reviews/governance/*.batch.json`
  - `memory_growth/raw/evidence/*.batch_governance.jsonl`
- `GET /api/v1/memory-growth/status` 的 `available_actions` 新增：
  - `batch-governance`
- 治理历史已支持 batch report：
  - 展示 `batch_governance`
  - 展示 executed / failed 摘要
- 控制台“今日治理建议”新增：
  - `批量执行前三条`
  - 前端会把前三条建议转成 operations，一次提交给 batch-governance API。
- Weekly Review 已接入治理结果：
  - 扫描 `memory_growth/reviews/governance/*.json`
  - 统计治理动作数、批量治理数、治理失败数
  - 写入 weekly lifecycle report summary
  - Markdown 周报新增 Governance Actions 章节
- Weekly Review recommendations 会根据治理历史给出建议：
  - 没有治理动作时提醒使用控制台处理知识治理任务
  - 有治理动作时提醒后续比较冲突压力，判断治理是否降低重复失败
  - 有失败治理动作时提醒审查 batch governance 的失败项和安全重试路径

核心文件：
- `l3_node/memory_growth_http.py`
- `l3_node/cognitive_kernel/weekly_review.py`
- `clients/desktop/src/console/pages/MemoryGrowthPanel.tsx`
- `tests/unit/test_memory_growth.py`

验证：
- `python -m py_compile l3_node\memory_growth_http.py l3_node\cognitive_kernel\weekly_review.py`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py::test_memory_growth_http_registers_routes tests\unit\test_memory_growth.py::test_memory_growth_batch_governance_executes_multiple_operations tests\unit\test_memory_growth.py::test_weekly_review_includes_governance_effect_summary`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py tests\unit\test_cognitive_kernel_runtime.py`
- `npx tsc --noEmit`

验证结果：
- Node 17 专项测试：3 passed
- Memory Growth + Cognitive Kernel 回归：49 passed, 4 warnings
- 前端 TypeScript 校验通过

下一步：
- Node 18：治理效果度量与闭环评分。
- 目标：
  - 将治理动作与后续 raw evidence、conflict、failure pattern 关联起来。
  - 统计“确认后的知识是否被引用”“playbook 生成后同类失败是否下降”“拒绝项是否避免了后续污染”。
  - 为每类治理动作生成 effectiveness score。
  - 在控制台展示治理效果趋势，而不仅是治理动作数量。
  - 让 Weekly Review 从“记录治理动作”升级为“评价治理动作是否真的让系统变好”。

## Node 18: 治理效果度量与闭环评分

状态：已完成第一版治理效果评分

实现内容：
- `GET /api/v1/memory-growth/status` 的 `monitoring` 新增：
  - `governance_effectiveness`
  - `health.governance_effectiveness_score`
- 治理效果评分使用本地可证明证据计算：
  - 治理动作数
  - 成功治理数
  - 失败治理数
  - 成功率
  - confirmed concept 数
  - generated recovery playbook 数
  - revalidated concept 数
  - archived concept 数
  - 治理后同类失败重现数
  - 当前 conflict pressure
  - 当前 failure pressure
- `generate_failure_playbook` 治理动作会和后续 failed TurnClosure 做弱关联：
  - 如果 playbook pattern 是 `failed_turn:ocr_mismatch`
  - 后续 raw evidence 中再次出现 `ocr_mismatch`
  - 则计入 `post_governance_failure_count`
- 控制台新增“治理效果评分”卡片：
  - 展示 score / grade
  - 展示成功数、失败数、playbook 产出数
  - 展示治理效果建议
- Weekly Review 新增治理效果评估：
  - `summary.governance_effectiveness_score`
  - `governance_effectiveness`
  - Markdown 周报新增 `Governance Effectiveness` 章节
- Weekly Review recommendations 会根据治理效果给出建议：
  - 分数弱时提示补更多 recovery playbook 和重试失败治理项
  - 分数健康时提示把该分数作为下周 baseline

核心文件：
- `l3_node/memory_growth_http.py`
- `l3_node/cognitive_kernel/weekly_review.py`
- `clients/desktop/src/console/pages/MemoryGrowthPanel.tsx`
- `tests/unit/test_memory_growth.py`

验证：
- `python -m py_compile l3_node\memory_growth_http.py l3_node\cognitive_kernel\weekly_review.py`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py::test_memory_growth_status_includes_governance_history_and_recommendations tests\unit\test_memory_growth.py::test_weekly_review_includes_governance_effect_summary`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py tests\unit\test_cognitive_kernel_runtime.py`
- `npx tsc --noEmit`

验证结果：
- Node 18 专项测试：2 passed
- Memory Growth + Cognitive Kernel 回归：49 passed, 4 warnings
- 前端 TypeScript 校验通过

下一步：
- Node 19：治理效果时间序列与趋势归因。
- 目标：
  - 将每次 Weekly Review 的治理效果评分写入 `indexes/governance_effectiveness.json`。
  - 控制台展示 7/14/30 天治理效果趋势。
  - 对比治理前后 conflict pressure / failure pressure 的变化。
  - 给出“哪些治理动作最有效、哪些治理动作无效或反复失败”的归因列表。
  - 让系统开始从“治理一次”升级为“持续学习哪种治理方式最有效”。

## Node 19: 治理效果时间序列与趋势归因

状态：已完成第一版趋势索引与归因展示

实现内容：
- Weekly Review 每次运行后会写入：
  - `memory_growth/indexes/governance_effectiveness.json`
- 该索引包含：
  - `latest`
  - `history`
  - `attribution`
- `history` 记录每次周复盘的治理效果快照：
  - week_id
  - week_start
  - score
  - grade
  - action_count
  - success_count
  - failure_count
  - success_rate
  - conflict_pressure
  - failure_pressure
  - report_path
  - markdown_path
- `attribution` 记录治理动作归因：
  - effective_actions
  - ineffective_actions
  - repeated_failures
- `GET /api/v1/memory-growth/status` 的 `monitoring` 新增：
  - `governance_effectiveness_trends`
  - `governance_effectiveness_attribution`
- 趋势支持：
  - `days_7`
  - `days_14`
  - `days_30`
- 控制台“治理效果评分”卡片新增：
  - 30 天治理评分趋势图
  - conflict pressure 对比线
  - 最有效动作列表
  - 反复失败动作列表
- 这一步让 Memory Growth 从“单次治理评分”升级为“持续观察治理方式是否有效”。

核心文件：
- `l3_node/cognitive_kernel/weekly_review.py`
- `l3_node/memory_growth_http.py`
- `clients/desktop/src/console/pages/MemoryGrowthPanel.tsx`
- `tests/unit/test_memory_growth.py`

验证：
- `python -m py_compile l3_node\memory_growth_http.py l3_node\cognitive_kernel\weekly_review.py`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py::test_governance_effectiveness_index_and_status_trends tests\unit\test_memory_growth.py::test_weekly_review_includes_governance_effect_summary`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py tests\unit\test_cognitive_kernel_runtime.py`
- `npx tsc --noEmit`

验证结果：
- Node 19 专项测试：2 passed
- Memory Growth + Cognitive Kernel 回归：50 passed, 4 warnings
- 前端 TypeScript 校验通过

下一步：
- Node 20：Memory Growth 策略学习与自动调参。
- 目标：
  - 根据治理效果趋势自动调整治理建议优先级。
  - 对高收益治理动作提高推荐权重，对反复失败动作降低自动执行倾向并要求更多证据。
  - 将 effectiveness attribution 回写到 playbook / concept frontmatter，影响后续 recall 和 recovery 排序。
  - 形成“证据 -> 治理 -> 效果 -> 策略调参 -> 下一次执行更聪明”的自生长闭环。

## Node 20: Memory Growth 策略学习与自动调参

状态：已完成第一版策略学习闭环。

实现内容：
- Weekly Review 写入 `memory_growth/indexes/governance_effectiveness.json` 时同步生成 `strategy_policy`。
- `strategy_policy` 会根据治理归因自动生成动作策略：
  - 成功率高的 action 提升权重，并标记 `batch_ok`。
  - 反复失败的 action 降低权重，并标记 `manual_review` 与 `requires_more_evidence`。
  - 最近治理评分下降时进入 `cautious` 模式，避免自动批量推进高风险动作。
- `GET /api/v1/memory-growth/status` 的 `monitoring` 新增：
  - `governance_strategy_policy`
  - 带 `priority_score` / `strategy` 的 `governance_recommendations`
- 治理建议排序从固定 high/medium/low 升级为策略分数排序。
- 控制台治理建议展示：
  - score
  - execution_mode
  - strategy reason
  - needs evidence 标记
- 控制台批量治理只执行策略允许的安全项；`manual_review` 和证据不足项不会被一键批量处理。
- 新增单测覆盖：
  - Weekly Review 策略索引写入。
  - status 返回策略。
  - 失败 action 被降权并转人工审查。
  - 有效 action 被提升并允许批量治理。

核心文件：
- `l3_node/cognitive_kernel/weekly_review.py`
- `l3_node/memory_growth_http.py`
- `clients/desktop/src/console/pages/MemoryGrowthPanel.tsx`
- `tests/unit/test_memory_growth.py`

下一步：
- Node 21：策略回写到 concept / playbook frontmatter，并让 MemoryRecall / RecoveryPlanner 排序真实读取这些策略权重。
- 目标是让策略学习不只影响控制台建议，也影响后续任务执行时的知识召回、恢复路径选择和治理动作推荐。

## Node 21: 策略回写与执行时排序生效

状态：已完成第一版执行时策略闭环。

实现内容：
- 新增 `memory_growth_strategy.py` 作为共享策略层。
- Weekly Review 生成 `strategy_policy` 后，会把策略写回 concept / playbook Markdown frontmatter：
  - `governance_strategy_action`
  - `governance_strategy_weight`
  - `governance_execution_mode`
  - `governance_requires_more_evidence`
  - `governance_strategy_reason`
  - `governance_strategy_updated_at`
- Memory Growth Recall 现在会读取策略并影响 concept / playbook 召回排序。
- MemoryRecallAgent 的统一排序阶段会继续读取 `strategy_weight`、`governance_execution_mode`、`requires_more_evidence`，避免二次排序抹掉策略学习结果。
- RecoveryPlanner 在使用 Memory Growth playbook 做恢复候选时，会读取策略：
  - `batch_ok` / 高权重路径可以进入自动恢复。
  - `manual_review` 或 `requires_more_evidence=true` 的路径不会被自动重试。
- 这一步让策略学习从“控制台建议层”进入真实执行路径：召回更准，恢复更谨慎。

核心文件：
- `l3_node/cognitive_kernel/memory_growth_strategy.py`
- `l3_node/cognitive_kernel/weekly_review.py`
- `l3_node/cognitive_kernel/memory_growth_recall.py`
- `l3_node/cognitive_kernel/memory_recall_agent.py`
- `l3_node/cognitive_kernel/recovery_planner.py`
- `tests/unit/test_memory_growth.py`
- `tests/unit/test_cognitive_kernel_runtime.py`

验证：
- `python -m py_compile l3_node\cognitive_kernel\memory_growth_strategy.py l3_node\cognitive_kernel\memory_growth_recall.py l3_node\cognitive_kernel\memory_recall_agent.py l3_node\cognitive_kernel\recovery_planner.py l3_node\cognitive_kernel\weekly_review.py`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py::test_weekly_review_writes_strategy_policy_to_artifact_frontmatter tests\unit\test_cognitive_kernel_runtime.py::test_memory_recall_loads_memory_growth_concepts_and_playbooks tests\unit\test_cognitive_kernel_runtime.py::test_recovery_planner_skips_manual_review_memory_growth_playbook`

下一步：
- Node 22：把策略效果反馈继续写回到具体 playbook / concept 的使用统计里。
- 目标是形成 artifact-level learning：不只知道哪个 action 有效，还要知道哪个具体方法论、哪个具体概念在真实任务里有效。

## Node 22: Artifact-level Learning 使用统计闭环

状态：已完成第一版具体知识资产使用统计闭环。

实现内容：
- ReviewBoard 的 Memory Growth refs 新增 `artifact_path` 和 `relevance_reason`，让后续闭环可以定位到具体 Markdown 知识资产。
- 新增 artifact usage 写入能力：
  - `memory_use_count`
  - `memory_success_count`
  - `memory_failure_count`
  - `memory_success_rate`
  - `memory_last_used_at`
  - `memory_last_turn_id`
  - `memory_last_failure_reason`
- TurnClosure 完成时，如果本轮使用了 Memory Growth concept / playbook，会自动更新对应 frontmatter。
- direct mainline / capability adapter 的 close_turn 已传递 `memory_context_refs`，真实聊天入口也能累计使用效果。
- 新增 `memory_growth/indexes/artifact_usage.json`，用于控制台和后续分析读取。
- `memory_growth_status().monitoring` 新增 `artifact_usage` 与 `health.artifact_usage_count`。
- Memory Growth Recall 读取 artifact usage：
  - 使用次数高、成功率高的知识资产召回权重提升。
  - 失败次数高、成功率低的知识资产召回权重下降。
- MemoryRecallAgent 总排序继续读取 artifact usage 标记，确保二次排序也保留具体知识资产的使用反馈。

核心文件：
- `l3_node/cognitive_kernel/memory_growth_strategy.py`
- `l3_node/cognitive_kernel/review_board.py`
- `l3_node/cognitive_kernel/runtime.py`
- `l3_node/cognitive_kernel/direct_mainline.py`
- `l3_node/cognitive_kernel/capability_work_order_adapter.py`
- `l3_node/cognitive_kernel/memory_growth_recall.py`
- `l3_node/cognitive_kernel/memory_recall_agent.py`
- `l3_node/memory_growth_http.py`
- `tests/unit/test_memory_growth.py`
- `tests/unit/test_cognitive_kernel_runtime.py`

验证：
- `python -m py_compile l3_node\cognitive_kernel\memory_growth_strategy.py l3_node\cognitive_kernel\memory_growth_recall.py l3_node\cognitive_kernel\memory_recall_agent.py l3_node\cognitive_kernel\review_board.py l3_node\cognitive_kernel\runtime.py l3_node\cognitive_kernel\direct_mainline.py l3_node\cognitive_kernel\capability_work_order_adapter.py l3_node\memory_growth_http.py`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py::test_turn_closure_updates_memory_growth_artifact_usage tests\unit\test_cognitive_kernel_runtime.py::test_memory_recall_loads_memory_growth_concepts_and_playbooks`

下一步：
- Node 23：Artifact usage 趋势与推荐治理。
- 目标是把具体知识资产的 7/14/30 天使用趋势、低成功率资产、长期未命中资产、最有效 playbook 榜单展示到控制台，并让 Weekly Review 给出“保留、降权、重写、合并、归档”的治理建议。

## Node 23: Artifact Usage Trends and Governance Recommendations

状态：已完成第一版 artifact 使用趋势与推荐治理闭环。

实现内容：
- Weekly Review 现在会读取 `memory_growth/indexes/artifact_usage.json`，并生成 artifact 使用分析：
  - `top_successful_assets`
  - `low_success_assets`
  - `high_failure_assets`
  - `stale_unused_assets`
  - artifact 使用总量、成功数、失败数、成功率和活跃资产数。
- Weekly Review summary 新增：
  - `artifact_usage_count`
  - `artifact_total_use_count`
  - `artifact_success_rate`
  - `artifact_low_success_count`
  - `artifact_stale_unused_count`
- 新增 `memory_growth/indexes/artifact_usage_trends.json`：
  - 保存每周 artifact 使用快照。
  - 保存最佳 playbook、低成功率资产、高失败资产、长期未使用资产归因。
  - 输出推荐治理动作：`rewrite_or_downrank`、`create_or_update_recovery_playbook`、`archive_or_revalidate`、`promote_preferred_guidance`。
- `memory_growth_status().monitoring` 新增：
  - `artifact_usage_trends`
  - `artifact_usage_attribution`
  - `artifact_usage_recommendations`
  - `health.artifact_low_success_count`
  - `health.artifact_stale_unused_count`
- 控制台 `MemoryGrowthPanel` 新增 Artifact Learning 卡片：
  - 展示 30 天使用趋势。
  - 展示总使用量、成功率、低成功资产数。
  - 展示 Best playbooks、Needs rewrite 和推荐治理动作。
- 周报 Markdown 新增 Artifact Usage 段落，方便人工复盘时直接看到哪些知识资产值得保留、降权、重写或归档。

核心文件：
- `l3_node/cognitive_kernel/weekly_review.py`
- `l3_node/memory_growth_http.py`
- `clients/desktop/src/console/pages/MemoryGrowthPanel.tsx`
- `tests/unit/test_memory_growth.py`

验证：
- `python -m py_compile l3_node\cognitive_kernel\weekly_review.py l3_node\memory_growth_http.py`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py::test_weekly_review_indexes_artifact_usage_trends_and_recommendations tests\unit\test_memory_growth.py::test_turn_closure_updates_memory_growth_artifact_usage`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py tests\unit\test_cognitive_kernel_runtime.py`
- `npx tsc --noEmit`

下一步：
- Node 24：Artifact Governance Action 执行化。
- 目标是让控制台不只是展示建议，而是可以对单个 artifact 执行“降权、重写、归档、重新验证、提升为首选 playbook”等治理动作，并把这些动作继续写回 Weekly Review 和策略权重中。

## Node 24: Artifact Governance Action Execution

状态：已完成第一版 artifact 治理动作执行化。

实现内容：
- 在统一 Memory Growth governance 接口中新增 artifact 专属治理动作：
  - `rewrite_or_downrank`
  - `create_or_update_recovery_playbook`
  - `archive_or_revalidate`
  - `promote_preferred_guidance`
  - `revalidate_artifact`
- `rewrite_or_downrank` 会对低成功率或反复失败的知识资产写回 frontmatter：
  - `governance_strategy_weight: "0.45"`
  - `governance_execution_mode: "manual_review"`
  - `governance_requires_more_evidence: "true"`
  - `artifact_review_status: "needs_rewrite"`
  - 同时写入 `reviews/artifact_rewrites/*.json`，形成可追踪的重写请求。
- `create_or_update_recovery_playbook` 会基于失败 artifact 生成 recovery playbook，并保留源 artifact、失败原因、使用次数、成功率和失败次数。
- `archive_or_revalidate` 会根据 artifact 使用情况决定归档或重新验证：
  - 未使用或低成功且高失败资产会移入 `archive/artifacts/`。
  - 仍有使用价值的资产会走 revalidate。
- `promote_preferred_guidance` 会把高成功率资产提升为首选指导：
  - `preferred_guidance: "true"`
  - `governance_strategy_weight: "1.50"`
  - `governance_execution_mode: "batch_ok"`
  - `artifact_review_status: "preferred"`
- 治理动作完成后会刷新 artifact usage index，保证后续控制台和 Weekly Review 看到最新状态。
- 控制台 `Artifact Learning` 卡片中的推荐治理动作现在可以直接点击执行，执行后刷新 Memory Growth 状态。
- `available_actions` 新增 `artifact-governance`，用于标识当前 L3 支持 artifact 治理动作。

核心文件：
- `l3_node/memory_growth_http.py`
- `l3_node/cognitive_kernel/memory_growth_strategy.py`
- `clients/desktop/src/console/pages/MemoryGrowthPanel.tsx`
- `tests/unit/test_memory_growth.py`

验证：
- `python -m py_compile l3_node\memory_growth_http.py l3_node\cognitive_kernel\memory_growth_strategy.py`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py::test_artifact_governance_actions_update_artifacts tests\unit\test_memory_growth.py::test_weekly_review_indexes_artifact_usage_trends_and_recommendations`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py tests\unit\test_cognitive_kernel_runtime.py`
- `npx tsc --noEmit`

下一步：
- Node 25：Artifact Rewrite Agent / Curator 执行化。
- 目标是让 `rewrite_or_downrank` 生成的 rewrite request 不只停留在待办 JSON，而是由一个专门的 Artifact Curator 读取原始证据、失败原因和成功样本，自动生成重写后的 concept/playbook draft，并进入确认队列。

## Node 25: Artifact Rewrite Agent / Curator

状态：已完成第一版 artifact rewrite curator。

实现内容：
- 新增 `artifact_curator.py`，专门处理 `reviews/artifact_rewrites/*.json` 中的重写请求。
- Curator 不直接覆盖原始 concept/playbook，而是生成两类可追踪产物：
  - `reviews/artifact_drafts/*.json`：机器可读 draft。
  - `reviews/artifact_drafts/*.md`：人工可读 rewrite draft。
- 每个 draft 会携带：
  - 原 artifact 路径。
  - artifact 类型。
  - 失败原因，优先使用原 artifact 的 `memory_last_failure_reason`。
  - 使用次数、成功率、失败次数。
  - source refs。
  - draft markdown。
- Curator 会同步写入 `conflicts/artifact_rewrites/*.json`，把 draft 放进待确认队列，不自动污染长期知识。
- 处理完成后会回写 rewrite request：
  - `curation_status: drafted`
  - `curation_id`
  - `curated_at`
  - `draft_path`
  - `confirmation_path`
- 新增 HTTP 入口：`POST /api/v1/memory-growth/artifact-curator`。
- `memory_growth_status().latest` 新增 `artifact_curator_report`。
- `available_actions` 新增 `artifact-curator`。
- 控制台 Memory Growth 页面新增“运行 Artifact Curator”按钮，并在最新报告区展示 Artifact Curator report。

核心文件：
- `l3_node/cognitive_kernel/artifact_curator.py`
- `l3_node/memory_growth_http.py`
- `clients/desktop/src/console/pages/MemoryGrowthPanel.tsx`
- `tests/unit/test_memory_growth.py`

验证：
- `python -m py_compile l3_node\cognitive_kernel\artifact_curator.py l3_node\memory_growth_http.py`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py::test_artifact_curator_turns_rewrite_requests_into_drafts tests\unit\test_memory_growth.py::test_artifact_governance_actions_update_artifacts`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py tests\unit\test_cognitive_kernel_runtime.py`
- `npx tsc --noEmit`

下一步：
- Node 26：Artifact Draft Confirmation / Merge Flow。
- 目标是让用户确认后的 artifact rewrite draft 可以真正合并回原 concept/playbook，生成版本化备份、更新 frontmatter、重建 artifact usage index，并把合并结果进入 Weekly Review 的治理效果统计。

## Node 26: Artifact Draft Confirmation / Merge Flow

状态：已完成第一版 artifact rewrite draft 安全合并闭环。

实现内容：
- `artifact_curator.py` 新增 `merge_artifact_draft`：
  - 读取已确认的 artifact draft。
  - 定位原始 concept/playbook。
  - 先写版本化备份到 `archive/artifact_versions/`。
  - 再把 draft markdown 合并回原 artifact。
  - 更新 frontmatter：`artifact_review_status`、`artifact_rewritten_at`、`artifact_rewrite_merge_id`、`governance_strategy_action`、`governance_strategy_weight`、`governance_execution_mode` 等。
  - 标记 draft 为 `merge_status: merged`。
  - 标记确认队列项为 `governance.status: confirmed`。
  - 刷新 artifact usage index。
- `memory_growth_http.py` 新增治理动作 `merge_artifact_draft`。
- `confirm_pending` 现在识别 artifact rewrite confirmation：
  - 如果 pending candidate 带有 `draft_path`，确认时会执行 artifact merge。
  - 不再把 artifact rewrite draft 错写成普通 confirmed concept。
- 控制台治理动作类型新增 `merge_artifact_draft`，为后续 UI 推荐项和确认按钮打通类型边界。
- 新增测试覆盖：
  - 显式 `merge_artifact_draft` 合并 draft。
  - 通过 `confirm_pending` 自动合并 artifact rewrite draft。
  - 校验备份文件、原 artifact 内容、frontmatter、draft merge 状态、confirmation confirmed 状态。

核心文件：
- `l3_node/cognitive_kernel/artifact_curator.py`
- `l3_node/memory_growth_http.py`
- `clients/desktop/src/console/pages/MemoryGrowthPanel.tsx`
- `tests/unit/test_memory_growth.py`

验证：
- `python -m py_compile l3_node\cognitive_kernel\artifact_curator.py l3_node\memory_growth_http.py`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py::test_artifact_draft_merge_updates_source_with_backup tests\unit\test_memory_growth.py::test_confirm_pending_artifact_rewrite_merges_draft`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py tests\unit\test_cognitive_kernel_runtime.py`
- `npx tsc --noEmit`

下一步：
- Node 27：Memory Growth End-to-End Scenario Smoke。
- 目标是跑一条端到端场景：真实任务失败 -> 进入 raw evidence -> daily review -> playbook/concept -> artifact usage -> weekly review -> downrank -> curator draft -> user confirmation -> merge -> 后续 recall/recovery 读取新 artifact，形成完整闭环证据。
# Node 25: Memory Governance and Confidence Engine

状态：已完成第一版

实现内容：
- 新增统一记忆质量规则层 `memory_confidence.py`。
- 统一管理记忆初始置信度、成功/失败反馈、复核标记、召回评分、记忆层级和作用域。
- `memory_lifecycle.py` 新增并使用：
  - `layer`
  - `domain`
  - `owner`
  - `skill_id`
  - `success_count`
  - `failure_count`
  - `last_verified_at_ms`
  - `review_required`
  - `review_reason`
- 记忆召回排序不再只看关键词，已经纳入成功率、失败率、置信度、复核状态和记忆层级。
- 实体纠错记忆（例如 `lock -> Lark`）已经从独立运行时缓存接入统一 `memory_lifecycle`，并进入 Memory Growth 的待复核队列。
- 新增记忆治理文档：`docs/11_memory_governance_and_confidence_architecture.md`。

验证：
- `python -m pytest -o addopts= tests\unit\test_cognitive_kernel_architecture.py`
- `python -m pytest -o addopts= tests\unit\test_cognitive_kernel_runtime.py::test_memory_lifecycle_dedupe_recall_and_expiry`
- `python -m pytest -o addopts= tests\unit\test_memory_growth.py::test_memory_growth_http_status_includes_quality_monitoring`

下一步：
- Memory Center 页面增加记忆编辑、确认、删除、合并。
- Skill/MCP manifest 增加 memory domain schema。
- Weekly Review 增加 lifecycle 质量趋势统计。
