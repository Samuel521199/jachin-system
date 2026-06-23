# Jachin 三层系统架构详解

> **分册**: 01 / 07 · [返回索引](./README.md)  
> **代码锚点**: `core/`（L2）、`l3_node/`（L3）、`cloud/nexus/`（L1）

---

## 目录

1. [设计原则与分层哲学](#一设计原则与分层哲学)
2. [三层职责总览](#二三层职责总览)
3. [L1 全球商城（Cloud）](#三l1-全球商城cloud)
4. [L2 本地数字仓库（Control Plane）](#四l2-本地数字仓库control-plane)
5. [L3 执行面（Execution Plane）](#五l3-执行面execution-plane)
6. [跨层数据流与时序](#六跨层数据流与时序)
7. [API Key 零信任安全模型](#七api-key-零信任安全模型)
8. [Skill/MCP 分发流程](#八skillmcp-分发流程)
9. [多节点集群拓扑](#九多节点集群拓扑)
10. [环境变量速查](#十环境变量速查)

---

## 一、设计原则与分层哲学

| 原则 | 说明 |
|------|------|
| **L2 不代理推理** | 控制面只管权限/Key/调度，LLM 调用全部在 L3 闭环 |
| **API Key 零信任** | L2 用 L3 公钥加密下发，L3 私钥解密后明文 Key 仅存内存 |
| **一店一库** | 每个企业有独立的 L2（数字仓库）；L1 是全局商城，不接触企业数据 |
| **本地闭环记忆** | L3 跨会话记忆在本机 Memory Nexus（SQLite）闭环，不依赖 L2 |
| **云边分治** | L1 负责商业分发，L2 负责企业控制，L3 负责设备侧推理 |

---

## 二、三层职责总览

```mermaid
flowchart TB
    subgraph L1["☁️  L1 全球商城（Cloud / cloud/nexus/）"]
        L1_STORE["商城 · catalog / publish / subscribe"]
        L1_IAM["IAM · 主账号 / License 颁发"]
        L1_SYNC["manifest 同步"]
        L1_AUDIT["审核 & 签名验证"]
    end

    subgraph L2["🏢  L2 本地数字仓库（Control Plane / core/）"]
        L2_INV["inventory · Skill/MCP 囤积包"]
        L2_KEY["API Key 保险箱（密文存储）"]
        L2_IAM2["子账号 / 权限 / RBAC"]
        L2_COORD["coordinate API · L3 跨节点调度"]
        L2_MEM["memory API · 多租户记忆（可选）"]
        L2_MCP["MCP 清单同步 · TaskManager"]
    end

    subgraph L3["💻  L3 执行面（l3_node/）"]
        L3_REACT["单主轴 ReAct · run_agent"]
        L3_TOOLS["工具池 · Native + Wasm + MCP"]
        L3_MEM2["Memory Nexus · SQLite + FastEmbed"]
        L3_BG["后台任务队列"]
        L3_AUTO["自治服务 · AwarenessLoop"]
        L3_LLM["LiteLLMEngine · 本地解密直连 LLM"]
    end

    subgraph CLIENT["📱 客户端层"]
        WS["WebSocket 桌面"]
        IM["飞书 / 钉钉 IM"]
        HTTP_C["HTTP REST API"]
    end

    L1 -->|"License + Skill 包下发"| L2
    L2 -->|"encrypted_api_keys + Skills + MCP"| L3
    CLIENT -->|"用户消息"| L3
    L3 -.->|"coordinate 跨节点"| L2
    L2 -.->|"L2 派发子任务"| L3

    style L1 fill:#e8f4fd,stroke:#2196F3
    style L2 fill:#fff3e0,stroke:#FF9800
    style L3 fill:#e8f5e9,stroke:#4CAF50
    style CLIENT fill:#fce4ec,stroke:#E91E63
```

---

## 三、L1 全球商城（Cloud）

### 3.1 职责

L1 是**商业收银台**，只处理商业逻辑，**不接触企业明文数据，不提供推理算力**。

```mermaid
flowchart LR
    subgraph L1_DETAIL["L1 商城内部"]
        CATALOG["目录服务\ncatalog · 展示 Skill/MCP"]
        PUBLISH["发布服务\npublish · 开发者上架"]
        SUBSCRIBE["订阅服务\nsubscribe · 企业购买"]
        LICENSE["License 颁发\n颁发授权证书"]
        MANIFEST["manifest 同步\n元数据分发"]
        SIGN["签名验证\nSHA-256 + ECDSA"]
    end

    DEV["开发者"] -->|"上架 Skill/MCP zip"| PUBLISH
    PUBLISH --> SIGN --> CATALOG
    ENTERPRISE["企业管理员"] -->|"购买/订阅"| SUBSCRIBE
    SUBSCRIBE --> LICENSE
    LICENSE -->|"manifest 推送"| MANIFEST
    MANIFEST -->|"同步到"| L2_TARGET["L2 数字仓库"]
```

### 3.2 商品形态与四大原语映射

| 商品形态 | 文件格式 | 执行归类（四大原语） | 执行位置 |
|----------|---------|------------------|---------|
| Skill（含 Wasm） | `.jpp` / `.jsp` (ZIP) | **Tools（jpp）** + **Skills（SKILL.md）** | L3 本地沙箱 |
| MCP 插件 | `.jmp` (ZIP) | **MCP** (`mcp:*`) | L3 MCPManager |
| 纯声明 Skill | `SKILL.md` | **Skills** | L3 system prompt 注入 |

---

## 四、L2 本地数字仓库（Control Plane）

### 4.1 内部组件架构

```mermaid
flowchart TB
    subgraph L2_INTERNAL["L2 内部组件（core/）"]
        subgraph AUTH["认证授权"]
            SYNC_API["POST /api/v2/auth/sync\n接收 L3 公钥注册"]
            APPROVE["管理员审批"]
            KEY_STORE["API Key 保险箱\n用 L3 公钥加密存储"]
        end

        subgraph INVENTORY["数字仓库"]
            INV_DB["inventory SQLite\nSkill/MCP 囤积包"]
            DOWNLOAD["GET /api/v2/skills/download\n按 License 授权下发"]
            MCP_LIST["GET /api/v2/mcps\nMCP 清单同步"]
        end

        subgraph COORD_SVC["协调服务"]
            COORD_API["POST /api/v2/coordinate/task\n子任务派发"]
            NODE_MATCH["节点匹配\nskill_required 过滤"]
            TASK_POLL["GET /api/v2/coordinate/poll\n结果轮询"]
        end

        subgraph MEM_SVC["记忆服务（可选）"]
            MEM_SEARCH["GET /api/v2/memory/search\n混合检索（向量+BM25）"]
            MEM_REINFORCE["POST /api/v2/memory/reinforce\n强化学习反馈"]
        end
    end

    L3_NODE["L3 节点"] <-->|"REST API"| AUTH
    L3_NODE <-->|"REST API"| INVENTORY
    L3_NODE <-->|"coordinate"| COORD_SVC
    L1_CLOUD["L1 商城"] -->|"manifest 推送"| INVENTORY
```

### 4.2 L2 关键 API 列表

| API | 方向 | 说明 |
|-----|------|------|
| `POST /api/v2/auth/sync` | L3 → L2 | L3 注册（携带公钥） |
| `GET /api/v2/auth/poll` | L3 → L2 | 轮询审批状态 |
| `GET /api/v2/keys` | L3 → L2 | 拉取密文 API Keys |
| `POST /api/v2/admin/nodes/assign` | 管理员 → L2 | 将节点分配给子账号 |
| `GET /api/v2/skills` | L3 → L2 | 拉取可用 Skill 列表 |
| `GET /api/v2/mcps` | L3 → L2 | 拉取 MCP 清单 |
| `POST /api/v2/coordinate/task` | L3 → L2 | 发起跨节点任务 |
| `GET /api/v2/coordinate/poll` | L3 → L2 | 轮询子任务结果 |
| `GET /api/v2/memory/search` | L3 → L2 | 多租户记忆检索（可选） |
| `POST /api/v2/memory/reinforce` | L3 → L2 | 反馈强化分（可选） |

---

## 五、L3 执行面（Execution Plane）

### 5.1 L3 内部组件全图

```mermaid
flowchart TB
    subgraph ENTRY["入口层"]
        WS_SRV["ws_server.py\nWebSocket 双向流式"]
        HTTP_SRV["http_server.py\nREST + SSE"]
        IM_DISP["dispatcher.py\n飞书 IM 分发"]
    end

    subgraph GATEWAY["意图网关层"]
        PREFLIGHT["agent_preflight.py\n域短路预检"]
        PLUGINS["routing/plugins.py\n域突变插件"]
        GW_PIPE["gateway_pipeline.py\n嗅探+语义层+环境报告"]
        SIQ["session_instruction_queue.py\n并发调度 SERIAL/PARALLEL"]
    end

    subgraph CORE["核心执行层（agent_core.py）"]
        BUILD_TOOL["assemble_tool_pool\nNative+Wasm+MCP 合并"]
        BUILD_SYS["_build_system_prompt\n前缀+后缀+记忆+SOP"]
        REACT_CORE["_run_react_core\nReAct 主循环"]
        HOOKS["hooks_pipeline.py\n洋葱 Hook 链"]
    end

    subgraph TOOL_LAYER["工具执行层"]
        NATIVE["native_tools.py\ncore:* 原子工具"]
        WASM["wasm_runner.py\njpp:* Wasm 沙箱"]
        MCP_REG["mcp_registry.py\nmcp:* 外挂进程"]
    end

    subgraph MEMORY_LAYER["记忆层"]
        MEM_NEXUS["Memory Nexus\nmemory_nexus.sqlite3"]
        EXP_MEM["Experience Memory\nexperience.jsonl"]
        TASK_PLAN["task_planning.py\ntask_plan.md / progress.md"]
    end

    subgraph AUTO_LAYER["自治层"]
        AWL["awareness_loop.py"]
        SKILL_EVO["skill_evolver.py"]
        L3_HEAL["level3_healer.py"]
        DAG_PLAN["dag_planner.py"]
    end

    ENTRY --> SIQ --> GATEWAY
    GATEWAY --> CORE
    CORE --> TOOL_LAYER
    CORE <--> MEMORY_LAYER
    AUTO_LAYER -.->|"意图触发"| CORE
    AUTO_LAYER -.->|"Skill 进化"| TOOL_LAYER
```

### 5.2 L3 启动流程

```mermaid
sequenceDiagram
    participant BS as bootstrap.py
    participant L2 as L2 数字仓库
    participant ENG as LiteLLMEngine
    participant MCP as MCPManager
    participant AUTO as 自治服务

    BS->>BS: 生成/读取 RSA 密钥对
    BS->>L2: POST /api/v2/auth/sync（公钥注册）
    L2-->>BS: node_id + 审批状态

    loop 轮询审批（每 5s）
        BS->>L2: GET /api/v2/auth/poll?node_id=xxx
        L2-->>BS: approved + encrypted_api_keys
    end

    BS->>BS: 本地私钥解密 → 明文 API Keys（仅存内存）
    BS->>ENG: LiteLLMEngine(api_key=decoded, model=LLM_MODEL)
    BS->>L2: 拉取 Skills + MCP 清单
    L2-->>BS: skill_list + mcp_server_configs
    BS->>MCP: 启动本地 MCP stdio 子进程
    BS->>AUTO: start_autonomy_services()
    AUTO-->>BS: AwarenessLoop + ProactiveReporter 已就绪
    BS->>BS: 启动 WS/HTTP/IM 服务器 → 就绪
```

---

## 六、跨层数据流与时序

### 6.1 完整启动 + 运行时序

```mermaid
sequenceDiagram
    participant U as 用户/IM
    participant L3 as L3 执行面
    participant L2 as L2 数字仓库
    participant L1 as L1 商城
    participant LLM as LLM API

    rect rgb(232, 244, 253)
        Note over L1,L3: 阶段一：启动（一次性，每次进程启动）
        L3->>L2: POST /auth/sync（RSA 公钥）
        L2-->>L3: node_id
        L3->>L2: GET /auth/poll（等待审批）
        L2-->>L3: approved + encrypted_api_keys
        L3->>L2: GET /api/v2/keys
        L2->>L1: License 校验
        L1-->>L2: 有效
        L2-->>L3: encrypted_api_keys（RSA 加密）
        L3->>L3: 私钥解密 → 内存明文 Key
    end

    rect rgb(232, 245, 233)
        Note over L1,L2: 阶段二：Skill/MCP 同步（按需）
        L3->>L2: GET /api/v2/skills
        L2-->>L3: skill_list（含版本/授权状态）
        L3->>L2: GET /api/v2/mcps
        L2-->>L3: mcp_configs
        L3->>L3: 写入 ~/.jachin/l3_skill_cache / l3_mcp_cache
    end

    rect rgb(255, 243, 224)
        Note over U,LLM: 阶段三：运行时（每次对话）
        U->>L3: 用户消息（WS/HTTP/IM）
        L3->>L3: agent_preflight 预检
        L3->>L3: apply_gateway_ingress_pipeline
        L3->>L3: assemble_tool_pool
        L3->>L3: _build_system_prompt（注入记忆+SOP+经验）

        loop ReAct 循环（max 8 轮）
            L3->>LLM: generate_response（密文 Key 解密后直连）
            LLM-->>L3: Thought / Action / Action Input
            L3->>L3: _parse_action → 路由分发
            L3->>L3: run_tool / MCP.invoke
            L3-->>L3: Observation → 写回 messages
        end

        L3-->>U: Final Answer（流式/一次性）
        L3->>L3: schedule_nexus_turn_commit_async（异步写记忆）
    end
```

### 6.2 coordinate 跨节点时序

```mermaid
sequenceDiagram
    participant L3_A as L3 节点 A（主）
    participant L2 as L2 coordinate API
    participant L3_B as L3 节点 B
    participant L3_C as L3 节点 C

    L3_A->>L3_A: 解析 "Action: coordinate"
    L3_A->>L2: POST /api/v2/coordinate/task
    Note over L2: payload: {intent, sub_tasks[{intent, skill_required, input_data}]}

    L2->>L2: 按 skill_required 匹配在线节点
    par 并行派发
        L2->>L3_B: 派发子任务 1（skill: hr_analyzer）
        L3_B->>L3_B: run_agent(intent) 或 run_tool
        L3_B-->>L2: 子任务 1 结果
    and
        L2->>L3_C: 派发子任务 2（skill: bi_report）
        L3_C->>L3_C: run_agent(intent) 或 run_tool
        L3_C-->>L2: 子任务 2 结果
    end

    loop 轮询（每 2s）
        L3_A->>L2: GET /api/v2/coordinate/poll?task_id=xxx
        L2-->>L3_A: status + partial_results
    end

    L2-->>L3_A: 全部完成 + 聚合结果
    L3_A->>L3_A: Observation → 继续 ReAct
```

---

## 七、API Key 零信任安全模型

### 7.1 密钥流转全流程

```mermaid
flowchart LR
    subgraph L3_KEYGEN["L3 密钥生成（一次性）"]
        GEN["RSA 4096 密钥对生成\n~/.jachin/keys/node_private.pem\n~/.jachin/keys/node_public.pem"]
    end

    subgraph L2_ENCRYPT["L2 加密下发"]
        L2_RECV["接收公钥 + 注册 node_id"]
        ADMIN["管理员审批\nPOST /api/v2/admin/nodes/assign"]
        ENCRYPT["用 L3 公钥加密\nRSA-OAEP + AES-256 对 API Keys 加密"]
        STORE["密文存 L2 数据库\n明文永不落盘"]
    end

    subgraph L3_DECRYPT["L3 解密使用"]
        PULL["GET /api/v2/keys → 密文包"]
        DECRYPT["本地私钥解密\n~/.jachin/keys/node_private.pem"]
        MEMORY["内存明文 Key\n仅在 LiteLLMEngine 实例内"]
        DIRECT["HTTPS 直连 LLM API\napi.openai.com / dashscope"]
    end

    GEN -->|"公钥"| L2_RECV
    L2_RECV --> ADMIN --> ENCRYPT --> STORE
    STORE -->|"encrypted_api_keys"| PULL
    PULL --> DECRYPT --> MEMORY --> DIRECT

    style MEMORY fill:#fff9c4,stroke:#fbc02d
    style DECRYPT fill:#c8e6c9,stroke:#388e3c
    style DIRECT fill:#e3f2fd,stroke:#1976d2
```

### 7.2 安全边界约束

| 约束 | 实现 |
|------|------|
| L3 明文 Key 仅存内存 | 解密结果仅传给 `LiteLLMEngine`，不写磁盘、不打日志 |
| L2 不持有明文 | 只存 RSA 加密后的密文包 |
| Key 轮换 | L2 可重新下发新密文包，L3 重启后重新拉取 |
| 断网降级 | `PolicyEnforcer` 断网时从 `role_permissions` 缓存降级 |
| 子账号隔离 | L3 需携带 `X-Sub-Account-Id` 才能访问受限 API |

---

## 八、Skill/MCP 分发流程

### 8.1 Skill 从开发到运行的全链路

```mermaid
flowchart TB
    subgraph DEV["开发者"]
        CODE["编写 Skill\nSKILL.md + Wasm + plugin.json"]
        PACK["打包 .jpp/.jsp (ZIP)\n含 config/manifest.yaml"]
        SIGN2["ECDSA 签名\nSHA-256 摘要"]
    end

    subgraph L1_FLOW["L1 商城"]
        UPLOAD["上架审核\n签名验证 + 内容审查"]
        CATALOG2["目录发布\ncatalog 索引"]
        LICENSE2["License 颁发"]
    end

    subgraph L2_FLOW["L2 数字仓库"]
        SUBSCRIBE2["企业订阅\nPOST /subscribe"]
        DOWNLOAD2["下载囤积\ninventory/{skill_id}/"]
        CONFIG_WRITE["配置写出\nconfig/manifest.yaml → ~/.jachin/config/skills/"]
    end

    subgraph L3_FLOW["L3 执行面"]
        CACHE["写入缓存\n~/.jachin/l3_skill_cache/{id}/"]
        LOAD["loader.py 扫描加载\nassemble_tool_pool 可见"]
        HOT["SKILL.md 热重载\nP1/P2 实时注入 system_prompt"]
    end

    DEV --> SIGN2 --> UPLOAD --> CATALOG2 --> LICENSE2
    LICENSE2 --> SUBSCRIBE2 --> DOWNLOAD2 --> CONFIG_WRITE
    CONFIG_WRITE --> CACHE --> LOAD --> HOT
```

### 8.2 MCP 分发与启动流程

```mermaid
sequenceDiagram
    participant L2 as L2 inventory
    participant L3_CACHE as ~/.jachin/l3_mcp_cache
    participant MCP_MGR as L3 MCPManager
    participant MCP_PROC as MCP stdio 进程

    L2-->>L3_CACHE: 同步 MCP zip 包 → 解压
    L3_CACHE-->>MCP_MGR: 扫描 plugin.json
    MCP_MGR->>MCP_PROC: 启动 stdio 子进程（npx / python）
    MCP_PROC-->>MCP_MGR: 初始化握手 + 工具列表
    MCP_MGR->>MCP_MGR: 注册到 mcp_registry
    Note over MCP_MGR: 工具 id = mcp:{plugin_id}:{tool_name}
    MCP_MGR-->>MCP_MGR: 写入 l3_mcp_cache 目录

    Note over MCP_PROC: 运行时调用
    MCP_MGR->>MCP_PROC: invoke(tool_name, params)
    MCP_PROC-->>MCP_MGR: result
    MCP_MGR-->>MCP_MGR: 返回 Observation
```

---

## 九、多节点集群拓扑

### 9.1 典型企业部署拓扑

```mermaid
flowchart TB
    subgraph CLOUD["☁️ 云端（L1）"]
        L1_SVC["L1 商城 / IAM"]
    end

    subgraph INTRANET["🏢 企业内网"]
        subgraph L2_SVR["L2 服务器（单台）"]
            L2_SVC["L2 数字仓库\ncoordinate API"]
            REDIS["Redis\nGlobalTaskRegistry SSOT\nSIQ Pub/Sub"]
        end

        subgraph CLUSTER["L3 节点集群"]
            L3_1["L3 节点 1\n（前台交互）\nWebSocket + IM"]
            L3_2["L3 节点 2\n（重负荷批处理）\n后台任务队列"]
            L3_3["L3 节点 3\n（定时任务）\nAwarenessLoop"]
        end

        SHARED_FS["共享文件系统\n~/.jachin/workspace/\nDAG handoff 包"]
    end

    subgraph LLM_API["LLM 服务"]
        QWEN["Qwen / DashScope"]
        GPT["OpenAI / Azure"]
    end

    L1_SVC -->|"License + manifest"| L2_SVC
    L2_SVC -->|"Keys + Skills + MCP"| L3_1 & L3_2 & L3_3
    L3_1 & L3_2 & L3_3 <-->|"coordinate 调度"| L2_SVC
    L3_1 & L3_2 & L3_3 <-->|"GlobalTaskRegistry"| REDIS
    L3_1 & L3_2 & L3_3 <-->|"DAG Handoff"| SHARED_FS
    L3_1 & L3_2 & L3_3 -->|"直连（解密 Key）"| QWEN & GPT
```

### 9.2 DAG 跨节点转交流程

```mermaid
sequenceDiagram
    participant L3_A as L3 节点 A（超载）
    participant COORD as DAG Coordinator
    participant L3_B as L3 节点 B（空闲）

    L3_A->>COORD: list_alive_nodes()
    COORD-->>L3_A: [节点A(load=0.9), 节点B(load=0.2)]
    L3_A->>COORD: find_idle_peer()（load < 0.5）
    COORD-->>L3_A: 节点 B（空闲）

    L3_A->>L3_A: export_dag_handoff(run_id)
    Note over L3_A: DagHandoffPackage{completed_node_ids, pending_nodes, resume_intent}

    L3_A->>L3_B: POST /dag-handoff/import（转交包）
    L3_B->>L3_B: import_dag_handoff → 写 active.json
    L3_B-->>L3_A: HandoffImportResult{resume_intent}

    L3_A->>COORD: release_dag(dag_id, token)
    L3_B->>COORD: claim_dag(dag_id)
    L3_B->>L3_B: run_agent(resume_intent) → 续跑
```

---

## 十、环境变量速查

| 变量 | 层级 | 作用 | 默认值 |
|------|------|------|--------|
| `JACHIN_HOME` | L3 | 配置根目录 | `~/.jachin` |
| `LLM_MODEL` | L3 | 日常档模型 | `qwen3.5-plus` |
| `LLM_COMPLEX_MODEL` | L3 | 复杂档模型 | `qwen-max` |
| `LLM_CODER_MODEL` | L3 | 编码档模型 | `qwen3-coder-plus` |
| `DASHSCOPE_API_KEY` | L3 | DashScope Key（启动前可选，通常由 L2 下发） | — |
| `JACHIN_L2_BASE_URL` | L3 | L2 服务地址 | — |
| `JACHIN_GLOBAL_REGISTRY_ENABLE` | L3 | 开启跨进程任务注册表 | `0` |
| `JACHIN_GLOBAL_REGISTRY_REDIS` | L3 | 使用 Redis 集群 SSOT | `0` |
| `JACHIN_REDIS_URL` | L3 | Redis 连接串 | — |
| `JACHIN_COORDINATOR_ENABLE` | L3 | 开启 DAG Coordinator | `0` |
| `JACHIN_COORDINATOR_PEER_URLS` | L3 | Peer 节点 URL 列表（逗号分隔） | — |
| `JACHIN_L2_STDIO_MCP` | L2 | L2 启动 stdio MCP 子进程（回滚模式） | `0` |

---

**上一篇**: [README.md](./README.md)  
**下一篇**: [02_MAIN_AGENT_DESIGN.md](./02_MAIN_AGENT_DESIGN.md)
