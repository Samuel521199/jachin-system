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

## 3. 数据诚实

Observation 为 null/空 → JSON `null` 或 `field_empty`；禁止编造 priority、日期、人名。
