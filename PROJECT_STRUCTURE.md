# Jachin-System V2 项目结构

## 文档信息

- **版本**: V2 (L2 控制面 + L3 单体)
- **最后更新**: 2026-03
- **架构**: L1 平台 / L2 零信任控制面 / L3 边缘单体执行面

---

## ⚠️ 架构宪法 (The Constitution)

1. **全面弃用** Dapr、Ray 集群、本地 PostgreSQL、Qdrant、Redis、复杂 Docker 编排。
2. **V2 分层**：L1 平台、L2 控制面（子账号/权限/API Key 保险箱/记忆）、L3 单体（对标 OpenClaw，持密文 Key 直连 API）。
3. **L2 不代理推理**：L3 持密文 Key，请求时解密后自行调用外部 LLM API。

---

## 目录树

```
jachin-system/
├── .cursor/                  # Cursor IDE 规则配置
│   └── rules/
│       ├── 000-structure.mdc      # V2 三层架构目录规范
│       ├── 065-v2-layer3-standalone.mdc # V2 L3 单体架构
│       └── ...
│
├── cloud/                    # [Layer 1] 云端代码 (Next.js + Drizzle ORM + Auth.js)
│   └── nexus/                # 控制台、舰队、Forge、IM Webhook、心跳 API
│
├── core/                     # [Layer 2] 控制面 (Python)
│   ├── db/                   # L2 数据库 (sub_accounts, api_keys_vault, l3_nodes)
│   ├── security/             # crypto_manager 零信任密钥
│   ├── api/routes/
│   │   ├── v2_auth.py        # POST /auth/sync, GET /auth/poll, GET /keys
│   │   └── v2_admin.py       # POST /admin/sub-accounts, /admin/keys, /admin/nodes/assign
│   ├── daemon.py             # 守护进程主循环
│   ├── agent_loop.py         # ReAct 循环（过渡期，未来迁移至 L3）
│   ├── event_bus.py          # 全息感官总线
│   ├── hooks_pipeline.py     # 洋葱中间件
│   ├── mcp_client.py         # 轨道 A：MCP 宿主
│   ├── runtime/skill_loader.py  # 轨道 B：SKILL.md 热加载
│   ├── wasm_runner.py        # 轨道 C：The Abyss Wasm 沙箱
│   ├── biological_memory.py # 海马体 + 大脑皮层
│   ├── memory_store.py       # v8.0 LanceDB 记忆碎片 (Dream Weaver 数据层)
│   ├── dreamer.py            # 梦境引擎 (short_term → core_memory)
│   ├── dream_weaver.py       # v8.0 Dream Weaver 梦境重塑
│   ├── swarm_registry.py     # v8.0 Edge Mesh 虫群任务注册表
│   ├── swarm_hook.py         # v8.0 虫群 Hook (heavy_tools 外包)
│   ├── llm_provider.py       # LiteLLM 认知引擎 (含流式神经)
│   ├── embedding/            # 可插拔向量引擎 (Cloud/Edge)
│   └── config/               # 配置管理
│
├── clients/                  # [Layer 3] 客户端
│   ├── desktop/              # Tauri v2 桌面精灵
│   └── iot/                  # 树莓派/IoT 脚本
├── l3_node/                  # [Layer 3] 单体执行引擎
│   ├── llm_client.py         # 本地解密 + 直连 LLM
│   ├── agent_core.py         # ReAct + MemorySyncDaemon
│   └── bootstrap.py         # 引导
│
├── skills_repo/              # 轨道 B SKILL.md + 轨道 C Wasm 插件
│
├── jachin-plugin-sdk/        # [Dev] JPP Rust 脚手架
├── jachin-plugin-sdk-python/ # [Dev] JPP Python 脚手架
│
├── scripts/                  # 极简启动脚本
│   └── mock_worker.py        # v8.0 Edge Mesh 工蜂测试
│
├── docs/                     # v8.0 核心白皮书
│   └── whitepaper/
│
├── .env.example              # 环境变量示例
└── README.md                 # 项目主文档
```

---

## 三层架构职责

### Layer 1: Jachin Nexus (The Cloud)

**目录**: `cloud/`

**职责**:
- 智慧分发枢纽：免密登录、舰队指挥、Forge 蓝图编排
- Universal Message Adapter：全渠道 Webhook 统一适配
- 资产确权：蓝图、JPP 插件元数据
- 心跳 API：指令下发、结果回传

**技术栈**: Next.js + Drizzle ORM + Auth.js（去 BaaS 化 P0 已落地）

---

### Layer 2: 控制面 (The Control Plane)

**目录**: `core/`

**职责**:
- **子账号与权限**：在 L2 创建子账号，定义 L3 节点、Skill 白名单
- **API Key 保险箱**：Master Key 加密存储，用 L3 公钥加密下发，**不代理推理请求**
- **记忆与梦境**：接收 L3 同步记忆，梦境优化后回传
- **L3 协同调度**：多 L3 节点任务分配

**技术栈**: Python 3.10+ + SQLite (~/.jachin/l2_control.db) + cryptography

**V2 API**: `/api/v2/auth/sync`, `/api/v2/keys`, `/api/v2/admin/*`

---

### Layer 3: 单体执行面 (The Execution Plane)

**目录**: `clients/desktop`（未来 `l3_node/`）

**职责**: 单体 OpenClaw 对标，多 Agent、多 Skill、本地记忆

**核心功能**:
- **持密文 Key**：从 L2 拉取，本地私钥解密后直连外部 API
- **桌面精灵**: Tauri v2 + React
- **Voice Wake**: Hey Jachin 唤醒词 + STT + TTS
- **可与 L2 同机部署**

**技术栈**: Tauri v2 + React

---

## 禁止目录 (core/)

- ❌ `core/dapr/`、`core/ray_cluster/`、`core/memory/schema/`
- 业务逻辑应在 `skills_repo/`、MCP 或 JPP 插件中实现

---

## 参考文档

- [docs/ARCHITECTURE_V2_LAYER3_STANDALONE.md](docs/ARCHITECTURE_V2_LAYER3_STANDALONE.md) — V2 架构规范
- [docs/V2_ARCHITECTURE_DIAGRAM.md](docs/V2_ARCHITECTURE_DIAGRAM.md) — V2 架构图与流程图
- [docs/whitepaper/00_INDEX.md](docs/whitepaper/00_INDEX.md) — 白皮书索引
