"""Direct execution bridge for Arbiter-issued low-risk WorkOrders.

This module is the narrow mainline bridge between ``run_agent`` and the
Memory-first Cognitive Kernel. ReviewBoard and Arbiter plan first, then
WorkOrder Dispatcher executes through the right Role Agent.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
from typing import Any, Callable, Optional

from .closure_memory import execute_turn_closure_memory_writes
from .contracts import DecisionContract, WorkOrder
from .dispatcher import dispatch_existing_work_order
from .kernel_loop import KernelPlanningResult
from .pending_confirmation import (
    cancel_pending_confirmation,
    clear_pending_confirmation,
    is_cancellation_text,
    is_confirmation_text,
    load_pending_confirmation,
    mark_pending_as_confirmed,
    save_pending_confirmation,
)
from .runtime import close_turn, close_turn_waiting_user

logger = logging.getLogger(__name__)

RunToolFunc = Callable[[str, str, Optional[list[str]]], Any]
KernelStatusCallback = Callable[[str, dict[str, Any]], None]


def direct_mainline_enabled() -> bool:
    disabled = (
        os.environ.get("JACHIN_DISABLE_COGNITIVE_DIRECT_MAINLINE", "")
        or os.environ.get("JACHIN_DISABLE_COGNITIVE_APPCONTROL_DIRECT", "")
    )
    return disabled.strip().lower() not in {"1", "true", "yes", "on"}


async def try_execute_cognitive_direct_plan(
    *,
    plan: KernelPlanningResult | None,
    tools: list[dict[str, Any]],
    allowed_skills: Optional[list[str]],
    run_tool_func: RunToolFunc,
    user_input: str = "",
    session_id: str = "",
    channel: str = "",
    status_callback: KernelStatusCallback | None = None,
) -> str | None:
    """Execute the first low-risk WorkOrder directly through Role Agents.

    Returns a user-facing reply when the direct path handled the turn; returns
    ``None`` when the caller should close through Kernel-only fallback instead
    of returning to a text Action/Verification evidence loop.
    """

    if not direct_mainline_enabled():
        _log_direct_execution(stage="skipped", reason="direct_mainline_disabled")
        return None

    if is_cancellation_text(user_input):
        cancelled = cancel_pending_confirmation(session_id=session_id, channel=channel)
        if cancelled is not None:
            final_text = "\u5df2\u53d6\u6d88\u4e0a\u4e00\u4e2a\u5f85\u786e\u8ba4\u4efb\u52a1\uff0c\u4e0d\u4f1a\u6267\u884c\u3002"
            close_turn(
                turn_id=cancelled.contract.turn_id,
                final_text=final_text,
                executed_work_orders=[],
                verification_reports=[],
                aborted=True,
            )
            return final_text
        return None

    if is_confirmation_text(user_input):
        resumed = await _try_resume_pending_confirmation(
            plan=plan,
            tools=tools,
            allowed_skills=allowed_skills,
            run_tool_func=run_tool_func,
            session_id=session_id,
            channel=channel,
        )
        if resumed is not None:
            return resumed
        return None

    if plan is None:
        _log_direct_execution(stage="skipped", reason="no_cognitive_plan")
        return None
    contract = plan.decision_contract
    if not plan.work_orders:
        _log_direct_execution(stage="skipped", contract=contract, reason="no_work_order")
        return None
    work_order = plan.work_orders[0]
    if not contract.execution_allowed and contract.tool_policy.requires_confirmation:
        save_pending_confirmation(
            contract=contract,
            work_order=work_order,
            session_id=session_id,
            channel=channel,
        )
        question = (
            contract.clarification_question
            or contract.tool_policy.confirmation_reason
            or "\u8fd9\u4e2a\u64cd\u4f5c\u9700\u8981\u786e\u8ba4\uff0c\u8bf7\u56de\u590d\u201c\u786e\u8ba4\u6267\u884c\u201d\u3002"
        )
        question = _with_pending_confirmation_ui_protocol(
            question,
            contract=contract,
            work_order=work_order,
        )
        closure = close_turn_waiting_user(
            turn_id=contract.turn_id,
            final_text=question,
            pending_decision={
                "decision_id": contract.decision_id,
                "work_order_id": work_order.work_order_id,
                "task_type": contract.task_type,
                "risk_level": contract.risk_level.value,
                "requires_confirmation": True,
            },
        )
        _log_direct_execution(
            stage="waiting_user_confirmation",
            contract=contract,
            work_order=work_order,
            tool_id=str(work_order.inputs.get("tool") or ""),
            closure=closure,
            final_text=question,
            reason="DecisionContract requires confirmation",
        )
        logger.info(
            "[CognitiveKernel] direct mainline saved pending confirmation turn=%s task=%s work_order=%s",
            contract.turn_id[:12],
            contract.task_type,
            work_order.work_order_id,
        )
        _emit_status(
            status_callback,
            "waiting_user_confirmation",
            task_type=contract.task_type,
            risk_level=contract.risk_level.value,
            work_order_id=work_order.work_order_id,
            tool_id=str(work_order.inputs.get("tool") or ""),
        )
        return question
    if not contract.execution_allowed:
        _log_direct_execution(
            stage="skipped",
            contract=contract,
            work_order=work_order,
            tool_id=str(work_order.inputs.get("tool") or ""),
            reason="execution_not_allowed",
        )
        return None
    tool_id = str(
        work_order.inputs.get("tool")
        or (contract.tool_policy.allowed_tools[0] if contract.tool_policy.allowed_tools else "")
    ).strip()
    if not _planned_direct_tool_allowed(plan, tool_id):
        _log_direct_execution(
            stage="skipped",
            contract=contract,
            work_order=work_order,
            tool_id=tool_id,
            reason="planned_direct_tool_not_allowed",
        )
        return None
    if not _planned_direct_tool_available(tool_id, tools):
        logger.info("[CognitiveKernel] direct mainline skipped: tool unavailable tool=%s", tool_id)
        _log_direct_execution(
            stage="skipped",
            contract=contract,
            work_order=work_order,
            tool_id=tool_id,
            reason="planned_direct_tool_unavailable",
        )
        return None

    _log_role_agent_prompt(
        contract=contract,
        work_order=work_order,
        tool_id=tool_id,
        allowed_skills=allowed_skills,
        available_tools=tools,
        stage="direct_mainline_dispatch",
    )
    _emit_status(
        status_callback,
        "role_execution_started",
        task_type=contract.task_type,
        role_agent=work_order.role_agent,
        work_order_id=work_order.work_order_id,
        tool_id=tool_id,
    )
    result = await _execute_work_order(
        contract=contract,
        work_order=work_order,
        tool_id=tool_id,
        allowed_skills=allowed_skills,
        run_tool_func=run_tool_func,
    )
    _emit_status(
        status_callback,
        "verification_finished",
        task_type=contract.task_type,
        role_agent=work_order.role_agent,
        work_order_id=work_order.work_order_id,
        tool_id=tool_id,
        ok=bool(result.verification.ok),
        verification_status="passed" if result.verification.ok else "failed",
    )
    final_text = _planned_direct_reply(plan, bool(result.verification.ok), result.observation)
    closure = close_turn(
        turn_id=contract.turn_id,
        final_text=final_text,
        executed_work_orders=[work_order.work_order_id],
        verification_reports=[result.verification],
        aborted=not bool(result.verification.ok),
    )
    _log_direct_execution(
        stage="executed",
        contract=contract,
        work_order=work_order,
        tool_id=tool_id,
        dispatch_result=result,
        closure=closure,
        final_text=final_text,
    )
    await execute_turn_closure_memory_writes(closure)
    _emit_status(
        status_callback,
        "turn_closure_finished",
        task_type=contract.task_type,
        role_agent=work_order.role_agent,
        work_order_id=work_order.work_order_id,
        tool_id=tool_id,
        ok=bool(result.verification.ok),
        verification_status=closure.verification_status,
        closure_type=closure.closure_type.value,
    )
    logger.info(
        "[CognitiveKernel] direct mainline executed turn=%s task=%s tool=%s ok=%s work_order=%s",
        contract.turn_id[:12],
        contract.task_type,
        tool_id,
        result.verification.ok,
        work_order.work_order_id,
    )
    return final_text


def _emit_status(callback: KernelStatusCallback | None, stage: str, **payload: Any) -> None:
    if callback is None:
        return
    try:
        callback(stage, payload)
    except Exception:
        logger.debug("[CognitiveKernel] status callback failed stage=%s", stage, exc_info=True)


def _log_direct_execution(
    *,
    stage: str,
    contract: DecisionContract | None = None,
    work_order: WorkOrder | None = None,
    tool_id: str = "",
    dispatch_result: Any = None,
    closure: Any = None,
    final_text: str = "",
    reason: str = "",
) -> None:
    try:
        from l3_node.terminal_turn_debug_log import log_cognitive_direct_execution

        log_cognitive_direct_execution(
            stage=stage,
            contract=contract,
            work_order=work_order,
            tool_id=tool_id,
            dispatch_result=dispatch_result,
            closure=closure,
            final_text=final_text,
            reason=reason,
        )
    except Exception:
        pass


def _with_pending_confirmation_ui_protocol(
    text: str,
    *,
    contract: DecisionContract,
    work_order: WorkOrder,
) -> str:
    """Attach a hidden desktop UI protocol block for confirm/cancel buttons."""

    payload = {
        "type": "pending_confirmation",
        "decision_id": contract.decision_id,
        "work_order_id": work_order.work_order_id,
        "task_type": contract.task_type,
        "risk_level": contract.risk_level.value,
        "tool": work_order.inputs.get("tool"),
        "confirm_text": "\u786e\u8ba4\u6267\u884c",
        "cancel_text": "\u53d6\u6d88",
    }
    marker = f"<!-- jachin-ui:pending-confirmation {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))} -->"
    return f"{text.rstrip()}\n\n{marker}"


async def _try_resume_pending_confirmation(
    *,
    plan: KernelPlanningResult | None,
    tools: list[dict[str, Any]],
    allowed_skills: Optional[list[str]],
    run_tool_func: RunToolFunc,
    session_id: str = "",
    channel: str = "",
) -> str | None:
    pending = load_pending_confirmation(session_id=session_id, channel=channel)
    if pending is None:
        return None
    mark_pending_as_confirmed(
        pending,
        confirmation_turn_id=plan.decision_contract.turn_id if plan is not None else "",
    )
    contract = pending.contract
    work_order = pending.work_order
    tool_id = str(
        work_order.inputs.get("tool")
        or (contract.tool_policy.allowed_tools[0] if contract.tool_policy.allowed_tools else "")
    ).strip()
    if not _confirmed_direct_tool_allowed(contract, tool_id):
        final_text = "\u5df2\u6536\u5230\u786e\u8ba4\uff0c\u4f46\u8be5\u64cd\u4f5c\u6682\u672a\u63a5\u5165\u76f4\u63a5\u6267\u884c\u901a\u9053\uff0c\u8bf7\u91cd\u65b0\u53d1\u8d77\u4efb\u52a1\u3002"
        clear_pending_confirmation(session_id=session_id, channel=channel)
        closure = close_turn(
            turn_id=contract.turn_id,
            final_text=final_text,
            executed_work_orders=[],
            verification_reports=[],
            aborted=True,
        )
        await execute_turn_closure_memory_writes(closure)
        return final_text
    if not _planned_direct_tool_available(tool_id, tools):
        final_text = f"\u5df2\u6536\u5230\u786e\u8ba4\uff0c\u4f46\u5f53\u524d\u5de5\u5177\u4e0d\u53ef\u7528\uff1a{tool_id}\u3002"
        closure = close_turn(
            turn_id=contract.turn_id,
            final_text=final_text,
            executed_work_orders=[],
            verification_reports=[],
            aborted=True,
        )
        await execute_turn_closure_memory_writes(closure)
        return final_text

    _log_role_agent_prompt(
        contract=contract,
        work_order=work_order,
        tool_id=tool_id,
        allowed_skills=allowed_skills,
        available_tools=tools,
        stage="resume_pending_dispatch",
    )
    result = await _execute_work_order(
        contract=contract,
        work_order=work_order,
        tool_id=tool_id,
        allowed_skills=allowed_skills,
        run_tool_func=run_tool_func,
    )
    if result.verification.ok:
        clear_pending_confirmation(session_id=session_id, channel=channel)
    final_text = _direct_reply_from_contract(contract, work_order, bool(result.verification.ok), result.observation)
    closure = close_turn(
        turn_id=contract.turn_id,
        final_text=final_text,
        executed_work_orders=[work_order.work_order_id],
        verification_reports=[result.verification],
        aborted=not bool(result.verification.ok),
    )
    _log_direct_execution(
        stage="resumed_pending_executed",
        contract=contract,
        work_order=work_order,
        tool_id=tool_id,
        dispatch_result=result,
        closure=closure,
        final_text=final_text,
    )
    await execute_turn_closure_memory_writes(closure)
    logger.info(
        "[CognitiveKernel] direct mainline resumed pending turn=%s task=%s tool=%s ok=%s work_order=%s",
        contract.turn_id[:12],
        contract.task_type,
        tool_id,
        result.verification.ok,
        work_order.work_order_id,
    )
    return final_text


def _log_role_agent_prompt(
    *,
    contract: DecisionContract,
    work_order: WorkOrder,
    tool_id: str,
    allowed_skills: Optional[list[str]],
    available_tools: list[dict[str, Any]],
    stage: str,
) -> None:
    try:
        from l3_node.terminal_turn_debug_log import log_role_agent_work_order_prompt

        log_role_agent_work_order_prompt(
            contract=contract,
            work_order=work_order,
            tool_id=tool_id,
            allowed_skills=allowed_skills or [],
            available_tools=available_tools,
            stage=stage,
        )
    except Exception:
        pass


async def _execute_work_order(
    *,
    contract: DecisionContract,
    work_order: WorkOrder,
    tool_id: str,
    allowed_skills: Optional[list[str]],
    run_tool_func: RunToolFunc,
):
    async def _mainline_executor(_work_order):
        work_order_input = str(_work_order.inputs.get("work_order_input") or "")
        if not work_order_input:
            from .dispatcher import _work_order_input_from_work_order

            work_order_input = _work_order_input_from_work_order(_work_order)
        return await _call_tool_runner(run_tool_func, tool_id, work_order_input, allowed_skills)

    return await dispatch_existing_work_order(
        contract=contract,
        work_order=work_order,
        executor=_mainline_executor,
    )


async def _call_tool_runner(
    run_tool_func: RunToolFunc,
    tool_id: str,
    work_order_input: str,
    allowed_skills: Optional[list[str]],
) -> str:
    if inspect.iscoroutinefunction(run_tool_func):
        result = await run_tool_func(tool_id, work_order_input, allowed_skills)
    else:
        result = await asyncio.to_thread(run_tool_func, tool_id, work_order_input, allowed_skills)
    if inspect.isawaitable(result):
        result = await result
    return str(result or "")


def _planned_direct_tool_available(tool_id: str, tools: list[dict[str, Any]]) -> bool:
    wanted = (tool_id or "").strip().lower()
    return bool(wanted) and any(str(t.get("id") or "").strip().lower() == wanted for t in tools if isinstance(t, dict))


def _planned_direct_tool_allowed(plan: KernelPlanningResult, tool_id: str) -> bool:
    contract = plan.decision_contract
    if not contract.execution_allowed or not plan.work_orders:
        return False
    if contract.risk_level.value == "critical":
        return False
    if contract.task_type == "app_control":
        return contract.risk_level.value == "low" and tool_id in {
            "mcp:windows_open_app",
            "mcp:windows_window_switch",
            "mcp:windows_window_close",
        }
    if contract.task_type == "message_delivery":
        return tool_id in {"mcp:windows_lark_send_message", "util:lark_send_text", "mcp:lark_send_text"}
    if contract.task_type == "file_operation":
        return contract.risk_level.value in {"low", "medium", "high"} and tool_id in {
            "core:fs_read",
            "core:fs_write",
            "mcp:windows_file_open",
            "mcp:windows_file_reveal_in_explorer",
        }
    return False


def _confirmed_direct_tool_allowed(contract: DecisionContract, tool_id: str) -> bool:
    if contract.risk_level.value == "critical":
        return False
    if contract.task_type == "app_control":
        return contract.risk_level.value in {"low", "medium", "high"} and tool_id in {
            "mcp:windows_open_app",
            "mcp:windows_window_switch",
            "mcp:windows_window_close",
        }
    if contract.task_type == "file_operation":
        return contract.risk_level.value == "low" and tool_id in {
            "core:fs_read",
            "mcp:windows_file_open",
            "mcp:windows_file_reveal_in_explorer",
        }
    if contract.task_type == "message_delivery":
        return contract.risk_level.value in {"low", "medium", "high"} and tool_id in {
            "mcp:windows_lark_send_message",
            "util:lark_send_text",
            "mcp:lark_send_text",
        }
    return False


def _direct_reply_from_contract(contract: DecisionContract, work_order: WorkOrder, ok: bool, observation: str) -> str:
    pseudo_plan = type(
        "_DirectReplyPlan",
        (),
        {
            "decision_contract": contract,
            "review_summary": type(
                "_DirectReplySummary",
                (),
                {
                    "top_intent": str(work_order.inputs.get("intent") or ""),
                    "target": work_order.inputs.get("target") or {},
                },
            )(),
        },
    )()
    return _planned_direct_reply(pseudo_plan, ok, observation)


def _planned_direct_reply(plan: KernelPlanningResult, ok: bool, observation: str) -> str:
    intent = plan.review_summary.top_intent
    task_type = plan.decision_contract.task_type
    target_obj = plan.review_summary.target or {}
    target = str(target_obj.get("name") or target_obj.get("path") or "\u76ee\u6807").strip() or "\u76ee\u6807"
    if ok:
        if intent == "open_app":
            return f"\u5df2\u6253\u5f00 {target}\u3002"
        if intent == "close_app":
            return f"\u5df2\u5173\u95ed {target}\u3002"
        if intent == "switch_app":
            return f"\u5df2\u5207\u6362\u5230 {target}\u3002"
        if task_type == "message_delivery":
            recipients = target_obj.get("recipients") if isinstance(target_obj.get("recipients"), list) else []
            names = "\u3001".join(str(x) for x in recipients if str(x).strip()) or "\u76ee\u6807\u4f1a\u8bdd"
            return f"\u5df2\u53d1\u9001\u6d88\u606f\u7ed9 {names}\u3002"
        if task_type == "file_operation":
            return f"\u5df2\u5b8c\u6210\u6587\u4ef6\u64cd\u4f5c\uff1a{target}\u3002"
        return "\u5df2\u5b8c\u6210\u3002"
    preview = str(observation or "").strip()[:300]
    if preview:
        return f"{target} \u7684\u4efb\u52a1\u6ca1\u6709\u901a\u8fc7\u9a8c\u8bc1\u3002{preview}"
    return f"{target} \u7684\u4efb\u52a1\u6ca1\u6709\u901a\u8fc7\u9a8c\u8bc1\u3002"
