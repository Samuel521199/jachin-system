# Jachin 云边协同数字发行操作系统 — 架构规范

**版本**: V2 (2026-04)  
**状态**: 当前实现基准  
**定位**: 一店一库、**四大原语**执行模型、三层云边、三大极简流程  

**现行实现一页索引**（MCP 守卫、stdio 噪声过滤、后台 zombie、桌面 WS）：[architecture/CURRENT_SYSTEM_ARCHITECTURE.md](./architecture/CURRENT_SYSTEM_ARCHITECTURE.md)

**架构全景（总—分，含流程图/时序图）**：[arch/README.md](./arch/README.md)（01~07 分册；原 `JACHIN_FULL_ARCHITECTURE_2026.md` 正文已迁入此目录）

---

## 一、核心范式

### 1.1 一店一库，云边分治

| 层级 | 定位 | 职责 |
|------|------|------|
| **L1 全球商城** | 商业收银台 | 展示 Skill/MCP、处理订阅、颁发 License；不接触企业明文密码，不提供推理算力 |
| **L2 本地数字仓库** | 企业数字金库 | L1 在企业内网的物理投影；静默同步已购订单、下载囤积包、向 L3 下发权限与 Skill；MCP 清单同步与 **TaskManager 式委托**（**默认**不在 L2 起 stdio MCP 子进程；`JACHIN_L2_STDIO_MCP=1` 可回滚） |

### 1.2 商城商品形态与执行（映射四大原语）

| 商品形态 | 流转 | 执行策略 |
|----------|------|----------|
| **Skill (.wasm)** | 轻量，L2 发放给 L3，员工电脑沙箱运行 | L3 本地执行 |
| **MCP** | L2 同步到 inventory；L3_LOCAL 包进 `l3_mcp_cache`，stdio 由 **L3 内嵌 MCPManager** 读 `mcp_servers.json` / `inventory/mcps` | **L3 执行**；L2 **GET /tools** 聚合与 **invoke 委托**（Pull、HTTP 须 Task Token）；详见 MCP_EXECUTION_MODEL v2.2 |

详见 [ARCHITECTURE_L3_MCP_HOST_AND_L2_TASK_MANAGER.md](ARCHITECTURE_L3_MCP_HOST_AND_L2_TASK_MANAGER.md)（规格）、[MCP_EXECUTION_MODEL.md](MCP_EXECUTION_MODEL.md)（目标 vs 现状）。

### 1.3 四大原语（术语 SSOT）

讨论 **工具、技能、子 Agent、后台任务** 时，以 **[Jachin 视角的「四大原语」终极架构规范.md](./Jachin%20视角的「四大原语」终极架构规范.md)** 为准：

- **Tools**：`core:*` Native、`jpp:*` Wasm 原子，单次 tool 调用级。
- **MCP**：`mcp:*` 外部 MCP 进程，协议扩展。
- **Skills**：`SKILL.md`、Skill 包声明与 SOP、能力域文档；**非** jpp 二进制本体。
- **Agent Tasks**：`delegate` / `core:submit_background_task` / `coordinate` 等多轮子运行时。

与上表「Skill (.wasm) / MCP」**商品形态**的关系：Wasm 商品在执行语义上主要属于 **Tools（jpp）**；商城元数据与 SKILL 正文仍属 **Skills**。索引：[FOUR_PRIMITIVES.md](./FOUR_PRIMITIVES.md)。

### 1.4 L3 执行主轴（混合智能体，非「对等多 Agent」默认）

L3 默认是 **单进程、单 `run_agent` ReAct 主循环**；在此主轴上挂载 **意图网关 / 环境嗅探、`db_semantics.yaml` 语义层、system 内 SOP、工具执行前内联 Critic、Experience RAG 检索** 等增强。**不是**把 Critic 当作与主 Agent 并行的独立运行时。详见 **[architecture/JACHIN_HYBRID_AGENT_ARCHITECTURE.md](./architecture/JACHIN_HYBRID_AGENT_ARCHITECTURE.md)**。

| 可见性 | 流转 |
|--------|------|
| **PUBLIC** | L1 审核 → 购买 → L2 同步 → L3 执行 |
| **PRIVATE** | L1 仅登记；实体侧载到 L2，断网隔离 |

---

## 二、三层架构

```
┌─────────────────────────────────────────────────────────────────┐
│  L1 (cloud/nexus) — 平台                                         │
│  用户主账号、商城、manifest、licenses；IAM 已下放 L2              │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│  L2 (core/) — 控制面 + 数字仓库                                   │
│  子账号、权限、API Key 保险箱、记忆、L3 调度、MCP、inventory       │
│  不代理推理                                                       │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│  L3 (l3_node/ + clients/desktop) — 执行面                         │
│  单主轴 ReAct（run_agent）+ MCP/Skill；语义层/内联 Critic/经验 RAG；本地记忆；持密文 Key 直连 LLM │
└─────────────────────────────────────────────────────────────────┘
```

---

## 三、关键组件

| 组件 | 路径 | 说明 |
|------|------|------|
| L1 商城 | `cloud/nexus/src/app/api/v1/store/` | catalog、publish、subscribe、licenses |
| L1 同步 | `cloud/nexus/src/app/api/v1/sync/` | manifest |
| L2 同步 | `core/sync_daemon.py` | CloudSyncDaemon：manifest → 下载；RBAC 本地管理 |
| L2 仓库 | `core/inventory_scanner.py` | 侧载扫描、`.local_meta` |
| L2 权限 | `core/policy_enforcer.py` | RBAC、断网降级、role_permissions |
| L2 清单 | `core/api/routes/v2_inventory.py` | `/skills`、`/download`、`/l3_mcps`、`/l3_mcps/{id}/download`（需 X-Sub-Account-Id） |
| L2 MCP / 任务 | `core/api/routes/v2_mcp.py` | **默认**仅委托（Redis Pull + HTTP 回退）；`GET /tools` 聚合 Redis；`JACHIN_L2_STDIO_MCP=1` 时合并本机 stdio（见 MCP_EXECUTION_MODEL） |
| L3 同步 | `clients/desktop/src-tauri/src/commands/skill_sync.rs`、`l3_node/mcp_sync.py` | 从 L2 拉取技能与 MCP |
| L3 Agent | `l3_node/agent_core.py` | ReAct、工具调用；前台同步超时、工具后预取、规划门禁见 [前台闲聊与后台重负荷任务的物理隔离与背压熔断.md](./前台闲聊与后台重负荷任务的物理隔离与背压熔断.md) |
| L3 后台任务 | `l3_node/primitives/agent_tasks/background_task_service.py`、`l3_node/l3_event_bus.py` | `core:submit_background_task` / `check` / **`check_interrupted_tasks`**、队列 Worker、`zombie_tasks.json`、WebSocket `subscribe_background_tasks`（含 `zombie_tasks_pending`）；详见 [architecture/CURRENT_SYSTEM_ARCHITECTURE.md](./architecture/CURRENT_SYSTEM_ARCHITECTURE.md) §5 |
| 跨会话规划文件 | `l3_node/task_planning.py` | `~/.jachin/workspace/task_plan.md`、`progress.md`、`findings.md`；Prompt 注入「继续执行计划」 |
| HR 招聘（DAG + 物理进度） | `l3_node/primitives/skills/hr_recruitment_dag.py` + `skills_repo/plugin/com.jachin.hr.recruitment/` | `hr_plan_init` → `harvest_loop` → 可选分析；`STOP_HARVEST`；`~/.jachin/workspace/hr_recruitment/` 下宏图与战况 — 详见 [HR_RECRUITMENT.md](HR_RECRUITMENT.md) |
| 智能化与编排 | `docs/arch/README.md`（**架构全景 2026**）、`docs/JACHIN_VS_OPENCLAW_INTELLIGENCE_ANALYSIS.md`、`docs/INTELLIGENCE_UPGRADE_OVERVIEW.md`、`docs/ORCHESTRATION_ARCHITECTURE.md`（**领域编排**） | OpenClaw 对比、记忆/梦境/规划、Skill 路由 / 领域子图 |

---

## 四、数据流

### 4.1 企业消费者（一键装配）

1. L1 订阅 → `user_licenses`
2. L2 `poll_manifest` → `download_and_extract` → `~/.jachin/inventory/`
3. L2 本地 `role_permissions`（RBAC 由 L2 管理，不依赖 L1；见 `v2_local_admin`）
4. L3 `perform_startup_sync` → `GET /skills`（带 X-Sub-Account-Id）→ `GET /download` → `~/.jachin/l3_skill_cache/`
5. L3 `sync_mcps_from_l2` → `GET /l3_mcps` → `GET /l3_mcps/{id}/download` → `~/.jachin/l3_mcp_cache/`（mcp_registry 动态加载）

### 4.2 内网极客（侧载）

1. 将 MCP/Wasm 放入 `~/.jachin/inventory/`
2. L2 `scan_local_*` → 生成 `.local_meta`
3. `POST /inventory/reload` 热重载

### 4.3 生态创作者（发布）

1. `jachin-cli publish` → L1 `POST /store/publish`
2. PRIVATE：`shadow_only=true`，仅 metadata
3. PUBLIC：完整包 → `status=pending` → Admin 审核

---

## 五、存储

| 存储 | 用途 |
|------|------|
|  PostgreSQL | L1：plugins_registry、user_licenses（IAM 已下放 L2） |
| SQLite | L2：sub_accounts、role_permissions、api_keys_vault、l3_nodes |
| LanceDB | L2：向量记忆 |
| 文件系统 | `~/.jachin/inventory/`、`~/.jachin/l3_skill_cache/`、`~/.jachin/l3_mcp_cache/`、`~/.jachin/l2_control.db`、`~/.jachin/workspace/`（任务规划、HR 招聘数据，见 [HR_RECRUITMENT.md](HR_RECRUITMENT.md)） |

---

## 六、禁止项（已废弃）

- `core/dapr/`、`core/ray_cluster/`、`core/memory/schema/`（已移除）
- Dapr、Ray、PostgreSQL、独立托管向量服务作为 L2 主存储（L2 现为 SQLite + LanceDB）

---

## 七、附录：关键 API

| 接口 | 说明 |
|------|------|
| `GET /api/v1/store/catalog` | 公开商品 |
| `GET /api/v1/sync/manifest` | 租户已购清单 |
| `POST /api/v1/store/subscribe` | 订阅 |
| `POST /api/v2/local-admin/roles/assign` | L2 本地 RBAC 角色权限（L2 数据主权） |
| `GET /api/v2/inventory/skills` | 技能清单（需 X-Sub-Account-Id） |
| `GET /api/v2/inventory/skills/{id}/download` | 下载（需 X-Sub-Account-Id） |
| `GET /api/v2/inventory/l3_mcps` | L3_LOCAL MCP 清单（供 L3 mcp_sync 拉取） |
| `GET /api/v2/inventory/l3_mcps/{id}/download` | 下载 L3_LOCAL MCP 包 |
| `POST /api/v2/mcp/invoke` | L3 缺工具时入口；L2 **TaskManager** 委托（Pull / HTTP）；仅回滚标志下 L2 本机 stdio（见 MCP_EXECUTION_MODEL §三） |
