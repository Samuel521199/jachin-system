# Jachin 现行系统架构总览（2026-04）

**定位**：以**当前仓库实现**为准的一页式索引；细节仍以各专题 SSOT 文档为准。  
**非替代**：不取代 [../ARCHITECTURE.md](../ARCHITECTURE.md) 的全局规范、亦不取代四大原语全文。

---

## 1. 分层与进程

| 层级 | 角色 | 典型路径 / 进程 |
|------|------|-----------------|
| **L1** | 商城、订阅、许可证 | `cloud/nexus/` |
| **L2** | 企业控制面、数字仓库、MCP 委托 / TaskManager、**默认不跑本机 stdio MCP** | `core/` |
| **L3** | 本机执行面：ReAct、`run_tool`、stdio MCP Host、WebSocket Sensory | `l3_node/`、`python -m l3_node` |
| **Desktop** | Tauri 壳 + 前端 Omni；**L3 WebSocket 客户端**（非 L2） | `clients/desktop/` |

**配对关系**：L2↔L3 零信任配对见 [../PAIRING_PROTOCOL_SPEC.md](../PAIRING_PROTOCOL_SPEC.md)；桌面可仅连本机 L3（`ws://127.0.0.1:18981/sensory`）。

---

## 2. 四大原语（术语）

**全文 SSOT**：[../Jachin 视角的「四大原语」终极架构规范.md](../Jachin%20视角的「四大原语」终极架构规范.md) · 索引：[../FOUR_PRIMITIVES.md](../FOUR_PRIMITIVES.md)

- **Tools**：`core:*` Native、`jpp:*` Wasm；单次 tool 调用。
- **MCP**：`mcp:*`，stdio 子进程，由 L3 `core/mcp_client.py`（MCPManager）托管。
- **Skills**：`SKILL.md`、声明式 SOP / 白名单；非可执行代码本体。
- **Agent Tasks**：多轮运行时 — `delegate`、`core:submit_background_task`、`coordinate` 等。

**混合增强（非第五原语）**：`architecture/JACHIN_HYBRID_AGENT_ARCHITECTURE.md` — 单主轴 ReAct 上挂载语义层、内联 Critic、Experience RAG。

---

## 3. L3 执行主轴

| 能力 | 实现要点 |
|------|-----------|
| 主循环 | `l3_node/agent_core.py` — `run_agent`、ReAct、system prompt（含工具表、业务注入）。 |
| 工具加载 | `l3_node/primitives/tools/loader.py` — Native + MCP 合并、`build_tools_description`。 |
| 直连 LLM 旁路 | `l3_node/routing/output_format_signals.py` — `direct_llm_bypass` 时可能无完整工具表。 |
| 前台同步超时 | `l3_node/foreground_tool_policy.py`；豁免与长耗时改走后台。 |

---

## 4. MCP（L3 stdio Host）

| 主题 | 说明 |
|------|------|
| 配置来源 | `~/.jachin/mcp_servers.json`、`~/.jachin/inventory/mcps/`；占位符解析见 `core/mcp_embedded_runtime.py`。 |
| 连接 | `core/mcp_client.py` — `MCPManager`、stdio、`StdioServerParameters`。 |
| **stdout 噪声过滤** | `core/mcp_stdio_noise_filter.py` — 在 import 时替换官方 `stdio_client`：跳过**非 JSON-RPC** 行（如 npx / dotenv 注入），避免 `JSONRPCMessage` 解析崩溃。 |
| **嵌入式 Node / npx** | `core/mcp_embedded_runtime.py` — `~/.jachin/runtime/node/`（或 exe 旁 `runtime/node/`）放置官方 Node 便携包（须含 **npx.cmd**）；裸 `npx`/`npm` 与占位符 `__JACHIN_MCP_NPX__` 解析到该目录；详见 [../L3_EMBEDDED_RUNTIME.md](../L3_EMBEDDED_RUNTIME.md)。 |
| **路径预检** | `core/inventory_scanner.py` — `@modelcontextprotocol/server-filesystem` 不存在根目录时跳过；`mcp_server_git` 的 `--repository` 须为**含 `.git` 的工作区**，否则跳过（避免子进程退出 → `Connection closed`）。 |
| L2 委托模型 | `docs/MCP_EXECUTION_MODEL.md`、`docs/ARCHITECTURE_L3_MCP_HOST_AND_L2_TASK_MANAGER.md` |

---

## 5. 前台 / 后台任务与断电遗留

**专题 SSOT**：[../前台闲聊与后台重负荷任务的物理隔离与背压熔断.md](../前台闲聊与后台重负荷任务的物理隔离与背压熔断.md)

| 概念 | 实现 |
|------|------|
| 队列 / Worker | `l3_node/primitives/agent_tasks/background_task_service.py`（兼容入口 `l3_node/background_task_service.py`） |
| 启动对账 | `reconcile_stale_background_tasks_on_startup`：`running`/`queued` → `interrupted` |
| **僵尸摘要** | `~/.jachin/workspace/.background_tasks/zombie_tasks.json` — 对账或优雅停机时追加 `task_id` / `task_prompt` 等 |
| 工具 | `core:submit_background_task`、`core:check_background_task`、**`core:check_interrupted_tasks`**（读 zombie 列表；可选 `consume` 清空） |
| Prompt 晨会 | `l3_node/agent_core.py` — 新会话建议模型调用 `core:check_interrupted_tasks` 并询问是否重投 |
| 事件总线 | `l3_node/l3_event_bus.py` — 订阅者：`subscribe_background_tasks` |

**WebSocket**（`l3_node/ws_server.py`）：

- 客户端发送 `subscribe_background_tasks` 后注册订阅；**成功订阅后**若磁盘上仍有 zombie 条目，会 **补推** `event: zombie_tasks_pending`（避免「L3 先启动广播、桌面后连」收不到）。
- 启动对账时若存在 zombie，也会 `broadcast_background_task_event` 推送同结构事件。

---

## 6. 桌面客户端（Omni）

| 主题 | 说明 |
|------|------|
| 连接 | `clients/desktop/src/hooks/useSensoryWebSocket.ts` — `ws://…:18981/sensory`，`onopen` 发送 `manifest` 与 **`subscribe_background_tasks`**。 |
| 僵尸 UI | 收到 `zombie_tasks_pending` 时展示横幅（条数角标 + 摘要）、「填入追问指令」、陪伴模式下 **哨兵 Toast**（`lib/jachinSentryNotify.ts`）。 |
| 主窗体 | `clients/desktop/src/chat.tsx`；旧式 `ChatPanel.tsx` 同步能力。 |

---

## 7. 文档索引（维护约定）

| 类型 | 文档 |
|------|------|
| **全局架构规范** | [../ARCHITECTURE.md](../ARCHITECTURE.md) |
| **架构全景（总—分，含工程图）** | [../arch/README.md](../arch/README.md) — 01~07 分册 |
| **本文（现行快照）** | `docs/architecture/CURRENT_SYSTEM_ARCHITECTURE.md` |
| **L3 混合智能体** | `JACHIN_HYBRID_AGENT_ARCHITECTURE.md`（本目录） |
| **工具池与 MCP 组装** | `L3_TOOL_POOL_AND_MCP_ASSEMBLY.md` |
| **MCP 执行模型** | [../MCP_EXECUTION_MODEL.md](../MCP_EXECUTION_MODEL.md) |
| **执行韧性** | [../JACHIN_EXECUTION_RESILIENCE_CONTRACT.md](../JACHIN_EXECUTION_RESILIENCE_CONTRACT.md)（见 `.cursor/rules/080`） |
| **历史设计稿** | `../ARCHITECTURE_V2_LAYER3_STANDALONE.md` — 保留作背景阅读；**实现以 ARCHITECTURE.md + 代码为准** |
| **已归档索引** | [../JACHIN_FULL_ARCHITECTURE_2026.md](../JACHIN_FULL_ARCHITECTURE_2026.md) → 重定向至 `docs/arch/` |

新增跨领域行为（如新 MCP 守卫、新 WS 事件）时：**更新本文件一节 + `docs/arch/` 对应分册或专题 SSOT**，避免再写第三份重复说明。
