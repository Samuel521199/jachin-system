# HR 招聘解耦 — 现状盘点

**版本**: 1.0  
**日期**: 2026-03  
**关联**: docs/HR_RECRUITMENT_DECOUPLE_STRATEGY.md

---

## 一、HR 相关文件清单

### 1.1 L3 核心（已解耦为 hr_loader 动态加载）

| 路径 | 说明 |
|------|------|
| `l3_node/hr_loader.py` | HR 模块动态加载器，从 l3_mcp_cache 或 skills_repo 加载 |
| `l3_node/paths.py` | get_app_root()，项目根解析（exe 下指向 bin 父级） |
| `l3_node/skills/loader.py` | 通过 hr_loader 获取 _resolve_safe_dir、persist_* |
| `l3_node/skills/mcp_registry.py` | HR 工具定义在 L3_LOCAL_MCP_TOOLS，add/stop 通过 hr_loader 执行 |
| `l3_node/skills/bi/scheduler.py` | 通过 hr_loader.get_recruitment_scheduler() 共享 APScheduler |
| `l3_node/http_server.py` | /api/recruitment/*、/api/scheduler/* 通过 hr_loader 动态加载 |
| `l3_node/im_channels/dispatcher.py` | 检测 l3_mcp_cache 有 HR 包时，路由到 process_lark_message |
| `l3_node/agent_core.py` | 招聘工具链校验 |
| `core/api/skills_fallback.py` | L2 Fallback 执行 HR 技能时，通过 hr_loader 持久化 |

### 1.2 Skill 包（hr-recruitment，纯 SKILL.md）

| 路径 | 说明 |
|------|------|
| `skills_repo/hr-recruitment/SKILL.md` | 定义全套流程：收集 JD → 确认发布 → 无人值守 → 调用 hr_analyze_resume 分析 → 排行榜 |

Agent 通过 `_load_hr_recruitment_skill_content()` 加载，优先 `skills_repo/hr-recruitment/`，其次 `l3_skill_cache/hr-recruitment/`。

### 1.3 HR MCP 包（com.jachin.hr.recruitment，纯工具）

| 路径 | 说明 |
|------|------|
| `skills_repo/plugin/com.jachin.hr.recruitment/plugin.json` | MCP 包元数据，tools 数组（含 hr_analyze_resume） |
| `skills_repo/plugin/com.jachin.hr.recruitment/recruitment_scheduler.py` | APScheduler 无人值守调度 |
| `skills_repo/plugin/com.jachin.hr.recruitment/recruitment_task.py` | 一键招聘流式任务 |
| `skills_repo/plugin/com.jachin.hr.recruitment/hr_analysis_persist.py` | 透析镜结果持久化 |
| `skills_repo/plugin/com.jachin.hr.recruitment/tools/config.py` | 路径配置，~/.jachin、JACHIN_HOME |
| `skills_repo/plugin/com.jachin.hr.recruitment/tools/hr_data_paths.py` | PLUGIN_DATA_ROOT、职位目录 |
| `skills_repo/plugin/com.jachin.hr.recruitment/tools/atom_post_job_boss.py` | 发布职位 |
| `skills_repo/plugin/com.jachin.hr.recruitment/tools/atom_greet_recommend_boss.py` | 打招呼 |
| `skills_repo/plugin/com.jachin.hr.recruitment/tools/add_automated_recruitment_task.py` | 添加调度任务 |
| `skills_repo/plugin/com.jachin.hr.recruitment/tools/stop_automated_recruitment.py` | 停止调度 |
| `skills_repo/plugin/com.jachin.hr.recruitment/tools/atom_lark_chat.py` | Lark 消息处理 |
| `skills_repo/plugin/com.jachin.hr.recruitment/tools/atom_inbox_harvester.py` | 收网抓取 |
| `skills_repo/plugin/com.jachin.hr.recruitment/tools/hr_analyze_resume.py` | 简历分析 MCP 工具（包装 Wasm） |
| `skills_repo/plugin/com.jachin.hr.recruitment/tools/local_archiver.py` | 本地归档 |
| `skills_repo/plugin/com.jachin.hr.recruitment/config/manifest.yaml` | 配置写出清单 |
| `skills_repo/plugin/com.jachin.hr.recruitment/config/mcps/com.jachin.hr.recruitment/` | 写出模板 |

### 1.4 HR Skill（Wasm）

| 路径 | 说明 |
|------|------|
| `skills_repo/hr-analyzer4/` | Rust 源码，编译 main.wasm |
| `skills_repo/hr-analyzer4/plugin.json` | Skill 元数据 |
| `l3_node/skills/wasm_plugins/hr-analyzer4/` | 开发侧载副本 |

### 1.5 L2_GATEWAY MCP（local-hr-fs）

| 路径 | 说明 |
|------|------|
| `config/local-hr-fs/plugin.json` | MCP 包元数据 |
| `config/local-hr-fs/config.json` | @modelcontextprotocol/server-filesystem 命令与参数 |

### 1.6 配置与数据

| 路径 | 说明 |
|------|------|
| `config/hr_jds/*.md` | JD 模板（开发） |
| `data/hr_resumes/` | 简历存储（开发） |
| `data/hr_analysis/` | 分析输出（开发） |
| `~/.jachin/workspace/hr_recruitment/` | 订阅后数据根（目标机） |
| `~/.jachin/workspace/hr_resumes/` | 订阅后简历根 |
| `~/.jachin/config/hr_jds/` | 订阅后 JD 配置 |

---

## 二、调用链

### 2.1 HTTP API → HR 执行

```
POST /api/recruitment/start_task
  → http_server.run_recruitment_task_stream()
  → hr_loader.get_recruitment_task()
  → recruitment_task.run_recruitment_task_stream()

GET /api/scheduler/jobs
  → http_server.list_scheduled_jobs()
  → hr_loader.get_recruitment_scheduler()
  → recruitment_scheduler.list_scheduled_jobs()

POST /api/v3/skills/{id}/execute (jpp:com.jachin.hr.analyzer4)
  → loader._invoke_wasm_plugin()
  → hr_loader.get_hr_analysis_persist()
  → persist_hr_analysis_result / persist_hr_analysis_batch_item
```

### 2.2 MCP 工具 → HR 执行

```
mcp:add_automated_recruitment_task
  → mcp_registry._invoke_add_automated_recruitment_task_local()
  → hr_loader.get_recruitment_scheduler()
  → recruitment_scheduler.add_scheduled_job()

mcp:stop_automated_recruitment
  → mcp_registry._invoke_stop_automated_recruitment_local()
  → hr_loader.get_recruitment_scheduler()
  → recruitment_scheduler.remove_scheduled_job() / set_recruitment_stopped()

mcp:atom_post_job_boss
  → mcp_registry 从 HR 包 tools.atom_post_job_boss 加载执行
```

### 2.3 Lark 长连接 → HR 处理

```
Lark WebSocket 消息
  → im_channels.dispatcher._do_agent_work()
  → _is_hr_package_available() && _is_recruitment_message()
  → _process_via_hr_package()
  → sys.path.insert(l3_mcp_cache/com.jachin.hr.recruitment)
  → tools.atom_lark_chat.process_lark_message()
```

### 2.4 BI 战报调度器共享

```
register_bi_daily_report_job()
  → hr_loader.get_recruitment_scheduler()
  → recruitment_scheduler.scheduler (APScheduler 单例)
  → scheduler.add_job(_run_bi_daily_report_job, ...)
```

---

## 三、依赖关系

### 3.1 HR 包内部依赖

```
recruitment_scheduler
  ├── recruitment_task (add_job 时延迟导入)
  ├── tools.config.get_data_root
  ├── tools.atom_inbox_harvester
  └── l3_node.paths.get_app_root（兜底）

recruitment_task
  ├── recruitment_scheduler.PLUGIN_DATA_ROOT
  ├── l3_node.skills.run_tool
  ├── l3_node.channels.lark.sync_bitable_from_md
  └── hr_analysis_persist._resolve_safe_dir

hr_analysis_persist
  └── l3_node.paths.get_app_root
```

### 3.2 L3 对 HR 的入口

| 入口 | 触发条件 |
|------|----------|
| HTTP /api/recruitment/* | 任意 |
| HTTP /api/scheduler/* | 任意 |
| HTTP /api/v3/skills/jpp:com.jachin.hr.analyzer4/execute | 技能 ID 白名单 |
| MCP invoke mcp:add_automated_recruitment_task 等 | 工具 ID 白名单 |
| Lark on_message | 招聘类关键词 + HR 包存在 |
| BI scheduler | schedule.enabled=true + hr_loader 可用 |

---

## 四、路径解析现状

| 用途 | 当前实现 | 目标（可移植） |
|------|----------|----------------|
| 招聘数据根 | tools.config.get_data_root() → ~/.jachin/workspace/hr_recruitment | ✅ 已支持 |
| 简历根 | tools.config.get_resume_root() → ~/.jachin/workspace/hr_resumes | ✅ 已支持 |
| JD 配置 | tools.config.get_jd_config_root() → ~/.jachin/config/hr_jds | ✅ 已支持 |
| PLUGIN_DATA_ROOT | recruitment_scheduler._get_hr_data_root() 优先 get_data_root() | ✅ 已支持 |
| 调度状态 | _get_scheduler_state_dir() → ~/.jachin/workspace/hr_recruitment/hr_analysis | ✅ 已支持 |
| 分析输出 | hr_analysis_persist 用 _get_default_output_root() → ~/.jachin/workspace/hr_analysis | ✅ 已支持 |

---

## 五、L1/L2/L3 发布与拉取流程

| 环节 | 路径 | 说明 |
|------|------|------|
| L1 manifest | plugins_registry + user_licenses | item_type=MCP/SKILL, package_url |
| L2 同步 | inventory/l3_mcps/{id}/, inventory/skills/{id}/ | CloudSyncDaemon 拉取 zip |
| L3 拉取 | l3_mcp_cache/{id}/, l3_skill_cache/{id}/ | mcp_sync, skill_sync |
| 配置写出 | ~/.jachin/config/ | config_writeout 按 manifest.yaml |
