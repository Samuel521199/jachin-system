# 04 — 文件结构 (The Purged Structure)

**文档类型**: 白皮书 · 文件结构  
**版本**: V2.3  
**更新日期**: 2026-06  
**基准**: [FILE_STRUCTURE.md](../FILE_STRUCTURE.md) · [ARCHITECTURE.md](../ARCHITECTURE.md)

> 完整树形说明以 `docs/FILE_STRUCTURE.md` 为准；本文为白皮书速查。

---

## 架构宪法

**严禁再引入**：`core/dapr/`、`core/ray_cluster/`、`core/memory/schema/`。

---

## 一、顶层目录

```text
jachin-system/
├── cloud/
│   ├── nexus/              # L1 平台 (Next.js + Drizzle + Auth.js + PostgreSQL)
│   └── jachin-downloads/   # 桌面安装包/更新分发（独立 Next 应用）
├── core/                   # L2 控制面 (FastAPI + SQLite + LanceDB)
├── l3_node/                # L3 执行引擎 (python -m l3_node)
├── clients/desktop/        # L3 桌面壳 (Tauri + React + Omni WS)
├── l3_client/              # 本地 MCP 服务（含 Memory Nexus backend）
├── skills_repo/            # Skill/MCP 开发源包
├── jachin-plugin-sdk/      # JPP Rust SDK
├── jachin-plugin-sdk-python/
├── tools/jachin-cli/       # 插件发布 CLI (publish/pack)
├── cli/jachin_cli/         # L2 配对/守护 CLI（与 publish CLI 分离）
├── common/                 # 共享 schema / protobuf
├── config/                 # MCP、db_semantics 模板
├── deploy/                 # L1/L2 部署 bundle
├── scripts/                # build_l3_sidecar、启动脚本
├── docs/                   # SSOT 文档（含本 whitepaper/）
└── .cursor/rules/          # AI 开发规则
```

**占位/非主路径**：`cloud/market/`、`cloud/relay/`、`edge/`（最小 stub）— 逻辑已收敛至 `cloud/nexus`。

---

## 二、Layer 1 — `cloud/nexus/`

```text
cloud/nexus/src/
├── app/api/v1/
│   ├── store/              # catalog, publish, subscribe, licenses, unpublish
│   ├── sync/manifest/
│   ├── organizations/      # create, members, invite, join, active-org, device-groups
│   ├── fleet/              # 舰队列表、deploy
│   ├── l2-bridge/          # mint, redeem（Web Bridge）
│   ├── l2-gateway/         # verify-credentials, workspace-members
│   ├── forge/publish/
│   ├── webhooks/telegram/
│   ├── agents/             # heartbeat, result, bind-im
│   ├── edge/               # heartbeat, resolve-org
│   ├── admin/              # 插件审核、desktop releases
│   └── desktop/releases/
├── app/console/            # workspace, fleet, l2-bridge
├── app/store/ · /forge/ · /market/
├── db/schema.ts            # Drizzle SSOT
└── lib/
    ├── with-org-role.ts    # 组织鉴权
    └── tenant.ts           # extractTenantId
```

**IAM 下放 L2**：子账号、L2 RBAC 见 `core/api/routes/v2_local_admin.py`。

---

## 三、Layer 2 — `core/`

```text
core/
├── main.py                 # FastAPI 入口
├── sync_daemon.py          # L1 manifest → inventory
├── inventory_scanner.py    # 侧载 .local_meta
├── policy_enforcer.py      # RBAC、断网降级
├── dream_weaver.py         # LanceDB 记忆聚类/去重
├── mcp_client.py           # MCPManager（L3 默认；L2 仅 JACHIN_L2_STDIO_MCP=1）
├── mcp_task_token.py       # 跨节点委托 Token
├── api/routes/
│   ├── v2_auth.py          # L3 注册/轮询/Key
│   ├── v2_admin.py         # 子账号、节点分配
│   ├── v2_inventory.py     # /skills, /download, /l3_mcps
│   ├── v2_mcp.py           # /tools, /invoke 委托
│   ├── v2_memory.py
│   └── v2_coordinate.py
├── embedding/              # 可插拔向量引擎（L2 Semantic Router）
├── vector_router.py
└── agent_loop.py           # ⚠️ legacy v8.0，非主执行路径
```

---

## 四、Layer 3 — `l3_node/` + `clients/desktop/`

```text
l3_node/
├── agent_core.py           # run_agent ReAct 主轴
├── llm_client.py           # LiteLLM 直连
├── bootstrap.py            # 配对、拉 Key、skill/mcp sync
├── ws_server.py            # :18981/sensory
├── intent_gateway/         # 意图分类、澄清、规划门禁 (~40+ 模块)
├── routing/                # direct_llm_bypass、output_format_signals
├── primitives/
│   ├── tools/              # loader、Native 注册
│   ├── mcp/                # mcp_sync、mcp_stdio_bootstrap
│   ├── skills/             # HR DAG、BI 等领域 Skill
│   ├── agent_tasks/        # background_task_service
│   └── multi_agent/        # fanout、verification（可选）
├── memory_nexus_bridge.py  # Memory Nexus Prompt 注入
├── channels/lark/          # IM 入站/出站
├── orchestration/          # skill_routing（L1 编排）
├── task_engine/            # DAG coordinator
├── jobs/                   # BI/PMO 调度
└── tools/                  # pmo_* 等领域工具

clients/desktop/
├── src/chat.tsx            # Omni 主界面
├── src/hooks/useSensoryWebSocket.ts
└── src-tauri/src/
    ├── l3_spawn.rs         # 启动 L3 子进程
    ├── commands/pairing.rs
    └── commands/skill_sync.rs
```

---

## 五、CLI 与 SDK

| 路径 | 用途 |
|------|------|
| `tools/jachin-cli/` | `jachin-cli publish` → L1 store |
| `cli/jachin_cli/` | L2 pair、daemon、status |
| `jachin-plugin-sdk-python/` | `@jachin_plugin` → Wasm |

---

## 六、本地数据 `~/.jachin/`

| 路径 | 说明 |
|------|------|
| `l2_control.db` | L2 SQLite |
| `nexus_config.json` | L2↔L1 凭证 |
| `l2_gateway_config.json` | L2↔L3 配对 |
| `inventory/skills/` · `inventory/mcps/` · `inventory/l3_mcps/` | L2 数字仓库 |
| `l3_skill_cache/` · `l3_mcp_cache/` | L3 运行时缓存 |
| `mcp_servers.json` | 用户 MCP 表（L3 Host 读取） |
| `palace_db/memory_nexus.sqlite3` | **L3 Memory Nexus** |
| `lancedb_data/` | L2 向量记忆（可选） |
| `workspace/` | task_plan、HR 宏图、`.background_tasks/zombie_tasks.json` |
| `runtime/node/` | 嵌入式 Node/npx（MCP 用） |

---

## 七、配置示例（节选）

`nexus_config.json` 仍承载 L2 侧 LLM/embedding 策略；L3 子进程通过 `load_l3_env_vars` 白名单注入 `.env`（如 `DASHSCOPE_API_KEY`、`LLM_MODEL`）。

区域 Key 与 SEA/CN 端点见 [DASHSCOPE_REGIONAL_KEYS.md](../DASHSCOPE_REGIONAL_KEYS.md)。

---

## 八、禁止与 Legacy

| 路径 | 状态 |
|------|------|
| `core/dapr/`、`core/ray_cluster/` | 已删除，禁止回归 |
| `core/agent_loop.py`、`core/daemon.py` | Legacy；桌面不依赖其为大脑 |
| `docs/INTELLIGENCE_UPGRADE_OVERVIEW.md` | 已移除 → 见 [AGI_OPTIMIZATION_ROADMAP.md](../AGI_OPTIMIZATION_ROADMAP.md) |
