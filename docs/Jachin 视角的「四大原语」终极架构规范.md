# Jachin 视角：四大原语架构规范（术语与边界）

**版本**: 2026-04-02  
**性质**: **单一事实来源（SSOT）** — 定义 Tools、MCP、Skills、Agent Tasks 四者在 Jachin 中的含义、边界与代码落点。  
**关联**: [ARCHITECTURE.md](./ARCHITECTURE.md)（三层架构）、[MCP_SPEC.md](./MCP_SPEC.md)、[SKILL_MD_SPEC.md](./SKILL_MD_SPEC.md)、[SKILL_MCP_FLOW_AND_RECENT_CHANGES.md](./SKILL_MCP_FLOW_AND_RECENT_CHANGES.md)、[L3_AGENT_CONTEXT_MEMORY_AND_PROMPT.md](./L3_AGENT_CONTEXT_MEMORY_AND_PROMPT.md)、[prompt_compose 实现](../l3_node/prompt_compose.py)。

---

## 一、总览表

| 原语 | 本质定位 | 状态与耗时（典型） | 在 Jachin 中的落点 |
|------|----------|-------------------|-------------------|
| **Tools** | 原子执行器（物理手脚） | 无会话状态 / 毫秒～秒级单次返回 | `core:*` Native、`jpp:*` Wasm 原子；进入 LLM `tools[]`，**排序靠前**以利前缀缓存 |
| **MCP** | 生态扩展（协议桥接） | 外部进程 / 秒级；单次调用仍属「一步」 | `mcp:*`，由 `MCPManager` / `mcp_registry` 托管；**排序靠后**；带 Locality（LOCAL_PINNED / ROUTABLE）等路由语义 |
| **Skills** | 领域知识卡（SOP / 声明式约束） | 非可执行代码本体；随 Prompt 或元数据加载 | `SKILL.md`、商城 Skill 包中的 **正文与 persona**、`plugin.json` 中的 **权限/依赖声明**；**不**等同于 `jpp` 二进制 |
| **Agent Tasks** | 协同子脑（多轮隔离运行时） | 有生命周期；分钟级可接受 | **同步**: `delegate` / `SubAgent`；**异步**: `core:submit_background_task`；**跨节点**: `coordinate` + L2 编排（见 Agent 文档） |

---

## 二、Tools（原子工具）

### 2.1 定义

**Tools** 是大模型 **单次 tool call 即可完成** 的原子能力：无独立多轮会话状态（同一调用内可读写文件，但不等于「子 Agent」）。

### 2.2 Jachin 包含两类

1. **Native Core**：源码内置，如 `core:fs_read`、`core:shell_exec`、`core:local_memory_search`、`core:submit_background_task`。实现主要在 `l3_node/primitives/tools/loader.py`（`run_tool`）、`core/native_tools.py`。
2. **Wasm 原子（JPP）**：商城/侧载插件，`jpp:com.xxx...`，沙箱内 `execute` / WASI。实现：`core/wasm_runner.py`，注册名由 `loader` 扫描 `primitives/tools/wasm_bundled/` 与 `l3_skill_cache/`。

### 2.3 架构铁律（与 Prompt 缓存）

- 合并进 LLM 的 **`tools[]` 描述串**中，**稳定、少变的条目应排在前面**（与 `prompt_compose` 的 stable sort 一致）。
- **Native / jpp 等与 MCP 混排时**：实践上 **MCP 工具名、数量更易变**，应置于 **列表后部**，减少热插拔对前缀缓存的扰动（对齐 Claude assembleToolPool 思路）。

### 2.4 易混澄清

- **商城里的「Skill 商品」** 若主体是 **Wasm**，在四大原语里其 **执行形态**归类为 **Tool（jpp 原子）**；同一 zip 里若还有 **SKILL.md**，其 **文字部分**归类为 **Skills（下节）**。

---

## 三、MCP（Model Context Protocol 扩展）

### 3.1 定义

**MCP** 指遵循 MCP 规范的 **外部服务进程**（stdio/SSE 等），**不是** Jachin 源码内建逻辑；由 L3（默认）或兼容路径托管。

### 3.2 Jachin 落点

- 配置：`~/.jachin/mcp_servers.json`、L2 下发的 `l3_mcp_cache` 等。
- 代码：`core/mcp_client.py`、`l3_node/primitives/mcp/registry.py`、`l3_node/primitives/mcp/mcp_stdio_bootstrap.py`（根目录同名 `.py` 为兼容 shim 时可忽略）。
- **Locality / 委托**：见 [ARCHITECTURE_L3_MCP_HOST_AND_L2_TASK_MANAGER.md](./ARCHITECTURE_L3_MCP_HOST_AND_L2_TASK_MANAGER.md)、[MCP_EXECUTION_MODEL.md](./MCP_EXECUTION_MODEL.md)。

### 3.3 与 Tools 的边界

- 对 **模型可见**时二者都是 `tools[]` 里的一项；区分维度是 **来源与运维模型**（内置/沙箱 atom **vs** 外部 MCP 进程）。
- Skill 包可通过 `plugin.json` **`required_mcps`** 声明依赖 MCP（见 [SKILL_MCP_UPLOAD_SPEC.md](./SKILL_MCP_UPLOAD_SPEC.md)）。

---

## 四、Skills（领域能力与知识包）

### 4.1 定义

**Skills** 是 **声明式** 的领域包：**Prompt/SOP、人设、允许使用的 Tools/MCP 白名单、依赖关系**，而不是「一段与 Native 同级的可执行宿主代码」。可执行部分若存在，应落在 **jpp（Tool）** 或 **MCP**，由 Skill **声明引用**。

### 4.2 Jachin 形态

| 形态 | 说明 |
|------|------|
| **SKILL.md** | `skills_repo/**/SKILL.md`，YAML Frontmatter + 自然语言 SOP；热加载与注入见 [SKILL_MD_SPEC.md](./SKILL_MD_SPEC.md) |
| **商城 Skill（plugin.json）** | L1 商品，`item_type: SKILL`；可含 Wasm **与** 文档；**依赖 MCP** 用 `required_mcps` |
| **能力域切片** | `docs/capability_domains/*.md` + `capability_catalog.py`，向 system 注入「谁会什么」（元层，仍属 Skills 知识侧） |

### 4.3 目标态与当前实现（诚实说明）

- **目标态（架构规范推荐）**：类似 Claude **SkillTool** —— 仅暴露 **`use_skill(skill_name=...)`** 等 **单一入口**，按需拉取 SOP，避免几十个轻量技能占满 `tools[]`。
- **当前 L3 实现**：多数场景下 **`jpp:*` / `core:*` / `mcp:*` 仍直接出现在 `tools[]`**；长 SOP（如招聘）可由 `_build_system_prompt` / 能力目录 **按域注入**。演进方向是收敛到「声明式 Skill 入口 + 动态加载」，与 [L3_LIMITATIONS_AND_REMEDIATION_ROADMAP.md](./L3_LIMITATIONS_AND_REMEDIATION_ROADMAP.md) 中 Prompt 治理一致。

---

## 五、Agent Tasks（多轮子运行时）

### 5.1 定义

**Agent Tasks** 是有 **独立消息上下文、迭代预算、可选 Token 顶** 的 **多轮 ReAct（或等价循环）** 实体；对主会话表现为 **一次工具调用**，内部却是 **子循环**。

### 5.2 Jachin 三种主要入口

| 类型 | 工具/机制 | 说明 |
|------|-----------|------|
| **同步子 Agent** | `delegate` → `SubAgent` → 嵌套 `run_agent` | 深度由 `max_delegate_depth` 限制；工作区/记忆可分片；见 `l3_node/agent_core.py` |
| **异步后台任务** | `core:submit_background_task` / `core:check_background_task` | 队列 + Worker + SQLite 持久化；见 [前台闲聊与后台重负荷任务的物理隔离与背压熔断.md](./前台闲聊与后台重负荷任务的物理隔离与背压熔断.md) |
| **L2 协同编排** | `coordinate` | 跨节点子任务，属 **Agent Task** 的「分布式」变体；见 Agent 文档与 L2 API |

### 5.3 与 Tools 的边界

- **Tool**：单次返回 Observation。  
- **Agent Task**：内部多轮 LLM + 多步工具，**对外**可能是一次 `delegate` 或一次「提交后台」的返回。

---

## 六、术语演进说明

仓库与 Cursor 规则已 **废弃「轨道 A / B / C」** 命名。工程与文档统一使用本文 **Tools / MCP / Skills / Agent Tasks** 四词。

- **MCP**：外部 MCP 进程与 `mcp:*` 工具（原所谓「轨道 A」）。
- **Skills**：`SKILL.md` 与声明式 SOP（原所谓「轨道 B」）。
- **Tools · jpp**：Wasm 原子与 Native `core:*`（原所谓「轨道 C」与 Core 合入 Tools 表述）。

执行面路由细则：`.cursor/rules/045-four-primitives-execution.mdc`。

---

## 七、修订记录

| 日期 | 说明 |
|------|------|
| 2026-04 | 初稿：对比 Claude Code / OpenClaw 叙述 |
| 2026-04-02 | 升格为 SSOT：去表情符号、补 Jachin 路径、商城 Skill 与 jpp 辨析、Agent Tasks 三入口、目标态 use_skill 与现状差异 |
| 2026-04-02 | 废弃「轨道 A/B/C」；全文与规则 **045-four-primitives-execution** 对齐 |
