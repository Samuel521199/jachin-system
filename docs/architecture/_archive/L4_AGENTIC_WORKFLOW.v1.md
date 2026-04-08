> **ARCHIVED（历史快照，非 SSOT）**  
> 文中 **「Actor-Critic 双轨」** 等表述已被当前实现替代：**Critic 为单主轴 `run_agent` 内的内联门控**（`l3_node/critic_agent.py`），主架构以 **[JACHIN_HYBRID_AGENT_ARCHITECTURE.md](../JACHIN_HYBRID_AGENT_ARCHITECTURE.md)** 为准。请勿以本存档做工程决策依据。

---

# L4 Agentic 工作流 — Actor-Critic 双轨与语义层（架构基石）

**版本**: 2026-04-07  
**状态**: **设计规范（Design SSOT）** — 描述从「指令驱动 / 单轨 ReAct」演进到 **L4 级 Agentic 智能体** 的目标架构；**实现为渐进落地**，与现有四大原语与 `run_agent` 共存。  
**关联**: [FOUR_PRIMITIVES.md](../FOUR_PRIMITIVES.md)（术语与边界 SSOT）、[Jachin 视角的「四大原语」终极架构规范.md](../Jachin%20视角的「四大原语」终极架构规范.md)、[L3_TOOL_POOL_AND_MCP_ASSEMBLY.md](./L3_TOOL_POOL_AND_MCP_ASSEMBLY.md)、[080 执行韧性合约](../JACHIN_EXECUTION_RESILIENCE_CONTRACT.md)（错误分类与 RunReport）。

---

## 1. 架构目标

### 1.1 现状（L3 主路径）

- **单轨 ReAct**：模型在统一循环中交替「思考 → 选工具 → 观察」，工具仍以 **四大原语** 中的 **Tools / MCP** 为原子执行面；**Skills** 以 Prompt/SOP 与白名单形式注入；**Agent Tasks** 用于子会话与异步。
- **风险**：对 **数据库查询**、**高参数 MCP 调用** 等场景，模型可能 **跳过结构认知** 直接生成「看起来像对」的 SQL / JSON，导致 **静默错误** 或 **幻觉成功**（与执行韧性合约中的 `per_item` / `config` 归因相关）。

### 1.2 目标（L4）

- 在 **不改变四大原语定义** 的前提下，将「一次直出工具调用」升级为 **Actor-Critic 双轨**：
  - **Actor（规划轨）**：按 **SOP** 强制 **慢思考** —— 先 **探查** 再 **映射** 再 **执行**（见第 4 节）。
  - **Critic（校验轨）**：独立 **审查** Actor 产出的「可执行意图」（如 SQL、MCP 参数），**未通过则不得落盘执行** 或必须降级为只读探查。
- **语义层**：用 **`db_semantics.yaml`（及扩展）** 将「业务语言」与「物理 schema / 工具契约」对齐，减少模型自由发挥空间。
- **Experience RAG**：把 **历史成功轨迹**（Few-Shot）注入规划与审查上下文，提高稳定复用率。

### 1.3 非目标（本阶段不推翻）

- **不** 把 MCP 改名为 Tool，**不** 把 Skill 与 jpp 混写（仍遵守四大原语 SSOT）。
- **不** 替代 `delegate` / `submit_background_task` / `coordinate` 的 Agent Tasks 语义；L4 层主要增强 **单体 `run_agent` 内** 的「数据与复杂 MCP」路径。

---

## 2. 核心组件

### 2.1 Semantic Layer（业务语义字典）

| 项 | 说明 |
|----|------|
| **载体** | `db_semantics.yaml`（及按域拆分的 `db_semantics.d/*.yaml`，实现期再定） |
| **内容** | 业务实体名、同义词、**允许引用的表/列**、**禁止直连的敏感域**、与 MCP 工具参数的 **字段映射**、单位/枚举约定 |
| **作用** | Planner 与 Critic 的 **共同词典**；Map 阶段必须引用语义层条目，而非纯模型臆测列名 |
| **与 Skills 关系** | Skills 仍是 **声明式 SOP**；语义层是 **结构化、可版本化** 的「数据语义」补充，可被 Skill 引用，但不替代 Skill |

### 2.2 SOP Planner（Actor / 慢思考规划器）

**角色**: 在 **执行任何写库 / 复杂 MCP 调用** 前，产出 **分步计划** 与 **中间制品**（artifacts）。

**强制 SOP（逻辑顺序）**：

1. **Probe（探查）**  
   - 只读：如 `list_tables` / `describe` / `information_schema` 类工具或 MCP 等价能力。  
   - 输出：**当前会话可见 schema 快照**（或引用句柄），写入上下文供下一步使用。

2. **Map（映射）**  
   - 将用户意图 + **Semantic Layer** + Probe 结果 **对齐**：目标表、列、过滤条件、与业务字典的一致性检查。  
   - 输出：**结构化映射说明**（自然语言 + 可选 JSON），**不** 在此步直接生成最终可执行 SQL。

3. **Execute（执行）**  
   - 在 Critic **通过**（见下）后，才生成 **最终** `Action` / `Action Input`（或等价 tool call），进入现有 `run_tool` / `mcp_registry.invoke` 路径。

**实现注**: Planner 可以是 **同一模型多轮**、**独立轻量模型** 或 **规则+LLM 混合**；架构上要求 **步骤可观测**（日志 / trace），便于审计与韧性策略（080 合约）。

### 2.3 Critic Agent（自我审查 / 校验轨）

| 项 | 说明 |
|----|------|
| **输入** | Actor 在 Map/Execute 边界提交的 **拟执行制品**（如 SQL 草案、MCP JSON）、Probe 快照摘要、语义层相关条目 |
| **输出** | `pass` / `fail` + **错误类别**（对齐 `transient` \| `resource` \| `per_item` \| `config` \| `permanent`）+ **可执行修改建议** |
| **硬约束** | **未 pass**：禁止进入 **写** SQL 或 **副作用 MCP**；允许退回 Probe 或要求 Actor 重写 Map |
| **与执行韧性** | Critic 失败不应用 `except: pass` 掩盖；应产生可解析的 **RunReport 片段** 或 ExecutionBrief 线索 |

**模型策略**: 可与 Actor **同型不同 prompt**、或 **更小/更便宜模型** 专做静态检查；架构上 **必须可插拔**。

### 2.4 Experience RAG（历史成功 Few-Shot 经验池）

| 项 | 说明 |
|----|------|
| **存什么** | 脱敏后的 **(意图摘要, Probe 要点, Map 结构, 最终工具调用, 结果 ok/fail)**；可选 embedding 索引 |
| **怎么用** | Planner 在 **Map** 前检索 Top-K；Critic 可参考「同类失败模式」负面清单 |
| **边界** | 不替代语义层 SSOT；**Few-Shot 仅作软引导**，最终以语义层 + Probe 为准 |
| **与 040 RAG** | 与现有记忆/RAG 规则共存；L4 经验池 **命名空间独立**（如 `l4_experience/`），避免与闲聊长记忆混写 |

---

## 3. 在系统中的挂载点（兼容性声明）

### 3.1 与四大原语的关系（包装与增强）

- **Tools / MCP**：仍是 **唯一原子执行面**；L4 **不新增**「第五原语」。
- **Skills**：继续提供 **域 SOP 与白名单**；L4 SOP Planner 可 **消费** Skill 中的步骤约束，并把 **Probe → Map → Execute** 写进 Skill 的可选扩展段（实现期约定格式）。
- **Agent Tasks**：`delegate` / 后台任务 **可复用** 同一套 L4 中间件（例如子 Agent 专跑 Critic），但 **默认先** 在单体 `run_agent` 内闭环。

### 3.2 与 `run_agent` / `_parse_action` 的边界

**推荐挂载（概念）**：

```
用户输入 / 会话
    → run_agent（现有：网关、工具池、ReAct 循环）
        → 【L4 中间件】（新增，可开关）
            → 若命中「数据/复杂 MCP」策略：强制 Planner SOP + Critic
            → 否则：透传，行为与今日 L3 一致
        → LLM 原始输出
        → _parse_action（现有：解析 Action / Final Answer）
        → run_tool / MCP invoke（现有）
```

- **兼容性**:  
  - **默认关闭 L4** 时，代码路径与当前 **二进制等价**（仅增加无操作分支或配置开关）。  
  - **打开 L4** 时，仅在 **声明的策略类意图**（如 SQLite、指定 MCP 家族）上强制 SOP；简单 `core:fs_read` 等可 **短路** 不经过 Critic。

### 3.3 与 MCP 工具池、Prompt 组装

- `tools[]` **合并顺序**、MCP 排序等仍遵守 [L3_TOOL_POOL_AND_MCP_ASSEMBLY.md](./L3_TOOL_POOL_AND_MCP_ASSEMBLY.md)。  
- L4 可在 **system 侧** 注入「你必须先 Probe」的 **附加契约**，**不得** 破坏四大原语术语与 `sort_tools_by_id` 的稳定项策略。

---

## 4. 三步 SOP（Probe → Map → Execute）— 规范摘要

| 阶段 | 模型/组件允许的行为 | 禁止 |
|------|---------------------|------|
| **Probe** | 只读探查 schema / 工具元数据 | 生成带写入的 SQL、不可逆 MCP |
| **Map** | 引用 `db_semantics.yaml` 做字段级对齐；输出映射说明 | **一次性直出**最终执行体并声称完成 |
| **Execute** | 仅在 Critic `pass` 后生成最终 tool 调用 | 跳过 Critic 的写操作 |

**与 Cursor 工程规则**: 实现与 Code Review 须遵守 `.cursor/rules/090-jachin-l4-agent.mdc` 中的硬性约束。

---

## 5. 迁移与开关（平滑过渡）

| 阶段 | 内容 |
|------|------|
| **P0** | 文档 + 配置开关（如 `JACHIN_L4_AGENTIC=0/1`）+ 结构化日志标识 `[L4]` |
| **P1** | 仅 SQLite / 单一 MCP 家族接入 Planner + Critic；Experience RAG 只读注入 |
| **P2** | 多域 `db_semantics.yaml`、Critic 与 RunReport 深度集成、子 Agent 可选 offload |

**回滚**: 关闭开关即回退到纯 ReAct；四大原语文档 **无需回滚**。

---

## 6. 安全与合规要点

- 语义层与经验池 **默认脱敏**；日志中的 SQL/参数遵守现有 **深度日志** 与密钥脱敏策略。  
- Critic **不得** 执行工具，仅 **评审**；执行仍单点走 `run_tool` / registry（便于审计）。

---

## 7. 文档维护

- 本文件为 **L4 工作流架构基石**；实现落地后应在 PR 中同步更新「迁移阶段」表与代码入口链接（`run_agent`、中间件模块路径）。  
- 术语冲突时以 [FOUR_PRIMITIVES.md](../FOUR_PRIMITIVES.md) 为准；本文件只描述 **编排层** 增强。

---

## 附录 A：大纲速览（供评审与索引）

1. 架构目标（L3 ReAct → L4 Actor-Critic；非目标）  
2. 核心组件（Semantic Layer；SOP Planner；Critic；Experience RAG）  
3. 挂载点与兼容性（四大原语；`run_agent` / `_parse_action`；MCP 池）  
4. 三步 SOP（Probe / Map / Execute）  
5. 迁移与开关  
6. 安全与合规  
7. 文档维护  

（正文即完整规范；本附录仅便于检索。）
