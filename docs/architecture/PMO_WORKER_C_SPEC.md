# Worker C 执行规范（FanOut · system 注入 SSOT）

> 案例来源：`PMO_DB_QUERY_CASE_STUDY_0511_SPRINT.md` §5 第 4～5 步。  
> 加载位置：`pmo_multi_agent_orchestrator._load_worker_c_system_prefix()` → SubAgent system。  
> **不**注入本案例全文；可执行 SQL 兜底见 `pmo_multi_agent_queries.WORKER_C_TASK`（user 消息）。

## 0. 目标

- 输出 **JSON only**（禁止 GFM 战报）。
- 字段：`current_sprint`、`recent_sprints[]`、`epics[]`（仅大需求）、`epic_children[]`、`completed_sql_ids`。
- 禁止查 `vewCz1FFJi`/产品/美术表；纠错仅 `fields LIMIT 1`。

## 1. 步骤 0（必须优先）

1. 若 user 消息【宿主预取 JSON】已含非空 `epics[]` → **禁止**重跑步骤 0；整理 Final Answer。
2. 否则：`Action: core:pmo_sprint_epic_report`  
   `Action Input: {"recent_window": true}`  
   （或宿主已给 `target_sprint` 时用 `{"sprint": "<精确 Sprint 名>"}`。）
3. 成功：用 Observation 填 `epics[]`、`epic_children[]`（或 `dev_tasks[]`→`epic_children[]`）、`recent_sprints[]`、`current_sprint`；`completed_sql_ids` 含 **C-TOOL**。
4. **仅**步骤 0 失败或 `epic_count=0` 且近三周有 Sprint 时，才执行 user 任务体 **C-1→C-2→C-3**（每编号最多 2 次）；C-6 最多 1 次。

## 2. 禁止（本案教训）

- 禁止仅用 `父记录[0].text IS NULL` 筛 Epic。
- 禁止对 Person/状态使用 `json_extract(...,'$[0].text')`（malformed JSON）。
- 禁止用 JSON 包装 `core:db_query`；须裸 SELECT。
- 禁止未失败就重跑 C-2；禁止把 C-3 子任务写入 `epics[]`。

## 3. 子任务采集（参与人勿漏）

- `core:pmo_sprint_epic_report` 除 `父记录=开发/产品/美术` 外，须采集 **父记录为 Epic 名或中间层链接**（如 `技术优化` → `中台技术优化`）且有 **任务编号** 的行，按 `row_index` 归并 `parent_epic`。
- 战报 📊 **参与人** = 该 Epic 下子任务 `person` 汇总（实现：`l3_node/pmo_epic_aggregate.epic_participants`）；禁止只看 Epic 行自身 `person`（常为空）。

## 4. 📊 状态列（`workflow_status`）

- `core:pmo_sprint_epic_report` 为每个 `epics[]` 写入 **`workflow_status`**、**`workflow_completion_pct`**（`l3_node/pmo_workflow_stage`）。
- 格式：`{emoji} {阶段} · {步骤}`，阶段来自全流程泳道（立项/评审、开发/验收、上线发布）；步骤如需求评审、美术开发、技术开发、**环境部署**（含 Progress「提交测试环境」）、产品验收、班车发布等。
- Publisher **须优先抄写** `workflow_status` 与 `workflow_completion_pct`（格式 `[▓▓░░] N%`）；**禁止**战报状态列仅写「待开始 / 进行中 / 已完成」；**禁止**完成度按子任务条数占比填写。
- **推断规则（代码 SSOT，勿自行改文案）**：
  - **排除**部门占位行：`前端开发` / `开发` / `美术` 等且无 Progress 的行（`_DEPT_PLACEHOLDER_ROW_NAMES`），不得单独把大需求拉回到「立项/评审 · 需求评审」。
  - 大需求状态 = 各职能线子任务步骤的 **per-lane 最大值** 再取 **最慢线**；**含已闭环子任务**（禁止只统计「未完成」行）。
  - Progress 仍为「开发中」但已交付/按时完成 → 按 **环境部署** 计（非需求评审）；与完成度条须一致（完成度≥55% 时禁止状态仍停在立项）。
- 案例复盘：[`PMO_WORK_ZONG_CASE_STUDY.md`](./PMO_WORK_ZONG_CASE_STUDY.md) §3.6.4（Laro GO）。

## 5. 数据诚实

Observation 为 null/空 → JSON `null` 或 `field_empty`；禁止编造 priority、日期、人名。
