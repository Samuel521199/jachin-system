# Jachin Nexus V2 架构图与流程图

**版本**: 2.0  
**状态**: 设计规范  
**关联**: [ARCHITECTURE_V2_LAYER3_STANDALONE.md](ARCHITECTURE_V2_LAYER3_STANDALONE.md)

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
        MEM[记忆存储]
        DREAM[梦境优化]
        SCHED[L3 协同调度]
    end

    subgraph L2_API["L2 API"]
        AUTH["POST /api/v2/auth/sync"]
        POLL["GET /api/v2/auth/poll"]
        KEYS["GET /api/v2/keys"]
        ADMIN_SA["POST /api/v2/admin/sub-accounts"]
        ADMIN_KEY["POST /api/v2/admin/keys"]
        ADMIN_NODE["POST /api/v2/admin/nodes/assign"]
    end

    L3_REG[L3 注册] --> AUTH
    L3_POLL[L3 轮询审批] --> POLL
    L3_KEYS[L3 拉取 Key] --> KEYS
    ADMIN_SA --> SA
    ADMIN_KEY --> PK
    SA --> PK
    MEM --> DREAM
    DREAM --> MEM
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
│   ├── db/                  # L2 数据库 (sub_accounts, api_keys_vault, l3_nodes)
│   ├── security/            # crypto_manager
│   ├── api/routes/
│   │   ├── v2_auth.py       # POST /auth/sync, GET /auth/poll, GET /keys
│   │   ├── v2_admin.py      # POST /admin/sub-accounts, /admin/keys
│   │   └── v2_memory.py     # POST /memory/sync
│   └── ...
├── cloud/nexus/             # Layer 1 平台
├── clients/desktop/         # Layer 3 终端 (Tauri)
├── l3_node/                 # L3 单体执行引擎 ✅
│   ├── llm_client.py        # SecurityContext + LiteLLMEngine 直连
│   ├── agent_core.py        # ReAct Agent + MemorySyncDaemon
│   ├── bootstrap.py        # 引导：注册、拉 Key、创建引擎
│   └── engine/hooks_pipeline.py
└── docs/
```

---

## 六、已废弃组件

| 组件 | 状态 | 说明 |
|------|------|------|
| `core/api/cluster.py` | 已删除 | 依赖 ray_cluster |
| `config/ray_config.yaml` | 已删除 | Ray 配置 |
| **Dapr** | 已弃用 | clients/desktop 已统一直连后端 |
| **Ray Cluster** | 已弃用 | task_planner/resource_allocator 已用占位类型 |
| **PostgreSQL** (L2) | 已弃用 | L2 使用 SQLite |
| `/api/v3/orchestrator/plan` | 410 | 原依赖 Ray |
| `/api/v3/orchestrator/execute` | 410 | 原依赖 Ray |

## 七、合规报告

详见 [V2_ARCHITECTURE_COMPLIANCE_REPORT.md](V2_ARCHITECTURE_COMPLIANCE_REPORT.md)
