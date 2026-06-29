# 02 — 框架架构 (The Trinity + Neural Bus)

**文档类型**: 白皮书 · 框架架构  
**版本**: V2.3  
**更新日期**: 2026-06  
**基准**: [ARCHITECTURE.md](../ARCHITECTURE.md) · [CURRENT_SYSTEM_ARCHITECTURE.md](../architecture/CURRENT_SYSTEM_ARCHITECTURE.md)

---

## 〇、Platform First（平台优先）

**Layer 1 默认为官方托管的多租户 SaaS**（`cloud/nexus`）。用户注册后在控制台创建/加入 **工作区（组织）**，再在企业内网部署 L2/L3 并绑定网关。

**私有化 Layer 1** 仅作政企/金融 fallback，非默认设计起点。

---

## 一、三位一体架构

```text
Layer 1 (cloud/nexus)  ↔  Layer 2 (core/)  ↔  Layer 3 (l3_node/ + clients/desktop)

L1 — 平台
  商城 catalog/publish/subscribe/licenses · sync/manifest · 组织/舰队/Forge
  Auth.js + Drizzle + PostgreSQL · 不存 L3 隐私记忆

L2 — 控制面 + 数字仓库
  子账号 · RBAC · API Key 保险箱（密文下发 L3）· inventory · MCP TaskManager 委托
  LanceDB 可选集中记忆 · Dream Weaver · 不代理 L3 推理

L3 — 执行面
  run_agent ReAct · stdio MCP Host · Wasm Tools · SKILL.md 注入
  Memory Nexus（SQLite+FastEmbed）· ws://127.0.0.1:18981/sensory
  Tauri 桌面 Omni · Lark/Telegram IM 通道
```

**配对边界**：L1↔L2 见 [L1_L2_PAIRING_AND_WEB_BRIDGE.md](../L1_L2_PAIRING_AND_WEB_BRIDGE.md)；L2↔L3 见 [PAIRING_PROTOCOL_SPEC.md](../PAIRING_PROTOCOL_SPEC.md)。

---

## 二、四大原语执行面（L3）

| 原语 | 形态 | 代码锚点 |
|------|------|----------|
| **Tools** | `core:*` Native、`jpp:*` Wasm | `l3_node/primitives/tools/` |
| **MCP** | `mcp:*` stdio | `core/mcp_client.py`（L3 默认 Host） |
| **Skills** | `SKILL.md`、能力域 | `skills_repo/`、`docs/capability_domains/` |
| **Agent Tasks** | 多轮运行时 | `delegate`、`core:submit_background_task`、`coordinate` |

**混合增强（非第五原语）**：`intent_gateway/`、语义层、`db_semantics.yaml`、内联 Critic、Experience RAG — 均挂载同一 `run_agent` 主轴。SSOT：[JACHIN_HYBRID_AGENT_ARCHITECTURE.md](../architecture/JACHIN_HYBRID_AGENT_ARCHITECTURE.md)。

---

## 三、记忆体系

### 3.1 L3 Memory Nexus（宿主默认）

- **存储**：`~/.jachin/palace_db/memory_nexus.sqlite3`（SQLite + FastEmbed 本地 embedding）。
- **实现**：`l3_client/local_mcps/jachin_memory_nexus/memory_backend.py`；桥接 `l3_node/memory_nexus_bridge.py`。
- **工具**：`core:local_memory_search`、`core:local_memory_append`；Prompt 注入「系统近期核心记忆」。
- SSOT：[MEMORY_NEXUS_L3.md](../architecture/MEMORY_NEXUS_L3.md)

### 3.2 L2 可选集中记忆

- LanceDB（`~/.jachin/lancedb_data/`）+ `core/dream_weaver.py` 聚类/去重/融合。
- API：`POST /api/v2/memory/sync`、`GET /api/v2/memory/search`（多租户 namespace）。
- L3 **默认不依赖** L2 记忆路径运行；多节点/审计场景可选用。

### 3.3 自我修复

ReAct 捕获工具异常 → Observation 反馈 → 可选写入 Memory Nexus 或 L2 core_memory 规则。

---

## 四、全息感知（Omni-Sensory Bus）

**现行实现**：L3 `ws_server.py`（端口 **18981**）+ 桌面 `useSensoryWebSocket.ts`。

- **输入**：桌面 chat、Voice STT、Lark webhook → 归一化为 Sensory 消息 → `run_agent`。
- **输出**：ReAct 步骤、流式 chunk、HITL、后台任务事件（含 `zombie_tasks_pending`）→ WS 广播。
- 详见 [OMNI_SENSORY_BUS.md](./OMNI_SENSORY_BUS.md)。

**Legacy 说明**：`core/event_bus.py` + `core/daemon.py` 仍保留 v8.0 感官总线代码，**非**桌面 Omni 主路径。

---

## 五、通信拓扑（现行 vs 规划）

| 链路 | 现行 | 规划 |
|------|------|------|
| 桌面 ↔ L3 | `localhost:18981` WebSocket | 同左 |
| L3 ↔ LLM | 直连 DashScope/OpenAI 等（LiteLLM） | 同左 |
| L2 ↔ L3 | HTTP API + MCP Pull/委托 | 同左 |
| L1 ↔ L2 | manifest 同步、Web Bridge、CLI 配对 | Jachin Mesh WS 长连 |
| L1 ↔ L3（IM） | Webhook → 队列 → L2/L3 拉取 | WS 推送 + 可选 P2P |
| 局域网 | — | mDNS + 内网直连（[10_CONTROL_DATA_PLANE.md](./10_CONTROL_DATA_PLANE.md)） |

---

## 六、废弃清单

❌ Dapr & Ray Cluster  
❌ L2 作为主 ReAct 执行引擎（`core/agent_loop.py` 等为 legacy）  
❌ L3 宿主记忆 = Chroma（已改为 SQLite + FastEmbed）  
❌ 「轨道 A/B/C」命名（统一四大原语）  
❌ 注册自动创建个人组织（V2.2 起显式 workspace onboarding）
