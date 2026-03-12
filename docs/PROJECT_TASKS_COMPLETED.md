# Jachin Nexus — 已完成任务清单

**文档版本**: v1.0  
**最后更新**: 2026-03  
**说明**: 按「框架/设计/基础功能」「最新功能落实」「技能相关」三大类罗列已完成任务项。**当前架构**：[ARCHITECTURE.md](./ARCHITECTURE.md)

---

## 一、框架、设计、基础功能、后续设计

### 1.1 架构框架（6项）

1. [x] V2 三层架构确立：L1 平台 / L2 控制面 / L3 单体执行面
2. [x] L2 不代理推理：L3 持密文 Key 直连 LLM API
3. [x] 架构宪法：弃用 Dapr、Ray 集群、本地 PostgreSQL、Qdrant
4. [x] L3 单体对标 OpenClaw：多 Agent、多 Skill、本地记忆、可协同
5. [x] L2 与 L3 可同机部署
6. [x] 项目结构宪法与目录树规范（`PROJECT_STRUCTURE.md`）

### 1.2 核心设计文档（7项）

1. [x] `ARCHITECTURE_V2_LAYER3_STANDALONE.md` — L3 单体架构规范
2. [x] `V2_ARCHITECTURE_DIAGRAM.md` — 架构图与流程图
3. [x] `L2_GATEWAY_CLUSTER_ARCHITECTURE.md` — L2 集群架构
4. [x] `LAYER3_L2_WAN_ARCHITECTURE.md` — L3-L2 广域网架构
5. [x] 架构合规（见 ARCHITECTURE.md、CLOUD_EDGE_AI_OS_IMPLEMENTATION_ANALYSIS.md）
6. [x] 白皮书 v8.0 (The Singularity OS)：`whitepaper/00_INDEX.md` 及系列
7. [x] `.cursor/rules/` 规则与架构同步

### 1.3 数据与存储（6项）

1. [x] SQLite 作为 L2 主存储：`sub_accounts`、`api_keys_vault`、`l3_nodes`、`gateway_admins`
2. [x] LanceDB 向量记忆：`l2_memory_lancedb.py`，namespace 隔离
3. [x] Redis 可选：L3 在线状态、任务队列、Leader 选举（`l3_redis_state.py`）
4. [x] PostgreSQL：L1 云端（`cloud/nexus/`）
5. [x] 生物学记忆管线：海马体 + 大脑皮层，SQLite 极简存储
6. [x] Dream Weaver：LanceDB 聚类/去重/融合，`is_consolidated`，冲突消解

### 1.4 安全与权限（6项）

1. [x] 零信任 API Key：L3 RSA 密钥对，L2 公钥加密下发
2. [x] `JACHIN_L2_MASTER_KEY` 加密 L2 存储
3. [x] 子账号 RBAC：`memory:read`、`memory:write`、`coordinate:task`、`keys:read`
4. [x] `allowed_skills` 白名单、`allowed_memory_namespaces` 记忆隔离
5. [x] L1 `global_banned_skills` 全局封禁
6. [x] 工作区沙箱：Native 工具限制在 `~/.jachin/workspace/`

### 1.5 基础服务与引导（7项）

1. [x] FastAPI 主应用：整合 Chat、Voice、Skills、Orchestrator、Monitoring、Config、Console、V2 路由
2. [x] L2 引导：`ensure_default_sub_account`、`sync_api_keys_from_env`
3. [x] L1-L2 创世溯源：配对成功后默认子账号写入 `l1_pairing_code`
4. [x] 健康检查 `/health`、根路径 `/`、测试 `/test`
5. [x] CORS 配置、UTF-8 JSON 响应
6. [x] 日志配置：控制台 + 文件，`ConsoleLogHandler` 供思维流
7. [x] 访问日志过滤：屏蔽高频轮询请求

### 1.6 配置与运维（6项）

1. [x] 推理策略：`power` / `default` / `perf` / `god`
2. [x] 配置管理：`cluster.yaml`、`skills_config.yaml`、`model_config.yaml`、`personalities.yaml`
3. [x] Admin UI：`/admin/`
4. [x] Hive Dashboard：`/hive/`
5. [x] L1 订阅拦截中间件：欠费返回 402
6. [x] Sync Daemon：L2 向 L1 心跳，Redis Leader 选举

### 1.7 废弃与清理（5项）

1. [x] 废弃 Dapr：移除 `core/dapr/`、`dapr/`、Dapr 相关脚本
2. [x] 废弃 Ray Cluster：移除 `core/brain/ray_cluster/`
3. [x] 废弃旧 memory 架构：移除 `core/memory/`
4. [x] 废弃过时文档：30+ 旧设计文档
5. [x] 废弃臃肿脚本：setup.ps1/sh、start-full、dapr_restart_scheduler 等

---

## 二、最新功能的落实和修改

### 2.1 v0.8.5 (2026-03)（2项）

1. [x] L1-L2 配对码溯源：配对成功后 6 位码写入默认子账号 `l1_pairing_code`
2. [x] 版本号统一至 v0.8.5

### 2.2 v8.0 (The Singularity OS)（8项）

1. [x] 全链路 runId 追踪：SensoryInputEvent → PipelineContext → SensoryOutputEvent，日志 `[RunID: xxx]`
2. [x] 流式神经 (Streaming Chunk)：`generate_response_stream` + `on_chunk`，caps 含 `stream_chunk` 时实时推送
3. [x] Session Multiplexing：按 `session_id` 隔离 Agent Actor，多路输入零串话
4. [x] Nexus Hook Pipeline：Koa 风格洋葱中间件，5 个生命周期 Hook
5. [x] Dream Weaver Consolidation：LanceDB 聚类/去重/融合，冲突消解
6. [x] Capability Negotiation：L3 Manifest 握手，按 caps 动态推送
7. [x] Edge Mesh Swarm：heavy_tools 外包至虫群节点
8. [x] 白皮书、规格、`.cursor/rules` 统一至 v8.0

### 2.3 V2 API 与认证（7项）

1. [x] `POST /api/v2/auth/sync` — L3 注册
2. [x] `GET /api/v2/auth/poll` — L3 轮询审批
3. [x] `GET /api/v2/keys` — L3 拉取加密 Key
4. [x] `GET /api/v2/auth/heartbeat` — L3 心跳
5. [x] `POST /api/v2/auth/check` — L2 校验
6. [x] `POST /api/v2/admin/login-with-l1` — L1-L2 登录
7. [x] `permissions_snapshot` 下发：`allowed_skills`、`service_switches`

### 2.4 记忆与协同（6项）

1. [x] `POST /api/v2/memory/sync` — L3 同步记忆
2. [x] `GET /api/v2/memory/search` — 向量检索，namespace 隔离
3. [x] `POST /api/v2/coordinate/task` — 创建协同任务
4. [x] `GET /api/v2/coordinate/poll` — L3 长轮询子任务
5. [x] `POST /api/v2/coordinate/result` — 提交子任务结果
6. [x] `GET /api/v2/coordinate/status` — 任务状态

### 2.5 桌面客户端与 L3（5项）

1. [x] Tauri v2 桌面精灵：聊天、JachinLink、控制台 HUD
2. [x] L3 WebSocket 服务：`127.0.0.1:18981`，manifest 握手
3. [x] L3 进程拉起：`l3_spawn.rs` 通过 `tauri_plugin_shell` 启动
4. [x] SensoryOverlay、ChatPanel、ChatUI 等前端组件
5. [x] useSensoryWebSocket、messageStorage 等前端逻辑

### 2.6 语音与 TTS（4项）

1. [x] Voice API：recognize、synthesize、synthesize-stream、process、chat、intent、voices
2. [x] STT：Whisper / 阿里云
3. [x] TTS：Edge TTS / 阿里云
4. [x] 语音唤醒 (Hey Jachin) 集成

### 2.7 控制台 HUD（7项）

1. [x] 思维流日志：`/api/v3/logs/recent`、`/api/v3/logs/stream`
2. [x] 记忆检索、删除、批量删除、计数
3. [x] 模型列表与切换、推理策略
4. [x] 建议列表与执行
5. [x] 日历事件 CRUD、待办
6. [x] GPU 状态、过热检测
7. [x] LLM 上下文重置

### 2.8 管理后台（3项）

1. [x] 子账号 CRUD、节点分配、Key 管理
2. [x] 节点列表、过期节点、清理、删除
3. [x] `v2_devices` 设备列表 API

### 2.9 编排与编排器（4项）

1. [x] `POST /api/v3/orchestrator/plan` — 任务规划
2. [x] `POST /api/v3/orchestrator/execute` — 任务执行
3. [x] `GET /api/v3/orchestrator/intent` — 意图解析
4. [x] `POST /api/v3/orchestrator/invoke` — 插件调用

### 2.10 近期修改（未提交）（10项）

1. [x] `.env.example` 更新
2. [x] `pairing.rs`、`l3_spawn.rs` 修改
3. [x] `chat.tsx`、`ChatUI.tsx`、`ChatPanel.tsx` 修改
4. [x] `SensoryOverlay.tsx`、`JachinLink.tsx` 修改
5. [x] `useSensoryWebSocket.ts`、`api.ts`、`messageStorage.ts` 修改
6. [x] `v2_admin.py`、`v2_auth.py`、`voice.py`、`tts.py` 修改
7. [x] `bootstrap.py`、`main.py`、`logger.py`、`config` 修改
8. [x] `qwen_adapter_v2.py`、`llm_provider.py` 修改
9. [x] `l3_node`：`__main__.py`、`agent_core.py`、`bootstrap.py`、`llm_client.py`、`ws_server.py` 修改
10. [x] 新增 `v2_devices.py`、`L3_KEY_AND_ENV_ANALYSIS.md`、`fix-gateway-config.ps1`、`fix_l2_keys_after_master_key_reset.py`、`run_l3.ps1`

---

## 三、技能相关的工作

### 3.1 三轨道技能体系（3项）

1. [x] 轨道 A：MCP 规范与设计（`MCP_SPEC.md`）
2. [x] 轨道 B：SKILL.md 声明式技能（`SKILL_MD_SPEC.md`），`skills_repo/` 热加载
3. [x] 轨道 C：JPP Wasm 沙箱，零信任第三方插件

### 3.2 L3 技能加载器（6项）

1. [x] `l3_node/skills/loader.py` — Native Core + JPP Wasm 扫描
2. [x] Native 工具：`core:fs_read`、`core:fs_write`、`core:shell_exec`
3. [x] JPP Wasm 扫描：`wasm_plugins/` 下 `.wasm` + `plugin.json` 或 `{id}.json`
4. [x] `load_tools()`、`run_tool()`、`build_tools_description()`
5. [x] `allowed_skills` 白名单硬拦截：`is_tool_allowed()`、`_build_allowed_ids()`
6. [x] L3 独立运行时 Native 兜底实现（无 core 依赖时）

### 3.3 JPP Wasm 执行（4项）

1. [x] `core/wasm_runner.py` — Wasmtime 执行
2. [x] `run_wasm_plugin()` — 燃料熔断、stdin/stdout JSON
3. [x] WASI 模式：`run_plugin_wasi()`、`stdin_json` 自动走 WASI
4. [x] 工具 ID 格式：`jpp:{plugin_id}`

### 3.4 L2 技能系统 (PluginManager)（4项）

1. [x] `core/system/plugin_manager.py` — 从 `skills_repo/` 加载
2. [x] 目录优先级：`_bundled`、`drivers`、`apps`、根目录
3. [x] `core/runtime/skill_runner.py` — Ray Actor、Docker、Wasm 沙箱
4. [x] `common/schemas/manifest.py` — SkillManifest、Capability、RuntimeConfig、DeploymentStrategy

### 3.5 内置技能（6项）

1. [x] `com.jachin.files` — 文件列表、搜索
2. [x] `com.jachin.calendar` — 日历
3. [x] `com.jachin.voip` — VoIP
4. [x] `com.jachin.os-mate` — 系统伴侣
5. [x] `com.jachin.web-surfer` — 网页浏览
6. [x] `com.jachin.sys-monitor` — 系统监控

### 3.6 技能 API（10项）

1. [x] `GET /api/v3/skills` — 列表
2. [x] `GET /api/v3/skills/{id}` — 详情
3. [x] `POST /api/v3/skills` — 注册
4. [x] `DELETE /api/v3/skills/{id}` — 删除
5. [x] `POST /api/v3/skills/{id}/execute` — 执行
6. [x] `POST /api/v3/skills/{id}/enable`、`/disable` — 启用/禁用
7. [x] `GET /api/v3/skills/{id}/health` — 健康检查
8. [x] `POST /api/v3/skills/reload`、`/{id}/reload` — 重载
9. [x] `GET /api/v3/skills/stats` — 统计
10. [x] `GET /api/v3/skills/debug/discovery` — 调试发现

### 3.7 向量路由与技能发现（3项）

1. [x] `core/vector_router.py` — SemanticRouter
2. [x] Intent → embedding → LanceDB 余弦相似度 → 最佳技能
3. [x] 引擎：Cloud (OpenAI) / Edge (ONNX)

### 3.8 技能权限与白名单（4项）

1. [x] `allowed_skills` 在 `permissions_json` 中配置
2. [x] 通过 `auth/poll` 下发至 L3
3. [x] 格式支持：`core:xxx`、`jpp:xxx`、`xxx`（自动补 `core:`）
4. [x] manifest 中 `permissions` 字段供 LiveTile 展示

### 3.9 JPP 开发者脚手架（4项）

1. [x] `jachin-plugin-sdk/` — Rust 模板，plugin.json、Makefile、标准 ABI
2. [x] `jachin-plugin-sdk-python/` — `@jachin_plugin` 装饰器、stdin/stdout JSON、py2wasm
3. [x] 示例：智能灯泡、数据清洗、fetch_crypto_price
4. [x] plugin.json：royalty_fee、schema（input/output）

### 3.10 技能配置与规范（5项）

1. [x] `config/skills_config.yaml` — repo_path、runtime、wasmtime、marketplace
2. [x] `docs/JMP_SPEC.md` — JMP 协议
3. [x] `docs/PLUGIN_SECURITY_SANDBOX.md` — 插件安全沙箱
4. [x] `docs/REVENUE_AND_ROYALTY_SPEC.md` — 分润规范
5. [x] `common/schemas/` — manifest、skill、jmp、auth、license、sdui
