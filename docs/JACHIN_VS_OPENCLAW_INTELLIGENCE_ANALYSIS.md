# Jachin vs OpenClaw 智能化深度分析

**更新**: 2026-03-16  
**目标**: 充分理解 OpenClaw 智能化核心，分析优劣势，制定 Jachin 全面赶超方案（含「越用越聪明」）  
**与** [INTELLIGENCE_UPGRADE_OVERVIEW.md](./INTELLIGENCE_UPGRADE_OVERVIEW.md) **同步**：实施状态与路线图以该文档 **§五～§六** 为准（**§5.2 🟢**）。

> **工程基线（现行）**：阶段 A～E 已闭环；**brainstorm 卡 / strict 硬只读 verify 轮 / `force_task_plan_file`+`task_plan_policy`、YAML 工作流持久化（`on_failure`/`retry`/`resume`）、三层编排**（[ORCHESTRATION_ARCHITECTURE.md](./ORCHESTRATION_ARCHITECTURE.md)：`l3_node/orchestration/`、`domain_ref`、`core:domain_workflow_run`）。  
> **§4.3 隐式学习**：文本+向量复述检测、**`implicit_turn_attribution`** 全端默认打标 + `intelligence_e`；**可解释检索**：`GET /api/v2/memory/search?explain=true`（见 [MEMORY_SCORING.md](./MEMORY_SCORING.md)）。  
> 本文 **§2.3** 列 **仍相对 OpenClaw 不足**；§2.2.1 侧重「已落地 vs 体验/生态差距」。

---

## 一、OpenClaw 智能化核心解析

### 1.1 记忆体系

| 组件 | 实现 | 特点 |
|------|------|------|
| **存储** | `MEMORY.md` + `memory/YYYY-MM-DD.md` | 纯 Markdown，文件即真相 |
| **日誌** | `memory/YYYY-MM-DD.md` | 按日追加，仅读当日+昨日 |
| **长期** | `MEMORY.md` | 人工/模型精选持久事实 |
| **规则** | 决策/偏好/持久事实 → MEMORY；日常上下文 → memory/YYYY-MM-DD |

### 1.2 记忆刷新 (memoryFlush)

- **触发**：会话 token 接近 `contextWindow - reserveTokensFloor - softThresholdTokens`（默认 4000）
- **行为**：静默 Agent 回合，提示模型将重要信息写入记忆
- **静默**：默认 `NO_REPLY`，用户无感知
- **限制**：每 compaction 周期仅一次；只读 workspace 跳过

### 1.3 检索能力

- **memory_search**：语义召回（向量 + BM25 混合）
- **memory_get**：精确读取指定文件/行
- **嵌入**：支持 OpenAI、Gemini、Ollama 等
- **后处理**：MMR 多样性重排、时间衰减

### 1.4 心智模型

- **纯 ReAct**：Thought → Action → Observation 循环
- **工具驱动**：技能通过 MCP/SKILL.md 暴露
- **ClawHub**：10,700+ skills，无沙箱、无签名（供应链风险）

### 1.5 OpenClaw 智能化短板

1. **无梦境融合**：写入即堆积，无聚类、去重、冲突消解
2. **记忆膨胀**：冗余、冲突并存，检索效率下降
3. **softThreshold 固定**：4000 token 不随 context 缩放（200K vs 1M 模型问题）
4. **单机极客**：无多节点协同、无集中记忆、无企业权限

---

## 二、Jachin vs OpenClaw 智能化优劣对比

### 2.1 优势 (Jachin 领先)

| 维度 | Jachin | OpenClaw |
|------|--------|----------|
| **梦境引擎** | Dream Weaver：聚类 + LLM 融合 + 冲突消解 + 升维 | 无 |
| **记忆刷新** | compaction_hook + memory_flush 可配置 | memoryFlush 固定 4000 |
| **混合检索** | 向量 70% + BM25 30%，专有名词更准 | 有，但无梦境提纯 |
| **L2 集中记忆** | LanceDB + 梦境优化 + 多 L3 同步 | 本地 Markdown 仅 |
| **记忆导出** | Markdown 导出 + API | 无统一导出 |
| **多 Agent 协同** | L2 调度多 L3、分身、delegate | 无 |
| **心智** | AST 蓝图 + ReAct + 量子记忆 + 自我修复 | 纯 ReAct |
| **供应链** | MCP + SKILL.md + JPP Wasm 零信任 | ClawHub 裸跑 |
| **主动能力** | cron_thinker + 云端心跳 | 30min HEARTBEAT |

### 2.2 劣势与差距（2026-03 修订）

以下区分 **仍明显落后**、**已部分拉平（仍有体验/产品化差距）**，与 [INTELLIGENCE_UPGRADE_OVERVIEW.md](./INTELLIGENCE_UPGRADE_OVERVIEW.md) 中 P0～P2 落地情况一致。

#### 2.2.1 仍明显落后或 **MVP 已补仍有差距**（OpenClaw 社区/产品更成熟处）

> 下列数项已有 **代码 MVP**（见 [INTELLIGENCE_UPGRADE_OVERVIEW.md §5.2](./INTELLIGENCE_UPGRADE_OVERVIEW.md)），表格中 **Jachin 现状** 强调与 OpenClaw **体验/完整度** 差距，避免误读为「完全未做」。

| 维度 | OpenClaw | Jachin 现状 |
|------|----------|-------------|
| **workspace 级 FLUSH 指令** | 可配置「必须更新的文件」列表，flush 时强制覆盖 | **已落地**：`workspace_must_update` + mtime；**`anchor_remediate`**；`MEMORY.md` 自动导出；可选 **`silent_anchor_file_round`**（静默锚点 JSON 直写）。**仍差**：与 OpenClaw 完全同构的 **产品开关/观测** 叙事 |
| **Post-compaction 审计** | 压缩后检查关键文件是否更新 | **MVP 已落地**：`post_compaction_audit` + `findings` MACHINE_CHECKPOINT + Nexus/`add_local_memory` 摘要；**`remediation`** 含 `memory_flush_retry`（对折叠前消息快照再 `memory_flush`）；`progress.md` 未完成项注入 compaction **续跑提示** |
| **结构化多文件编辑** | `apply_patch` 等 | **已落地**：备份/回滚/审计；**Python `ast.parse` 预检**（`apply_patch.python_ast_validate` / 单次参数）。**仍弱于** OpenClaw：**语言服务**级校验、**影子 git**、与 **特定模型多文件编辑流** 的一体优化 |
| **exec 沙箱 / OS 级 HITL 审批链** | sandbox、gateway、approval 细粒度 | P1+ 已有 shell；**MVP**：`sandbox_profile` → `workspace/sandboxes/<id>/` cwd；`shell_hitl` + **`core:shell_hitl_approve`**（hash/command/pending_id）+ `pending_shell_approvals.json`。**仍无**：Docker/WSL/OS 级隔离、**Lark/Console 守护进程式**批准后自动执行、与 OpenClaw **gateway** 同级「一键安全策略」产品面 |
| **Skill 生态规模** | ClawHub 万级 skill | MCP + SKILL + JPP，**数量与一键迁移仍弱** |
| **强制任务范式** | doing-tasks：brainstorm→plan→execute→verify | **已落地（可配置）**：`planned`+**jachin_plan_card**；`strict`+**VERIFY_PASS**；**`require_brainstorm_card`**（先 **jachin_brainstorm_card**）；**`enforce_readonly_verify_round`**（strict 下默认 **true**：系统仅暴露只读工具白名单直至 VERIFY_PASS）。**仍弱于** OpenClaw：无 **独立产品化 brainstorm 阶段 UI**、verify 侧无 **Wasm 自动证明** 链 |
| **单机开箱** | 极简 CLI，单机即用 | 完整能力常需 **L2 配对**，上手成本更高 |
| **MMR / 显式时间衰减（L3 路径）** | memory_search 侧产品化成熟 | **`core:local_memory_search`** + L2 **`memory_scoring` MMR**；**`GET .../memory/search?explain=true`** 可解释分量。仍可能在 **默认参数/运营叙事** 上有差距 |

#### 2.2.2 已落地或显著缩小差距（原 §2.2 部分表述已过时）

| 维度 | 原表述 | 当前实现（见总览） |
|------|--------|---------------------|
| **Pre-reset / 新会话前刷新** | 「仅有 compaction 前」 | **P0 已落地**：`POST /api/v3/llm/context/reset` 前 `run_pre_reset_memory_flush()`（`core/compaction_hook.py`、`core/api/console.py`）。与 OpenClaw「每日 /new」自动化程度可比性取决于产品是否再包一层定时任务。 |
| **用户偏好** | 「仅显式 remember」 | **P1**：`user_preferences.json` + Prompt 注入 + 梦境 `PREFERENCE_JSON:` / v8 JSON；**隐式**仍弱于纯靠模型自觉的社区玩法，但已不是「只有显式一条腿」。 |
| **工具结果短期复用** | 未写 | **P1**：只读类工具 TTL 缓存（`tool_invoke_cache.json`）。 |
| **冲突 / 待澄清** | 未写 | **P1**：`clarification_pending.json` + 梦境入队 + System Prompt 注入。 |
| **梦境仅凌晨** | — | **P0**：`fragment_threshold` 达阈值即跑 Dream Weaver。 |
| **L3 断网无记忆** | — | **P0**：**Memory Nexus（Chroma `~/.jachin/palace_db`）** + L1 注入 + `core:local_memory_*` / **`recall_memory`（同源）**；**不**依赖 L2 记忆 API。见 [MEMORY_NEXUS_L3.md](./architecture/MEMORY_NEXUS_L3.md)。 |
| **修正 / 检索越用越准** | — | **P2 MVP**：修正意图、意图-技能 JSONL、强化检索 + `POST /memory/reinforce`。 |
| **隐式事件 → 检索加权** | 社区多靠插件/脚本 | **已落地**：JSONL + **`intelligence_e`**；**[MEMORY_SCORING.md](./MEMORY_SCORING.md)** + **`/memory/feedback`**；**[IMPLICIT_SIGNALS.md](./IMPLICIT_SIGNALS.md)**（文本+向量+**`implicit_turn_attribution`** 全端默认打标+HTTP 埋点）。**仍差**：IM **停留/跳过** 与业务看板 **全量** 对接 |
| **上万 Skill 与编排** | 单图挂全量 skill | **L1**：`l3_node/orchestration/skill_routing.py` 封装向量收窄；**L2**：`register_domain` / `run_domain`；**L3**：YAML **`domain_ref`** + **`core:domain_workflow_run`**。见 [ORCHESTRATION_ARCHITECTURE.md](./ORCHESTRATION_ARCHITECTURE.md) |

### 2.3 相对 OpenClaw 仍显著不足的方面（现行盘点）

下列为 **工程已补 MVP 之后**，在产品形态、社区成熟度或能力深度上 **OpenClaw 仍常占优** 的方向（便于评审与排期）：

| 序号 | 维度 | OpenClaw 常见优势 | Jachin 现状与差距 |
|------|------|-------------------|-------------------|
| 1 | **Skill / 插件生态规模** | ClawHub 万级、开箱即用 | MCP + JPP + 自建，**数量与迁移成本**仍落后 |
| 2 | **单机极简上手** | 单 CLI、少依赖 | 完整能力常需 **L2 配对**，运维与心智负担更高 |
| 3 | **exec 安全产品面** | sandbox、gateway、`security=` 档位、审批与审计叙事成熟 | 有 **子目录沙箱 cwd** + **哈希 HITL** + 批准工具；**无** OS/容器级隔离、**无** 与 IM 深度集成的 **守护式** 批核执行链 |
| 4 | **记忆检索「一站式」体验（尤其 L3 断网）** | `memory_search` 产品化（MMR、衰减、单工具心智） | **已补**：`core:local_memory_search` / `recall_memory`（**deep_search**/Chroma）；若部署 L2 可有 **MMR** 等增强；**仍差**：社区默认心智、调试面板、与 OpenClaw **完全同档**的运营叙事 |
| 5 | **统一排序公式与可解释性** | 社区讨论与实现较集中 | **已文档化** `MEMORY_SCORING.md` + **`memory_scoring.profile` A/B**；实现上侧车与行内仍 **双源**，以 **merged_raw** 统一进饱和 bonus |
| 6 | **隐式信号（§4.3）** | 部分场景有插件埋点 | **已系统化**：标准 type + HTTP + **向量级** 复述/回声（`intelligence_implicit_embedding`）+ **每轮 `implicit_turn_attribution`**（Lark/WS/HTTP/HR/子 Agent 默认打标）；**仍差**：业务侧 **停留/跳过** 与 IM SDK 的 **全量** 对接与看板 |
| 7 | **通用任务 DAG 引擎（产品化深度）** | agent-task-manager：社区模板量、仪表、统一限流叙事 | **已有**：YAML **`depends_on` + 持久化**（`.workflow_state/`、`on_failure`/`retry`/`resume`）+ **L2 领域注册**（`domain_ref` / `core:domain_workflow_run`）+ HR **`DAGWorkflow`**。**仍差**：与 OpenClaw 生态 **开箱 DAG 模板数量**、**跨工具一站式监控** 相比仍弱 |
| 8 | **执行范式完整度** | brainstorm→plan→execute→verify 社区习惯深 | **可选门禁已齐**（见 §2.2.1）。**仍差**：独立 **brainstorm 产品 UI**、verify 与 **Wasm 证明** 的深度整合 |
| 9 | **apply_patch 与模型协同** | 与特定模型/工具链深度绑定的一体编辑流 | **已补** Python **AST 预检**（可配置）；**仍差** 语言服务、影子 git、模型侧一体多文件流 |
| 10 | **任务「必须写 plan 文件」路由** | planning-files 约定强 | **`force_task_plan_file` + `task_plan_policy`**（启发式 + **HR 话术豁免**）已可启用；**仍差**：无 **向量/意图分类器级** 与 L2 统一下发的 **强契约路由**（与 §9.2 演进项） |

---

## 三、智能化完成任务：对比与 Jachin 不足

### 3.1 OpenClaw 任务执行能力

| 能力 | 实现 | 特点 |
|------|------|------|
| **执行工具** | `exec` + `process` | 支持 foreground/background、sandbox/gateway/node、allowlist、HITL 审批 |
| **多步编排** | agent-task-manager | DAG 任务、状态持久、错误恢复、限速 |
| **复杂任务规划** | planning-files | `task_plan.md`、`findings.md`、`progress.md` 跨会话工作记忆 |
| **执行范式** | doing-tasks | brainstorm → plan → execute → verify，系统性执行 |
| **apply_patch** | exec 子工具 | 结构化多文件编辑（仅 GPT-5.2） |
| **exec 安全** | 沙箱、allowlist、safeBins、approval | 细粒度执行策略，`security=deny/allowlist/full` |

### 3.2 Jachin 任务执行能力

| 能力 | 实现 | 特点 |
|------|------|------|
| **ReAct 循环** | L3 agent_core、L2 agent_loop | Thought→Action→Obs，最多 8 轮（L3）|
| **工具调用** | MCP + SKILL.md + JPP run_tool | 按 tool_id 路由，无统一 exec 抽象 |
| **子任务拆分** | delegate | 主 Agent 拆分 → 子 Agent 并行执行 |
| **多节点协同** | coordinate | L2 调度，子任务分配至多 L3 |
| **Handoff** | core:handoff | L2 人格接力（architect/researcher/default）|
| **Swarm 外包** | swarm_hook | 重型工具（video_encode）广播 task_offer 给虫群 |
| **幻觉校验** | 招聘 SOP | 强制工具链校验（atom_post_job_boss 等）|
| **轻量 DAG + 信号** | agent-task-manager 等 | `DAGWorkflow` + `STOP_HARVEST`；HR：`build_hr_recruitment_dag`（见 [HR_RECRUITMENT.md](./HR_RECRUITMENT.md)）|
| **规划文件（P0）** | planning-files 生态 | `task_planning.py`：`task_plan.md` / `progress.md` / `findings.md` + Prompt 注入；招聘另有 `hr_recruitment/` 下宏图与战况 |
| **YAML 工作流（L3 glue）** | agent-task-manager | **`core:workflow_run`**：`depends_on` **拓扑** + **持久化**（`.workflow_state/`、`on_failure`/`retry`/`resume`）；步骤可选 **`domain_ref`** 委托 **L2**（与 `tool_id` 二选一） |
| **多文件 patch** | apply_patch | **`core:apply_patch`** + **`core:apply_patch_rollback`**；备份 `.patch_backups/`；审计 JSONL |
| **执行范式门禁** | doing-tasks | **`intelligence_b`**：`planned` / **`strict`+VERIFY_PASS** / **`require_brainstorm_card`** / **`enforce_readonly_verify_round`** / **`force_task_plan_file`**；见 `intelligence_b_execution.py`、`task_plan_policy.py`、`agent_core` |
| **Shell HITL** | gateway approval | **`shell_hitl`** + **`core:shell_hitl_approve`**；`pending_shell_approvals.json`、`shell_hitl_approved.json` |
| **Compaction（L3）** | session compaction | **`l3_compaction_bridge`**：与 core 共用 `compaction_before_llm_think`（锚点、checkpoint、审计） |
| **编排 L1（技能收窄）** | 万级 skill 全量注入 | **`suggest_skills_from_intent`**（`orchestration` + `SemanticRouter`）；见 `l3_node/orchestration/skill_routing.py` |
| **编排 L2（领域子图）** | 垂直 SOP 散落 | **`register_domain` / `run_domain`**；内置 **`hr_recruitment`**；**`core:domain_workflow_run`** |
| **编排文档** | — | [ORCHESTRATION_ARCHITECTURE.md](./ORCHESTRATION_ARCHITECTURE.md) |

### 3.3 优劣对比（任务完成维度）

| 维度 | OpenClaw | Jachin | 结论 |
|------|----------|--------|------|
| **多步任务持久化** | task_plan/findings/progress.md 跨会话 | **已具备** 三文件 + HR 子目录物理绑定；通用任务仍依赖 Agent 是否写入 | **基本拉平**，习惯与强制度 OpenClaw 社区更成熟 |
| **任务 DAG/状态机** | agent-task-manager 有 DAG、状态持久 | **已有** HR `DAGWorkflow` + YAML **持久化 DAG** + **`domain_ref`** 嵌领域；**仍差** 社区级 **模板/可观测性** | **工程能力已大幅补位**；**生态厚度** OpenClaw 仍常更成熟 |
| **执行范式** | brainstorm→plan→execute→verify | **可配置**：brainstorm 卡、计划卡、strict+只读 verify、task_plan 门（HR 豁免） | **产品 UI 与 Wasm 证明链** OpenClaw 叙事仍常更完整；Jachin **工程门禁**已齐 |
| **Shell 执行** | exec 完整：前台/后台/沙箱/节点/HITL | P1+ 前后台 + `native_tool`；**MVP**：`sandbox_profile` 子目录 + **HITL 队列 + `shell_hitl_approve`** | **OpenClaw 仍领先**（OS/容器沙箱、gateway 产品化）；Jachin **审批链工程面**已闭环到文件+工具 |
| **多 Agent 协同** | 无 | delegate + coordinate 多 L3 | **Jachin 领先** |
| **跨设备执行** | exec host=node 可指定节点 | coordinate 子任务分配 | **Jachin 领先**（L2 调度）|
| **人格接力** | 无 | handoff 切换专家人格 | **Jachin 领先** |
| **供应链安全** | 裸跑 | Wasm 零信任 + MCP 高信任 | **Jachin 领先** |
| **Skill 规模** | 10,700+ 开箱 | MCP + 自建，规模小 | **OpenClaw 领先** |
| **apply_patch** | 有（GPT 多文件编辑）| **MVP**：unified diff + **备份/回滚** + 审计 | **模型侧与 AST 校验**仍常落后 OpenClaw；**工程侧**已对齐社区 diff 工作流主路径 |

### 3.4 Jachin 任务完成方面的不足（现行）

**已落地（工程侧）**：`planned`/`strict`+`VERIFY_PASS`、**brainstorm 卡**、**硬只读 verify 轮**（可配）、**`force_task_plan_file`**+`task_plan_policy`（HR 豁免）、`workflow_run` **持久化**+`on_failure`/`retry`、`domain_ref`/`core:domain_workflow_run`、`apply_patch` 回滚、`shell_hitl`、compaction 锚点/续跑、`intelligence_e` 消费。

**仍相对 OpenClaw / 社区不足**（与 **§2.3** 对照）：

1. **task_plan 强契约**：当前为 **启发式 + 配置**；缺 **L2 统一下发意图类 → 必写 plan** 的完整产品链。
2. **跨会话持久**：用户/模型 **长期不写** 三文件仍可能断档（checkpoint 不能替代 progress 维护）。
3. **Shell**：无 OS/容器沙箱、无 **守护式** 批核执行产品面。
4. **DAG 生态**：开箱模板、监控与 **agent-task-manager** 社区体量仍落后。
5. **apply_patch**：无 AST/影子 git/模型侧一体流专项。
6. **verify**：**strict + `enforce_readonly_verify_round`** 下已 **硬只读工具白名单**；仍无 **Wasm 自动校验** 与 OpenClaw 级「证明」叙事。
7. **Skill 规模与迁移**：ClawHub 万级 vs 自建，差距仍在。

### 3.5 补足建议（任务完成）

| 优先级 | 能力 | 方案 | 状态（现行） |
|--------|------|------|-------------------|
| **P0** | 规划阶段 | L2 意图类 + 强制 `task_plan.md` | **可选启发式已落地**；**L2 契约** 仍演进 |
| **P0** | 跨会话持久 | 三文件 + Prompt + compaction checkpoint | **已落地** |
| **P1 / P1+** | exec 增强 | 后台、`shell_job_*`、沙箱 cwd、HITL+approve | **MVP 已落地**；OS 沙箱 **仍待** |
| **P1** | 任务状态机 | HR DAG + YAML 持久 + `domain_ref` | **已落地**；仪表/模板 **仍待** |
| **P2** | planning-files 类 Skill | 模板化三文件 | 部分 |
| **P2** | apply_patch | diff + 备份回滚 + 审计 | **MVP 已落地** |
| **P3** | 执行范式 | `intelligence_b` 全套门禁 | **MVP 已落地** |
| **P2** | 编排分层 | L1/L2/L3 包 + 文档 | **已落地**（见 ORCHESTRATION 文） |

---

## 四、「越用越聪明」核心差距与补足方案

### 4.1 数据持久化

| 能力 | 现状 | 差距 | 补足方案 |
|------|------|------|----------|
| **对话持久化** | short_term → core_memory，L2 LanceDB | 离线仅靠 L3 时需本地兜底 | P0：**Memory Nexus**（`palace_db`）+ L1 注入；L2 **不**强制 merge 进本地 JSON |
| **偏好持久化** | remember_core_fact、梦境 tag；**P1**：`~/.jachin/config/user_preferences.json` + Prompt 注入 | 若需 DB/多租户表形态可再演进 | **文件形态已落地**（`l3_node/intelligence_p1.py`） |
| **工具调用结果** | Observation 当轮；**P1**：只读类工具短期缓存（TTL，白名单） | 非白名单工具、写操作仍无缓存 | **已实现** `l3_node/tool_call_cache.py`；扩展白名单见 `nexus_config.intelligence_p1` |
| **错误模式** | 梦境可生成 bug_fix 规则 | 未系统化 | 梦境阶段显式输出 `error_pattern` → core_memory |

### 4.2 提优（持续优化）

| 能力 | 现状 | 差距 | 补足方案 |
|------|------|------|----------|
| **梦境频率** | 凌晨 3 点 / 设备闲置；**P0**：`dream_weaver.fragment_threshold` 达阈值即触发 | 可按业务再调参 | **已落地**（见 `INTELLIGENCE_UPGRADE_OVERVIEW.md` §1.3） |
| **冲突消解规则** | 时间优先；**P1**：`needs_clarification` / `CLARIFICATION:` 入队 + System Prompt 注入待澄清 | 产品级「用户点选消解」可再接 API | **澄清闭环已落地**（`clarification_pending.json`） |
| **检索质量** | 混合检索；**P2-9** 强化分侧车 + API | 无显式 UI 点赞闭环 | `POST /memory/reinforce`；混合排序已加权（见 `memory_reinforcement.py`） |
| **Prompt 进化** | 静态 system_prompt | 无根据历史微调 | 可选：基于 core_memory 动态拼接「人格强化」段落 |
| **意图命中率** | vector_router；**P2-8** JSONL 打点 | 无自动改 description | `intent_skill_stats.jsonl`；离线聚合见 `intent_skill_stats.aggregate_recent()` |

### 4.3 隐式学习（无感变聪明）

| 能力 | 现状 | 差距 | 补足方案 |
|------|------|------|----------|
| **用户修正** | **P2**：关键词检测 + 多路写入 + 梦境优先 | 产品与 UI 级「点踩并关联记忆 id」可再接 `POST /memory/reinforce` | **MVP 已落地**（见总览 §三） |
| **跳过/重试** | **`user_message_skipped`**：`run_agent(implicit_signals)` + **`POST /api/v2/intelligence/implicit-signal`**（`signal: skip`） | 各端是否 **默认接入** | 见 **[IMPLICIT_SIGNALS.md](./IMPLICIT_SIGNALS.md)** |
| **停留时长** | **`user_message_dwell`**：`dwell_ms` / `dwell_sec` 上报 | 与业务曝光时长打通 | 同上 + `intelligence_e.dwell_bucket_delta` |
| **复述/追问** | **自动**：`user_repeat_intent`、`user_repeat_followup`（`agent_core` + `core/intelligence_implicit.py`）；**HTTP**：`repeat_intent` / `repeat_followup` | 语义级 embedding 复述检测 | 可调阈值；后续可加向量相似 |

### 4.4 跨会话连续性

| 能力 | 现状 | 差距 | 补足方案 |
|------|------|------|----------|
| **会话恢复** | 多轮 message 在 session 内 | 新 session 丢失上文 | 会话结束前 memoryFlush，新 session 预加载最近 core_memory |
| **跨设备记忆** | 多 L3 各自 Nexus，无默认集中 | 跨机一致需另行设计同步 | 可外挂备份/同步层；单机以 **palace_db** 为 SSOT |
| **身份绑定** | sub_account_id、node_id | 无「主人」级统一身份 | 主账号下多 L3 共享同一记忆 namespace |

---

## 五、Jachin 全面赶超路线图

### P0：基础补齐 — **已实现**（详见 [INTELLIGENCE_UPGRADE_OVERVIEW.md §一](./INTELLIGENCE_UPGRADE_OVERVIEW.md)）

1. **L3 Memory Nexus 持久化**
   - 路径：**`~/.jachin/palace_db`**（Chroma）；可选 HTTP 客户端
   - 结构：Wing/Room **Drawer** 向量库；宿主工具 `commit_drawer` / `deep_search`
   - 触发：回合末异步 commit；**不向 L2 同步宿主记忆**（与 `palace_db` 一体闭环）

2. **Pre-reset / Pre-new 记忆刷新**
   - 在 `/new`、`/reset`、每日重置前执行一次 memoryFlush
   - 复用 compaction_hook 的 `_run_memory_flush` 逻辑

3. **梦境阈值触发**
   - 配置：`dream_weaver.fragment_threshold: 50`
   - 当短期碎片 ≥50 时，立即触发梦境，不等凌晨

### P1：提优增强 — **已实现**（细节见总览 §二）

单一事实来源：**[INTELLIGENCE_UPGRADE_OVERVIEW.md §二](./INTELLIGENCE_UPGRADE_OVERVIEW.md)**（含 P1+：后台 shell、多节点 `native_tool` 直派发）。

| 路线图项 | 落地要点 |
|----------|----------|
| 用户偏好结构化 | `user_preferences.json`；v8 梦境 JSON `preferences`；Lance 融合 `PREFERENCE_JSON:`；Prompt 注入 |
| 冲突澄清闭环 | `clarification_pending.json`；`needs_clarification` / `CLARIFICATION:`；Prompt 注入 |
| 工具调用缓存 | `tool_invoke_cache.json`；默认只读白名单；`mcp:invoke` / `recall_memory` / `core:fs_read` |
| exec 策略与 P1+ | 危险子串拦截 + 可选前缀白名单；`shell_job_*`；协同子任务 `input_data.type=native_tool` 直接 `run_tool` |

**主要代码**：`l3_node/intelligence_p1.py`、`l3_node/tool_call_cache.py`、`l3_node/shell_jobs.py`、`l3_node/primitives/mcp/registry.py`、`l3_node/agent_core.py`、`core/native_tools.py`、`core/dream_weaver.py`、`core/db/dream_weaver.py`。

### P2：隐式学习 — **MVP 已落地**（见 [INTELLIGENCE_UPGRADE_OVERVIEW.md §三](./INTELLIGENCE_UPGRADE_OVERVIEW.md)）

7. **修正意图检测** — ✅ 关键词 + 多路写入 + 梦境排序/Prompt 优先  
8. **意图命中统计** — ✅ 本地 JSONL + Agent 工具后打点（自动分析报告可后续加）  
9. **检索反馈** — ✅ 侧车强化分 + 混合/纯向量检索加权 + `POST /memory/reinforce`（UI 点赞可对接该 API）

### P3：体验打磨（持续）

10. **workspace 级 FLUSH 指令** — ✅ **MVP 已落地**（总览 §5.2 A）  
    - `workspace_must_update` + mtime 校验；`anchor_remediate`（`second_llm` / `touch_workspace_anchors`）；`MEMORY.md` 列入时自动导出。  
    - **仍可持续对齐**：OpenClaw 式「专用静默回合仅写锚点」的产品叙事与默认策略。

11. **Post-compaction 审计** — ✅ **MVP 已落地**（总览 §5.2 A）  
    - `post_compaction_audit` + `findings` checkpoint + `l3_local`；`memory_flush_retry`；`progress.md` 未完成项 → compaction **续跑提示**。

12. **Skill 生态**
    - 兼容 ClawHub 部分 skill；MCP 开箱；JPP 商城建设
    - 提供迁移工具与兼容层

---

## 六、与 OpenClaw 的最终对标（目标状态）

| 维度 | OpenClaw | Jachin 目标 | 实现进度（2026-03） |
|------|----------|-------------|---------------------|
| **记忆写入** | memoryFlush + 手动提醒 | memoryFlush + pre-reset + 梦境自动 | **已达**：compaction + reset 前 flush + 碎片阈值梦境 + **workspace_must_update** + **锚点补救**；与 OpenClaw「静默专用写锚点回合」仍可继续产品化 |
| **记忆提纯** | 无 | Dream Weaver 聚类 + 冲突消解 | **已达** |
| **记忆检索** | 混合 search | 混合 + 反馈加权 + 时间衰减 | **大部已达**：混合 + P2-9 强化；OpenClaw 式 MMR/衰减「单工具」产品体验可继续磨 |
| **越用越聪明** | 依赖用户显式「记住」 | 显式 + 隐式（修正、跳过、重复追问）| **已达**：文本+向量检测、HTTP 埋点、**全端 turn 打标**、**`intelligence_e`** 含 embedding 类 delta；**仍差**：IM **停留/跳过** 与运营看板 **全量** 闭环 |
| **跨会话** | 文件持久 | 文件 + L2 + L3 **Memory Nexus** + 遗留 JSON | **已达**：三文件 + HR 子目录 + **palace_db** / 可选 `l3_local*.json`（遗留） + L2 |
| **多节点** | 无 | L2 调度多 L3 协同 | **已达** |
| **安全** | 裸跑 | MCP + Wasm 零信任 | **已达（模型）**；exec **子目录沙箱 cwd + 哈希 HITL + 批准工具**已 MVP；**OS/容器级**沙箱与 gateway 级产品面仍弱于 OpenClaw |
| **生态** | 10,700+ 无沙箱 | MCP + SKILL + JPP 可扩展 | **进行中**：规模仍落后，安全模型领先 |

---

## 七、参考与实施状态

- **实施总览**: [INTELLIGENCE_UPGRADE_OVERVIEW.md](./INTELLIGENCE_UPGRADE_OVERVIEW.md) — P0 / P1 / P1+ / **P2（§三）** 已落地；**P3 中 workspace FLUSH、post-compaction 审计（含锚点补救/续跑提示）已 MVP**；P3 仅剩 **Skill 生态与体验持续打磨**
- **HR 招聘架构（单一事实来源）**: [HR_RECRUITMENT.md](./HR_RECRUITMENT.md)
- OpenClaw Memory: https://docs.openclaw.ai/concepts/memory
- OpenClaw Compaction: https://docs.openclaw.ai/reference/session-management-compaction
- Jachin `core/compaction_hook.py` / `core/intelligence_workspace.py`：memoryFlush、pre-reset flush、锚点审计与 `anchor_remediate`
- Jachin `core/dreamer.py`：梦境序列
- Jachin `core/db/dream_weaver.py`：L2 梦境聚类与融合
- Jachin `l3_node/task_planning.py`：task_plan / progress / HR 子目录规划文件
- Jachin `l3_node/local_memory.py`：L3 本地记忆
- Jachin `l3_node/intelligence_p1.py` / `tool_call_cache.py`：P1 偏好、澄清、缓存
- Jachin `l3_node/intelligence_p2.py`：P2 修正意图等
- Jachin `core/apply_patch_unified.py`、`core/native_tools.py`：`apply_patch` / `apply_patch_rollback`
- Jachin `l3_node/workflow_spec_runner.py`：`workflow_run` + `depends_on` + **持久化** + **`domain_ref`**
- Jachin `l3_node/orchestration/`：**L1** `skill_routing`、**L2** `domain_registry` / `domain_hr`、**`core:domain_workflow_run`**
- Jachin `l3_node/task_plan_policy.py`：task_plan 门（HR 豁免）
- Jachin `l3_node/shell_hitl.py`：HITL 队列与批准路径
- Jachin `core/intelligence_e_consumer.py`：事件 → reinforce 侧车
- Jachin `core/intelligence_implicit.py` / `core/intelligence_implicit_embedding.py`：隐式信号检测与向量级复述
- Jachin `l3_node/local_memory_search.py`：L3 半衰 + MMR + 可选 `MEMORY.md` 分块
- **长期编排单一说明**：[ORCHESTRATION_ARCHITECTURE.md](./ORCHESTRATION_ARCHITECTURE.md)

---

## 八、全面落后维度速查（小结）

便于评审一眼对齐：**哪里还输 OpenClaw**、**哪里已不必再写「完全缺失」**。

| 大类 | Jachin 仍落后 / 弱于 OpenClaw | Jachin 已对齐或领先 |
|------|------------------------------|---------------------|
| **记忆写入节奏** | OpenClaw 社区叙事更「单文件好讲」 | pre-reset flush、**workspace_must_update**、**静默锚点回合**、审计/重试、L3 兜底；**对外话术**见 **[MEMORY_WRITE_AND_SCORE_NARRATIVE.md](./MEMORY_WRITE_AND_SCORE_NARRATIVE.md)** |
| **记忆提纯** | — | Dream Weaver（OpenClaw 无对等） |
| **检索** | 单工具心智与运营叙事仍可磨 | L2 混合+**MMR**+**`explain=true`**；**`core:local_memory_search`**；P2-9 + reinforce/feedback API |
| **越用越聪明** | IM 停留/跳过与看板全量闭环（§4.3 演进） | 修正/统计/偏好；**文本+向量+turn 打标** 见 **IMPLICIT_SIGNALS.md** |
| **任务执行** | 社区级 DAG 模板/仪表、L2 级 plan 强契约、Wasm verify | 三文件 + HR `DAGWorkflow` + **YAML 持久 DAG** + **`domain_ref`** + **brainstorm/只读 verify/task_plan 门（可配）** + **`planned`/`strict`+VERIFY_PASS** + apply_patch 回滚 + P1+ shell |
| **exec 安全** | OS/容器沙箱、gateway 级审批 UX | 黑白名单、后台任务、**子目录 cwd 沙箱**、**HITL 队列 + `shell_hitl_approve`**、协同直跑工具 |
| **生态与上手** | Skill 数量、单机极简 | Wasm 供应链、多 L3 协同、MCP 可控扩展 |

---

## 九、任务完成与记忆：从落后项到持平/领先的设计分析

> **范围**：对应 §3.3 表格中除 **「社区习惯成熟度」「Skill 规模」** 外的全部维度，并**扩展** §4.1～§4.4 数据持久化与记忆相关落后项。  
> **性质**：架构与产品层设计推理，用于对齐路线图优先级；**不替代**具体 RFC/任务拆分。

### 9.1 统一原则（为何能「少拼生态、多拼架构」）

| 原则 | 说明 |
|------|------|
| **契约优于自觉** | OpenClaw 社区强在「约定俗成」；Jachin 要用 **可配置门禁 + 机器可读状态** 达到同等「完成率」，而不依赖模型每次记得写文件。 |
| **状态单一事实源** | 任务进度、记忆片段、审批队列各自 **一个权威存储** + 版本号/时间戳，避免 compaction 后与磁盘不一致。 |
| **与 compaction 共生** | 任何「怕丢的上下文」必须在 **软阈值前** 或 **compaction 钩子内** 显式落盘（checkpoint），而不是指望长上下文。 |
| **领先抓手** | OpenClaw 无 L2 多节点、无 Wasm 供应链；Jachin 的 **coordinate + 校验型 Wasm + DAG 节点级派发** 是差异化领先路径。 |

---

### 9.2 多步任务持久化（§3.3：与 OpenClaw「基本拉平」→ **强制度持平、多节点领先**）

**落后本质**：不是缺 `task_plan.md`，而是 **缺少「何时必须创建/更新」的触发与校验**；新 session 是否续跑取决于上一轮 LLM 是否写入。

**实现进展（现行）**：**`intelligence_b.force_task_plan_file` + `l3_node/task_plan_policy.py`** — 启发式多步任务 + **HR 关键词豁免**，在写类工具 / delegate / coordinate 前校验 `task_plan.md` 有效长度；允许 **仅** `core:fs_write` 指向 `task_plan.md` 先落盘。

**设计路径（后续）**：

1. **意图—契约表（路由层）**  
   - 在 L2 或向量意图分类：对 `long_running` 等类 **统一下发**策略（超越当前启发式）。  
   - 持平：等价于 OpenClaw planning-files「默认就要有文件」。  
   - 领先：多 L3 同一 `project_id` 读同一路径（`~/.jachin/workspace/projects/{id}/`）。

2. **机器可读进度块**  
   - 在 `progress.md` 约定 YAML front matter 或 `<!-- TASK_STATE: json -->` **固定区块**，供调度器/DAG/规则引擎解析，而非仅自然语言。  
   - 便于与 HR `progress.md` 模式统一，**通用任务与垂直任务同一套解析器**。

3. **会话首包注入「续跑契约」**  
   - System Prompt 不仅注入文件摘要，还注入 **「当前阻塞点 + 下一可执行动作」**（由上一步 machine-readable 块生成）。  
   - 减少「模型忘了之前干到哪」。

**目标判定**：**持平**＝长任务断档率与 OpenClaw 典型 doing-tasks 用户相当；**领先**＝多节点下 plan 版本冲突可检测（L2 合并或 CAS 写）。

---

### 9.3 任务 DAG / 状态机（§3.3：通用广度 → **已补工程，续磨产品**）

**现状（现行）**：**YAML** 已支持 **`depends_on` 拓扑 + 持久化状态**（`.workflow_state/`）、**`on_failure` / `retry` / `retry_delay_sec` / `resume`**；步骤可选 **`domain_ref`** 委托 **`l3_node/orchestration`** 已注册 **L2**（如 `hr_recruitment`），与 HR **`DAGWorkflow`** **并存**（见 [ORCHESTRATION_ARCHITECTURE.md](./ORCHESTRATION_ARCHITECTURE.md)）。  
**仍差**：社区 **模板量、限流/仪表产品叙事**、与 OpenClaw 生态「开箱多步任务」的 **心智与文档厚度**。

**设计路径（演进）**：

1. **DAG 规格与代码分离**  
   - 扩展 `WorkflowSpec`：节点类型 `tool` | `domain_ref` | `llm` | `human`；边 + 条件 + 统一遥测。  
   - 垂直领域继续用 **`DAGWorkflow`**；通用 glue 继续用 **YAML + 注册表**。

2. **与 delegate 编译**  
   - `delegate` 返回的 sub_tasks 可 **可选编译** 为临时 DAG（一次性），失败则落盘到 `task_plan.md` 的「子图」段落。  
   - 持平：与「子任务依赖」社区实践对齐。  
   - 领先：某节点标记 `execute_on: node_id`，由 L2 **coordinate** 派发到指定 L3（OpenClaw 单机无此维）。

3. **断点与重试**  
   - 状态里持久化 `completed_nodes` / `failed_at_node`（HR 已有）**泛化到任意 workflow_id**；规则引擎可定时 resume。  

**目标判定**：**持平**＝通用多步任务可配置 DAG 而无需复制 HR 代码；**领先**＝跨设备节点级执行与恢复。

---

### 9.4 执行范式：brainstorm → plan → execute → verify（§3.3 OpenClaw 领先项）

**落后本质**：ReAct 单循环，无 **强制阶段边界**；verify 若仅靠模型自觉仍易虚报完成。

**实现状态（现行）**：`execution_mode`：`react` \| `planned` \| `strict`；**`require_brainstorm_card`**（**jachin_brainstorm_card** 先于计划卡）；**`enforce_readonly_verify_round`**（**strict 默认 true**：仅白名单只读工具 + 拦截 delegate/coordinate/其它写类 native，直至 **VERIFY_PASS**）；见 `intelligence_b_execution.py`、`agent_core`。**仍差**：独立 **brainstorm 产品 UI**；verify 与 **Wasm 自动证明** 的深度整合（§2.3）。

**设计路径（后续）**：

1. **三档 `execution_mode` + 上述布尔开关** — **已实现**。

2. **Plan 卡协议** — **已实现**；可演进：更严 schema、与 `task_plan.md` 机器块联动。

3. **Verify 钩子（深化）**  
   - 在已有 **硬只读轮** 基础上，接入 **Wasm/schema** 自动校验，失败写回 `progress.md`。  
   - **领先点**：Wasm 证明执行结果。

---

### 9.5 Shell 执行：沙箱与 HITL（§3.3：OpenClaw 仍领先）

**落后本质**：**子目录 cwd 沙箱 + 文件队列 HITL** 已补工程闭环；**仍缺** OS/容器级隔离与 **守护进程式**「批准→自动执行」产品面。

**设计路径（分层）**：

| 层级 | 目标 | 设计要点 |
|------|------|----------|
| **L1（已有）** | 防误删/误格盘 | 黑名单、前缀白名单、`shell_exec_mode` |
| **L2 沙箱（MVP）** | 工作区隔离 cwd | **`sandbox_profile`** → `workspace/sandboxes/<id>/`（已实现）；**仍待**：Docker/WSL2/Windows Sandbox **进程级**隔离 |
| **L3 HITL（MVP）** | 人类审批链 | **`shell_hitl`** + **`core:shell_hitl_approve`** + `pending_shell_approvals.json` / `shell_hitl_approved.json`（已实现）；**仍待**：Lark/Console **daemon** 批准后自动注入下一轮执行 |
| **领先** | 多节点 | 审批在 **L2 统一队列**，执行在指定 L3，审计日志回传 L2 |

**目标判定**：**持平**＝`security` 三档与 OpenClaw deny/allowlist/full 可一一映射；**领先**＝企业场景下审批与审计默认开启。

---

### 9.6 apply_patch（§3.3：OpenClaw 领先）

**落后本质**：模型改多文件靠 `fs_write` 多次，无 **原子多文件事务** 与 **统一 diff 审计** — **工程主路径已补**；**模型侧一体体验与 AST 校验**仍常落后 OpenClaw。

**设计路径**：

- **方案 A（快）**：MCP 工具封装社区 **apply_patch / unified diff** 协议；输入 patch 文本，落地前做语法/路径白名单校验。  
- **方案 B（稳）**：自研 `core:apply_patch`：解析 unified diff → 逐文件应用 → **写审计日志**（谁、何时、哪 session）→ **备份 `.patch_backups/<id>/`** → 失败 **整批回滚** + **`core:apply_patch_rollback`**。  
- **领先**：patch 应用前后跑 **Wasm 语言服务**（或 tree-sitter）做 AST 级校验，失败则整包回滚；可选影子 git。

**依赖**：模型需能稳定输出 patch（或中间层用小模型做「编辑稿→patch」）。

**实现状态（现行）**：**方案 B** — 备份/回滚/审计 + **Python `ast.parse` 预检**（可配置）；**仍待**：Wasm/语言服务级校验、影子 git、与特定模型输出形态专项优化。

---

### 9.7 已领先维度（协同 / 跨设备 / 人格 / 供应链）：如何**巩固为壁垒**

| 维度 | 巩固设计 |
|------|----------|
| **多 Agent + 跨设备** | 为 `coordinate` 增加 **SLA 指标**（超时、重试、成本）写入 `findings.md`；失败自动 escalate 到 L2 默认节点。 |
| **人格 handoff** | handoff 时 **打包** `task_plan` 摘要 + `clarification_pending` + 相关 `l3_local` 条目，避免接力断档。 |
| **供应链** | 维持 MCP 高信任 / Wasm 零信任 **双轨**；对「仅脚本」类 skill 强制降级为 Wasm 或沙箱 shell。 |

---

### 9.8 数据持久化：统一模型与「上下文不丢」（承接 §4.1、§3.4-6）

**落后本质**：工具结果、任务态、记忆片段 **写入路径分散**；compaction 后 **Observation 折叠** 导致长任务丢中间证据。

**设计路径**：

1. **统一关联键**  
   - 建议所有落盘事件带 `session_id` + `plan_revision`（或 `workflow_id`），便于梦境与检索 **联表**（逻辑上）还原一次「任务故事线」。

2. **Compaction 前 / 后 checkpoint（产品化）**  
   - **MVP**：`compaction_hook` + `post_compaction_audit` + `findings` MACHINE_CHECKPOINT + `l3_local`；`memory_flush_retry`；`progress_has_open_checkboxes` → **续跑提示**；锚点 **`anchor_remediate`**。  
   - **仍演进**：machine-readable `progress.md` 块与 **任意** workflow_id 的 DAG 状态 **统一解析**，便于调度器定时 resume。

3. **写工具结果缓存策略扩展**  
   - 在 **只读白名单** 基础上，增加 **「可复现写」** 缓存：例如相同 `tool_id+hash(input)` 在短 TTL 内直接返回上次结果（可选），减少重复昂贵调用。

4. **错误模式系统化（§4.1 缺口）**  
   - 梦境输出 schema 增加 `error_pattern[]` → 写入 core_memory / l3_local；`agent_core` 在相似栈迹时 **预注入**「已知排错片段」。

**目标判定**：**持平**＝长任务在 compaction 后 **可恢复连续叙事**；**领先**＝多 L3 共享同一 checkpoint 视图（L2 合并）。

---

### 9.9 记忆体系：FLUSH、检索形态、隐式信号（承接 §2.2.1、§4.2～§4.3、§六）

| 落后项 | 设计路径 | 持平/领先 |
|--------|----------|-----------|
| **workspace 级 FLUSH（P3）** | `nexus_config` 配置 `memory_flush.workspace_must_update`（路径相对 `~/.jachin/`）；flush 后 **mtime 校验**；`MEMORY.md` 列入时自动导出 | **MVP 已落地**；与 OpenClaw「强制再写一轮」等行为可继续对齐 |
| **Post-compaction 审计（P3）** | compaction 后 **checkpoint**（`findings` 块 + `l3_local`）+ 可选 `post_compaction_audit` | **MVP 已落地**（总览 §5.2 A） |
| **MMR / 时间衰减产品化** | L2：`memory_scoring`（MMR 默认开）；L3：`core:local_memory_search`（半衰 + MMR） | **已落地**；见 `MEMORY_SCORING.md` |
| **reinforce 与 decay 统一** | 文档化 hybrid + **merged_raw** + 饱和 bonus；**profile** A_sum_cap / B_l2norm_cap | **已落地**（年龄衰减主要在 L3 工具与产品叙事；L2 行内 `timestamp` 可继续纳入公式） |
| **隐式信号（§4.3）** | **IMPLICIT_SIGNALS.md** + 向量检测 + **`implicit_turn_attribution`** + **`intelligence_e`**（含 embedding_*_delta） | **已落地**；IM **停留/跳过** 全量接线、L2 **age 进公式** 可继续演进 |

---

### 9.10 小结：优先级建议（与 §3.5、总览 §五 编排对齐）

**阶段 A～E + YAML 持久 + 范式扩展 + 三层编排** 已在工程落地，下表为 **后续** 产品/生态优先级（与 §2.3 仍差项对应）：

| 优先级 | 项 | 理由 |
|--------|-----|------|
| **P0 产品** | L2 级意图 → **强契约** `task_plan.md`（超越启发式） | 与 OpenClaw planning-files **完全行为等价** |
| **P1 安全** | OS/容器沙箱 + HITL daemon / L2 统一审批队列（§9.5） | 对齐 OpenClaw sandbox/gateway |
| **P1 任务** | DAG **模板库 + 可观测性**（§9.3） | YAML/领域 glue 已有；补 **社区体验** |
| **P2 检索** | 观测 **UI/运营叙事**（`explain=true` 已可 API 取分量） | 在已落地公式上 **超越** OpenClaw 可观测性 |
| **P2 编辑** | 语言服务校验、影子 git（§9.6） | 与 OpenClaw 多文件编辑 **完整度** |
| **持续** | IM SDK **停留/已读/跳过** 与运营看板 | 隐式学习 **业务闭环** |

> **单一事实来源**：**[INTELLIGENCE_UPGRADE_OVERVIEW.md](./INTELLIGENCE_UPGRADE_OVERVIEW.md)** §五～§六；**记忆排序**：[MEMORY_SCORING.md](./MEMORY_SCORING.md)；**编排分层**：[ORCHESTRATION_ARCHITECTURE.md](./ORCHESTRATION_ARCHITECTURE.md)。  
> **执行韧性（全 Skill/任务）**：[JACHIN_EXECUTION_RESILIENCE_CONTRACT.md](./JACHIN_EXECUTION_RESILIENCE_CONTRACT.md)；Cursor 规则：`.cursor/rules/080-jachin-execution-resilience.mdc`（`alwaysApply`）。

---