# HR 招聘 Skill 包

**纯 SKILL.md**：定义 HR 招聘全套流程（**分支 A 新发帖** + **分支 B 已有岗位仅收网/并行打招呼**），指挥 Agent 一步步执行。

## 架构

- **Skill 包**（本目录）：`SKILL.md` 定义 **Agent 对话层** SOP（多轮问 JD、发布、停止等）
- **MCP 包**：`skills_repo/plugin/com.jachin.hr.recruitment/` 提供工具与调度
- **DAG 编排**（主仓）：`l3_node/primitives/skills/hr_recruitment_dag.py` — 无人值守打招呼/收网走 **状态机 + 信号 + 物理 progress**，与 Skill 互补

**主仓单一事实文档**：`docs/HR_RECRUITMENT.md`（含路径、STOP、规划文件说明）。

```
Skill 包 (hr-recruitment/)
└── SKILL.md     ← 定义流程：收集 JD → 确认发布 → 无人值守 → 分析简历 → 排行榜

MCP 包 (com.jachin.hr.recruitment)
├── atom_post_job_boss
├── atom_greet_recommend_boss
├── add_automated_recruitment_task
├── stop_automated_recruitment
├── atom_lark_chat
└── hr_analyze_resume   ← 简历分析（包装 Wasm com.jachin.hr.analyzer4）
```

## 加载顺序

L3 Agent 通过 `_load_hr_recruitment_skill_content()` 加载 SKILL.md，优先级：

1. `skills_repo/hr-recruitment/SKILL.md`
2. `~/.jachin/l3_skill_cache/hr-recruitment/SKILL.md`
3. `~/.jachin/l3_mcp_cache/com.jachin.hr.recruitment/SKILL.md`（向后兼容）

## 依赖

- **com.jachin.hr.recruitment** MCP 包（工具）
- **com.jachin.hr.analyzer4** Wasm（hr_analyze_resume 底层）
- **com.jachin.hr.filesystem**（Wasm 读取简历需）
