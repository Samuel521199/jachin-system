# Jachin 智能化升级总览

**版本**: 1.9  
**日期**: 2026-03-16  
**基准**: [JACHIN_VS_OPENCLAW_INTELLIGENCE_ANALYSIS.md](./JACHIN_VS_OPENCLAW_INTELLIGENCE_ANALYSIS.md)（与本文同步；§2.2 / §八 / §九 + **长期三层编排** 见 [ORCHESTRATION_ARCHITECTURE.md](./ORCHESTRATION_ARCHITECTURE.md)）  
**产品基线**: **v0.8.50**（Milestone **DeepBrain**；含阶段 0 **Vanguard** 能力）  
**下一阶段编排**: 本文 **§五**（含 **Jachin OS 智能化演进路线图** 与对标 §9.10，**以本文为执行状态单一事实来源**）

---

## 一、P0 已实现（2026-03-17）

### 1.1 L3 本地记忆持久化

| 组件 | 路径 | 说明 |
|------|------|------|
| **存储** | `~/.jachin/memory/l3_local.json` | 本地核心记忆，最多 200 条 |
| **注入** | System Prompt | `get_local_memory_for_prompt(limit=12)` 断网/无 L2 时仍可用 |
| **合并** | recall_memory 成功后 | L2 检索结果自动 `merge_from_l2` |

**代码**: `l3_node/local_memory.py`

**补充（现行 v1.9，合并 v1.7～v1.9）**：**`core:local_memory_search`**（半衰 + **MMR**）；L2 **`memory_scoring`** + **[MEMORY_SCORING.md](./MEMORY_SCORING.md)**；**`GET /api/v2/memory/search?explain=true`**；Compaction **`silent_anchor_file_round`**；**`POST /api/v2/memory/feedback`**；**`apply_patch.python_ast_validate`**。§4.3 **[IMPLICIT_SIGNALS.md](./IMPLICIT_SIGNALS.md)** + **`POST /api/v2/intelligence/implicit-signal`** + 文本/向量复述 + **`implicit_turn_attribution`**（全端默认）+ WS **`implicit_signals`**；`intelligence_e`（**repeat_followup_delta**、**embedding_*_delta**）；叙事 **[MEMORY_WRITE_AND_SCORE_NARRATIVE.md](./MEMORY_WRITE_AND_SCORE_NARRATIVE.md)**。

### 1.2 Pre-reset / Pre-new 记忆刷新

| 触发 | 实现 |
|------|------|
| `POST /api/v3/llm/context/reset` | 重置前调用 `run_pre_reset_memory_flush()` |
| **逻辑** | 从 short_term 提取近期对话，执行 LLM 记忆刷新，写入 core_memory |

**代码**: `core/compaction_hook.py` → `run_pre_reset_memory_flush()`，`core/api/console.py`

### 1.3 梦境阈值触发

| 配置 | 默认 | 说明 |
|------|------|------|
| `dream_weaver.fragment_threshold` | 50 | 未整合碎片达此数即触发 Dream Weaver，不等 3am/空闲 |
| `dream_weaver.consolidation_threshold` | 10 | 至少 N 条才融合（DreamWeaver 内部）|

**配置**: `~/.jachin/nexus_config.json`:

```json
{
  "dream_weaver": {
    "fragment_threshold": 50,
    "consolidation_threshold": 10
  }
}
```

**代码**: `core/dream_weaver.py`，`core/daemon.py` dream_scheduler_loop

### 1.4 跨会话任务持久

| 文件 | 路径 | 说明 |
|------|------|------|
| **task_plan.md** | `~/.jachin/workspace/task_plan.md` | 任务计划 |
| **progress.md** | `~/.jachin/workspace/progress.md` | 执行进度 |
| **findings.md** | `~/.jachin/workspace/findings.md` | 发现/结论 |

**注入**: 若存在 task_plan 或 progress，System Prompt 自动注入「继续执行计划」上下文。  
**提示**: 复杂多步任务可先用 `core:fs_write` 写入 task_plan.md。

**代码**: `l3_node/task_planning.py`，`l3_node/agent_core.py` _build_system_prompt

### 1.5 HR 招聘：规划与执行物理绑定

| 项 | 路径 / 说明 |
|----|-------------|
| **宏图与战况** | `~/.jachin/workspace/hr_recruitment/task_plan.md`、`progress.md`（API 见 `task_planning.py` 中 `*_hr_recruitment_*`） |
| **DAG 节点** | `HrRecruitmentPlanInitNode` 写宏图 + Session 头；`HarvestLoopNode` 内打招呼/收网成功行与 `STOP_HARVEST`、OS 停止、tick 暂停均落盘 |
| **单一事实文档** | [HR_RECRUITMENT.md](./HR_RECRUITMENT.md) |

---

## 二、P1 / P1+ 已实现（2026-03）

> 对标分析文档已同步：**[JACHIN_VS_OPENCLAW_INTELLIGENCE_ANALYSIS.md §五](./JACHIN_VS_OPENCLAW_INTELLIGENCE_ANALYSIS.md)** 中 P0/P1/P2 标记为已落地；§四表格中「偏好 / 工具缓存 / 冲突 / 梦境阈值」列已与实现一致。

| 能力 | 实现要点 |
|------|-----------|
| **用户偏好结构化** | `~/.jachin/config/user_preferences.json`；`l3_node/intelligence_p1.py`；Lance 梦境末尾 `PREFERENCE_JSON:`；v8 梦境 JSON 可选 `preferences` |
| **冲突澄清闭环** | `~/.jachin/workspace/clarification_pending.json`；梦境 `needs_clarification` / `CLARIFICATION:` 入队；`agent_core` System Prompt 注入「有待澄清」 |
| **工具调用缓存** | `~/.jachin/cache/tool_invoke_cache.json`；默认缓存 `mcp:read_file`、`core:fs_read`、`recall_memory`；TTL 默认 3600s；`mcp_registry.invoke` + `_recall_memory_search` |
| **exec 最小增强** | `core:shell_exec` 默认危险子串拦截；可选 `intelligence_p1.shell_exec_mode=restricted` + `shell_exec_allowlist_prefixes` |

### P1+（exec 后台 / 多节点原生派发）

| 能力 | 实现要点 |
|------|-----------|
| **后台 shell** | `core:shell_exec` 支持 JSON `{"command":"...","background":true,"timeout":3600}`；日志在 `~/.jachin/workspace/.shell_jobs/` |
| **任务查询 / 取消** | `core:shell_job_status`（查日志尾）、`core:shell_job_cancel`（需 `shell_job_cancel_enabled`）；白名单含 `core:shell_exec` 时自动允许二者 |
| **多节点原生派发** | L2 `coordinate` 子任务若 `input_data` 为 `{"type":"native_tool","tool_id":"core:shell_exec","action_input":{...}}`，执行节点 **直接 `run_tool`**，不经子 Agent LLM（可 `coordinate_native_tool_dispatch:false` 关闭） |

**配置示例**（`~/.jachin/nexus_config.json`）：

```json
{
  "intelligence_p1": {
    "tool_call_cache_enabled": true,
    "tool_call_cache_ttl_seconds": 3600,
    "tool_call_cache_allowlist": ["mcp:read_file", "core:fs_read", "recall_memory"],
    "shell_exec_mode": "open",
    "shell_exec_blocklist_patterns": ["rm -rf", "mkfs"],
    "shell_exec_allowlist_prefixes": ["git ", "dir"],
    "inject_preferences_to_prompt": true,
    "inject_clarifications_to_prompt": true,
    "shell_background_max_jobs": 16,
    "shell_job_status_tail_lines": 80,
    "shell_job_cancel_enabled": false,
    "coordinate_native_tool_dispatch": true
  }
}
```

---

## 三、P2 已实现（MVP）

| 项 | 说明 |
|----|------|
| **P2-7 修正意图** | `l3_node/intelligence_p2.py`：关键词检测 → `tag=correction` 写入 `l3_local.json`；可选 `core.memory_store.add_memory_fragment`（v8 梦境）；`l3_memory.json` 条目供同步 L2；梦境 Prompt + 碎片/簇内排序优先（`core/dream_weaver.py`、`core/db/dream_weaver.py`） |
| **P2-8 意图-技能统计** | `~/.jachin/cache/intent_skill_stats.jsonl`；`agent_core` 在 recall / coordinate / delegate / native&MCP 工具后写入 `(intent_hash, skill_id, success)` |
| **P2-9 检索强化** | `~/.jachin/memory/memory_reinforcement.json` 侧车；`l2_memory_lancedb._hybrid_search` 与纯向量分支加权；新库 `memories` 表 init 行含 `reinforce_score`；`POST /api/v2/memory/reinforce` |

**配置**（`nexus_config.json` → `intelligence_p2`）：

```json
{
  "intelligence_p2": {
    "correction_detection_enabled": true,
    "correction_min_user_chars": 4,
    "correction_write_vector_fragment": true,
    "correction_write_l3_sync": true,
    "dream_prioritize_correction": true,
    "intent_stats_enabled": true,
    "intent_stats_max_file_mb": 4,
    "reinforce_search_enabled": true,
    "reinforce_weight": 0.12,
    "reinforce_max_boost": 3.0
  }
}
```

---

## 四、原 P3 条目（已并入 §五 阶段 A）

以下两项仍为 **代码未实现**，已纳入 **§五 阶段 A**，不再单独维护一张「仅两行」的表。

| 能力 | 设计细节 |
|------|----------|
| workspace 级 FLUSH 指令 | 见 [JACHIN_VS_OPENCLAW_INTELLIGENCE_ANALYSIS.md §9.9](./JACHIN_VS_OPENCLAW_INTELLIGENCE_ANALYSIS.md) |
| Post-compaction 审计 | 同上 |

---

## 五、下一阶段优化阶段与执行编排

> **依据**：[JACHIN_VS_OPENCLAW_INTELLIGENCE_ANALYSIS.md §九](./JACHIN_VS_OPENCLAW_INTELLIGENCE_ANALYSIS.md) 深度设计 + **本文 §一～三实际落地状态**。  
> **用途**：排期、并行拆分、与 OpenClaw 对齐度的验收参照；**对外汇报**可用 **§5.0** 的 Milestone / 内部代号 / To Boss 列。**不替代**具体 RFC/issue。

### 5.0 Jachin OS 智能化演进路线图（当前发布: **v0.8.50**）

> 版本号为 **目标 Milestone**（与 Git 标签对齐）；**v0.8.50 = 当前发布基线（DeepBrain）**，阶段 B～E 为后续演进。

| 阶段代号 | 核心战役目标 | 迭代版本号 (Milestone) | 内部战役代号 | 汇报侧重点 (To Boss) |
|----------|--------------|------------------------|--------------|----------------------|
| **阶段 0** | P0～P2 核心流落地（DAG / HITL / 物理绑定等） | **v0.8.46** | **Vanguard（先锋）** | ✅ **已落地**：业务核心 SOP 跑通，具备秒级防线与断点续传（已合入后续发布）。 |
| **阶段 A** | 记忆节奏、Workspace Flush、压缩后审计 | **v0.8.50** | **DeepBrain（深脑）** | ✅ **当前发布标签 v0.8.50**：锚点必更新、压缩后可审计，长期记忆与底层日志可追溯。 |
| **阶段 B** | 任务范式、Checkpoint、Plan 卡 | **v0.8.60** | **Autonomy（自主）** | 从「死执行」到「项目经理」：`execution_mode` + 计划卡 + checkpoint，长线任务可续跑、可验收。 |
| **阶段 C** | WorkflowSpec、`apply_patch` | **v0.8.70** | **Weaver（编织）** | 热插拔编排 + 动态多文件修补：新业务 / Skill 接入成本下降，变更可审计、可回滚。 |
| **阶段 D** | 沙箱防线、完整 HITL 审批链 | **v0.8.80** | **Aegis（神盾）** | 企业级物理隔离：高危动作默认经人类授权，满足合规与内审叙事。 |
| **阶段 E** | 隐式事件总线、统一记忆打分 | **v0.8.90** | **Evolution（进化）** | 行为与反馈闭环进系统；记忆排序可解释、可 A/B，「越用越准」可量化汇报。 |

**与对标文档映射**：阶段 A～E 与 [JACHIN_VS_OPENCLAW_INTELLIGENCE_ANALYSIS.md §9.10](./JACHIN_VS_OPENCLAW_INTELLIGENCE_ANALYSIS.md) 优先级表 **一一对应**。

### 5.1 基线：已完成阶段（当前生产能力）

| 阶段代号 | 对应文档 | Milestone | 执行状态 | 摘要 |
|----------|----------|-----------|----------|------|
| **阶段 0** | 本文 §一 | **v0.8.46** | ✅ **已落地** | P0：L3 本地记忆、pre-reset flush、梦境阈值、三文件规划、HR 物理绑定 |
| **阶段 0** | 本文 §二 | **v0.8.46** | ✅ **已落地** | P1 / P1+：偏好、澄清、工具缓存、shell 黑白名单、后台 shell、`shell_job_*`、coordinate `native_tool` 直派 |
| **阶段 0** | 本文 §三 | **v0.8.46** | ✅ **已落地** | P2 MVP：修正意图、意图-技能 JSONL、检索强化 + reinforce API |
| **阶段 A** | 本文 §5.2 A | **v0.8.50** | ✅ **已落地** | 记忆节奏 P3：`anchor_remediate`、`post_compaction_audit` / `memory_flush_retry`、`l3_compaction_bridge` 等（见 §5.2） |

**结论**：对标文档中的 **P0～P2 与阶段 A（DeepBrain）** 与代码库一致；**§3.3 中已领先项**（多 Agent、跨设备、handoff、Wasm 供应链）在阶段 0 即具备，后续阶段 **以巩固 + 文档化 SLA 为主**（见对标 §9.7）。

### 5.2 下一阶段：分段目标与执行状态

下列阶段为 **MVP 已部分落地 / 持续演进**（细节以本表「当前执行状态」为准）；**目标 Milestone** 见 **§5.0**，技术优先级与对标 **§9.10** 对齐。

| 阶段 | 名称 | Milestone | 目标（对齐 OpenClaw / 补短） | 主要交付物（设计见对标 §九） | **当前执行状态** |
|------|------|-----------|------------------------------|------------------------------|------------------|
| **A** | **记忆节奏 P3** | v0.8.50 | 工作区锚点写入 + 压缩后可审计 | 锚点二次补救 `anchor_remediate`（`second_llm` / `touch_workspace_anchors`）、`post_compaction_audit.remediation` 含 `memory_flush_retry`、progress 感知续跑提示 | 🟢 **本版本发布（v0.8.50）**；`core/compaction_hook.py`、`core/intelligence_workspace.py`、`l3_compaction_bridge` |
| **B** | **任务范式与上下文** | v0.8.60 | plan→execute→verify 可配置；长任务 compaction 不断档 | `planned` 计划卡门禁；**`strict`**：`VERIFY_PASS` + **可选硬只读 verify 轮**（`enforce_readonly_verify_round`，默认 strict 开启）；**`require_brainstorm_card`**；**`force_task_plan_file`** + `task_plan_policy`（HR 话术豁免）+ `progress_has_open_checkboxes` | 🟢 **MVP 演进**（brainstorm/只读轮/task_plan 门均可配置） |
| **C** | **通用编排与多文件编辑** | v0.8.70 | 通用 DAG 广度、`apply_patch` | `core:apply_patch` **备份+失败回滚**、`core:apply_patch_rollback`、`core:workflow_run`：**`depends_on` DAG** + **持久化运行时**（`.workflow_state/`、`on_failure`、`retry`/`retry_delay_sec`、`resume`/`reset`/`run_id`）；与 **HR `DAGWorkflow`** 独立并存 | 🟢 **MVP 完整** |
| **D** | **Shell 企业级** | v0.8.80 | 沙箱隔离 + 审批链 | `sandbox_profile`、`shell_hitl`、`core:shell_hitl_approve` 批准 API | 🟢 **MVP 完整**（OS 级沙箱 / Lark UI 仍为后续） |
| **E** | **可度量「越用越聪明」** | v0.8.90 | 隐式信号与统一排序公式 | `intelligence_e` 消费 `intelligence_events.jsonl` → `memory_reinforcement.json` 侧车增量 | 🟢 **MVP 完整消费端**；**统一 memory_score 公式**仍为后续项 |

### 5.3 编排策略（并行与依赖）

```
阶段 0 + A（v0.8.50 已发布）
    │
    ├─► 记忆 P3（DeepBrain）──────────┐
    │     core/compaction_hook、配置   │  可并行（不同子系统）
    └─► 阶段 B（范式 + checkpoint）───┤
          agent_core、task_planning     │
                                      ├─► 阶段 C 依赖 B 的「机器可读 progress」更佳（非硬阻塞）
                                      └─► 阶段 D 可与 C 并行；高危路径建议晚于 C 的 apply_patch 审计规范
阶段 E 可与 A/B 起并行埋点（先 JSONL 日志，再消费）
```

| 策略 | 说明 |
|------|------|
| **优先 A** | 成本低、直接补齐对标 §2.2.1「记忆节奏」短板，且为 B 的 checkpoint 审计提供挂钩。 |
| **A ∥ B** | A 偏基础设施钩子，B 偏 Agent 行为契约，工程上宜 **两条线并行**。 |
| **C 次之** | 依赖模型与工具链形态，工作量大于 A/B。 |
| **D** | 运维与环境相关（Docker/WSL/审批通道），适合与产品形态绑定后推进。 |
| **E 持续** | 先 **只写事件** 不消费，不阻塞主功能。 |

### 5.4 验收口径（摘要）

| 阶段 | 最小验收 |
|------|----------|
| A | 配置锚点文件后，memoryFlush 回合结束锚点已更新；compaction 后审计失败能触发补救或日志可告警。 |
| B | `planned` 模式下无 plan 卡则不进入 tool；未完成任务经 compaction 后新 session 仍能从 checkpoint 续跑。 |
| C | 非 HR 场景可通过 YAML 跑通一条 tool 链 DAG；多文件 patch 有审计日志且可回滚。 |
| D | 命中策略的命令进入审批队列，批准前进程不执行；沙箱内写盘不越出 `sandboxes/{job_id}`。 |
| E | 事件落盘 + 至少一种消费端（如 reinforce 权重）可配置开关。 |

---

## 六、配置速查

```json
// ~/.jachin/nexus_config.json
{
  "llm": {
    "memory_flush": {
      "enabled": true,
      "soft_threshold": 4000,
      "workspace_must_update": ["memory/MEMORY.md", "workspace/task_plan.md"],
      "anchor_remediate": "none"
    },
    "post_compaction_audit": {
      "enabled": true,
      "remediation": "log",
      "write_checkpoint_on_fold": true
    },
    "compaction_threshold": 6000,
    "compaction_model": "ollama/qwen2.5"
  },
  "intelligence_b": {
    "execution_mode": "react",
    "require_brainstorm_card": false,
    "enforce_readonly_verify_round": false,
    "allow_recall_before_plan_gates": true,
    "force_task_plan_file": false,
    "verify_round_extra_tools": []
  },
  "intelligence_d": {
    "shell_hitl_enabled": false,
    "shell_hitl_patterns": ["curl.*http", "ssh\\\\s+"]
  },
  "orchestration": {
    "skill_routing_enabled": true,
    "vector_router_threshold": 0.75
  },
  "intelligence_e": {
    "enabled": false,
    "reinforce_memory_id": "_intel_from_events",
    "anchor_stale_delta": 0.04,
    "plan_gate_delta": 0.02,
    "max_events_per_run": 400,
    "min_interval_seconds": 60
  },
  "dream_weaver": {
    "fragment_threshold": 50,
    "consolidation_threshold": 10
  }
}
```

- **阶段 A**：`workspace_must_update` 相对 `~/.jachin/`；`anchor_remediate`：`none` | `second_llm`（锚点二次刷新）| `touch_workspace_anchors`（仅 workspace 下锚点追加注释更新时间）；`post_compaction_audit.remediation` 可取 `log` | `clarification` | `none` | `memory_flush_retry`（审计失败再跑一轮 memory_flush）。
- **阶段 B**：`execution_mode`：`react` | `planned` | `strict`。**strict** 下写类工具后须 **VERIFY_PASS**；若 `enforce_readonly_verify_round` 为 true（未配置时 **strict 默认为 true**），系统 **仅暴露只读工具**（默认 `core:fs_read`、`core:shell_job_status`，可加 `verify_round_extra_tools`），并拦截 delegate/coordinate/其它 native。**`require_brainstorm_card`**：planned/strict 下先 **jachin_brainstorm_card** 再计划卡。**`force_task_plan_file`**：启发式多步任务须先写满 `task_plan.md`（`l3_node/task_plan_policy.py`，**招聘相关话术不触发**）。
- **阶段 C**：`core:apply_patch` 默认写 `workspace/.patch_backups/<id>/`，**`core:apply_patch_rollback`** 回滚；YAML `steps[].depends_on` 拓扑执行；**持久化**：`core:workflow_run` 的 JSON 可含 `persistent` / `run_id` / `resume` / `reset` / `keep_completed_state`；步骤级 `on_failure: abort|continue|retry`、`max_retries`、`retry_delay_sec`（状态目录 `workspace/.workflow_state/`）。**HR 招聘 DAG** 仍走 `hr_recruitment_dag` / `DAGWorkflow`，与此 YAML 引擎无关。
- **阶段 D**：除手改 `shell_hitl_approved.json` 外，可用 **`core:shell_hitl_approve`**（`hash_hex` / `command` / `pending_id`）。
- **阶段 E**：`intelligence_e.enabled=true` 时，每次 `run_agent` 入口节流消费事件，向 `memory_reinforcement.json` 写入增量（见 `core/intelligence_e_consumer.py`）。日志：`intelligence_events.jsonl`、`compaction_audit.jsonl`、`apply_patch_audit.jsonl`。
- **长期编排**：`l3_node/orchestration/`（L1 `suggest_skills_from_intent`、L2 `register_domain`/`run_domain`、内置 `hr_recruitment`）；L3 YAML 步骤 `domain_ref`；工具 **`core:domain_workflow_run`**。详见 [ORCHESTRATION_ARCHITECTURE.md](./ORCHESTRATION_ARCHITECTURE.md)。

---

## 七、参考

- [ORCHESTRATION_ARCHITECTURE.md](./ORCHESTRATION_ARCHITECTURE.md)（**长期三层编排**：L1 路由 / L2 领域子图 / L3 YAML+`domain_ref`）
- [JACHIN_VS_OPENCLAW_INTELLIGENCE_ANALYSIS.md](./JACHIN_VS_OPENCLAW_INTELLIGENCE_ANALYSIS.md)（含 §九 设计推导）
- [HR_RECRUITMENT.md](./HR_RECRUITMENT.md)（招聘 DAG / 调度 / 数据路径）
