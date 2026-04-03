# Skill / MCP 流转与近期变更文档

**版本**: V2 (2026-03)  
**状态**: 当前实现基准  
**定位**: 三层架构中 Skill 与 MCP 的完整流转、云端上传流程、近期代码变更说明

---

## 一、近期修改摘要

### 1.1 HR 简历透视镜技能 (hr-analyzer4)

| 路径 | 说明 |
|------|------|
| `skills_repo/hr-analyzer4/` | Rust Wasm 源码、plugin.json、main.wasm |
| `l3_node/skills/wasm_plugins/hr-analyzer4/` | 内置版本，与 execute ABI 兼容 |

**功能**：根据岗位 JD 对候选人简历进行严苛评估，输出 Markdown 报告（综合评分、优劣势、录用建议）。

**参数**：`target_role`（如 `backend_engineer`）、`resume_filename`（如 `zhangsan_resume.md`）。

**Host 函数**：
- `mcp_read_file(path_ptr, path_len)`：L3 本地有则直读，否则经 L2 `/api/v2/mcp/invoke`（L2 委托至具备工具的 L3；**兼容** HTTP peer 链）读简历/JD
- `llm_complete(prompt_ptr, prompt_len)`：调用 L3 本地 LLM

**依赖**：L3 已配对并持有 API Key；简历/JD 读取优先 L3 本地，无则 L2 委托。

---

### 1.2 core/wasm_runner.py

| 变更 | 说明 |
|------|------|
| **llm_complete 模型规范化** | L2 可能返回裸模型名（无 `dashscope/` 前缀），LiteLLM 需带前缀。调用前使用 `engine._normalize_model(model)`；主模型与降级以 `core.llm_provider` / L3 配置为准。 |
| **mcp_read_file 本地直读** | 本地绝对路径且文件存在时直接读取，否则解析为 `project_root/data/hr_resumes` 或 `project_root/config/hr_jds`，再经 L2 委托或 L2 MCP。 |
| **execute ABI** | 支持 Rust JPP 插件的 `execute(ptr, len) -> i32`，提供 `__rust_alloc`/`__rust_dealloc` 等 host 函数。 |
| **WASI 回退** | execute ABI 不可用时回退 WASI；若错误含 `__rust_dealloc`，抛出明确提示，避免误用纯 WASI。 |

---

### 1.3 l3_node/skills/loader.py

| 变更 | 说明 |
|------|------|
| **技能 ID 统一** | 内置 `hr-analyzer4` 统一用 `jpp:com.jachin.hr.analyzer4`（L1 发布 id）。 |
| **wasm 路径** | `jpp:com.jachin.hr.analyzer4` 使用 `l3_node/skills/wasm_plugins/hr-analyzer4/main.wasm`。 |
| **resume_path / jd_path 注入** | `target_role` 为 `backend_engineer` 时注入 `jd_path`，由 Wasm 通过 MCP 读取，避免 JSON 转义问题。 |

---

### 1.4 l3_node/http_server.py

| 变更 | 说明 |
|------|------|
| **同名去重** | `_tools_to_skill_infos` 按 `(name, version)` 去重，避免多次同步或重启后技能重复展示。 |

---

### 1.5 配置与数据路径

| 路径 | 说明 |
|------|------|
| `config/hr_jds/backend_engineer.md` | 后端工程师 JD |
| `config/local-hr-fs/config.json` | MCP `local-hr-fs`，允许 `data/hr_resumes`、`config/hr_jds` |
| `data/hr_resumes/` | 简历目录 |
| `data/hr_analysis/` | 分析报告输出目录（HR 透析镜执行后自动写入） |
| `~/.jachin/volumes/hr_analysis_output_4/` | HR 透析镜 4 数据卷，分析报告同时写入 |

### 1.6 控制台自然语言与持久化（已支持回退）

| 入口 | 调用路径 | 是否持久化 |
|------|----------|------------|
| **控制台军械库自然语言输入框** | `invokePlugin(q)` → L2 编排器；若 404 则回退 L3 `POST /api/v3/agent/run` → `run_agent` → `run_tool` → `persist_hr_analysis_result` | ✅ 回退后生成文件 |
| **直接点击技能执行按钮** | `executeSkill(id)` → L3 `POST /api/v3/skills/{id}/execute` → `run_tool` → `persist_hr_analysis_result` | ✅ 生成文件 |
| **主 Chat 界面**（连接 L3 WebSocket） | `run_agent` → `run_tool` → `persist_hr_analysis_result` | ✅ 生成文件 |

**实现**：当 L2 编排器返回 404（无匹配插件）时，前端自动调用 L3 `POST /api/v3/agent/run`，由 L3 Agent 理解自然语言并调用 HR 透析镜等 Wasm 技能，执行 `run_tool` 后触发 `persist_hr_analysis_result`，报告写入 `data/hr_analysis/` 和 `~/.jachin/volumes/hr_analysis_output_4/`。

---

## 二、云端上传流程

### 2.1 前置条件

- L1 Nexus 已启动（`cd cloud/nexus && npm run dev`）
- 环境变量 `JACHIN_DEV_TOKEN` 已配置（与 `cloud/nexus/.env.local` 一致）

### 2.2 步骤

```powershell
# 1. 进入技能目录
cd skills_repo/hr-analyzer4

# 2. 编译 Wasm（Rust）
cargo build --target wasm32-unknown-unknown --release
Copy-Item target\wasm32-unknown-unknown\release\hr_analyzer4.wasm main.wasm

# 3. 打包
jachin pack
# 输出: dist/com.jachin.hr.analyzer4_v1.0.0.zip

# 4. 发布
jachin publish --visibility PUBLIC --price 0   # 完整上传
jachin publish --visibility PRIVATE            # 仅元数据（影子上传）
```

### 2.3 jachin pack 校验

- `plugin.json` 必含：`id`、`name`、`description`、`version`
- `id` 格式：反向域名，如 `com.jachin.hr.analyzer4`
- Skill 类型需存在 `entry`（默认 `main.wasm`）

### 2.4 jachin publish 行为

| 可见性 | 行为 |
|--------|------|
| **PUBLIC** | 上传 zip 至 L1 `POST /api/v1/store/publish`，需管理员审核 |
| **PRIVATE** | 仅登记元数据（shadow_only），实体包需侧载到 L2 |

### 2.5 L1 处理

- `cloud/nexus` 接收 multipart zip，解析 `plugin.json`
- 写入 `plugins_registry`（id、name、version、package_url、visibility、status）
- PUBLIC：`status=pending` → 管理员审核 → `approved`

---

## 三、三层架构 Skill 流转

### 3.1 总览

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  L1 (cloud/nexus) — 平台商城                                                 │
│  plugins_registry、user_licenses、审核、订阅                                  │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ CloudSyncDaemon 轮询 manifest
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  L2 (core/) — 控制面 + 数字仓库                                               │
│  ~/.jachin/inventory/skills/、role_permissions、API Key 保险箱               │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ L3 skill_sync / mcp_sync / perform_startup_sync
                                    │ GET /skills、GET /l3_mcps、GET /download
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  L3 (l3_node/ + clients/desktop) — 执行面                                    │
│  ~/.jachin/l3_skill_cache/、~/.jachin/l3_mcp_cache/、wasm_plugins/          │
│  core.wasm_runner、loader.run_tool                                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 详细流转

| 阶段 | 组件 | 行为 |
|------|------|------|
| **发布** | 创作者 | `jachin pack` → `jachin publish` → L1 `POST /store/publish` |
| **订阅** | 租户 | L1 Store 订阅 → `user_licenses`（tenant_id, item_id, ACTIVE） |
| **L2 同步** | `core/sync_daemon.py` | 轮询 `GET /api/v1/sync/manifest` → 下载到 `~/.jachin/inventory/skills/<item_id>/` |
| **L2 分配** | L2 Admin | 角色勾选物资 → `role_permissions`；子账号绑定角色 |
| **L3 同步** | `l3_node/skill_sync.py` | L3 获批后：`POST /trigger-sync` → `GET /skills` → `GET /download` → `~/.jachin/l3_skill_cache/<uuid>/` |
| **L3 加载** | `l3_node/skills/loader.py` | 扫描 `wasm_plugins/` + `l3_skill_cache/` → `load_skills_for_ui()`、`load_tools()` |
| **L3 执行** | `core/wasm_runner.py` | `run_tool(jpp:xxx)` → `run_wasm_plugin()` → execute ABI / WASI |

### 3.3 关键 API

| 接口 | 说明 |
|------|------|
| `GET /api/v1/sync/manifest` | L1 返回租户已购清单 |
| `GET /api/v2/inventory/skills` | L2 技能清单（需 X-Sub-Account-Id） |
| `GET /api/v2/inventory/skills/{id}/download` | L2 下载技能包 |
| `GET /api/v2/inventory/l3_mcps` | L2 L3_LOCAL MCP 清单（供 L3 mcp_sync 拉取） |
| `GET /api/v2/inventory/l3_mcps/{id}/download` | L2 下载 L3_LOCAL MCP 包 |
| `POST /api/v2/inventory/trigger-sync` | L3 触发 L2 从 L1 同步 |
| `GET /api/v3/skills` | L3 技能列表（供 Skill Matrix） |
| `POST /api/v3/skills/{skill_id}/execute` | L3 执行技能 |

### 3.4 存储路径

| 路径 | 说明 |
|------|------|
| `~/.jachin/inventory/skills/` | L2 从 L1 同步的技能 |
| `~/.jachin/inventory/l3_mcps/` | L2 从 L1 同步的 L3_LOCAL MCP |
| `~/.jachin/l3_skill_cache/` | L3 从 L2 下载的技能 |
| `~/.jachin/l3_mcp_cache/` | L3 从 L2 拉取的 MCP（mcp_sync 同步，mcp_registry 动态加载） |
| `l3_node/skills/wasm_plugins/` | 内置技能（如 hr-analyzer4） |

---

## 四、三层架构 MCP 流转

### 4.1 总览（规格 vs 现状）

**规格与实现对照**（单一维护点）：[MCP_EXECUTION_MODEL.md](./MCP_EXECUTION_MODEL.md)（v2.2）、[ARCHITECTURE_L3_MCP_HOST_AND_L2_TASK_MANAGER.md](./ARCHITECTURE_L3_MCP_HOST_AND_L2_TASK_MANAGER.md)（v0.4）。

本文 §4 仅保留 API/路径索引；流程、环境变量、Task Token、LOCAL_PINNED、NAT 降级说明以 **MCP_EXECUTION_MODEL** 为准。

### 4.2 MCP 执行策略（摘要）

| 场景 | 执行位置 | 说明 |
|------|----------|------|
| **本机有工具** | L3 本地 | stdio + l3_mcp_cache + 内置工具 |
| **本机无工具** | L2 TaskManager | Pull 优先；HTTP 入站须 `task_token`；候选节点 ∩ `l3_nodes` |
| **LOCAL_PINNED 工具** | 仅本 L3 | 禁止跨节点委托（`core/mcp_tool_locality.py`） |
| **复杂任务** | 多 L3 | L2 coordinate / 编排演进中 |

### 4.3 MCP 配置与运行位置（默认 L3）

- 配置：`~/.jachin/mcp_servers.json`（参考 `config/mcp_servers.json.example`）；侧载/同步的 MCP 描述仍在 `~/.jachin/inventory/mcps/`（L2 从 L1 同步后，**同一台用户机上的 L3** 读取并起 stdio 子进程）。
- L3_LOCAL MCP：L2 同步到 `inventory/l3_mcps/`，L3 拉取到 `l3_mcp_cache/` 动态加载（Python 模块型）。
- L2_GATEWAY（清单语义）：**长期默认由 L3 执行 stdio**；仅在 **`JACHIN_L2_STDIO_MCP=1`** 时由 L2 进程侧载 stdio（兼容/回滚）。跨节点代跑仍走 **Pull + HTTP 回退**，与 TaskManager 一致。

### 4.4 L3 调用 MCP 的两种方式

| 方式 | 说明 |
|------|------|
| **Agent 直接调用** | `mcp_registry.invoke` 本机优先；否则 `invoke_via_l2` |
| **Wasm host 函数** | `mcp_read_file`：本地直读优先；否则 `POST /api/v2/mcp/invoke` |

### 4.5 MCP 关键 API

| 接口 | 说明 |
|------|------|
| `GET /api/v2/mcp/tools` | 默认：**Redis 聚合**各 L3 `mcp_tools`；`JACHIN_L2_STDIO_MCP=1` 时合并 L2 本机侧载 |
| `POST /api/v2/mcp/invoke` | L3 缺工具入口；L2 **委托**（Pull / HTTP）；仅回滚标志开启时 L2 本机 stdio |
| `POST /api/v3/mcp/execute` | L2 对 **可达** peer 触发本机 MCP（须 `task_id`+`task_token`；`JACHIN_L3_MCP_EXECUTE_ALLOW_LEGACY=1` 可跳过） |

### 4.6 mcp_read_file 实现逻辑（wasm_runner）

1. 本地绝对路径且存在 → 直接读取
2. 否则解析为 `project_root/data/hr_resumes/{filename}` 或 `config/hr_jds/{filename}`
3. L3 本地有 read_file 则本地执行；否则调用 `POST /api/v2/mcp/invoke` 请求 L2 委托

---

## 五、关键文件索引

| 文件 | 职责 |
|------|------|
| `core/wasm_runner.py` | Wasm 沙箱、execute ABI、host 函数（mcp_read_file、llm_complete） |
| `l3_node/skills/loader.py` | 技能扫描、ID 统一、路径覆盖、resume/jd 注入 |
| `l3_node/skill_sync.py` | L3 从 L2 同步技能到 l3_skill_cache |
| `l3_node/mcp_sync.py` | L3 从 L2 同步 L3_LOCAL MCP 到 l3_mcp_cache |
| `l3_node/mcp_stdio_bootstrap.py` | L3 内嵌 stdio MCP Host（`MCPManager` + inventory 扫描） |
| `l3_node/http_server.py` | GET /api/v3/skills、POST /execute，技能去重 |
| `l3_node/llm_client.py` | LiteLLMEngine、_normalize_model |
| `core/api/routes/v2_mcp.py` | L2 MCP tools / invoke（TaskManager 委托 + 可选本机 stdio） |
| `core/l2_stdio_mcp_flag.py` | `JACHIN_L2_STDIO_MCP` 回滚开关 |
| `core/mcp_task_token.py` | 跨节点委托 Task Token |
| `core/mcp_tool_locality.py` | LOCAL_PINNED |
| `core/l3_node_db_filter.py` | 委托目标 ∩ SQLite `l3_nodes` |
| `core/api/routes/v2_inventory.py` | L2 技能清单与下载 |
| `tools/jachin-cli/src/jachin_cli/commands/pack.py` | jachin pack |
| `tools/jachin-cli/src/jachin_cli/commands/publish.py` | jachin publish |
| `skills_repo/hr-analyzer4/` | HR 简历透视镜源码 |
| `config/local-hr-fs/config.json` | MCP local-hr-fs 配置 |
| `config/hr_jds/` | JD 配置目录 |

---

## 六、相关文档

- [ARCHITECTURE_L3_MCP_HOST_AND_L2_TASK_MANAGER.md](./ARCHITECTURE_L3_MCP_HOST_AND_L2_TASK_MANAGER.md) — L3 MCP Host + L2 TaskManager 规格 v0.4
- [MCP_EXECUTION_MODEL.md](./MCP_EXECUTION_MODEL.md) — 目标态与兼容实现对照
- [ARCHITECTURE.md](./ARCHITECTURE.md) — 一店一库总览
- [L1_L2_L3_END_TO_END_FLOW.md](./L1_L2_L3_END_TO_END_FLOW.md) — 端到端流程
- [MCP_SPEC.md](./MCP_SPEC.md) — MCP 接入规范
- [PAIRING_PROTOCOL_SPEC.md](./PAIRING_PROTOCOL_SPEC.md) — **L2↔L3** 配对（非 L1↔L3）；总述见 [ARCHITECTURE_L1_WORKSPACE_L2_GATEWAY_L3.md](./ARCHITECTURE_L1_WORKSPACE_L2_GATEWAY_L3.md)
- [L1_L2_PAIRING_AND_WEB_BRIDGE.md](./L1_L2_PAIRING_AND_WEB_BRIDGE.md) — L1-L2 控制面信任（网关邮箱 / Web Bridge / CLI 辅助）
