# Jachin 项目文件结构

**版本**: V2 (2026-03)
**基准**: [ARCHITECTURE.md](./ARCHITECTURE.md)

---

## 顶层目录

```
jachin-system/
├── cloud/nexus/          # L1 平台 (Next.js + Drizzle + Auth.js)
├── core/                 # L2 控制面 (Python + FastAPI)
├── clients/desktop/      # L3 桌面端 (Tauri + React)
├── l3_node/              # L3 执行引擎 (Agent + Skill)
├── tools/jachin-cli/     # 插件发布 CLI
├── scripts/              # 启动与运维脚本
├── docs/                 # 文档
└── .cursor/rules/        # Cursor AI 规则
```

---

## L1 (cloud/nexus/)

```
cloud/nexus/
├── src/
│   ├── app/api/v1/       # API 路由
│   │   ├── store/        # catalog, publish, subscribe, licenses
│   │   ├── sync/         # manifest
│   │   └── ...           # IAM 已下放 L2，见 core/v2_local_admin
│   ├── app/dashboard/    # Admin 审核（插件审核）；IAM 在 L2
│   ├── app/store/        # 商城页
│   ├── db/               # Drizzle schema
│   └── lib/              # tenant, admin-auth, ratelimit
├── drizzle/              # 迁移
└── package.json
```

---

## L2 (core/)

```
core/
├── api/routes/           # V2 API
│   ├── v2_auth.py        # sync, poll, keys
│   ├── v2_admin.py       # 子账号、Key、节点分配
│   ├── v2_inventory.py   # /skills, /download
│   ├── v2_mcp.py         # /tools（Redis 聚合 + 可选 L2 stdio）/invoke（委托优先；JACHIN_L2_STDIO_MCP 本机）
│   ├── v2_memory.py      # sync, search
│   └── v2_coordinate.py  # task, poll, result
├── db/                   # LanceDB, dream_weaver
├── inventory_scanner.py  # 侧载扫描、.local_meta（L2 默认不向 MCPManager 注入 stdio）
├── l2_stdio_mcp_flag.py  # JACHIN_L2_STDIO_MCP：L2 本机 stdio 回滚开关
├── mcp_task_token.py     # 跨节点 MCP 委托 Task Token（HMAC）
├── mcp_tool_locality.py  # LOCAL_PINNED 工具禁止委托
├── l3_node_db_filter.py  # Redis 节点 ∩ SQLite l3_nodes 分配
├── policy_enforcer.py    # RBAC、断网降级
├── sync_daemon.py        # CloudSyncDaemon
├── mcp_client.py         # MCPManager（L3 默认；L2 仅 JACHIN_L2_STDIO_MCP=1）
├── bootstrap.py          # 默认子账号、API Key 同步
├── main.py               # FastAPI 入口
└── requirements.txt
```

---

## L3 (clients/desktop + l3_node/)

```
clients/desktop/
├── src/
│   ├── components/       # UI 组件
│   ├── console/pages/    # 控制台页
│   ├── hooks/            # useSkillSync, useUISyncEventSource
│   └── lib/api.ts        # BACKEND_URL
└── src-tauri/src/
    ├── commands/
    │   ├── pairing.rs    # L2 网关配对
    │   └── skill_sync.rs # 技能同步
    ├── l3_spawn.rs       # 启动 L3 进程
    └── stt/, tts/        # 语音

l3_node/
├── agent_core.py         # Cognitive Kernel chat transport / RoleExecutor entry
├── bootstrap.py          # 注册、拉 Key、skill_sync、mcp_sync
├── llm_client.py         # LiteLLM 直连
├── skill_sync.py         # 从 L2 拉取技能到 l3_skill_cache
├── mcp_sync.py           # 从 L2 拉取 L3_LOCAL MCP 到 l3_mcp_cache
├── mcp_stdio_bootstrap.py # L3 内嵌 stdio MCP Host（mcp_servers.json + inventory/mcps + l3_mcp_cache stdio 包）
├── l3_packaged_stdio_mcp.py # L3_LOCAL 制品中 stdio_server 声明 → MCPManager.add_server
├── ws_server.py          # WebSocket 18981
├── skills/
│   ├── loader.py         # Wasm 加载
│   └── mcp_registry.py   # MCP 路由：本机有则本地执行，无则 L2 委托；l3_mcp_cache 动态加载
└── engine/hooks_pipeline.py
```

---

## 本地数据 (~/.jachin/)

| 路径 | 说明 |
|------|------|
| `l2_control.db` | L2 SQLite |
| `lancedb_data/` | 向量记忆 |
| `inventory/skills/` | L2 技能（L1 同步 + 侧载） |
| `inventory/mcps/` | MCP 配置（L2 同步；**默认由 L3** 起 stdio；L2 侧载需 `JACHIN_L2_STDIO_MCP=1`） |
| `mcp_servers.json` | 用户级 MCP 服务表（L3/L2 同源读取路径） |
| `inventory/l3_mcps/` | L3_LOCAL MCP（L2 从 L1 同步，供 L3 拉取） |
| `l3_skill_cache/` | L3 技能缓存 |
| `l3_mcp_cache/` | L3 MCP 缓存（mcp_sync 从 L2 拉取，mcp_registry 动态加载） |
| `l2_gateway_config.json` | L2↔L3 配对配置（非 L1↔L3） |
| `nexus_config.json` | L2↔L1 信任凭证（网关 L1 邮箱登录、Web Bridge 或 CLI 辅助写入） |

---

## 禁止目录（已废弃）

- `core/dapr/`
- `core/ray_cluster/`
- `core/memory/schema/`
