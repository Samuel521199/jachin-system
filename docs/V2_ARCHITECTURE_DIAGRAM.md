# Jachin Nexus V2 架构图与流程图

**版本**: 2.0  
**状态**: 设计规范  
**关联**: [ARCHITECTURE.md](ARCHITECTURE.md) | [ARCHITECTURE_V2_LAYER3_STANDALONE.md](ARCHITECTURE_V2_LAYER3_STANDALONE.md)

---

## 一、三层架构总览

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  Layer 1：平台 (cloud/nexus)                                                      │
│  • 用户主账号注册/登录                                                            │
│  • 平台主账号管理平台内部                                                          │
│  • 与 L2/L3 无直接耦合                                                            │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        │ 用户主账号登录 L2 管理
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  Layer 2：控制面 (core/)                                                          │
│  • 子账号、权限、API Key 保险箱                                                    │
│  • 记忆存储、梦境优化、L3 协同调度                                                 │
│  • 不代理 L3 推理请求                                                              │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        │ 密文 Key 下发 / 记忆同步
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  Layer 3：执行面 (clients/desktop + l3_node/)                                     │
│  • 单体 OpenClaw：多 Agent、多 Skill、本地记忆                                     │
│  • 持密文 Key，请求时解密后直连外部 API                                           │
│  • 可与 L2 同机部署                                                                │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 二、API Key 零信任流转

```mermaid
sequenceDiagram
    participant L3 as L3 节点
    participant L2 as L2 控制面
    participant Admin as L2 管理员
    participant DB as L2 数据库

    Note over L3: 1. 生成 RSA 密钥对
    L3->>L2: POST /api/v2/auth/sync<br/>{device_fingerprint, public_key_pem}
    L2->>DB: 登记 l3_nodes (sub_account_id=NULL)
    L2-->>L3: {node_id}

    Note over L3: 2. 轮询审批状态
    loop 轮询
        L3->>L2: GET /api/v2/auth/poll?node_id=xxx
        L2-->>L3: {status: "pending"}
    end

    Admin->>L2: POST /api/v2/admin/nodes/assign<br/>{node_id, sub_account_id}
    L2->>DB: 更新 l3_nodes.sub_account_id

    L3->>L2: GET /api/v2/auth/poll?node_id=xxx
    L2->>DB: 查 api_keys_vault，用 L3 公钥加密
    L2-->>L3: {status: "approved", encrypted_api_keys}

    Note over L3: 3. 本地解密（内存级）
    L3->>L3: 私钥解密 → 明文 Key
    L3->>L3: 直连 api.openai.com
```

---

## 三、L2 控制面职责

```mermaid
flowchart TB
    subgraph L2["Layer 2 控制面"]
        SA[子账号管理]
        PK[API Key 保险箱]
        MEM[记忆存储 + namespace]
        DREAM[梦境优化]
        INV[数字仓库 inventory]
        MCP[MCP 代理]
        SCHED[L3 协同调度]
        REDIS[Redis 状态/队列]
    end

    subgraph L2_API["L2 API"]
        AUTH["POST /auth/sync"]
        POLL["GET /auth/poll"]
        KEYS["GET /keys"]
        MEM_SYNC["POST /memory/sync"]
        MEM_SEARCH["GET /memory/search"]
        INV_SKILLS["GET /inventory/skills"]
        INV_DL["GET /inventory/skills/{id}/download"]
        MCP_INVOKE["POST /mcp/invoke"]
        COORD_TASK["POST /coordinate/task"]
        COORD_POLL["GET /coordinate/poll"]
        ADMIN_SA["POST /admin/sub-accounts"]
        ADMIN_KEY["POST /admin/keys"]
        ADMIN_NODE["POST /admin/nodes/assign"]
    end

    L3_REG[L3 注册] --> AUTH
    L3_POLL[L3 轮询审批] --> POLL
    L3_KEYS[L3 拉取 Key] --> KEYS
    L3_SKILLS[L3 技能同步] --> INV_SKILLS
    L3_SKILLS --> INV_DL
    INV_SKILLS --> INV
    INV_DL --> INV
    MCP_INVOKE --> MCP
    L3_MEM[L3 记忆同步/检索] --> MEM_SYNC
    L3_MEM --> MEM_SEARCH
    L3_COORD[L3 协同请求/拉取] --> COORD_TASK
    L3_COORD --> COORD_POLL
    ADMIN_SA --> SA
    ADMIN_KEY --> PK
    SA --> PK
    MEM_SYNC --> MEM
    MEM_SEARCH --> MEM
    MEM --> DREAM
    DREAM --> MEM
    COORD_TASK --> SCHED
    COORD_POLL --> REDIS
    SCHED --> REDIS
```

---

## 四、L3 单体执行流

```mermaid
flowchart LR
    subgraph L3["L3 单体节点"]
        IN[入口: Tauri/IM/CLI]
        AGENT[主 Agent]
        SUB[子 Agent]
        SKILL[Skills]
        MEM[本地记忆]
        KEY[密文 Key]
    end

    IN --> AGENT
    AGENT --> SUB
    AGENT --> SKILL
    AGENT --> MEM
    KEY -->|解密| LLM[外部 API]
    AGENT --> LLM
```

---

## 五、V2 文件结构

```
jachin-system/
├── core/                    # Layer 2 控制面
│   ├── db/                  # schema, l2_memory_lancedb (namespace), dream_weaver
│   ├── security/            # crypto_manager
│   ├── api/routes/
│   │   ├── v2_auth.py       # POST /auth/sync, GET /auth/poll, GET /keys
│   │   ├── v2_admin.py      # POST /admin/sub-accounts, /admin/keys, /admin/nodes/assign
│   │   ├── v2_memory.py     # POST /memory/sync, GET /memory/search (namespace)
│   │   └── v2_coordinate.py # POST /coordinate/task, GET /coordinate/poll
│   ├── l3_redis_state.py    # L2 无状态集群：L3 状态、任务队列
│   ├── redis_manager.py     # Redis 客户端、Leader 锁
│   └── permissions.py      # RBAC（allowed_memory_namespaces）
├── cloud/nexus/             # Layer 1 平台
├── clients/desktop/         # Layer 3 终端 (Tauri)
├── l3_node/                 # L3 单体执行引擎
│   ├── llm_client.py        # SecurityContext + LiteLLMEngine 直连
│   ├── agent_core.py        # ReAct + SubAgent 分身 + MemorySyncDaemon
│   ├── bootstrap.py         # 引导：注册、拉 Key、创建引擎
│   └── skills/              # MCP + SKILL.md + JPP .wasm
└── docs/
```

---

## 六、L2 无状态集群

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  L2 无状态化（K8s 横向扩缩容）                                                 │
│                                                                             │
│  Redis: l3_node_status:{node_id} (TTL 60s)  ← L3 poll 时写入                 │
│         l3_task_queue:{node_id}             ← 子任务 LPUSH，poll 时 RPOP     │
│         l2_cluster_leader_lock              ← 仅 Leader 执行 L1 心跳         │
│                                                                             │
│  任意 L2 节点均可处理任意 L3 请求；Redis 不可用时回退 SQLite 单节点模式。        │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 七、已废弃组件

| 组件 | 状态 | 说明 |
|------|------|------|
| **Dapr** | 已弃用 | clients/desktop 已统一直连后端 |
| **Ray Cluster** | 已弃用 | task_planner/resource_allocator 已用占位类型 |
| **PostgreSQL** (L2) | 已弃用 | L2 使用 SQLite |
| `/api/v3/orchestrator/plan` | 410 | 原依赖 Ray |
| `/api/v3/orchestrator/execute` | 410 | 原依赖 Ray |
