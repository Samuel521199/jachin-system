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

## Node 28: Experience Playbook Growth Loop

状态：已完成第一版“越用越强”经验沉淀闭环。

实现内容：
- 新增 `experience_playbook_builder.py`，把结构化失败经验自动沉淀为 `memory_growth/playbooks/learned/*.md`。
- `FailureLearningLoop` 不再只写 ledger，现在会同步写入 Memory Growth raw evidence，进入 Daily Review 消化链路。
- `DailyReviewAgent` 在生成每日 patch 时，会自动调用 Experience Playbook Builder。
- 系统会按 `task_type + tool + failure_class` 聚合同类失败，生成可复用的 Learned Recovery Playbook。
- Learned Playbook 会写入：
  - `indexes/learned_playbooks.json`
  - `indexes/playbooks.json`
- `MemoryGrowthRecall` 已可通过现有 playbook 索引召回这些新经验，并在召回摘要中带出 `failure_class` 与 `next_strategy`。
- Builder 对 raw event 做幂等处理，同一天重复运行 Daily Review 不会重复膨胀 support count。

这次升级的核心意义：
- 失败不再只是“记录一下”，而是会长成下一次可用的恢复策略。
- 系统开始具备“同类问题越遇越会处理”的基础能力。
- RecoveryPlanner 后续可以从这些 learned playbook 中选择更合适的下一条路径。

验证：
- `python -m py_compile l3_node\cognitive_kernel\experience_playbook_builder.py l3_node\cognitive_kernel\daily_review.py l3_node\cognitive_kernel\failure_learning_loop.py`
- `pytest -q -o addopts= tests\unit\test_memory_growth.py`
- `pytest -q -o addopts= tests\unit\test_memory_recall_precision.py tests\unit\test_memory_deep_mvp.py`
- `pytest -q -o addopts= tests\unit\test_adaptive_recovery_planner.py tests\unit\test_stage5_pressure_matrix.py`
- `pytest -q -o addopts= tests\unit\test_memory_growth.py::test_learned_experience_playbook_is_recalled_for_similar_task tests\unit\test_memory_recall_precision.py`

验证结果：
- `36 passed`
- `7 passed`
- `116 passed`
- `6 passed`

下一步：
- 把 Learned Recovery Playbook 进一步喂给 RecoveryPlanner，让失败后的下一步不只依赖 manifest recovery_playbook，也能参考本机真实经验。
- 在 Evidence Console 展示“本次恢复参考了哪些 learned playbook”。
- 把成功路径也沉淀成 Learned Success Playbook，让系统不仅能从失败中学，也能从高成功率路径中学。

## Node 29: RecoveryPlanner Consumes Learned Playbooks

状态：已完成第一版“失败恢复读取本机经验”的闭环。

实现内容：
- `RecoveryPlanner` 不再只依赖 `DecisionContract.memory_context_refs` 中已经携带的 memory refs。
- 当 WorkOrder 失败时，`RecoveryPlanner` 会根据：
  - `task_type`
  - `goal`
  - `role_agent`
  - `tool`
  - `failure_reason`
  主动查询 Memory Growth playbooks。
- 查询到的 learned playbook 会和 contract 内已有 memory refs 合并去重。
- 如果 learned playbook 中携带 `next_strategy`，恢复规划会优先尝试该策略。
- learned strategy 会写入恢复后的 `work_order_input.recovery_strategy`。
- 对 timeout / verification / target resolution / output quality 等策略，会自动补充对应的恢复参数：
  - `timeout`
  - `require_verification_evidence`
  - `resolve_target_from_memory`
  - `quality_gate`
- `candidate_path.metadata.memory_growth_lookup` 会记录：
  - 是否使用 live recall
  - 采用的 learned next strategy
  - 参考了多少条 memory refs
- 修复了一个恢复评分问题：当 capability governance 处于 degraded 状态时，`retry_same_path` 不再过度加权，系统会更倾向切换到替代路径。

这次升级的核心意义：
- 系统遇到失败时，会主动想起本机历史经验，而不是等上游碰巧把记忆塞进合同。
- RecoveryPlanner 开始具备“同类失败越多，下次越会换路”的能力。
- manifest recovery 是静态能力经验，learned playbook 是本机动态经验，两者现在可以共同进入恢复决策。

验证：
- `python -m py_compile l3_node\cognitive_kernel\capability_recovery_registry.py l3_node\cognitive_kernel\recovery_planner.py`
- `pytest -q -o addopts= tests\unit\test_memory_growth.py::test_recovery_planner_live_recalls_learned_playbook_without_contract_refs tests\unit\test_memory_growth.py::test_learned_experience_playbook_is_recalled_for_similar_task`
- `pytest -q -o addopts= tests\unit\test_intelligence_foundation_layers.py::test_recovery_planner_uses_governance_health_to_prefer_alternate_path tests\unit\test_adaptive_recovery_planner.py`
- `pytest -q -o addopts= tests\unit\test_memory_growth.py tests\unit\test_memory_deep_mvp.py tests\unit\test_memory_recall_precision.py tests\unit\test_adaptive_recovery_planner.py tests\unit\test_stage5_pressure_matrix.py tests\unit\test_intelligence_foundation_layers.py`
- `pytest -q -o addopts= tests\unit\test_cognitive_kernel_runtime.py::test_recovery_planner_uses_memory_growth_playbook_when_manifest_has_no_candidate tests\unit\test_cognitive_kernel_runtime.py::test_recovery_planner_skips_manual_review_memory_growth_playbook`

验证结果：
- `2 passed`
- `10 passed`
- `173 passed`
- `2 passed`

下一步：
- 在 Evidence Console 展示每次恢复参考的 learned playbook、manifest playbook、评分依据和被拒绝路径。
- 把成功路径沉淀成 Learned Success Playbook，让高成功率路径也能反哺 TaskDecomposer 和 RecoveryPlanner。
- 增加真实任务级压测：同类失败连续出现时，验证第 2、3、4 次是否真的会越来越少走重复错误路径。

## Node 30: Recovery Evidence Explainability

状态：已完成第一版“恢复决策可解释化”。

实现内容：
- `os_evidence.rs` 不再只抽取裸 `adaptive_scorecard`，现在会把 Recovery 候选路径整体归一化为 `recovery_candidate`。
- Evidence 聚合会保留 learned playbook 来源、manifest playbook 来源、candidate strategy/tool、candidate score/rank score、eligible/reject_reason、manifest_path、memory_growth_lookup、memory_context_refs。
- `OsEvidencePanel.tsx` 的 Recovery 区块升级为 `Recovery Decision Intelligence`。
- 前端现在可以直接看到本次恢复参考了哪些 learned playbook、哪些路径来自 manifest、哪些路径被拒绝以及拒绝原因、候选路径评分、失败类别、失败工具、历史失败类别和 rationale。
- 这一步让 Failure Learning Loop 不再是黑盒，用户可以在 Evidence Console 中追踪“为什么这次选择这条恢复路径”。

验证：
- `cargo check --manifest-path clients\desktop\src-tauri\Cargo.toml --target-dir clients\desktop\src-tauri\target\codex-check`
- `npx tsc --noEmit`（在 `clients\desktop` 下执行）
- `git diff --check`

下一步：
- 继续沉淀 Learned Success Playbook，让高成功率路径也能反哺 TaskDecomposer 与 RecoveryPlanner。
- 做真实任务级恢复压测，观察同类失败连续出现时，Recovery 是否逐步减少重复错误路径。
## Node 31: Learned Success Playbook Loop

状态：已完成第一版“从成功路径中学习”的闭环。

实现内容：
- `experience_playbook_builder.py` 新增 success playbook 构建流程。
- Daily Review 现在会同时沉淀失败恢复 playbook 和成功执行 playbook。
- 成功路径写入 `memory_growth/playbooks/learned_success/*.md`。
- 新增索引 `memory_growth/indexes/learned_success_playbooks.json`。
- 总索引 `memory_growth/indexes/playbooks.json` 会同时保留 failure playbook 与 success playbook，二者互不覆盖。
- success playbook 会记录：task_type、primary tool、role_agent、work_order_chain、success_strategy、support count、confidence、source_refs。
- Memory Growth Recall 现在会把 `success_strategy` 与 `work_order_chain` 放进 playbook 内容，供主链路召回。
- TaskDecomposer 会读取 `DecisionContract.memory_context_refs` 中的 success playbook，并把它挂到 DAG 节点的 `preferred_success_playbooks` 与 `recovery_policy` 中。
- 这一步让系统不仅能从失败中学习避坑，也能从高成功率路径中学习“优先怎么做”。

验证：
- `python -m py_compile l3_node\cognitive_kernel\experience_playbook_builder.py l3_node\cognitive_kernel\daily_review.py l3_node\cognitive_kernel\memory_growth_recall.py l3_node\cognitive_kernel\task_decomposer.py`
- `python -m pytest -q -o addopts= tests\unit\test_memory_growth.py::test_success_experience_playbook_is_built_and_indexed tests\unit\test_memory_growth.py::test_success_experience_playbook_is_recalled_for_similar_task tests\unit\test_memory_growth.py::test_task_decomposer_attaches_success_playbook_refs`
- `python -m pytest -q -o addopts= tests\unit\test_memory_growth.py`
- `python -m pytest -q -o addopts= tests\unit\test_intelligence_foundation_layers.py::test_recovery_planner_uses_governance_health_to_prefer_alternate_path tests\unit\test_adaptive_recovery_planner.py`

验证结果：
- 新增 success playbook 三项测试：3 passed。
- Memory Growth 全量测试：41 passed。
- Recovery Planner 相关测试：10 passed。

下一步：
- 让 CapabilityIntelligence 和 TaskDecomposer 在拆 DAG 时优先参考高成功率 success playbook，而不是只把它当作提示。
- 把 success playbook 的使用结果继续写回 artifact usage，让低成功率的成功路径自动降权，高成功率路径继续升权。
## Node 32: Success Playbook Preference Loop

状态：已完成第一版“高成功率路径优先采用”的闭环。

实现内容：
- `TaskDecomposerAgent` 不再只是把 Learned Success Playbook 当作参考提示挂到 DAG 节点上。
- 现在会从 success playbook 中抽取：
  - `success_strategy`
  - `work_order_chain`
  - `confidence`
  - `memory_success_rate / artifact_success_rate`
- 新增 success playbook 排序逻辑：
  - 优先看召回置信度
  - 再看历史成功率
  - 再看是否包含明确成功策略
  - 再看是否包含可复用 WorkOrder 链路
- 每个 DAG 节点现在会写入：
  - `preferred_success_playbooks`
  - `success_playbook_preference`
  - `preferred_execution_strategy`
  - `preferred_work_order_chain`
- `recovery_policy` 同步写入同一套 success preference，让 Dispatcher、RoleExecutor、RecoveryPlanner 和 Evidence 都能看到“为什么优先走这条成功路径”。
- TaskDecomposer rationale 会明确记录采用了哪条 learned success strategy。
- 这一步让系统从“记住成功案例”升级成“同类任务优先复用历史高成功率路径”。

验证：
- `python -m py_compile l3_node\cognitive_kernel\task_decomposer.py`
- `python -m pytest -q -o addopts= tests\unit\test_memory_growth.py -k "success_playbook or decomposer"`
- `python -m pytest -q -o addopts= tests\unit\test_memory_growth.py`
- `python -m pytest -q -o addopts= tests\unit\test_memory_deep_mvp.py tests\unit\test_memory_recall_precision.py tests\unit\test_adaptive_recovery_planner.py tests\unit\test_intelligence_foundation_layers.py`
- `git diff --check`

验证结果：
- success playbook / decomposer 相关测试：2 passed。
- Memory Growth 全量测试：42 passed。
- 记忆深度 MVP、召回精度、自适应恢复、智能底座测试：28 passed。
- diff 检查通过，仅保留既有 CRLF 提示。

下一步：
- 把 success preference 接入 Dispatcher / RoleExecutor 的实际执行选择，让 WorkOrder 执行层真正按 `preferred_work_order_chain` 和 `preferred_execution_strategy` 优先走高成功率路径。
- 把 success preference 的使用结果继续写回 artifact usage，形成“越成功越优先、失败会降权”的长期强化闭环。

## Node 33: Success Preference Execution Feedback Loop

状态：已完成第一版“成功路径进入执行层并回写结果”的闭环。

实现内容：
- `Arbiter` 在把 `DecomposedTaskNode` 转成 `WorkOrder` 时，会生成标准化 `execution_preference`。
- `execution_preference` 保留：
  - `source`
  - `selection_reason`
  - `selected_memory_id`
  - `selected_artifact_path`
  - `selected_confidence`
  - `selected_success_rate`
  - `preferred_execution_strategy`
  - `preferred_work_order_chain`
  - `candidate_count`
- `Dispatcher` 会把 `execution_preference` 传入 `RoleExecutionContext.metadata`。
- `VerificationReport` 的 role execution evidence 会保留本次使用的 execution preference。
- `RoleExecutor` 的 evidence 会展示本次是否采用了 learned success path，以及采用的是哪条策略和链路。
- `TurnClosure` 继续通过 `memory_context_refs` 调用 artifact usage 写回。
- 新增测试确认：success playbook 被任务使用并验证通过后，会更新对应 playbook 的：
  - `memory_use_count`
  - `memory_success_count`
  - `memory_failure_count`
  - `memory_success_rate`
- 这一步让系统从“拆解时偏向成功路径”推进到“执行证据链也知道并记录成功路径”，同时结果会反馈回长期记忆评分。

验证：
- `python -m py_compile l3_node\cognitive_kernel\arbiter.py l3_node\cognitive_kernel\dispatcher.py l3_node\cognitive_kernel\role_executors.py`
- `python -m pytest -q -o addopts= tests\unit\test_memory_growth.py -k "success_execution_preference or success_playbook_usage_feedback or role_execution_evidence"`
- `python -m pytest -q -o addopts= tests\unit\test_memory_growth.py`
- `python -m pytest -q -o addopts= tests\unit\test_memory_deep_mvp.py tests\unit\test_memory_recall_precision.py tests\unit\test_adaptive_recovery_planner.py tests\unit\test_intelligence_foundation_layers.py`

验证结果：
- 新增 success execution preference / usage feedback 测试：3 passed。
- Memory Growth 全量测试：45 passed。
- 记忆深度 MVP、召回精度、自适应恢复、智能底座测试：28 passed。

下一步：
- 让 Dispatcher 根据 `preferred_work_order_chain` 对同类候选 WorkOrder 做非破坏式排序建议，而不是硬重排 DAG。
- 把“成功路径使用后失败”的降权原因写进 Evidence Console，让用户能看到为什么某条历史成功路径开始被降权。
- 做一组同类任务连续运行的模拟压测，验证高成功率路径会升权，失败路径会降权。

## Node 34: Success Path Promotion / Demotion Pressure Test

状态：已完成第一版“成功路径升权、失败路径降权”的可验证闭环。

实现内容：
- `Arbiter` 会根据 `preferred_work_order_chain` 给每个 `WorkOrder` 标注非破坏式执行顺序建议。
- 新增 `execution_order_advice`：
  - `mode: non_destructive`
  - `matched`
  - `matched_step`
  - `matched_index`
  - `chain_length`
  - `reason`
- 这不是硬重排 DAG，不会破坏依赖关系；它只是告诉执行层和 Evidence：当前 WorkOrder 是否正在沿着历史成功链路执行。
- `RoleExecutor` evidence 现在会携带：
  - learned success preference
  - selected memory id
  - preferred strategy
  - preferred chain
  - execution order advice
- Evidence Console 的 RoleExecution 区块新增 Learned Success Preference 展示：
  - success path 成功率
  - chain 匹配序号
  - strategy
  - memory id
  - matched step
- 新增压测样本：同一任务下构造两个相关 success playbook。
  - 一个高成功率：`memory_success_rate=0.9`
  - 一个低成功率：`memory_success_rate=0.1` 且有 `memory_last_failure_reason`
- `MemoryGrowthRecall` 必须优先召回高成功率 playbook。
- `TaskDecomposer / Arbiter` 必须把高成功率 playbook 作为执行偏好。

验证：
- `python -m py_compile l3_node\cognitive_kernel\arbiter.py l3_node\cognitive_kernel\role_executors.py`
- `python -m pytest -q -o addopts= tests\unit\test_memory_growth.py -k "usage_score_promotes or success_execution_preference or role_execution_evidence"`
- `python -m pytest -q -o addopts= tests\unit\test_memory_growth.py`
- `python -m pytest -q -o addopts= tests\unit\test_memory_deep_mvp.py tests\unit\test_memory_recall_precision.py tests\unit\test_adaptive_recovery_planner.py tests\unit\test_intelligence_foundation_layers.py`
- `npx tsc --noEmit`（在 `clients\desktop` 下执行）

验证结果：
- 新增升降权 / execution preference / role evidence 测试：3 passed。
- Memory Growth 全量测试：46 passed。
- 记忆深度 MVP、召回精度、自适应恢复、智能底座测试：28 passed。
- 前端 TypeScript 检查通过。

下一步：
- 把 success path 降权原因进一步汇总到 Memory Growth Dashboard，形成“哪些成功路径正在退化”的运营指标。
- 做连续多轮模拟：同一类任务成功 10 次、失败 5 次，观察 success rate、recall 排序和 Evidence 展示是否稳定变化。
- 把这些升降权指标用于 RecoveryPlanner，当历史成功路径开始退化时，自动降低它在恢复候选里的优先级。
## Node 35: Success Path Health Dashboard and Recovery Degradation Weighting

Status: completed.

Implementation:
- Added `success_path_health` to Memory Growth monitoring.
- Success paths are now grouped into:
  - reliable paths: enough usage and high success rate.
  - degraded paths: low success rate or repeated failures.
  - unproven paths: not enough usage yet.
- Memory Growth health now exposes:
  - `success_path_reliable_count`
  - `success_path_degraded_count`
- Memory Growth Dashboard now shows:
  - total success paths.
  - reliable success paths.
  - degrading success paths.
  - top reliable/degraded path lists.
- RecoveryPlanner now reads artifact usage health from recalled Memory Growth playbooks.
- If a learned playbook has repeated failures or low success rate, the recovery candidate priority is automatically downranked.
- Recovery candidate metadata now records:
  - `artifact_usage_multiplier`
  - `artifact_usage_health`
  - degraded refs / reliable refs / recent failure reasons.
- This turns success memory from a static preference into an operational signal: reliable paths get trusted, degraded paths lose priority.

Verification:
- `python -m py_compile l3_node\memory_growth_http.py l3_node\cognitive_kernel\recovery_planner.py`
- `python -m pytest -q -o addopts= tests\unit\test_memory_growth.py -k "success_path_health or degraded_learned_playbook"`
- `python -m pytest -q -o addopts= tests\unit\test_memory_growth.py`
- `python -m pytest -q -o addopts= tests\unit\test_memory_deep_mvp.py tests\unit\test_memory_recall_precision.py tests\unit\test_adaptive_recovery_planner.py tests\unit\test_intelligence_foundation_layers.py`
- `npx tsc --noEmit` in `clients\desktop`.

Verification result:
- New success path health and degraded recovery tests: 2 passed.
- Memory Growth full suite: 48 passed.
- Memory deep MVP / recall precision / adaptive recovery / intelligence foundation suites: 28 passed.
- Desktop console TypeScript check passed.

Next step:
- Feed degraded success path signals back into TaskDecomposer ranking, so degraded success paths are not only downranked during recovery, but also become less likely to be selected as the preferred route for future task DAGs.
- Add a small simulated multi-round trend test: the same success path succeeds several times, then starts failing, and the dashboard plus planner should show the change without manual intervention.
## Node 36: Degraded Success Path Pre-Ranking and Multi-Round Trend Test

Status: completed.

Implementation:
- TaskDecomposer success playbook ranking now consumes usage health before choosing the preferred DAG route.
- Success playbook refs now extract:
  - `artifact_use_count / memory_use_count`
  - `artifact_failure_count / memory_failure_count`
  - `artifact_last_failure_reason / memory_last_failure_reason`
- Success playbooks now receive a health label:
  - `reliable`
  - `degraded`
  - `unproven`
- A degraded success path is no longer selected just because it has higher confidence.
- Ranking now combines confidence, success rate, usage count, explicit strategy, work order chain, reliable bonus, low-rate penalty, and repeated-failure penalty.
- `success_playbook_preference` now records selected health, selected use count, selected failure count, and selected last failure reason.
- Added a multi-round simulation:
  - the same learned success path succeeds several times and appears as reliable.
  - the same path then fails several times and automatically appears as degraded.
- This moves learning earlier in the task lifecycle: bad historical success paths are avoided during DAG creation, not only during post-failure recovery.

Verification:
- `python -m py_compile l3_node\cognitive_kernel\task_decomposer.py`
- `python -m pytest -q -o addopts= tests\unit\test_memory_growth.py -k "degraded_success_playbook_ref or multi_round_feedback"`
- `python -m pytest -q -o addopts= tests\unit\test_memory_growth.py`
- `python -m pytest -q -o addopts= tests\unit\test_memory_deep_mvp.py tests\unit\test_memory_recall_precision.py tests\unit\test_adaptive_recovery_planner.py tests\unit\test_intelligence_foundation_layers.py`
- `npx tsc --noEmit` in `clients\desktop`.

Verification result:
- New degraded TaskDecomposer ranking and multi-round trend tests: 2 passed.
- Memory Growth full suite: 50 passed.
- Memory deep MVP / recall precision / adaptive recovery / intelligence foundation suites: 28 passed.
- Desktop console TypeScript check passed.

Next step:
- Use the same success/degradation signal inside Arbiter and Dispatcher candidate selection, so non-primary candidate tools can be ordered by learned reliability before WorkOrder execution.
- Add a synthetic end-to-end task run where success memory selects a preferred route, the route degrades, and a later run automatically switches to the next healthier candidate.
## Node 37: Candidate Tool Reliability Ranking in Arbiter and Dispatcher Evidence

Status: completed.

Implementation:
- Arbiter no longer blindly selects `candidate_tools[0]` when Memory Growth has relevant tool reliability evidence.
- Candidate tools are now scored with:
  - matched Memory Growth refs.
  - confidence.
  - artifact / memory success rate.
  - use count.
  - failure count.
  - last failure reason.
  - reliable / degraded / unproven health.
- Exact tool id matches are preferred over broad aliases, so generic terms like `lark` do not incorrectly attach every Lark memory to every Lark tool candidate.
- Arbiter records `candidate_tool_reliability` in the decision event and WorkOrder inputs.
- Dispatcher now passes candidate tool reliability into `RoleExecutionContext.metadata`.
- Verification role evidence now includes:
  - `candidate_tool_reliability`
  - `selected_tool_reliability`
- This lets Evidence explain not only which route was preferred, but also why a particular MCP/tool candidate was selected over another.

Verification:
- `python -m py_compile l3_node\cognitive_kernel\arbiter.py l3_node\cognitive_kernel\dispatcher.py`
- `python -m pytest -q -o addopts= tests\unit\test_memory_growth.py -k "reliable_candidate_tool or candidate_tool_reliability"`
- `python -m pytest -q -o addopts= tests\unit\test_memory_growth.py`
- `python -m pytest -q -o addopts= tests\unit\test_memory_deep_mvp.py tests\unit\test_memory_recall_precision.py tests\unit\test_adaptive_recovery_planner.py tests\unit\test_intelligence_foundation_layers.py`
- `npx tsc --noEmit` in `clients\desktop`.

Verification result:
- New candidate tool reliability tests: 2 passed.
- Memory Growth full suite: 52 passed.
- Memory deep MVP / recall precision / adaptive recovery / intelligence foundation suites: 28 passed.
- Desktop console TypeScript check passed.

Next step:
- Build one synthetic end-to-end task run where the first candidate tool is degraded, the second candidate is reliable, Arbiter selects the reliable tool, Dispatcher executes it, and TurnClosure writes the result back into artifact usage.
- Then add a second run after the reliable tool degrades, verifying that the system automatically switches again.
## Node 38: Candidate Tool Reliability End-to-End Feedback and Auto Switch

Status: completed.

Implementation:
- Added a synthetic end-to-end Memory Growth test for candidate tool reliability.
- The first run provides three candidate tools:
  - a degraded path.
  - a stable/reliable path.
  - an alternate path.
- Arbiter selects the reliable `mcp:stable_tool_path` instead of the degraded first candidate.
- TaskDecomposer/WorkOrder carries `candidate_tool_reliability` into execution inputs.
- Dispatcher executes the selected WorkOrder and records selected reliability evidence.
- TurnClosure writes the successful run back into Memory Growth artifact usage.
- The second run marks the previous stable path as degraded and the alternate path as healthier.
- Arbiter then switches to `mcp:alternate_tool_path`, proving that historical success memory is not static. It changes future routing after feedback.

Verification:
- `python -m py_compile l3_node\cognitive_kernel\arbiter.py l3_node\cognitive_kernel\dispatcher.py l3_node\cognitive_kernel\runtime.py`
- `python -m pytest -q -o addopts= tests\unit\test_memory_growth.py -k "candidate_tool_reliability_end_to_end_feedback_and_switch"`
- `python -m pytest -q -o addopts= tests\unit\test_memory_growth.py`
- `python -m pytest -q -o addopts= tests\unit\test_memory_deep_mvp.py tests\unit\test_memory_recall_precision.py tests\unit\test_adaptive_recovery_planner.py tests\unit\test_intelligence_foundation_layers.py`

Verification result:
- New candidate tool reliability E2E test: 1 passed.
- Memory Growth full suite: 53 passed.
- Memory deep MVP / recall precision / adaptive recovery / intelligence foundation suites: 28 passed.

Next step:
- Move from synthetic candidate tool paths to dry-run real multi-MCP workflows.
- The next target should be `web_research_delivery`: search -> fetch -> per-page summary -> final brief -> Lark dry-run/send.
- The same reliability ranking should choose between search/fetch/summary tools based on manifest recovery playbooks and learned local success/failure history.
## Node 39: Manifest-Driven Web Research DAG Tool Reliability Switch

Status: completed.

Implementation:
- WorkOrder generation now ranks node-level candidate tools from capability recovery paths and learned Memory Growth reliability.
- Web research search nodes can switch from degraded `mcp:tavily_search` to reliable `mcp:browser_search`.
- Web research fetch nodes can switch from degraded `mcp:fetch` to reliable `mcp:browser_extract`.
- Broad capability ids such as `web_research_delivery` are no longer treated as executable node tools.
- Generic single-node tasks still keep `summary.candidate_tools`, so Arbiter can rank real alternate tools instead of only the current node tool.
- WorkOrder inputs now record:
  - planned tool.
  - selected tool.
  - node candidate tools.
  - candidate tool reliability.
  - selected tool reliability.
  - tool selection reason when memory chooses an alternate path.
- Role execution start and verification evidence now include candidate and selected tool reliability, so Evidence can explain which Role Agent used which route and why.
- Added a dry-run real multi-MCP Web Research workflow test:
  - `mcp:browser_search`
  - `mcp:browser_extract`
  - `core:web_research_summarize`
  - `mcp:windows_lark_send_message`
- The dry-run chain injects upstream search/fetch/summary results through Dispatcher and verifies the Lark message keeps complete links and avoids truncation.

Verification:
- `python -m py_compile l3_node\cognitive_kernel\arbiter.py l3_node\cognitive_kernel\role_executors.py l3_node\cognitive_kernel\dispatcher.py l3_node\cognitive_kernel\task_decomposer.py`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_memory_growth.py`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_web_research_lark_workflow_pressure.py`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_intent_tool_memory_combo_matrix.py`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_web_research_lark_workflow_pressure.py tests\unit\test_intent_tool_memory_combo_matrix.py tests\unit\test_adaptive_recovery_planner.py tests\unit\test_intelligence_foundation_layers.py`
- `npx tsc --noEmit` in `clients\desktop`.
- `git diff --check`.

Verification result:
- Memory Growth full suite: 53 passed.
- Web Research Lark workflow pressure suite: 6 passed.
- Intent / tool / memory combo matrix: 16 passed.
- Adaptive Recovery Planner, intelligence foundation, Web Research, and combo suites together: 43 passed.
- Desktop TypeScript check passed.
- `git diff --check` passed with only existing CRLF normalization warnings.

Next step:
- Upgrade `web_research_delivery` from dry-run DAG validation to live-safe quality-gated execution.
- Add a model-backed final brief composer with source quality scoring, human-readable Lark preview, and confirmation policy for external sending.
- Feed search/fetch/source-quality failures back into Memory Growth learned playbooks so future web research automatically avoids low-quality sources and brittle fetch paths.
## Node 40: Live-Safe Web Research Brief Quality Gate and Lark Preview Contract

Status: completed.

Implementation:
- `core:web_research_summarize` now returns a structured `quality_report` instead of only a message string.
- The quality report includes:
  - `send_ready`
  - `requires_preview`
  - `quality_level`
  - `score`
  - `issues`
  - `primary_issue`
  - source count and URL count
  - model/rule source counts
  - readable finding count
  - message length
- Summary generation now blocks unsafe outbound content before Lark delivery when:
  - no source is available.
  - source URLs are missing.
  - web residue remains in the brief.
  - markdown/webpage fragments leak into the brief.
  - ellipsis/truncation appears.
  - bullet sentences are incomplete.
  - source lines are missing.
- `BrowserExecutor` summary evidence now exposes `web_research_quality_report` and source count.
- `MessageExecutorAgent` now reads the upstream `quality_report` and records a `send_preview_policy` in Evidence.
- Direct mainline DAG injection now forwards summary `quality_report` and `sources` from the summary WorkOrder into the Lark send WorkOrder.
- This connects the full chain:
  - search/fetch evidence
  - page summary
  - final human brief
  - quality gate
  - Lark preview/send evidence
- Added tests proving that:
  - a good web research brief is `send_ready`.
  - source metadata is preserved.
  - quality report survives injection into the Lark send WorkOrder.
  - planned Web Research DAG still switches tools using learned reliability.

Verification:
- `python -m py_compile l3_node\cognitive_kernel\role_executors.py l3_node\cognitive_kernel\direct_mainline.py`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_web_research_lark_workflow_pressure.py`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_stage5_pressure_matrix.py -k "web_research"`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_intent_tool_memory_combo_matrix.py tests\unit\test_adaptive_recovery_planner.py tests\unit\test_intelligence_foundation_layers.py tests\unit\test_memory_growth.py`
- `npx tsc --noEmit` in `clients\desktop`.
- `git diff --check`.

Verification result:
- Web Research Lark workflow pressure suite: 7 passed.
- Stage5 Web Research quality tests: 30 passed.
- Intent / recovery / intelligence / memory growth combined suites: 90 passed.
- Desktop TypeScript check passed.
- `git diff --check` passed with only existing CRLF normalization warnings.

Next step:
- Feed `web_research_quality_report` failures into Memory Growth as structured source-quality memories.
- Add source/domain reputation scoring so repeated bad domains, login walls, and brittle fetch paths are automatically downranked before search/fetch execution.
- Extend Evidence Console to group Web Research by source quality, summary quality, send preview policy, and final delivery verification.
## Node 41: Source Quality Memory and Domain Reputation for Web Research

Status: completed.

Implementation:
- Added a dedicated source-quality memory layer for Web Research.
- New runtime index:
  - `memory_growth/indexes/source_quality.json`
- Each domain now accumulates:
  - use count.
  - success count.
  - failure count.
  - average quality score.
  - issue counts.
  - last query.
  - last URL.
  - last primary issue.
  - reputation score.
  - health label: `reliable`, `degraded`, or `unproven`.
- `core:web_research_summarize` now records source quality feedback into Memory Growth after a quality report is produced.
- Failed source quality is recorded even when search/fetch found sources but they were rejected before a usable brief could be generated.
- Web fetch URL ordering now consumes source reputation:
  - reliable domains are ranked earlier.
  - degraded domains are ranked later.
  - unknown domains remain neutral.
- Web research findings are also ranked by source reputation before summarization.
- Memory Growth raw evidence now receives `source_quality` JSONL events for later digestion into concepts/playbooks.
- Memory Growth status now exposes a `source_quality` monitoring block with:
  - summary counts.
  - reliable sources.
  - degraded sources.
  - unproven sources.
- This makes the system start learning not only which tool path works, but which web sources are worth trusting.

Verification:
- `python -m py_compile l3_node\cognitive_kernel\source_quality_memory.py l3_node\cognitive_kernel\role_executors.py l3_node\memory_growth_http.py`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_web_research_lark_workflow_pressure.py`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_stage5_pressure_matrix.py -k "web_research"`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_web_research_lark_workflow_pressure.py tests\unit\test_stage5_pressure_matrix.py -k "web_research"`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_intent_tool_memory_combo_matrix.py tests\unit\test_adaptive_recovery_planner.py tests\unit\test_intelligence_foundation_layers.py tests\unit\test_memory_growth.py`
- `npx tsc --noEmit` in `clients\desktop`.
- `git diff --check`.

Verification result:
- Web Research Lark workflow pressure suite: 8 passed.
- Stage5 Web Research quality tests: 30 passed.
- Web Research + Stage5 combined run: 38 passed.
- Intent / recovery / intelligence / memory growth combined suites: 90 passed.
- Desktop TypeScript check passed.
- `git diff --check` passed with only existing CRLF normalization warnings.

Next step:
- Extend Evidence Console to visually group Web Research execution by:
  - source quality.
  - summary quality.
  - send preview policy.
  - final delivery verification.
- Then add live-safe Web Research dry-run/live-run toggles so the same workflow can be demonstrated without accidentally sending external messages.

## Node 42: Evidence Console Web Research Grouped Replay

Status: completed.

Implementation:
- Extended the Web Research role evidence so summarized source lists now include source-quality evidence:
  - URL.
  - domain.
  - title.
  - source type.
  - reliability score.
  - success and failure counts.
  - last quality level and primary issue.
- Added a dedicated Web Research Replay block in the OS Evidence Console.
- The replay block groups a Web Research delivery into four user-visible stages:
  - Source Quality: which domains were used and how trustworthy they currently are.
  - Summary Quality: send-ready state, score, quality level, source count, message length, and quality issues.
  - Send Preview Policy: whether the result can auto-send or must be previewed, and why.
  - Delivery Verification: message channel, send result, post-send verification state, and reason.
- The grouping is evidence-driven rather than hardcoded to one tool. It scans existing role execution, tool quality, and tool result payloads for:
  - `source_quality`.
  - `sources`.
  - `web_research_quality_report`.
  - `quality_report`.
  - `send_preview_policy`.
  - `send_result`.
  - `post_send_verified`.
- This gives Web Research workflows a clear replay path in the console while still preserving raw RoleExecution and ToolQuality JSON for deeper debugging.

Verification:
- `npx tsc --noEmit` in `clients\desktop`.
- `python -m py_compile l3_node\cognitive_kernel\role_executors.py l3_node\cognitive_kernel\source_quality_memory.py`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_web_research_lark_workflow_pressure.py tests\unit\test_stage5_pressure_matrix.py -k "web_research"`

Verification result:
- Desktop TypeScript check passed.
- Python compile check passed.
- Web Research + Stage5 combined run: 38 passed.

Next step:
- Add live-safe Web Research dry-run/live-run toggles:
  - dry-run should exercise search, fetch, summary, source quality, and preview without sending Lark.
  - live-run should require an explicit delivery policy and write final send verification to Evidence.
- Then add an operator-facing Web Research demo template that can be run safely from the console.

## Node 43: Live-Safe Web Research Dry-Run / Live-Run Delivery Boundary

Status: completed.

Implementation:
- Added a dedicated Web Research decomposition fallback when no Skill/MCP manifest decomposition is available.
- The fallback DAG is:
  - `mcp:tavily_search`
  - `mcp:fetch`
  - `core:web_research_summarize`
  - `mcp:windows_lark_send_message`
- Added delivery policy propagation across both manifest-driven and fallback DAG paths:
  - `delivery_mode=dry_run`
  - `dry_run=true`
  - `send_allowed=false`
- Default Web Research delivery mode is now `dry_run` unless the user or caller explicitly requests live delivery.
- ReviewBoard input context now detects:
  - dry-run / dry run / preview only / 只演练 / 不要发送.
  - live-run / live run / 真实发送 / 立即发送.
- Direct mainline now injects upstream summary, source, quality report, and delivery mode into the final Lark WorkOrder.
- `MessageExecutorAgent` now has a first-class dry-run preview path:
  - it validates recipient and message slots.
  - it returns preview evidence.
  - it does not call the external Lark transport.
  - it writes `dry_run_preview_verified=true`.
- Strict verification now recognizes two valid delivery outcomes:
  - live-run: `post_send_verified=true` and delivery evidence is present.
  - dry-run: `dry_run_preview_verified=true` and a non-empty preview message is present.
- Tool quality gate now treats dry-run preview as a valid completion and no longer reports `message_post_send_unverified` for dry-run previews.
- User-facing reply for Web Research dry-run now says the brief was previewed and not actually sent, avoiding false-send language.

Verification:
- `python -m py_compile l3_node\cognitive_kernel\task_decomposer.py l3_node\cognitive_kernel\direct_mainline.py l3_node\cognitive_kernel\role_executors.py l3_node\cognitive_kernel\runtime.py l3_node\cognitive_kernel\tool_quality.py l3_node\cognitive_kernel\review_board.py`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_web_research_lark_workflow_pressure.py tests\unit\test_stage5_pressure_matrix.py -k "web_research"`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_intent_tool_memory_combo_matrix.py tests\unit\test_intelligence_foundation_layers.py -k "web_research or task_decomposer or goal"`

Verification result:
- Web Research + Stage5 combined run: 40 passed.
- Intent / task decomposition / goal targeted run: 9 passed.
- Python compile check passed.

Next step:
- Add an operator-facing Web Research demo template in the Evidence Console:
  - default dry-run.
  - explicit live-run toggle.
  - configurable query and recipient.
  - Evidence replay opens directly after completion.
- Then run one dry-run template smoke and one guarded live-run smoke with a safe recipient.

## Node 44: Operator-Facing Web Research Demo Template

Status: completed.

Implementation:
- Added a Web Research -> Lark template to the Evidence Console task template library.
- Added a dedicated Web Research control block in the Evidence Console:
  - configurable query.
  - shared recipient picker.
  - safe preview button.
  - explicit live-run send button.
- The template path now passes `web_query` from React -> Tauri command -> Python evidence runner.
- The Python runner loads the project `.env` before starting Web Research so the template can access local Tavily / model credentials in the same way the desktop runtime does.
- The template creates a real Cognitive Kernel turn instead of synthetic evidence:
  - `build_cognitive_turn_context`.
  - `plan_cognitive_turn`.
  - `try_execute_cognitive_direct_plan`.
  - Search -> fetch -> summarize -> Lark preview/delivery WorkOrders.
- Web Research template execution now normalizes explicit template slots:
  - query is taken from the UI field.
  - recipients are taken from the UI picker.
  - delivery mode is `dry_run` unless the user presses the explicit live-run button.
- Dry-run message delivery writes preview evidence without calling the external Lark transport.
- Template evidence now extracts the final Lark preview body from the Cognitive Kernel ledger, preferring full WorkOrder input over truncated role preview fields.
- Fixed a Web Research quality-gate false negative:
  - previous logic compared message URLs against all candidate page summaries.
  - new logic compares message URLs against actually cited sources, so a brief can safely cite the best two sources from a larger candidate pool.

Verification:
- `python -m py_compile scripts\os_evidence_task_runner.py l3_node\cognitive_kernel\role_executors.py`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_web_research_lark_workflow_pressure.py tests\unit\test_stage5_pressure_matrix.py -k "web_research"`
- `npx tsc --noEmit` in `clients\desktop`.
- Dry-run smoke:
  - `python scripts\os_evidence_task_runner.py --mode template --template-id web_research_lark --web-query "latest AI model news" --recipients-json '["Neil"]' --dry-run --out-dir output\os_evidence_console_runs\web_research_lark_dry_run_smoke_full_preview`
- Ledger extraction check confirmed:
  - full Lark preview body can be recovered from `work_order.inputs.work_order_input.message`.
  - complete source URLs are preserved.
  - `delivery_mode=dry_run`.
  - recipient is `Neil`.

Verification result:
- Web Research + Stage5 combined run: 40 passed.
- Desktop TypeScript check passed.
- Python compile check passed.
- Dry-run smoke generated evidence and did not send Lark.
- Full preview extraction check returned a 1000+ character brief with complete source links.

Next step:
- Add a guarded live-run smoke path that requires an explicit safe recipient and writes a stronger post-send verification report.
- Then add Web Research source ranking policy controls so low-quality domains can be automatically downranked after repeated weak summaries.

## Node 45: Web Research Source Quality Policy

Status: completed.

Implementation:
- Web Research now applies source-quality memory before final summary composition, not only during URL fetch ranking.
- If non-degraded sources exist, historically degraded domains are excluded from the final brief candidate set.
- If all sources are degraded, the workflow can still surface them but the quality report marks degraded-source usage for preview.
- The quality report now records `source_health` counts so Evidence can show whether final sources were reliable, unproven, degraded, or unknown.
- Source-quality Evidence now uses the actual reputation fields from `source_quality_memory`:
  - `health`.
  - `reputation_score`.
  - `success_rate`.
  - `use_count`.
  - `average_quality_score`.
- Added a regression test proving that a degraded domain is skipped when a clean alternative exists, preventing repeated bad web pages from leaking into human-facing Lark briefs.

Verification target:
- Web Research pressure tests.
- Stage5 tool-quality tests.
- Python compile check.
- Desktop Rust check.
- diff hygiene check.

Next step:
- Add guarded live-run smoke for Web Research -> Lark with explicit safe recipients, then extend post-send verification evidence so live sends record recipient, message hash, UI/API proof, and quality report in one replayable bundle.

## Node 46: Guarded Web Research Live-Run Smoke Gate

Status: completed.

Implementation:
- Added a runner-level live-send guard for the Web Research -> Lark template.
- Live sends are allowed only when all recipients are in `JACHIN_WEB_RESEARCH_LIVE_RECIPIENT_ALLOWLIST`.
- Default live allowlist is intentionally narrow:
  - `Neil`.
  - `测试备注冒烟草稿`.
- The guard runs twice:
  - before planning/executing Web Research, so unsafe live runs do not even start network/model work.
  - immediately before calling `windows_lark_send_message`, so a corrupted WorkOrder cannot bypass the runner guard.
- Template evidence now records:
  - `live_guard`.
  - `delivery_evidence`.
  - `message_sha256`.
  - delivery mode.
  - post-send verification status.
  - dry-run preview verification status.
  - send result and web research quality report when available.
- Added ledger extraction for Lark delivery proof from `role_execution_finished`, so the evidence file can show whether MessageExecutorAgent actually ran and whether post-send verification passed.
- This directly addresses the previous fake-send class of bugs: a user-facing reply is no longer enough; the evidence must show role execution plus delivery verification.

Verification:
- `python -m py_compile scripts\os_evidence_task_runner.py l3_node\cognitive_kernel\role_executors.py`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_web_research_lark_workflow_pressure.py tests\unit\test_stage5_pressure_matrix.py -k "web_research"`
- `python scripts\os_evidence_task_runner.py --mode template --template-id web_research_lark --web-query "latest AI model news" --recipients-json '["Neil"]' --dry-run --out-dir output\os_evidence_console_runs\web_research_lark_dry_run_guard_smoke`
- `python scripts\os_evidence_task_runner.py --mode template --template-id web_research_lark --web-query "latest AI model news" --recipients-json '["Vivian"]' --out-dir output\os_evidence_console_runs\web_research_lark_live_guard_block_smoke`
- `git diff --check`

Verification result:
- Web Research pressure tests: 43 passed, 77 deselected.
- Dry-run smoke produced preview evidence, message hash, and `dry_run_preview_verified=true` without sending Lark.
- Unsafe live-run to Vivian was blocked before external send with `recipient_not_in_live_allowlist`.
- diff check passed; only existing CRLF warnings remain.

Next step:
- Add an explicit live-run confirmation surface in the Evidence Console for safe recipients, then run one real Web Research -> Lark live smoke only after operator confirmation.
- After that, extend Evidence Console rendering so `live_guard`, `delivery_evidence`, `message_sha256`, and web research quality report are visible without opening raw JSON.

## Node 47: Memory Trust Layer

Status: completed.

Implementation:
- Added a horizontal Memory Trust Layer for all memory producers and recall paths.
- Memory can now carry:
  - `confirmed`: explicitly confirmed by the user.
  - `floating`: system inferred or not yet verified.
  - `rejected`: explicitly rejected or denied by the user.
  - `conflicted`: contradictory memory that must ask before being used or overwritten.
  - `expired`: stale lifecycle memory.
- Memory write requests and memory evidence now include trust metadata:
  - `trust_state`.
  - `trust_reason`.
  - `user_attitude`.
  - `recall_allowed`.
- Lifecycle memory writes infer trust from explicit metadata, user feedback events, confirmation markers, rejection markers, and expiry.
- Recall filters rejected memories by default before ranking.
- Ranking now applies trust weighting:
  - confirmed memories are boosted.
  - floating memories remain normal.
  - conflicted and expired memories are heavily downweighted.
  - rejected memories are filtered or nearly zero-weight if explicitly included for diagnostics.
- Conflicted memories survive as evidence but emit `memory_trust_requires_confirmation`, so the system asks before using or overwriting them.
- Arbiter memory context refs now include trust explanation fields, so Evidence can show why a memory was trusted and whether it was user-confirmed or system-inferred.

Verification:
- `python -m py_compile l3_node\cognitive_kernel\memory_trust.py l3_node\cognitive_kernel\contracts.py l3_node\cognitive_kernel\memory_lifecycle.py l3_node\cognitive_kernel\memory_recall_agent.py l3_node\cognitive_kernel\arbiter.py`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_memory_trust_layer.py`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_memory_recall_precision.py tests\unit\test_memory_quality_governance.py`

Verification result:
- Memory Trust Layer tests: 5 passed.
- Existing memory recall and quality governance tests: 7 passed.

Next step:
- Surface trust-state summaries in the Memory Growth / Evidence Console UI, including counts for confirmed, floating, conflicted, rejected, and expired memories.
- Then add user-facing confirmation flows for conflicted memory so users can promote, reject, or correct memory directly from the console.

## Node 48: Memory Trust Console Visibility

Status: completed.

Implementation:
- Memory Growth status API now exposes a `memory_trust` summary from lifecycle memory.
- The summary includes:
  - confirmed memories.
  - floating / system-inferred memories.
  - conflicted memories that require confirmation.
  - rejected memories that are blocked from recall by default.
  - expired memories.
  - recall blocked count.
- Memory Growth Console now shows a dedicated `Memory Trust Layer` card with trust-state counts and recent review samples.
- Evidence Console Recovery Decision Intelligence now preserves and displays memory trust details for `memory_context_refs`.
- Recovery candidates can now show whether a referenced memory was confirmed, floating, rejected, or conflicted, plus trust reason and weight in the tooltip.
- Added a status test proving Memory Growth API reports trust counts and blocked recall correctly.

Verification:
- `python -m py_compile l3_node\memory_growth_http.py l3_node\cognitive_kernel\memory_trust.py l3_node\cognitive_kernel\memory_lifecycle.py l3_node\cognitive_kernel\memory_recall_agent.py l3_node\cognitive_kernel\arbiter.py`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_memory_trust_layer.py tests\unit\test_memory_growth.py -k "memory_trust or trust_summary or success_path_health"`
- `npx tsc --noEmit` in `clients\desktop`.

Verification result:
- Trust and status tests: 8 passed, 51 deselected.
- Desktop console TypeScript check passed.

Next step:
- Add user-facing trust governance actions in the console:
  - confirm memory.
  - reject memory.
  - mark as conflicted / needs clarification.
  - correct and rewrite memory.
- These actions should update lifecycle memory and write governance evidence so memory trust becomes editable, not only visible.

## Node 49: Memory Trust Governance Actions

Status: completed.

Implementation:
- Added explicit lifecycle memory governance actions:
  - confirm memory.
  - reject memory.
  - mark memory as conflicted.
  - correct memory content.
- The Memory Growth governance API now accepts trust governance actions and writes the result into the same governance evidence stream as other memory growth actions.
- Confirmed memories are boosted, marked user-confirmed, made recallable, and stamped with `last_verified_at_ms`.
- Rejected memories are preserved for audit, but marked non-recallable by default.
- Conflicted memories remain visible but require user confirmation before they should be trusted.
- Corrected memories rewrite the content hash, promote the corrected content to confirmed, and restore recall.
- The Memory Growth Console `Memory Trust Layer` card now exposes governance buttons for recent trust samples.
- Governance side effects include `memory_trust_governed`, so later Evidence and review jobs can explain who changed trust state and why.

Verification:
- `python -m py_compile l3_node\memory_growth_http.py l3_node\cognitive_kernel\memory_lifecycle.py l3_node\cognitive_kernel\memory_trust.py`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_memory_trust_layer.py tests\unit\test_memory_growth.py -k "memory_trust or trust_summary or trust_governance"`
- `npx tsc --noEmit` in `clients\desktop`.

Verification result:
- Trust governance tests: 7 passed, 53 deselected.
- Desktop console TypeScript check passed.

Next step:
- Add a first-class Memory Trust Review queue that shows all pending conflicted/floating/rejected memories, not just the recent samples.
- Add a correction modal so users can rewrite a bad memory from the console instead of only confirming/rejecting/marking conflict.
- Feed trust governance outcomes back into promotion and failure-learning policies, so repeatedly rejected inferred memories lower confidence for similar future writes.

## Node 50: Memory Trust Review Queue and Correction Modal

Status: completed.

Implementation:
- Memory Growth status now returns a first-class `memory_trust.review_queue`.
- The review queue combines:
  - conflicted memories that require confirmation.
  - floating / system-inferred memories that can be promoted or rejected.
  - rejected memories that are kept for audit and possible correction.
- Each queue row includes:
  - trust state.
  - trust reason.
  - trust weight.
  - recall allowed flag.
  - confidence.
  - review priority.
  - full editable content preview.
- Memory Growth Console now prefers the review queue over recent samples.
- The console displays up to 8 actionable memory rows, instead of only 3 recent samples.
- Added a correction editor inside the Memory Trust card.
- Users can now rewrite a bad memory and submit it through `correct_memory`, which promotes corrected content back to confirmed recallable memory.
- The queue keeps rejected memories visible without letting them silently affect recall.

Verification:
- `python -m py_compile l3_node\memory_growth_http.py l3_node\cognitive_kernel\memory_lifecycle.py l3_node\cognitive_kernel\memory_trust.py`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_memory_trust_layer.py tests\unit\test_memory_growth.py -k "memory_trust or trust_summary or trust_governance"`
- `npx tsc --noEmit` in `clients\desktop`.

Verification result:
- Trust governance and trust summary tests: 7 passed, 53 deselected.
- Desktop console TypeScript check passed.

Next step:
- Feed Memory Trust governance outcomes into future memory writes:
  - if similar inferred memories are repeatedly rejected, lower confidence or require confirmation before write.
  - if similar memories are repeatedly confirmed, promote future writes faster.
- Add batch governance actions for trust queue:
  - confirm selected.
  - reject selected.
  - mark selected as conflicted.
- Add Evidence Console rendering for memory trust governance events so a user can see exactly when and why memory trust changed.

## Node 51: Memory Trust Prior for Future Writes

Status: completed.

Implementation:
- Lifecycle memory writes now consume prior trust governance before committing a new memory.
- For each new memory write, the lifecycle store scans active same-type memories and compares:
  - governance key / alias key / entity key / target key.
  - query-term overlap.
  - domain and owner.
  - historical trust state.
- If similar memories were rejected by the user, the new memory is not silently trusted:
  - confidence is reduced.
  - trust state becomes `conflicted`.
  - review is required.
  - recall remains heavily downweighted until the user confirms or corrects it.
- If similar memories were confirmed by the user, the new write is promoted faster:
  - confidence is boosted.
  - trust reason records `trust_prior:similar_memory_confirmed_by_user`.
  - the write is allowed to become confirmed when the pattern is strong enough.
- Trust prior evidence is attached to the lifecycle record, including:
  - matched memory ids.
  - match reason.
  - similarity.
  - confirmed / rejected / conflicted strength.
  - recommended state.
  - confidence delta.
- Ledger event `memory_lifecycle_write` now includes the computed `trust_prior`, so later Evidence and review jobs can explain why the write was downgraded or promoted.

Verification:
- `python -m py_compile l3_node\cognitive_kernel\memory_lifecycle.py l3_node\cognitive_kernel\memory_trust.py`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_memory_trust_layer.py tests\unit\test_memory_growth.py -k "memory_trust or trust_summary or trust_governance or trust_prior"`
- `python -m py_compile l3_node\memory_growth_http.py l3_node\cognitive_kernel\memory_lifecycle.py l3_node\cognitive_kernel\memory_recall_agent.py l3_node\cognitive_kernel\arbiter.py`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_memory_trust_layer.py tests\unit\test_memory_growth.py tests\unit\test_memory_recall_precision.py tests\unit\test_memory_quality_governance.py`

Verification result:
- Trust prior focused tests: 9 passed, 53 deselected.
- Broader memory regression tests: 69 passed.

Next step:
- Add batch governance actions for the trust review queue.
- Add Evidence Console rendering for `memory_lifecycle_trust_governance` and write-time `trust_prior`.
- Add periodic trust-prior analytics: show which inferred memory patterns are repeatedly rejected, which are becoming stable, and which should be promoted into long-term methodology.

## Node 52: Memory Trust Batch Governance and Evidence Playback

Status: completed.

Implementation:
- Memory Growth Console now supports batch governance for the visible trust review queue:
  - batch confirm.
  - batch reject.
  - batch mark conflicted.
- Batch trust governance uses a larger queue-oriented safety limit than ordinary recommendations, so a user can clear multiple memory trust items without clicking one by one.
- OS Evidence aggregation now collects:
  - `memory_lifecycle_trust_governance` events.
  - `memory_trust_governance` / `memory_trust_governed` objects.
  - `memory_lifecycle_write` events.
  - write-time `trust_prior` records.
- Evidence Console now renders a dedicated `Memory Trust Evidence` block that explains:
  - which memory was confirmed / rejected / marked conflicted.
  - whether recall is allowed or blocked.
  - why a future memory write was promoted, downgraded, or forced into review.
  - how many similar confirmed / rejected / conflicted memories influenced the write.
- Added Rust collector tests for memory trust governance and write-time trust prior events.

Verification:
- `python -m py_compile l3_node\memory_growth_http.py l3_node\cognitive_kernel\memory_lifecycle.py l3_node\cognitive_kernel\memory_trust.py`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_memory_trust_layer.py tests\unit\test_memory_growth.py -k "memory_trust or trust_summary or trust_governance or trust_prior"`
- `npx tsc --noEmit` in `clients\desktop`.
- `cargo test --manifest-path clients\desktop\src-tauri\Cargo.toml --lib commands::os_evidence::tests::collects_memory --target-dir clients\desktop\src-tauri\target\codex-check`

Verification result:
- Trust prior focused tests: 9 passed, 53 deselected.
- Desktop console TypeScript check passed.
- Rust OS Evidence memory trust collector tests: 2 passed.

Next step:
- Add Memory Trust analytics:
  - identify repeatedly rejected inferred-memory patterns.
  - identify confirmed patterns ready for promotion into long-term method memory.
  - surface trust drift and stale confirmed facts.
- Feed these analytics into Daily Review and Memory Growth reports so the system can actively recommend which memories to promote, revise, or retire.

## Node 53: Memory Trust Analytics and Daily Review Integration

Status: completed.

Implementation:
- Added Memory Trust analytics on top of the lifecycle memory trust index.
- The analytics layer now groups memories into pattern signatures using:
  - memory type.
  - domain / skill scope.
  - normalized content terms.
  - trust state and verification signals.
- It identifies five curation categories:
  - repeatedly rejected inferred patterns.
  - confirmed patterns ready for promotion into long-term method memory.
  - conflict clusters where confirmed and rejected memories overlap.
  - floating hotspots that appear often but have no user confirmation.
  - stale confirmed facts that need re-verification.
- Memory Growth status now exposes these analytics under `monitoring.memory_trust.analytics`.
- Memory Growth health now includes:
  - rejected trust pattern count.
  - promotion candidate count.
  - stale confirmed count.
- Memory Growth Console now renders a `Trust Analytics` section inside the Memory Trust card.
- Daily Review now embeds `memory_trust_analytics` in its patch JSON and markdown report.
- Daily Review warnings now mention rejected memory patterns and stale confirmed memories.

Verification:
- `python -m py_compile l3_node\memory_growth_http.py l3_node\cognitive_kernel\daily_review.py`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_memory_growth.py -k "memory_trust"`
- `npx tsc --noEmit` in `clients\desktop`.

Verification result:
- Memory trust focused tests: 3 passed, 53 deselected.
- Desktop console TypeScript check passed.

Next step:
- Connect Memory Trust analytics to automatic governance recommendations:
  - rejected patterns should recommend deny/ask-first policies.
  - promotion candidates should create method-memory proposals.
  - stale confirmed facts should become lightweight re-confirmation prompts.
- Add an exportable review artifact for trust analytics so product and debugging reviews can inspect how the memory system is becoming more reliable over time.

## Node 54: Memory Trust Analytics to Governance Recommendations

Status: completed.

Implementation:
- Memory Trust analytics now feeds the unified governance recommendation list.
- Repeatedly rejected inferred-memory patterns generate `review_rejected_memory_pattern` recommendations.
- Stable confirmed memory patterns generate `promote_memory_pattern` recommendations.
- Stale confirmed memories generate `revalidate_confirmed_memory` recommendations.
- Added governance execution handlers for these new actions:
  - rejected patterns write review artifacts under `conflicts/memory_trust`.
  - promotion candidates write method-memory proposals under `playbooks/method_memory`.
  - stale confirmed memories write revalidation requests under `conflicts/memory_revalidation`.
- Governance history dedupe now understands trust-pattern actions, so the same pattern does not keep resurfacing after it has already been reviewed.
- Recommendation summaries now support `pattern_key`, `sample`, `recommendation`, and `memory_id` fields.
- This keeps the system safe: analytics does not silently mutate trusted memory. It creates reviewable artifacts that Daily Review and later governance stages can consume.

Verification:
- `python -m py_compile l3_node\memory_growth_http.py l3_node\cognitive_kernel\daily_review.py`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_memory_growth.py -k "memory_trust"`
- `npx tsc --noEmit` in `clients\desktop`.

Verification result:
- Memory trust focused tests: 3 passed, 53 deselected.
- Desktop console TypeScript check passed.

Next step:
- Add a Trust Governance Review artifact/export page:
  - show which analytics recommendation created which artifact.
  - show whether the artifact later became confirmed memory, method memory, or a rejected pattern.
  - track conversion rate from trust recommendation to durable knowledge.
- This will make the memory system measurable: not just "remembered more", but "converted uncertain memory into reliable knowledge".

## Node 55: Trust Governance Review and Conversion Tracking

Status: completed.

Implementation:
- Added `trust_governance_review` to Memory Growth monitoring.
- The review layer reads governance reports and tracks whether Memory Trust recommendations became durable review artifacts.
- It measures:
  - current trust recommendations.
  - executed trust governance actions.
  - converted governance actions.
  - pending recommendations.
  - failed executions.
  - conversion rate.
- It recognizes conversion side effects:
  - `memory_trust_rejected_pattern_review_written`.
  - `memory_trust_method_memory_proposal_written`.
  - `memory_trust_revalidation_request_written`.
- Memory Growth Console now includes a `Trust Governance Review` card showing:
  - suggested / executed / converted / pending / failed.
  - conversion percentage.
  - recent converted, pending, and failed rows.
- Memory Growth health now exposes `trust_governance_conversion_rate`.
- This creates an observable chain from uncertain memory to review artifact to long-term method-memory proposal.

Verification:
- `python -m py_compile l3_node\memory_growth_http.py l3_node\cognitive_kernel\daily_review.py`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_memory_growth.py -k "memory_trust"`
- `npx tsc --noEmit` in `clients\desktop`.

Verification result:
- Memory trust focused tests: 3 passed, 53 deselected.
- Desktop console TypeScript check passed.

Next step:
- Feed Trust Governance Review conversion data into Weekly Review / Daily Review scoring:
  - high conversion rate should increase memory governance health.
  - repeated pending trust recommendations should raise follow-up priority.
  - failed conversion should create recovery hints for the memory governance agent.
- This will close the loop from memory trust recommendation to measurable governance effectiveness.

## Node 56: Trust Governance Conversion into Review Scoring

Status: completed.

Implementation:
- Memory Growth governance effectiveness now consumes `trust_governance_review`.
- Trust-governance conversion rate can raise the governance effectiveness score when review actions successfully create durable artifacts.
- Pending trust recommendations and failed trust conversions now lower the score and produce explicit follow-up recommendations.
- Governance effectiveness signals now include:
  - `trust_governance_converted`
  - `trust_governance_pending`
  - `trust_governance_failed`
- Weekly Review now records trust-governance metrics in the lifecycle report summary:
  - conversion rate
  - pending count
  - failed count
- `indexes/governance_effectiveness.json` now stores trust-governance conversion metrics in the latest row and history rows.
- Governance trends now expose trust conversion rate, pending count, and failed count so future 7/14/30 day dashboards can explain whether memory-quality governance is improving.

Verification:
- `python -m py_compile l3_node\memory_growth_http.py l3_node\cognitive_kernel\weekly_review.py l3_node\cognitive_kernel\daily_review.py`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_memory_growth.py -k "memory_trust_analytics or governance_effectiveness_index"`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_memory_trust_layer.py tests\unit\test_memory_growth.py tests\unit\test_memory_recall_precision.py tests\unit\test_memory_quality_governance.py`

Verification result:
- Targeted review scoring tests: 2 passed, 54 deselected.
- Full memory regression: 70 passed.

Next step:
- Add Memory Trust governance recovery hints:
  - pending trust recommendations should be batched into a follow-up queue.
  - failed trust conversions should produce a suggested safer governance action.
  - Weekly Review should include a small "memory governance next action" list that the console can run directly.
- This will make trust governance more operational: not only scoring what happened, but proposing the next safest action.

## Node 57: Trust Governance Follow-up Queue and Next Actions

Status: completed.

Implementation:
- `trust_governance_review` now produces an operational follow-up queue.
- Pending trust recommendations become `pending_trust_governance` follow-up rows.
- Failed trust conversions become `failed_trust_conversion` follow-up rows with safer retry hints.
- Follow-up rows are sorted by priority score, so failed conversions are handled before lower-risk pending work.
- `trust_governance_review.summary` now exposes:
  - `follow_up_count`
  - `next_action_count`
- `trust_governance_review.next_actions` now emits executable governance rows compatible with the existing `/api/v1/memory-growth/governance` endpoint.
- Weekly Review now writes:
  - `trust_governance_follow_up_count`
  - `trust_governance_next_action_count`
  - `memory_governance_next_actions`
- Weekly recommendations now explicitly tell the operator to run the memory-governance next-action list when pending or failed trust work exists.
- Memory Growth Console now shows next actions inside the Trust Governance Review card and can execute them using the same governance action path as other recommendations.

Verification:
- `python -m py_compile l3_node\memory_growth_http.py l3_node\cognitive_kernel\weekly_review.py l3_node\cognitive_kernel\daily_review.py`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_memory_growth.py -k "memory_trust_analytics or trust_governance_follow_up"`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_memory_trust_layer.py tests\unit\test_memory_growth.py tests\unit\test_memory_recall_precision.py tests\unit\test_memory_quality_governance.py`
- `npx tsc --noEmit` in `clients\desktop`.

Verification result:
- Targeted trust-governance tests: 2 passed, 55 deselected.
- Full memory regression: 71 passed.
- Desktop console TypeScript check passed.

Next step:
- Add Memory Governance auto-run policy:
  - safe trust follow-ups can run automatically during Daily Review when strategy mode allows it.
  - failed conversions should retry once with validated input, then escalate to manual review.
  - next-action execution results should feed back into governance effectiveness and trust conversion trends.
- This will move memory governance from operator-assisted execution toward safe autonomous maintenance.

## Node 58: Memory Governance Auto-run Policy

Status: completed.

Implementation:
- Added `apply_memory_growth_auto_governance`.
- The auto policy consumes `trust_governance_review.next_actions` instead of inventing new governance work.
- Only safe trust-governance actions are eligible for automatic execution:
  - `review_rejected_memory_pattern`
  - `promote_memory_pattern`
  - `revalidate_confirmed_memory`
- Unsupported or malformed actions are skipped with an explicit reason.
- Failed trust conversions are retried automatically at most once per action and pattern key.
- Auto governance writes:
  - an `.auto.json` governance report under `reviews/governance`
  - raw evidence stream `auto_governance`
  - execution results, skipped rows, retry-limit decisions, and report paths
- Daily Review now invokes the auto policy before writing the review patch.
- Daily Review patch now includes `memory_governance_auto_policy`.
- Daily Review Markdown now renders a `Memory Governance Auto Policy` section when work was executed, skipped, failed, or blocked.

Verification:
- `python -m py_compile l3_node\memory_growth_http.py l3_node\cognitive_kernel\daily_review.py l3_node\cognitive_kernel\weekly_review.py`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_memory_growth.py -k "trust_governance_follow_up or memory_trust_analytics or daily_review_runs_memory_governance_auto_policy"`

Verification result:
- Targeted auto-governance tests: 3 passed, 55 deselected.

Next step:
- Add a configurable memory-governance run mode:
  - `off`
  - `manual`
  - `safe_auto`
- Expose the current mode and last auto-run result in the Memory Growth Console.
- Add a 7/14/30 day trend for auto governance:
  - executed count
  - skipped count
  - retry-limit count
  - failed count
- This will make autonomous memory maintenance visible and controllable instead of hidden behind Daily Review.

## Node 59: Configurable Memory Governance Auto Mode

Status: completed.

Implementation:
- Added persisted auto-governance policy at `indexes/memory_governance_auto_policy.json`.
- Supported run modes:
  - `off`: Daily Review records that auto governance is disabled and executes nothing.
  - `manual`: Daily Review records that auto governance is manual-only and executes nothing.
  - `safe_auto`: Daily Review runs bounded safe trust-governance follow-ups.
- Added `/api/v1/memory-growth/auto-governance-policy` for saving the mode and per-run limit.
- `memory_growth_status.monitoring` now exposes:
  - `memory_governance_auto_policy`
  - `memory_governance_auto_latest`
  - `memory_governance_auto_trends.days_7 / days_14 / days_30`
- Auto-governance trends track:
  - run count
  - executed count
  - failed count
  - skipped count
  - retry-limit count
- Memory Growth Console now has a `Memory Governance Auto Policy` card:
  - current mode
  - max items per run
  - latest run summary
  - trend chart
  - mode buttons for `off / manual / safe_auto`

Verification:
- `python -m py_compile l3_node\memory_growth_http.py l3_node\cognitive_kernel\daily_review.py l3_node\cognitive_kernel\weekly_review.py`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_memory_growth.py -k "memory_governance_auto_policy or daily_review_runs_memory_governance_auto_policy or trust_governance_follow_up"`
- `npx tsc --noEmit` in `clients\desktop`.

Verification result:
- Targeted auto-policy tests: 3 passed, 56 deselected.
- Desktop console TypeScript check passed.

Next step:
- Add governance mode policy learning:
  - if auto-governance failures rise, recommend switching from `safe_auto` to `manual`.
  - if conversion rate stays high and retry-limit stays low, recommend keeping or enabling `safe_auto`.
  - if `off` is used for too long while pending trust work grows, surface a dashboard warning.
- This will make the policy adaptive without taking control away from the operator.

## Node 60: Adaptive Auto Governance Mode Recommendation

Status: completed.

Implementation:
- Added `memory_governance_auto_recommendation` to `memory_growth_status.monitoring`.
- The recommendation layer does not change modes automatically. It only recommends the safest operator-facing mode.
- Inputs used by the recommender:
  - current auto-governance mode
  - 14-day auto-governance runs
  - executed count
  - failed count
  - skipped count
  - retry-limit count
  - trust-governance pending / failed / next-action count
  - trust conversion rate
  - governance effectiveness score
- Recommendation behavior:
  - `safe_auto` with repeated failures or retry limits recommends `manual`.
  - clean `safe_auto` with healthy conversion recommends staying on `safe_auto`.
  - `manual` with healthy conversion and growing safe next actions can recommend `safe_auto`.
  - `off` with any trust-governance pressure recommends `manual`.
- Memory Growth Console now shows the recommendation in the auto-governance card:
  - current mode
  - recommended mode
  - whether a change is recommended
  - top reasons

Verification:
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_memory_growth.py -k "memory_governance_auto"`
- `python -m py_compile l3_node\memory_growth_http.py l3_node\cognitive_kernel\daily_review.py l3_node\cognitive_kernel\weekly_review.py`
- `npx tsc --noEmit` in `clients\desktop`.

Verification result:
- Targeted adaptive auto-governance tests: 4 passed, 57 deselected.
- Python compile passed.
- Desktop console TypeScript check passed.

Next step:
- Add Memory Governance Mode Recommendation into Daily Review and Weekly Review summaries.
- This will connect policy recommendations to the same review artifact chain as concepts, playbooks, trust governance, and growth metrics, so long-term trend review can explain why a mode was recommended.

## Node 61: Auto Governance Recommendation in Daily and Weekly Reviews

Status: completed.

Implementation:
- Daily Review now snapshots `memory_governance_auto_recommendation` after auto-governance execution.
- Daily Review patch now includes:
  - `memory_governance_auto_policy`
  - `memory_governance_auto_recommendation`
- Daily Review Markdown now renders:
  - Memory governance auto executed / failed counts.
  - Current and recommended auto-governance mode.
  - Whether the mode should change.
  - Recommendation reasons.
- Weekly Review now reads the same Memory Growth status snapshot and writes `memory_governance_auto`.
- Weekly Review summary now includes:
  - `memory_governance_auto_current_mode`
  - `memory_governance_auto_recommended_mode`
  - `memory_governance_auto_should_change`
- Weekly Review recommendations now include an explicit instruction when the auto-governance mode should be reviewed.
- Weekly Review Markdown now has a `Memory Governance Auto Recommendation` section.

Verification:
- `python -m py_compile l3_node\cognitive_kernel\daily_review.py l3_node\cognitive_kernel\weekly_review.py l3_node\memory_growth_http.py`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_memory_growth.py -k "memory_governance_auto or weekly_review_includes_governance_effect_summary"`

Verification result:
- Targeted Daily / Weekly auto-governance review tests: 5 passed, 56 deselected.

Next step:
- Build an automatic governance-mode history index.
- The index should store each Daily / Weekly recommendation as a time series:
  - current mode
  - recommended mode
  - should change
  - reasons
  - trust pressure metrics
  - auto-governance failure / retry-limit metrics
- This will let the system answer: "Over the last 30 days, did memory governance become safer or noisier?"

## Node 62: Auto Governance Mode History Index

Status: completed.

Implementation:
- Added `l3_node/cognitive_kernel/memory_governance_auto_index.py`.
- The new index is written to `indexes/memory_governance_auto_mode_history.json`.
- Each Daily / Weekly recommendation now becomes a time-series row with:
  - source
  - current mode
  - recommended mode
  - should-change flag
  - severity and reasons
  - trust pressure metrics
  - auto-governance executed / failed / skipped / retry-limited counts
  - report path
- The index keeps rolling `days_7`, `days_14`, and `days_30` trend rows.
- The index summary classifies governance direction as:
  - `stable`
  - `watch`
  - `noisy`
  - `unknown`
- Daily Review now writes `memory_governance_auto_history` into its patch and Markdown.
- Weekly Review now writes `memory_governance_auto_history`, includes `memory_governance_auto_history_risk` in summary, and renders a Markdown section.
- `memory_growth_status.monitoring` now exposes `memory_governance_auto_mode_history`.
- Memory Growth Console now shows:
  - 30-day history records
  - mode-change recommendations
  - auto failures
  - retry-limited count
  - mode-history trend chart

Verification:
- `python -m py_compile l3_node\cognitive_kernel\daily_review.py l3_node\cognitive_kernel\weekly_review.py l3_node\memory_growth_http.py l3_node\cognitive_kernel\memory_governance_auto_index.py`
- `python -m pytest -q -o addopts= --tb=short tests\unit\test_memory_growth.py -k "memory_governance_auto"`
- `npx tsc --noEmit` in `clients\desktop`.

Verification result:
- Python compile passed.
- Targeted auto-governance history tests: 4 passed, 57 deselected.
- Memory regression suite: 75 passed.
- Desktop console TypeScript check passed.

Next step:
- Add governance-mode attribution analysis.
- The system should explain which concrete signals caused mode instability, for example repeated retry limits, rejected-memory pressure, failed trust conversions, or low conversion rate.
