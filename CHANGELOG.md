# Changelog

All notable changes to this project will be documented in this file.

## [v0.8.9.1] - 2026-03

### Added

- **deploy-bundle-l2**: Standalone L2 deploy package with Docker (patched image: fastapi, uvicorn, static/admin)
- **L1-L2 pairing via env**: sync_daemon, v2_admin, bootstrap support NEXUS_* env vars for Docker deployment

### Changed

- core/requirements.txt: Add fastapi, uvicorn for L2 Docker image
- deploy/Dockerfile.l2: Copy static/ for /admin UI
- Version bump to v0.8.9.1

---

## [v0.8.5] - 2026-03

### Added

- **L1-L2 配对码溯源**：配对成功后，将 6 位配对码写入 L2 默认子账号 `l1_pairing_code`，实现审计溯源

### Changed

- 版本号统一更新至 v0.8.5

---

## [v8.0] - 2026-02 (The Singularity OS)

### Added

- **全链路 runId 追踪 (Distributed Tracing)**：每次用户请求注入唯一 run_id，贯穿 SensoryInputEvent → PipelineContext → SensoryOutputEvent，日志染色 `[RunID: xxx]`
- **流式神经 (Streaming Chunk)**：LLM 逐 token 流式输出，`generate_response_stream` + `on_chunk` 回调，caps 含 `stream_chunk` 的客户端实时接收
- **Session Multiplexing**：按 session_id 隔离 Agent Actor，多用户/多路输入零串话
- **Nexus Hook Pipeline**：Koa.js 风格洋葱中间件，5 个生命周期 Hook
- **Dream Weaver Consolidation**：LanceDB 记忆聚类/去重/融合，is_consolidated + 冲突消解
- **Capability Negotiation**：Layer 3 Manifest 握手，按 caps 动态推送
- **Edge Mesh Swarm**：同网设备算力协同，heavy_tools 外包至虫群节点

### Changed

- 白皮书、规格、`.cursor/rules` 全面统一至 v8.0 架构
- 移除所有 v3/v5/v6/v7 版本引用，项目完全统一到 v8.0

---

## [v0.6.1] - 2026-02-28

### Removed

- **废弃 Dapr**：移除 `core/dapr/`、`dapr/`、`clients/desktop/src-tauri/src/dapr.rs` 及 Dapr 相关脚本
- **废弃 Ray Cluster**：移除 `core/brain/ray_cluster/` 全部模块
- **废弃旧 memory 架构**：移除 `core/memory/`（schema、lancedb_store、embedding 等），由 SQLite + 生物学记忆取代
- **废弃过时文档**：移除 ARCHITECTURE_DESIGN_SPEC、DAPR_GUIDE、LAYER1/LAYER2 旧设计、MICROKERNEL、NEXUS_DAEMON、RAG_ARCHITECTURE、VOICE_GUIDE 等 30+ 旧文档
- **废弃臃肿脚本**：移除 setup.ps1/sh、start-full.ps1/sh、dapr_restart_scheduler.ps1

### Changed

- 以当前版本为准，远程与本地完全同步
- 白皮书、规格、rules 已全面更新至 v6.0 架构

---

## [v0.6.0] - 2026-02-28

### Added

- **双轨制执行引擎 (Dual-Track Engine)**
  - 轨道 A：MCP (Model Context Protocol) 宿主，继承全球 AI 工具生态，开箱即用
  - 轨道 B：SKILL.md 声明式技能，`skills_repo/` 热加载，零编译
  - 轨道 C：The Abyss Wasm 沙箱，商城第三方付费插件，零信任
- **量子记忆 (Quantum Memory)**
  - Vector SQLite (sqlite-vss/lancedb) 扩展，百万级 Token 语义检索
  - 自我修复 (Self-Healing)：工具报错时自动重试，梦境阶段生成 bug_fix 规则
- **生物钟主动心跳 (cron_thinker)**
  - 脱离云端，每 30 分钟主动环顾
  - 扫描系统日志、未读邮件，异常时 IM 推送报警
  - 支持 HEARTBEAT.md 式任务清单
- **全息感知器官 (Jarvis Protocol)**
  - Universal Message Adapter：全渠道 Webhook 统一适配（Discord、Slack、WhatsApp、iMessage 等）
  - Voice Wake (Hey Jachin)：Porcupine/Snowboy 唤醒词 → Whisper STT → Agent → TTS 播报
  - jachin-cli：`pair`、`shell` 极客终端入口
- **文档与规范**
  - 白皮书升级至 v6.0 (The Neural Bus Edition)
  - 新增 `docs/MCP_SPEC.md`、`docs/SKILL_MD_SPEC.md`
  - 更新 `docs/JACHIN_VS_OPENCLAW_ANALYSIS.md`、P0、VOICE、IM_GATEWAY 等规格
  - `.cursor/rules/*.mdc` 全面同步 v6.0 架构

### Changed

- Layer 2 定位由「边缘守护引擎」升级为「神经中枢总线 (Neural Bus)」
- 技能体系由单一 JPP Wasm 扩展为三轨道（MCP + SKILL.md + Wasm）
- 记忆系统由生物学梦境扩展为量子记忆（向量 + 自我修复）
- 主动能力由纯 10s 心跳拉取扩展为 cron_thinker 生物钟 + 云端心跳

---

## [v0.5.7] - 2026-03-03

### Added

- **WASI 经脉打通**：`core/wasm_runner.py` 支持 stdin/stdout 协议
  - `run_plugin_wasi(wasm_path, stdin_str)`：WASI 模式执行 Python (py2wasm) 插件
  - `run_plugin(..., stdin_json=...)`：传入 stdin_json 时自动走 WASI 模式
- **战役 3：JPP Python SDK**（jachin-plugin-sdk-python）
  - `@jachin_plugin` 装饰器、stdin/stdout JSON 协议
  - 示例：fetch_crypto_price（加密货币价格）
  - plugin.json：royalty_fee、schema（input/output）
  - py2wasm 编译、Makefile、build.ps1

### Changed

- **文档更新**：LAYER2_AGENT_LOOP、JMP_SPEC、REVENUE、ECOSYSTEM、BATTLE_PLAN、NEXUS_DAEMON、PLUGIN_SECURITY_SANDBOX、core/README、TECHNICAL_SPECIFICATIONS 同步 JPP Python SDK、WASI、memory.db、Mock 工具等

### Removed

- **core/MVP_CHECKLIST.md**：过时（引用不存在的 backend/ 路径）

---

## [v0.5.6] - 2026-02-28

### Added

- **生物学记忆管线 (Biological Memory Pipeline)**
  - `core/biological_memory.py`：海马体 (short_term_logs) + 大脑皮层 (core_memory)，SQLite 极简存储
  - `core/dreamer.py`：梦境引擎，凌晨 3 点对短期日志执行 LLM 提纯，遗忘无用内容
  - Agent Loop 集成：每次交互写入短期记忆，System Prompt 注入核心记忆
  - Daemon 调度：dream_scheduler_loop 与心跳并行，每日 3:00 触发梦境
- **进化战役三：JPP 开发者脚手架**
  - `jachin-plugin-sdk/`：Rust 模板，plugin.json、Makefile、标准 ABI
  - 示例：智能灯泡、数据清洗
  - README：3 步入门、分润说明、煽动式文案

---

## [v0.5.5] - 2026-02-28

### Added

- **进化战役二：IM 网关（Telegram / 飞书）**
  - 数据库：`edge_agents.im_binding_id`、`im_platform`，`agent_message_queue` 表
  - Webhook：`POST /api/v1/webhooks/telegram` 接收 Telegram 消息，插入队列
  - 心跳扩展：返回 `task`、`pending_message_ids` 供边缘 Agent 拉取
  - 结果 API：`POST /api/v1/agents/result` 接收执行结果，推回用户手机
  - 绑定 API：`POST /api/v1/agents/bind-im` 将 Agent 与 Telegram chat_id 绑定
  - Layer 2 daemon：消费 task，执行后调用 result API
- **文档**：`docs/IM_GATEWAY_SPEC.md`

---

## [v0.5.4] - 2026-02-28

### Added

- **进化战役一：Agent Loop 与自主执行**
  - `core/agent_memory.py`：持久化记忆（add_memory, get_context），SQLite/JSON 存储
  - `core/agent_loop.py`：ReAct 代理循环（Thought → Action → Observation），LLM + Wasm 技能
  - 蓝图重定义：Persona & Skillset，Processor 节点 = Wasm 技能武器，由 Agent 按需调用
- **文档**：`docs/LAYER2_AGENT_LOOP_DESIGN.md` 完整架构说明

### Changed

- `core/daemon.py`：心跳收到蓝图后，喂给 AgentLoop.run() 自主执行，不再机械执行 Trigger→Processor→Action
- 心跳 API 支持扩展 `task`/`message` 字段，作为 Agent 用户输入
- 文档更新：plugins/README、scripts/README、NEXUS_DAEMON、LAYER1_ARCHITECTURE、docs/README

---

## [v0.2.0] - 2026-02-12

### Added

- **控制台 HUD API**：思维流日志、建议、记忆搜索、模型列表与切换
- **配置 API**：`/api/v3/config` 供 Horizon 显示环境与模型
- **技能权限字段**：manifest 中 `permissions` 支持 LiveTile 悬停展示
- **Dapr 部署适配**：`start.ps1` 支持 placement/scheduler 地址配置，适配本地/云/多级部署

### Changed

- ConsoleLayout：Void 节点数由记忆数驱动，Horizon 从后端 config 获取 environment/model
- DAPR_GUIDE：新增 Placement 与 Scheduler 地址配置文档

### Fixed

- Dapr scheduler 连接超时：显式指定 `localhost:6060` 避免 mDNS 返回容器内网 IP

---

## [v3.2] - 2026-02-03

详见 [docs/whitepaper_v3.2_final.md](docs/whitepaper_v3.2_final.md)
