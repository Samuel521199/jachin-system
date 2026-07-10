# HR 招聘 — 当前架构（单一事实来源）

**版本**: 2026-03
**说明**: 主仓内招聘相关文档以此为准；插件仓历史方案仅作背景，实现以本仓库代码为准。

---

## 1. 组成

| 类型 | 位置 | 作用 |
|------|------|------|
| **能力总目录（通用）** | [L3_CAPABILITY_CATALOG.md](./L3_CAPABILITY_CATALOG.md) | 系统身份、软/硬路由、**域注册方式**；Agent 注入 `PROMPT_INJECT_CORE` |
| **招聘域切片** | [capability_domains/hr_recruitment.md](./capability_domains/hr_recruitment.md) | 招聘 MCP 映射、硬路径指针；Agent 注入 `PROMPT_INJECT_RECRUITMENT`（由 `capability_catalog.DOMAIN_REGISTRY` 挂载） |
| **典型场景 Q&A** | [HR_RECRUITMENT_WORKFLOWS.md](./HR_RECRUITMENT_WORKFLOWS.md) | 仅收网 / 全链路默认数字 / 换岗挂起 / 停止与继续 |
| **Skill 文案** | `skills_repo/hr-recruitment/SKILL.md`（及 MCP 包内同名） | Agent SOP：多轮问 JD、发布、停止等 **对话层** |
| **MCP 包** | `skills_repo/plugin/com.jachin.hr.recruitment/` | 原子工具、APScheduler、`recruitment_task`、Lark、`anti_bot` 等 |
| **L3 加载** | `l3_node/hr_loader.py` | 动态加载上述 MCP 包 |
| **DAG** | `l3_node/primitives/skills/hr_recruitment_dag.py` | **执行层编排**：`HrRecruitmentPlanInitNode` → `HarvestLoopNode` →（可选）`AnalyzeResumeNode` |
| **角色化编排** | `l3_node/orchestration/domain_hr.py` | 通过 `domain_ref: hr_recruitment` 或 `core:domain_workflow_run` 委托同一套 `build_hr_recruitment_dag`（不复制业务逻辑）；编排边界以 [07_memory_first_main_agent_and_voice_app_agents.md](./07_memory_first_main_agent_and_voice_app_agents.md) 为准 |

**关系**：飞书「我要招聘」多由 Skill 驱动工具；**无人值守打招呼/收网**由调度器走 **DAG**（非直接散调 atom），以便信号与进度统一。

### 1.1 必读：`l3_mcp_cache` 与仓库代码「两套插件」

`l3_node/hr_loader.py` 默认 **优先** 加载 `~/.jachin/l3_mcp_cache/.../com.jachin.hr.recruitment`（L2 订阅落盘）。若缓存是旧版本，会出现：

- 日志里 **MCP 已打印** `enable_greet_recommend=False`，但调度仍注册 **「推荐每15min」**（旧 `recruitment_scheduler`）；
- 调度日志文案仍是 **`已添加岗位任务:`** 等旧格式，与当前仓库不一致。

**开发/本仓库联调**（`scripts/start-layer3.ps1`、`scripts/run_l3.ps1` 已默认设置）：

- `JACHIN_DEV_HR_FIRST=1`：优先使用 **`skills_repo/plugin/com.jachin.hr.recruitment`**；
- `JACHIN_APP_ROOT`：指向项目根（便携/侧载时便于找到 `skills_repo`）；
- 或 `JACHIN_HR_RECRUITMENT_ROOT=<包目录>` 显式指定。

生产仅订阅包时保持默认（缓存优先）即可；更新 L2 插件或删除旧缓存目录后重启 L3。

---

## 2. 执行路径

| 场景 | 入口 | 行为摘要 |
|------|------|----------|
| **飞书 / 手动长流程** | `build_hr_recruitment_dag(wid).run(...)` | 默认可写宏图、`~/.jachin/workspace/hr_recruitment/` 下 `task_plan.md` / `progress.md`；`workflow_signal_bridge` + `STOP_HARVEST` |
| **APScheduler 无人值守** | `recruitment_scheduler._run_hr_recruitment_dag_tick(...)` | `include_analyze=False`；context 常带 `skip_hr_plan_init_node`、`skip_hr_progress_restore`、单 tick 限制，避免覆盖宏图/误恢复计数 |
| **流式一键任务** | `recruitment_task.run_recruitment_task_stream` | 收网经 DAG tick；与调度器类似跳过宏图初始化 |

### 2.1 已有在招岗位 · 轻量收网（不重新发帖）

适用于 Boss **职位已在线**，HR 只想 **抓简历** 或 **打招呼 + 收网**。Boss 单页下为**单轨交替**（同一时刻只跑推荐或收网一种，可按轮次提前切换）。对话层见 **`skills_repo/hr-recruitment/SKILL.md` 分支 B**；工具为 **`mcp:add_automated_recruitment_task`**，主要参数：

| 参数 | 含义 |
|------|------|
| `enable_greet_recommend` | `false` = 仅收网，不打招呼 |
| `resume_collect_target` | 未处理简历达到该份数后**停止收网**（默认与 `analyze_threshold` 相同） |
| `analyze_threshold` | 规则引擎触发透析镜的未处理份数阈值 |
| `auto_analyze` | `false` = 不跑 Wasm 透析，仅达 `resume_collect_target` 后停表 |

调度实现：`recruitment_scheduler.add_scheduled_job` 注册 **`rec_*_alternate`** 交替任务（或仅收网），**不再**提供并行双 Job；旧字段 `parallel_greet_and_harvest`、`harvest_delay_seconds` 已忽略或写回时剔除。

**飞书 / Lark**：HR 可发 **`python工程师 杭州 15-25k`**（无下划线）；会规范为带 **` _ `** 与 **`K`** 的选岗串。`get_jd_select` **优先**用 `jd.json` 里的 **`job_title` + `job_location` + `salary_min/max`** 拼行（与 Boss 在招列表一致，如 **`Python 工程师 _ 杭州 15-25K`**），避免仅依赖 `jd_select` 小写/无下划线与页面不一致。

HTTP / MCP 注册：`l3_node/http_server.py`、`l3_node/primitives/mcp/registry.py`、`plugin.json`。

---

## 3. 数据、信号与智能化绑定

| 项 | 说明 |
|----|------|
| **数据根** | 优先 `~/.jachin/workspace/hr_recruitment/{岗位}/pending|result`（`tools/config.py`） |
| **停止** | `DAGWorkflow.inject_signal`；atom 内 `os_context` + `try_consume_stop_harvest` / `WorkflowContext.drain_merge_into_context` |
| **全局规划三文件** | `l3_node/task_planning.py` → `~/.jachin/workspace/task_plan.md` 等 |
| **HR 专用宏图与战况** | 同模块：`~/.jachin/workspace/hr_recruitment/task_plan.md`、`progress.md`（Session 块按 `workflow_id` 分段，便于恢复计数） |

招聘与 **P0 智能化**（跨会话规划、OpenClaw 对标）的文档索引：

- [JACHIN_VS_OPENCLAW_INTELLIGENCE_ANALYSIS.md](./JACHIN_VS_OPENCLAW_INTELLIGENCE_ANALYSIS.md)
- [INTELLIGENCE_UPGRADE_OVERVIEW.md](./INTELLIGENCE_UPGRADE_OVERVIEW.md)

---

## 4. 打包与 Chrome

- RPA 依赖 Chrome **CDP**（默认 `9222`），脚本见仓库 `scripts/launch_chrome_debug.ps1`。
- 示例配置：`dist_jachin_desktop/config/l3_recruitment.yaml.example`（注释指向本文）。

---

## 5. 相关排查

- Lark「我要招聘」无回复：[LARK_NO_REPLY_TROUBLESHOOTING.md](./LARK_NO_REPLY_TROUBLESHOOTING.md)
- 部署总览：[README_DEPLOY.md](./README_DEPLOY.md)
