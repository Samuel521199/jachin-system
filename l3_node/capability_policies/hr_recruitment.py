"""HR recruitment capability final-answer guards."""

from __future__ import annotations

import logging
import re
from typing import Any

from l3_node.cognitive_kernel.capability_hook_bridge import build_work_order_suggestion
from l3_node.engine.hooks_pipeline import PipelineContext

logger = logging.getLogger(__name__)


def answer_claims_job_published(ans: str) -> bool:
    """Detect whether an answer claims a Boss job post has already succeeded."""

    text = ans or ""
    return bool(
        "JOB_" in text
        or re.search(r"职位.*已发布|发布.*成功|已在\s*Boss|Boss.*上架|job.*published", text, re.I)
    )


def answer_claims_unmanned_scheduler_running(ans: str) -> bool:
    """Detect whether the answer claims unattended recruitment scheduling is running."""

    text = ans or ""
    return bool(
        re.search(r"无人值守|自动招聘|收网|抓简历|调度", text, re.I)
        and re.search(r"已启动|运行中|已开始|任务\s*ID|TASK_AUTO|hr_recruit", text, re.I)
    )


def recruitment_success_answer(ctx: Any, ans: str) -> bool:
    """Recruitment flow success answer, based on executed tools and answer text."""

    if answer_claims_job_published(ans) or "TASK_AUTO" in (ans or ""):
        return True
    tools = getattr(ctx, "_executed_tools_this_run", None) or set()
    if "add_automated_recruitment_task" in tools and re.search(r"无人值守|收网|抓简历|调度|自动招聘|已启动", ans or ""):
        return True
    if "hr_scheduler_send_confirm_prompt" in tools and re.search(r"飞书|调度|确认|定时任务|无人值守", ans or ""):
        return True
    return False


def build_job_published_without_tool_prompt() -> str:
    return build_work_order_suggestion(
        tool="mcp:atom_post_job_boss",
        work_order_input={"jd_config": "$previous_confirmed_jd_config"},
        reason="hr_claimed_job_published_without_tool",
        role_agent="MessageExecutorAgent",
        visible_message="回答声称职位已发布，但缺少发布工具证据；已生成 Boss 发帖 WorkOrder 建议。",
    )


def build_scheduler_confirm_missing_prompt(*, branch_b: bool = False) -> str:
    tool = "mcp:add_automated_recruitment_task" if branch_b else "mcp:hr_scheduler_send_confirm_prompt"
    payload = (
        {"source": "previous_branch_b_config"}
        if branch_b
        else {"job_name": "$confirmed_job_name", "source": "previous_publish_result"}
    )
    return build_work_order_suggestion(
        tool=tool,
        work_order_input=payload,
        reason="hr_scheduler_confirmation_missing",
        role_agent="MessageExecutorAgent",
        visible_message="职位发布后缺少调度确认/启动证据；已生成招聘调度 WorkOrder 建议。",
    )


def build_scheduler_running_without_tool_prompt() -> str:
    return build_work_order_suggestion(
        tool="mcp:add_automated_recruitment_task",
        work_order_input={
            "job_name": "$confirmed_job_name",
            "resume_collect_target": "$resume_collect_target",
            "analyze_threshold": "$analyze_threshold",
        },
        reason="hr_claimed_scheduler_running_without_tool",
        role_agent="MessageExecutorAgent",
        visible_message="回答声称招聘调度已运行，但缺少调度工具证据；已生成自动招聘任务 WorkOrder 建议。",
    )


def reject_hr_final_answer_guards(
    ctx: PipelineContext,
    messages: list[dict[str, Any]],
    response: str,
    ans: str,
    *,
    branch_b_context: bool,
    skip_force_atom_post: bool,
    via: str,
) -> bool:
    """Reject HR final answers that claim side effects without the matching tool call."""

    tools = getattr(ctx, "_executed_tools_this_run", set()) or set()
    has_success = recruitment_success_answer(ctx, ans)
    no_post = "atom_post_job_boss" not in tools
    sched_step_done = "add_automated_recruitment_task" in tools or "hr_scheduler_send_confirm_prompt" in tools

    if not branch_b_context and not skip_force_atom_post and has_success and no_post and answer_claims_job_published(ans):
        logger.warning("[CapabilityHook][hr_recruitment] via=%s claimed job published without atom_post_job_boss", via)
        messages.append({"role": "assistant", "content": response})
        messages.append({"role": "user", "content": build_job_published_without_tool_prompt()})
        return True

    if (
        not branch_b_context
        and not skip_force_atom_post
        and has_success
        and not sched_step_done
        and answer_claims_job_published(ans)
    ):
        logger.warning("[CapabilityHook][hr_recruitment] via=%s missing scheduler confirmation after publish", via)
        messages.append({"role": "assistant", "content": response})
        messages.append({"role": "user", "content": build_scheduler_confirm_missing_prompt(branch_b=branch_b_context)})
        return True

    if answer_claims_unmanned_scheduler_running(ans) and not sched_step_done:
        logger.warning("[CapabilityHook][hr_recruitment] via=%s claimed scheduler running without scheduler tool", via)
        messages.append({"role": "assistant", "content": response})
        messages.append({"role": "user", "content": build_scheduler_running_without_tool_prompt()})
        return True

    return False
