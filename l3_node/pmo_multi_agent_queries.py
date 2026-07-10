"""
PMO 多 Agent 方案 B：Worker B / Worker C 任务体与 SQL 模板 SSOT。

业务字段定义见 skills_repo/pmo-copilot/SKILL.md §1.2.2；
本模块供 pmo_multi_agent_orchestrator 引用，保证 FanOut 任务与 SQL 可复制、可测。
"""
from __future__ import annotations

from typing import Any

_HONESTY_BLOCK = (
    "**数据诚实（强制）**：Verification evidence 中为 null / 空 / 0 行的字段，User-facing result 中须原样填 null "
    '或 "field_empty": true，**禁止**编造 priority / 日期 / 人名 / 状态。'
    "若某视图无对应列（columns_json 中不存在），填 null 并附 "
    '"column_missing_in_view": true。\n'
)

_PMO_DEV_TABLE_ID = "tblfK9gk6vTQpJtB"
_PMO_VIEW_REQUIREMENTS = "vewpI8lyYw"
_PMO_VIEW_PERSONNEL = "vewCz1FFJi"

_WORKER_B_TABLE_BLOCK = (
    "**👥 Worker B · 单表主读 + 辅表对照（禁止多表交叉 SQL）**：\n"
    f"- **主表（强制）**：飞书 `table={_PMO_DEV_TABLE_ID}` · `view={_PMO_VIEW_PERSONNEL}` → "
    f"镜像 `source_view='{_PMO_VIEW_PERSONNEL}'`；`personnel_tasks[]` **仅**来自 **B-4**。\n"
    f"- **辅表（强制 1 次 · B-SUP）**：同 table · `view={_PMO_VIEW_REQUIREMENTS}` → "
    f"`source_view='{_PMO_VIEW_REQUIREMENTS}'`；用 B-S1 的 `recent_sprints` 拉需求名/状态/进度，"
    "在 User-facing result **文字对照** B-4 人员任务，**禁止** UNION/JOIN/子查询拼多表。\n"
    "- **禁止**查产品表（vew8TxMcSh/vewL9Mofgd）、美术表（vewjSEz5Xr）、或其它 view；"
    "跨表一致性由 **Auditor** 读 B/C 两段 JSON 完成。\n"
)

_WORKER_C_TABLE_BLOCK = (
    "**📊 Worker C · 需求表单表（禁止读人员/产品表）**：\n"
    f"- **唯一表**：飞书 `table={_PMO_DEV_TABLE_ID}` · `view={_PMO_VIEW_REQUIREMENTS}` → "
    f"镜像 `source_view='{_PMO_VIEW_REQUIREMENTS}'`。\n"
    f"- **禁止**查 `{_PMO_VIEW_PERSONNEL}`、产品表、美术表；"
    "与人员矩阵的对照留给 **Auditor**（Worker B 的 personnel_tasks[]）。\n"
)

# vewCz1FFJi：Person 常为 plain string（Buck/Seth），非数组；单独 json_each 会 malformed JSON
_PERSON_PLAIN_NAME_SQL = (
    "trim(json_extract(fields, '$.\"Person in charge/Participant\"'))"
)
_PERSON_PLAIN_WHERE = (
    f"typeof(json_extract(fields, '$.\"Person in charge/Participant\"')) = 'text'\n"
    f"  AND {_PERSON_PLAIN_NAME_SQL} IS NOT NULL\n"
    f"  AND {_PERSON_PLAIN_NAME_SQL} != ''\n"
    "  AND json_extract(fields, '$.\"Person in charge/Participant\"') NOT GLOB '[*'"
)
_PERSON_ARRAY_LIKE = (
    "fields LIKE '%\"Person in charge/Participant\": [%'"
)

_SPRINT_DATE_FROM_FIELDS_EXPR = (
    "date(replace(substr(json_extract(fields, '$.Sprint'), 1, 10), '/', '-'))"
)

_WORKER_B_SPRINT_BLOCK = (
    "**Sprint 时间窗（Worker B · 人员 SSOT · 禁止写死某一周期）**：\n"
    "- Sprint 格式：`YYYY/MM/DD-Sprint`。\n"
    f"- **B-S1**（必须先于 B-4）：在 `vewCz1FFJi` 取 sprint_date = {_SPRINT_DATE_FROM_FIELDS_EXPR}；\n"
    "  `>= date('now','-21 days')` 降序 **最多 3 个** → `recent_sprints[]`；**禁止** ORDER BY latest_row。\n"
    "- **B-4**：`Sprint IN (recent_sprints)`；有效任务行须 **有任务编号**（排除无负责人的分组占位行）。\n"
    "- User-facing result 须含：`recent_sprints[]`、`personnel_tasks[]`（来自 B-4）。\n"
)

_WORKER_B_SELF_HEAL_BLOCK = (
    "**自我修复（Worker B · 同编号 SQL 最多重试 2 次）**：\n"
    "| 现象 | 动作 |\n"
    "| B-S1 为 0 行 | 逐字复制 B-S1（replace 斜杠）；禁止 latest_row；仍 0 → sprint_window_empty |\n"
    "| B-4 malformed JSON | 禁止单独 json_each 扫全表；逐字复制 B-4 UNION（B-4a typeof+NOT GLOB + B-4b） |\n"
    "| B-4 为 0 但 B-S1 有 Sprint | 确认 Sprint IN 三个名；须含 任务编号 IS NOT NULL |\n"
    "| B-4 行数过多 | 加 任务编号 过滤；禁止去掉 Sprint IN |\n"
    "| B-SUP 报 C-2 / 任务标题·任务ID | 禁止自编字段；逐字复制 B-SUP（非 Worker C C-2） |\n"
    "| hints / 字段名错误 | 只改当前编号；主表禁止产品字段名 |\n"
    "Reasoning trace 开头写「已完成: B-S1, B-4, B-SUP」；未完成 B-S1 + B-4 + B-SUP 禁止 User-facing result。\n"
)

# 镜像 vewpI8lyYw：父记录常为 plain string；空链接可能为 JSON 字符串 text_arr:[]
from l3_node.pmo_parent_record import sql_parent_epic_null_clause

_PARENT_EPIC_NULL_SQL = sql_parent_epic_null_clause("fields")
_PARENT_TEXT_EXPR = (
    "COALESCE(NULLIF(trim(json_extract(fields, '$.\"父记录\"')), ''),\n"
    "         json_extract(fields, '$.\"父记录\"[0].text'))"
)
_DEPT_PLACEHOLDER_IN = (
    "'开发','美术','产品','测试','平台前端','平台后端','游戏','中台','后台','游戏客户端'"
)

_EPIC_HIERARCHY_BLOCK = (
    "**📊 周汇报「大需求」层级（SKILL §1.2.3 · 禁止硬编码需求名）**：\n"
    "- 飞书 UI 带序号 1.2.3. 的最外层 = 大需求；**序号不入库**，用 Requirement + 层级识别。\n"
    f"- **大需求（C-2）**：`vewpI8lyYw` + Sprint IN recent_sprints + {_PARENT_EPIC_NULL_SQL}\n"
    "  + `Requirement` 非空 + **有** `任务编号` + 排除部门占位 Requirement。\n"
    f"- **子任务（C-3）**：Sprint IN recent_sprints；{_PARENT_TEXT_EXPR} AS parent_epic 非空；"
    "排除部门占位；parent 可为 Epic 名或「开发/产品/平台前端」等。\n"
    "- **parent_epic=开发**：用 C-2 与 C-6 的 row_index，将任务归到**上一个**大需求 epic_name（同 Sprint）。\n"
    "- `epics[]` **仅** C-2 结果（每 Sprint 约十余条 Epic）；**禁止**把 C-3/自编 Sprint IN 全量写入 epics[]。\n"
    "- `epic_children[]` 来自 C-3；须带 parent_epic；无法关联时标 parent_epic_unresolved。\n"
    "- 📊 战报仅 current_sprint；近三周数据供 epic_children 汇总与审计。\n"
)

_WORKER_C_SELF_HEAL_BLOCK = (
    "**自我修复（强制 · 同编号 SQL 最多重试 2 次）**：\n"
    "| 现象 | 动作 |\n"
    "| C-1 为 0 行 | 禁止 latest_row；原样再跑 C-1；仍 0 → sprint_window_empty |\n"
    "| C-2 malformed JSON | 禁止 Person/状态 的 [0].text；逐字复制 C-2（plain string 提取） |\n"
    "| C-2 为 0 但 C-1 有 Sprint | 逐字复制 C-2（父记录双形态+任务编号）；禁止仅用 [0].text IS NULL |\n"
    "| C-2 行数>25 或含开发/产品占位 | 非大需求；逐字复制 C-2；子任务用 C-3，勿标 completed C-2 |\n"
    "| Sprint IN 全表 SELECT | 不算 C-2；须父记录双形态+任务编号 WHERE |\n"
    "| C-3 为 0 但 C-2 有 Epic | 逐字复制 C-3；仍 0 → 执行 C-6 一次，按 row_index 归并 parent_epic |\n"
    "| hints / malformed JSON | 只改当前编号 SQL |\n"
    "Reasoning trace 开头写「已完成: C-x」；未完成 C-1 + C-2 + C-3 禁止 User-facing result（C-6 仅兜底）。\n"
)

_SPRINT_TIME_WINDOW_BLOCK = (
    "**Sprint 时间窗（Worker C · 禁止用 row_index 选 Sprint）**：\n"
    "- Sprint 格式：`YYYY/MM/DD-Sprint`（如 `2026/06/01-Sprint`）。\n"
    f"- **C-1**：`sprint_date` = {_SPRINT_DATE_FROM_FIELDS_EXPR}（**禁止**对 `substr` 直接 `date()`，斜杠会导致 NULL）。\n"
    "  按 sprint_date **降序**，且 `>= date('now','-21 days')`，取 **最多 3 个** → `recent_sprints[]`。\n"
    "  **禁止** `ORDER BY latest_row DESC`（会把 2025 等历史 Sprint 误当成「最近」）。\n"
    "  C-1 若 0 行且 Verification evidence hints 提到 sprint_date：同编号 **重试 1 次**（复制任务体 C-1 SQL），禁止改 latest_row。\n"
    "- **current_sprint**：C-1 结果中 **sprint_date 最大** 的一行（本周战报 Sprint）。\n"
    "- **C-2～C-3**：`Sprint IN (recent_sprints 三个名)`，均在 vewpI8lyYw 单表内完成。\n"
    "- User-facing result 须含：`current_sprint`、`recent_sprints[]`、`epics[]`、`epic_children[]`。\n"
)

# 产品视图 vew8TxMcSh / vewL9Mofgd：需求状态/开发状态为 plain string（非对象数组）
_PRODUCT_DEMAND_STATUS_EXPR = (
    "json_extract(fields, '$.\"需求状态\"') AS demand_status"
)
_PRODUCT_DEV_STATUS_EXPR = (
    "json_extract(fields, '$.\"开发状态\"') AS dev_status"
)

# vewCz1FFJi / vewpI8lyYw Epic 行：状态、Person 常为 plain string（含 ''），禁止 nested [0].text
_PERSONNEL_STATUS_EXPR = (
    "json_extract(fields, '$.\"状态\"') AS status_text"
)
_DEV_EPIC_PERSON_EXPR = (
    "trim(json_extract(fields, '$.\"Person in charge/Participant\"'))"
)

_VIEW_FIELD_MAP_BLOCK = (
    "**视图字段对照（Worker B/C 仅两张表 · 禁止混用）**：\n"
    "| source_view | 用途 | 任务名 | 优先级 | 负责人 | 状态 |\n"
    f"| **{_PMO_VIEW_PERSONNEL}** | Worker B 主表 | `Requirement` | `priority` | Person → **B-4 UNION** | plain string |\n"
    f"| **{_PMO_VIEW_REQUIREMENTS}** | Worker C 全量；B **B-SUP** 辅表 | `Requirement` | `priority` | B-SUP trim Person | plain string |\n"
    "❌ 禁止自编：`任务标题`、`任务ID`、`负责人`、`关联需求` 等 Jira 式字段名。\n"
    f"❌ **{_PMO_VIEW_PERSONNEL}** 禁止单独 json_each 扫全表（Person 常为 Buck/Seth 字符串 → malformed JSON）。\n"
)

_PRODUCT_VIEW_BLOCK = (
    "**产品视图字段类型（vew8TxMcSh / vewL9Mofgd · 与开发表不同）**：\n"
    "- `需求状态` / `开发状态`：**纯字符串**（如「需求评审通过」「未启动」），"
    "**禁止** `json_extract(json_extract(...), '$[0].text')`（会报 malformed JSON；宿主会拦截）。\n"
    "- `责任人`：对象数组，用 `json_extract(json_extract(fields,'$.\"责任人\"'),'$[0].text')`。\n"
    "- B-1/B-2/C-4：**必须原样执行**下方 SQL 块，**禁止**自行把开发表「状态」[0].text 套到「需求状态」。\n"
    "  ❌ 错误：json_extract(json_extract(fields,'$.\"需求状态\"'),'$[0].text')\n"
    f"  ✅ 正确：{_PRODUCT_DEMAND_STATUS_EXPR}\n"
)

WORKER_B_TASK = (
    "【Worker B · 人员看板单表 + 开发需求辅表】\n"
    + _HONESTY_BLOCK
    + _WORKER_B_TABLE_BLOCK
    + _WORKER_B_SPRINT_BLOCK
    + _WORKER_B_SELF_HEAL_BLOCK
    + _VIEW_FIELD_MAP_BLOCK
    + "**职责边界**：你是 Worker B，**不是 Worker A/C**。禁止 Step1 全量 `pmo_views_meta` 地图；"
    "禁止 `PRAGMA table_info`；纠错仅 `SELECT fields FROM pmo_raw_records WHERE source_view='…' LIMIT 1`。\n"
    + "**字段对齐（强制）**：FanOut 启动时会注入 **【字段对齐·B-x】**（来自 pmo_views_meta 的 columns_json）。"
    "每步 **只读** 对应 B-x 对齐小节，再 **逐字复制** 该步 SQL。\n"
    + "**执行顺序（强制 · 仅 3 步）**：\n"
    "  1) **B-S1**（`vewCz1FFJi` 近三周 Sprint）\n"
    "  2) **B-4**（`vewCz1FFJi` 人员 SSOT · UNION）\n"
    "  3) **B-SUP**（`vewpI8lyYw` 辅表 · Sprint IN recent_sprints）\n"
    "未完成 `completed_sql_ids` 含 **B-S1、B-4、B-SUP** 前 **禁止** User-facing result。\n"
    + "**去重**：B-S1 / B-4 / B-SUP 各只执行一次；Reasoning trace 开头写「已完成: B-S1, B-4, B-SUP」。\n"
    "若 Verification evidence 含 **error / hints / malformed JSON**，同编号最多重试 2 次。\n"
    "User-facing result JSON 结构：\n"
    "  recent_sprints[]（B-S1）\n"
    "  personnel_tasks[]（B-4 · 主表）\n"
    "  requirement_context[]（B-SUP · 按 Requirement 对照 B-4，文字分析勿多表 SQL）\n"
    '  completed_sql_ids: ["B-S1","B-4","B-SUP"]\n\n'
    "**B-S1 · 近三周 Sprint 名（vewCz1FFJi · 必须先于 B-4）**\n"
    "SELECT json_extract(fields, '$.Sprint') AS sprint,\n"
    f"       {_SPRINT_DATE_FROM_FIELDS_EXPR} AS sprint_date,\n"
    "       COUNT(*) AS cnt\n"
    "FROM pmo_raw_records WHERE source_view = 'vewCz1FFJi'\n"
    "  AND json_extract(fields, '$.Sprint') IS NOT NULL AND json_extract(fields, '$.Sprint') != ''\n"
    "  AND json_extract(fields, '$.Sprint') GLOB '????/??/??-Sprint'\n"
    "GROUP BY json_extract(fields, '$.Sprint')\n"
    "HAVING sprint_date IS NOT NULL AND sprint_date >= date('now', '-21 days')\n"
    "ORDER BY sprint_date DESC LIMIT 3;\n"
    "（Reasoning trace 须写明 recent_sprints = 全部 ≤3 行 sprint 文本，填入 B-4 / B-SUP 的 IN）\n\n"
    "**B-4 · 👥 人员安排 SSOT · vewCz1FFJi（B-S1 后执行 · UNION · 近三周 Sprint）**\n"
    "写入 personnel_tasks[]；**禁止**用 B-5 替代。Person 在镜像中常为 **plain string**（非数组）。\n"
    "（一条 SQL 含 UNION ALL：B-4a=Person 字符串；B-4b=Person 数组+json_each，须同次 db_query 提交）\n"
    "SELECT source_view,\n"
    f"       {_PERSON_PLAIN_NAME_SQL} AS person,\n"
    "       json_extract(fields, '$.Requirement') AS task,\n"
    "       json_extract(fields, '$.priority') AS priority,\n"
    "       json_extract(fields, '$.Sprint') AS sprint,\n"
    f"       {_PARENT_TEXT_EXPR} AS department,\n"
    f"       {_PERSONNEL_STATUS_EXPR},\n"
    "       json_extract(fields, '$.\"Version Goal\"') AS version_goal,\n"
    "       json_extract(fields, '$.\"Expectation/Purpose\"') AS expectation_purpose,\n"
    "       json_extract(fields, '$.Progress') AS progress,\n"
    "       json_extract(fields, '$.\"Start Date\"') AS start_date,\n"
    "       json_extract(fields, '$.\"Review Date\"') AS review_date,\n"
    "       json_extract(fields, '$.\"Acceptance Date\"') AS acceptance_date,\n"
    "       json_extract(fields, '$.\"Expected Delivery Date\"') AS expected_delivery_date,\n"
    "       json_extract(fields, '$.\"Actual Delivery Date\"') AS actual_delivery_date,\n"
    "       json_extract(fields, '$.\"任务编号\"') AS task_no\n"
    "FROM pmo_raw_records\n"
    "WHERE source_view = 'vewCz1FFJi'\n"
    f"  AND {_PERSON_PLAIN_WHERE}\n"
    "  AND json_extract(fields, '$.Requirement') IS NOT NULL\n"
    "  AND trim(json_extract(fields, '$.Requirement')) != ''\n"
    "  AND json_extract(fields, '$.\"任务编号\"') IS NOT NULL\n"
    "  AND trim(json_extract(fields, '$.\"任务编号\"')) != ''\n"
    "  AND json_extract(fields, '$.Sprint') IN ('<s1>','<s2>','<s3>')\n"
    "UNION ALL\n"
    "SELECT source_view,\n"
    "       json_extract(value, '$.en_name') AS person,\n"
    "       json_extract(fields, '$.Requirement') AS task,\n"
    "       json_extract(fields, '$.priority') AS priority,\n"
    "       json_extract(fields, '$.Sprint') AS sprint,\n"
    f"       {_PARENT_TEXT_EXPR} AS department,\n"
    f"       {_PERSONNEL_STATUS_EXPR},\n"
    "       json_extract(fields, '$.\"Version Goal\"') AS version_goal,\n"
    "       json_extract(fields, '$.\"Expectation/Purpose\"') AS expectation_purpose,\n"
    "       json_extract(fields, '$.Progress') AS progress,\n"
    "       json_extract(fields, '$.\"Start Date\"') AS start_date,\n"
    "       json_extract(fields, '$.\"Review Date\"') AS review_date,\n"
    "       json_extract(fields, '$.\"Acceptance Date\"') AS acceptance_date,\n"
    "       json_extract(fields, '$.\"Expected Delivery Date\"') AS expected_delivery_date,\n"
    "       json_extract(fields, '$.\"Actual Delivery Date\"') AS actual_delivery_date,\n"
    "       json_extract(fields, '$.\"任务编号\"') AS task_no\n"
    "FROM pmo_raw_records,\n"
    "     json_each(json_extract(fields, '$.\"Person in charge/Participant\"'))\n"
    "WHERE source_view = 'vewCz1FFJi'\n"
    f"  AND {_PERSON_ARRAY_LIKE}\n"
    "  AND person IS NOT NULL AND trim(person) != ''\n"
    "  AND json_extract(fields, '$.\"任务编号\"') IS NOT NULL\n"
    "  AND trim(json_extract(fields, '$.\"任务编号\"')) != ''\n"
    "  AND json_extract(fields, '$.Sprint') IN ('<s1>','<s2>','<s3>')\n"
    "ORDER BY person, sprint, task_no LIMIT 300;\n\n"
    f"**B-SUP · 辅表 · {_PMO_VIEW_REQUIREMENTS}（B-4 后 · 禁止自编字段 · 禁止 C-2 Epic WHERE）**\n"
    "写入 requirement_context[]；**禁止** json_each；**禁止** 父记录 IS NULL / 任务编号 Epic 筛选（那是 Worker C C-2）。\n"
    "SELECT source_view,\n"
    "       json_extract(fields, '$.Requirement') AS requirement,\n"
    "       json_extract(fields, '$.priority') AS priority,\n"
    "       json_extract(fields, '$.Sprint') AS sprint,\n"
    f"       {_DEV_EPIC_PERSON_EXPR} AS person,\n"
    f"       {_PERSONNEL_STATUS_EXPR},\n"
    "       json_extract(fields, '$.Progress') AS progress,\n"
    "       json_extract(fields, '$.\"任务编号\"') AS task_no\n"
    f"FROM pmo_raw_records WHERE source_view = '{_PMO_VIEW_REQUIREMENTS}'\n"
    "  AND json_extract(fields, '$.Sprint') IN ('<s1>','<s2>','<s3>')\n"
    "ORDER BY sprint, requirement LIMIT 300;\n"
)

WORKER_B_MAX_ITERATIONS = 14
WORKER_B_AGENT_MAX_ITERATIONS = 8
WORKER_B_TASK_PREVIEW = (
    f"vewCz1FFJi 人员（B-S1+B-4）+ {_PMO_VIEW_REQUIREMENTS} 辅表（B-SUP）"
)
WORKER_B_AGENT_TASK_PREVIEW = f"B-TOOL/B-SUP（宿主已预取 personnel_tasks[] · current_sprint）"

_WORKER_B_TOOL_FIRST_BLOCK = (
    "**步骤 0（必须优先 · B-TOOL）**：\n"
    "1. 若【宿主预取 JSON】已含 personnel_tasks[] 与 requirement_context[]，**禁止**重跑步骤 0；"
    "直接复制 current_sprint / recent_sprints / personnel_tasks / requirement_context → User-facing result。\n"
    "2. 否则 WorkOrder: core:pmo_personnel_report\n"
    '   tool input: {"recent_window": true}\n'
    "3. Verification evidence 成功 → 用 report 填 current_sprint、recent_sprints[]、personnel_tasks[]、"
    "requirement_context[]、completed_sql_ids 含 **B-TOOL** → User-facing result。\n"
    "4. **仅**步骤 0 失败时，执行下方 B-SUP（db_query · 最多 2 次）；**禁止**重跑 B-S1/B-4。\n\n"
)

_WORKER_B_HOST_AGENT_BLOCK = (
    "**【宿主预取 · 已完成 B-TOOL 或 B-S1+B-4】**（见 user 消息末尾【宿主预取 JSON】）\n"
    "⛔ **禁止** core:db_query 重跑 B-S1 / B-4 / 任何 `vewCz1FFJi` 人员 UNION。\n"
    "⛔ **禁止**用 recent_sprints[0] 覆盖宿主 current_sprint（须 sd≤today）。\n"
    "   仅当 B-SUP **连续 2 次**失败且 hints 指向 Sprint/字段问题时，才允许 **1 次** B-4 重试。\n"
    f"**若宿主已给 requirement_context[]**：可直接 User-facing result，无需 db_query。\n"
    f"**否则第 1 次 db_query = B-SUP**（`{_PMO_VIEW_REQUIREMENTS}` · Sprint IN 用预取 recent_sprints）。\n"
    "B-SUP error/0 行：同编号最多重试 2 次；**禁止**自编 任务标题/任务ID/负责人 等字段名。\n"
    "User-facing result：**原样保留**宿主 current_sprint、recent_sprints[]、personnel_tasks[]，"
    "追加或保留 requirement_context[]，"
    '`completed_sql_ids`: ["B-TOOL"] 或 ["B-S1","B-4","B-SUP"]。\n\n'
)

WORKER_B_AGENT_TASK = (
    "【Worker B · RoleExecutionAgent 段 · B-TOOL 优先 · 仅缺则 B-SUP】\n"
    + _WORKER_B_TOOL_FIRST_BLOCK
    + _WORKER_B_HOST_AGENT_BLOCK
    + _HONESTY_BLOCK
    + _WORKER_B_TABLE_BLOCK
    + "**自我修复（仅 B-SUP）**：\n"
    "| B-SUP 0 行 | 核对 Sprint IN 与预取 recent_sprints 格式（须 `YYYY/MM/DD-Sprint`）；同编号重试 ≤2 |\n"
    "| hints / 字段名错误 | 逐字复制下方 B-SUP SQL；禁止 C-2 Epic WHERE |\n"
    "Reasoning trace 开头写「已完成: B-TOOL(宿主) 或 B-SUP …」。\n\n"
    "User-facing result JSON 结构：\n"
    "  current_sprint（从宿主预取复制 · sd≤today）\n"
    "  current_sprint_date（可选）\n"
    "  recent_sprints[]（从宿主预取复制）\n"
    "  personnel_tasks[]（从宿主预取复制）\n"
    "  requirement_context[]（宿主或 B-SUP 查询结果）\n"
    '  completed_sql_ids: ["B-TOOL"] 或 ["B-S1","B-4","B-SUP"]\n\n'
    f"**B-SUP · 辅表 · {_PMO_VIEW_REQUIREMENTS}（逐字复制 · Sprint IN 见宿主预取）**\n"
    "写入 requirement_context[]；**禁止** json_each；**禁止** 父记录 IS NULL / Epic 筛选（那是 Worker C C-2）。\n"
    "SELECT source_view,\n"
    "       json_extract(fields, '$.Requirement') AS requirement,\n"
    "       json_extract(fields, '$.priority') AS priority,\n"
    "       json_extract(fields, '$.Sprint') AS sprint,\n"
    f"       {_DEV_EPIC_PERSON_EXPR} AS person,\n"
    f"       {_PERSONNEL_STATUS_EXPR},\n"
    "       json_extract(fields, '$.Progress') AS progress,\n"
    "       json_extract(fields, '$.\"任务编号\"') AS task_no\n"
    f"FROM pmo_raw_records WHERE source_view = '{_PMO_VIEW_REQUIREMENTS}'\n"
    "  AND json_extract(fields, '$.Sprint') IN ({sprint_in})\n"
    "ORDER BY sprint, requirement LIMIT 300;\n"
)


def build_worker_b_agent_task(host_seed: dict | None = None) -> str:
    """FanOut Worker B RoleExecutionAgent 任务体：注入宿主预取的 Sprint IN 列表。"""
    in_clause = sprint_in_clause_from_seed(host_seed or {})
    return WORKER_B_AGENT_TASK.replace("{sprint_in}", in_clause)


def sprint_in_clause_from_seed(host_seed: dict) -> str:
    """从宿主 bootstrap 结果生成 B-SUP/C-x 用的 Sprint IN 子句。"""
    names = host_seed.get("sprint_names_for_in")
    if isinstance(names, list) and names:
        return _sql_sprint_in_clause([str(s) for s in names if str(s).strip()])
    rows = host_seed.get("recent_sprints")
    if isinstance(rows, list):
        return _sql_sprint_in_clause(_sprint_names_from_rows(rows))
    return _sql_sprint_in_clause([])


def _sprint_names_from_rows(rows: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for r in rows:
        s = str(r.get("sprint") or "").strip()
        if s and s not in names:
            names.append(s)
    return names


_WORKER_C_TOOL_FIRST_BLOCK = (
    "**步骤 0（必须优先 · C-TOOL）**：\n"
    "1. 若【宿主预取 JSON】已含 epics[]，**禁止**重跑步骤 0；直接整理 User-facing result。\n"
    "2. 否则 WorkOrder: core:pmo_sprint_epic_report\n"
    '   tool input: {"recent_window": true}\n'
    "   （单 Sprint 探针时用 {\"sprint\": \"<C-1 首行 sprint>\"}。）\n"
    "3. Verification evidence 成功 → epics[]←report.epics[]；epic_children[]←report.epic_children[] "
    "或 dev_tasks[]；recent_sprints[]/current_sprint 从 report 填入；completed_sql_ids 含 **C-TOOL**。\n"
    "4. **仅**步骤 0 返回 error 或 epic_count=0 且 C-1 有 Sprint 时，才执行下方 C-1→C-2→C-3（每编号最多 2 次）。\n\n"
)

WORKER_C_TASK = (
    "【Worker C · 近三周 Sprint · Epic 与子任务（单表 vewpI8lyYw）】\n"
    + _WORKER_C_TOOL_FIRST_BLOCK
    + _HONESTY_BLOCK
    + _WORKER_C_TABLE_BLOCK
    + _EPIC_HIERARCHY_BLOCK
    + _SPRINT_TIME_WINDOW_BLOCK
    + _WORKER_C_SELF_HEAL_BLOCK
    + "**db_query · tool input（强制）**：**只写裸 SQL**（从 SELECT 到 `;`），**禁止** `{\"sql\":\"...\"}` JSON 包装"
    "（SQL 内 `\"` 会导致 JSON 解析失败 → missing_sql）。\n"
    + "**去重**：C-1 / C-2 / C-3 各只执行一次（C-6 仅兜底，最多 1 次）；Reasoning trace 开头写「已完成: C-x, …」。\n"
    "目标：近 **3 周** Sprint（C-1 的 recent_sprints，非写死某一周期）采集大需求与子任务；战报 📊 **仅 current_sprint**。\n"
    "User-facing result JSON：current_sprint, recent_sprints[], epics[]（**仅大需求**）, epic_children[], "
    "completed_sql_ids（可含 C-6）\n\n"
    "**C-1 · 当前 Sprint + 近三周 Sprint 名（vewpI8lyYw · 按日期，禁止 latest_row）**\n"
    "SELECT json_extract(fields, '$.Sprint') AS sprint,\n"
    f"       {_SPRINT_DATE_FROM_FIELDS_EXPR} AS sprint_date,\n"
    "       COUNT(*) AS cnt\n"
    "FROM pmo_raw_records WHERE source_view = 'vewpI8lyYw'\n"
    "  AND json_extract(fields, '$.Sprint') IS NOT NULL AND json_extract(fields, '$.Sprint') != ''\n"
    "  AND json_extract(fields, '$.Sprint') GLOB '????/??/??-Sprint'\n"
    "GROUP BY json_extract(fields, '$.Sprint')\n"
    "HAVING sprint_date IS NOT NULL AND sprint_date >= date('now', '-21 days')\n"
    "ORDER BY sprint_date DESC LIMIT 3;\n"
    "（Reasoning trace 须写明：current_sprint = 首行 sprint；recent_sprints = 全部 ≤3 行）\n\n"
    "**C-2 · 近三周大需求（Epic · 须整段逐字复制，禁止删 WHERE）**（C-1 sprint 填入 IN，最多 3 个）\n"
    "⚠️ Person/状态 在 vewpI8lyYw 常为 plain string；**禁止** json_extract(..., '$[0].text')（会 malformed JSON）。\n"
    "SELECT json_extract(fields, '$.Requirement') AS epic_name,\n"
    "       json_extract(fields, '$.Sprint') AS sprint,\n"
    "       json_extract(fields, '$.priority') AS priority,\n"
    "       json_extract(fields, '$.\"Version Goal\"') AS version_goal,\n"
    "       json_extract(fields, '$.\"Expectation/Purpose\"') AS expectation_purpose,\n"
    "       json_extract(fields, '$.Progress') AS progress,\n"
    f"       {_DEV_EPIC_PERSON_EXPR} AS person,\n"
    "       json_extract(fields, '$.\"Start Date\"') AS start_date,\n"
    "       json_extract(fields, '$.\"Review Date\"') AS review_date,\n"
    "       json_extract(fields, '$.\"Acceptance Date\"') AS acceptance_date,\n"
    "       json_extract(fields, '$.\"Expected Delivery Date\"') AS expected_delivery_date,\n"
    "       json_extract(fields, '$.\"Actual Delivery Date\"') AS actual_delivery_date,\n"
    "       json_extract(fields, '$.\"任务编号\"') AS task_no,\n"
    f"       {_PERSONNEL_STATUS_EXPR}\n"
    "FROM pmo_raw_records WHERE source_view = 'vewpI8lyYw'\n"
    f"  AND {_PARENT_EPIC_NULL_SQL}\n"
    "  AND json_extract(fields, '$.Requirement') IS NOT NULL\n"
    "  AND trim(json_extract(fields, '$.Requirement')) != ''\n"
    f"  AND json_extract(fields, '$.Requirement') NOT IN ({_DEPT_PLACEHOLDER_IN})\n"
    "  AND json_extract(fields, '$.\"任务编号\"') IS NOT NULL\n"
    "  AND json_extract(fields, '$.Sprint') IN ('<s1>','<s2>','<s3>')\n"
    "ORDER BY sprint, task_no LIMIT 200;\n\n"
    "**C-3 · 子任务全量 · vewpI8lyYw（COALESCE 父记录 · json_each 执行人）**\n"
    f"SELECT {_PARENT_TEXT_EXPR} AS parent_epic,\n"
    "       json_extract(fields, '$.Requirement') AS task,\n"
    "       json_extract(fields, '$.priority') AS priority,\n"
    "       json_extract(fields, '$.Sprint') AS sprint,\n"
    "       json_extract(fields, '$.\"Version Goal\"') AS version_goal,\n"
    "       json_extract(fields, '$.\"Expectation/Purpose\"') AS expectation_purpose,\n"
    "       json_extract(fields, '$.Progress') AS progress,\n"
    "       json_extract(value, '$.en_name') AS person,\n"
    "       json_extract(fields, '$.\"Start Date\"') AS start_date,\n"
    "       json_extract(fields, '$.\"Review Date\"') AS review_date,\n"
    "       json_extract(fields, '$.\"Acceptance Date\"') AS acceptance_date,\n"
    "       json_extract(fields, '$.\"Expected Delivery Date\"') AS expected_delivery_date,\n"
    "       json_extract(fields, '$.\"Actual Delivery Date\"') AS actual_delivery_date,\n"
    "       json_extract(fields, '$.\"任务编号\"') AS task_no,\n"
    f"       {_PERSONNEL_STATUS_EXPR}\n"
    "FROM pmo_raw_records,\n"
    "     json_each(json_extract(fields, '$.\"Person in charge/Participant\"'))\n"
    "WHERE source_view = 'vewpI8lyYw'\n"
    f"  AND {_PARENT_TEXT_EXPR} IS NOT NULL\n"
    f"  AND trim({_PARENT_TEXT_EXPR}) != ''\n"
    f"  AND json_extract(fields, '$.Requirement') NOT IN ({_DEPT_PLACEHOLDER_IN})\n"
    "  AND json_extract(fields, '$.Sprint') IN ('<s1>','<s2>','<s3>')\n"
    "ORDER BY sprint, parent_epic, task_no LIMIT 500;\n"
    "（parent_epic=开发：用 C-2 顺序 + C-6 row_index 归到上一个 epic_name。）\n"
    "（C-3 的 status 为 plain string，同 C-2，禁止 nested [0].text。）\n\n"
    "**C-6 · 层级探针（兜底 · 仅 C-3 失败或 parent 无法关联时 1 次）**\n"
    f"SELECT row_index, {_PARENT_TEXT_EXPR} AS parent_text,\n"
    "       json_extract(fields, '$.Requirement') AS req,\n"
    "       json_extract(fields, '$.Sprint') AS sprint,\n"
    "       json_extract(fields, '$.\"任务编号\"') AS task_no\n"
    "FROM pmo_raw_records WHERE source_view = 'vewpI8lyYw'\n"
    "  AND json_extract(fields, '$.Sprint') IN ('<s1>','<s2>','<s3>')\n"
    "ORDER BY row_index LIMIT 800;\n"
)

WORKER_C_MAX_ITERATIONS = 12
WORKER_C_TASK_PREVIEW = f"table={_PMO_DEV_TABLE_ID} · vewpI8lyYw · Epic 与子任务"

WORKER_A_VIEWS_SQL = (
    "'vewpI8lyYw','vewCz1FFJi','vew8TxMcSh','vewL9Mofgd','vewjSEz5Xr'"
)

WORKER_A_TASK = (
    "【Worker A · Step 1+2 查字典】\n"
    "1) SELECT view_id, view_name, record_count, columns_json "
    f"FROM pmo_views_meta WHERE view_id IN ({WORKER_A_VIEWS_SQL}) ORDER BY view_id;\n"
    "2) 每视图取一条非空 fields 样本（禁止 GROUP BY 误选空行）：\n"
    "SELECT source_view, fields FROM pmo_raw_records WHERE rowid IN (\n"
    "  SELECT MIN(rowid) FROM pmo_raw_records\n"
    "  WHERE source_view IN ('vew8TxMcSh','vewL9Mofgd','vewpI8lyYw','vewCz1FFJi','vewjSEz5Xr')\n"
    "    AND length(trim(fields)) > 2 AND fields != '{}'\n"
    "  GROUP BY source_view);\n"
    "User-facing result：JSON 含 views_meta[]、samples[]、field_mapping（按视图列出 JSON 键路径）"
)

WORKER_A_MAX_ITERATIONS = 8
WORKER_A_TASK_PREVIEW = "Step 1+2 查字典 — 视图 meta + 样本字段映射"


def _sql_sprint_in_clause(sprints: list[str]) -> str:
    """B-4/C-2 等 Sprint IN 子句；无 Sprint 时用永假占位避免 SQL 语法错误。"""
    quoted = [f"'{s.replace(chr(39), chr(39) + chr(39))}'" for s in sprints if (s or "").strip()]
    if not quoted:
        return "('')"
    return "(" + ",".join(quoted) + ")"


def sql_worker_b_s1() -> str:
    """B-S1：近三周 Sprint（vewCz1FFJi）。"""
    return (
        "SELECT json_extract(fields, '$.Sprint') AS sprint,\n"
        f"       {_SPRINT_DATE_FROM_FIELDS_EXPR} AS sprint_date,\n"
        "       COUNT(*) AS cnt\n"
        "FROM pmo_raw_records WHERE source_view = 'vewCz1FFJi'\n"
        "  AND json_extract(fields, '$.Sprint') IS NOT NULL AND json_extract(fields, '$.Sprint') != ''\n"
        "  AND json_extract(fields, '$.Sprint') GLOB '????/??/??-Sprint'\n"
        "GROUP BY json_extract(fields, '$.Sprint')\n"
        "HAVING sprint_date IS NOT NULL AND sprint_date >= date('now', '-21 days')\n"
        "ORDER BY sprint_date DESC LIMIT 3"
    )


def sql_worker_b_b4(sprints: list[str]) -> str:
    """B-4：人员 SSOT（vewCz1FFJi UNION）。"""
    in_clause = _sql_sprint_in_clause(sprints)
    return (
        "SELECT source_view,\n"
        f"       {_PERSON_PLAIN_NAME_SQL} AS person,\n"
        "       json_extract(fields, '$.Requirement') AS task,\n"
        "       json_extract(fields, '$.priority') AS priority,\n"
        "       json_extract(fields, '$.Sprint') AS sprint,\n"
        f"       {_PARENT_TEXT_EXPR} AS department,\n"
        f"       {_PERSONNEL_STATUS_EXPR},\n"
        "       json_extract(fields, '$.\"Version Goal\"') AS version_goal,\n"
        "       json_extract(fields, '$.\"Expectation/Purpose\"') AS expectation_purpose,\n"
        "       json_extract(fields, '$.Progress') AS progress,\n"
        "       json_extract(fields, '$.\"Start Date\"') AS start_date,\n"
        "       json_extract(fields, '$.\"Review Date\"') AS review_date,\n"
        "       json_extract(fields, '$.\"Acceptance Date\"') AS acceptance_date,\n"
        "       json_extract(fields, '$.\"Expected Delivery Date\"') AS expected_delivery_date,\n"
        "       json_extract(fields, '$.\"Actual Delivery Date\"') AS actual_delivery_date,\n"
        "       json_extract(fields, '$.\"任务编号\"') AS task_no\n"
        "FROM pmo_raw_records\n"
        "WHERE source_view = 'vewCz1FFJi'\n"
        f"  AND {_PERSON_PLAIN_WHERE}\n"
        "  AND json_extract(fields, '$.Requirement') IS NOT NULL\n"
        "  AND trim(json_extract(fields, '$.Requirement')) != ''\n"
        "  AND json_extract(fields, '$.\"任务编号\"') IS NOT NULL\n"
        "  AND trim(json_extract(fields, '$.\"任务编号\"')) != ''\n"
        f"  AND json_extract(fields, '$.Sprint') IN {in_clause}\n"
        "UNION ALL\n"
        "SELECT source_view,\n"
        "       json_extract(value, '$.en_name') AS person,\n"
        "       json_extract(fields, '$.Requirement') AS task,\n"
        "       json_extract(fields, '$.priority') AS priority,\n"
        "       json_extract(fields, '$.Sprint') AS sprint,\n"
        f"       {_PARENT_TEXT_EXPR} AS department,\n"
        f"       {_PERSONNEL_STATUS_EXPR},\n"
        "       json_extract(fields, '$.\"Version Goal\"') AS version_goal,\n"
        "       json_extract(fields, '$.\"Expectation/Purpose\"') AS expectation_purpose,\n"
        "       json_extract(fields, '$.Progress') AS progress,\n"
        "       json_extract(fields, '$.\"Start Date\"') AS start_date,\n"
        "       json_extract(fields, '$.\"Review Date\"') AS review_date,\n"
        "       json_extract(fields, '$.\"Acceptance Date\"') AS acceptance_date,\n"
        "       json_extract(fields, '$.\"Expected Delivery Date\"') AS expected_delivery_date,\n"
        "       json_extract(fields, '$.\"Actual Delivery Date\"') AS actual_delivery_date,\n"
        "       json_extract(fields, '$.\"任务编号\"') AS task_no\n"
        "FROM pmo_raw_records,\n"
        "     json_each(json_extract(fields, '$.\"Person in charge/Participant\"'))\n"
        "WHERE source_view = 'vewCz1FFJi'\n"
        f"  AND {_PERSON_ARRAY_LIKE}\n"
        "  AND person IS NOT NULL AND trim(person) != ''\n"
        "  AND json_extract(fields, '$.\"任务编号\"') IS NOT NULL\n"
        "  AND trim(json_extract(fields, '$.\"任务编号\"')) != ''\n"
        f"  AND json_extract(fields, '$.Sprint') IN {in_clause}\n"
        "ORDER BY person, sprint, task_no LIMIT 300"
    )


def sql_worker_b_b_sup(sprints: list[str]) -> str:
    """B-SUP：vewp 辅表需求上下文（近三周 Sprint IN）。"""
    in_clause = _sql_sprint_in_clause(sprints)
    return (
        "SELECT source_view,\n"
        "       json_extract(fields, '$.Requirement') AS requirement,\n"
        "       json_extract(fields, '$.priority') AS priority,\n"
        "       json_extract(fields, '$.Sprint') AS sprint,\n"
        f"       {_DEV_EPIC_PERSON_EXPR} AS person,\n"
        f"       {_PERSONNEL_STATUS_EXPR},\n"
        "       json_extract(fields, '$.Progress') AS progress,\n"
        "       json_extract(fields, '$.\"任务编号\"') AS task_no\n"
        f"FROM pmo_raw_records WHERE source_view = '{_PMO_VIEW_REQUIREMENTS}'\n"
        f"  AND json_extract(fields, '$.Sprint') IN {in_clause}\n"
        "ORDER BY sprint, requirement LIMIT 300"
    )


def sql_worker_c_c1() -> str:
    """C-1：近三周 Sprint（vewpI8lyYw）。"""
    return (
        "SELECT json_extract(fields, '$.Sprint') AS sprint,\n"
        f"       {_SPRINT_DATE_FROM_FIELDS_EXPR} AS sprint_date,\n"
        "       COUNT(*) AS cnt\n"
        "FROM pmo_raw_records WHERE source_view = 'vewpI8lyYw'\n"
        "  AND json_extract(fields, '$.Sprint') IS NOT NULL AND json_extract(fields, '$.Sprint') != ''\n"
        "  AND json_extract(fields, '$.Sprint') GLOB '????/??/??-Sprint'\n"
        "GROUP BY json_extract(fields, '$.Sprint')\n"
        "HAVING sprint_date IS NOT NULL AND sprint_date >= date('now', '-21 days')\n"
        "ORDER BY sprint_date DESC LIMIT 3"
    )


def sql_worker_c_c2(sprints: list[str]) -> str:
    """C-2：近三周大需求 Epic（vewpI8lyYw · plain string Person/状态）。"""
    in_clause = _sql_sprint_in_clause(sprints)
    return (
        "SELECT json_extract(fields, '$.Requirement') AS epic_name,\n"
        "       json_extract(fields, '$.Sprint') AS sprint,\n"
        "       json_extract(fields, '$.priority') AS priority,\n"
        "       json_extract(fields, '$.\"Version Goal\"') AS version_goal,\n"
        "       json_extract(fields, '$.\"Expectation/Purpose\"') AS expectation_purpose,\n"
        "       json_extract(fields, '$.Progress') AS progress,\n"
        f"       {_DEV_EPIC_PERSON_EXPR} AS person,\n"
        "       json_extract(fields, '$.\"Start Date\"') AS start_date,\n"
        "       json_extract(fields, '$.\"Review Date\"') AS review_date,\n"
        "       json_extract(fields, '$.\"Acceptance Date\"') AS acceptance_date,\n"
        "       json_extract(fields, '$.\"Expected Delivery Date\"') AS expected_delivery_date,\n"
        "       json_extract(fields, '$.\"Actual Delivery Date\"') AS actual_delivery_date,\n"
        "       json_extract(fields, '$.\"任务编号\"') AS task_no,\n"
        f"       {_PERSONNEL_STATUS_EXPR}\n"
        "FROM pmo_raw_records WHERE source_view = 'vewpI8lyYw'\n"
        f"  AND {_PARENT_EPIC_NULL_SQL}\n"
        "  AND json_extract(fields, '$.Requirement') IS NOT NULL\n"
        "  AND trim(json_extract(fields, '$.Requirement')) != ''\n"
        f"  AND json_extract(fields, '$.Requirement') NOT IN ({_DEPT_PLACEHOLDER_IN})\n"
        "  AND json_extract(fields, '$.\"任务编号\"') IS NOT NULL\n"
        f"  AND json_extract(fields, '$.Sprint') IN {in_clause}\n"
        "ORDER BY sprint, task_no LIMIT 200"
    )


WORKER_D_MAX_ITERATIONS = 4
WORKER_D_AGENT_MAX_ITERATIONS = 3
WORKER_D_TASK_PREVIEW = "D-TOOL（宿主已预取 completed_epics[] · 发版邮件窗）"

_WORKER_D_TABLE_BLOCK = (
    "**📦 Worker D · 发版 Epic 清单（禁止读人员表 / Version Goal 辅表）**：\n"
    "- **唯一工具**：`core:pmo_release_epic_mapping`（Vivian 邮箱 + vewpI8lyYw 完成度 100% Epic）。\n"
    "- **禁止** `core:db_query`；**禁止** Worker B/C 的 B-SUP / C-2 SQL。\n"
)

_WORKER_D_TOOL_FIRST_BLOCK = (
    "**步骤 0（必须优先 · D-TOOL）**：\n"
    "1. 若【宿主预取 JSON】已含 `markdown_section` 或 `completed_sql_ids` 含 **D-TOOL**，"
    "**禁止**重跑步骤 0；直接复制 window_* / completed_epics[] / markdown_section → User-facing result。\n"
    "2. 否则 WorkOrder: core:pmo_release_epic_mapping\n"
    '   tool input: {}\n'
    "3. Verification evidence status=ok → 填入 completed_epics[]、completed_count、markdown_section、"
    "window_since/window_until、since_mail_subject、since_maintenance_date；"
    "completed_sql_ids 含 **D-TOOL** → User-facing result。\n"
    "4. **仅**步骤 0 失败时，可再调 **1 次** core:pmo_release_epic_mapping（禁止同参无限重试）。\n\n"
)

_WORKER_D_HOST_AGENT_BLOCK = (
    "**【宿主预取 · 已完成 D-TOOL】**（见 user 消息末尾【宿主预取 JSON】）\n"
    "⛔ **禁止**重跑 core:pmo_release_epic_mapping（避免重复调邮件 API）。\n"
    "⛔ **禁止** core:db_query / Version Goal 统计 / 人员表查询。\n"
    "User-facing result：**原样保留**宿主 completed_epics[]、markdown_section、window 字段，"
    '`completed_sql_ids`: ["D-TOOL"]。\n\n'
)

WORKER_D_AGENT_TASK = (
    "【Worker D · RoleExecutionAgent 段 · D-TOOL 优先 · 发版邮件窗已完成 Epic】\n"
    + _WORKER_D_TOOL_FIRST_BLOCK
    + _WORKER_D_HOST_AGENT_BLOCK
    + _HONESTY_BLOCK
    + _WORKER_D_TABLE_BLOCK
    + "Reasoning trace 开头写「已完成: D-TOOL(宿主) 或 D-TOOL …」。\n\n"
    "User-facing result JSON 结构：\n"
    "  window_since, window_until（ISO 8601）\n"
    "  since_mail_subject, since_maintenance_date（可选）\n"
    "  completed_epics[]（顶层 Epic：epic_name, priority, sprint, completion_date, person）\n"
    "  completed_count\n"
    "  markdown_section（📦 GFM 段，供 Publisher 拼接）\n"
    '  completed_sql_ids: ["D-TOOL"]\n'
    "  error_reason（仅邮件/镜像失败时）\n"
)

WORKER_D_TASK = WORKER_D_AGENT_TASK


def build_worker_d_agent_task(host_seed: dict | None = None) -> str:
    """FanOut Worker D RoleExecutionAgent 任务体（宿主预取为主，无 SQL 注入）。"""
    _ = host_seed  # 保留签名与 B/C 对齐
    return WORKER_D_AGENT_TASK
