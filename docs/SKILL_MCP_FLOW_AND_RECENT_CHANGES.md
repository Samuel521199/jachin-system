# Skill / MCP 流转与近期变更文档

**版本**: V2 (2026-03)  
**状态**: 当前实现基准  
**定位**: 三层架构中 Skill 与 MCP 的完整流转、云端上传流程、近期代码变更说明

---

## 一、近期修改摘要

### 1.1 HR 简历透视镜技能 (hr-analyzer)

| 路径 | 说明 |
|------|------|
| `skills_repo/hr-analyzer/` | Rust Wasm 源码、plugin.json、main.wasm |
| `l3_node/skills/wasm_plugins/hr-analyzer/` | 内置版本，与 execute ABI 兼容 |

**功能**：根据岗位 JD 对候选人简历进行严苛评估，输出 Markdown 报告（综合评分、优劣势、录用建议）。

**参数**：`target_role`（如 `backend_engineer`）、`resume_filename`（如 `zhangsan_resume.md`）。

**Host 函数**：
- `mcp_read_file(path_ptr, path_len)`：通过 L2 MCP 读取简历/JD
- `llm_complete(prompt_ptr, prompt_len)`：调用 L3 本地 LLM

**依赖**：L2 MCP `local-hr-fs`（`config/local-hr-fs/config.json`）、L3 已配对并持有 API Key。

---

### 1.2 core/wasm_runner.py

| 变更 | 说明 |
|------|------|
| **llm_complete 模型规范化** | L2 可能返回 `qwen3.5-flash`，LiteLLM 需 `dashscope/qwen3.5-flash`。调用前使用 `engine._normalize_model(model)` 补全 provider 前缀。 |
| **mcp_read_file 本地直读** | 本地绝对路径且文件存在时直接读取，否则解析为 `project_root/data/hr_resumes` 或 `project_root/config/hr_jds`，再走 L2 MCP。 |
| **execute ABI** | 支持 Rust JPP 插件的 `execute(ptr, len) -> i32`，提供 `__rust_alloc`/`__rust_dealloc` 等 host 函数。 |
| **WASI 回退** | execute ABI 不可用时回退 WASI；若错误含 `__rust_dealloc`，抛出明确提示，避免误用纯 WASI。 |

---

### 1.3 l3_node/skills/loader.py

| 变更 | 说明 |
|------|------|
| **技能 ID 统一** | 内置 `hr-analyzer` 统一用 `jpp:com.jachin.hr.analyzer`（L1 发布 id），不再同时展示 `jpp:hr-analyzer`，避免重复。 |
| **jpp:hr-analyzer 兼容** | `run_tool("jpp:hr-analyzer")` 自动映射到 `jpp:com.jachin.hr.analyzer`。 |
| **wasm 路径覆盖** | `jpp:com.jachin.hr.analyzer` 优先使用 `l3_node/skills/wasm_plugins/hr-analyzer/main.wasm`，避免 L1 cache 版 `__rust_dealloc` 不兼容。 |
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
| `data/hr_analysis/` | 分析报告输出目录 |

---

## 二、云端上传流程

### 2.1 前置条件

- L1 Nexus 已启动（`cd cloud/nexus && npm run dev`）
- 环境变量 `JACHIN_DEV_TOKEN` 已配置（与 `cloud/nexus/.env.local` 一致）

### 2.2 步骤

```powershell
# 1. 进入技能目录
cd skills_repo/hr-analyzer

# 2. 编译 Wasm（Rust）
cargo build --target wasm32-unknown-unknown --release
Copy-Item target\wasm32-unknown-unknown\release\hr_analyzer.wasm main.wasm

# 3. 打包
jachin pack
# 输出: dist/com.jachin.hr.analyzer_v1.0.0.zip

# 4. 发布
jachin publish --visibility PUBLIC --price 0   # 完整上传
jachin publish --visibility PRIVATE            # 仅元数据（影子上传）
```

### 2.3 jachin pack 校验

- `plugin.json` 必含：`id`、`name`、`description`、`version`
- `id` 格式：反向域名，如 `com.jachin.hr.analyzer`
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
                                    │ L3 skill_sync / perform_startup_sync
                                    │ GET /api/v2/inventory/skills
                                    │ GET /api/v2/inventory/skills/{id}/download
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  L3 (l3_node/ + clients/desktop) — 执行面                                    │
│  ~/.jachin/l3_skill_cache/、l3_node/skills/wasm_plugins/                     │
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
| `POST /api/v2/inventory/trigger-sync` | L3 触发 L2 从 L1 同步 |
| `GET /api/v3/skills` | L3 技能列表（供 Skill Matrix） |
| `POST /api/v3/skills/{skill_id}/execute` | L3 执行技能 |

### 3.4 存储路径

| 路径 | 说明 |
|------|------|
| `~/.jachin/inventory/skills/` | L2 从 L1 同步的技能 |
| `~/.jachin/l3_skill_cache/` | L3 从 L2 下载的技能 |
| `l3_node/skills/wasm_plugins/` | 内置技能（如 hr-analyzer） |

---

## 四、三层架构 MCP 流转

### 4.1 总览

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  L2 — MCP 宿主                                                                │
│  ~/.jachin/mcp_servers.json、core/mcp_client.py、core/api/routes/v2_mcp.py   │
│  MCP 服务器（如 server-filesystem）运行在 L2 进程内                            │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ POST /api/v2/mcp/invoke
                                    │ { tool_name, arguments }
                                    │ X-Sub-Account-Id
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  L3 — Wasm 技能                                                               │
│  Wasm 通过 host 函数 mcp_read_file(path_ptr, path_len) 间接调用 L2 MCP       │
│  core/wasm_runner._mcp_read_file → httpx.post(L2 /api/v2/mcp/invoke)         │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 4.2 MCP 双轨制

| 轨道 | 形态 | 信任级别 | 流转 |
|------|------|----------|------|
| **A** | MCP 宿主 | 高信任 | 死锁 L2，绝不下发 L3；L3 通过 HTTP 代理调用 |
| **B** | Skill (.wasm) | 零信任 | L2 发放给 L3，沙箱运行；Wasm 内可调用 MCP（经 host 函数） |

### 4.3 L2 MCP 配置

- 配置：`~/.jachin/mcp_servers.json`（参考 `config/mcp_servers.json.example`）
- HR 简历透视镜依赖：`config/local-hr-fs/config.json` 中的 `local-hr-fs`
  - `server-filesystem` 允许：`__PROJECT_ROOT__`、`data/hr_resumes`、`config/hr_jds`

### 4.4 L3 调用 MCP 的两种方式

| 方式 | 说明 |
|------|------|
| **Agent 直接调用** | `l3_node/agent_core.py`：若 `action_name` 在 `mcp_registry.known_mcp_tools`，则 `mcp_registry.invoke_via_l2()` |
| **Wasm host 函数** | `mcp_read_file`：Wasm 内声明 `extern "C" { fn mcp_read_file(...) }`，宿主实现为 `POST /api/v2/mcp/invoke` |

### 4.5 MCP 关键 API

| 接口 | 说明 |
|------|------|
| `GET /api/v2/mcp/tools` | L2 返回 MCP 工具列表 |
| `POST /api/v2/mcp/invoke` | L2 执行 MCP 工具（需 X-Sub-Account-Id） |

### 4.6 mcp_read_file 实现逻辑（wasm_runner）

1. 本地绝对路径且存在 → 直接读取
2. 否则解析为 `project_root/data/hr_resumes/{filename}` 或 `config/hr_jds/{filename}`
3. 调用 `POST /api/v2/mcp/invoke`，`tool_name=read_file`，`arguments={path}`

---

## 五、关键文件索引

| 文件 | 职责 |
|------|------|
| `core/wasm_runner.py` | Wasm 沙箱、execute ABI、host 函数（mcp_read_file、llm_complete） |
| `l3_node/skills/loader.py` | 技能扫描、ID 统一、路径覆盖、resume/jd 注入 |
| `l3_node/skill_sync.py` | L3 从 L2 同步技能到 l3_skill_cache |
| `l3_node/http_server.py` | GET /api/v3/skills、POST /execute，技能去重 |
| `l3_node/llm_client.py` | LiteLLMEngine、_normalize_model |
| `core/api/routes/v2_mcp.py` | L2 MCP invoke |
| `core/api/routes/v2_inventory.py` | L2 技能清单与下载 |
| `tools/jachin-cli/src/jachin_cli/commands/pack.py` | jachin pack |
| `tools/jachin-cli/src/jachin_cli/commands/publish.py` | jachin publish |
| `skills_repo/hr-analyzer/` | HR 简历透视镜源码 |
| `config/local-hr-fs/config.json` | MCP local-hr-fs 配置 |
| `config/hr_jds/` | JD 配置目录 |

---

## 六、相关文档

- [ARCHITECTURE.md](./ARCHITECTURE.md) — 架构规范
- [L1_L2_L3_END_TO_END_FLOW.md](./L1_L2_L3_END_TO_END_FLOW.md) — 端到端流程
- [MCP_SPEC.md](./MCP_SPEC.md) — MCP 接入规范
- [PAIRING_PROTOCOL_SPEC.md](./PAIRING_PROTOCOL_SPEC.md) — L3-L2 配对
