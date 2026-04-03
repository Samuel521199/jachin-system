"""
参谋长范式（软拦截）：与 task_plan / planning_composite / 槽位追问等统一话术骨架。
硬拦截（OOD 乱码等）仍走固定短拒答，不使用本模块。
"""

CHIEF_ADVISOR_SYSTEM_BLOCK = """你现在是 Jachin 核心参谋长。请参考上方的 [ENVIRONMENT_REPORT]（若存在：Git 现状与安全法典摘要、本地经验摘要）辅助决策。
若用户的操作违背安全锁条文，或缺少必填槽位 / 规划门禁未通过，你须拦截继续执行，并严格按以下格式回复统帅（勿只回答「不行」）：

【情报汇整】：简述拦截原因（可引用安全锁或缺失参数 / 门禁说明）。

【行动预案】：必须给出至少 2 条可直接采纳的替代路径（例如：只读探查、缩小范围执行、先写 task_plan.md 再执行、申请人工特批/改走后台任务等）。"""


def task_plan_gate_user_message() -> str:
    return (
        "【情报汇整】当前任务命中 **task_plan 门禁**：尚未在工作区根目录落盘可用的 task_plan.md（目标与步骤不足）。\n"
        "【行动预案】（1）使用 Action: core:fs_write 将计划写入 workspace 根目录 task_plan.md 后再执行写操作、Shell、delegate 或 coordinate；"
        "（2）若仅需咨询或只读分析，请明确说明「只读/不改仓库」，我将仅使用只读工具回答。\n"
        "（系统标签 · task_plan）"
    )


def planning_composite_gate_user_message() -> str:
    return (
        "【情报汇整】当前处于 **复合规划阶段**（planning_composite）：须先完成可执行计划落盘并通过静态校验，不可直接执行高风险工具或 MCP。\n"
        "【行动预案】（1）使用 core:fs_write 将计划写入 workspace 根目录 task_plan.md，工具 id 须在可见白名单内；"
        "（2）若关键信息不足，先输出 [Needs_Info: …] 向用户反问；"
        "（3）若用户仅需摘要或只读说明，请明确降级范围以便仅使用只读/检索类工具。\n"
        "（系统标签 · planning_composite）"
    )
