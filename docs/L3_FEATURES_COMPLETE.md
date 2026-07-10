# Jachin L3 单体功能全量清单

**版本**：2026-05-18（y）
**性质**：L3 节点（`l3_node/`）全部已落地优化项，不含 L2 集群能力。
**条目总数**：44 项（A–X 早期项 + Y–Z 记忆/无人值守基础 + AA–AX 进阶能力）
**对应路线图**：`docs/AGI_OPTIMIZATION_ROADMAP.md`

---

## 一、并发与会话隔离（A–D, AT, AU）

### A — HTTP 同会话串行锁
**文件**：`l3_node/http_server.py`
`chat_id` / `session_id` 非空时用异步锁 `_http_agent_session_lock` 互斥 `run_agent`，确保同一会话下的 HTTP 请求严格串行，避免上下文污染。

---

### B — 飞书第二条进线 ack + 可打断
**文件**：`l3_node/im_channels/dispatcher.py`, `im_second_instruction.py`, `foreground_run_registry.py`
主任务运行中收到第二条消息时，立即回复"正在处理"安抚文案；分流到 interrupt / parallel / supplement / queue 四种路径；`foreground_run_registry` 支持取消当前 run。

---

### C — Prompt 注入后台负载感知
**文件**：`l3_node/task_runtime_registry.py`
`format_combined_runtime_prompt_suffix()` 将当前运行中的前台任务、进程内定时任务摘要注入 system prompt 后缀块，让 LLM 在回答时感知系统当前的并发状态。

---

### D — 进程内 GlobalTaskRegistry（轻量版）
**文件**：`l3_node/task_runtime_registry.py`
内存字典登记顶层 `run_agent` 的 `run_id`、通道、`session_key`、可选 `resource_tags`；线程安全（`threading.Lock`）；提供 `register_foreground_task` / `unregister_foreground_task`。

---

### AT — GlobalTaskRegistry 跨进程 SSOT + resource_tags 抢占调度
**文件**：`l3_node/global_task_registry.py`（新建）
**开关**：`JACHIN_GLOBAL_REGISTRY_ENABLE=1`，抢占：`JACHIN_GLOBAL_REGISTRY_PREEMPT=1`

- SQLite WAL 模式跨进程双写，与进程内注册表并行（向后兼容）
- 任务优先级枚举：P1（用户前台）> P2（定时强制）> P3（后台批量）> P4（低优先级）
- `check_and_preempt`：新 P1 任务检测持有相同 `resource_tags` 的低优先级任务，标记 `preempted` 并调用 `request_cancel_run` 发送取消信号
- 僵尸任务 TTL 自动清除（`JACHIN_GLOBAL_REGISTRY_TTL`，默认 300s）
- `get_global_registry_summary()` 供 HTTP 诊断端点读取

---

### AU — SessionInstructionQueue 全量队列化（真·双轨并行）
**文件**：`l3_node/session_instruction_queue.py`（新建）
**开关**：`JACHIN_SIQ_ENABLE=1`，模式：`JACHIN_SIQ_MODE=SERIAL|PARALLEL`

- 每个 `session_key` 独立一个 `SIQSession`（asyncio.Queue + worker 协程）
- **SERIAL 模式**（默认）：有序串行，行为与原有一致
- **PARALLEL 模式**：新指令直接 `asyncio.create_task` 并发执行，超并发上限（`JACHIN_SIQ_MAX_PARALLEL`，默认 2）时降级串行
- 指令执行超时保护（`JACHIN_SIQ_INSTRUCTION_TIMEOUT_SEC`，默认 300s）
- 弱引用 `WeakValueDictionary` 管理会话，空闲后自动 GC 回收

---

## 二、飞书 IM 交互增强（B, P, T, W, X, AE, AH, AX）

### T — WebSocket 抢占上一轮流式前即时提示
**文件**：`l3_node/ws_server.py`
**开关**：`JACHIN_WS_SUPERSEDE_ACK=0` 关闭（默认开）
新 WS 进线时，若上一轮 `active_turn_task` 仍在跑，先推送 `step_type: system_status`（`kind: prior_turn_superseded`）即时通知客户端，再 cancel 旧任务并起新轮。

---

### W — 飞书 IM 进线深度只读 HTTP
**文件**：`l3_node/http_server.py`
`GET /api/v1/registry/im-channel-pending`：返回当前飞书队列中待处理的会话信息（`session_key / chat_id / intent_preview / since_ts`），只读观测，无抢占。

---

### X — 飞书 IM 排队摘录 → 本轮 user_input 合并
**文件**：`l3_node/im_channels/dispatcher.py`
**开关**：`JACHIN_IM_QUEUE_ROLLUP_DISABLE=1` 关闭
第二条消息等锁期间写入 `_im_queue_rollup`；主任务获锁后合并到本轮 `user_input` 前缀，避免内容丢失。去重处理，与同句则省略。

---

### P — 飞书「补充意图」轻量分流
**文件**：`l3_node/im_second_instruction.py`, `l3_node/agent_core.py`
规则识别补充/更正类第二条（`_SUPPLEMENT_HINTS` 关键词），回复 supplement ack；`agent_core` 中注入 `【飞书·排队补充意图】` 提示让 LLM 主动合并补充内容。

---

### AE — 同会话中段热并入（HTTP + 可选 IM）
**文件**：`l3_node/session_hot_user_inject.py`（新建）
**开关**：`JACHIN_SESSION_HOT_USER_INJECT_DISABLE=1` 全关；飞书：`JACHIN_IM_SESSION_HOT_INJECT=1`

- HTTP 请求在等服务端锁时，将 `user_input` 写入热注入缓冲
- `agent_core` 每轮 LLM 前 `drain` 并入 user 消息块
- `runtime-snapshot` 可返回 `session_hot_user_pending` 字段

---

### AH — 飞书第二条多子句意图优先级仲裁（规则）
**文件**：`l3_node/im_second_instruction.py`
按换行、中英文分号拆分多段文本，逐段分类后取 **interrupt > parallel > supplement > queue** 最高优先级作为最终意图（轻量冲突仲裁，无 LLM 调用）。

---

### AX — 飞书第二条 LLM 冲突仲裁
**文件**：`l3_node/im_second_instruction.py`, `l3_node/im_channels/dispatcher.py`
**开关**：`JACHIN_IM_LLM_CONFLICT_RESOLVE=1`，模型：`JACHIN_IM_LLM_CONFLICT_MODEL`

- `classify_busy_followup_llm(new_text, current_task_summary)`：规则 `interrupt/parallel` 高置信直通（不调 LLM），`queue` 类走 LLM 二次裁决
- `_ARBITER_SYSTEM` 四分类 prompt（interrupt / parallel / supplement / queue），temperature=0，max_tokens=10
- 超时（`JACHIN_IM_LLM_CONFLICT_TIMEOUT`，默认 3s）或失败自动回退规则结果
- `dispatcher` 接入同步包装 `analyze_second_im_intent_llm_sync`，并注入当前任务摘要

---

## 三、多 Agent 协作（E, F, AA, AB, AF, AG）

### E — 角色池 YAML + delegate 注入
**文件**：`skills_repo/agent_roles/role_pool.yaml`, `l3_node/agent_roles_loader.py`
预定义 `critic / executor / domain_expert` 等角色；`kernel prompt composer` 后缀注入 `delegate_hint`，让主 Agent 知道如何组建子 Agent 团队。

---

### F — 子 Agent 禁嵌套 delegate
**文件**：`l3_node/agent_core.py`
子 Agent（`max_delegate_depth > 0`）尝试调用 `delegate_sub_agent` 时，直接返回 JSON 错误 Verification evidence，防止无限嵌套导致栈爆炸。

---

### AA — multi_agent discuss 模式 + StructuredResultMerger
**文件**：`l3_node/primitives/multi_agent/discussion.py`, `result_merger.py`

- `run_discussion(DiscussionConfig, engine)`：Round 1 并行（planner + critic），Round N 串行修订 + 二次审查，critic 无新质疑或达 `max_rounds` 终止，可选 summarizer 输出最终共识
- `StructuredResultMerger.merge_discussion`：讨论结果结构化合并
- `agent_core` delegate 分支新增 `mode: discuss` 路由，Verification evidence 前缀 `format_summary()` 摘要行

---

### AB — 动态角色创建安全沙箱
**文件**：`l3_node/agent_core.py`（`_sanitize_inline_role`）

- `sub_tasks[i]["role"]` 为 dict 时激活 inline role 创建
- role_id 仅允许字母数字下划线（防 prompt 注入）
- system_prefix 移除高危关键词（`[REDACTED]`）
- `allowed_tools` 与父级工具集取交集，强制剔除 delegate（防递归）

---

### AF — 并行 delegate 结构化汇总（Markdown 索引表）
**文件**：`l3_node/primitives/multi_agent/result_merger.py`, `l3_node/agent_core.py`
`merge_parallel`：生成 Markdown 索引表（子任务编号 + 标题 + 状态 + 耗时）+ 各子任务详块；并行 `delegate`（`asyncio.gather`）统一经 merge_parallel 汇总输出。

---

### AG — multi_agent 超参配置 + Experience 多 Agent 摘要落盘
**文件**：`l3_node/primitives/multi_agent/`, `l3_node/experience_memory.py`

- `nexus_config.json` → `multi_agent.max_discussion_rounds`（默认 3）/ `discussion_item_max_iterations`（默认 3）
- 环境变量 `JACHIN_DISCUSS_MAX_ROUNDS` / `JACHIN_DISCUSS_ITEM_MAX_ITER` 优先级更高
- `JACHIN_EXPERIENCE_RECORD_MULTI_AGENT=1`：讨论/并行子任务执行后 `save_multi_agent_episode` 写入 `multi_agent:discuss` / `multi_agent:parallel_delegate` JSONL，并触发 `on_experience_learned` Hook

---

## 四、任务拆解与 DAG 体系（G, H, K, Q, V, AO, AP, AR, AS, AV）

### G — 生命周期 Hook 体系（P0 触发点）
**文件**：`l3_node/engine/hooks_pipeline.py`
扩充事件：`on_task_node_start` / `on_task_node_done` / `on_retry` / `on_execution_brief` / `on_memory_commit` / `on_experience_learned` / `on_task_decompose` 等；无注册时无运行时开销（空检查直通）。

---

### H — TaskDAG 轻量 prompt 注入
**文件**：`l3_node/task_engine/task_dag.py`
`active.json` → `format_active_task_dag_prompt_suffix()`：将当前结构化任务图的节点状态（pending/running/done/failed）注入 system prompt 后缀块，让 LLM 感知任务进度。

---

### K — Hook 事件 SQLite 持久化（可选）
**文件**：`l3_node/engine/persistent_hook_log.py`
**开关**：`JACHIN_PERSIST_HOOKS=1`
`run_agent` 入口注册持久化 Hook，所有生命周期事件追加写入 `workspace/hook_events.sqlite3`（WAL 模式），是 DAG 续跑引擎的数据来源。

---

### Q — Hook 事件只读 HTTP
**文件**：`l3_node/http_server.py`
`GET /api/v1/registry/hook-events-recent`：支持 `run_id` 过滤，`run_id_exact=1` 精确匹配单次 run 完整事件链（轻量回放探针）；与 R/U/V/W 共用诊断双 Token 鉴权。

---

### V — TaskDAG active.json 只读 HTTP
**文件**：`l3_node/http_server.py`
`GET /api/v1/registry/task-dag-active`：`load_task_dag_dict()` 拉取当前 active.json 全量内容，无文件则返回 `dag: null`。

---

### AV — TaskDAG Planner LLM 自动拆解
**文件**：`l3_node/task_engine/dag_planner.py`（新建）
**开关**：`JACHIN_DAG_AUTO_PLAN=1`，模型：`JACHIN_DAG_PLAN_MODEL`

- `should_auto_plan(intent)`：启发式判断（字符数 > `JACHIN_DAG_AUTO_PLAN_MIN_CHARS` 或包含多步关键词正则），active.json 已存在时默认不覆盖（`JACHIN_DAG_AUTO_PLAN_OVERWRITE=0`）
- `plan_task_dag(intent, force=False)`：调 LLM 生成结构化 JSON（title + nodes），解析后写回 `active.json`
- 合并写入时保留已完成节点的 done/running 状态（不覆盖已执行进度）
- `_max_nodes`（默认 16）防止节点数爆炸
- `plan_task_dag_sync()` 同步包装，供非 async 上下文调用

---

### AO — DAG 轻量续跑引擎
**文件**：`l3_node/task_engine/dag_resume.py`（新建）
**前提**：`JACHIN_PERSIST_HOOKS=1`

- `probe_dag_resume(run_id)`：从 `hook_events.sqlite3` 查已完成节点（`HOOK_ON_TASK_NODE_DONE`），与 `active.json` 对比找待续跑节点，生成 `resume_intent` 自然语言续跑提示
- `apply_dag_resume(run_id)`：将待续跑节点重置为 `pending` 并写回 active.json
- `POST /api/v1/registry/dag-resume`：`dry_run=true` 只探测，`false` 则应用；若 DAG 级 Guardrails 触发则阻止续跑

---

### AP — DAG 级 Guardrails（跨 Node 预算控制）
**文件**：`l3_node/task_engine/dag_guardrails.py`（新建）
**开关**：`JACHIN_DAG_GUARDRAILS_ENABLE=1`

- 以 `dag_id` 为粒度追踪整个 DAG 的总迭代次数 / 总工具调用 / 总 Token / 已执行节点数
- 持久化到 `workspace/dag_guardrails.sqlite3`（跨进程可见，WAL 模式）
- `DagGuardrailsChecker.check_dag_budget()`：违规时产出 `dag_brief()` ExecutionBrief 并阻止续跑
- 四类上限：`JACHIN_DAG_GR_MAX_TOTAL_ITERATIONS` / `_TOOL_CALLS` / `_TOKENS` / `_NODES`
- `GET /api/v1/registry/dag-guardrails?dag_id=` 可查单 DAG 预算状态

---

### AR — 跨进程 DAG 续跑转交（HTTP Handoff）
**文件**：`l3_node/task_engine/dag_handoff.py`（新建）
**配置**：`JACHIN_DAG_HANDOFF_DIR`（共享目录）

- `DagHandoffPackage`：含 schema_version / package_id / source_run_id / completed_node_ids / pending_nodes / resume_intent / context_hint
- `export_dag_handoff(run_id)`：从本地 hook_events + active.json 构建转交包，可落文件到共享目录
- `import_dag_handoff(package_data)`：校验 schema 后写入本地 active.json，返回 `HandoffImportResult`（含 resume_intent）
- HTTP：`POST /dag-handoff/export` / `POST /dag-handoff/import` / `GET /dag-handoff/list`

---

### AS — DAG Coordinator（节点注册 + 分布式锁 + Peer 发现）
**文件**：`l3_node/task_engine/dag_coordinator.py`（新建）
**开关**：`JACHIN_COORDINATOR_ENABLE=1`

- **节点注册表**：`register_node` / `heartbeat` / `list_alive_nodes`（SQLite `dag_coordinator.sqlite3`，心跳 TTL `JACHIN_COORDINATOR_NODE_TTL`，默认 90s）
- **分布式 DAG 锁**（SQLite CAS + TTL）：`claim_dag`（乐观锁，过期自动抢占）/ `release_dag`（token 校验）/ `refresh_dag_lock`（续约）/ `get_dag_owner`
- **Peer 发现**：本地 SQLite 同机节点 + `discover_http_peers`（轮询 `JACHIN_COORDINATOR_PEER_URLS`）+ `find_idle_peer`（load_score < 0.5）
- `auto_handoff_to_peer`：export → find_idle_peer → HTTP POST /import → optional release_lock（一键转交）
- HTTP 端点：`POST /dag-handoff/auto-transfer` + 六个 coordinator 端点（`/info` / `/peers` / `/register` / `/dag-claim` POST/DELETE / `/dag-locks`）
- `on_startup` 启动心跳后台循环，`ensure_coordinator_started()` 幂等调用

---

## 五、可观测性与运维诊断（L, M, O, R, S, U, AD）

### L — 进程内定时任务 → prompt 感知
**文件**：`l3_node/task_runtime_registry.py`
`register_scheduled_job_hint` / `unregister_scheduled_job_hint`：APScheduler 注册成功时写入摘要；合并进 `format_combined_runtime_prompt_suffix()`，让 LLM 感知当前有哪些定时任务在跑。

---

### M — 外部定时进程心跳 → prompt
**文件**：`l3_node/task_runtime_registry.py`（`external_scheduled_hints.json`）
外部独立进程（如 `fb_report_scheduler.py`）调用 `merge_external_scheduled_process_hint()` 写入 JSON 心跳文件；读侧通过 `read_external_scheduled_hints_dict()` 合并进 prompt。

---

### O — HTTP 外部定时任务登记接口
**文件**：`l3_node/http_server.py`
**鉴权**：`JACHIN_REGISTRY_EXTERNAL_SCHED_TOKEN` + `X-Jachin-Registry-Token`
`POST /api/v1/registry/external-sched-hint`（登记心跳）；`DELETE`（撤销登记）；未配置 Token → 503。

---

### R — 运行时只读快照 · HTTP 锁探针
**文件**：`l3_node/http_server.py`
`GET /api/v1/registry/runtime-snapshot?session_key=`：返回当前所有前台任务（含 resource_tags）、HTTP 锁状态、热注入 pending；与 Q/U/V/W 共用诊断双 Token 鉴权。

---

### S — FB 调度守护退出清心跳
**文件**：`scripts/fb_report_scheduler.py`
`finally` 块调用 `clear_fb_external_sched_hint()`：本地清除外部心跳条目；可选通过 HTTP DELETE 通知远端 L3 节点撤销登记。

---

### U — 外部定时心跳文件只读 HTTP
**文件**：`l3_node/http_server.py`
`GET /api/v1/registry/external-scheduled-hints`：读取 `external_scheduled_hints.json`，返回 `processes` / `hints_prompt_read_disabled` / `file_present`。

---

### AD — 可观测性面板 HTTP
**文件**：`l3_node/autonomy/dashboard.py`（新建）
**端点**：`GET /api/v1/autonomy/status`

返回字段：`uptime_hours` / `active_intents` / `running_tasks` / `queued_tasks` / `background_p3_running` / `today_token_used` / `today_token_budget` / `anomalies` / `next_scheduled_task` / `disk_free_gb`；`llm_client` 每次响应后通过 `record_daily_llm_usage` 自动累计 Token 日消耗。

---

## 六、记忆架构增强（I, J, Y, AI, AL, AW）

### I — Experience RAG 可选向量重排
**文件**：`l3_node/experience_memory.py`
**开关**：`JACHIN_EXPERIENCE_USE_EMBED=1`，预过滤：`JACHIN_EXPERIENCE_EMBED_PREFILTER=1`
与 Memory Nexus 共用 FastEmbed 模型；向量相似度重排历史执行经验；失败时降级字符串相似度匹配。

---

### J — Nexus 回合末写入 · 闲聊与低价值过滤
**文件**：`l3_node/agent_core.py`（`schedule_nexus_turn_commit_async`）
**开关**：`JACHIN_NEXUS_TURN_COMMIT_SKIP_LOW_VALUE=1`（默认开）
跳过纯寒暄、`[ExecutionBrief]`、`[未产出回复]`、`【需要补充信息】`、短 `[System]`、极短套话等低价值内容，避免噪音污染 Memory Nexus。

---

### Y — 记忆遗忘曲线时间权重
**文件**：`l3_client/local_mcps/jachin_memory_nexus/memory_backend.py`
**开关**：`JACHIN_NEXUS_TIME_DECAY_WEIGHT`（默认 0.2，0=纯语义）
Ebbinghaus 遗忘曲线：`decay = 0.5^(age_days / half_life_days)`；`final_score = sem*(1-w) + decay*w`；Wing 各有不同半衰期（Procedures=180d / Knowledge=90d / 其余=30d）。

---

### AI — Wing 重要性分级乘数
**文件**：`l3_client/local_mcps/jachin_memory_nexus/memory_backend.py`
**开关**：`JACHIN_NEXUS_WING_IMPORTANCE_WEIGHT`（默认 0.15，0=关闭）
对 `deep_search` 候选结果按 Wing 类型施加重要性乘数（Procedures 1.30 / Knowledge 1.20 / Core 1.25 / Episodes 1.00），时间衰减融合后再乘以乘数，结果 clamp 到 [0,1]。

---

### AL — Wing 全量重映射（规范注册表）
**文件**：`l3_client/local_mcps/jachin_memory_nexus/wing_registry.py`（新建）
五 Wing 规范定义：Episodes(30d/×1.00) / Knowledge(90d/×1.20) / Procedures(180d/×1.30) / Core(180d/×1.25) / Inbox(7d/×0.80)；`normalize_wing()` 别名归一化；`commit_drawer` / `upsert_drawer` 写入时自动归一；`JACHIN_WING_IMPORTANCE_OVERRIDE` 运行时 JSON 覆盖单 Wing 系数。

---

### AW — 记忆跨 Agent 共享 + 向量主导检索
**文件**：`l3_client/local_mcps/jachin_memory_nexus/memory_backend.py`
**共享**：`JACHIN_NEXUS_SHARED_PATH=/path/to/shared.sqlite3`
**向量主导**：`JACHIN_NEXUS_VECTOR_LEAD=1`

- 设置共享路径后，多进程/多 Agent 读写同一 SQLite（WAL 模式并发安全），实现跨 Agent 记忆共享
- 向量主导模式：将时间衰减权重和 Wing 重要性权重各缩至 0.3 倍，余弦相似度主导，解决跨 Agent 时间戳差异导致的检索偏差
- 未设置共享路径时退回默认本地路径，原有行为不变

---

## 七、无人值守自主能力（Z, AC, AJ, AK, AM, AN, AQ）

### Z — PersistedIntent 意图持久化
**文件**：`l3_node/autonomy/intent_persister.py`（新建）
SQLite `persisted_intents.sqlite3`；`IntentPersister` 完整 CRUD（save / create / list / get / set_enabled / delete / record_execution）；`IntentRecovery.restore_to_scheduler()` 进程重启后自动恢复 cron/interval 意图；HTTP CRUD：`GET/POST/PATCH/DELETE /api/v1/autonomy/intents/{id}`。

---

### AC — AutonomousAwarenessLoop + ProactiveReporter
**文件**：`l3_node/autonomy/awareness_loop.py`, `proactive_reporter.py`（新建）
**开关**：`JACHIN_AWARENESS_LOOP_DISABLE=1` 关闭，间隔：`JACHIN_AWARENESS_SCAN_INTERVAL`（默认 60s）

每轮扫描四项：
1. interval 类意图到期 → 自动 fire
2. 磁盘 / Token 资源告警（`JACHIN_TOKEN_DAY_BUDGET`）
3. 连续失败异常检测
4. 日终 23:55 → `ProactiveReporter`（生成今日统计 + Token + 经验摘要 + 明日预计，飞书推送）

---

### AJ — 条件触发意图轻量评估器
**文件**：`l3_node/autonomy/awareness_loop.py`
**开关**：`JACHIN_CONDITION_INTENT_ENABLE=1`
支持四类内置条件：`disk_free_gb <op> N` / `token_used <op> N` / `token_used_pct <op> N` / `consecutive_failures:intent_id <op> N`；满足则 fire_intent，解析失败安全返回 False。

---

### AK — 失败意图自动重置（Level 2 轻量自愈）
**文件**：`l3_node/autonomy/intent_persister.py`, `awareness_loop.py`
**开关**：`JACHIN_INTENT_AUTORESET_HOURS=N`（默认 0 关闭）
失败意图超过 N 小时后，AwarenessLoop 扫描时自动调用 `autoreset_failed`（重置 `status=active, consecutive_failures=0`）并推送「[自愈通知]」至飞书。

---

### AM — LLM 驱动条件评估（fallback 路径）
**文件**：`l3_node/autonomy/awareness_loop.py`
**开关**：`JACHIN_CONDITION_LLM_EVAL=1`，模型：`JACHIN_CONDITION_LLM_MODEL`
内置规则无法解析条件时，调 `_evaluate_condition_llm_fallback`：构建「system state + condition → yes/no」prompt，单次调用（temperature=0，max_tokens=8），失败安全返回 False。

---

### AN — WorkOrder 内护栏 GuardrailsChecker
**文件**：`l3_node/guardrails.py`（新建）, `l3_node/agent_core.py`
**开关**：`JACHIN_GUARDRAILS_ENABLE=1`

五维检查：
| 维度 | 环境变量 | 默认值 | 触发动作 |
|------|----------|--------|---------|
| 最大迭代次数 | `JACHIN_GR_MAX_ITERATIONS` | 20 | truncate |
| 最大工具调用次数 | `JACHIN_GR_MAX_TOOL_CALLS` | 40 | truncate |
| 最大 Token 消耗 | `JACHIN_GR_MAX_TOKENS` | 200k | truncate |
| 禁用工具列表 | `JACHIN_GR_FORBIDDEN_TOOLS` | 空 | abort |
| 相同参数重复调用 | `JACHIN_GR_REPEAT_TOOL_ACTION_MAX` | 3 | warn→abort |

`agent_core` 每轮迭代开始前 `check_all_pre_iteration`，工具调用前 `check_all_pre_tool`。

---

### AQ — Level 3 自愈（Experience RAG 辅助诊断）
**文件**：`l3_node/autonomy/level3_healer.py`（新建）
**开关**：`JACHIN_LEVEL3_HEALER_ENABLE=1`，自动应用：`JACHIN_LEVEL3_AUTO_APPLY=1`

- `diagnose_failed_intent`：连续失败 ≥ `JACHIN_LEVEL3_FAILURE_THRESHOLD`（默认 3）时，从 Experience RAG 检索相似历史成功案例（top_k `JACHIN_LEVEL3_RAG_TOP_K`），构建 `HealingDiagnosis`（建议工具列表 + 修复文案）
- `run_level3_healing`：异步执行诊断并推送飞书 rich 报告
- `JACHIN_LEVEL3_AUTO_APPLY=1` 时将首条成功路径注入意图 `extra_meta`（通过 `IntentPersister.update_extra_meta`）
- `awareness_loop._execute_action` 中 `anomaly` 分支自动调用

---

## 八、环境变量速查表

| 环境变量 | 默认 | 功能 |
|----------|------|------|
| `JACHIN_GLOBAL_REGISTRY_ENABLE` | 0 | 跨进程 GlobalTaskRegistry（AT） |
| `JACHIN_GLOBAL_REGISTRY_PREEMPT` | 0 | resource_tags 抢占调度（AT） |
| `JACHIN_GLOBAL_REGISTRY_TTL` | 300 | 僵尸任务超时清除秒（AT） |
| `JACHIN_SIQ_ENABLE` | 0 | SessionInstructionQueue（AU） |
| `JACHIN_SIQ_MODE` | SERIAL | SERIAL/PARALLEL 模式（AU） |
| `JACHIN_SIQ_MAX_PARALLEL` | 2 | 并行最大并发数（AU） |
| `JACHIN_SIQ_INSTRUCTION_TIMEOUT_SEC` | 300 | 单条指令执行超时（AU） |
| `JACHIN_DAG_AUTO_PLAN` | 0 | DAG Planner 自动触发（AV） |
| `JACHIN_DAG_AUTO_PLAN_MIN_CHARS` | 60 | 自动触发字符数阈值（AV） |
| `JACHIN_DAG_AUTO_PLAN_OVERWRITE` | 0 | 覆盖已有 active.json（AV） |
| `JACHIN_DAG_PLAN_MODEL` | LLM_MODEL | 规划使用的模型（AV） |
| `JACHIN_NEXUS_SHARED_PATH` | 空 | 共享 SQLite 路径（AW） |
| `JACHIN_NEXUS_VECTOR_LEAD` | 0 | 向量主导检索（AW） |
| `JACHIN_IM_LLM_CONFLICT_RESOLVE` | 0 | 飞书第二条 LLM 仲裁（AX） |
| `JACHIN_IM_LLM_CONFLICT_MODEL` | LLM_MODEL | 仲裁模型（AX） |
| `JACHIN_IM_LLM_CONFLICT_TIMEOUT` | 3 | LLM 仲裁超时秒（AX） |
| `JACHIN_PERSIST_HOOKS` | 0 | Hook 事件 SQLite 持久化（K） |
| `JACHIN_GUARDRAILS_ENABLE` | 0 | WorkOrder 内护栏（AN） |
| `JACHIN_GR_MAX_ITERATIONS` | 20 | 最大迭代次数（AN） |
| `JACHIN_GR_MAX_TOOL_CALLS` | 40 | 最大工具调用次数（AN） |
| `JACHIN_GR_MAX_TOKENS` | 200000 | 最大 Token 消耗（AN） |
| `JACHIN_GR_FORBIDDEN_TOOLS` | 空 | 禁用工具列表（AN） |
| `JACHIN_DAG_GUARDRAILS_ENABLE` | 0 | DAG 级 Guardrails（AP） |
| `JACHIN_COORDINATOR_ENABLE` | 0 | DAG Coordinator 心跳（AS） |
| `JACHIN_COORDINATOR_NODE_TTL` | 90 | 节点心跳 TTL 秒（AS） |
| `JACHIN_COORDINATOR_LOCK_TTL` | 120 | DAG 锁 TTL 秒（AS） |
| `JACHIN_COORDINATOR_PEER_URLS` | 空 | 跨机 Peer URL 列表（AS） |
| `JACHIN_DAG_HANDOFF_DIR` | 空 | Handoff 包共享目录（AR） |
| `JACHIN_AWARENESS_LOOP_DISABLE` | 0 | 关闭 AwarenessLoop（AC） |
| `JACHIN_AWARENESS_SCAN_INTERVAL` | 60 | 扫描间隔秒（AC） |
| `JACHIN_TOKEN_DAY_BUDGET` | 空 | Token 日预算（AC/AD） |
| `JACHIN_CONDITION_INTENT_ENABLE` | 0 | 条件类意图评估器（AJ） |
| `JACHIN_CONDITION_LLM_EVAL` | 0 | LLM 条件评估 fallback（AM） |
| `JACHIN_INTENT_AUTORESET_HOURS` | 0 | 失败意图自动重置小时（AK） |
| `JACHIN_LEVEL3_HEALER_ENABLE` | 0 | Level 3 自愈（AQ） |
| `JACHIN_LEVEL3_FAILURE_THRESHOLD` | 3 | 触发自愈的连续失败次数（AQ） |
| `JACHIN_LEVEL3_AUTO_APPLY` | 0 | 自动注入修复路径（AQ） |
| `JACHIN_NEXUS_TIME_DECAY_WEIGHT` | 0.2 | 遗忘曲线权重（Y） |
| `JACHIN_NEXUS_WING_IMPORTANCE_WEIGHT` | 0.15 | Wing 重要性权重（AI） |
| `JACHIN_WING_IMPORTANCE_OVERRIDE` | 空 | Wing 系数 JSON 覆盖（AL） |
| `JACHIN_EXPERIENCE_USE_EMBED` | 0 | Experience RAG 向量重排（I） |
| `JACHIN_DISCUSS_MAX_ROUNDS` | 3 | discuss 最大轮次（AG） |
| `JACHIN_DISCUSS_ITEM_MAX_ITER` | 3 | discuss 子任务最大迭代（AG） |
| `JACHIN_EXPERIENCE_RECORD_MULTI_AGENT` | 0 | 多 Agent 经验落盘（AG） |
| `JACHIN_WS_SUPERSEDE_ACK` | 1 | WS 抢占即时提示（T） |
| `JACHIN_IM_QUEUE_ROLLUP_DISABLE` | 0 | 关闭排队摘录合并（X） |
| `JACHIN_SESSION_HOT_USER_INJECT_DISABLE` | 0 | 关闭热并入（AE） |
| `JACHIN_IM_SESSION_HOT_INJECT` | 0 | 飞书热并入开关（AE） |
| `JACHIN_NEXUS_TURN_COMMIT_SKIP_LOW_VALUE` | 1 | 低价值回复跳过写入（J） |
| `JACHIN_EXTERNAL_SCHED_HINTS_DISABLE` | 0 | 关闭外部心跳 prompt 注入（M） |
| `JACHIN_REGISTRY_EXTERNAL_SCHED_TOKEN` | 空 | 外部登记接口鉴权 Token（O） |

---

## 九、功能总览（按类别）

```
并发 & 会话隔离（6项）
  A  · HTTP 同会话串行锁
  B  · 飞书第二条 ack + 可打断
  C  · Prompt 注入后台负载感知
  D  · 进程内 GlobalTaskRegistry
  AT · 跨进程 GlobalTaskRegistry + resource_tags 抢占
  AU · SessionInstructionQueue 真·双轨并行

飞书 IM 交互增强（8项）
  T  · WS 抢占即时提示
  W  · IM 进线深度只读 HTTP
  X  · 排队摘录 → user_input 合并
  P  · 补充意图轻量分流
  AE · 同会话中段热并入
  AH · 多子句意图优先级仲裁（规则）
  AX · 第二条 LLM 冲突仲裁
  (B)· 飞书 ack + 分流（共享 B）

多 Agent 协作（6项）
  E  · 角色池 YAML + delegate 注入
  F  · 子 Agent 禁嵌套 delegate
  AA · discuss 模式 + StructuredResultMerger
  AB · 动态角色安全沙箱
  AF · 并行 delegate Markdown 汇总
  AG · multi_agent 超参 + Experience 落盘

任务拆解 & DAG（10项）
  G  · 生命周期 Hook 体系
  H  · TaskDAG prompt 注入
  K  · Hook 事件 SQLite 持久化
  Q  · Hook 事件只读 HTTP
  V  · active.json 只读 HTTP
  AV · DAG Planner LLM 自动拆解
  AO · DAG 续跑引擎
  AP · DAG 级 Guardrails
  AR · 跨进程 DAG Handoff
  AS · DAG Coordinator（SQLite 分布式锁）

可观测性 & 运维（7项）
  L  · 进程内定时任务 → prompt
  M  · 外部心跳 → prompt
  O  · 外部定时任务 HTTP 登记
  R  · 运行时快照 HTTP 端点
  S  · FB 调度守护退出清心跳
  U  · 外部心跳只读 HTTP
  AD · 可观测性面板 HTTP

记忆架构增强（6项）
  I  · Experience RAG 向量重排
  J  · 闲聊 & 低价值回复过滤
  Y  · 遗忘曲线时间权重
  AI · Wing 重要性分级乘数
  AL · Wing 规范注册表
  AW · 跨 Agent 共享 + 向量主导检索

无人值守 & 自主能力（7项）
  Z  · PersistedIntent 意图持久化
  AC · AwarenessLoop + ProactiveReporter
  AJ · 条件触发意图评估器
  AK · 失败意图自动重置（Level 2 自愈）
  AM · LLM 驱动条件评估 fallback
  AN · WorkOrder 内护栏 GuardrailsChecker
  AQ · Level 3 自愈（Experience RAG 诊断）

合计：50 项
```

---

*最后更新：2026-05-18（y）*
