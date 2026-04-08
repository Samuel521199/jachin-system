# Jachin 混合智能体架构白皮书（L3 主轴 + L4 增强）

**版本**: 2026-04-07  
**状态**: **架构 SSOT（单一事实来源）** — 描述 Jachin AI OS 在 **单体 ReAct 主轴** 之上如何挂载语义层、SOP、**内联 Critic** 与 **Experience RAG**；与实现渐进对齐。  
**术语 SSOT**: [FOUR_PRIMITIVES.md](../FOUR_PRIMITIVES.md) → 中文全文 [Jachin 视角的「四大原语」终极架构规范.md](../Jachin%20视角的「四大原语」终极架构规范.md)  
**执行韧性**: [JACHIN_EXECUTION_RESILIENCE_CONTRACT.md](../JACHIN_EXECUTION_RESILIENCE_CONTRACT.md)  
**工具池**: [L3_TOOL_POOL_AND_MCP_ASSEMBLY.md](./L3_TOOL_POOL_AND_MCP_ASSEMBLY.md)

---

## 1. 核心论断：单主轴 ReAct 为绝对核心

Jachin L3 **不是**「对等多 Agent 运行时」作为默认拓扑，而是：

- **Monolithic Main Loop（单主轴 ReAct）**：`run_agent` 内 **一条** 主循环 —— 模型交替输出 Thought / Action / Observation，工具执行仍经 **`_parse_action` → `run_tool` / MCP Registry** 的既有防线（含 `sqlite_write_guard` 等），**不得**因 L4 增强而绕过或削弱。
- **可选多 Agent**：`delegate`（子 Agent 并行）、`submit_background_task`（异步 Agent Task）、`coordinate`（L2 多节点）属于 **按需分支**，与主轴 **正交**，不改变「默认一条 ReAct」的定义。

---

## 2. 四大原语（不变）

- **Tools**：`core:*`、`jpp:*` 原子执行。  
- **MCP**：`mcp:*` 协议外挂进程。  
- **Skills**：`SKILL.md` / 域文档 — 声明式 SOP 与白名单。  
- **Agent Tasks**：多轮子生命周期（delegate / 后台 / coordinate）。

L4 **不引入第五原语**；仅增加 **编排层增强**（提示词契约、门控、经验检索）。

---

## 3. 内联 Critic ≠ 独立并发 Agent

| 维度 | 说明 |
|------|------|
| **代码载体** | `l3_node/critic_agent.py` 的 `evaluate_action` |
| **运行时位置** | **同一 ReAct 迭代**内、**真正调用 MCP/Native 之前**；与 Actor 共享会话与 `ctx`，**无**独立 `run_agent` 实例 |
| **性质** | **Inline Guardrail（内联认知门控）**：一次（或数次）**轻量 LLM 调用**，产出 pass/fail + 批评文案；失败则 **伪造 Observation** 打回重做 |
| **非目标** | **不是**与主 Agent 并行的常驻进程，**不是**单独工具白名单上的「第六类原语」 |

**Fail-open**：Critic API 异常、超时、解析失败时 **必须放行** 主循环，并打 **Warning** 日志（见实现约束）。

---

## 4. 端到端调度链（主路径）

以下为 **单次用户请求** 在 L3 上的逻辑顺序（与代码大致对应，细节以仓库为准）：

1. **意图 / 网关**：`GatewayContextBundle`、澄清门控、路由 utterance、分类与 enrich。  
2. **嗅探（含语义层）**：`apply_gateway_ingress_pipeline` → `context_sniffer`；加载 `db_semantics.md` 摘要、`db_semantics.yaml` → `bundle.extra["semantic_layer"]`。  
3. **工具池组装**：`assemble_tool_pool`（Native / Wasm / MCP 合并，白名单拦截）。  
4. **System Prompt**：注入网关块、（可选）参谋长 / 环境报告、**业务语义层**、**L4 SOP（Probe→Map→Execute）**、**[HISTORY_FEW_SHOTS] 经验块**（Experience RAG-lite）。  
5. **ReAct 循环**：LLM 输出 → **`_parse_action`（禁止破坏）** →  
6. **审查（Critic）**：对 **SQLite 族** 等策略命中路径，在 **`HOOK_BEFORE_TOOL_EXEC` 之前** `await evaluate_action`；未通过则 **不执行工具**，注入 Observation 并 `continue`。  
7. **派发**：`run_tool` / MCP → Observation → 下一迭代或 Final Answer。  
8. **经验飞轮（写）**：Critic 通过（或 Critic 关闭时的 fail-open 语义）且 **`read_query` / `write_query`** 成功、Observation 启发式为成功 → `experience_memory.save_successful_action`。

---

## 5. 语义层与 SOP

- **语义层**：`db_semantics.yaml`（工作区优先，次选仓库 `config/db_semantics.yaml`）；与 `db_semantics.md`（环境报告摘要）互补。  
- **SOP**：系统提示词中的 **L4 智能体法则** —— 数据路径上强制 **先 Probe、再 Map（含 `<thinking>`/语义层对齐）、再 Execute**；实现与 Review 见 `.cursor/rules/090-jachin-l4-agent.mdc`。

---

## 6. Experience RAG（动态经验飞轮）

| 项 | 说明 |
|----|------|
| **模块** | `l3_node/experience_memory.py` |
| **存储** | 默认 `~/.jachin/l4_experience/experience.jsonl`（**JSONL**，无向量库依赖） |
| **检索** | 轻量 **TF-IDF + 余弦相似度**（纯标准库 + `math`），Top-K（默认 2） |
| **注入** | `[HISTORY_FEW_SHOTS]` 块挂入 **system 后缀**（`SuffixChunk` `l4_experience_rag`） |
| **边界** | **软引导**；表结构仍以当前轮 Probe 与语义层为准 |
| **开关** | `JACHIN_EXPERIENCE_RAG_ENABLED`（默认开启）；失败 **静默跳过** |

---

## 7. 健壮性与可观测性

- **经验库**：读/写异常不得冒泡拖死 ReAct。  
- **Critic**：异常 / 超时 → **Warning + fail-open**。  
- **前端状态**：`on_step` 文案建议统一为「⏳ 正在检索历史经验…」「🛡️ Critic 审查中…」「✅ 审查通过，即将执行」「❌ Critic 未通过，已打回重做」（以 `agent_core` 实现为准）。

---

## 8. 文档地图与归档

| 文档 | 角色 |
|------|------|
| **本文** | **混合架构 + L4 挂载 + 经验飞轮** 总 SSOT |
| [L3_AGENT_CONTEXT_MEMORY_AND_PROMPT.md](../L3_AGENT_CONTEXT_MEMORY_AND_PROMPT.md) | L3 执行面深度说明（与本文 **代码级** 互补） |
| [FOUR_PRIMITIVES.md](../FOUR_PRIMITIVES.md) | 四大原语 **索引** |
| [L4_AGENTIC_WORKFLOW.md](./L4_AGENTIC_WORKFLOW.md) | **跳转 stub** → 本文 + 历史存档 |
| [_archive/L4_AGENTIC_WORKFLOW.v1.md](./_archive/L4_AGENTIC_WORKFLOW.v1.md) | L4 规范 **历史快照**（合并前正文） |

**冲突处理**：若旧文档与本文或 `FOUR_PRIMITIVES` 术语冲突，以 **四大原语中文规范 + 本文** 为准。

---

## 9. 实现入口（维护用索引）

- 主循环：`l3_node/agent_core.py` — `run_agent`、ReAct 内 `native` 分支、`_build_system_prompt`。  
- Critic：`l3_node/critic_agent.py`。  
- 经验：`l3_node/experience_memory.py`。  
- 语义层加载：`l3_node/intent_gateway/workspace_db_context.py`、`context_sniffer.py`、`gateway_pipeline.py`。

---

## 附录：与「多 Agent」话术的对齐

- **说「多 Agent」时**：通常指 `delegate` / 后台任务 / L2 `coordinate`，或产品叙事上的「多角色」。  
- **说「Actor–Critic」时**：在 Jachin 实现上指 **同一循环内的 Actor（主模型）+ 内联 Critic（轻量校验 LLM）**，**不是**两个对等的 ReAct OS 进程。
