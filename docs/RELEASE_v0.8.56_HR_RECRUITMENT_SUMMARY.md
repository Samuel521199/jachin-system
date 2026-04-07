# v0.8.56 代码更新总结（hr 招聘插件升级完成）

**范围**：Git 提交 `8ca9968` → `145b1b6`（标签 `v0.8.56`，说明：hr 招聘插件升级完成）。

**说明**：本段 rebase 时 `l3_node/primitives/mcp/mcp_tools/bi/tool_web_scraper.py`、`l3_node/primitives/mcp/mcp_tools/bi/spa_collector.py` 保留了上游 `8ca9968`（BI 全流程）版本，未作为本标签内的 BI 功能增量列出；v0.8.56 主体为 **HR 招聘插件 + L3 编排 + 能力目录 / 执行韧性 / 模糊意图澄清 + 文档与配置**。

---

## 1. Core / 宿主与 LLM（约 10 项）

1. [x] `core/plugin_llm_identity.py` — 新增：插件侧 LLM 身份/标识相关逻辑
2. [x] `core/llm_provider.py` — 扩展/调整 LLM 提供方与调用路径（体量较大）
3. [x] `core/mcp_client.py` — MCP 客户端行为更新
4. [x] `core/wasm_runner.py` — Wasm 宿主执行与错误处理增强
5. [x] `core/agent_loop.py` — Agent 循环与工具路径衔接
6. [x] `core/errors.py` — 错误类型/分类扩展（与韧性叙事一致）
7. [x] `core/inventory_scanner.py` — 清单扫描能力增强
8. [x] `core/config/__init__.py` — 配置入口小改
9. [x] `core/compaction_hook.py` — 压缩/钩子侧补充
10. [x] `core/requirements.txt`、`core/requirements-layer2.txt` — 依赖更新

---

## 2. L3 Agent / 编排 / 会话（约 9 项）

1. [x] `l3_node/agent_core.py` — HR 招聘对话与任务编排主逻辑大幅扩展（选岗、改参、A/B 分支、预检等）
2. [x] `l3_node/lark_workflow_command_interceptor.py` — 飞书工作流/指令拦截与 HR 命令解析增强
3. [x] `l3_node/task_planning.py` — 任务规划与 HR 流程衔接
4. [x] `l3_node/llm_client.py` — L3 LLM 调用与上下文
5. [x] `l3_node/local_memory.py` — 本地记忆与 HR 会话状态
6. [x] `l3_node/bootstrap.py`、`l3_node/__main__.py` — 启动与入口参数
7. [x] `l3_node/http_server.py`、`l3_node/ws_server.py` — HTTP/WS 侧小扩展
8. [x] `l3_node/workflow_signal_bridge.py` — 工作流信号桥接
9. [x] `l3_node/hr_loader.py` — HR 配置/数据加载与 L3 对接

---

## 3. 能力目录 / 执行韧性 / 模糊意图（5 项）

1. [x] `l3_node/capability_catalog.py` — L3 能力目录注册与查询
2. [x] `l3_node/execution_resilience.py` — 执行韧性辅助（与契约文档一致）
3. [x] `l3_node/intent_clarification.py` — 模糊意图澄清框架
4. [x] `l3_node/intent_clarification_plugins/hr_recruitment_lark.py` — 招聘域飞书澄清插件
5. [x] `.cursor/rules/079-l3-capability-catalog.mdc`、`.cursor/rules/080-jachin-execution-resilience.mdc`、`.cursor/rules/085-l3-fuzzy-intent-clarification.mdc` — 对应 Cursor 规则（与 `docs/L3_*.md`、`JACHIN_EXECUTION_RESILIENCE_CONTRACT` 配套）

---

## 4. HR 飞书 / IM / 审计（约 12 项）

1. [x] `l3_node/channels/lark/hr_recruitment_notify.py` — 招聘简报、通知与状态文案（含挂起等）
2. [x] `l3_node/channels/lark/hr_lark_llm_polish.py` — 飞书侧 LLM 润色/格式化
3. [x] `l3_node/channels/lark/client.py`、`l3_node/channels/lark/bitable.py`、`l3_node/channels/lark/__init__.py` — Lark API/多维表格与导出能力补强
4. [x] `l3_node/hr_tool_reply_zh.py` — 工具成功/失败中文回复模板
5. [x] `l3_node/hr_lark_command_lexicon.py` — 飞书短指令词表与归一化
6. [x] `l3_node/hr_prompt_context.py`、`l3_node/hr_reference_time.py` — Prompt 上下文与参考时间
7. [x] `l3_node/hr_audit_log.py` — HR 操作审计日志
8. [x] `l3_node/hr_workspace_full_reset.py` — 工作区/招聘数据一键重置脚本级能力（L3 侧）
9. [x] `l3_node/im_channels/dispatcher.py`、`l3_node/im_channels/__init__.py` — 多渠道分发与 HR 路由
10. [x] `l3_node/lark_session.py` — 会话小改
11. [x] `config/im_channels.yaml.example` — IM 通道示例配置补充

---

## 5. Skill 注册 / DAG / Wasm（4 项）

1. [x] `l3_node/primitives/mcp/registry.py` — MCP 工具注册、HR 指针与 `jd_select` 冲突策略等
2. [x] `l3_node/primitives/tools/loader.py` — Skill 加载与依赖解析增强
3. [x] `l3_node/primitives/skills/hr_recruitment_dag.py` — 招聘 DAG 与 L3 对齐
4. [x] `l3_node/primitives/tools/wasm_bundled/hr-analyzer4/main.wasm`、`plugin.json` — hr-analyzer4 Wasm 与清单更新（与 `skills_repo/hr-analyzer4` 同步）

---

## 6. MCP 工具层（L3 侧）（2 项）

1. [x] `l3_node/primitives/mcp/mcp_tools/human_ask_tool.py` — 人工确认/追问工具行为扩展（与 `config/mcps/human_ask` 示例一致）
2. [x] `l3_client/local_mcps/boss_harvester/server.py` — 本地 Boss 采集 MCP 小改

---

## 7. skills_repo — HR 招聘插件 `com.jachin.hr.recruitment`（约 18 项）

1. [x] `recruitment_scheduler.py` — 调度器大改：挂起/列表/按目录恢复、动态间隔、确认提示等
2. [x] `recruitment_task.py` — 任务模型与调度衔接
3. [x] `tools/add_automated_recruitment_task.py` — 自动化任务添加与参数
4. [x] `tools/hr_scheduler_confirm_prompt.py` — 调度确认 Prompt 生成
5. [x] `tools/hr_dynamic_intervals.py` — 动态轮询/间隔策略
6. [x] `tools/list_hr_scheduler_suspended_jobs.py`、`tools/resume_hr_job_scheduler.py` — 挂起任务列举与恢复
7. [x] `tools/jd_full_llm.py` — JD 全文 LLM 处理
8. [x] `tools/boss_utils.py`、`tools/hr_data_paths.py` — Boss 与 HR 数据路径/工具函数大幅增强
9. [x] `tools/atom_inbox_harvester.py`、`tools/atom_greet_recommend_boss.py`、`tools/atom_lark_chat.py`、`tools/atom_post_job_boss.py`、`tools/hr_analyze_resume.py` — 原子工具链调整与补强
10. [x] `tools/boss_harvest_orchestrator.py`、`tools/brain_filter.py` — 编排与过滤
11. [x] `lark_bot.py`、`server.py`、`requirements.txt` — 从 `2-track-a-atomic-mcp` 迁入并归一到本 Skill 包
12. [x] `SKILL.md`、`skills_repo/hr-recruitment/SKILL.md`、`README.md` — 文档与对外说明
13. [x] **移除** `skills_repo/plugin/2-track-a-atomic-mcp/tools/*` — 旧路径删除，能力合并至 `com.jachin.hr.recruitment`
14. [x] `skills_repo/plugin/3-track-c-swarm-wasm/src/main.py`、`install.py`、`src/llm_client.py`、各类 `scripts/*.py` — 路径与集成文档指向新包名

*路径前缀：`skills_repo/plugin/com.jachin.hr.recruitment/`*（除单独注明外）

---

## 8. skills_repo/hr-analyzer4（Rust / Wasm）（2 项）

1. [x] `skills_repo/hr-analyzer4/src/lib.rs` — 分析逻辑与导出调整
2. [x] `main.wasm`、`plugin.json` — 构建产物与插件元数据版本对齐

---

## 9. BI Skill（随仓库一并触达）（2 项）

1. [x] `l3_node/primitives/skills/bi/bi_daily_report/main_skill.py` — 与配置/文档引用的小幅同步
2. [x] `config/skills/com.jachin.bi.daily_report/bi_daily_report.yaml.example`、`docs/bi_daily_report/*.md` — 示例与文档一字级更新

---

## 10. 配置 / 环境 / MCP 配置示例（6 项）

1. [x] `.env.example`、`dist_jachin_desktop/.env.example`、`skills_repo/plugin/.env.example` — 环境变量示例扩展
2. [x] `config/local-hr-fs/.../config.json`、`config/mcps/local-hr-fs/*` — 本地 HR 文件系统 MCP 说明与配置
3. [x] `config/mcps/human_ask/config.yaml.example` — human_ask 配置示例

---

## 11. 脚本与构建（约 10 项）

1. [x] `scripts/reset_hr_recruitment_all.py` — 招聘数据/工作区重置
2. [x] `scripts/test_hr_analyze_jd_pipeline.py`、`scripts/test_greet_mcp_from_hr_flow.py` — JD 分析与 MCP 联调测试
3. [x] `scripts/test_boss_harvester_l3_local.py`、`scripts/test_*`、`scripts/verify_jd_to_model_full_chain.py` — 与新版插件路径对齐
4. [x] `scripts/build_l3_sidecar.py`、`scripts/build_webhook.py`、`scripts/run_bi_lark_long_connection.py`
5. [x] `scripts/run_l3.ps1`、`scripts/start-layer3.ps1` — 本地启动脚本小改

---

## 12. 文档（约 12 项）

1. [x] `docs/HR_LARK_COMMANDS.md`、`docs/HR_RECRUITMENT_WORKFLOWS.md` — 飞书指令与工作流（新建/大补）
2. [x] `docs/HR_RECRUITMENT.md`、`docs/HR_ANALYZE_PIPELINE_TEST.md`、`docs/capability_domains/hr_recruitment.md`
3. [x] `docs/L3_CAPABILITY_CATALOG.md`、`docs/L3_FUZZY_INTENT_CLARIFICATION.md`、`docs/JACHIN_EXECUTION_RESILIENCE_CONTRACT.md`
4. [x] `docs/SKILL_MD_SPEC.md`、`docs/SKILL_MCP_FLOW_AND_RECENT_CHANGES.md`、`docs/JACHIN_VS_OPENCLAW_INTELLIGENCE_ANALYSIS.md`
5. [x] `skills_repo/plugin/docs/INTEGRATION_CORE.md`、`HR_PLUGIN_NEW_SCHEME.md`、`LARK_*` 等 — 插件集成与飞书对话说明更新
6. [x] `l3_node/channels/ARCHITECTURE_AND_RULES.md` — 渠道架构说明小改

---

## 13. Cursor / 结构规则（2 项）

1. [x] `.cursor/rules/000-structure.mdc`、`.cursor/rules/065-v2-layer3-standalone.mdc` — 结构/L3 独立规则微调

---

## 延伸阅读

- 执行韧性契约：`docs/JACHIN_EXECUTION_RESILIENCE_CONTRACT.md`
- L3 能力目录：`docs/L3_CAPABILITY_CATALOG.md`
- 模糊意图澄清：`docs/L3_FUZZY_INTENT_CLARIFICATION.md`
- 飞书 HR 指令：`docs/HR_LARK_COMMANDS.md`
- 招聘工作流：`docs/HR_RECRUITMENT_WORKFLOWS.md`
