# Jachin-System v8.0 项目结构

## 文档信息

- **版本**: v8.0 (The Singularity OS)
- **最后更新**: 2026-02
- **架构**: 分布式数字生命操作系统 (Distributed Digital Life OS)

---

## ⚠️ 架构宪法 (The Constitution)

1. **全面弃用** Dapr、Ray 集群、本地 PostgreSQL、Qdrant、Redis、复杂 Docker 编排。
2. Layer 2 执行引擎为**双轨制**：轨道 A (MCP)、轨道 B (SKILL.md)、轨道 C (Wasm 沙箱)。
3. **v8.0 升维**：Session Multiplexing、Nexus Hook Pipeline、Dream Weaver、Capability Negotiation、Edge Mesh Swarm、全链路 runId 追踪、流式神经 (Streaming Chunk)。

---

## 目录树

```
jachin-system/
├── .cursor/                  # Cursor IDE 规则配置
│   └── rules/
│       ├── 000-structure.mdc      # v8.0 三层架构目录规范
│       ├── 060-v8-singularity.mdc # v8.0 升维特性
│       └── ...
│
├── cloud/                    # [Layer 1] 云端代码 (Next.js + Supabase)
│   └── nexus/                # 控制台、舰队、Forge、IM Webhook、心跳 API
│
├── core/                     # [Layer 2] 神经中枢总线 (Python)
│   ├── daemon.py             # 守护进程主循环 (心跳 + dream_scheduler + Layer 3 WS)
│   ├── cron_thinker.py       # 生物钟：每 30min 主动环顾
│   ├── agent_loop.py         # ReAct 循环 (v8.0 Nexus Hook Pipeline)
│   ├── event_bus.py          # 全息感官总线 (Session Multiplexing)
│   ├── session_manager.py    # v8.0 会话隔离器 (session_id → Actor)
│   ├── hooks_pipeline.py     # v8.0 洋葱中间件
│   ├── mcp_client.py         # 轨道 A：MCP 宿主
│   ├── skill_loader.py       # 轨道 B：SKILL.md 热加载
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

**技术栈**: Next.js + Supabase

---

### Layer 2: Edge Agent (The Core)

**目录**: `core/`

**职责**:
- **神经中枢总线**：双轨制 (MCP + SKILL.md + Wasm)、量子记忆、自我修复
- **v8.0 升维**：Session Multiplexing、Nexus Hook Pipeline、Dream Weaver、Edge Mesh Swarm、全链路 runId 追踪、流式神经
- **Jachin Mesh**：WebSocket 双向长连，毫秒级指令下发
- **生物钟**：cron_thinker 每 30min 主动环顾

**技术栈**: Python 3.10+ (asyncio, httpx) + wasmtime + sqlite3 + MCP Client

**数据底座**: SQLite (memory.db, event_queue.db) + LanceDB (vector_db/)

---

### Layer 3: Jachin Terminal (The Edge)

**目录**: `clients/`

**职责**: 全息感知外壳，零摩擦体验

**核心功能**:
- **桌面精灵**: Tauri v2 + React，扫码即连、静默拉起 Layer 2
- **Voice Wake**: Hey Jachin 唤醒词 + Whisper STT + TTS
- **jachin-cli**: pair、shell 极客终端
- **Capability Negotiation**: 连接时发送 Manifest，按 caps 接收推送

**技术栈**: Tauri v2 + React

---

## 禁止目录 (core/)

- ❌ `core/dapr/`、`core/ray_cluster/`、`core/memory/schema/`
- 业务逻辑应在 `skills_repo/`、MCP 或 JPP 插件中实现

---

## 参考文档

- [docs/whitepaper/00_INDEX.md](docs/whitepaper/00_INDEX.md) — 白皮书索引
- [docs/whitepaper/V8_SINGULARITY_OS.md](docs/whitepaper/V8_SINGULARITY_OS.md) — v8.0 架构升维
- [docs/whitepaper/04_FILE_STRUCTURE.md](docs/whitepaper/04_FILE_STRUCTURE.md) — 详细文件结构
