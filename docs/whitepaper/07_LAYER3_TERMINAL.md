# 07 — Layer 3: 单体执行节点 (V2)

**文档类型**: 白皮书 · Layer 3 详细说明  
**版本**: V2.3  
**更新日期**: 2026-06  
**基准**: [CURRENT_SYSTEM_ARCHITECTURE.md](../architecture/CURRENT_SYSTEM_ARCHITECTURE.md) · [JACHIN_HYBRID_AGENT_ARCHITECTURE.md](../architecture/JACHIN_HYBRID_AGENT_ARCHITECTURE.md)

---

## 一、定位与职责

**Layer 3 = 本机 OpenClaw 级执行面**：ReAct、MCP、Skill、记忆、IM、桌面 Omni。

| 维度 | 说明 |
|------|------|
| **入口** | `python -m l3_node`；Tauri `l3_spawn.rs` 拉起 |
| **主循环** | `agent_core.run_agent` — 单主轴 ReAct |
| **LLM** | LiteLLM 直连（持 L2 下发的密文 Key） |
| **工具** | `core:*` Native + `mcp:*` stdio + `jpp:*` Wasm |
| **记忆** | Memory Nexus（SQLite + FastEmbed） |
| **传输** | `ws://127.0.0.1:18981/sensory` |
| **制品** | `l3_skill_cache/`、`l3_mcp_cache/` 从 L2 同步 |

---

## 二、与 v8.0 的差异

| 维度 | v8.0 | V2 现行 |
|------|------|---------|
| L3 角色 | 偏 UI/HITL 外壳 | **完整执行节点** |
| 推理 | 发往 L2 | **L3 直连 API** |
| 大脑位置 | `core/agent_loop` | **`l3_node/agent_core`** |
| 记忆 | Chroma palace_db | **SQLite memory_nexus.sqlite3** |

---

## 三、进程与入口

```bash
python -m l3_node          # 或桌面 Tauri 自动 spawn
```

**Bootstrap 顺序**（`bootstrap.py`）：

1. 读取 L2 网关配置 / 配对状态  
2. 拉取 API Key、skill_sync、mcp_sync  
3. `mcp_stdio_bootstrap` 注册 MCP  
4. 启动 `ws_server`（18981）、可选 HTTP（后台任务事件）

---

## 四、代码结构（现行）

```text
l3_node/
├── agent_core.py              # run_agent、system prompt、工具执行
├── llm_client.py              # LiteLLM、区域 Key
├── critic_agent.py            # 内联 Critic（L4 增强）
├── intent_gateway/            # 意图分类、澄清、规划门禁、JIT binding
├── routing/                   # direct_llm_bypass、heuristic_tool_need
├── primitives/
│   ├── tools/loader.py        # assemble_tool_pool、build_tools_description
│   ├── mcp/                   # sync、stdio bootstrap
│   ├── skills/                # HR DAG、BI 等领域
│   ├── agent_tasks/           # 后台任务 Worker、zombie 对账
│   └── multi_agent/           # 可选 fanout/verify
├── memory_nexus_bridge.py     # Prompt 记忆块、回合末 commit
├── local_memory_search.py     # core:local_memory_*
├── ws_server.py               # Sensory WebSocket
├── channels/lark/             # 飞书 IM（L3 原生，非订阅包）
├── orchestration/             # skill_routing
├── task_engine/               # DAG、task_plan 文件
├── foreground_tool_policy.py  # 前台超时
└── bootstrap.py
```

**桌面**：`clients/desktop/` — `useSensoryWebSocket.ts`、`chat.tsx`、配对/同步 Tauri commands。

---

## 五、四大原语在 L3 的落地

| 原语 | L3 实现 |
|------|---------|
| Tools | `primitives/tools/`、`skills/native_tools/`、`core/wasm_runner.py`（jpp） |
| MCP | `core/mcp_client.MCPManager` + `mcp_servers.json` + cache |
| Skills | Prompt 注入 SKILL.md、能力域、`capability_catalog.py` |
| Agent Tasks | `delegate`、`submit_background_task`、`coordinate` |

工具池组装 SSOT：[L3_TOOL_POOL_AND_MCP_ASSEMBLY.md](../architecture/L3_TOOL_POOL_AND_MCP_ASSEMBLY.md)

---

## 六、混合智能体（L4 增强，非第五原语）

在同一 `run_agent` 上挂载：

- **意图网关** — 分类、模糊澄清（`085-l3-fuzzy-intent-clarification`）
- **语义层** — `db_semantics.yaml`、Probe → Map → Execute
- **内联 Critic** — 工具执行前校验（复杂 SQL/MCP）
- **Experience RAG** — `experience_memory.py` JSONL 检索

SSOT：[JACHIN_HYBRID_AGENT_ARCHITECTURE.md](../architecture/JACHIN_HYBRID_AGENT_ARCHITECTURE.md)

---

## 七、Memory Nexus

- 文件：`~/.jachin/palace_db/memory_nexus.sqlite3`
- Backend：`l3_client/local_mcps/jachin_memory_nexus/memory_backend.py`
- 工具：`core:local_memory_search`、`core:local_memory_append`
- **不**默认请求 L2 `/memory/search`

SSOT：[MEMORY_NEXUS_L3.md](../architecture/MEMORY_NEXUS_L3.md)

---

## 八、前台 / 后台隔离

| 机制 | 说明 |
|------|------|
| 前台 | `run_agent` 同步；`foreground_tool_policy` 超时 |
| 后台 | `core:submit_background_task` + Worker |
| Zombie | 对账 → `zombie_tasks.json`；WS `zombie_tasks_pending` |
| 恢复 | `core:check_interrupted_tasks` |

SSOT：[前台闲聊与后台重负荷任务的物理隔离与背压熔断.md](../前台闲聊与后台重负荷任务的物理隔离与背压熔断.md)

---

## 九、轻量分发（Slim L3）

- Sidecar 打包：`scripts/build_l3_sidecar.py`
- 业务 MCP/Skill 经 L1 订阅 → L2 inventory → L3 cache
- 见 [L3_SLIM_DISTRIBUTION_AND_SUBSCRIBED_ARTIFACTS.md](../L3_SLIM_DISTRIBUTION_AND_SUBSCRIBED_ARTIFACTS.md)

---

## 十、参考

- [PAIRING_PROTOCOL_SPEC.md](../PAIRING_PROTOCOL_SPEC.md)
- [L3_CAPABILITY_CATALOG.md](../L3_CAPABILITY_CATALOG.md)
- [MCP_SPEC.md](../MCP_SPEC.md)
- [l3_node/README.md](../../l3_node/README.md)（若存在）
