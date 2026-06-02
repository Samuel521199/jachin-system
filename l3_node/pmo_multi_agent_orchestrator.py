"""
PMO-Copilot 方案 B：三阶段多 Agent 编排。

阶段一 FanOut（并行捞数）→ 阶段二 Pipeline（交叉审计）→ 阶段三 run_agent（排版发报）。

代码锚点：scripts/run_pmo_copilot_skill.py --multi-agent
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from l3_node.agent_core import PMO_BRANCH_A_MIN_DB_QUERIES, PMO_BRANCH_A_PERSONNEL_SSOT_VIEW
from l3_node.agent_core import (
    PMO_BRANCH_A_PRODUCT_VIEW_ALTS,
    PMO_BRANCH_A_REQUIRED_CROSS_VIEWS,
)

logger = logging.getLogger("pmo_multi_agent")

PMO_WORKER_DB_ROLE: dict[str, Any] = {
    "id": "analyst",
    "system_prefix": (
        "你是 PMO 数据搬砖工（Analyst）。\n"
        "唯一职责：用 core:db_query 执行指定 SQL，将 Observation 整理为 **合法 JSON** 作为 Final Answer。\n"
        "规则：\n"
        "- 字段名以 columns_json 为准；禁止 Task/Status/负责人 等臆造键名\n"
        "- Person 字段必须用 json_each 展开\n"
        "- 状态须 json_extract(json_extract(fields,'$.\"状态\"'),'$[0].text')\n"
        "- Epic 须 json_extract(fields,'$.\"父记录\"[0].text') IS NULL（禁止 json_extract(父记录) IS NULL）\n"
        "- Sprint 用 json_extract(fields,'$.Sprint')，禁止 [0].text\n"
        "- Final Answer **仅输出 JSON**（可含 observations 数组记录每步 SQL 与 rows），禁止 GFM 战报表\n"
    ),
    "allowed_tools": ["core:db_query"],
}

PMO_AUDITOR_ROLE: dict[str, Any] = {
    "id": "reviewer",
    "system_prefix": (
        "你是 PMO 交叉审计员（Reviewer / Auditor）。\n"
        "⛔ **禁止**调用 core:db_query 或任何数据库工具；仅基于提供的 JSON 做 Step 6 交叉分析。\n"
        "须检查：\n"
        "1. 幽灵需求：Epic/主表有、人员看板无（或反之）\n"
        "2. 状态倒挂：同一需求在不同视图状态矛盾\n"
        "3. 人员超载：按 §1.4.1b「计划周期×完成进度×当前时间」节奏判定 🚨/🟡/✅（**禁止** task_cnt 排名定过载）\n"
        "4. Sprint 集合差：两视图 Sprint 不一致项\n"
        "输出：**项目风险诊断书**（Markdown，分 ## 章节；每条风险含 ⚠️ 与依据）\n"
    ),
    "allowed_tools": [],
}

PMO_PUBLISHER_USER_TEMPLATE = """【PMO 多 Agent · 阶段三 · 排版发报】
前序阶段已完成数据捞取与交叉审计。**禁止** core:db_query / mirror_import / bi_project_context。

你的唯一任务：
1. 将下方 JSON 与「风险诊断书」死板填入 §1.4 三张 **GFM Markdown 表**：
   - 📊 需求进度全览
   - 👥 人员任务矩阵
   - 📦 版本发布需求映射
2. 每表须含表头行 + `|---|---|` 分隔 + 至少 3 行数据（缺口用 ⚠️ 占位行）
3. 将 **完整 markdown_content 全文** 写入 mcp:atom_lark_notifier（**两次**：主群 + 监控群，`native_table_card: true`）
4. Final Answer 仅在双群 notifier 均 success 后 ≤3 句确认

【阶段一 · 字段映射 JSON（Worker A）】
{worker_a}

【阶段一 · 人员负荷 JSON（Worker B）】
{worker_b}

【阶段一 · Epic/Sprint/Version JSON（Worker C）】
{worker_c}

【阶段二 · 项目风险诊断书（Auditor）】
{audit_report}
"""


def build_pmo_multi_agent_implicit_attribution() -> dict[str, Any]:
    """阶段三 run_agent 用：标记多 Agent 已完成探针，允许直推。"""
    views = set(PMO_BRANCH_A_REQUIRED_CROSS_VIEWS)
    views.add(PMO_BRANCH_A_PERSONNEL_SSOT_VIEW)
    views.update(PMO_BRANCH_A_PRODUCT_VIEW_ALTS)
    probes = {
        "sprint": True,
        "status": True,
        "personnel": True,
        "personnel_kanban": True,
        "version": True,
        "epic": True,
        "cross_view_6a": True,
        "cross_view_6b": True,
    }
    return {
        "channel": "pmo_copilot_cli",
        "source": "pmo_multi_agent_phase3",
        "pmo_analysis_only": True,
        "pmo_db_ready": True,
        "pmo_multi_agent_complete": True,
        "pmo_multi_agent_seed": {
            "_pmo_db_query_count": PMO_BRANCH_A_MIN_DB_QUERIES,
            "_pmo_analysis_probes": probes,
            "_pmo_views_queried": sorted(views),
            "_pmo_step1_map_done": True,
        },
    }


def apply_pmo_multi_agent_metadata_seed(metadata: dict[str, Any], implicit: dict[str, Any]) -> None:
    """将 implicit_attribution.pmo_multi_agent_seed 合并进 run_agent metadata。"""
    if not implicit.get("pmo_multi_agent_complete"):
        return
    metadata["pmo_multi_agent_complete"] = True
    seed = implicit.get("pmo_multi_agent_seed")
    if not isinstance(seed, dict):
        return
    for k, v in seed.items():
        metadata[k] = v


@dataclass
class PmoMultiAgentResult:
    status: str  # completed | partial | failed
    phase1: Any = None
    phase2_output: str = ""
    phase3_answer: str = ""
    worker_a: str = ""
    worker_b: str = ""
    worker_c: str = ""
    audit_report: str = ""
    errors: list[str] = field(default_factory=list)

    def format_summary(self) -> str:
        lines = [f"[PMO Multi-Agent] status={self.status}"]
        if self.errors:
            lines.append("  错误: " + "; ".join(self.errors[:5]))
        if self.phase3_answer:
            lines.append(f"  Final: {self.phase3_answer[:200]}…")
        return "\n".join(lines)


def _phase1_fanout_items() -> list[dict[str, Any]]:
    return [
        {
            "role": PMO_WORKER_DB_ROLE,
            "task": (
                "【Worker A · Step 1+2 查字典】\n"
                "1) SELECT view_id, view_name, record_count, columns_json "
                "FROM pmo_views_meta WHERE view_id IN "
                "('vewpI8lyYw','vewCz1FFJi','vew8TxMcSh','vewL9Mofgd') ORDER BY view_id;\n"
                "2) SELECT source_view, fields FROM pmo_raw_records "
                "WHERE source_view IN ('vewpI8lyYw','vewCz1FFJi') "
                "GROUP BY source_view LIMIT 2;\n"
                "Final Answer：JSON 含 views_meta[]、samples[]、field_mapping（Requirement/状态/Sprint/Person 路径说明）"
            ),
            "max_iterations": 8,
        },
        {
            "role": PMO_WORKER_DB_ROLE,
            "task": (
                "【Worker B · Step 3 查人力 · vewCz1FFJi SSOT】\n"
                "执行以下 SQL（禁止删列、禁止 [0].en_name）：\n"
                "SELECT json_extract(value, '$.en_name') AS person,\n"
                "       json_extract(fields, '$.Requirement') AS task,\n"
                "       json_extract(json_extract(fields, '$.\"状态\"'), '$[0].text') AS status_text,\n"
                "       json_extract(fields, '$.Sprint') AS sprint,\n"
                "       json_extract(fields, '$.\"Expected Delivery Date\"') AS due,\n"
                "       json_extract(fields, '$.Progress') AS progress\n"
                "FROM pmo_raw_records,\n"
                "     json_each(json_extract(fields, '$.\"Person in charge/Participant\"'))\n"
                "WHERE source_view = 'vewCz1FFJi'\n"
                "  AND person IS NOT NULL AND person != ''\n"
                "LIMIT 200;\n"
                "Final Answer：JSON 含 personnel_tasks[]，按 person 分组摘要 optional"
            ),
            "max_iterations": 6,
        },
        {
            "role": PMO_WORKER_DB_ROLE,
            "task": (
                "【Worker C · Step 4+5+7 查进度】\n"
                "1) Epic（仅 vewpI8lyYw）：\n"
                "SELECT json_extract(fields, '$.Requirement') AS epic,\n"
                "       json_extract(fields, '$.Sprint') AS sprint,\n"
                "       json_extract(fields, '$.priority') AS priority\n"
                "FROM pmo_raw_records\n"
                "WHERE source_view = 'vewpI8lyYw'\n"
                "  AND json_extract(fields, '$.\"父记录\"[0].text') IS NULL\n"
                "LIMIT 100;\n"
                "2) 状态分布（vewCz1FFJi GROUP BY）：\n"
                "SELECT json_extract(json_extract(fields, '$.\"状态\"'), '$[0].text') AS status_text,\n"
                "       COUNT(*) AS cnt\n"
                "FROM pmo_raw_records WHERE source_view = 'vewCz1FFJi'\n"
                "GROUP BY status_text;\n"
                "3) Sprint 分布（两视图）：\n"
                "SELECT source_view, json_extract(fields, '$.Sprint') AS sprint, COUNT(*) AS cnt\n"
                "FROM pmo_raw_records\n"
                "WHERE source_view IN ('vewpI8lyYw','vewCz1FFJi')\n"
                "GROUP BY source_view, json_extract(fields, '$.Sprint');\n"
                "4) Version Goal 填写率（vewpI8lyYw + vewCz1FFJi）：\n"
                "SELECT source_view,\n"
                "       COUNT(*) AS total,\n"
                "       SUM(CASE WHEN json_extract(fields, '$.\"Version Goal\"') IS NOT NULL "
                "AND json_extract(fields, '$.\"Version Goal\"') != 'null' THEN 1 ELSE 0 END) AS filled\n"
                "FROM pmo_raw_records\n"
                "WHERE source_view IN ('vewpI8lyYw','vewCz1FFJi')\n"
                "GROUP BY source_view;\n"
                "Final Answer：JSON 含 epics[]、status_distribution[]、sprint_distribution[]、version_goal_stats[]"
            ),
            "max_iterations": 10,
        },
    ]


async def run_pmo_multi_agent_workflow(
    engine: Any,
    *,
    parent_allowed_skills: list[str],
    on_status: Any | None = None,
) -> PmoMultiAgentResult:
    """执行 PMO 三阶段多 Agent 工作流；阶段三由调用方 run_agent 完成。"""
    from l3_node.primitives.multi_agent.fanout import fanout_parallel
    from l3_node.primitives.multi_agent.pipeline import PipelineStage, run_pipeline

    result = PmoMultiAgentResult(status="failed")

    def _status(msg: str) -> None:
        logger.info("[PMO Multi-Agent] %s", msg)
        if on_status:
            try:
                on_status(msg)
            except Exception:
                pass

    _status("阶段一：FanOut 并行捞数（Worker A/B/C）…")
    phase1 = await fanout_parallel(
        _phase1_fanout_items(),
        engine,
        max_concurrent=3,
        delegate_depth=1,
        item_max_iterations=10,
        parent_allowed_skills=parent_allowed_skills,
    )
    result.phase1 = phase1

    if phase1.ok_count == 0:
        result.errors.append("阶段一全部失败")
        result.status = "failed"
        return result

    by_idx = {item.index: item for item in phase1.items}
    result.worker_a = by_idx[1].result if by_idx.get(1) and by_idx[1].ok else ""
    result.worker_b = by_idx[2].result if by_idx.get(2) and by_idx[2].ok else ""
    result.worker_c = by_idx[3].result if by_idx.get(3) and by_idx[3].ok else ""

    if phase1.failed_count:
        result.errors.extend(
            f"Worker {it.index}({it.role}): {it.error[:120]}"
            for it in phase1.failed_items
        )

    bundle_json = json.dumps(
        {
            "worker_a_field_map": _clip(result.worker_a, 8000),
            "worker_b_personnel": _clip(result.worker_b, 12000),
            "worker_c_progress": _clip(result.worker_c, 12000),
        },
        ensure_ascii=False,
        indent=2,
    )

    _status("阶段二：Pipeline 交叉审计（Auditor · 无 db_query）…")
    audit_pipeline = await run_pipeline(
        stages=[
            PipelineStage(
                role=PMO_AUDITOR_ROLE,
                task=(
                    "基于【阶段一 JSON 数据包】执行 Step 6 跨视图矛盾检验，"
                    "输出《项目风险诊断书》（Markdown）。"
                    "数据包见 context_data。"
                ),
                max_iterations=5,
                pass_context=True,
            ),
        ],
        initial_context=bundle_json,
        engine=engine,
        delegate_depth=1,
        parent_allowed_skills=parent_allowed_skills,
    )

    if audit_pipeline.status == "aborted" or not audit_pipeline.final_output.strip():
        result.errors.append(
            audit_pipeline.execution_brief or "阶段二审计失败或无输出"
        )
        result.status = "partial" if phase1.ok_count > 0 else "failed"
        result.audit_report = audit_pipeline.final_output or ""
        return result

    result.audit_report = audit_pipeline.final_output
    result.phase2_output = audit_pipeline.final_output
    result.status = "partial" if phase1.failed_count else "completed"
    _status(f"阶段一 {phase1.ok_count}/3 · 阶段二完成 · 待阶段三排版发报")
    return result


def build_publisher_user_message(workflow: PmoMultiAgentResult) -> str:
    return PMO_PUBLISHER_USER_TEMPLATE.format(
        worker_a=_clip(workflow.worker_a, 6000),
        worker_b=_clip(workflow.worker_b, 8000),
        worker_c=_clip(workflow.worker_c, 8000),
        audit_report=_clip(workflow.audit_report, 6000),
    )


def _clip(text: str, n: int) -> str:
    t = (text or "").strip()
    if len(t) <= n:
        return t
    return t[: n - 1] + "…"
