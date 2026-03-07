# Jachin Nexus — 已实现功能清单

**文档版本**: v1.0  
**最后更新**: 2026-03  
**说明**: 本文档从系统、流程、插件三个维度梳理项目已实现功能。**架构规范**：[ARCHITECTURE.md](./ARCHITECTURE.md) | **实现度分析**：[CLOUD_EDGE_AI_OS_IMPLEMENTATION_ANALYSIS.md](./CLOUD_EDGE_AI_OS_IMPLEMENTATION_ANALYSIS.md)

---

## 一、系统层面 (System)

### 1.1 分层架构 (V2)

| 层级 | 职责 | 目录 | 技术栈 |
|------|------|------|--------|
| **Layer 1** | 平台：用户主账号、配对、舰队、Forge、IM Webhook | `cloud/nexus/` | Next.js + Drizzle ORM + Auth.js |
| **Layer 2** | 控制面：子账号、权限、API Key 保险箱、记忆、L3 协同 | `core/` | Python 3.10+ + FastAPI |
| **Layer 3** | 执行面：ReAct Agent、LLM、工具、WebSocket | `l3_node/` + `clients/desktop/` | Python + Tauri v2 + React |

- L2 不代理推理：L3 持密文 Key 直连外部 LLM API
- 弃用 Dapr、Ray 集群、本地 PostgreSQL、Qdrant

### 1.2 核心组件

- **FastAPI 后端** (`core/main.py`): 整合 Chat、Voice、Skills、Orchestrator、Monitoring、Config、Console、V2 Auth/Admin/Memory/Coordinate/Devices
- **L2 引导** (`core/bootstrap.py`): 默认子账号创建、从 `.env` 同步 API Key（`DASHSCOPE_API_KEY`、`OPENAI_API_KEY`）
- **L3 WebSocket 服务** (`l3_node/ws_server.py`): 监听 `127.0.0.1:18981`，manifest 握手、intent → agent → chunk/answer/error
- **L3 进程拉起** (`clients/desktop/src-tauri/src/l3_spawn.rs`): 桌面端通过 `tauri_plugin_shell` 启动 L3 进程

### 1.3 数据存储

| 存储 | 用途 | 位置/表 |
|------|------|---------|
| **SQLite** | L2 控制面数据 | `~/.jachin/l2_control.db`：`sub_accounts`、`api_keys_vault`、`l3_nodes`、`gateway_admins` |
| **LanceDB** | 向量记忆 | `~/.jachin/lancedb_data`，`memories` 表，支持 namespace 隔离 |
| **Redis** | L2 集群化时 L3 在线状态、任务队列、Leader 选举 | 可选 |
| **PostgreSQL** | L1 云端 | `cloud/nexus/`：`edge_agents`、`organizations`、`blueprints`、`plugins_registry` |

### 1.4 安全机制

- **零信任 API Key** (`core/security/crypto_manager.py`): L3 生成 RSA 密钥对，L2 用 L3 公钥加密 Key 下发；`JACHIN_L2_MASTER_KEY` 加密 L2 存储
- **子账号权限** (`core/permissions.py`): `memory:read`、`memory:write`、`coordinate:task`、`keys:read`；`allowed_skills` 白名单；`allowed_memory_namespaces` 记忆隔离
- **L1 全局封禁** (`core/l1_policy.py`): `global_banned_skills` 从 allowed_skills 中剔除
- **工作区沙箱** (`l3_node/skills/loader.py`): Native 工具限制在 `~/.jachin/workspace/`

### 1.5 配置与部署

- **推理策略** (`core/main.py`): `power` / `default` / `perf` / `god`
- **配置管理** (`core/config/__init__.py`): `cluster.yaml`、`skills_config.yaml`、`model_config.yaml`、`personalities.yaml`
- **Admin UI** (`core/admin_ui/index.html`): L2 管理面板 `/admin/`
- **Hive Dashboard** (`core/web_ui/`): ROG 风格仪表盘 `/hive/`
- **L1 订阅拦截** (`core/middleware/l1_subscription.py`): 订阅欠费时返回 402

### 1.6 监控与运维

- **健康检查** (`/health`): 服务存活探测
- **监控 API** (`core/api/monitoring.py`): `/api/v3/monitoring/stats`、`/metrics`、`/errors`、`/alerts`、`/reset`
- **GPU 状态** (`core/api/console.py`): `/api/v3/gpu/stats`、`/gpu/overheat`
- **日志缓冲** (`core/api/console.py`): 环形缓冲区供思维流展示

---

## 二、流程层面 (Process / Workflow)

### 2.1 认证与配对

| 流程 | 接口 | 说明 |
|------|------|------|
| **L3 注册** | `POST /api/v2/auth/sync` | L3 提交 `device_fingerprint`、`public_key_pem` |
| **L3 轮询审批** | `GET /api/v2/auth/poll?node_id=` | 待审批返回 `pending`；已分配返回 `approved` + `encrypted_api_keys` |
| **L3 拉取 Key** | `GET /api/v2/keys` | 需 `X-Sub-Account-Id`，返回加密 Key |
| **L3 心跳** | `GET /api/v2/auth/heartbeat` | 更新 `last_seen_at` |
| **L2 校验** | `POST /api/v2/auth/check` | 校验 L3 公钥与节点状态 |
| **L1-L2 登录** | `POST /api/v2/admin/login-with-l1` | 使用 `nexus_config.json` 配对 |
| **L1 配对** | `cloud/nexus` | `POST /pairing/request` → 6 位码 → `POST /pairing/confirm` |

### 2.2 Agent 循环与钩子

- **ReAct 循环** (`l3_node/agent_core.py`): Thought → Action → Observation，`MAX_REACT_ITERATIONS=5`
- **Nexus Hook Pipeline** (`l3_node/engine/hooks_pipeline.py`): Koa 风格洋葱中间件
  - `on_intent_received` → `before_llm_think` → `before_tool_exec` → `after_tool_exec` → `before_response`
- **Sub-Agent 分身** (`l3_node/agent_core.py`): `delegate` 动作，角色：`coder`、`writer`、`researcher`、`default`
- **内置工具**: `recall_memory`（向 L2 检索记忆）、`coordinate`（向 L2 请求协同）、Native、JPP Wasm

### 2.3 记忆流程

| 接口 | 说明 |
|------|------|
| `POST /api/v2/memory/sync` | L3 同步本地记忆到 LanceDB |
| `GET /api/v2/memory/search` | 向量检索，支持 namespace 隔离 |
| **Dream Weaver** (`core/db/dream_weaver.py`) | 聚类、LLM 融合、冲突消解，short_term → long_term |
| **LanceDB 字段** | `id`、`vector`、`text`、`node_id`、`sub_account_id`、`timestamp`、`namespace`、`memory_tier` |

### 2.4 协同流程 (Edge Mesh)

| 接口 | 说明 |
|------|------|
| `POST /api/v2/coordinate/task` | 创建任务，按 `skill_required` 匹配 L3 节点 |
| `GET /api/v2/coordinate/poll` | L3 长轮询获取子任务 |
| `POST /api/v2/coordinate/result` | L3 提交子任务结果 |
| `GET /api/v2/coordinate/status` | 轮询任务状态与聚合结果 |

### 2.5 聊天与语音流程

| 接口组 | 端点 | 说明 |
|--------|------|------|
| **Chat V1** | `POST /api/chat` | 简单对话 |
| **Chat V2** | `POST /api/v2/chat/text` | 文本对话 |
| | `POST /api/v2/chat/image` | 图像理解 |
| | `POST /api/v2/chat/web-search` | 联网搜索 |
| | `POST /api/v2/chat/tools` | 工具调用 |
| | `GET /api/v2/chat/personalities` | 人格列表 |
| | `GET /api/v2/chat/capabilities` | 能力声明 |
| **Voice** | `POST /api/v2/voice/recognize` | 语音识别 (Whisper/阿里云) |
| | `POST /api/v2/voice/synthesize` | 语音合成 (Edge TTS/阿里云) |
| | `POST /api/v2/voice/synthesize-stream` | 流式 TTS |
| | `POST /api/v2/voice/process` | 语音 → 文本 → LLM → TTS 全流程 |
| | `POST /api/v2/voice/chat` | 语音对话 |
| | `POST /api/v2/voice/intent` | 意图路由 |
| | `GET /api/v2/voice/voices` | 可用音色列表 |

### 2.6 控制台流程 (HUD)

| 接口 | 说明 |
|------|------|
| `GET /api/v3/logs/recent` | 最近思维流日志 |
| `GET /api/v3/logs/stream` | 流式日志 |
| `GET /api/v3/memory/search` | 记忆检索 |
| `DELETE /api/v3/memory/{id}` | 删除单条记忆 |
| `POST /api/v3/memory/batch-delete` | 批量删除 |
| `GET /api/v3/memory/count` | 记忆数量 |
| `GET /api/v3/models` | 模型列表 |
| `POST /api/v3/models/current` | 切换当前模型 |
| `GET /api/v3/inference/strategy` | 推理策略 |
| `POST /api/v3/inference/strategy` | 设置推理策略 |
| `GET /api/v3/suggestions` | 建议列表 |
| `POST /api/v3/suggestions/{id}/execute` | 执行建议 |
| `GET /api/v3/calendar/*` | 日历事件 CRUD |
| `GET /api/v3/todos` | 待办列表 |
| `GET /api/v3/gpu/stats` | GPU 状态 |
| `GET /api/v3/gpu/overheat` | 过热检测 |
| `POST /api/v3/llm/context/reset` | 重置 LLM 上下文 |

### 2.7 L1-L2 同步

- **Sync Daemon** (`core/sync_daemon.py`): L2 向 L1 心跳 `/api/v1/edge/heartbeat`；Redis Leader 选举
- **L1 Policy** (`core/l1_policy.py`): `global_banned_skills`、`subscription_status`

### 2.8 管理流程 (Admin)

| 接口 | 说明 |
|------|------|
| `POST /api/v2/admin/login` | 管理员登录 |
| `GET /api/v2/admin/me` | 当前管理员信息 |
| `GET /api/v2/admin/sub-accounts` | 子账号列表 |
| `POST /api/v2/admin/sub-accounts` | 创建子账号 |
| `GET /api/v2/admin/nodes` | L3 节点列表 |
| `POST /api/v2/admin/nodes/assign` | 将节点分配给子账号 |
| `GET /api/v2/admin/nodes/stale` | 过期节点 |
| `POST /api/v2/admin/nodes/cleanup` | 清理过期节点 |
| `DELETE /api/v2/admin/nodes/{node_id}` | 删除节点 |
| `POST /api/v2/admin/keys` | 管理 API Key |

### 2.9 编排流程 (Orchestrator)

| 接口 | 说明 |
|------|------|
| `POST /api/v3/orchestrator/plan` | 任务规划 |
| `POST /api/v3/orchestrator/execute` | 任务执行 |
| `GET /api/v3/orchestrator/intent` | 意图解析 |
| `POST /api/v3/orchestrator/invoke` | 插件调用 |

---

## 三、插件层面 (Plugins / Skills)

### 3.1 三轨道技能体系

| 轨道 | 形态 | 信任级别 | 实现状态 |
|------|------|----------|----------|
| **A** | MCP (Model Context Protocol) | 高信任 | 规划中 |
| **B** | SKILL.md / manifest.yaml | 用户可控 | 已实现，`skills_repo/` |
| **C** | JPP Wasm | 零信任 | 已实现，`l3_node/skills/wasm_plugins/` |

### 3.2 Native Core 工具 (L3)

| 工具 ID | 说明 | 权限 |
|---------|------|------|
| `core:fs_read` | 读取文件 | 限于 `~/.jachin/workspace/` |
| `core:fs_write` | 写入文件 | 同上 |
| `core:shell_exec` | 执行 Shell 命令 | 工作目录死锁在 workspace，默认超时 30s |

### 3.3 JPP Wasm 插件 (L3)

- **加载** (`l3_node/skills/loader.py`): 扫描 `l3_node/skills/wasm_plugins/`，`.wasm` + `plugin.json` 或 `{id}.json`
- **执行** (`core/wasm_runner.py`): `run_wasm_plugin()` 通过 Wasmtime，燃料熔断
- **工具 ID 格式**: `jpp:{plugin_id}`
- **描述文件**: `id`、`name`、`description`、`parameters`、`entry`

### 3.4 L2 技能 (PluginManager)

- **PluginManager** (`core/system/plugin_manager.py`): 从 `skills_repo/` 加载技能，支持 `_bundled`、`drivers`、`apps`、根目录
- **SkillRunner** (`core/runtime/skill_runner.py`): 执行技能，支持 Ray Actor、Docker、Wasm 沙箱
- **Manifest** (`common/schemas/manifest.py`): `SkillManifest`、`Capability`、`RuntimeConfig`、`DeploymentStrategy`

### 3.5 内置技能 (`skills_repo/`)

| 技能 ID | 目录 | 能力 |
|---------|------|------|
| `com.jachin.files` | `_bundled/` | 文件列表、搜索 |
| `com.jachin.calendar` | `_bundled/` | 日历 |
| `com.jachin.voip` | `_bundled/` | VoIP |
| `com.jachin.os-mate` | `_bundled/` | 系统伴侣 |
| `com.jachin.web-surfer` | `apps/` | 网页浏览 |
| `com.jachin.sys-monitor` | `drivers/` | 系统监控 |

### 3.6 技能 API (L2)

| 接口 | 说明 |
|------|------|
| `GET /api/v3/skills` | 技能列表 |
| `GET /api/v3/skills/{id}` | 技能详情 |
| `POST /api/v3/skills` | 注册技能 |
| `DELETE /api/v3/skills/{id}` | 删除技能 |
| `POST /api/v3/skills/{id}/execute` | 执行技能 |
| `POST /api/v3/skills/{id}/enable` | 启用 |
| `POST /api/v3/skills/{id}/disable` | 禁用 |
| `GET /api/v3/skills/{id}/health` | 健康检查 |
| `POST /api/v3/skills/reload` | 全局重载 |
| `POST /api/v3/skills/{id}/reload` | 单技能重载 |
| `GET /api/v3/skills/stats` | 统计 |
| `GET /api/v3/skills/debug/discovery` | 调试发现 |

### 3.7 向量路由 (Skill Discovery)

- **SemanticRouter** (`core/vector_router.py`): Intent → embedding → LanceDB 余弦相似度 → 最佳技能
- **引擎**: Cloud (OpenAI) / Edge (ONNX)
- **SKILL.md** (`docs/SKILL_MD_SPEC.md`): YAML frontmatter + 自然语言指令正文

### 3.8 技能白名单 (L3)

- **allowed_skills**: L2 子账号 `permissions_json` 配置，通过 `auth/poll` 下发
- **格式**: `core:xxx`、`jpp:xxx` 或 `xxx`（自动补 `core:`）
- **硬拦截**: 未在白名单内的技能不加载、不执行

### 3.9 配置与脚手架

- **Skills 配置** (`config/skills_config.yaml`): `repo_path`、`runtime`（docker/wasm/native）、`wasmtime`、`marketplace`
- **JPP 脚手架**: `jachin-plugin-sdk-python`、`jachin-plugin-sdk`（Rust）
- **通用 Schema** (`common/schemas/`): `manifest.py`、`skill.py`、`jmp.py`、`auth.py`、`license.py`、`sdui.py`

---

## 四、客户端与入口

### 4.1 桌面精灵 (Tauri)

- **入口**: Tauri v2 + React
- **能力**: 聊天、语音唤醒 (Hey Jachin)、JachinLink 配对、控制台 HUD、SensoryOverlay
- **L3 通信**: WebSocket `ws://127.0.0.1:18981`，manifest 握手
- **API 直连**: `VITE_BACKEND_URL` 默认 `http://localhost:18888`

### 4.2 设备 API

- `GET /api/v2/devices`: 设备列表（L3 节点在线状态）

---

## 五、文件索引

| 功能域 | 主要文件 |
|--------|----------|
| L2 主应用 | `core/main.py` |
| L3 Agent | `l3_node/agent_core.py`、`l3_node/ws_server.py` |
| 认证与密钥 | `core/api/routes/v2_auth.py`、`core/security/crypto_manager.py` |
| 记忆 | `core/api/routes/v2_memory.py`、`core/db/l2_memory_lancedb.py`、`core/db/dream_weaver.py` |
| 协同 | `core/api/routes/v2_coordinate.py`、`core/l3_redis_state.py` |
| 钩子 | `l3_node/engine/hooks_pipeline.py` |
| L2 技能 | `core/api/skills.py`、`core/system/plugin_manager.py` |
| L3 技能 | `l3_node/skills/loader.py` |
| 向量路由 | `core/vector_router.py` |
| L1 配对 | `cloud/nexus/src/app/api/v1/pairing/` |
| 桌面客户端 | `clients/desktop/src/lib/api.ts`、`clients/desktop/src-tauri/src/l3_spawn.rs` |
| TTS/STT | `core/voice/tts.py`、`core/api/voice.py` |
