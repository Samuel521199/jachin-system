# HR 招聘 — 当前架构（单一事实来源）

**版本**: 2026-03  
**说明**: 主仓内招聘相关文档以此为准；插件仓历史方案仅作背景，实现以本仓库代码为准。

---

## 1. 组成

| 类型 | 位置 | 作用 |
|------|------|------|
| **Skill 文案** | `skills_repo/hr-recruitment/SKILL.md`（及 MCP 包内同名） | Agent SOP：多轮问 JD、发布、停止等 **对话层** |
| **MCP 包** | `skills_repo/plugin/com.jachin.hr.recruitment/` | 原子工具、APScheduler、`recruitment_task`、Lark、`anti_bot` 等 |
| **L3 加载** | `l3_node/hr_loader.py` | 动态加载上述 MCP 包 |
| **DAG** | `l3_node/skills/hr_recruitment_dag.py` | **执行层编排**：`HrRecruitmentPlanInitNode` → `HarvestLoopNode` →（可选）`AnalyzeResumeNode` |
| **长期编排 L2** | `l3_node/orchestration/domain_hr.py` | 通过 `domain_ref: hr_recruitment` 或 `core:domain_workflow_run` **委托**同一套 `build_hr_recruitment_dag`（不复制业务逻辑）；详见 [ORCHESTRATION_ARCHITECTURE.md](./ORCHESTRATION_ARCHITECTURE.md) |

**关系**：飞书「我要招聘」多由 Skill 驱动工具；**无人值守打招呼/收网**由调度器走 **DAG**（非直接散调 atom），以便信号与进度统一。

---

## 2. 执行路径

| 场景 | 入口 | 行为摘要 |
|------|------|----------|
| **飞书 / 手动长流程** | `build_hr_recruitment_dag(wid).run(...)` | 默认可写宏图、`~/.jachin/workspace/hr_recruitment/` 下 `task_plan.md` / `progress.md`；`workflow_signal_bridge` + `STOP_HARVEST` |
| **APScheduler 无人值守** | `recruitment_scheduler._run_hr_recruitment_dag_tick(...)` | `include_analyze=False`；context 常带 `skip_hr_plan_init_node`、`skip_hr_progress_restore`、单 tick 限制，避免覆盖宏图/误恢复计数 |
| **流式一键任务** | `recruitment_task.run_recruitment_task_stream` | 收网经 DAG tick；与调度器类似跳过宏图初始化 |

HTTP / MCP 注册：`l3_node/http_server.py`、`l3_node/skills/mcp_registry.py`、`plugin.json`。

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
