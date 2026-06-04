"""
PMO 战报 §1.4 版式 SSOT（需求进度全览等 GFM 表列定义）。
"""
from __future__ import annotations

PMO_DEMAND_TABLE_HEADERS: tuple[str, ...] = (
    "需求名称",
    "时间跨度",
    "参与人",
    "完成度",
    "状态",
)

PMO_DEMAND_TABLE_FORBIDDEN_HEADERS: tuple[str, ...] = (
    "风险说明",
    "优先级",
    "进度条",
)

PMO_DEMAND_TABLE_GFM_TEMPLATE = (
    "| 需求名称 | 时间跨度 | 参与人 | 完成度 | 状态 |\n"
    "| --- | --- | --- | --- | --- |\n"
    "| （大需求名） | 05/01→05/25 | Ethan; Celine | [▓▓▓░░░░░░░] 30% | 🔵 按时完成 |"
)

PMO_DEMAND_TABLE_PUBLISHER_SPEC = (
    "**📊 需求进度全览 — 固定 5 列（禁止增删改列名）**\n"
    "```\n"
    + PMO_DEMAND_TABLE_GFM_TEMPLATE
    + "\n```\n"
    "- **需求名称**：Worker C `epics[].epic_name`（大需求 Requirement，每行一个 Epic）\n"
    "- **时间跨度**：`Start Date`～`Expected Delivery Date`，或当前 Sprint 周期；示例 `05/18→05/25`\n"
    "- **参与人**：该 Epic 下子任务 person 汇总（含父记录 Epic 链接链采集，见 pmo_sprint_query._collect_epic_chain_tasks）；可兜底 personnel_tasks；分号分隔\n"
    "- **完成度**：**仅此列**写 10 格进度条 + 百分比，如 `[▓▓▓░░░░░░░] 30%`（**禁止**单独「进度条」列）\n"
    "- **状态**：汇总状态（🟢🔵🟡🔴 + 短语），如 `🔵 按时完成`\n"
    "- **禁止列**：优先级、风险说明、说明、审计长文等；**禁止**把「项目风险诊断书」写入表内。\n"
    "  审计结论放在 📊 **表上方** 1～3 句摘要（可选），表内仅上述 5 列。\n"
)


def pmo_demand_table_header_line(mc: str, section_keywords: tuple[str, ...]) -> str:
    """取 📊 区块后第一个含 | 且非 --- 分隔行的表头。"""
    if not mc:
        return ""
    start = -1
    for kw in section_keywords:
        i = mc.find(kw)
        if i >= 0:
            start = i if start < 0 else min(start, i)
    if start < 0:
        return ""
    for line in mc[start : start + 1200].splitlines():
        s = line.strip()
        if "|" in s and "---" not in s and s.count("|") >= 2:
            return s
    return ""


def pmo_demand_table_column_issues(
    mc: str,
    section_keywords: tuple[str, ...],
) -> list[str]:
    """📊 表头列是否符合 5 列 SSOT。"""
    header = pmo_demand_table_header_line(mc, section_keywords)
    if not header:
        return []
    issues: list[str] = []
    for col in PMO_DEMAND_TABLE_HEADERS:
        if col not in header:
            issues.append(f"缺少列「{col}」")
    for col in PMO_DEMAND_TABLE_FORBIDDEN_HEADERS:
        if col in header:
            issues.append(f"禁止列「{col}」")
    return issues
