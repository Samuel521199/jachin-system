# HR 招聘功能解耦战略计划

**版本**: 1.0  
**日期**: 2026-03  
**目标**: 将 HR 招聘功能从 L3 打包中完全解耦，实现「本地打包 → 云端发布 → 目标机订阅拉取」的完整闭环。

---

## 一、现状分析

### 1.1 当前 HR 招聘功能组成（架构：Skill 包 + MCP 包分离）

| 类型 | 组件 | 位置 | 说明 |
|------|------|------|------|
| **Skill 包** | hr-recruitment | `skills_repo/hr-recruitment/SKILL.md` | 纯 SKILL.md，定义全套流程：发布→打招呼→收网→分析→排行榜 |
| **MCP (L3 本地)** | com.jachin.hr.recruitment | `skills_repo/plugin/com.jachin.hr.recruitment/` | 纯工具：atom_post_job_boss、atom_greet_recommend_boss、add_automated_recruitment_task、stop_automated_recruitment、atom_lark_chat、**hr_analyze_resume** |
| **Skill (Wasm)** | hr-analyzer4 | `~/.jachin/inventory/skills/`（L2 侧载） | HR 透析镜底层实现，hr_analyze_resume MCP 包装调用 |
| **MCP (L2_GATEWAY)** | local-hr-fs | `~/.jachin/inventory/mcps/local-hr-fs/` | stdio 文件系统 MCP，访问 `data/hr_resumes`、`config/hr_jds` |
| **配置** | hr_jds | `config/hr_jds/` | JD 模板 |
| **数据** | hr_resumes | `data/hr_resumes/` | 简历存储 |

### 1.2 当前 L3 打包依赖（build_l3_sidecar.py）

```text
--hidden-import l3_node.hr_loader
```

HR 模块（recruitment_scheduler、recruitment_task、hr_analysis_persist）已迁入 `com.jachin.hr.recruitment` 包，通过 `hr_loader` 动态加载。`mcp_registry.py`、`http_server.py` 等通过 `hr_loader` 获取 HR 能力。

### 1.3 目标状态

- **L3 exe**：不再包含任何 HR 相关代码，体积更小，启动更快
- **Skill**：hr-analyzer4 等通过 L1 发布，L2 同步到 inventory，L3 skill_sync 拉取到 l3_skill_cache
- **MCP**：HR 招聘 MCP 通过 L1 发布，L2 同步到 inventory/l3_mcps，L3 mcp_sync 拉取到 l3_mcp_cache 动态加载
- **配置**：随包 config/manifest.yaml 写出到 ~/.jachin/config/
- **目标机**：仅 exe + 网络，订阅 HR 招聘后即可使用

---

## 二、战略阶段总览

| 阶段 | 名称 | 产出 |
|------|------|------|
| **Phase 0** | 现状盘点与依赖梳理 | 依赖清单、接口清单 |
| **Phase 1** | HR MCP 包化 | L3_LOCAL MCP 包（含 tools + 调度逻辑） |
| **Phase 2** | HR Skill 包化 | 可发布的 Skill 包 |
| **Phase 3** | L2_GATEWAY MCP 包化 | local-hr-fs 可订阅包 |
| **Phase 4** | L3 剥离与插件化 | L3 移除 HR 硬编码，改为动态发现 |
| **Phase 5** | 端到端验证 | 机器 A 打包上传 → 机器 B 拉取运行 |

---

## 三、Phase 0：现状盘点与依赖梳理

### 3.1 步骤

| 步骤 | 动作 | 产出 |
|------|------|------|
| 0.1 | 列出所有 HR 相关文件路径 | `docs/hr_decouple_inventory.md` |
| 0.2 | 绘制调用关系图（mcp_registry → tools → recruitment_*） | 调用链文档 |
| 0.3 | 列出 L3 对 HR 的入口（HTTP API、Agent 校验、技能 ID） | 入口清单 |
| 0.4 | 确认 L1/L2/L3 现有发布与拉取流程 | 流程确认 |

### 3.2 难点

- **隐式依赖**：`recruitment_task` 与 `loader`、`hr_analysis_persist` 的循环引用
- **路径硬编码**：`hr_data_paths` 中 `PLUGIN_DATA_ROOT`、`get_app_root()` 等需统一为可配置

### 3.3 验证方式

- [ ] 依赖清单无遗漏（grep 全仓 HR 相关 import）
- [ ] 调用链可追溯（从 HTTP 请求到 MCP 执行）
- [ ] 现有 BI 战报等非 HR 功能不受影响

---

## 四、Phase 1：HR MCP 包化

### 4.1 目标

将 L3 内置的 HR MCP 工具（read_file、atom_post_job_boss、atom_greet_recommend_boss、add_automated_recruitment_task、stop_automated_recruitment）及 recruitment_scheduler、recruitment_task、hr_analysis_persist 打包为 L3_LOCAL MCP 包，符合 `docs/L1_L2_L3_DEPLOYMENT_AND_SKILL_MCP_SPEC.md` 中 L3_LOCAL MCP 规范。

### 4.2 步骤

| 步骤 | 动作 | 产出 |
|------|------|------|
| 1.1 | 创建 MCP 包目录结构 `skills_repo/plugin/com.jachin.hr.recruitment/` | 目录 + plugin.json |
| 1.2 | 迁移 tools：hr_data_paths、atom_post_job_boss、boss_harvest_orchestrator、local_archiver、recruitment_status 等 | tools/*.py |
| 1.3 | 迁移调度逻辑：recruitment_scheduler、recruitment_task、hr_analysis_persist 到 tools/ 或 tools/recruitment/ | 自包含模块 |
| 1.4 | 编写 plugin.json：tools 数组，每项含 id、module、function、params、desc | plugin.json |
| 1.5 | 编写 config/manifest.yaml：写出 hr_jds、技能配置等到 ~/.jachin/config/ | config/ |
| 1.6 | 处理路径：所有 get_app_root()、PLUGIN_DATA_ROOT 改为从 ~/.jachin/ 或 config 读取 | 路径可配置 |
| 1.7 | 处理 APScheduler：确保单例在 MCP 包内初始化，与 L3 其他调度器（如 bi_scheduler）不冲突 | 调度隔离 |
| 1.8 | jachin pack 校验 | zip 包 |

### 4.3 难点

| 难点 | 说明 | 应对 |
|------|------|------|
| **调度器单例** | recruitment_scheduler 使用 APScheduler，需在 MCP 包加载时初始化，且与 bi_scheduler 共享同一 scheduler 实例（见 bi/scheduler.py） | 保留 L3 的 scheduler 宿主，MCP 包通过约定接口注册 job；或 MCP 包自带独立 scheduler，与 L3 主 scheduler 隔离 |
| **异步流式** | run_recruitment_task_stream 为 async generator，mcp_registry 的 _invoke_cached_mcp_tool 为同步 | HTTP /api/recruitment/start_task 需单独处理：从 MCP 包动态 import 并 await；或改为同步轮询模式 |
| **Wasm 调用** | recruitment_task 内部调用 Wasm 技能（hr-analyzer4） | 需确保 skill 路径从 l3_skill_cache 或 inventory 解析，不依赖项目根 |
| **Chrome CDP** | atom_post_job_boss、atom_greet_recommend_boss 依赖 Chrome 调试模式 | 无变化，仅路径从 MCP 包内解析 |

### 4.4 验证方式

| 验证项 | 方法 |
|--------|------|
| 包结构 | `unzip -l xxx.zip` 检查 plugin.json、tools/、config/ |
| 本地侧载 | 解压到 `~/.jachin/l3_mcp_cache/com.jachin.hr.recruitment/`，重启 L3，检查 mcp_registry 是否加载 |
| 工具调用 | 通过 Agent 或 API 调用 add_automated_recruitment_task，检查调度是否生效 |
| jachin pack | 执行 `jachin pack`，无报错 |

---

## 五、Phase 2：HR Skill 包化

### 5.1 目标

hr-analyzer4（及 hr-analyzer 等）已是 Wasm 技能，需确保其可独立打包、上传、订阅拉取。当前可能已在 inventory 侧载，需补齐 L1 发布流程。

### 5.2 步骤

| 步骤 | 动作 | 产出 |
|------|------|------|
| 2.1 | 确认 hr-analyzer4 的 plugin.json、main.wasm、config 结构 | 现状文档 |
| 2.2 | 按 076 规范补充 config/manifest.yaml（写出 hr_jds 等） | config/ |
| 2.3 | 声明 required_mcps：如 mcp:com.jachin.hr.recruitment（Phase 1 的 MCP 包）或 read_file | plugin.json |
| 2.4 | jachin pack 产出 zip | zip 包 |
| 2.5 | jachin publish 到 L1（测试环境） | 发布记录 |

### 5.3 难点

| 难点 | 说明 | 应对 |
|------|------|------|
| **依赖顺序** | Skill 依赖 MCP，需先发布 MCP 再发布 Skill | 按 Phase 1 → Phase 2 顺序 |
| **JD 配置** | hr_jds 需随 Skill 或 MCP 包写出，避免用户手动创建 | config/manifest.yaml 中 writes 包含 hr_jds 目录 |

### 5.4 验证方式

| 验证项 | 方法 |
|--------|------|
| 本地 pack | `jachin pack` 通过 |
| L1 发布 | `jachin publish` 成功，L1 审核通过 |
| L2 同步 | L2 订阅后 inventory/skills/ 出现对应目录 |
| L3 拉取 | L3 skill_sync 后 l3_skill_cache 有 wasm |
| 执行 | 控制面板执行 HR 透析镜，输出正常 |

---

## 六、Phase 3：L2_GATEWAY MCP（local-hr-fs）包化

### 6.1 目标

local-hr-fs 当前为 L2 侧载（inventory/mcps/local-hr-fs/），使用 @modelcontextprotocol/server-filesystem。需支持通过 L1 发布，L2 订阅拉取到 inventory/mcps/ 或 inventory/l3_mcps/（若归为 L3 使用）。

### 6.2 现状说明

- **L2_GATEWAY**：在 L2 机器运行，L3 通过 L2 委托调用
- **local-hr-fs**：允许访问项目根、data/hr_resumes、config/hr_jds
- 若目标机 L3 与 L2 同机，路径一致；若 L3 异地，路径需可配置（如通过 __PROJECT_ROOT__ 占位符）

### 6.3 步骤

| 步骤 | 动作 | 产出 |
|------|------|------|
| 3.1 | 确认 local-hr-fs 的 config.json 结构（command、args、allowed_dirs） | 现状文档 |
| 3.2 | 创建 MCP 包：plugin.json（runtime_tier=L2_GATEWAY）、config.json 模板 | 包结构 |
| 3.3 | config/manifest.yaml 写出到 ~/.jachin/config/mcps/com.jachin.hr.filesystem/ | 配置写出 |
| 3.4 | 路径占位符：__PROJECT_ROOT__ 在 L2 解压时替换为实际项目根或配置值 | 可移植 |
| 3.5 | jachin pack + publish | 发布 |

### 6.4 难点

| 难点 | 说明 | 应对 |
|------|------|------|
| **L2 vs L3** | L2_GATEWAY 在 L2 运行，L2 sync 下载到 inventory/mcps/；L3_LOCAL 在 L3 运行，L2 下载到 inventory/l3_mcps/，L3 拉取到 l3_mcp_cache | local-hr-fs 为 L2_GATEWAY，走 inventory/mcps/ |
| **路径差异** | 机器 B 的项目根可能与机器 A 不同 | 使用 ~/.jachin/ 下统一路径，或配置写出时写入实际路径 |

### 6.5 验证方式

| 验证项 | 方法 |
|--------|------|
| L2 同步 | L2 订阅后 inventory/mcps/ 有 local-hr-fs |
| MCP 连接 | L2 启动后 MCP 管理器连接 local-hr-fs 成功 |
| 工具调用 | L3 通过 L2 调用 read_file 等，可访问 data/hr_resumes |

---

## 七、Phase 4：L3 剥离与插件化

### 7.1 目标

从 L3 代码中移除所有 HR 硬编码，改为「有 HR 包则启用，无则跳过」。

### 7.2 步骤

| 步骤 | 动作 | 产出 |
|------|------|------|
| 4.1 | 从 mcp_registry.L3_LOCAL_MCP_TOOLS 移除 HR 工具（read_file、atom_post_job_boss 等） | 代码修改 |
| 4.2 | 从 build_l3_sidecar.py 移除 recruitment_scheduler、recruitment_task、hr_analysis_persist 的 --hidden-import | 打包脚本 |
| 4.3 | http_server.py：/api/recruitment/*、/api/scheduler/*、/api/v3/skills/{id}/execute 中 HR 逻辑改为动态检测 | 条件加载 |
| 4.4 | agent_core.py：招聘工具链校验（atom_post_job_boss → add_automated_recruitment_task）改为检测 l3_mcp_cache 中是否有 HR 包 | 条件校验 |
| 4.5 | 实现「HR 插件发现」：扫描 l3_mcp_cache，若存在 com.jachin.hr.recruitment 则注册 recruitment 相关路由与校验 | 插件发现 |
| 4.6 | 移除 skills_repo/plugin/2-track-a-atomic-mcp 到 dist 的复制（若已完全迁移到 MCP 包） | 构建清理 |

### 7.3 难点

| 难点 | 说明 | 应对 |
|------|------|------|
| **read_file 通用性** | read_file 被 BI 战报等复用，移除后需保留或拆分为「通用 read_file」+「HR read_file」 | 方案 A：read_file 保留在 L3 内置（通用）；方案 B：拆为通用 MCP 包 + HR 专用包 |
| **HTTP 流式 API** | /api/recruitment/start_task 需调用 run_recruitment_task_stream，该函数在 MCP 包内 | 动态 import：`from importlib.util import module_from_spec, spec_from_file_location` 从 l3_mcp_cache 加载 |
| **技能 ID 白名单** | http_server 中 _HR_SKILL_IDS 硬编码 | 改为从 l3_skill_cache 扫描或配置读取 |

### 7.4 验证方式

| 验证项 | 方法 |
|--------|------|
| 无 HR 包时 | L3 启动正常，无 recruitment 相关 API，Agent 不展示招聘工具 |
| 有 HR 包时 | L3 启动后 mcp_sync 拉取 HR 包，招聘工具可用，/api/recruitment/start_task 正常 |
| 打包体积 | PyInstaller 产物对比，移除 HR 后体积减小 |
| BI 战报 | 不受影响，独立运行 |

---

## 八、Phase 5：端到端验证

### 8.1 目标

在机器 A 完成打包、上传；在机器 B（或另一用户环境）拉取并运行，全流程打通。

### 8.2 步骤

| 步骤 | 动作 | 产出 |
|------|------|------|
| 5.1 | 机器 A：jachin pack 产出 HR MCP、HR Skill、local-hr-fs 包 | 3 个 zip |
| 5.2 | 机器 A：jachin publish 到 L1（指定 --nexus） | 发布成功 |
| 5.3 | L1：审核通过，加入 manifest，tenant 订阅 | 订阅生效 |
| 5.4 | 机器 B：L2 启动，CloudSyncDaemon 拉取到 inventory | inventory 有内容 |
| 5.5 | 机器 B：L3 启动，skill_sync + mcp_sync 拉取到 l3_skill_cache、l3_mcp_cache | 缓存有内容 |
| 5.6 | 机器 B：执行招聘流程（发布职位、无人值守、透析镜分析） | 功能正常 |

### 8.3 难点

| 难点 | 说明 | 应对 |
|------|------|------|
| **环境差异** | 机器 B 可能无 Chrome、无 data/hr_resumes | 文档说明前置条件；或首次使用时引导创建目录 |
| **配置写出** | config/manifest.yaml 写出后，用户需知路径 | 文档说明 ~/.jachin/config/ 结构 |
| **License** | L1 需为 tenant 分配 HR 相关 license | 确保 L1 权限配置正确 |

### 8.4 验证方式

| 验证项 | 方法 |
|--------|------|
| 清单 | L2 GET /api/v2/inventory/l3_mcps 含 HR MCP |
| 下载 | L3 mcp_sync 日志显示下载成功 |
| 加载 | mcp_registry 日志显示从 l3_mcp_cache 加载 HR 工具 |
| 执行 | 端到端跑通：发布 → 打招呼 → 收网 → 分析 |

---

## 九、依赖关系与发布顺序

```text
1. HR MCP 包 (com.jachin.hr.recruitment)     ← 先发布
2. local-hr-fs (L2_GATEWAY)                  ← 可与 1 并行
3. HR Skill (hr-analyzer4)                  ← 依赖 1，后发布
```

L1 manifest 的 required_mcps 机制可自动将依赖 MCP 加入下发清单（077 规范）。

---

## 十、风险与缓解

| 风险 | 缓解 |
|------|------|
| 调度器冲突 | 明确 bi_scheduler 与 recruitment_scheduler 的共享/隔离策略 |
| 路径不可移植 | 统一使用 ~/.jachin/ 或配置，避免绝对路径 |
| L3 插件发现复杂度 | 采用简单策略：扫描 l3_mcp_cache 下特定 id 前缀（如 com.jachin.hr） |
| 回滚困难 | 保留 Phase 4 前的 L3 版本，支持「带 HR 打包」与「无 HR 打包」双轨 |

---

## 十一、检查清单（执行前）

- [ ] Phase 0 依赖清单完成
- [ ] L1 测试环境可用，jachin publish 可连接
- [ ] L2 已配对 L1，CloudSyncDaemon 正常
- [ ] L3 已获批，skill_sync、mcp_sync 正常
- [ ] 现有 BI 战报、其他技能不受影响（隔离验证）

---

## 十二、完整步骤清单（含长连接模式）

**目标**：目标机器仅下载 L3 exe，通过订阅获得 HR 招聘技能，并通过 L3 内置 Lark 长连接接收飞书消息（无需单独运行 lark_bot.py）。

| 步骤 | 动作 | 产出/验证 | 状态 |
|------|------|----------|------|
| **1** | 现状盘点：梳理 HR 相关文件、调用链、依赖 | `docs/hr_decouple_inventory.md` | ✅ 已完成 |
| **2** | 创建 HR MCP 包目录，迁移 tools（hr_data_paths、atom_post_job_boss、atom_lark_chat 等）、recruitment_scheduler、recruitment_task、hr_analysis_persist | `skills_repo/plugin/com.jachin.hr.recruitment/` | ✅ 已完成 |
| **3** | 编写 plugin.json（tools 数组）、config/manifest.yaml | 可 jachin pack | ✅ 已完成 |
| **4** | 路径可配置化：所有 get_app_root()、PLUGIN_DATA_ROOT 改为 ~/.jachin/ 或配置 | 可移植 | ✅ 已完成（tools.config、hr_analysis_persist、recruitment_scheduler 已统一） |
| **5** | 长连接集成：HR MCP 包内 atom_lark_chat 可被 L3 im_channels 调用；或 L3 检测到 HR 包时，im_channels 的 on_message 路由到 process_lark_message（招聘类消息） | Lark 消息 → L3 长连接 → Agent/HR 处理 | ✅ 已完成（dispatcher 检测 l3_mcp_cache 有 HR 包则路由） |
| **6** | 创建 local-hr-fs MCP 包（L2_GATEWAY），含 config/manifest.yaml | 可 jachin pack | ✅ 已完成（config/local-hr-fs 含 plugin.json、config.json、config/manifest.yaml） |
| **7** | 创建 HR Skill 包（hr-analyzer4），含 config、required_mcps | 可 jachin pack | ✅ 已完成（config/manifest.yaml、hr_jds、required_mcps） |
| **8** | L3 剥离：从 mcp_registry 移除 HR 内置工具，从 build_l3_sidecar 移除 recruitment_* 等 hidden-import | L3 exe 不含 HR | ✅ 已完成（build 已改 hr_loader；mcp_registry 工具定义仍内置，执行时动态加载） |
| **9** | L3 插件化：im_channels 支持「HR 包存在时」使用 process_lark_message 或等效逻辑；HTTP /api/recruitment/* 动态加载 | 有包则启用，无包则跳过 | ✅ 已完成 |
| **10** | 机器 A：jachin pack 产出 HR MCP、HR Skill、local-hr-fs 三个 zip | 3 个 zip | ⬜ 未完成 |
| **11** | 机器 A：jachin publish 到 L1（指定 --nexus） | L1 审核通过 | ⬜ 未完成 |
| **12** | L1：为 tenant 分配 HR 相关 license，manifest 包含上述包 | 订阅可见 | ⬜ 未完成 |
| **13** | 目标机：安装 L3 exe（仅 L3，无 HR 源码） | L3 可启动 | ⬜ 未完成 |
| **14** | 目标机：配置 L2 连接（l2_gateway_config.json）、L2 已配对 L1 | L3 可获批 | ⬜ 未完成 |
| **15** | 目标机：L3 启动，skill_sync + mcp_sync 从 L2 拉取 HR 包到 l3_skill_cache、l3_mcp_cache | 缓存有 HR 包 | ⬜ 未完成 |
| **16** | 目标机：配置 ~/.jachin/config/im_channels.yaml（Lark app_id、app_secret、domain） | 长连接生效 | ⬜ 未完成 |
| **17** | 验证：飞书发招聘消息 → L3 长连接接收 → Agent 调用 HR 工具 → 回复回传飞书 | 端到端通过 | ⬜ 未完成 |

---

## 十三、参考文档

- `docs/L1_L2_L3_DEPLOYMENT_AND_SKILL_MCP_SPEC.md` — 部署与 MCP 规范
- `docs/SKILL_MCP_UPLOAD_SPEC.md` — 上传规范
- `.cursor/rules/076-skill-mcp-upload-spec.mdc` — 配置随包
- `.cursor/rules/077-skill-mcp-dependency.mdc` — Skill 依赖 MCP
- `docs/L3_RECRUITMENT_BUILD_SPEC.md` — 当前 L3 招聘打包规范
