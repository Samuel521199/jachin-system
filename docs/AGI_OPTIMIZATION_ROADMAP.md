# Jachin AGI 优化路线图

**版本**：2026-05-18（z）  
**性质**：基于现有代码架构的 AGI 级深度优化方案，从单机 Agent 走向真正的自主智能体系统。  
**代码基线**：`L3_LIMITATIONS_AND_REMEDIATION_ROADMAP.md`（2026-04-02）+ `JACHIN_MEMORY_ARCHITECTURE.md` + 仓库实现快照。

---

## 〇、未实现项 · 分期与诚实范围（2026-05-18）

路线图内凡标 **⏳** 的条目，多数对应**多迭代 / 多季度**的架构或产品能力（集群调度、记忆 Wing 体系、DAG 自动续跑、完整 Guardrails 等）。**单次开发周期无法也不应声称「已全部实现」**；落地进度表中的 **✅** 仅代表**当前仓库已合并**的行为。

| 能力域 | 文档中的 ⏳ 要点（摘） | 诚实状态 | 建议分期 |
|--------|----------------------|----------|----------|
| 并发 / GlobalTaskRegistry | 集群 SSOT、SessionInstructionQueue **全量队列化**、飞书真·双轨并行 | **✅/⏳**：**AT**·y `global_task_registry.py` SQLite 跨进程 SSOT + `resource_tags` 抢占信号（`JACHIN_GLOBAL_REGISTRY_ENABLE=1` + `JACHIN_GLOBAL_REGISTRY_PREEMPT=1`）；**AU**·y `session_instruction_queue.py` 真·双轨并行（SERIAL/PARALLEL 模式，`JACHIN_SIQ_MODE=PARALLEL`）；⏳ Redis 集群 / 跨机 SSOT | Phase C 与编排器联动 |
| `resource_tags` | 细粒度**抢占**调度 | **✅/⏳**：**AT**·y `check_and_preempt` 检测 `resource_tags` 重叠 + 标记 `preempted` + 调用 `request_cancel_run`（同进程）；⏳ 跨机 HTTP 信号 | Phase C 与编排器联动 |
| 多 Agent | StructuredResultMerger、动态角色 | **✅**（**AA**·p）：`mode: discuss` + `StructuredResultMerger`；**AB**·p：inline role 安全沙箱；**AF**·s：普通 `delegate` 并行子任务 Observation 经 **`merge_parallel`**（索引表 + 详块）；**AG**·t：`nexus_config` / 环境变量 讨论轮次与子任务 `max_iterations` + 可选 **Experience** `multi_agent:*` 落盘；**AH**·t：飞书第二条**多子句**规则优先级仲裁 | 完整 discuss 多轮调优 ⏳；全量 LLM 意图仲裁 ⏳ |
| TaskDAG | Planner **自动**维护、`task_plan.md` 全量迁移 | **✅/⏳**：**AV**·y `dag_planner.py` LLM 自动拆解 → `active.json`（`JACHIN_DAG_AUTO_PLAN=1`，启发式触发 + `force` 参数，保留已完成节点状态）；⏳ ReAct 中途自动重规划 | **H**/**V** 已覆盖手工/工具维护与只读诊断 |
| Hook / 韧性 | DAG **回放**、失败自动策略链 | **✅/⏳**：**Q**·r 支持 `run_id_exact=1` 精确拉单 run Hook 序列（轻量回放探针）；**AO**·v `probe_dag_resume` / `apply_dag_resume`（hook_events + active.json 找待续跑节点，重置 pending + `POST /api/v1/registry/dag-resume`）；**AR**·w DAG Handoff Package（`export_dag_handoff` / `import_dag_handoff`，JSON 跨节点传输，`POST /dag-handoff/export` + `/import` + `/list`）；**AS**·x `dag_coordinator.py` 节点注册表 + 分布式 DAG 锁（SQLite CAS + TTL）+ Peer 发现（本地/HTTP）+ `auto_handoff_to_peer`（`JACHIN_COORDINATOR_ENABLE=1`）；`POST /coordinator/dag-claim` / `DELETE /dag-claim/{id}` / `GET /peers` / `GET /dag-locks` / `GET /coordinator/info`；心跳循环 on_startup；⏳ 专用 Coordinator 服务器 / Redis 支持 |
| 记忆 / Experience | 四维 Wing 分级、跨 Agent 共享、自动沉淀策略 | **✅/⏳**：**Y**·p 遗忘曲线时间权重（`JACHIN_NEXUS_TIME_DECAY_WEIGHT`）；**AI**·u Wing 重要性乘数分级（`JACHIN_NEXUS_WING_IMPORTANCE_WEIGHT`，默认 0.15）；**AL**·v `wing_registry.py` 五 Wing 规范注册表（Episodes/Knowledge/Procedures/Core/Inbox）+ `normalize_wing` 写入归一化 + `JACHIN_WING_IMPORTANCE_OVERRIDE` 覆盖；**AW**·y `JACHIN_NEXUS_SHARED_PATH` 共享 SQLite 跨 Agent 记忆 + `JACHIN_NEXUS_VECTOR_LEAD=1` 向量主导检索（时间/Wing 权重缩至 0.3 倍）；⏳ 跨机向量数据库 | 见 §四 |
| 无人值守 | 可观测面板、Level 3 系统级自愈、动态意图、完整 Guardrails | **✅/⏳**：**Z**·p；**AC**·p；**AD**·q `GET /api/v1/autonomy/status` + `llm_budget` 今日用量落盘；**AJ**·u condition 类意图内置条件评估；**AK**·u 失败意图自动重置 + 自愈通知；**AM**·v LLM fallback 条件评估（`JACHIN_CONDITION_LLM_EVAL=1`）；**AN**·v `GuardrailsChecker`（五维护栏）；**AP**·w `dag_guardrails.py` DAG 级跨 Node 预算控制（SQLite 持久，`JACHIN_DAG_GUARDRAILS_ENABLE=1`）；**AQ**·w `level3_healer.py` Level 3 Experience RAG 辅助诊断（`JACHIN_LEVEL3_HEALER_ENABLE=1`，自动 rich notify + optional auto-inject）；⏳ L2 Coordinator / 完整 DAG 编排 Guardrails | 见 §五阶段树 |
| 前台 SOP / 提示词热同步（磁盘 → ReAct） | 与「自动进化」「L1 拉包」解耦：受管 Markdown 写盘后，当前会话 **system prompt** 尽快与磁盘对齐（**当前落地以 HR 标记段为主**） | **✅**：**ab**·**P1** 每轮 `skill_md_hot_reload` 读盘刷新 HR 段（`JACHIN_SKILL_MD_HOT_RELOAD`）；**ac**·**P2** 任意写盘方在成功 `write` 后调用 **`notify_skill_md_changed_from_disk_write`**（`JACHIN_SKILL_MD_INLINE_ENABLE`）：来源含 **AY** `skill_evolver`、**L1** `skill_sync_guard.handle_upstream_update`（覆盖 / 合并 / 强制覆盖 / 首次安装）；⏳ 非 HR 全域 | `skill_md_hot_reload.py`；与 §六 Skill 子项正交 |
| Skill 自动进化 | SKILL.md 格式统一 + 错误自愈后自动更新 SKILL.md + 进化日志 + 零感知 | **✅**：**AY**·z/`skill_evolver`；**ab** HR **每轮**热重载 `skill_md_hot_reload`（`JACHIN_SKILL_MD_HOT_RELOAD`）；**ac** **inline** 写盘 notify + `_skill_sop_dirty` + 世代（`JACHIN_SKILL_MD_INLINE_ENABLE`，§六 P2）；✅ **P3** `evolution_peers` + `JACHIN_SKILL_COEVOLVE_ENABLE`（一跳协同，§六）；⏳ 非 HR 域统一 | 见 §六 |
| 飞书场景四 | 补充句 **热并入** 当前轮 Observation、LLM 冲突仲裁、并行汇总展示 | **✅**：**AE**·r：**HTTP** 同会话二条在等锁时写入 `session_hot_user_inject`，当前 `run_agent` 每轮 LLM 前 **drain** 并入 `full_messages`；**R** 轮询可带 `session_hot_user_pending`；飞书可选 `JACHIN_IM_SESSION_HOT_INJECT=1`（默认关，防与 **X** 重复）；**AF**·s：并行 `delegate` 子任务 **Markdown 索引表 + 结构化详块**；讨论模式 Observation 前缀 **`[Discussion]` 摘要行**；**AH**·t：第二条文案含**多段**（换行/；）时 **interrupt > parallel > supplement > queue** 取最高优先级子句（**非 LLM**）；**AX**·y `classify_busy_followup_llm` 全对话上下文 LLM 冲突仲裁（`JACHIN_IM_LLM_CONFLICT_RESOLVE=1`，规则 `interrupt/parallel` 高置信直通，`queue` 走 LLM，超时回退规则）；`dispatcher` 接入 `analyze_second_im_intent_llm_sync` |

**本轮增量（z）**：**AY** `autonomy/skill_evolver.py` Skill 自动进化引擎（`analyze_and_evolve_skill`：LLM 生成最小 patch → 改动比例验证（≤30%）→ frontmatter 保护 → SKILL.md 备份快照 → 写入新版 → JSONL 进化日志；`awareness_loop._try_skill_evolution_after_success` 成功路径自动触发；`JACHIN_SKILL_EVOLVE_ENABLE=1`，干运模式 `JACHIN_SKILL_EVOLVE_DRY_RUN=1`，阈值 `JACHIN_SKILL_EVOLVE_MIN_SUCCESSES`；零用户感知）；§〇/§六/落地表 **AY**/阶段树/§八（z）同步。  
**本轮增量（ad）**：§六 **P3** 多 Skill 协同进化初版：`skill_evolver` 读取进化**前** SKILL.md frontmatter 的 `evolution_peers` / `co_evolve_peers`；主技能 **proactive/healing** 成功 `applied` 且 `JACHIN_SKILL_COEVOLVE_ENABLE=1` 时，对至多 `JACHIN_SKILL_COEVOLVE_MAX_PEERS` 个 peer 跑 `co_evolve` LLM 补丁（**仅一跳**，peer 不再下传）；JSONL 记 `trigger=co_evolve`、`co_evolve_from`；`docs/SKILL_MD_SPEC.md`、`.cursor/rules/091` 同步。
**本轮增量（ae）**：**横切**「前台 SOP / 提示词热同步」（§〇 新行，与 Skill 进化正交）：`skill_sync_guard.handle_upstream_update` 在成功 **首次安装 / 覆盖 / smart merge / 强制覆盖** 写盘后与 **AY** 同源调用 `notify_skill_md_changed_from_disk_write`；落地表 **AZ**。
**本轮增量（n）**：**W** — IM 线程池待处理深度只读 HTTP；`resource_tags` 写入 **D** / **R**。  
**本轮增量（o）**：**X** — 飞书 IM **排队摘录**（`prior>0` 时入账，持锁执行前合并进本轮 `user_input` 前缀）；关闭：`JACHIN_IM_QUEUE_ROLLUP_DISABLE=1`。  
**本轮增量（p）**：**Y** 遗忘曲线；**Z** PersistedIntent；**AA** mode:discuss + StructuredResultMerger；**AB** 动态角色安全沙箱；**AC** AwarenessLoop + ProactiveReporter。  
**本轮增量（q）**：**AD** 可观测面板 `GET /api/v1/autonomy/status`（诊断 Token）；`llm_budget` `record_daily_llm_usage` + `get_today_token_usage`/`get_token_day_budget`；`background_task_service.get_background_queue_metrics`；delegate 子任务 `_parent_allowed_skills` 自 `ctx.metadata["_skills"]` 传入以修复 **AB** 动态角色交集。
**本轮增量（r）**：**AE** `session_hot_user_inject.py`（HTTP 等锁前入账 + ReAct 每轮 LLM 前并入；**R** `session_hot_user_pending`；关 `JACHIN_SESSION_HOT_USER_INJECT_DISABLE`；飞书 `JACHIN_IM_SESSION_HOT_INJECT`）；**Q** /query `run_id_exact` 精确筛 run_id。
**本轮增量（s）**：**AF** `StructuredResultMerger.merge_parallel` 增加 **Markdown 索引表**；`agent_core` 普通并行 `delegate` 走 **merge_parallel**（替代简单拼接）；`mode: discuss` Observation 前缀 **`DiscussionResult.format_summary()`**；修订表 **（q）** 补全；§〇/落地表/§1.4.5/§2.5/§2.6 同步。
**本轮增量（t）**：**AG** `multi_agent` 讨论 **max_rounds / item_max_iterations**（`nexus_config.json` + `JACHIN_DISCUSS_MAX_ROUNDS` / `JACHIN_DISCUSS_ITEM_MAX_ITER`）+ **`JACHIN_EXPERIENCE_RECORD_MULTI_AGENT`** → `save_multi_agent_episode` + Hook；**AH** `classify_busy_followup` 多子句优先级；落地表 **AG**/**AH**、§〇、**P**、§1.4.5、§2.6、§四 P2、阶段树、§八（t）同步。
**本轮增量（y）**：**AT** `global_task_registry.py` SQLite 跨进程 GlobalTaskRegistry + `resource_tags` 抢占调度（`check_and_preempt`，`JACHIN_GLOBAL_REGISTRY_PREEMPT=1`）；**AU** `session_instruction_queue.py` SessionInstructionQueue 全量队列化（SERIAL/PARALLEL 双模式，`JACHIN_SIQ_MODE=PARALLEL`，弱引用会话注册表）；**AV** `dag_planner.py` TaskDAG LLM 自动拆解写回 `active.json`（启发式 + `force`，保留已完成节点，`JACHIN_DAG_AUTO_PLAN=1`）；**AW** `memory_backend.py` 共享 SQLite 跨 Agent 记忆（`JACHIN_NEXUS_SHARED_PATH`）+ 向量主导检索（`JACHIN_NEXUS_VECTOR_LEAD=1`）；**AX** `im_second_instruction.py` `classify_busy_followup_llm` LLM 冲突仲裁 + `dispatcher` 接入（`JACHIN_IM_LLM_CONFLICT_RESOLVE=1`）；§〇/落地表/阶段树/§八（y）同步。
**本轮增量（y）**：**AT** `global_task_registry.py` SQLite 跨进程 GlobalTaskRegistry + `resource_tags` 抢占调度（`check_and_preempt`，`JACHIN_GLOBAL_REGISTRY_PREEMPT=1`）；**AU** `session_instruction_queue.py` SessionInstructionQueue 全量队列化（SERIAL/PARALLEL 双模式，`JACHIN_SIQ_MODE=PARALLEL`，弱引用会话注册表）；**AV** `dag_planner.py` TaskDAG LLM 自动拆解写回 `active.json`（启发式 + `force`，保留已完成节点，`JACHIN_DAG_AUTO_PLAN=1`）；**AW** `memory_backend.py` 共享 SQLite 跨 Agent 记忆（`JACHIN_NEXUS_SHARED_PATH`）+ 向量主导检索（`JACHIN_NEXUS_VECTOR_LEAD=1`）；**AX** `im_second_instruction.py` `classify_busy_followup_llm` LLM 冲突仲裁 + `dispatcher` 接入（`JACHIN_IM_LLM_CONFLICT_RESOLVE=1`）；§〇/落地表/阶段树/§八（y）同步。
**本轮增量（x）**：**AS** `dag_coordinator.py` DAG Coordinator Phase 2（节点注册表 + SQLite CAS 分布式锁 + Peer 发现 + `auto_handoff_to_peer` + 六端点 + on_startup 心跳，`JACHIN_COORDINATOR_ENABLE=1`）；§〇/落地表/阶段树/§八（x）同步。  
**本轮增量（w）**：**AP** `dag_guardrails.py` DAG 级跨 Node 预算控制（SQLite 持久，四类上限，续跑前自动检查 + HTTP 诊断）；**AQ** `level3_healer.py` Level 3 Experience RAG 辅助诊断（`JACHIN_LEVEL3_HEALER_ENABLE=1`，rich 报告 + optional auto-inject，`update_extra_meta`）；**AR** `dag_handoff.py` 跨进程 DAG 续跑转交（export/import/list，JSON 包 + 共享目录，`JACHIN_DAG_HANDOFF_DIR`）；§〇/落地表/阶段树/§八（w）同步。  
**本轮增量（v）**：**AL** `wing_registry.py` 规范 Wing 注册表 + `normalize_wing` 写入归一化；**AM** `_evaluate_condition_llm_fallback`（LLM fallback 条件评估，`JACHIN_CONDITION_LLM_EVAL`）；**AN** `guardrails.py` `GuardrailsChecker` 五维护栏 + `agent_core` hook（`JACHIN_GUARDRAILS_ENABLE`）；**AO** `dag_resume.py` `probe/apply_dag_resume` + `POST /api/v1/registry/dag-resume`；§〇/落地表/阶段树/§八（v）同步。  
**本轮增量（v）**：**AL** `wing_registry.py` 规范 Wing 注册表 + `normalize_wing` 写入归一化；**AM** `_evaluate_condition_llm_fallback`（LLM fallback 条件评估，`JACHIN_CONDITION_LLM_EVAL`）；**AN** `guardrails.py` `GuardrailsChecker` 五维护栏 + `agent_core` hook（`JACHIN_GUARDRAILS_ENABLE`）；**AO** `dag_resume.py` `probe/apply_dag_resume` + `POST /api/v1/registry/dag-resume`；§〇/落地表/阶段树/§八（v）同步。  
**本轮增量（u）**：**AI** Wing 重要性乘数（`_WING_IMPORTANCE`、`JACHIN_NEXUS_WING_IMPORTANCE_WEIGHT`，默认 0.15，Procedures 1.30/Knowledge 1.20/Core 1.25）；**AJ** `AutonomousAwarenessLoop` 支持 `condition` 类意图内置条件评估（`JACHIN_CONDITION_INTENT_ENABLE=1`，支持 `disk_free_gb/token_used/token_used_pct/consecutive_failures` 四类）；**AK** `IntentPersister.autoreset_failed` + AwarenessLoop `JACHIN_INTENT_AUTORESET_HOURS` 自动重置 + 飞书自愈通知；§〇/§四/§五/落地表/阶段树/§八（u）同步。

---

## 落地进度（与仓库同步）

| 步骤 | 状态 | 代码/行为 |
|------|------|-----------|
| **A. HTTP 同会话串行** | ✅ | `l3_node/http_server.py`：`_http_agent_session_lock`，`chat_id`/`session_id` 非空则互斥 `run_agent`。 |
| **AU. SessionInstructionQueue 全量队列化** | ✅ | `l3_node/session_instruction_queue.py`：`SIQSession` 每会话独立 asyncio.Queue + worker 协程；SERIAL 模式（有序串行，默认）/ PARALLEL 模式（真·双轨并行，直接 `create_task`，`JACHIN_SIQ_MODE=PARALLEL`）；`_max_parallel` 并发上限（默认 2）；`submit_instruction` 统一入口；弱引用 `_sessions` 自动回收空闲会话；`JACHIN_SIQ_ENABLE=1` 开启。 |
| **AU. SessionInstructionQueue 全量队列化** | ✅ | `l3_node/session_instruction_queue.py`：`SIQSession` 每会话独立 asyncio.Queue + worker 协程；SERIAL 模式（有序串行，默认）/ PARALLEL 模式（真·双轨并行，直接 `create_task`，`JACHIN_SIQ_MODE=PARALLEL`）；`_max_parallel` 并发上限（默认 2）；`submit_instruction` 统一入口；弱引用 `_sessions` 自动回收空闲会话；`JACHIN_SIQ_ENABLE=1` 开启。 |
| **B. 飞书第二条进线 ack + 可打断** | ✅ | `dispatcher`：`_im_chat_inflight`、`_notify_im_when_prior_turn_inflight`、**X** `_im_append_queue_rollup` / `_im_consume_queue_rollup_prefix`（排队多条摘录合并进本轮）；`foreground_run_registry` + `run_agent`；`im_second_instruction` 分流。 |
| **C. Prompt 注入后台负载** | ✅ | `format_combined_runtime_prompt_suffix()`（`task_runtime_registry` + 原 P3 摘要）→ `agent_core` 后缀块。 |
| **D. 轻量 GlobalTaskRegistry（进程内）** | ✅ | `l3_node/task_runtime_registry.py`：登记顶层 `run_agent` 的 `run_id`+通道+可选 **`resource_tags`**（**R**/**W**）；无跨进程、无抢占。 |
| **AT. GlobalTaskRegistry 跨进程 SSOT + resource_tags 抢占调度** | ✅ | `l3_node/global_task_registry.py`：SQLite WAL 跨进程双写（`JACHIN_GLOBAL_REGISTRY_ENABLE=1`）；`register_task` / `unregister_task` 进程内 + SQLite 同步；`check_and_preempt` 检测优先级 + resource_tags 重叠 → 标记 `preempted` + `request_cancel_run`（`JACHIN_GLOBAL_REGISTRY_PREEMPT=1`）；`get_global_registry_summary` 供 HTTP 诊断；P1>P2>P3>P4 优先级枚举；僵尸任务 TTL 清除（`JACHIN_GLOBAL_REGISTRY_TTL`）。 |
| **AT. GlobalTaskRegistry 跨进程 SSOT + resource_tags 抢占调度** | ✅ | `l3_node/global_task_registry.py`：SQLite WAL 跨进程双写（`JACHIN_GLOBAL_REGISTRY_ENABLE=1`）；`register_task` / `unregister_task` 进程内 + SQLite 同步；`check_and_preempt` 检测优先级 + resource_tags 重叠 → 标记 `preempted` + `request_cancel_run`（`JACHIN_GLOBAL_REGISTRY_PREEMPT=1`）；`get_global_registry_summary` 供 HTTP 诊断；P1>P2>P3>P4 优先级枚举；僵尸任务 TTL 清除（`JACHIN_GLOBAL_REGISTRY_TTL`）。 |
| **E. 角色池 YAML + delegate 注入** | ✅ | `skills_repo/agent_roles/role_pool.yaml`；`l3_node/agent_roles_loader.py` → `_build_system_prompt` 的 `delegate_hint` 后缀；`SUB_AGENT_PROMPTS/SKILLS` 增加 `critic`/`executor`/`domain_expert`。 |
| **F. 子 Agent 禁嵌套 delegate** | ✅ | `agent_core` ReAct：`delegate_sub_agent` 通道命中即 JSON Observation 拒绝（与 `max_delegate_depth` 同源处理路径）。 |
| **G. 生命周期 Hook（P0 触发点）** | ✅ | `l3_node/engine/hooks_pipeline.py` 扩充 `on_task_node_*` / `on_retry` / `on_execution_brief` / `on_memory_commit` / `on_experience_learned` 等；`agent_core` 在 delegate 子任务起止、`[ExecutionBrief]` 出口、写回纠偏与伪 MCP/天气纠偏续跑、直连与 ReAct 回合末 `schedule_nexus_turn_commit_async` 之后、L4 Experience 写入成功后触发对应 Hook（无注册则无运行时开销）。 |
| **AV. TaskDAG Planner 自动维护** | ✅ | `l3_node/task_engine/dag_planner.py`：`plan_task_dag(intent)` 调 LLM 生成结构化节点列表（JSON）并写回 `active.json`；启发式 `should_auto_plan`（字符数阈值 + 多步关键词正则，`JACHIN_DAG_AUTO_PLAN=1`）；`force=True` 可绕过启发式；保留已完成节点状态；`_max_nodes`（默认 16）；同步包装 `plan_task_dag_sync`；`JACHIN_DAG_PLAN_MODEL` 可指定规划模型。 |
| **AV. TaskDAG Planner 自动维护** | ✅ | `l3_node/task_engine/dag_planner.py`：`plan_task_dag(intent)` 调 LLM 生成结构化节点列表（JSON）并写回 `active.json`；启发式 `should_auto_plan`（字符数阈值 + 多步关键词正则，`JACHIN_DAG_AUTO_PLAN=1`）；`force=True` 可绕过启发式；保留已完成节点状态；`_max_nodes`（默认 16）；同步包装 `plan_task_dag_sync`；`JACHIN_DAG_PLAN_MODEL` 可指定规划模型。 |
| **H. TaskDAG 轻量 prompt 注入** | ✅ | `l3_node/task_engine/task_dag.py`：`active.json` → `format_active_task_dag_prompt_suffix`；`save_active_task_dag_dict` / `load_task_dag_dict`；`_build_system_prompt` 后缀块 `task_dag_active_json`；**V** `GET /api/v1/registry/task-dag-active`（诊断 Token）只读拉取 JSON。 |
| **I. Experience RAG 可选向量重排** | ✅ | `l3_node/experience_memory.py`：`JACHIN_EXPERIENCE_USE_EMBED` + `JACHIN_EXPERIENCE_EMBED_PREFILTER`；与 Nexus 共用 FastEmbed（失败降级字符串相似度）。Memory Nexus 批量嵌入：`l3_client/.../memory_backend.py` — `embed_texts_normalized_list`。 |
| **J. Nexus 回合末写入 · 闲聊过滤** | ✅ | `schedule_nexus_turn_commit_async`：纯寒暄跳过（**J**）；**低价值助手回复**跳过：`[ExecutionBrief]`、`[未产出回复]`、`【需要补充信息】`、短 `[System]`、极短套话等（`JACHIN_NEXUS_TURN_COMMIT_SKIP_LOW_VALUE`，默认开启）。 |
| **K. Hook 事件 SQLite（可选）** | ✅ | `l3_node/engine/persistent_hook_log.py`：`JACHIN_PERSIST_HOOKS=1` 时在 `run_agent` 入口注册，追加写入 `$JACHIN_HOME/workspace/hook_events.sqlite3`（轻量 §3.2.4，非完整 DAG 续跑引擎）。 |
| **L. 进程内定时任务 → prompt 感知** | ✅ | `task_runtime_registry`：`register_scheduled_job_hint` / `unregister_*`；`kalaroko_scheduler` 与 `bi/scheduler` 在 APScheduler（或 BI loop）注册成功时写入摘要，合并进 `format_combined_runtime_prompt_suffix`。`agent_core`：`delegate` 且 `sub_tasks` 非空时触发 `on_task_decompose`。外加 **M** 外部心跳文件。 |
| **M. 外部定时心跳 → prompt** | ✅ | `external_scheduled_hints.json` + `merge_external_scheduled_process_hint()` / `read_external_scheduled_hints_dict()`（**U** 只读 HTTP）；**O** `POST`/`DELETE`；`fb_report_scheduler` 写入心跳，退出清条目见 **S**；读侧 **`JACHIN_EXTERNAL_SCHED_HINTS_DISABLE`**（关闭 prompt 注入时 **U** 仍可读文件并带 `hints_prompt_read_disabled`）。 |
| **O. HTTP 外部定时登记** | ✅ | `http_server`：`JACHIN_REGISTRY_EXTERNAL_SCHED_TOKEN` + `X-Jachin-Registry-Token`；**`POST`** 合并心跳（body：`process_key`、`title`、`schedule_summary`、`pid?`）；**`DELETE`** 撤销登记（body：`process_key`）→ `remove_external_scheduled_process_hint`。未配置 Token → 503。 |
| **P. 飞书「补充意图」轻量分流** | ✅/⏳ | `classify_busy_followup` + supplement ack + `agent_core` **【飞书·排队补充意图】**；**X** 将排队期多条原文并入**即将执行**的 `user_input`；**AE**·r：**HTTP** 同会话二条在等服务端锁时中段热并入当前 ReAct（`session_hot_user_inject`）。**AF**·s：并行 `delegate` 汇总。**AH**·t：多子句优先级。**⏳**：全量 **LLM** 冲突仲裁。 |
| **Q. Hook 事件只读 HTTP** | ✅ | `GET …/hook-events-recent`；与 **R**/**U**/**V**/**W** 共用诊断双 Token/双头；`read_recent_hook_events`；`run_id` + **`run_id_exact=1`** 精确筛单次 `run`（轻量回放探针）；未开 **K** 或库不存在则返回空列表。 |
| **R. 运行时只读快照 · HTTP 锁探针** | ✅ | `GET …/runtime-snapshot?session_key=`；**与 Q 同源鉴权**；`get_runtime_registry_snapshot_dict()`；`foreground_tasks[]` 含 **`resource_tags`**；可选 `http_agent_session.lock_held`；**AE**：有则返回 `session_hot_user_pending`（等锁先进线摘要，不消费）。 |
| **S. FB 调度守护退出清心跳** | ✅ | `scripts/fb_report_scheduler.py`：`finally` 中 `clear_fb_external_sched_hint()` → 本地 `remove_external_scheduled_process_hint("fb_report_scheduler")`；可选 `FB_SCHED_L3_REGISTRY_URL` / `JACHIN_L3_HTTP_URL` + `JACHIN_REGISTRY_EXTERNAL_SCHED_TOKEN` → 远地 L3 **`DELETE /api/v1/registry/external-sched-hint`**。 |
| **T. WebSocket 抢占上一轮流式前即时提示** | ✅ | `l3_node/ws_server.py`：若 `active_turn_task` 仍在跑，新进线先发 `step_type: system_status`（JSON `kind: prior_turn_superseded`），再 `cancel` 旧任务并起新轮（等同「打断并替换」）；镜像订阅可收同步提示。默认开启，关闭：`JACHIN_WS_SUPERSEDE_ACK=0`。 |
| **U. 外部定时心跳文件只读 HTTP** | ✅ | `GET /api/v1/registry/external-scheduled-hints`；**与 Q 同源鉴权**；`read_external_scheduled_hints_dict()` → `processes` / `hints_prompt_read_disabled` / `file_present`。 |
| **V. TaskDAG active.json 只读 HTTP** | ✅ | `GET /api/v1/registry/task-dag-active`；**与 Q 同源鉴权**；`load_task_dag_dict()`，无文件则 `dag: null`。 |
| **W. 飞书 IM 队列·只读 HTTP（当前会话在飞书进线待处理）** | ✅ | `GET /api/v1/registry/im-channel-pending`；**与 Q 同源鉴权**；`peek_im_channel_pending()`→ `{session_key, chat_id, message_id, intent_preview, since_ts}`；**观测**，无抢占。 |
| **X. 飞书 IM 排队摘录 → 本轮 user_input** | ✅ | `dispatcher`：`prior>0` 时 `_im_append_queue_rollup`；持锁后 `_im_consume_queue_rollup_prefix` 拼前缀（去重、与同句等则省）；**非**独立 SessionInstructionQueue worker；`JACHIN_IM_QUEUE_ROLLUP_DISABLE=1` 关闭。 |
| **Y. 记忆遗忘曲线时间权重** | ✅ | `memory_backend.py`：`_compute_time_decay`（Ebbinghaus，Wing-specific 半衰期：Procedures=180d, Knowledge=90d, 其余=30d）；`deep_search` 读取 `timestamp` 列，`final_score = sem*(1-w) + decay*w`；`JACHIN_NEXUS_TIME_DECAY_WEIGHT`（默认 0.2，0=纯语义）。 |
| **Z. PersistedIntent 意图持久化** | ✅ | `l3_node/autonomy/intent_persister.py`：SQLite `persisted_intents.sqlite3`；`IntentPersister` CRUD（save/create/list/get/set_enabled/delete/record_execution）；`IntentRecovery.restore_to_scheduler()` 进程重启恢复 cron/interval 意图；HTTP：`GET/POST /api/v1/autonomy/intents`、`PATCH/DELETE …/{intent_id}`（共用诊断 Token）。 |
| **AA. mode:discuss + StructuredResultMerger** | ✅ | `l3_node/primitives/multi_agent/discussion.py`：`run_discussion(DiscussionConfig, engine)` — Round 1 并行（planner+critic），Round N 串行修订+二次审查，终止条件：critic 无新质疑 OR `max_rounds`；可选 summarizer 最终共识。`result_merger.py`：`StructuredResultMerger.merge_parallel/merge_discussion`。`agent_core` delegate 分支新增 `mode: discuss` 路由（先于普通 sub_tasks）；system prompt 补 discuss 用法；Observation 前缀 `format_summary()`（**s**）。普通并行 `delegate` 详块见 **AF**。 |
| **AB. 动态角色创建安全沙箱** | ✅ | `agent_core._sanitize_inline_role`：`sub_tasks[i]["role"]` 为 dict 时激活；role_id 仅允许字母数字下划线；system_prefix 移除 prompt 注入关键词（`[REDACTED]`）；allowed_tools 与父级工具集取交集 + 强制剔除 delegate（防递归）；`_spawn_sub_agent_async` 接受 `_inline_system_prefix` / `_inline_allowed_skills` 参数。 |
| **AC. AwarenessLoop + ProactiveReporter** | ✅ | `l3_node/autonomy/awareness_loop.py`：`AutonomousAwarenessLoop.run_forever()`（`JACHIN_AWARENESS_SCAN_INTERVAL`，默认 60s）；扫描：① interval 意图到期触发、② 磁盘/Token 资源告警（`JACHIN_TOKEN_DAY_BUDGET`）、③ 连续失败异常检测、④ 日终 23:55 → `ProactiveReporter`；`proactive_reporter.py`：生成今日执行统计 + Token + 经验 + 明日预计 + 关注问题，并通过飞书推送；`bootstrap.start_autonomy_services()` + http_server `on_startup`；关闭：`JACHIN_AWARENESS_LOOP_DISABLE=1`。 |
| **AD. 可观测性面板 HTTP** | ✅ | `l3_node/autonomy/dashboard.py`：`build_autonomy_status_dict()`；`GET /api/v1/autonomy/status`（与 **Q**/**R** 同源诊断鉴权）；字段含 `uptime_hours`、`active_intents`、`running_tasks`、`queued_tasks`/`background_p3_running`、`today_token_used`（`workspace/llm_token_daily.json`）、`today_token_budget`、`anomalies`、`next_scheduled_task`、`disk_free_gb` 等。**llm_client** 每次 LLM 响应经 `_apply_usage_budget` 调用 `record_daily_llm_usage`。 |
| **AE. 同会话中段用户热并入（HTTP + 可选 IM）** | ✅ | `l3_node/session_hot_user_inject.py`：`record_pending_session_user_text` / `peek_pending_session_user` / `drain_pending_session_user_texts`；`http_server` 同会话且他请求已持锁时对 `user_input` 入账；`agent_core` 每轮 LLM 前 drain 并入 **user** 块；runtime-snapshot 可选 `session_hot_user_pending`；飞书 `JACHIN_IM_SESSION_HOT_INJECT=1` 与 **X** 并行（默认关）；`JACHIN_SESSION_HOT_USER_INJECT_DISABLE=1` 全关。 |
| **AF. 并行 delegate 结构化汇总** | ✅ | `result_merger.merge_parallel`：**Markdown 索引表**（`with_index_table`，默认开）+ 逐子任务详块；`agent_core` 并行 `delegate`（`asyncio.gather` 子任务）在 **RunReport 行** 之后统一走 **merge_parallel**（`SubAgentResult` 承载成功/异常）。 |
| **AG. multi_agent 超参 · Experience 多 Agent 摘要** | ✅ | `nexus_config.json` → `multi_agent.max_discussion_rounds`（默认 3，1..12）/`discussion_item_max_iterations`（默认 3，1..24）；环境变量 **`JACHIN_DISCUSS_MAX_ROUNDS`**、**`JACHIN_DISCUSS_ITEM_MAX_ITER`** 优先；`agent_core` `mode: discuss` 构建 `DiscussionConfig` 时注入；**`JACHIN_EXPERIENCE_RECORD_MULTI_AGENT=1`** 且 **Experience RAG 开** 时 `experience_memory.save_multi_agent_episode` 写入 `multi_agent:discuss` / `multi_agent:parallel_delegate` 并触发 **`on_experience_learned`**；prompt 块对 `multi_agent:*` 单独展示。 |
| **AH. 飞书第二条多子句意图优先级（非 LLM）** | ✅ | `im_second_instruction.py`：`classify_busy_followup` 按换行、中文/英文分号拆多段，各段 `_classify_busy_followup_clause` 后合并为 **interrupt > parallel > supplement > queue** 最高优先级（轻量冲突仲裁）。 |
| **AI. Wing 重要性分级乘数** | ✅ | `memory_backend.py`：`_WING_IMPORTANCE`（Procedures 1.30 / Knowledge 1.20 / Core 1.25 / Episodes 1.00）；`_compute_wing_importance(wing, weight)` → lerp；`_wing_importance_weight()`（**`JACHIN_NEXUS_WING_IMPORTANCE_WEIGHT`**，默认 0.15）；`deep_search` 在时间衰减融合后再乘以 Wing 重要性乘数（clamp 到 [0,1]）。 |
| **AJ. 条件触发意图轻量评估器** | ✅ | `awareness_loop._check_intents` 对 `trigger.type == "condition"` 意图调用 `_evaluate_condition(expr, resource)`（**`JACHIN_CONDITION_INTENT_ENABLE=1`** 开启）；支持 `disk_free_gb <op> N`、`token_used <op> N`、`token_used_pct <op> N`、`consecutive_failures:intent_id <op> N` 四类内置条件。条件满足则 `fire_intent`，失败时安全返回 False。 |
| **AK. 失败意图自动重置 + Level 2 自愈通知** | ✅ | `IntentPersister.autoreset_failed(intent_id)`：将 `status=failed` 重置为 `active`，`consecutive_failures=0`。`AutonomousAwarenessLoop` 新增 `JACHIN_INTENT_AUTORESET_HOURS=N`（默认 0 关闭）：失败意图超过 N 小时后扫描到时自动调用 `autoreset_failed` 并推送「[自愈通知]」至飞书。 |
| **AL. Wing 全量重映射（规范注册表）** | ✅ | `l3_client/.../wing_registry.py`：五 Wing 规范定义（Episodes 30d/Knowledge 90d/Procedures 180d/Core 180d/Inbox 7d）+ `normalize_wing()` 别名归一化 + `wing_half_life_days()` / `wing_importance_mult()`；`memory_backend.py` 的 `commit_drawer` / `upsert_drawer` 写入时调用 `normalize_wing` 自动归一化；`_compute_time_decay` / `_compute_wing_importance` 改为从注册表读取；`JACHIN_WING_IMPORTANCE_OVERRIDE` 运行时 JSON 覆盖单 Wing 重要性系数。 |
| **AM. LLM 驱动条件评估（fallback 路径）** | ✅ | `awareness_loop._evaluate_condition` 改为 `async def`；内置规则无法解析时若 `JACHIN_CONDITION_LLM_EVAL=1` 则调 `_evaluate_condition_llm_fallback`：构建「system state + condition → yes/no」轻量 prompt，调 `LiteLLMEngine.generate_response`（单次，temperature=0，max_tokens=8），失败时安全返回 False；模型可通过 `JACHIN_CONDITION_LLM_MODEL` 覆盖。 |
| **AN. Guardrails 基础实现** | ✅ | `l3_node/guardrails.py`：`GuardrailsChecker`（含 `GuardrailsState`）；五维检查：`max_iterations`（`JACHIN_GR_MAX_ITERATIONS`，默认 20）/ `max_tool_calls`（`JACHIN_GR_MAX_TOOL_CALLS`，默认 40）/ `max_tokens`（`JACHIN_GR_MAX_TOKENS`，默认 200k）/ `forbidden_tools`（`JACHIN_GR_FORBIDDEN_TOOLS`）/ `repeat_tool_action`（`JACHIN_GR_REPEAT_TOOL_ACTION_MAX`，默认 3）；`action`：`warn`（继续）/ `truncate`（返回 ExecutionBrief）/ `abort`（抛 `GuardrailsAbortError`）；`agent_core` 每次 ReAct 迭代开始时调 `check_all_pre_iteration`，工具执行前调 `check_all_pre_tool`；`JACHIN_GUARDRAILS_ENABLE=1` 开启（默认关）。 |
| **AO. DAG 轻量续跑引擎** | ✅ | `l3_node/task_engine/dag_resume.py`：`probe_dag_resume(run_id)` 从 `hook_events.sqlite3` 查已完成节点 ID（`HOOK_ON_TASK_NODE_DONE`），与 `active.json` 对比找待续跑节点，返回 `DagResumeResult`（含 `completed_node_ids` / `pending_nodes` / `resume_intent` 续跑意图文本）；`apply_dag_resume(run_id)` 将待续跑节点重置为 `pending` 并写回 active.json；`POST /api/v1/registry/dag-resume`（`dry_run=true` 只探测，`false` 则应用）。前提：`JACHIN_PERSIST_HOOKS=1`。 |
| **AP. DAG 级 Guardrails（跨 Node 预算控制）** | ✅ | `l3_node/task_engine/dag_guardrails.py`：`DagBudgetState` 以 `dag_id` 为粒度追踪整个 DAG 的总迭代次数 / 总工具调用 / 总 Token / 已执行节点数；持久化到 `workspace/dag_guardrails.sqlite3`（跨进程可见）；`DagGuardrailsChecker.check_dag_budget()` 在续跑前检查四类上限（`JACHIN_DAG_GR_MAX_TOTAL_ITERATIONS` / `_TOOL_CALLS` / `_TOKENS` / `_NODES`）；违规时产出 `DagGuardrailsViolation.dag_brief()` 并阻止续跑；`dag_resume.py` 续跑前自动调用；`GET /api/v1/registry/dag-guardrails?dag_id=` 可查单 DAG 预算状态；`JACHIN_DAG_GUARDRAILS_ENABLE=1` 开启（默认关）。 |
| **AQ. Level 3 自愈（Experience RAG 辅助诊断）** | ✅ | `l3_node/autonomy/level3_healer.py`：`diagnose_failed_intent` 在连续失败次数 ≥ `JACHIN_LEVEL3_FAILURE_THRESHOLD`（默认 3）时，从 Experience RAG 检索与失败意图相似的历史成功案例（`retrieve_experience`，top_k `JACHIN_LEVEL3_RAG_TOP_K`），构建 `HealingDiagnosis`（含建议工具列表 + 修复文案）；`run_level3_healing` 异步执行并推送飞书 rich 报告；`JACHIN_LEVEL3_AUTO_APPLY=1` 时将首条成功路径注入意图 metadata；`awareness_loop._execute_action` 中 `anomaly` 分支调用；`IntentPersister.update_extra_meta` 支持注入；`JACHIN_LEVEL3_HEALER_ENABLE=1` 开启（默认关）。 |
| **AR. 跨进程 DAG 续跑转交（HTTP Handoff）** | ✅ | `l3_node/task_engine/dag_handoff.py`：`DagHandoffPackage`（schema_version / package_id / completed_node_ids / pending_nodes / resume_intent / context_hint）；`export_dag_handoff(run_id)` 从本地 hook_events + active.json 构建包，可选落文件到 `JACHIN_DAG_HANDOFF_DIR`；`import_dag_handoff(package_data)` 校验 schema 后将待续跑节点写入本地 active.json，返回 `HandoffImportResult`（含 `resume_intent`）；三个端点：`POST /dag-handoff/export` / `POST /dag-handoff/import` / `GET /dag-handoff/list`；Phase 2（中心化 Coordinator）仍 ⏳。 |
| **AS. DAG Coordinator Phase 2（节点注册 + 分布式锁 + Peer 发现）** | ✅ | `l3_node/task_engine/dag_coordinator.py`：① **节点注册表**：`register_node` / `heartbeat` / `list_alive_nodes`（心跳 TTL `JACHIN_COORDINATOR_NODE_TTL`，默认 90s，SQLite `dag_coordinator.sqlite3`）；② **分布式 DAG 锁**：`claim_dag`（CAS，锁已过期自动抢占）/ `release_dag`（token 校验）/ `refresh_dag_lock`（续约）/ `get_dag_owner`（TTL `JACHIN_COORDINATOR_LOCK_TTL`，默认 120s）；③ **Peer 发现**：本地 SQLite 同机 + `discover_http_peers`（轮询 `JACHIN_COORDINATOR_PEER_URLS`）+ `find_idle_peer`（load_score < 0.5）；`dag_handoff.auto_handoff_to_peer`：export → find_idle_peer → HTTP POST /import → optional release_lock；`POST /dag-handoff/auto-transfer`；六个 coordinator 端点（`/info` / `/peers` / `/register` / `/dag-claim` POST/DELETE / `/dag-locks`）；`on_startup` 心跳循环自启；`JACHIN_COORDINATOR_ENABLE=1` 开启（默认关）；⏳ 专用 Coordinator 服务 / Redis 分布式锁 / 自动 Failover。 |
| **AY. Skill 自动进化引擎** | ✅ | `l3_node/autonomy/skill_evolver.py`：`analyze_and_evolve_skill`（LLM patch → `_validate_candidate` ≤`JACHIN_SKILL_EVOLVE_MAX_PATCH_RATIO` → 快照 → 写 SKILL.md → JSONL）；`run_skill_evolution_if_ready`；`awareness_loop._try_skill_evolution_after_success`；`JACHIN_SKILL_EVOLVE_ENABLE`（默认关）、`JACHIN_SKILL_EVOLVE_DRY_RUN`。写盘成功 → `skill_md_hot_reload.notify_skill_md_changed_from_disk_write`（§六 **P2 inline**：`_skill_sop_dirty` + 世代；与 **AZ** 同源入口）。`skill_md_hot_reload.py`：**P1** 每轮刷新 HR 标记段；**P2** 同步 `_react_system_prompt_full`；`JACHIN_SKILL_MD_HOT_RELOAD` / `JACHIN_SKILL_MD_INLINE_ENABLE`。 |
| **AZ. 前台 SOP / 提示词热同步（写盘 → inline notify）** | ✅ | **横切能力**（与 **AY** 进化、L1 同步解耦）：`skill_md_hot_reload.notify_skill_md_changed_from_disk_write(path)` 由 **`skill_evolver` 成功写盘**与 **`skill_sync_guard.handle_upstream_update` 成功写盘**（首次安装 / 无分叉覆盖 / smart merge / 强制覆盖）共同调用；HR 路径下 bump 世代 + 已注册 ReAct `_skill_sop_dirty`；非 HR 为 no-op。详见 §〇「前台 SOP / 提示词热同步」行。 |
| GlobalTaskRegistry… | ⏳ | 见 **§〇**；**X** 为**上下文合并**，非全量队列抽象。 |

---

## 目录

1. [并发执行模型：主线程保序 + 多任务并行/排队](#一并发执行模型主线程保序--多任务并行排队)
   - [1.4 主任务执行中，第二条指令进来怎么办？](#14-主任务执行中第二条指令进来怎么办)
2. [多 Agent 协作框架：主管 + 角色池 + 动态组队](#二多-agent-协作框架主管--角色池--动态组队)
3. [任务拆解 + Hook 体系](#三任务拆解--hook-体系)
4. [记忆架构现状分析与优化](#四记忆架构现状分析与优化)
5. [24 小时真正无人值守机器人](#五24-小时真正无人值守机器人)
6. [Skill 自动进化：MD 格式 + 错误自愈后自动更新 + 零感知进化日志](#六skill-自动进化md-格式--错误自愈后自动更新--零感知进化日志)

---

## 一、并发执行模型：主线程保序 + 多任务并行/排队

### 1.1 现状与瓶颈

**当前模型（L3 主路径）**：

```
用户请求 → run_agent（单协程串行 ReAct）
定时任务 → 独立进程/subprocess（fb_report_scheduler.py）
后台任务 → asyncio.Queue + N 个 worker 协程（background_task_service.py）
```

核心问题：

| 问题 | 表现 | 代码锚点 |
|------|------|---------|
| **主会话与定时任务互相不感知** | 定时任务触发时，主会话 `run_agent` 不知道有并发任务在跑，无排队通知 | `scripts/fb_report_scheduler.py` 是进程外孤岛 |
| **主会话线程实际上是单协程** | 同会话 HTTP 已串行；多连接 WS/多会话仍共享单进程事件循环 | `http_server.py`、`ws_server.py` |
| **后台任务与前台感知断层** | prompt 可注入 **P3 + 前台路数**（进程内近似） | `task_runtime_registry`、`background_task_service` |
| **`asyncio.to_thread` 假超时** | 超时返回后底层同步调用仍占线程池槽位，导致池饱和饥饿 | `agent_core._invoke_react_tool`、`mcp_registry.py` |
| **定时任务冲突无处理** | 若定时任务与前台任务需相同资源（如同一 MCP），无优先级/排队机制 | 无全局资源调度器 |

### 1.2 优化目标（AGI 视角）

> **AGI 原则**：系统应该像人类大脑一样——主意识（主线程）专注当前任务，但后台意识（后台任务）持续处理信息流，二者通过「注意力调度」协调，而非相互阻塞。

目标态：
- 主会话（`run_agent`）**永远响应**，不因后台任务繁忙而超时
- 定时任务与主会话**可同时运行**，互相感知对方的存在
- 资源冲突时**有明确的优先级与排队通知**，不是静默等待或失败
- 主会话可以**实时查询当前系统中所有在执行的任务状态**

### 1.3 优化方案

#### 方案 A：全局任务调度器（Global Task Orchestrator）

引入一个轻量中心化的**任务注册表（Task Registry）**，所有任务（前台、后台、定时）统一注册：

```python
# 新增：core/task_registry.py（概念设计）

@dataclass
class RegisteredTask:
    task_id: str
    task_type: Literal["foreground", "background", "scheduled"]
    status: Literal["running", "queued", "paused", "done"]
    priority: int           # 1=最高（用户前台），2=定时，3=后台批量
    resource_tags: list[str]  # 如 ["llm:main", "mcp:feishu"]
    started_at: float
    estimated_duration_sec: float | None
    notify_channel: str | None  # 用于排队时通知谁

class GlobalTaskRegistry:
    """线程安全的全局任务注册表，单例"""
    _tasks: dict[str, RegisteredTask]
    _lock: asyncio.Lock

    async def register(self, task: RegisteredTask) -> Literal["run", "queue", "reject"]
    async def can_run_now(self, resource_tags: list[str], priority: int) -> bool
    async def get_running_summary(self) -> str  # 供 Agent 感知
    async def notify_queued(self, task_id: str, blocking_task_ids: list[str])
```

**定时任务对接**：`fb_report_scheduler.py` 等进程外调度器在触发时，先通过 L3 HTTP API 注册任务意图，拿到「可以执行」或「排队中（预计 N 分钟后）」的回应。

**已部分落地（2026-05-18·c）**：`l3_node/task_runtime_registry.py` 实现进程内 **顶层 `run_agent` 登记** + **后台队列摘要** 合并进 prompt（见 §落地进度 **D**、§1.3 方案 B）。**O** 已提供跨进程 HTTP 合并/撤销；**W** 补充 `resource_tags` 登记与 IM 待处理深度诊断；仍不含：**resource_tags 调度抢占**、统一集群编排视图（**§〇**）。

#### 方案 B：并发感知的 `run_agent` 入口

在 `run_agent` 开始前，注入系统当前负载上下文到 system prompt 后缀：

```python
# 在 _build_system_prompt 后缀段追加（低优先级，可驱逐）
running_tasks_summary = await global_task_registry.get_running_summary()
# 输出示例："当前后台任务：[FB日报生成(进行中 2分钟)、K11质量检查(队列中)]"
```

**已实现（轻量版，2026-05-18）**：`format_combined_runtime_prompt_suffix()`（`task_runtime_registry`）在 **无 GlobalTaskRegistry** 时合并：① 进程内顶层前台任务路数 + 通道概览；② `background_task_service` 的 `running` + 内存队列 `qsize`。经 `SuffixChunk("low", "runtime_background_tasks", ...)` 注入；无负载时不占后缀。

这让 Agent 在 **多会话前台** 或 **P3 后台** 有负载时，能在答复中诚实提及系统可能较忙。

#### 方案 C：优先级抢占 + 排队通知

```
任务优先级定义：
P1 = 用户直接前台交互（run_agent，HTTP/WS）
P2 = 定时强制任务（Scheduled，必须按时执行）
P3 = 主动触发后台任务（submit_background_task）
P4 = 系统自动触发的低优先级批量任务

冲突规则：
- P1 与 P2 可同时运行（并行）
- P3 排在 P1/P2 后面，但不被 P1 无限阻塞（最大等待 10 分钟后强制通知用户）
- P4 只在 P1/P2/P3 全部空闲时运行
```

通知机制：当定时任务触发但有前台任务正在运行时，通过 `l3_event_bus` 广播一条 `TASK_QUEUED` 事件，前台会话可在当前回答末尾附加：

> "💡 注意：飞书日报定时任务已进入队列，将在当前对话结束后 30 秒内开始执行。"

### 1.4 主任务执行中，第二条指令进来怎么办？

这是并发模型中最核心的用户体验问题，也是现状差距最明显的地方。

#### 1.4.1 当前实际行为（按入口分类）

**① 飞书 IM 通道（`im_channels/dispatcher.py`）**

```
串行（未改）：仍按 chat_id 的 threading.Lock，保证同会话 session_messages 不竞态。

已加（2026-05-18）：
  - _im_chat_inflight：同 chat 已提交线程池且未结束的工单数。
  - 当 prior>0 时，新进线立即 _notify_im_when_prior_turn_inflight：
      排队 ack / 关键词打断（request_cancel_run + foreground_run_registry）/
      「并行」说明性 ack（实际仍排队执行）。
  - 12s 延时安抚逻辑保留。
  - **supplement**（`classify_busy_followup`）：专属排队说明 + `agent_core` **【飞书·排队补充意图】** 前缀（仍为排队执行，非热补丁 Observation）。
```

**② HTTP `/agent/run` 接口（`http_server.py`）**

```
现状（已改进）：
  - 若 body 中提供非空 chat_id 或 session_id（二者取一，记为会话键），
    则对该键使用 asyncio.Lock，串行执行 run_agent。
  - 未提供会话键的请求仍为无状态并发（与旧行为一致）。

仍缺（相对「理想 SSE」）：
  - 第二条请求在锁上等待时，HTTP 响应尚未返回，客户端仍「挂起」，无**单向** SSE/WebSocket 推送 ack。

已缓解（2026-05-18·j · **R**）：
  - 轮询 `runtime-snapshot` 的 `lock_held` + 进程内摘要。

已缓解（2026-05-18·r · **AE** / **R**）：
  - 同会话第二条在**等待锁前**将 `user_input` 记入 `session_hot_user_inject`，当前持锁的 `run_agent` 在**每轮 LLM 前** drain 并入对话，实现「中段热并入」（非 Observation 字段改写，而是追加 user 消息块）。
  - 轮询 **R** 时可读 `session_hot_user_pending`（pending 条数与预览，不消费）。
```

**③ WebSocket 通道（`ws_server.py`）**

```
单连接内：新用户 intent 到达且上一轮 run_agent 仍在执行时，**先 cancel 旧任务再起新轮**（打断并替换，非排队等待）。

已落地（2026-05-18·l · **T**）：
  - 在 cancel 之前向本连接发送 `step_type: system_status`，`content` 为 JSON：`{"kind":"prior_turn_superseded",...}`；
    有 `chat_id` 时对 Lark 镜像订阅连接同步一条系统提示。
  - 关闭提示：`JACHIN_WS_SUPERSEDE_ACK=0`。
不同 WebSocket 连接之间仍无互斥（与多终端多连接一致）。
```

**问题汇总：**

| 通道 | 第二条指令的遭遇 | 用户是否知情 | 数据安全 |
|------|----------------|------------|---------|
| 飞书 IM | 仍按 `threading.Lock` 串行执行；**已在进线时推送 ack**（排队/打断/说明性并行） | ✅ 第二条起有即时回复 | ✅ 锁保护 |
| HTTP | **同 session 串行**；第二条在服务器侧等待锁释放；**AE** 二条文案可并进**正在执行**的 ReAct | ⚠️ 同步响应仍挂起；**可轮询 R** `lock_held`、`session_hot_user_pending` + 前台摘要 | ✅ 同键下不再竞态 |
| WebSocket | **同连接内新进线会取消上一轮**（打断替换） | ✅ **T** `system_status` / `prior_turn_superseded`（可关）；非排队 | ✅ 单连接串行任务句柄 |

**核心缺陷**（剩余）：**HTTP** 仍无**服务器主动下行**的第二条 ack 通道（依赖 **R** 轮询 `lock_held` / `session_hot_user_pending`）；**AE** 已支持持锁 run **中段**合并新进线文案。**WS** 已具备抢占前 **T** 提示，但非排队模式；飞书 **真·双轨并行**（两路独立 `run_agent`）未实现，见 §1.4.5。

#### 1.4.2 应该有的四种处理模式

从 AGI 视角，当主任务运行中收到新指令，应给用户（或 Agent 自主判断）提供四种处理方式：

```
模式一：排队等待（Queue）
  第二条指令进入有序队列，第一条完成后自动执行。
  适用：两条指令彼此独立，顺序执行即可。
  用户感知："收到！当前有一个任务在执行中，你的指令已排队，预计 N 分钟后开始。"

模式二：打断并替换（Interrupt & Replace）
  取消第一条指令（发送 cancel Event），立即执行第二条。
  适用：用户改变主意，第二条比第一条更紧急。
  用户感知："好的，已停止当前任务，马上处理你的新指令。"

模式三：并行执行（Parallel）
  第一条继续跑，第二条另开一个 run_agent 实例（独立 session）并行执行。
  适用：两条指令完全独立，都需要尽快完成。
  用户感知："收到！两个任务同时在执行，完成后分别告知你结果。"

模式四：智能判断（Auto-Decide）
  Agent 自动分析两条指令的关系，选择上述三种模式之一。
  判断依据：
  - 第二条含「等一下」「先停」「取消」→ 模式二（打断）
  - 第二条含「同时」「另外」「还有」→ 模式三（并行）
  - 否则默认 → 模式一（排队）
```

#### 1.4.3 优化方案

**核心数据结构：每会话的前台指令队列**

```python
# 新增：l3_node/session_instruction_queue.py（概念设计）

@dataclass
class QueuedInstruction:
    instruction_id: str
    text: str                         # 用户原文
    arrived_at: float
    status: Literal["queued", "running", "cancelled", "done"]
    run_id: str | None                # run_agent 的 run_id，用于 cancel

@dataclass
class InstructionConflictIntent:
    """Agent 对「新指令 vs 当前任务」冲突的分析结果"""
    mode: Literal["queue", "interrupt", "parallel", "clarify"]
    reason: str                       # 为什么选这个模式
    ack_message: str                  # 立即回复用户的安抚消息

class SessionInstructionQueue:
    """
    每个 session（chat_id）一个实例。
    负责：
    1. 维护有序指令队列
    2. 检测「当前是否在执行」
    3. 分析新指令意图，决定冲突处理模式
    4. 立即给用户发 ack 消息（不等 run_agent 完成）
    """
    _queue: asyncio.Queue[QueuedInstruction]
    _current: QueuedInstruction | None
    _chat_id: str
    _send_reply_fn: Callable[[str, str], bool]  # 直接回复用户的函数

    async def submit(self, text: str) -> InstructionConflictIntent:
        """
        新指令进来时调用。
        - 若当前无任务 → 立即执行，返回 mode=run_now
        - 若当前有任务 → 分析冲突意图 → 发 ack → 入队或打断
        """

    def _analyze_conflict_intent(self, new_text: str, current_text: str) -> InstructionConflictIntent:
        """
        快速本地规则分析（不调 LLM，避免循环依赖）：
        含「停」「取消」「算了」「换成」→ interrupt
        含「同时」「另外」「还有」「顺便」→ parallel
        含「等」「结束后」「之后」→ queue（显式）
        其余 → queue（默认）
        """
```

**飞书 IM 通道改造（`dispatcher.py`）**

```python
# 当前：with lock 静默等待（用户无感知）
# 优化后：
async def _handle_new_instruction(text, chat_id, send_reply_fn):
    queue = get_session_queue(chat_id)  # 每 chat_id 一个 SessionInstructionQueue

    intent = await queue.submit(text)

    # 立即给用户发 ack（不等任务完成），这步在锁外完成
    if intent.mode == "queue":
        send_reply_fn(chat_id, f"⏳ {intent.ack_message}")
    elif intent.mode == "interrupt":
        # 取消当前 run_agent
        request_cancel_run(queue.current_run_id)
        send_reply_fn(chat_id, f"⏹️ {intent.ack_message}")
    elif intent.mode == "parallel":
        # 新开独立 session 的 run_agent（独立 run_id，独立消息历史）
        asyncio.create_task(_run_parallel_agent(text, chat_id, send_reply_fn))
        send_reply_fn(chat_id, f"🔀 {intent.ack_message}")
```

**HTTP 通道补锁（`http_server.py`）**

```
# 当前：无锁，并发请求共享 session，存在竞态
# 优化后：
_http_session_locks: dict[str, asyncio.Lock] = {}

async def _handle_agent_run(request):
    session_id = body.get("session_id") or ""
    if session_id:
        lock = _get_or_create_session_lock(session_id)
        async with lock:
            answer = await run_agent(...)
    else:
        # 无 session_id 的请求直接执行（无状态）
        answer = await run_agent(...)
```

**已落地（2026-05-18）**：实现为 `_http_agent_session_lock` + `_ch_s`（`chat_id` 与 `session_id` 合一取非空串）。无会话键时行为与旧版一致。

#### 1.4.4 用户侧的完整交互体验设计

```
场景一：普通排队
  用户 14:30:01 发「帮我整理今天的飞书消息」（开始执行）
  用户 14:30:45 发「再帮我查一下明天的日历」
  
  系统立即回复（14:30:45）：
  "⏳ 收到！当前正在整理飞书消息（已进行 44 秒），
   你的「查明天日历」已进入队列，排第 1 位，
   预计整理完成后立刻开始。"
  
  14:31:20 第一条完成，自动开始执行第二条，完成后推送结果。

场景二：打断替换
  用户 14:30:01 发「帮我写一份详细的季度报告，要 5000 字」（开始执行）
  用户 14:31:00 发「算了，先不写报告了，帮我查一下今天有没有会议」
  
  系统立即回复（14:31:00）：
  "⏹️ 好的，已停止生成报告（已完成约 30%）。
   马上帮你查今天的会议安排。"
  
  run_agent（报告）收到 cancel Event，当前 LLM 调用中断，
  立即开始执行「查会议」。

场景三：并行执行
  用户 14:30:01 发「帮我分析这份合同」（开始执行）
  用户 14:30:30 发「顺便帮我查一下 XX 公司的背景」
  
  系统立即回复（14:30:30）：
  "🔀 同时处理！合同分析和 XX 公司背景调查正在并行执行，
   完成后分别告知你结果。"
  
  两个 run_agent 实例各自独立运行，结果分别推送。

场景四：需要澄清
  用户 14:30:01 发「帮我处理邮件」（模糊任务，执行中）
  用户 14:30:15 发「发给张总」
  
  系统判断：这是对当前任务的补充说明，而非新任务
  → 注入到当前 run_agent 的下一轮 Observation 中（追加上下文）
  → 不入队，不打断，不并行
  "✅ 已收到补充：发给张总，正在处理中。"
```

#### 1.4.5 实施优先级

| 阶段 | 工作 | 预期收益 | 落地 |
|------|------|----------|------|
| **P0** | 飞书通道：第二条消息进来时立即发送 ack（「收到，排队中」），不再静默等待 | 消除用户「发消息到黑洞」的感知 | ✅ `_notify_im_when_prior_turn_inflight` + `_im_chat_inflight` |
| **P0** | HTTP 通道：补充 per-session `asyncio.Lock`，消除共享 session 竞态风险 | 数据安全 | ✅ `_http_agent_session_lock` |
| **P0** | WebSocket：打断替换上一轮前即时下行系统提示，避免用户误以为「无响应」 | 用户知悉抢占 | ✅ **T**（`JACHIN_WS_SUPERSEDE_ACK`，默认 1） |
| **P1** | 实现 `SessionInstructionQueue`；飞书通道改用队列替代锁；本地规则分析冲突意图 | 排队、打断、并行三种模式可用 | ⏳ 排队 ack + 关键词 **interrupt/parallel/supplement（说明性）** ✅；完整队列化 ⏳ |
| **P1** | 打断模式接通 `request_cancel_run`（`agent_cancel.py` 已有）；用户可通过自然语言触发取消 | 用户有控制权 | ✅ 飞书 + `foreground_run_registry` |
| **P2** | 「对当前任务的补充说明」识别（场景四）；LLM 辅助冲突意图分析（替代纯规则）；并行执行的结果汇总展示 | 完整的多任务交互体验 | ✅/⏳（✅：本地 **supplement** + **X** + **P** + **AE**·r HTTP/可选 IM 中段并入；**AF**·s：**merge_parallel** 索引表 + 详块；**AA** discuss 前缀摘要；**AH**·t 多子句规则仲裁；⏳：**LLM** 全量仲裁） |

### 1.5 实施优先级（模块整体）

| 阶段 | 工作 | 预期收益 | 落地 |
|------|------|----------|------|
| **P0** | 后台任务状态注入 `run_agent` prompt（或统一注册表） | 前台感知 P3 负载 | ✅ **L+M+O**（含 HTTP 心跳）；⏳ 中心化集群注册 |
| **P1** | `GlobalTaskRegistry` 完整态；定时任务入口先注册再执行 | 并发感知与排队通知 | ⏳ 统一集群 CRUD/认证；✅ 文件 + HTTP 合并 + **L** + **进程内快照 R** |
| **P2** | `resource_tags` 细粒度抢占 | 资源互斥 | ⏳（✅ **W** + **D**：tags **登记 + 快照**；⏳ **调度抢占**） |

---

## 二、多 Agent 协作框架：主管 + 角色池 + 动态组队

### 2.1 现状与瓶颈

**当前 Agent 拓扑**：

```
用户 → run_agent（主 Agent）
         ↓ Action: delegate
      asyncio.gather([SubAgent1, SubAgent2, ...])
         ↓（每个 SubAgent 内部仍是 run_agent）
      结果汇聚 → 主 Agent 继续 ReAct
```

核心问题：

| 问题 | 表现 | 影响 |
|------|------|------|
| **SubAgent 无角色区分** | 所有 SubAgent 使用相同的 system prompt 骨架，只是任务不同 | 无法做「产品经理 vs 工程师 vs 测试」的专业分工 |
| **无讨论/辩论机制** | 复杂决策全靠主 Agent 单一视角，不会召开「内部会议」 | 复杂问题质量差，缺少多角度验证 |
| **角色固定，无法按场景创建** | 目前 SubAgent 只有「子任务执行者」一种形态 | 无法根据任务性质动态组建专业团队 |
| **嵌套 delegate 风险** | ~~子 Agent 默认可再 delegate~~ **已实现：子会话硬拒绝 `delegate`**；主会话仍受 `max_delegate_depth` 约束 | `agent_core.py` delegate 分支 |
| **协作结果无结构化合并** | 多 SubAgent 结果拼接后直接给主 Agent 看，没有「会议纪要」结构 | 主 Agent 需要自己解析噪音 |

### 2.2 优化目标（AGI 视角）

> **AGI 原则**：AGI 不是一个全知全能的单体，而是一个**组织**——有首席决策者（主 Agent），有专家小组（角色池），有会议记录员（结构化结果合并器），有执行者（工具调用）。复杂任务时召集合适的专家开会，而不是让一个人包打天下。

### 2.3 固定角色池（Persistent Role Pool）

**已落地（2026-05-18·c）**：仓库文件 **`skills_repo/agent_roles/role_pool.yaml`**，由 **`l3_node/agent_roles_loader.py`** 解析并 **追加** 至主 Agent 的 `delegate_hint`（与内置 coder/writer/… 并存）。运行时 **`SUB_AGENT_PROMPTS`** 已补充 **`critic` / `executor` / `domain_expert`**，delegate 的 `role` 填 YAML `id` 即可加载对应人设与工具白名单。

沿用原文的设计说明（历史归档）：

定义系统内置的核心 Agent 角色，每个角色有专属 system prompt 前缀与工具白名单：

```yaml
# 已落地示例见 skills_repo/agent_roles/role_pool.yaml（以下为概念草稿，可与文件对照迭代）

roles:
  - id: analyst
    name: "分析师"
    description: "负责数据分析、逻辑推理、假设验证"
    system_prefix: |
      你是一位严谨的数据分析师，擅长从数据中发现规律，用逻辑推导结论。
      你的输出必须包含：假设、证据、结论、置信度。
    allowed_tools: ["core:fs_read", "core:shell_exec", "mcp:code_exec", "core:local_memory_search"]
    
  - id: critic
    name: "批评者"
    description: "负责质疑方案、找漏洞、提出反驳意见"
    system_prefix: |
      你是一位严格的评审者，你的职责是找出方案中的问题、风险和遗漏。
      你必须给出至少 3 个质疑点，并对每点评估风险等级（高/中/低）。
    allowed_tools: ["core:local_memory_search"]
    
  - id: planner
    name: "规划者"
    description: "负责将复杂目标分解为可执行计划"
    system_prefix: |
      你是一位经验丰富的项目规划师，擅长将模糊目标转化为清晰的执行步骤。
      你的输出必须是结构化的任务树，每个节点包含：目标、依赖、预估时间、成功标准。
    allowed_tools: ["core:fs_write", "core:local_memory_append"]
    
  - id: executor
    name: "执行者"
    description: "负责执行具体操作，不做过多分析"
    system_prefix: |
      你是一位高效的执行者，专注于完成具体任务。减少废话，直接行动，报告结果。
    allowed_tools: ["*"]  # 执行者有完整工具权限
    
  - id: summarizer
    name: "总结者"
    description: "负责汇总多方意见，形成结构化会议纪要"
    system_prefix: |
      你是一位专业的会议记录员。将多个 Agent 的输出整理为结构化报告：
      共识点 / 分歧点 / 推荐行动 / 风险提示。
    allowed_tools: ["core:fs_write"]
    
  - id: domain_expert
    name: "领域专家（动态）"
    description: "由主 Agent 按场景实例化，注入专域 system prompt"
    system_prefix: "{{DYNAMIC_INJECTED_BY_ORCHESTRATOR}}"
    allowed_tools: "{{DYNAMIC_FROM_SKILL}}"
```

### 2.4 多 Agent 协作模式

#### 模式 A：并行执行模式（当前已有，需增强）

适用于：任务可拆分为独立子任务，无依赖。

```
主 Agent（Orchestrator）
├── SubAgent[executor] → 子任务 A
├── SubAgent[executor] → 子任务 B
└── SubAgent[executor] → 子任务 C
       ↓ asyncio.gather（已实现）
SubAgent[summarizer] → 汇总结果（新增）
       ↓
主 Agent 接收结构化会议纪要
```

#### 模式 B：讨论/辩论模式（新增）

适用于：复杂决策、有争议的方案、需要多角度验证。

```
主 Agent（Orchestrator）
├── Round 1（并行）
│   ├── SubAgent[planner] → 提出方案草稿
│   └── SubAgent[critic]  → 列出质疑点
├── Round 2（串行）
│   └── SubAgent[planner] → 根据批评修订方案
├── Round 3（并行，可选）
│   ├── SubAgent[analyst] → 数据验证
│   └── SubAgent[critic]  → 二次审查
└── SubAgent[summarizer]  → 输出最终共识

最大讨论轮次：nexus_config.multi_agent.max_discussion_rounds（默认 3）；可 **`JACHIN_DISCUSS_MAX_ROUNDS`** 覆盖（1..12）。每角色子 ReAct 上限：`discussion_item_max_iterations` / **`JACHIN_DISCUSS_ITEM_MAX_ITER`**（1..24）。
终止条件：critic 无新质疑点 OR 达到最大轮次
```

实现关键：在 `agent_core.py` 的 `delegate` 处理中新增 `mode: "discuss"` 参数：

```python
# Action Input 示例（模型输出）
{
  "mode": "discuss",
  "topic": "是否采用微服务架构",
  "roles": ["planner", "critic", "analyst"],
  "max_rounds": 3,
  "context": "当前系统是单体，用户量预计翻 10 倍"
}
```

#### 模式 C：动态角色创建（新增）

适用于：任务场景高度特殊，固定角色不够用。

主 Agent 可以在 ReAct 过程中动态定义新角色：

```python
# Action: delegate
# Action Input:
{
  "mode": "parallel",
  "sub_tasks": [
    {
      "task": "分析竞品的定价策略",
      "role": {
        "id": "pricing_strategist",  # 动态角色
        "name": "定价策略专家",
        "system_prefix": "你是一位拥有 10 年 SaaS 定价经验的顾问...",
        "allowed_tools": ["mcp:web_search", "core:fs_write"]
      }
    }
  ]
}
```

安全约束：
- 动态角色的 `allowed_tools` **只能是主 Agent 当前工具集的子集**（不可升权）
- 动态角色 `allow_delegate=False`（禁止再次动态创建角色，防递归）
- 动态角色描述注入 system prompt 前须经 `role_description_sanitize`（防 prompt 注入）

### 2.5 结构化结果合并器（Structured Result Merger）

所有 `delegate` 结果在返回主 Agent 前，经过 `SubResultMerger` 处理：

```python
# 新增：l3_node/multi_agent/result_merger.py（概念设计）

@dataclass
class SubAgentResult:
    role_id: str
    task: str
    output: str
    status: Literal["success", "partial", "failed"]
    token_used: int

class StructuredResultMerger:
    """将多个 SubAgent 结果合并为结构化 Observation"""
    
    def merge_parallel(self, results, *, max_output_chars=4000, with_index_table=True) -> str:
        """并行模式：可选 Markdown 索引表 + 列表式详块，保留来源标注（实现见仓库 result_merger.py）"""
    
    def merge_discussion(self, rounds: list[list[SubAgentResult]]) -> str:
        """讨论模式：按轮次展示共识演化，最终输出决策建议"""
```

### 2.6 实施优先级

| 阶段 | 工作 | 预期收益 | 落地 |
|------|------|----------|------|
| **P0** | `role_pool.yaml` 解析；delegate 使用扩展 `role`；角色工具白名单 | 可区分的专业角色 | ✅ YAML + loader + `SUB_AGENT_*` 扩展 |
| **P1** | 讨论模式（`mode: discuss`）；`StructuredResultMerger`；多轮讨论终止条件 | 复杂决策质量提升 | ✅ **AA** + **AF** + **AG**（轮次/子迭代 `nexus`+env）；⏳ 更细动态超参 |
| **P2** | 动态角色创建（安全沙箱验证）；Experience RAG 记录角色组合效果 | 自适应组队 | ✅/⏳（✅：**AB**（`_sanitize_inline_role`，防注入 + 防升权 + 禁递归）；**AG**·t 可选 `multi_agent:*` Experience 落盘；⏳ 自动评分/全量角色效果分析） |

---

## 三、任务拆解 + Hook 体系

### 3.1 现状分析

**当前任务拆解**：

```
用户意图 → LLM 自由 ReAct（无结构化拆解）
          ↓ 若模型决定 delegate
          Task Plan（task_plan.md 文件，模型自觉维护）
          ↓ planning_gate 门禁
          （危险操作需要 task_plan.md 存在）
```

**当前 Hook 体系**：

```
L3 HookRegistry（engine/hooks_pipeline.py）：
  on_intent_received
  before_llm_think
  before_tool_exec
  after_tool_exec
  before_response
  # 扩展（路线图 §3.2.3，2026-05-18 起）：on_task_decompose, on_task_node_start/done,
  # on_task_dag_complete, on_agent_team_assembled, on_discussion_round_start/end,
  # on_consensus_reached, on_retry, on_strategy_shift, on_execution_brief,
  # on_memory_commit, on_experience_learned
  # agent_core 已挂：delegate 且 sub_tasks 非空 → on_task_decompose；子任务起止 → on_task_node_*；
  # 纠偏续跑 → on_retry；[ExecutionBrief] → on_execution_brief；
  # schedule_nexus 后 → on_memory_commit；Experience 写入 → on_experience_learned

Core 侧（core/hooks_pipeline.py）：
  HOOK_BEFORE_LLM_THINK → compaction_hook, swarm_hook
```

核心问题：

| 问题 | 表现 |
|------|------|
| **任务拆解依赖模型「自觉」** | `task_plan.md` 由模型决定是否写，无强制结构 |
| **无任务图（DAG）** | 子任务之间没有显式的依赖关系，模型通过文本描述隐式维护 |
| **Hook 触发点不够细** | 没有「子任务开始/完成」、「讨论轮次」、「任务阻塞」等事件 |
| **Hook 无持久化** | Hook 结果不持久，跨会话的长任务无法续跑 |
| **无任务级别的韧性** | 子任务失败没有自动的重试/替代策略触发 |

### 3.2 优化方案：结构化任务树（Task DAG）

#### 3.2.1 任务树数据模型

```python
# 新增：l3_node/task_engine/task_dag.py（✅ 已落地：active.json 读入 + prompt 摘要）
# 完整 TaskNode/TaskDAG 调度器仍属 P1；以下仅为概念模型，与 §3.2.1 设计对齐。

@dataclass
class TaskNode:
    node_id: str
    title: str
    description: str
    status: Literal["pending", "running", "blocked", "done", "failed", "skipped"]
    
    # 依赖关系
    depends_on: list[str]           # node_id 列表
    blocks: list[str]               # 哪些任务等着我
    
    # 执行配置
    assigned_role: str | None       # 哪个 Agent 角色执行
    allowed_tools: list[str]        # 工具白名单
    max_retries: int                # 失败后最大重试次数
    fallback_strategy: str | None   # 失败后降级策略
    
    # 结果
    result: str | None
    error: str | None
    started_at: float | None
    finished_at: float | None

@dataclass  
class TaskDAG:
    dag_id: str
    title: str                      # 顶层任务名
    nodes: dict[str, TaskNode]      # node_id → TaskNode
    created_at: float
    session_id: str
    
    # 执行状态
    def get_ready_nodes(self) -> list[TaskNode]:  # 依赖全满足的节点
    def get_critical_path(self) -> list[str]:     # 关键路径
    def is_completed(self) -> bool
    def get_progress_report(self) -> str          # 供 prompt 注入
```

#### 3.2.2 任务拆解触发器

```
触发条件（任意一条满足时，自动进入「拆解模式」）：
1. 用户消息含「帮我完成」「制定计划」「执行以下步骤」等规划意图
2. 预估执行步骤 > 5 步（planning_gate 扩展）
3. 用户明确说「分步执行」「帮我拆解」
4. 当前任务涉及多个不同领域（hr + data + code）

拆解流程：
用户意图 
  → planning_gate 检查（已有）
  → 若需拆解：触发 on_task_decompose Hook
  → SubAgent[planner] 生成 TaskDAG（结构化 JSON）
  → 存储到 ~/.jachin/workspace/task_dags/<dag_id>.json
  → 注入 system prompt（替代 task_plan.md 的朴素文本）
  → 主 Agent 按 DAG 顺序调度执行
```

#### 3.2.3 增强 Hook 体系

在现有 5 个 Hook 点基础上新增：

```python
# 新增 Hook 事件（l3_node/engine/hooks_pipeline.py 扩展）

class HookEvent(str, Enum):
    # 现有
    ON_INTENT_RECEIVED = "on_intent_received"
    BEFORE_LLM_THINK = "before_llm_think"
    BEFORE_TOOL_EXEC = "before_tool_exec"
    AFTER_TOOL_EXEC = "after_tool_exec"
    BEFORE_RESPONSE = "before_response"
    
    # 新增：任务生命周期
    ON_TASK_DECOMPOSE = "on_task_decompose"       # 任务开始结构化拆解时
    ON_TASK_NODE_START = "on_task_node_start"     # DAG 某节点开始执行
    ON_TASK_NODE_DONE = "on_task_node_done"       # DAG 某节点完成（含失败）
    ON_TASK_DAG_COMPLETE = "on_task_dag_complete" # 整个 DAG 执行完成
    
    # 新增：多 Agent 协作
    ON_AGENT_TEAM_ASSEMBLED = "on_agent_team_assembled"  # 角色团队组建完成
    ON_DISCUSSION_ROUND_START = "on_discussion_round_start"
    ON_DISCUSSION_ROUND_END = "on_discussion_round_end"
    ON_CONSENSUS_REACHED = "on_consensus_reached"
    
    # 新增：韧性
    ON_RETRY = "on_retry"                          # 工具/子任务重试
    ON_STRATEGY_SHIFT = "on_strategy_shift"        # 策略切换（[StrategyShift] 日志）
    ON_EXECUTION_BRIEF = "on_execution_brief"      # 有界退出时产出 Brief
    
    # 新增：记忆
    ON_MEMORY_COMMIT = "on_memory_commit"          # 回合末记忆写入
    ON_EXPERIENCE_LEARNED = "on_experience_learned" # 新经验被 Experience RAG 记录
```

#### 3.2.4 Hook 持久化（解决跨会话长任务续跑）

```python
# 新增：l3_node/engine/persistent_hook_log.py（概念设计）

class PersistentHookLog:
    """将 Hook 事件持久化到 SQLite，使跨会话长任务可续跑"""
    
    _db: sqlite3.Connection  # ~/.jachin/workspace/hook_events.sqlite3
    
    async def log_event(
        self,
        event: HookEvent,
        dag_id: str | None,
        node_id: str | None,
        payload: dict,
        session_id: str,
        run_id: str
    ) -> None: ...
    
    async def get_events_for_dag(self, dag_id: str) -> list[HookEventRow]: ...
    
    async def can_resume_dag(self, dag_id: str) -> bool:
        """检查 DAG 是否有未完成节点，支持跨会话续跑"""
```

**✅ 轻量落地（2026-05-18）**：`l3_node/engine/persistent_hook_log.py` — 环境变量 `JACHIN_PERSIST_HOOKS=1` 时向 `hook_events` 表追加 `(hook, run_id, intent_preview, meta_json)`；**Q** / **U** / **V** / **R** / **W** 共用诊断 Token 族。完整 `PersistentHookLog` 类 API 与 DAG 回放仍属 ⏳（见 **§〇**）。

### 3.3 实施优先级

| 阶段 | 工作 | 预期收益 | 状态 |
|------|------|----------|------|
| **P0** | 增加 `ON_TASK_DECOMPOSE`（delegate 子任务）、`ON_TASK_NODE_START/DONE`、`ON_RETRY`、`ON_EXECUTION_BRIEF`、`ON_MEMORY_COMMIT`、`ON_EXPERIENCE_LEARNED` Hook 点；在现有 `agent_core` 中补触发 | 可观测性提升 | ✅ |
| **P1** | `TaskDAG` 数据模型；`task_plan.md` 迁移到结构化 JSON；`on_task_decompose` 自动触发逻辑 | 任务拆解结构化 | ⏳（✅ **H** `active.json` + `save_active_task_dag_dict`；✅ **delegate → on_task_decompose**；✅ **V** 只读 HTTP 拉取 `active.json`） |
| **P2** | `PersistentHookLog`；DAG 跨会话续跑；Hook 事件驱动的韧性策略（失败自动触发 `ON_RETRY` → 策略变更） | 真正的跨会话任务韧性 | ✅/⏳（✅：可选 SQLite 落盘 **K**、`hook_events.sqlite3`；✅ **Q** 只读 HTTP + **`run_id_exact`** 精确拉取单 run 事件链（**r**）；⏳：自动回放执行器/自动策略链） |

---

## 四、记忆架构现状分析与优化

### 4.1 当前记忆架构全景

```
┌─────────────────────────────────────────────────────────────┐
│                    记忆注入 System Prompt                     │
│  L0（Core_Profile、User_Persona）+ L1（近期核心块）           │
│  入口：memory_nexus_bridge（异步，超时可禁用）                │
└────────────────────┬────────────────────────────────────────┘
                     │
    ┌────────────────▼──────────────────────────────────────┐
    │           Memory Nexus（主存）                         │
    │  SQLite: ~/.jachin/palace_db/memory_nexus.sqlite3      │
    │  表: drawers (wing → room → drawer)                    │
    │  向量: FastEmbed（进程内）+ NumPy 打分                   │
    │  检索: 候选集最多 2500 条 → 内积排序 → Top-K            │
    │  写入: 回合末 schedule_nexus_turn_commit_async          │
    └───────────────────────────────────────────────────────┘
    
    ┌──────────────────────────────────────────────────────┐
    │          Experience RAG-lite（经验层）                 │
    │  JSONL: ~/.jachin/workspace/.jachin_experience.jsonl  │
    │  检索: difflib + Jaccard（无向量，字符串相似度）         │
    │  写入: 严格条件触发，非每轮                            │
    └──────────────────────────────────────────────────────┘
    
    ┌──────────────────────────────────────────────────────┐
    │          Core Memory（生物/核心记忆）                  │
    │  SQLite: core/biological_memory.py + memory_store.py  │
    │  用途: Core agent_loop 专属，与 L3 Nexus 独立          │
    └──────────────────────────────────────────────────────┘
    
    ┌──────────────────────────────────────────────────────┐
    │          工作区规划记忆（文件型）                       │
    │  文件: task_plan.md / progress.md / findings.md       │
    │  读写: 模型通过 fs_write/fs_read 工具操作              │
    │  入口: task_planning.get_planning_context_for_prompt   │
    └──────────────────────────────────────────────────────┘
    
    ┌──────────────────────────────────────────────────────┐
    │          遗留层（只读）                                │
    │  l3_local.json + shard_*.json（只读/诊断）             │
    │  memory_compactor（全局 no-op）                       │
    └──────────────────────────────────────────────────────┘
```

### 4.2 现有架构的问题

#### 问题 1：记忆层级混乱，缺少明确的「时间维度」

| 当前分层 | 设计目的 | 实际问题 |
|---------|---------|---------|
| Memory Nexus（Wing/Room/Drawer） | 空间隐喻（宫殿法） | 没有「短期/中期/长期」时间维度的概念 |
| Experience RAG-lite | 操作经验沉淀 | 用字符串相似度检索，语义召回率低 |
| Core Memory | Core Agent 专属 | 与 L3 Nexus 完全孤立，无法互通 |
| 工作区文件记忆 | 当前任务状态 | 是任务状态不是记忆，概念有混淆 |

从 AGI 角度，记忆应该分为：
- **工作记忆（Working Memory）**：当前会话的活跃上下文
- **情节记忆（Episodic Memory）**：具体事件的经历记录
- **语义记忆（Semantic Memory）**：概念、知识、规律
- **程序记忆（Procedural Memory）**：「怎么做」的经验/SOP

当前架构的 Wing/Room/Drawer 是空间隐喻，缺少上述时间和认知维度的对齐。

#### 问题 2：Experience RAG 召回质量差

```
现状：
  difflib.SequenceMatcher + Jaccard 系数
  → 「上次我帮你分析了飞书日报」vs「飞书周报分析」→ 低相似度，漏召回

目标：
  与 Memory Nexus 统一走 FastEmbed 向量检索
  → 语义相似 → 「飞书日报」「飞书周报」语义相近 → 高召回
```

#### 问题 3：记忆写入是被动的，没有主动学习

```
现状：
  - 回合末 schedule_nexus_turn_commit_async（✅ 默认跳过纯寒暄写入 General_Chat；仍可按长度阈值过滤极短轮）
  - Experience 写入条件严格，实际写入频率极低
  - 模型通过 core:local_memory_append 主动写（需要模型自觉）

问题：
  - 无关紧要的聊天也会写入 Nexus，稀释重要记忆
  - 真正有价值的「经验教训」（失败→重试→成功的过程）没有被系统化记录
  - 记忆无重要性分级
```

#### 问题 4：记忆检索没有「遗忘曲线」

```
现状：
  - next_prompt_cycle + last_accessed_turn 做衰减（已部分实现）
  - 但 Memory Nexus 的 Deep Search 是纯向量相似度，不含时间权重
  
问题：
  - 1 年前的记忆和 1 小时前的记忆在向量空间里平等竞争
  - 近期记忆应该有更高权重（类似人类短期增强效应）
```

#### 问题 5：无跨 Agent 的共享记忆

```
现状：
  - 每个 SubAgent 有独立的 workspace sandbox
  - SubAgent 的发现/经验无法自动沉淀到主 Agent 的 Nexus
  
问题：
  - SubAgent 处理完一个复杂子任务，下次相同任务还要从零开始
  - 团队协作的「知识积累」效应为零
```

### 4.3 优化方案

#### 方案 A：四维记忆体系（AGI 认知对齐）

```
工作记忆（Working Memory）
  → 当前会话消息列表（已有：_session_messages，截断约 30 条）
  → 当前任务 DAG 状态（新增）
  生命周期：单会话，不持久化

情节记忆（Episodic Memory）
  → 存储：Memory Nexus Wing=Episodes
  → 内容：「什么时间、什么场景、发生了什么、结果如何」
  → 写入：每次任务完成后触发 ON_TASK_DAG_COMPLETE Hook 自动写入
  → 检索：向量 + 时间权重混合打分
  生命周期：持久，按时间衰减（遗忘曲线）

语义记忆（Semantic Memory）
  → 存储：Memory Nexus Wing=Knowledge（原 User_Persona/General_Chat 重分类）
  → 内容：用户偏好、领域知识、组织信息
  → 写入：主动检测（模型 Action: learn_fact）+ 高置信度自动提炼
  生命周期：持久，衰减慢

程序记忆（Procedural Memory）
  → 存储：Experience JSONL（升级为 FastEmbed 向量检索）
  → 内容：「遇到 X 问题，用 Y 方法解决，注意 Z 坑」
  → 写入：ReAct 轨迹中「重试→成功」模式自动识别并提炼
  生命周期：持久，成功执行后强化，失败后标记为谨慎
```

#### 方案 B：Experience RAG 升级为向量检索

```python
# 修改：l3_node/experience_memory.py

# 当前：
similarity = SequenceMatcher(None, query, exp["situation"]).ratio()

# 优化后：
# 复用 Memory Nexus 的 FastEmbed 实例
from l3_client.local_mcps.jachin_memory_nexus.memory_backend import get_embedding

query_vec = get_embedding(query)
exp_vec = get_embedding(exp["situation"])  # 预计算并缓存
similarity = np.dot(query_vec, exp_vec)    # 内积 = 余弦相似度（归一化后）
```

或者更彻底地：将 Experience 条目迁入 Memory Nexus 的专用 Wing（`Wing=Procedures`），统一检索接口。

#### 方案 C：记忆重要性分级写入

```python
# 新增：l3_node/memory_importance_scorer.py（概念设计）

class MemoryImportanceScorer:
    """评估一段对话/操作的记忆重要性，决定写入策略"""
    
    IMPORTANCE_LEVELS = {
        "ephemeral": 0,   # 不写入（闲聊、重复问答）
        "low": 1,         # 写入但低权重，快速衰减
        "medium": 2,      # 正常写入
        "high": 3,        # 高优先级写入，慢衰减
        "critical": 4,    # 核心记忆，几乎不衰减（如用户核心偏好）
    }
    
    def score(self, turn: TurnContext) -> int:
        """
        评分规则：
        - 包含「记住」「下次」「以后」等明确记忆指令 → critical
        - 任务成功完成（有明确结果）→ high
        - 用户纠正了 Agent 的行为 → high（负向经验）
        - 工具重试/策略切换场景 → medium（程序记忆）
        - 普通问答、信息查询 → low
        - 重复的同类问答（已有相似记忆）→ ephemeral
        """
```

#### 方案 D：时间权重混合打分（遗忘曲线）

在 `memory_backend.py` 的 Deep Search 中加入时间衰减因子：

```python
# 修改：l3_client/local_mcps/jachin_memory_nexus/memory_backend.py

def _compute_time_decay(timestamp: float, now: float, half_life_days: float = 30) -> float:
    """
    Ebbinghaus 遗忘曲线的简化版本：
    decay = 0.5 ^ (age_days / half_life_days)
    
    对于不同类型的记忆，half_life_days 不同：
    - 程序记忆（Procedures）：180 天
    - 语义记忆（Knowledge）：90 天
    - 情节记忆（Episodes）：30 天
    """
    age_days = (now - timestamp) / 86400
    return 0.5 ** (age_days / half_life_days)

# 在 Deep Search 打分中：
# final_score = semantic_similarity * (1 - time_weight) + time_decay * time_weight
# time_weight 可配置，默认 0.2（语义优先，时间辅助）
```

#### 方案 E：SubAgent 经验自动沉淀到主 Agent

```python
# 在 _run_sub_agent 完成后，触发 ON_TASK_NODE_DONE Hook
# Hook 处理器检查 SubAgent 是否产生了值得沉淀的经验：
# - 如果 SubAgent 经历了重试/策略切换 → 提炼为程序记忆
# - 如果 SubAgent 产生了重要发现（findings.md 有更新）→ 写入情节记忆
# - 沉淀目标：主 Agent 的 Wing=Procedures 或 Wing=Episodes
```

### 4.4 实施优先级

| 阶段 | 工作 | 预期收益 | 状态 |
|------|------|----------|------|
| **P0** | Experience RAG 升级为 FastEmbed 向量检索；记忆重要性分级（禁止闲聊写入 Nexus）| 检索质量提升，Nexus 不再被无效记忆稀释 | ✅（✅：**J** 寒暄 + **低价值助手回复**跳过 Nexus 回合写入；Experience 可选嵌入重排；**AI**·u Wing 重要性乘数分级打分） |
| **P1** | 四维记忆分类（Wings 重新映射为 Episodes/Knowledge/Procedures）；时间权重打分 | 记忆检索更像人类认知 | ✅/⏳（✅：**Y**·p 遗忘曲线时间权重；**AI**·u 重要性乘数；⏳ Wing 全量重映射） |
| **P2** | SubAgent 经验自动沉淀；`ON_EXPERIENCE_LEARNED` Hook；记忆跨 Agent 共享 | 团队协作知识积累 | ✅/⏳（✅：SQLite 门控写入后 Hook；**AG**·t 多 Agent 回合摘要可选写入 JSONL + Hook；⏳：自动沉淀策略与跨 Agent 共享） |

---

## 五、24 小时真正无人值守机器人

### 5.1 「真正无人值守」的定义

当前系统能做到「有人监督的自动化」，但离「真正无人值守」还差以下能力：

| 能力 | 当前状态 | 差距 |
|------|---------|------|
| **自主判断「现在该做什么」** | 完全依赖用户触发或外部 cron | 无内部驱动力 |
| **异常自愈** | 部分（重试/策略切换）| 不能自主决策「放弃本次」还是「换方案」 |
| **状态持久与跨进程续跑** | 后台任务有 SQLite 持久化 | 进程重启后无法恢复主会话上下文 |
| **主动汇报** | 后台任务有 `l3_event_bus` 广播 | 无「日终总结」「异常告警」的主动推送 |
| **资源自我监控** | 无 | 不知道自己的 token 消耗、成本、磁盘空间 |
| **意图持久化** | 无 | 用户说「帮我每周一自动发报告」→ 下次重启丢失 |

### 5.2 优化方案：自主性五层架构

#### Layer 1：意图持久化（Persistent Intent）

```
用户说「每周一上午 9 点帮我生成飞书日报并发送」
→ IntentPersister 将此意图存入 SQLite
→ 进程重启后 IntentRecovery 恢复定时任务注册
→ 不再依赖外部 cron 文件
```

```python
# 新增：l3_node/autonomy/intent_persister.py（概念设计）

@dataclass
class PersistedIntent:
    intent_id: str
    description: str        # 自然语言描述，供诊断
    trigger: IntentTrigger  # 时间触发 / 事件触发 / 条件触发
    action: str             # 要执行的任务描述
    created_at: float
    last_executed_at: float | None
    enabled: bool
    
    # 韧性
    max_retries_per_execution: int
    failure_notification_channel: str | None  # 失败时通知哪个 channel

class IntentTrigger:
    type: Literal["cron", "event", "condition", "interval"]
    cron: str | None        # "0 9 * * 1" = 每周一 9 点
    event: str | None       # "on_new_feishu_message"
    condition: str | None   # "when: memory.contains('urgent task')"
    interval_sec: int | None
```

#### Layer 2：自主任务感知（Autonomous Awareness）

系统需要一个「自我意识循环」，每隔一段时间检查：

```python
# 新增：l3_node/autonomy/awareness_loop.py（概念设计）

class AutonomousAwarenessLoop:
    """
    每 N 分钟运行一次「意识扫描」：
    1. 检查所有 PersistedIntent，识别即将触发的任务
    2. 检查后台任务队列健康状态
    3. 检查资源使用（token 消耗、磁盘空间）
    4. 识别异常模式（某任务连续失败 3 次）
    5. 决策是否需要主动通知用户
    """
    
    scan_interval_sec: int = 60  # 默认每分钟扫描一次
    
    async def scan_once(self) -> list[AutonomousAction]:
        """返回需要执行的自主动作列表"""
    
    async def _check_resource_health(self) -> ResourceHealthReport:
        """检查 token 消耗速率、磁盘空间、API 错误率"""
    
    async def _identify_anomalies(self) -> list[AnomalyAlert]:
        """识别连续失败、死循环、token 超标等异常"""
```

#### Layer 3：自愈策略引擎（Self-Healing Engine）

基于现有的 `ExecutionBrief` 和 `[StrategyShift]` 机制增强：

```
当前：
  工具失败 → 重试（有上限）→ 策略切换 → 产出 ExecutionBrief → 停止

优化后（三级自愈）：
  Level 1 - 工具级自愈（已有）：
    同工具重试（有限次）→ 换参数/降级参数 → 告警

  Level 2 - 任务级自愈（新增）：
    单个 DAG 节点连续失败 → 
    a. 检查 Experience RAG：过去是否遇到类似失败？用什么方法解决的？
    b. 召集 SubAgent[analyst] 分析失败原因
    c. 生成替代方案 → 人工确认 OR 置信度足够时自动执行

  Level 3 - 系统级自愈（新增）：
    整个 Intent 连续 N 次失败 →
    a. 暂停该 Intent（避免资源浪费）
    b. 生成详细的 ExecutionBrief（包含所有尝试记录）
    c. 主动推送飞书/邮件通知用户
    d. 在下次用户交互时主动提起「有个任务需要您的帮助」
```

#### Layer 4：主动汇报系统（Proactive Reporting）

```python
# 新增：l3_node/autonomy/proactive_reporter.py（概念设计）

class ProactiveReporter:
    """无人值守期间的主动汇报"""
    
    REPORT_TRIGGERS = [
        "daily_summary",      # 每日终总结
        "task_complete",      # 重要任务完成
        "anomaly_detected",   # 检测到异常
        "budget_warning",     # Token/费用接近阈值
        "intent_paused",      # 某持久意图被暂停
    ]
    
    async def generate_daily_summary(self) -> DailySummaryReport:
        """
        每日总结包含：
        - 今日执行了哪些任务，结果如何
        - 哪些任务成功/失败/跳过
        - Token 消耗统计（与上周对比）
        - 新学到的经验（写入了什么到 Procedural Memory）
        - 明日预计执行的任务
        - 需要用户关注的问题
        """
    
    async def push_to_feishu(self, report: str, urgency: Literal["info", "warning", "urgent"]) -> None:
        """通过 MCP 飞书工具推送，不依赖用户主动查询"""
```

#### Layer 5：AGI 自主循环（Autonomous Agent Loop）

将上述各层整合为一个持续运行的「自主循环」：

```
┌─────────────────────────────────────────────────────┐
│                  AGI 自主循环（24h）                  │
│                                                      │
│  每 60s 意识扫描：                                    │
│  ┌──────────────────────────────────────────────┐   │
│  │ 1. 检查 PersistedIntent → 触发待执行的任务     │   │
│  │ 2. 检查任务健康 → 识别异常，触发自愈           │   │
│  │ 3. 检查资源 → 接近阈值则告警                  │   │
│  │ 4. 每日 23:55 触发 DailySummary 生成+推送     │   │
│  └──────────────────────────────────────────────┘   │
│                                                      │
│  任务执行层（按需）：                                  │
│  ┌──────────────────────────────────────────────┐   │
│  │ background_task_service（已有）               │   │
│  │ + TaskDAG（新增）                            │   │
│  │ + MultiAgent 协作（新增）                    │   │
│  └──────────────────────────────────────────────┘   │
│                                                      │
│  记忆沉淀层（回合末）：                               │
│  ┌──────────────────────────────────────────────┐   │
│  │ schedule_nexus_turn_commit_async（已有）       │   │
│  │ + Experience 自动提炼（新增）                 │   │
│  │ + 四维记忆分类写入（新增）                   │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

### 5.3 无人值守关键保障机制

#### 5.3.1 越界防护（Guardrails）

```
无人值守时必须有的防护：
1. Token 日消耗硬上限（超过即暂停所有 IntentTask，告警用户）
2. 文件系统写操作不得超出 workspace 范围（已有 sandboxes）
3. 外部 API 调用有速率限制（避免误触发大量飞书消息）
4. 危险操作（删除、外发敏感数据）必须经人工确认（planning_gate 已有，需对无人值守场景特殊标注）
5. 所有自主执行的操作必须记录 AutonomousActionLog（可审计）
```

#### 5.3.2 进程守护与自动重启

```
当前：Unix SIGTERM 有优雅停机；Windows 强杀无保证

优化：
- 提供 systemd service / Windows Service 配置模板
- 进程重启后：
  a. _recover_sqlite_pending_queue（已有）
  b. IntentRecovery：恢复所有 PersistedIntent 的调度
  c. TaskDAGResume：恢复跨会话任务 DAG 的执行状态
  d. 发送「系统重启，已恢复 N 个任务」通知
```

#### 5.3.3 可观测性面板

**已落地（2026-05-18·q · AD）**：`l3_node/autonomy/dashboard.py` → **`GET /api/v1/autonomy/status`**（与 **Q**/**R** 同源诊断 Token）；返回进程运行时长、PersistedIntent 活跃数、前台登记任务数、P3 后台队列指标、`today_token_used` / `today_token_budget`、异常意图列表、下一启发式调度任务、磁盘剩余等。LLM 每次响应经 `llm_client._apply_usage_budget` → `llm_budget.record_daily_llm_usage` 写入 `workspace/llm_token_daily.json`。

历史设计草稿（字段对齐参考）：

```
返回示例：
{
  "uptime_hours": 48.5,
  "active_intents": 3,
  "running_tasks": 1,
  "queued_tasks": 0,
  "today_token_used": 45000,
  "today_token_budget": 200000,
  "today_tasks_completed": 7,
  "today_tasks_failed": 1,
  "last_experience_learned": "2026-05-18T08:30:00",
  "anomalies": [],
  "next_scheduled_task": {"intent_id": "fb_report", "at": "2026-05-19T09:00:00"}
}
```

### 5.4 实施优先级

| 阶段 | 工作 | 预期收益 | 落地 |
|------|------|----------|------|
| **P0** | `PersistedIntent` 数据模型 + SQLite 存储；`IntentRecovery` 进程重启恢复；日终总结自动生成+飞书推送 | 意图不再因重启丢失；有主动汇报 | ✅ **Z+AC**（`intent_persister.py` + `awareness_loop.py` + `proactive_reporter.py`；HTTP CRUD；`bootstrap.start_autonomy_services()` on_startup） |
| **P1** | `AutonomousAwarenessLoop`（60s 扫描）；Level 2 任务级自愈（Experience RAG 辅助诊断）；Token 日消耗硬上限 | 真正的自主性基础 | ✅/⏳（✅：**AC** AwarenessLoop；**AK**·u 失败意图自动重置 + 自愈推送（Level 2 轻量自愈）；⏳：完整 Experience RAG 辅助诊断） |
| **P2** | 完整越界防护 Guardrails；可观测性面板；Level 3 系统级自愈；动态意图（「当检测到X时自动执行Y」） | 企业级无人值守 | ✅/⏳（✅：**AD** HTTP 面板 + 今日 Token 落盘；**AJ**·u condition 类动态意图（内置条件）；⏳ 完整 Guardrails / Level3 / LLM 驱动条件） |

---

## 六、Skill 自动进化：MD 格式 + 错误自愈后自动更新 + 零感知进化日志

### 6.1 设计背景

Jachin 的所有 Skill 统一使用 **SKILL.md 格式**（YAML Frontmatter + Markdown 正文）。这意味着 Skill 的人设、规则、工具白名单等**全部存储在纯文本文件中**，天然可被程序读写。

**Skill 来源分两类**：
- **本地开发**（`skills_repo/{name}/SKILL.md`）：仓库内直接维护
- **L1 订阅**（`~/.jachin/skills/{plugin_id}/SKILL.md`）：从 L1 平台下载后在本地运行，可自动进化；再次从 L1 推送新版时触发同步保护（smart merge）

当系统在执行某个 Skill 时：
1. **出错** → Level 3 自愈（**AQ**）诊断并生成修复路径，同步预存进化候选（**healing 路径**）
2. **下次成功时立即应用** → 不等 N 次，诊断即触发
3. 或者 **连续成功 N 次**（默认 3）→ 主动路径基于 Experience RAG 进化
4. 所有变更零用户感知，完整日志留存

### 6.2 进化工作流（AY）

两条触发路径，最终都调用 `_apply_evolution`：

```
[healing 路径（优先）]─────────────────────────────────────────────────
Intent 失败 N 次（consecutive_failures ≥ threshold）
    ↓ awareness_loop → level3_healer.run_level3_healing（AQ）
    ↓ _try_stage_skill_evolution → stage_evolution_candidate
    ↓ manifest.pending_evolution 预存（RAG 证据 + 失败描述）
    ↓ 下次 Intent 成功
    ↓ awareness_loop._try_skill_evolution_after_success
    ↓ run_skill_evolution_if_ready → consume_staged_evolution
    ↓ _apply_evolution（mode=healing）
        ├─ _summarize_healing_evidence（失败描述 + 修复路径）
        ├─ _call_llm_evolve（healing 模式：追加防错规则）
        ├─ _validate → _snapshot → 写 SKILL.md → 写日志
        └─ 清除 pending_evolution

[proactive 路径（次要）]────────────────────────────────────────────────
Intent 连续成功 ≥ JACHIN_SKILL_EVOLVE_MIN_SUCCESSES（默认 3）次
    ↓ awareness_loop._try_skill_evolution_after_success
    ↓ run_skill_evolution_if_ready（无 staged 候选时走此路径）
    ↓ analyze_and_evolve_skill → _apply_evolution（mode=proactive）
        ├─ Experience RAG retrieve（成功记录）
        ├─ _call_llm_evolve（proactive 模式：提炼优化规则）
        ├─ _validate → _snapshot → 写 SKILL.md → 写日志
        └─ 重置成功计数器
```

### 6.3 L1 订阅 Skill 生命周期

```
L1 平台发布 Skill（plugin_id + version）
    ↓ L3 订阅 → handle_upstream_update（skill_sync_guard）
    ↓ 首次安装：写 ~/.jachin/skills/{plugin_id}/SKILL.md
    ↓ mark_skill_origin（记录 origin=l1_subscribed + upstream_version）
    ↓ 正常使用 → 自动进化（本地 SKILL.md 修改，diverged_from_upstream=True）

L1 推送新版本
    ↓ handle_upstream_update（skill_sync_guard）
    ├─ 无本地进化 → 直接覆盖（透明同步）
    ├─ 有本地进化 + JACHIN_SKILL_SYNC_AUTO_MERGE=1（默认）
    │   ↓ 3-way smart merge
    │   │   base     = skill_snapshots/{skill}/upstream/{old_ver}.md
    │   │   local    = 当前本地（含进化）
    │   │   upstream = 新版
    │   ├─ Frontmatter → 取 upstream（接受 mcp_tools/工具链更新）
    │   ├─ Rules/Persona → 保留 local 进化；若双方均改则 local 优先 + ⚠️ 注释
    │   └─ 写入 merged SKILL.md + 写 sync_merged 日志
    └─ 有本地进化 + auto_merge 关闭 → 跳过，写 sync_skipped 日志
```

**per-skill 状态文件** `.skill_evo_manifest.json` 存储在 SKILL.md 同目录：
```json
{
  "skill_name": "com.jachin.bi.analysis",
  "origin": "l1_subscribed",
  "upstream_version": "1.0.0",
  "local_version": "1.0.3",
  "local_evolution_count": 3,
  "diverged_from_upstream": true,
  "pending_evolution": null
}
```

### 6.3 SKILL.md 格式约定

进化引擎 **只修改正文 Markdown 部分**，严格保护 Frontmatter 结构：

| 字段 | 进化引擎行为 |
|------|-------------|
| `name` / `mcp_tools` / `tools` | **绝对不改**（validate 阶段校验） |
| `version` | **自动 +0.0.1**（记录进化次数） |
| `# Rules` 段 | 追加新规则（`- ` 列表项，≤50 字/条） |
| `# Examples` 段 | 可追加成功示例 |
| `## 补充经验` | 若无 Rules 段，在文末新增此段 |

**SKILL.md 热重载（与进化联动）**：招聘域注入 `skills_repo/hr-recruitment/SKILL.md`（及缓存路径）时，正文包在 HTML 标记 `<!--JACHIN_HR_SKILL_MD_BODY-->` … `<!--/JACHIN_HR_SKILL_MD_BODY-->` 内；主路径 ReAct **每轮** LLM 前由 `l3_node/skill_md_hot_reload.py` 从磁盘替换该段，使 **磁盘变更后下一轮 Thought 即见新规则**（默认 `JACHIN_SKILL_MD_HOT_RELOAD=1`）。**§六 P2 inline**：以下写盘路径在成功保存后均需调用 `notify_skill_md_changed_from_disk_write`，对已注册的 ReAct 上下文置 `_skill_sop_dirty` 并 bump 世代；下一次 `HOOK_BEFORE_LLM_THINK` 前与热重载同逻辑刷新，并同步 `_react_system_prompt_full`（`JACHIN_SKILL_MD_INLINE_ENABLE`，默认开）：① `skill_evolver._apply_evolution`；② `skill_sync_guard.handle_upstream_update`（首次安装、无本地进化覆盖、smart merge、强制覆盖）。其它域 SKILL 尚未统一该机制。**路线图 §〇** 将该横切能力单独列为「前台 SOP / 提示词热同步」，与 Skill 自动进化条目正交。

### 6.4 进化日志

所有事件（进化/拒绝/演练/同步分叉）均写入：

```
~/.jachin/workspace/skill_evolution.jsonl
```

日志条目（JSONL，每行一条）：

```json
{
  "evolution_id": "uuid | sync_{ts}",
  "skill_name": "com.jachin.bi.analysis",
  "status": "applied | rejected | dry_run | error | sync_merged | sync_skipped | sync_forced",
  "trigger": "proactive | healing | upstream_sync",
  "origin": "l1_subscribed | local",
  "upstream_version": "1.0.0",
  "change_summary": "新增规则：采集失败时重试 3 次并降级到历史缓存",
  "change_ratio": 0.08,
  "original_hash": "abc123",
  "new_hash": "def456",
  "snapshot_path": "~/.jachin/workspace/skill_snapshots/.../",
  "evidence_count": 5,
  "confidence": 0.85,
  "model": "qwen-plus",
  "timestamp": 1716000000.0
}
```

### 6.5 安全与回滚

- **改动比例上限**：默认 30%，超出则拒绝并记录 `rejected` 日志，需人工确认（可调 `JACHIN_SKILL_EVOLVE_MAX_PATCH_RATIO`）
- **快照备份**：每次写入前将原版备份到 `skill_snapshots/{skill_name}/{timestamp}_{evo_id[:8]}.md`，随时可手动回滚
- **演练模式**：`JACHIN_SKILL_EVOLVE_DRY_RUN=1` 只分析记录，不修改文件
- **危险内容过滤**：检测 `rm -rf`、`os.system`、`subprocess` 等注入风险

### 6.6 环境变量速查

| 变量 | 默认值 | 说明 |
|------|---------|----|
| `JACHIN_SKILL_EVOLVE_ENABLE` | `0`（关） | 开启 Skill 自动进化 |
| `JACHIN_SKILL_EVOLVE_MIN_SUCCESSES` | `3` | proactive 路径触发阈值 |
| `JACHIN_SKILL_EVOLVE_MAX_PATCH_RATIO` | `0.3` | 最大改动比例（超出拒绝） |
| `JACHIN_SKILL_EVOLVE_DRY_RUN` | `0` | 演练模式（只分析不修改） |
| `JACHIN_SKILL_EVOLVE_MODEL` | `LLM_MODEL` | 进化 LLM 模型 |
| `JACHIN_SKILL_L1_CACHE` | `~/.jachin/skills/` | L1 订阅 Skill 根目录 |
| `JACHIN_SKILL_SYNC_AUTO_MERGE` | `1`（开） | L1 更新时自动 smart merge |
| `JACHIN_SKILL_SYNC_FORCE_OVERWRITE` | `0` | 强制覆盖本地进化（危险） |
| `JACHIN_SKILL_MD_HOT_RELOAD` | `1`（开） | ReAct 每轮刷新 HR 招聘 SKILL.md 注入段（磁盘最新正文） |
| `JACHIN_SKILL_MD_INLINE_ENABLE` | `1`（开） | P2：进化写盘后对前台 ReAct 打 `_skill_sop_dirty` + 世代 bump |

### 6.7 实施优先级

| 阶段 | 工作 | 预期收益 | 状态 |
|------|------|----------|------|
| **P0** | `skill_evolver.py` 核心引擎 + `awareness_loop` 触发接入 | Skill 可自动进化，零用户感知 | ✅ **AY**·z |
| **P0+** | `skill_sync_guard.py` L1 订阅保护 + healing 路径预存候选 | L1 订阅 Skill 全生命周期闭环 | ✅ **AY**·aa |
| **P1** | SKILL.md 热重载（HR 招聘 SOP 段，ReAct 每轮刷新） | 进化后同一会话内生效 | ✅ **AY**·ab |
| **P2** | ReAct 中途 inline 进化（写盘 notify + `_skill_sop_dirty` + 同步冻结 prompt） | 进化后同一 run 内尽快对齐 LLM | ✅ **AY**·ac |
| **P3** | 多 Skill 协同进化 | 系统级知识迁移 | ✅ 初版：`evolution_peers` + `JACHIN_SKILL_COEVOLVE_ENABLE`（一跳 `co_evolve`，`co_evolve_from` 审计） |

---

## 七、整体实施路线图

### 阶段划分

```
第一阶段（2-4 周）：基础设施增强
├── [并发] ~~后台任务注册表统一化~~ ✅ **L** + **M** + **O** + P3 + **R** + **S** + **T** + **W**（IM 深度/tags 观测）；⏳ 飞书队列 / 集群 SSOT（**§〇**）
├── [并发] ~~前台感知~~ ✅ prompt 合并前台路数 + 后台队列
├── [记忆] Experience RAG ~~升级 FastEmbed~~ ✅ 可选 `JACHIN_EXPERIENCE_USE_EMBED`；✅ **J**（闲聊 + 低价值助手跳过 Nexus）；✅ **Y** 遗忘曲线时间权重；✅ **AI** Wing 重要性乘数；✅ **AL** `wing_registry.py` 规范 Wing 注册表；⏳ 跨 Agent 共享 / 向量主导
├── [Hook] ~~新增任务生命周期 Hook 事件~~ ✅ …；✅ **`run_id_exact`** 单 run 事件链（**r**）；✅ **AO** DAG 续跑引擎（v）；✅ **AP** DAG 级 Guardrails（w）；✅ **AR** Handoff 转交（w）；✅ **AS** Coordinator Phase 2（x）；⏳ 专用 Coordinator 服务 / Redis 分布式锁
├── [任务拆解] TaskDAG ✅ `save_active_task_dag_dict` + `active.json` prompt；✅ `on_task_decompose`；✅ 只读 **V**；⏳ Planner 自动维护
└── [无人值守] ~~PersistedIntent SQLite 持久化~~ ✅ **Z** + HTTP CRUD + `IntentRecovery`

第二阶段（4-8 周）：协作与自主
├── [多 Agent] ~~role_pool.yaml + delegate~~ ✅（§2.3）；~~`mode: discuss`~~ ✅ **AA** + **AB**；~~并行 delegate 汇总~~ ✅ **AF**·s；~~讨论超参/多Agent经验~~ ✅ **AG**·t；~~第二条多段仲裁~~ ✅ **AH**·t
├── [任务拆解] TaskDAG 完整数据模型与调度（替代「仅靠」task_plan.md）⏳
├── [并发] GlobalTaskRegistry 完整 + 优先级排队通知 ⏳
├── [无人值守] ~~AutonomousAwarenessLoop~~ ✅ **AC**（60s 扫描 + interval 触发 + 告警）
└── [无人值守] ~~日终总结自动生成+推送~~ ✅ **AC** `ProactiveReporter`

第三阶段（8-16 周）：AGI 级能力
├── [多 Agent] 讨论模式超参调优 + Experience 记录角色效果
├── [任务拆解] PersistentHookLog + DAG 跨会话续跑
├── [记忆] 四维 Wing 重映射 + SubAgent 经验自动沉淀
├── [记忆] SubAgent 经验自动沉淀
├── [Skill 进化] ~~P0~~ ✅ **AY**·z；✅ **ab** 热重载；✅ **ac** inline notify（`JACHIN_SKILL_MD_INLINE_ENABLE`）；✅ **P3** 协同进化（`evolution_peers`，`JACHIN_SKILL_COEVOLVE_ENABLE`）；⏳ 非 HR 域
├── [无人值守] ~~条件触发动态意图（内置）~~ ✅ **AJ**（condition 类意图）；~~LLM fallback 条件评估~~ ✅ **AM**（v）
├── [无人值守] ~~Level 2 失败自愈~~ ✅ **AK**（自动重置 + 通知）；⏳ Experience RAG 辅助诊断
└── [无人值守] ~~Guardrails 基础~~ ✅ **AN**（五维，v）；~~Level 3 自愈~~ ✅ **AQ**（Experience RAG 诊断，w）；✅ **AD**（HTTP 面板）；⏳ L2 Coordinator / 完整 DAG 编排
```

### 优先级决策依据

| 维度 | 权重 | 说明 |
|------|------|------|
| 用户感知价值 | 40% | 用户直接感受到的改善（响应质量、自主性） |
| 工程风险 | 30% | 改动核心路径（`agent_core`）风险高 |
| 依赖关系 | 20% | 后续功能依赖的基础设施优先做 |
| 现有代码复用 | 10% | 能复用现有机制的方案优先 |

---

## 八、AGI 思路总结

这 6 个方向本质上是让 Jachin 从「**会执行任务的工具**」进化为「**有自主性且会自我进化的智能体**」：

| 维度 | 工具视角 | AGI 视角 |
|------|---------|---------|
| 并发 | 一次做一件事 | 多线意识并行，主意识保持响应 |
| 多 Agent | 执行子任务 | 组建专业团队，集体决策 |
| 任务拆解 | 模型自由发挥 | 结构化任务图，可跟踪可续跑 |
| 记忆 | 存档历史消息 | 认知进化，越用越聪明 |
| 无人值守 | 按时跑脚本 | 主动感知世界，自主决策，知道边界 |
| **Skill 进化** | 人工维护规则文件 | **错误→修复→自动更新 SKILL.md，系统越用越精准** |

真正的 AGI 不是「更强的 LLM」，而是**有组织架构、有记忆进化、有自主意识、有边界感知、且能自我进化的系统**。以上优化方向对齐的正是这个目标。

---

## 九、修订记录

| 日期 | 说明 |
|------|------|
| 2026-05-18（ae） | **AZ** 横切「前台 SOP / 提示词热同步」：`skill_sync_guard.handle_upstream_update` 成功写盘/合并后与 **AY** 同源调用 `notify_skill_md_changed_from_disk_write`；§〇 新表格行、§六热重载段、落地表 **AZ**、**AY** 行、本轮增量（ae）、`.cursor/rules/091` §3/§4 同步。 |
| 2026-05-18（ad） | §六 **P3**：`skill_evolver._propagate_co_evolve_to_peers` — `evolution_peers` / `co_evolve_peers`；`JACHIN_SKILL_COEVOLVE_ENABLE` / `JACHIN_SKILL_COEVOLVE_MAX_PEERS`；`trigger=co_evolve`、`co_evolve_from`；§〇 Skill 行、§6.7、阶段树、`SKILL_MD_SPEC`、`091-skill-auto-evolution.mdc` 同步。 |
| 2026-05-18（ac） | §六 **P2**：`skill_md_hot_reload.py` — `notify_skill_md_changed_from_disk_write`（`skill_evolver` 写盘成功后）、`register_react_ctx_for_skill_inline`（ReAct 起算前）、`_skill_sop_dirty` + `_hr_skill_md_gen_seen` + 同步 `_react_system_prompt_full`；`apply_hr_skill_md_hot_reload_to_react_ctx` 置于 `HOOK_BEFORE_LLM_THINK` 前；`JACHIN_SKILL_MD_INLINE_ENABLE`。§六/§〇/落地表 **AY**/阶段树 **Skill 进化** 同步。 |
| 2026-05-18（z） | **AY** \l3_node/autonomy/skill_evolver.py\ Skill 自动进化引擎（\nalyze_and_evolve_skill\uff1aLLM 生成最小 patch → 改动比例验证（≤30%）+ frontmatter 保护 + 危险内容过滤 → 快照备份 → 写入新版 SKILL.md → JSONL 进化日志；\_bump_version\ 自动递增 frontmatter version；\wareness_loop._try_skill_evolution_after_success\ + \_extract_skill_name_from_action\ 成功路径触发；\JACHIN_SKILL_EVOLVE_ENABLE=1\ 开启，\JACHIN_SKILL_EVOLVE_DRY_RUN=1\ 演练模式；零用户感知）；§〇/§六（新章节）/§七阶段树/§八总结表/落地表 **AY**/本轮增量（z）同步。 |
| 2026-05-18（y） | **AT** `global_task_registry.py` SQLite 跨进程 SSOT + `resource_tags` 抢占（`check_and_preempt`，P1>P2>P3>P4，TTL 清僵尸）；**AU** `session_instruction_queue.py` SessionInstructionQueue（SERIAL/PARALLEL，`JACHIN_SIQ_MODE=PARALLEL`，弱引用会话）；**AV** `dag_planner.py` TaskDAG LLM 自动拆解 + 写回 `active.json`（启发式触发，`JACHIN_DAG_AUTO_PLAN=1`）；**AW** `memory_backend._db_file` 共享路径 + `_vector_lead_mode`（`JACHIN_NEXUS_SHARED_PATH`/`JACHIN_NEXUS_VECTOR_LEAD=1`）；**AX** `im_second_instruction` `classify_busy_followup_llm` + `dispatcher` 接入（`JACHIN_IM_LLM_CONFLICT_RESOLVE=1`）；§〇/落地表/本轮增量（y）同步。 |
| 2026-05-18（y） | **AT** `global_task_registry.py` SQLite 跨进程 SSOT + `resource_tags` 抢占（`check_and_preempt`，P1>P2>P3>P4，TTL 清僵尸）；**AU** `session_instruction_queue.py` SessionInstructionQueue（SERIAL/PARALLEL，`JACHIN_SIQ_MODE=PARALLEL`，弱引用会话）；**AV** `dag_planner.py` TaskDAG LLM 自动拆解 + 写回 `active.json`（启发式触发，`JACHIN_DAG_AUTO_PLAN=1`）；**AW** `memory_backend._db_file` 共享路径 + `_vector_lead_mode`（`JACHIN_NEXUS_SHARED_PATH`/`JACHIN_NEXUS_VECTOR_LEAD=1`）；**AX** `im_second_instruction` `classify_busy_followup_llm` + `dispatcher` 接入（`JACHIN_IM_LLM_CONFLICT_RESOLVE=1`）；§〇/落地表/本轮增量（y）同步。 |
| 2026-05-18（x） | **AS** `dag_coordinator.py`：节点注册表（SQLite upsert + 心跳 TTL）、分布式 DAG 锁（CAS + TTL + token 校验）、Peer 发现（本地 SQLite + HTTP 轮询 `JACHIN_COORDINATOR_PEER_URLS`）、`find_idle_peer`、`auto_handoff_to_peer`（`dag_handoff.py`）、六 coordinator HTTP 端点、`on_startup` 心跳循环；`_handle_coordinator_*` handler；`_on_startup_dag_coordinator`；`JACHIN_COORDINATOR_ENABLE=1`；§〇/落地表/阶段树/本轮增量（x）同步。 |
| 2026-05-18（w） | **AP** `dag_guardrails.py` DAG 级跨 Node 预算控制（SQLite 持久化，四类上限检查 + HTTP 诊断端点，续跑前自动 Guardrails）；**AQ** `level3_healer.py` Level 3 Experience RAG 辅助诊断（`diagnose_failed_intent` + `run_level3_healing` + `auto-inject`，`IntentPersister.update_extra_meta`，`awareness_loop` anomaly 分支调用）；**AR** `dag_handoff.py` 跨进程 DAG 续跑转交（`DagHandoffPackage` export/import/list，三端点，`JACHIN_DAG_HANDOFF_DIR`）；§〇/落地表/阶段树/本轮增量（w）同步。 |
| 2026-05-18（v） | **AL** `wing_registry.py` 五 Wing 规范注册表 + `normalize_wing` 写入归一化（半衰期/重要性系数均从注册表读取）；**AM** `_evaluate_condition` 改 async，`_evaluate_condition_llm_fallback` LLM yes/no（`JACHIN_CONDITION_LLM_EVAL=1`）；**AN** `l3_node/guardrails.py` `GuardrailsChecker` 五维护栏 + `agent_core` ReAct 入口 hook（`JACHIN_GUARDRAILS_ENABLE=1`）；**AO** `dag_resume.py` `probe/apply_dag_resume` + `POST /api/v1/registry/dag-resume`；§〇/落地表/阶段树/本轮增量（v）同步。 |
| 2026-05-18（v） | **AL** `wing_registry.py` 五 Wing 规范注册表 + `normalize_wing` 写入归一化（半衰期/重要性系数均从注册表读取）；**AM** `_evaluate_condition` 改 async，`_evaluate_condition_llm_fallback` LLM yes/no（`JACHIN_CONDITION_LLM_EVAL=1`）；**AN** `l3_node/guardrails.py` `GuardrailsChecker` 五维护栏 + `agent_core` ReAct 入口 hook（`JACHIN_GUARDRAILS_ENABLE=1`）；**AO** `dag_resume.py` `probe/apply_dag_resume` + `POST /api/v1/registry/dag-resume`；§〇/落地表/阶段树/本轮增量（v）同步。 |
| 2026-05-18（u） | **AI** `memory_backend.py` Wing 重要性乘数（`_WING_IMPORTANCE` + `JACHIN_NEXUS_WING_IMPORTANCE_WEIGHT`）；**AJ** `awareness_loop._check_intents` 支持 `condition` 类意图（`_evaluate_condition`，`JACHIN_CONDITION_INTENT_ENABLE=1`）；**AK** `IntentPersister.autoreset_failed` + `JACHIN_INTENT_AUTORESET_HOURS` + 自愈飞书推送；§〇/§四/§五/落地表/阶段树同步。 |
| 2026-05-18（t） | **AG**：`multi_agent` 讨论轮次/子迭代（`nexus_config` + `JACHIN_DISCUSS_MAX_ROUNDS` / `JACHIN_DISCUSS_ITEM_MAX_ITER`）+ **`JACHIN_EXPERIENCE_RECORD_MULTI_AGENT`** → `save_multi_agent_episode` + `on_experience_learned`；**AH**：`classify_busy_followup` 多子句优先级；§〇/落地表/§1.4.5 P2/§2.6/§四 P2/阶段树同步。 |
| 2026-05-18（s） | **AF** `merge_parallel` Markdown 索引表 + `agent_core` 并行 delegate 统一合并；discuss Observation 前缀 `format_summary()`；修订表 **（q）** 全文恢复；§〇/落地表 **AF**、§1.4.5 P2、§2.5/§2.6、**飞书场景四** 同步。 |
| 2026-05-18（r） | **AE** `session_hot_user_inject.py`：HTTP 等锁前 `record_pending`；`agent_core` 每轮 LLM 前热并入 user 块；**R** `session_hot_user_pending`；飞书可选 `JACHIN_IM_SESSION_HOT_INJECT`；**Q** `run_id_exact`；§〇/§1.4/落地表 **P**/**Q**/**R**/**AE**、§3.3 P2、阶段树 Hook 行同步。 |
| 2026-05-18（q） | **AD** `autonomy/dashboard.py` + `GET /api/v1/autonomy/status`；`llm_budget.record_daily_llm_usage` + `get_today_token_usage`/`get_token_day_budget`；`llm_client._apply_usage_budget` 始终累加日用量；`get_background_queue_metrics`；`_run_sub_agent_hooked` 传入 `_parent_allowed_skills`；§〇/§5.3.3/§5.4 P2/落地表/阶段树同步。 |
| 2026-05-19（o） | **X**：`dispatcher` 在 `prior>0` 时摘录排队进线并入**下轮** `user_input`（`_im_append/consume_queue_rollup_prefix`；`JACHIN_IM_QUEUE_ROLLUP_DISABLE=1` 关闭）；§〇 **飞书场景四** 标 **✅/⏳**；落地表增 **X**、**B**/**P** 注 **X**；Global 行注明非全量队列；**W** 行表格文案补全。 |
| 2026-05-19（n） | 增 **§〇**（⏳ 诚实分期）；**W** `im-channel-pending` + `resource_tags` 登记（**D**/**R**）；**不**声称完成集群/discuss/Wing/DAG 回放等；§1.5 **P2**、阶段树、**Q** 族、hook 段同步。 |
| 2026-05-18（m） | **U** `GET …/external-scheduled-hints` + `read_external_scheduled_hints_dict()`；**V** `GET …/task-dag-active` + `load_task_dag_dict()`；与 **Q** 同源诊断鉴权；**H**/**M**/**§3.2.4**/**§3.3 P1** 与阶段树 **V** 同步。 |
| 2026-05-18（l） | **T**：`ws_server` 抢占前 `system_status`（`prior_turn_superseded`）+ 镜像广播；环境变量 `JACHIN_WS_SUPERSEDE_ACK`；修 `dispatcher.create_im_message_handler` 中 `cid` 未绑定导致的 inflight/第二条 ack 失效；§1.4 WebSocket 段落与表格、§1.4.5 P0、阶段树与落地表 **T** 同步。 |
| 2026-05-18（k） | **S**：`fb_report_scheduler` 退出 `finally` 清除 `external_scheduled_hints` 中 `fb_report_scheduler`；可选远地 L3 `DELETE`（`FB_SCHED_L3_REGISTRY_URL`/`JACHIN_L3_HTTP_URL` + Registry Token）；落地表加 **S**、**M** 行补生命周期；总表与阶段树同步。 |
| 2026-05-18（j） | **R**：`GET /api/v1/registry/runtime-snapshot` + `get_runtime_registry_snapshot_dict()`；HTTP 同会话 `lock_held` 探针；**Q** 与 **R** 共用 `JACHIN_REGISTRY_DIAG_TOKEN`（优先）/ `JACHIN_HOOK_EVENTS_READ_TOKEN`；CORS 增 `X-Jachin-Registry-Diag-Token`；§1.4 HTTP 表格与「仍缺」改写。 |
| 2026-05-18（i） | **O** 增补 `DELETE /api/v1/registry/external-sched-hint`；**Q** `GET …/hook-events-recent` + `JACHIN_HOOK_EVENTS_READ_TOKEN`；落地进度表增 **P/Q**；§1.4 标注 supplement 与 P2 分项；阶段树 Hook 行补 **Q**。 |
| 2026-05-18（c） | 落地 `task_runtime_registry`、`agent_roles`（YAML+loader）、子 Agent 禁止嵌套 `delegate`；`SUB_AGENT_*` 扩展 critic/executor/domain_expert；prompt 改为合并前后台摘要；更新 §1～§2 与路线图阶段勾选。 |
| 2026-05-18（b） | HTTP 会话锁、飞书第二条 ack、后台摘要注入 prompt 等（见落地进度 A–C）。 |
