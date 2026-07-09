"""HR recruitment capability final-answer guards."""

from __future__ import annotations

import logging
import re
from typing import Any

from l3_node.engine.hooks_pipeline import PipelineContext

logger = logging.getLogger(__name__)


def answer_claims_job_published(ans: str) -> bool:
    """Detect whether an answer claims a Boss job post has already succeeded."""

    a = ans or ""
    if "JOB_" in a:
        return True
    phrases = (
        "职位已发布",
        "职位发布成功",
        "已发布职位",
        "Boss 发布成功",
        "boss 发布成功",
        "已在 Boss 发布",
        "已在Boss 发布",
        "已成功发布职位",
        "职位发布完成",
        "发帖成功",
        "已成功发帖",
        "已在 Boss 上架",
        "职位已在 Boss 上架",
    )
    return any(p in a for p in phrases)


def answer_claims_unmanned_scheduler_running(ans: str) -> bool:
    """Detect whether the answer claims unattended recruitment scheduling is running."""

    a = ans or ""
    if re.search(r"调度状态\s*\*?\s*[|｜]\s*\*?\s*运行中", a, re.I):
        return True
    if re.search(r"无人值守[^\n]{0,48}(已启动|运行中|已开始)", a):
        return True
    if "收网任务已启动" in a or ("自动抓取简历" in a and "已启动" in a):
        return True
    if re.search(r"任务\s*ID\s*\|\s*[`'\"]?hr_recruit", a, re.I):
        return True
    return "✅" in a and "无人值守" in a and ("启动" in a or "运行" in a)


def recruitment_success_answer(ctx: Any, ans: str) -> bool:
    """Recruitment flow success answer, based on executed tools and answer text."""

    if answer_claims_job_published(ans):
        return True
    if "极速测试模式" in (ans or "") or "TASK_AUTO" in (ans or ""):
        return True
    tools = getattr(ctx, "_executed_tools_this_run", None) or []
    if "add_automated_recruitment_task" in tools and any(
        k in (ans or "")
        for k in ("无人值守", "收网", "抓简历", "调度", "已添加", "自动化招聘", "已启动")
    ):
        return True
    if "hr_scheduler_send_confirm_prompt" in tools and any(
        k in (ans or "")
        for k in ("飞书", "调度", "同意调度", "定时任务", "无人值守", "参数")
    ):
        return True
    return False


def build_job_published_without_tool_prompt() -> str:
    return (
        "【系统校验】你声称职位已发布，但未实际调用 mcp:atom_post_job_boss。"
        "请立即输出 Action: mcp:atom_post_job_boss，Action Input 为上一轮 JSON 配置单"
        "（从你之前的 Assistant 回复中提取），不得直接给出 Final Answer。"
    )


def build_scheduler_confirm_missing_prompt(*, branch_b: bool = False) -> str:
    suffix = "若 HR 明确要求跳过飞书立即开跑，可改 mcp:add_automated_recruitment_task。" if not branch_b else (
        "若 HR 明确要求跳过飞书、立即开跑，可改调用 mcp:add_automated_recruitment_task。"
    )
    return (
        "【系统校验】你已发布职位。请先输出 Action: mcp:hr_scheduler_send_confirm_prompt，"
        'Action Input 为 {"job_name": "<与 job_title 一致>"}'
        + ("，向飞书发送无人值守参数确认单（定时任务此时不启动）。" if branch_b else "。")
        + suffix
        + "不得直接给出 Final Answer。"
    )


def build_scheduler_running_without_tool_prompt() -> str:
    return (
        "【系统校验】你给出了含「调度已运行/无人值守已启动」的答复，但尚未执行 "
        "mcp:add_automated_recruitment_task 或 mcp:hr_scheduler_send_confirm_prompt。"
        "请立即输出 Action: mcp:add_automated_recruitment_task，Action Input 须包含 job_name、"
        "resume_collect_target、analyze_threshold（与 HR 要求一致）；"
        "若只是需要飞书确认而非真正启动，请调用 mcp:hr_scheduler_send_confirm_prompt。不得直接 Final Answer。"
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

    has_success = recruitment_success_answer(ctx, ans)
    no_post = "atom_post_job_boss" not in getattr(ctx, "_executed_tools_this_run", set())
    sched_step_done = (
        "add_automated_recruitment_task" in getattr(ctx, "_executed_tools_this_run", set())
        or "hr_scheduler_send_confirm_prompt" in getattr(ctx, "_executed_tools_this_run", set())
    )

    if (
        not branch_b_context
        and not skip_force_atom_post
        and has_success
        and no_post
        and answer_claims_job_published(ans)
    ):
        logger.warning(
            "[CapabilityHook][hr_recruitment] via=%s claimed job published without atom_post_job_boss",
            via,
        )
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
        logger.warning(
            "[CapabilityHook][hr_recruitment] via=%s missing scheduler confirmation after publish",
            via,
        )
        messages.append({"role": "assistant", "content": response})
        messages.append({"role": "user", "content": build_scheduler_confirm_missing_prompt(branch_b=branch_b_context)})
        return True

    if answer_claims_unmanned_scheduler_running(ans) and not sched_step_done:
        logger.warning(
            "[CapabilityHook][hr_recruitment] via=%s claimed scheduler running without recruitment scheduler tool",
            via,
        )
        messages.append({"role": "assistant", "content": response})
        messages.append({"role": "user", "content": build_scheduler_running_without_tool_prompt()})
        return True

    return False
