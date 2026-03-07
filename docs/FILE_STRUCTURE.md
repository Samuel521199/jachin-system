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
│   │   ├── iam/          # policies/sync, inventory, roles
│   │   └── ...
│   ├── app/dashboard/    # IAM、Admin 审核
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
│   ├── v2_mcp.py         # /invoke
│   ├── v2_memory.py      # sync, search
│   └── v2_coordinate.py  # task, poll, result
├── db/                   # LanceDB, dream_weaver
├── inventory_scanner.py  # 侧载扫描、.local_meta
├── policy_enforcer.py    # RBAC、断网降级
├── sync_daemon.py        # CloudSyncDaemon
├── mcp_client.py         # MCPManager
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
├── agent_core.py         # ReAct Agent
├── bootstrap.py          # 注册、拉 Key
├── llm_client.py         # LiteLLM 直连
├── ws_server.py          # WebSocket 18981
├── skills/
│   ├── loader.py         # Wasm 加载
│   └── mcp_registry.py   # MCP 代理
└── engine/hooks_pipeline.py
```

---

## 本地数据 (~/.jachin/)

| 路径 | 说明 |
|------|------|
| `l2_control.db` | L2 SQLite |
| `lancedb_data/` | 向量记忆 |
| `inventory/skills/` | L2 技能（L1 同步 + 侧载） |
| `inventory/mcps/` | MCP 配置 |
| `l3_skill_cache/` | L3 技能缓存 |
| `l2_gateway_config.json` | L3-L2 配对配置 |
| `nexus_config.json` | L2-L1 配对配置 |

---

## 禁止目录（已废弃）

- `core/dapr/`
- `core/ray_cluster/`
- `core/memory/schema/`
