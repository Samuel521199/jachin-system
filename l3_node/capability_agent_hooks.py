"""Capability-owned hooks used by the core RoleExecutionAgent agent.

The core agent should not contain business-skill rules.  It calls these generic
hooks, and installed capability policy modules decide whether they need to act.
"""
from __future__ import annotations

from typing import Any

from l3_node.engine.hooks_pipeline import PipelineContext


def reset_capability_policy_metadata(ctx: PipelineContext) -> None:
    from l3_node.pmo_agent_policy import reset_pmo_policy_metadata

    reset_pmo_policy_metadata(ctx)


def capture_capability_debug_thought(ctx: PipelineContext, thought: str) -> None:
    from l3_node.pmo_agent_policy import capture_pmo_debug_thought

    capture_pmo_debug_thought(ctx, thought)


def append_capability_debug_action(
    ctx: PipelineContext,
    *,
    tool: str,
    inp: str,
    iteration: int,
) -> None:
    from l3_node.pmo_agent_policy import append_pmo_debug_action

    append_pmo_debug_action(ctx, tool=tool, inp=inp, iteration=iteration)


def append_capability_debug_observation(
    ctx: PipelineContext,
    *,
    tool: str,
    observation_full: str,
    iteration: int,
) -> None:
    from l3_node.pmo_agent_policy import append_pmo_debug_observation

    append_pmo_debug_observation(
        ctx,
        tool=tool,
        observation_full=observation_full,
        iteration=iteration,
    )


def capability_publisher_tool_lock_enabled(implicit_attribution: Any) -> bool:
    from l3_node.pmo_agent_policy import pmo_publisher_tool_lock_enabled

    return pmo_publisher_tool_lock_enabled(implicit_attribution)


def apply_capability_metadata_seed(metadata: dict[str, Any], implicit_attribution: Any) -> None:
    from l3_node.pmo_agent_policy import apply_pmo_metadata_seed

    apply_pmo_metadata_seed(metadata, implicit_attribution)


def reject_capability_final_answer_guards(
    ctx: PipelineContext,
    messages: list[dict[str, Any]],
    response: str,
    ans: str,
    *,
    via: str,
) -> bool:
    from l3_node.pmo_agent_policy import reject_pmo_final_answer_guards

    return reject_pmo_final_answer_guards(ctx, messages, response, ans, via=via)


def reject_workspace_writeback_guard(
    ctx: PipelineContext,
    messages: list[dict[str, Any]],
    response: str,
    ans: str,
    *,
    via: str,
) -> bool:
    from l3_node.capability_policies.workspace_writeback import reject_workspace_writeback_missing_guard

    return reject_workspace_writeback_missing_guard(ctx, messages, response, ans, via=via)


def reject_sqlite_grounding_guard(
    ctx: PipelineContext,
    messages: list[dict[str, Any]],
    response: str,
    ans: str,
    *,
    via: str,
) -> bool:
    from l3_node.capability_policies.sqlite_grounding import reject_ungrounded_sqlite_final_answer

    return reject_ungrounded_sqlite_final_answer(ctx, messages, response, ans, via=via)


def reject_hr_recruitment_guard(
    ctx: PipelineContext,
    messages: list[dict[str, Any]],
    response: str,
    ans: str,
    *,
    branch_b_context: bool,
    skip_force_atom_post: bool,
    via: str,
) -> bool:
    from l3_node.capability_policies.hr_recruitment import reject_hr_final_answer_guards

    return reject_hr_final_answer_guards(
        ctx,
        messages,
        response,
        ans,
        branch_b_context=branch_b_context,
        skip_force_atom_post=skip_force_atom_post,
        via=via,
    )


def before_capability_tool_exec(
    ctx: PipelineContext,
    *,
    tool: str,
    inp: str,
    response: str,
) -> tuple[str, str | None, bool, bool]:
    from l3_node.pmo_agent_policy import before_pmo_tool_exec

    return before_pmo_tool_exec(ctx, tool=tool, inp=inp, response=response)


async def maybe_reject_sqlite_work_order_before_tool_exec(
    ctx: PipelineContext,
    *,
    tool: str,
    inp: str,
    response: str,
    messages: list[dict[str, Any]],
    observation_excerpt_fn: Any,
    followup_user_text_fn: Any,
) -> bool:
    from l3_node.capability_policies.sqlite_work_order_critic import maybe_reject_sqlite_work_order

    return await maybe_reject_sqlite_work_order(
        ctx=ctx,
        tool=tool,
        inp=inp,
        response=response,
        messages=messages,
        observation_excerpt_fn=observation_excerpt_fn,
        followup_user_text_fn=followup_user_text_fn,
    )


def mark_workspace_io_capability_flags(ctx: PipelineContext, tool: str, observation_full: str) -> None:
    from l3_node.capability_policies.workspace_writeback import mark_workspace_io_flags

    mark_workspace_io_flags(ctx, tool, observation_full)


def mark_sqlite_experience_capability_gate(ctx: PipelineContext, tool: str) -> None:
    from l3_node.capability_policies.sqlite_work_order_critic import mark_sqlite_experience_save_gate

    mark_sqlite_experience_save_gate(ctx, tool)


def after_capability_tool_exec(
    ctx: PipelineContext,
    *,
    tool: str,
    inp: str,
    response: str,
    observation_full: str,
    iteration: int,
    max_iterations: int,
) -> str:
    from l3_node.pmo_agent_policy import after_pmo_tool_exec

    return after_pmo_tool_exec(
        ctx,
        tool=tool,
        inp=inp,
        response=response,
        observation_full=observation_full,
        iteration=iteration,
        max_iterations=max_iterations,
    )


def capability_observation_nudge(ctx: PipelineContext, observation_full: str, tool: str) -> str:
    from l3_node.pmo_agent_policy import pmo_observation_nudge

    return pmo_observation_nudge(ctx, observation_full, tool)
