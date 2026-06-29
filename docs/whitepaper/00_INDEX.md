# Jachin Nexus 白皮书 — 文档索引

**版本**: V2.3  
**更新日期**: 2026-06  
**核心基调**: L1 平台 / L2 零信任控制面 / L3 单体执行面（对标 OpenClaw）

> **现行实现 SSOT（优先阅读）**  
> - [ARCHITECTURE.md](../ARCHITECTURE.md) — 全局架构宪法  
> - [architecture/CURRENT_SYSTEM_ARCHITECTURE.md](../architecture/CURRENT_SYSTEM_ARCHITECTURE.md) — 代码同步的一页索引  
> - [architecture/JACHIN_HYBRID_AGENT_ARCHITECTURE.md](../architecture/JACHIN_HYBRID_AGENT_ARCHITECTURE.md) — L3 单主轴 ReAct + 语义层 / Critic / Experience RAG  
> - [FOUR_PRIMITIVES.md](../FOUR_PRIMITIVES.md) — 四大原语索引  

> **历史背景（非实现 SSOT）**  
> [ARCHITECTURE_V2_LAYER3_STANDALONE.md](../ARCHITECTURE_V2_LAYER3_STANDALONE.md) · [V2_ARCHITECTURE_DIAGRAM.md](../V2_ARCHITECTURE_DIAGRAM.md)

---

## ⚠️ 架构宪法 (The Constitution)

致所有阅读此文档的开发者与 AI 编程助手（如 Cursor）：

1. **已全面弃用**：Dapr、Ray 集群、L2 本地 PostgreSQL、复杂 Docker 编排。`core/dapr/`、`core/ray_cluster/`、`core/memory/schema/` **禁止再引入**。
2. **V2 执行模型**：**ReAct 与 stdio MCP 在 L3**（`l3_node/agent_core.py`）；L2 为控制面（子账号、权限、记忆、API Key、MCP **TaskManager 委托**）。L2 本机 stdio 仅 `JACHIN_L2_STDIO_MCP=1` 回滚。详见 [MCP_EXECUTION_MODEL.md](../MCP_EXECUTION_MODEL.md) v2.2。
3. **四大原语**（术语 SSOT：[Jachin 视角的「四大原语」终极架构规范.md](../Jachin%20视角的「四大原语」终极架构规范.md)）：**Tools**（`core:*` + `jpp:*`）· **MCP**（`mcp:*`）· **Skills**（`SKILL.md`）· **Agent Tasks**（delegate / 后台 / coordinate）。
4. **L3 记忆（现行）**：宿主跨会话记忆为 **Memory Nexus**（SQLite + FastEmbed，`~/.jachin/palace_db/memory_nexus.sqlite3`），**非** Chroma。L2 LanceDB + Dream Weaver 为**可选集中式**能力。SSOT：[architecture/MEMORY_NEXUS_L3.md](../architecture/MEMORY_NEXUS_L3.md)。
5. **组织即租户**：租户 = `organizations.id`；成员关系 **仅** 查 `organization_users`；**禁止**假设 `users` 表自带 `tenant_id`。V2.2 起注册 **不自动建组织**，须 `/console/workspace` 显式 onboarding。
6. **L3 轻量分发**：本体 Sidecar + L1→L2→`l3_skill_cache` / `l3_mcp_cache` 订阅制品。见 [L3_SLIM_DISTRIBUTION_AND_SUBSCRIBED_ARTIFACTS.md](../L3_SLIM_DISTRIBUTION_AND_SUBSCRIBED_ARTIFACTS.md)。
7. **Omni-Sensory Bus**：桌面 **WebSocket `ws://127.0.0.1:18981/sensory`**（`l3_node/ws_server.py`），非 L2 daemon 大脑。
8. **混合增强（非第五原语）**：意图网关（`l3_node/intent_gateway/`）、内联 Critic、Experience RAG 挂载在同一 `run_agent` 主轴。
9. **规划中 / 部分落地**：Jachin Mesh 长连、控制面/数据面 P2P（[10_CONTROL_DATA_PLANE.md](./10_CONTROL_DATA_PLANE.md)）、cron_thinker 生物钟、Edge Mesh Swarm。

---

## 文档列表

| 序号 | 文档 | 内容概要 | 与代码对齐度 |
|------|------|----------|--------------|
| 01 | [设计目的](./01_DESIGN_PURPOSE.md) | 解决什么问题、B2B/B2C、对标 OpenClaw | ✅ 已同步 V2.3 |
| 02 | [框架架构](./02_FRAMEWORK.md) | 三位一体 + 四大原语 + 记忆 + 感官 | ✅ 已同步 V2.3 |
| 03 | [业务流程](./03_WORKFLOW.md) | 配对、IM、梦境、舰队、Voice Wake | ✅ 已同步 V2.3 |
| 04 | [文件结构](./04_FILE_STRUCTURE.md) | 目录树、`l3_node/primitives/`、`~/.jachin/` | ✅ 已同步 V2.3 |
| 05 | [Layer 1 云端中枢](./05_LAYER1_NEXUS.md) | 商城、组织、舰队、Forge、IM 网关 | ✅ 已同步 V2.3 |
| 06 | [Layer 2 控制面](./06_LAYER2_EDGE.md) | 子账号、Key、记忆、MCP 委托 | ✅ 已同步 V2.3 |
| 07 | [Layer 3 执行节点](./07_LAYER3_TERMINAL.md) | ReAct、intent_gateway、WS、Memory Nexus | ✅ 已同步 V2.3 |
| 08 | [JPP 与技能生态](./08_JPP_SDK_AND_SKILLS.md) | 四大原语商品形态与发布 | ✅ 已同步 V2.3 |
| 09 | [去 BaaS 化战役](./09_DE_BAASIFICATION.md) | Auth.js、Drizzle、Redis/MinIO 路线图 | ⚠️ P0 已落地，P1+ 规划 |
| 10 | [控制面与数据面分离](./10_CONTROL_DATA_PLANE.md) | mDNS、WebRTC、信令分离 | ⚠️ 战略蓝图 |
| — | [可插拔向量引擎](./PLUGGABLE_VECTOR_ENGINE.md) | L2 Semantic Router Embedding | ✅ L2 侧 |
| — | [可插拔认知引擎](./PLUGGABLE_COGNITIVE_ENGINES.md) | LiteLLM、区域 Key、三档模型 | ✅ L3 侧 |
| — | [全息感官总线](./OMNI_SENSORY_BUS.md) | WS 18981、桌面 Omni | ✅ 已同步 V2.3 |
| — | [每日 BI 战报](../bi_daily_report/04_WHITEPAPER.md) | BI MCP + Skill + 调度 | 域专题 |
| — | [MCP 规范](../MCP_SPEC.md) | stdio MCP、npm 包名校验 | SSOT |
| — | [SKILL.md 规范](../SKILL_MD_SPEC.md) | 声明式技能 | SSOT |

---

## 阅读顺序建议

1. **新人**：`ARCHITECTURE.md` → `CURRENT_SYSTEM_ARCHITECTURE.md` → 本目录 01 → 02 → 05/06/07  
2. **L3 开发**：07 → `JACHIN_HYBRID_AGENT_ARCHITECTURE.md` → `L3_TOOL_POOL_AND_MCP_ASSEMBLY.md`  
3. **L1 开发**：05 → `ARCHITECTURE_L1_WORKSPACE_L2_GATEWAY_L3.md` → `cloud/nexus/src/db/schema.ts`  
4. **历史考古**：`ARCHITECTURE_V2_LAYER3_STANDALONE.md`（背景阅读，**勿**当作实现 SSOT）
