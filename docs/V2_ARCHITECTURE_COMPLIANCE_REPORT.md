# Jachin V2 架构合规性扫描报告

**版本**: 1.1  
**基准文档**: [ARCHITECTURE_V2_LAYER3_STANDALONE.md](./ARCHITECTURE_V2_LAYER3_STANDALONE.md)  
**扫描日期**: 2026-03-05  
**2026-03 更新**: 本文档为历史合规扫描快照。V2 核心已落地：v2_coordinate、v2_memory（namespace）、L2 无状态集群（Redis）、SubAgent 分身、JPP .wasm。**以 [ARCHITECTURE_V2_LAYER3_STANDALONE.md](./ARCHITECTURE_V2_LAYER3_STANDALONE.md) 与 [PROJECT_STRUCTURE.md](../PROJECT_STRUCTURE.md) 为权威参考。**

---

## 〇、V2 规范摘要

| 层级 | V2 职责 |
|------|---------|
| **L1 平台** | 用户主账号注册/登录，平台主账号管理平台内部，与 L2/L3 无直接耦合 |
| **L2 控制面** | 子账号、权限、API Key 管理（密文下发）、记忆、梦境、L3 协同调度；**不代理推理** |
| **L3 单体** | 对标 OpenClaw：多 Agent、多 Skill、本地记忆，持密文 Key 解密后直连外部 API |

**禁止/已弃用**：Ray、Dapr、PostgreSQL（L2）、L2 代理推理、中心化执行引擎（Agent 在 L2）  
**Redis**：L2 集群化时可选（L3 状态、任务队列、Leader 选举）

---

## 一、架构相关文件清单与合规标注

### 1.1 docs/

| 文件 | 合规 | 说明 |
|------|------|------|
| **ARCHITECTURE_V2_LAYER3_STANDALONE.md** | ✅ V2 | 基准规范文档 |
| **V2_ARCHITECTURE_DIAGRAM.md** | ✅ V2 | V2 架构图与流程图 |
| **L2_GATEWAY_CLUSTER_ARCHITECTURE.md** | ✅ V2 | L2 网关集群，与 V2 兼容 |
| **LAYER3_L2_WAN_ARCHITECTURE.md** | ✅ V2 | L3 与 L2 通信 |
| **JACHIN_VS_OPENCLAW_ANALYSIS.md** | ⚠️ 部分 | 含 v8.0 表述，需补充 V2 对标 |
| **PROJECT_STRUCTURE.md** | ✅ V2 | 已更新为 V2 结构 |
| **README.md** | ⚠️ 部分 | 需核对 De-BaaSification 表述 |
| **whitepaper/00_INDEX.md** | ⚠️ 部分 | 含「Layer 2 执行引擎为双轨制」— V2 执行在 L3 |
| **whitepaper/01_DESIGN_PURPOSE.md** | - | 通用设计目的 |
| **whitepaper/02_FRAMEWORK.md** | ❌ 旧 | Dapr & Ray Cluster 已弃用；L2 执行引擎描述 |
| **whitepaper/04_FILE_STRUCTURE.md** | ✅ V2 | 禁止 dapr/ray_cluster |
| **whitepaper/05_LAYER1_NEXUS.md** | ⚠️ 部分 | L1 为「云端指挥中枢」— V2 为「平台」；含 Dapr 废弃说明 |
| **whitepaper/06_LAYER2_EDGE.md** | ✅ V2 | 已更新：L2 控制面、namespace、L2 无状态集群 |
| **whitepaper/07_LAYER3_TERMINAL.md** | ❌ 旧 | L3 为「零摩擦体验外壳」— V2 为「单体 OpenClaw」执行节点 |
| **whitepaper/08_JPP_SDK_AND_SKILLS.md** | - | 技能生态，与分层无关 |
| **whitepaper/09_DE_BAASIFICATION.md** | ⚠️ 部分 | PostgreSQL 用于 L1，符合；L2 应 SQLite |
| **whitepaper/10_CONTROL_DATA_PLANE.md** | - | 控制面/数据面分离 |
| **whitepaper/V8_SINGULARITY_OS.md** | ❌ 旧 | v8.0 特性，Agent 在 L2 |
| **whitepaper/OMNI_SENSORY_BUS.md** | - | 感官总线 |
| **whitepaper/PLUGGABLE_*.md** | - | 可插拔引擎 |
| **MCP_SPEC.md** | - | MCP 规范 |
| **SKILL_MD_SPEC.md** | - | SKILL 规范 |
| **TESTING_GUIDE.md** | ⚠️ 部分 | 含 Ray 废弃说明 |
| **VISION.md** | ⚠️ 部分 | 含 SQLite 替代 PostgreSQL |
| **IM_GATEWAY_SPEC.md** | - | IM 网关 |
| **PAIRING_PROTOCOL_SPEC.md** | ✅ V2 | V2 L3-L2 零信任配对；含 Legacy L1 说明 |
| **P0_TRUST_AND_HEARTBEAT_SPEC.md** | - | 信任与心跳 |

### 1.2 .cursor/rules/

| 文件 | 合规 | 说明 |
|------|------|------|
| **000-structure.mdc** | ✅ V2 | V2 分层、禁止目录 |
| **030-layer1-nexus.mdc** | ⚠️ 部分 | 含「舰队指挥」等 v8.0 表述；技术栈 Supabase |
| **040-rag-memory.mdc** | - | RAG 记忆 |
| **041-pluggable-vector-engine.mdc** | - | 向量引擎 |
| **042-pluggable-cognitive-engines.mdc** | - | 认知引擎 |
| **043-omni-sensory-bus.mdc** | - | 感官总线 |
| **045-dual-track-mcp.mdc** | - | 双轨 MCP |
| **050-distributed.mdc** | ⚠️ 部分 | v8.0 双轨制、无 Dapr/Ray；未明确 L3 执行 |
| **055-tts-service.mdc** | - | TTS |
| **057-voice-endpointing.mdc** | - | 语音 |
| **060-v8-singularity.mdc** | ❌ 旧 | v8.0 特性，Agent 在 L2、Edge Mesh Swarm |
| **060-scripts-one-click.mdc** | - | 脚本 |
| **070-layer1-platform.mdc** | ✅ V2 | L1 平台化、多租户 |
| **070-visual-aesthetic.mdc** | - | 视觉 |
| **100-jachin-link.mdc** | - | Jachin Link |
| **200-plugin-economy.mdc** | - | 插件经济 |
| **ambient-audio.mdc** | - | 环境音 |

### 1.3 core/

| 文件/目录 | 合规 | 说明 |
|-----------|------|------|
| **db/schema.py** | ✅ V2 | sub_accounts, l3_nodes, api_keys_vault |
| **api/routes/v2_auth.py** | ✅ V2 | POST /auth/sync, GET /auth/poll, GET /keys |
| **api/routes/v2_admin.py** | ✅ V2 | 子账号、API Key 管理 |
| **api/routes/v2_memory.py** | ✅ V2 | 记忆同步、检索（namespace） |
| **api/routes/v2_coordinate.py** | ✅ V2 | 协同任务、poll |
| **l3_redis_state.py** | ✅ V2 | L2 无状态：L3 状态、任务队列 |
| **security/crypto_manager.py** | ✅ V2 | 零信任密钥 |
| **nexus_daemon/daemon.py** | ✅ V2 | L2 点火总控 |
| **sensory_server.py** | ✅ V2 | 感官 WebSocket |
| **main.py** | ✅ V2 | 已废弃 Ray/Dapr |
| **api/orchestrator.py** | ✅ V2 | /plan、/execute 已 410 |
| **agent_loop.py** | ⚠️ 过渡 | 注释「未来迁移至 L3」；当前仍在 core |
| **event_bus.py** | ✅ V2 | 全息感官总线 |
| **hooks_pipeline.py** | ✅ V2 | Nexus Hook Pipeline |
| **llm_provider.py** | ⚠️ 过渡 | 推理引擎，V2 应在 L3 |
| **memory_store.py** | ✅ V2 | LanceDB 记忆 |
| **dreamer.py** | ✅ V2 | 梦境 |
| **dream_weaver.py** | ✅ V2 | 梦境重塑 |
| **swarm_registry.py** | ⚠️ 部分 | Edge Mesh，V2 为 L2 调度多 L3 |
| **swarm_hook.py** | ⚠️ 部分 | 同上 |
| **brain/agent_orchestrator.py** | ❌ 旧 | **import ray; @ray.remote** |
| **brain/planner/task_planner.py** | ❌ 破损 | **from core.brain.ray_cluster.task_types** — ray_cluster 已删除 |
| **brain/planner/resource_allocator.py** | ❌ 破损 | **from core.brain.ray_cluster.*** — ray_cluster 已删除 |
| **brain/ray_actors/** | ❌ 旧 | Ray Actor 残留 |
| **runtime/skill_runner.py** | ❌ 旧 | **import ray**，runtime_type="ray" |
| **config/__init__.py** | ❌ 旧 | RAY_CONFIG_PATH, Dapr 配置 |
| **api/routes/handshake.py** | ⚠️ 部分 | Dapr Pub/Sub 注释，已改 HTTP |
| **api/console.py** | ⚠️ 部分 | Dapr StateStore 已废弃 |
| **api/chat_v2.py** | ⚠️ 部分 | Dapr StateStore 已废弃 |
| **system/kernel.py** | ✅ V2 | Ray 已废弃占位 |
| **api/skills.py** | ✅ V2 | PostgreSQL 已废弃 |

### 1.4 cloud/nexus/

| 文件/目录 | 合规 | 说明 |
|-----------|------|------|
| **src/db/schema.ts** | ⚠️ 部分 | users, organizations, edge_agents, blueprints — V2 L1 为平台；edge_agents 可映射为 L3 节点概念 |
| **src/db/index.ts** | ✅ V2 | PostgreSQL（L1 可用） |
| **src/app/api/v1/agents/** | ⚠️ 部分 | 心跳、result — 沿用 edge_agents 模型 |
| **src/app/api/v1/fleet/** | ⚠️ 部分 | 舰队管理 — V2 L1 与 L2/L3 解耦，舰队可能归属 L2 |
| **src/app/api/v1/blueprints/** | ⚠️ 部分 | 蓝图 — V2 需明确归属 |
| **src/app/api/v1/instances/** | ⚠️ 部分 | 实例心跳 |
| **src/app/api/v1/pairing/** | Legacy | L1 6 位码配对，仅 Layer 2 daemon 使用；V2 L3 走 L2 网关 |
| **src/app/console/** | ⚠️ 部分 | 控制台、舰队页 — 需与 V2 主/子账号模型对齐 |
| **src/middleware.ts** | - | 中间件 |

### 1.5 clients/

| 文件/目录 | 合规 | 说明 |
|-----------|------|------|
| **desktop/src/lib/api.ts** | ❌ 旧 | **Dapr 调用**（invokeViaDapr, USE_DAPR, DAPR_PORT） |
| **desktop/src/components/GatewayConnectScreen.tsx** | ✅ V2 | L2 网关神经接驳 UI |
| **desktop/src-tauri/commands/pairing.rs** | ✅ V2 | gateway_connect, is_l3_engine_ready, read_l2_gateway_url |
| **desktop/src/chat.tsx** | - | 聊天 UI |
| **desktop/src/hooks/useSensoryWebSocket.ts** | - | 感官 WebSocket |
| **desktop/README.md** | ❌ 旧 | Dapr 通信说明 |
| **desktop/GAP_ANALYSIS.md** | ❌ 旧 | Dapr Pub/Sub 订阅 |
| **desktop/IMPLEMENTATION_*.md** | ❌ 旧 | Dapr 相关 |
| **iot/mock_device/** | ❌ 旧 | 大量 Dapr Pub/Sub 文档与代码 |

### 1.6 l3_node/

| 文件/目录 | 合规 | 说明 |
|-----------|------|------|
| **agent_core.py** | ✅ V2 | ReAct Agent + MemorySyncDaemon |
| **llm_client.py** | ✅ V2 | SecurityContext + 直连 LLM |
| **bootstrap.py** | ✅ V2 | 注册、拉 Key |
| **crypto.py** | ✅ V2 | 加解密 |
| **engine/hooks_pipeline.py** | ✅ V2 | 洋葱中间件 |
| **README.md** | ✅ V2 | L3 单体执行引擎 |

---

## 二、残留识别

### 2.1 需删除

| 类型 | 路径 | 说明 |
|------|------|------|
| **配置** | `config/ray_config.yaml` | Ray 集群配置 |
| **Docker** | `docker-compose.yml` 中 ray-head、dapr-placement、core-dapr、postgres 服务 | 或改为可选/注释 |
| **Docker** | `docker-compose.minimal.yml` 中同上 | 同上 |
| **Docker** | `docker-compose.dev.yml` 中 dapr-placement、dapr-scheduler | 同上 |
| **文档** | `clients/desktop/GAP_ANALYSIS.md` | 过时 Dapr 分析 |
| **文档** | `clients/desktop/IMPLEMENTATION_V2.md` 等 Dapr 相关段落 | 或整体更新 |
| **可选** | `core/brain/ray_actors/` | 若不再使用 Ray，可移除或改为占位 |

### 2.2 需更新（含具体修改点）

#### core/

| 文件 | 修改点 |
|------|--------|
| **brain/planner/task_planner.py** | 移除 `from core.brain.ray_cluster.task_types`；定义本地 `Task`/`TaskType` 或迁移至 L3 规划逻辑 |
| **brain/planner/resource_allocator.py** | 移除对 `ray_cluster` 的依赖；V2 资源分配改为 L2 调度逻辑（基于 l3_nodes 负载） |
| **brain/agent_orchestrator.py** | 移除 `import ray` 与 `@ray.remote`；改为普通 async 或迁移至 l3_node |
| **runtime/skill_runner.py** | 移除 `import ray`；`runtime_type="ray"` 改为 `"local"` 或 `"wasm"`；移除 `ray.get(ref)` |
| **config/__init__.py** | 移除 `RAY_CONFIG_PATH`、Dapr 相关配置项 |
| **swarm/scheduler.py** | 移除对 `ray_cluster/task_scheduler` 的引用；改为 L2 协同调度 |

#### clients/desktop/

| 文件 | 修改点 |
|------|--------|
| **src/lib/api.ts** | 移除 `invokeViaDapr`、`USE_DAPR`、`DAPR_PORT`；统一直连后端 API |
| **README.md** | 移除 Dapr 启动说明，改为直连后端 |
| **QUICKSTART.md** | 同上 |
| **TEST_GUIDE.md** | 同上 |

#### docs/

| 文件 | 修改点 |
|------|--------|
| **whitepaper/06_LAYER2_EDGE.md** | 重写：L2 为控制面（子账号、权限、记忆、调度），执行引擎在 L3 |
| **whitepaper/07_LAYER3_TERMINAL.md** | 重写：L3 为单体 OpenClaw（Agent + Skill + 本地记忆），非仅 UI 外壳 |
| **whitepaper/02_FRAMEWORK.md** | 移除 Dapr/Ray；更新 L2/L3 职责 |
| **whitepaper/00_INDEX.md** | 「Layer 2 执行引擎」→「L2 控制面；执行在 L3」 |
| **whitepaper/05_LAYER1_NEXUS.md** | 「云端指挥中枢」→「平台」；明确与 L2/L3 解耦 |
| **whitepaper/V8_SINGULARITY_OS.md** | 标注 v8.0 历史，或迁移特性至 V2 文档 |

#### scripts/

| 文件 | 修改点 |
|------|--------|
| **README.md** | 移除 Ray、Dapr 依赖说明 |
| **run-daemon.ps1** | 确认无 Dapr 调用 |

#### tests/

| 文件 | 修改点 |
|------|--------|
| **conftest.py** | 移除 `import ray`、`ray_init` fixture，或标记为 `@pytest.mark.skip` |
| **test_plugin_loading.py** | 移除 `ray_cluster` 依赖，或跳过 |
| **test_jarvis_logic.py** | 移除 Ray 相关测试 |
| **integration/test_plugin_system.py** | 同上 |
| **integration/test_intent_planning.py** | `runtime.type` 改为非 ray |
| **integration/test_e2e_natural_language.py** | 同上 |
| **e2e/test_plugin_gateway_e2e.py** | 同上 |
| **performance/test_plugin_execution_performance.py** | 同上 |

#### skills_repo/

| 文件 | 修改点 |
|------|--------|
| **manifest.yaml**（各技能） | `runtime.type: ray` → `runtime.type: local` 或新类型 |
| **common/schemas/manifest.py** | 默认 `type` 改为 `"local"` |

#### environment.yml / requirements

| 文件 | 修改点 |
|------|--------|
| **environment.yml** | 移除 `dapr`、`dapr-ext-grpc`（若 L2 不用） |
| **core/environment.yml** | 同上 |
| **core/requirements.txt** | 移除 `ray`（若全面弃用） |

### 2.3 需新增

| 类型 | 路径/内容 | 说明 |
|------|-----------|------|
| **文档** | `docs/whitepaper/V2_MIGRATION_GUIDE.md` | v8.0 → V2 迁移指南 |
| **文档** | 更新 `06_LAYER2_EDGE.md` | L2 控制面完整说明（子账号、权限、Key、记忆、调度） |
| **文档** | 更新 `07_LAYER3_TERMINAL.md` | L3 单体 OpenClaw 完整说明 |
| **API** | L1 ↔ L2 关联 | main_user_id 与 L2 sub_accounts 的关联（若 L1 需管理） |
| **Schema** | cloud/nexus | 若需主/子账号，增加 `sub_accounts` 或与 L2 同步策略 |
| **集成** | clients/desktop ↔ l3_node | 桌面端与 l3_node 的集成（当前桌面端仍连 core/daemon） |

---

## 三、Rules 更新建议

| 规则文件 | 建议 |
|----------|------|
| **060-v8-singularity.mdc** | 重命名为 `065-v2-singularity.mdc`，更新为 V2：Agent 在 L3、L2 控制面、无 Ray |
| **050-distributed.mdc** | 补充「L3 单体执行、L2 协同调度」 |
| **030-layer1-nexus.mdc** | 明确 L1 为「平台」，与 L2/L3 解耦；移除 Supabase 若已去 BaaS |
| **000-structure.mdc** | 补充「agent_loop 过渡期在 core，目标迁移至 l3_node」 |
| **新增** | `065-v2-layer3-standalone.mdc` | 引用 ARCHITECTURE_V2_LAYER3_STANDALONE.md，约束 L3 实现 |

---

## 四、优先级建议

| 优先级 | 内容 |
|--------|------|
| **P0** | 修复 `task_planner.py`、`resource_allocator.py` 对已删除 `ray_cluster` 的导入（当前会导致 ImportError） |
| **P0** | 移除 `clients/desktop` 的 Dapr 依赖，统一直连后端 |
| **P1** | 移除 `core` 中 Ray 残留（agent_orchestrator、skill_runner、ray_actors） |
| **P1** | 更新 whitepaper 06、07 与 V2 对齐 |
| **P2** | 更新 rules（060、050、030） |
| **P2** | 清理 Docker Compose、config、tests 中的 Ray/Dapr |
| **P3** | 新增 V2 迁移指南；L1 与 L2 主/子账号模型对齐 |

---

## 五、总结（2026-03 更新）

- **V2 已落地**：`core/db/`、`core/api/routes/v2_*`、`core/security/`、`l3_node/`、`PROJECT_STRUCTURE.md`、rules、whitepaper 02/05/06/07、`docs/README.md`。
- **已修复**：`task_planner`、`resource_allocator` 已用占位；`clients/desktop` 已移除 Dapr，统一直连后端；`config/ray_config.yaml` 已删除；`GAP_ANALYSIS.md`、`IMPLEMENTATION_STATUS.md`、`IMPLEMENTATION_V2.md` 已删除。
- **已更新**：whitepaper 02（L1 平台、L2 控制面、L3 执行）、05（L1 平台化）、V8_SINGULARITY_OS（历史标注）、LAYER3_L2_WAN（V2 版本）、rules 030/050/060。
- **待清理**：Ray 在 `agent_orchestrator`、`skill_runner`、tests 中仍存在（非阻塞）；Docker Compose 中 Ray/Dapr 服务可注释。
