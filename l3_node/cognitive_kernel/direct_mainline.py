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
import re
from typing import Any, Callable, Optional

from .closure_memory import execute_turn_closure_memory_writes
from .contracts import DecisionContract, WorkOrder
from .dispatcher import dispatch_existing_work_order
from .entity_corrections import (
    record_confirmed_entity_correction_from_input_context,
    record_confirmed_entity_correction_from_work_order,
    record_entity_correction_usage_from_work_order,
)
from .kernel_loop import KernelPlanningResult
from .pending_confirmation import (
    cancel_pending_confirmation,
    clear_pending_confirmation_by_key,
    is_cancellation_text,
    is_confirmation_text,
    load_pending_confirmation,
    mark_pending_as_confirmed,
    save_pending_confirmation,
)
from .runtime import close_turn, close_turn_waiting_user
from .task_session_manager import attach_task_session_ui_protocol
from .task_memory import build_task_experience_memory_requests
from l3_node.voice_entity_correction import correct_voice_entities, teach_alias

logger = logging.getLogger(__name__)

RunToolFunc = Callable[[str, str, Optional[list[str]]], Any]


def direct_mainline_enabled() -> bool:
    disabled = (
        os.environ.get("JACHIN_DISABLE_COGNITIVE_DIRECT_MAINLINE", "")
        or os.environ.get("JACHIN_DISABLE_COGNITIVE_APPCONTROL_DIRECT", "")
    )
    return disabled.strip().lower() not in {"1", "true", "yes", "on"}


def pending_slot_reply_available(*, user_input: str, session_id: str = "", channel: str = "") -> bool:
    """Return true when a short reply should be routed to an existing pending turn.

    Always-on voice may classify very short replies such as "A." or "1." as
    noise when viewed in isolation. Pending slot replies are not isolated turns:
    they are answers to the previous question, so they must bypass the voice
    false-trigger guard and reach ``_try_resume_pending_with_slot_reply``.
    """

    pending = load_pending_confirmation(session_id=session_id, channel=channel)
    if pending is None:
        return False
    text = str(user_input or "").strip()
    if not text:
        return False
    if is_cancellation_text(text) or is_confirmation_text(text):
        return True
    if _resolve_pending_message_slot_reply(text, pending.work_order):
        return True
    if _resolve_pending_app_slot_reply(text, pending.work_order):
        return True
    return False


async def try_execute_cognitive_direct_plan(
    *,
    plan: KernelPlanningResult | None,
    tools: list[dict[str, Any]],
    allowed_skills: Optional[list[str]],
    run_tool_func: RunToolFunc,
    user_input: str = "",
    session_id: str = "",
    channel: str = "",
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

    slot_resumed = await _try_resume_pending_with_slot_reply(
        user_input=user_input,
        plan=plan,
        tools=tools,
        allowed_skills=allowed_skills,
        run_tool_func=run_tool_func,
        session_id=session_id,
        channel=channel,
    )
    if slot_resumed is not None:
        return slot_resumed

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
    missing_message_work_order, missing_message_tool, missing_message_slot = _first_message_send_slot_gap(
        plan.work_orders,
        contract=contract,
    )
    if missing_message_work_order is not None and missing_message_slot:
        save_pending_confirmation(
            contract=contract,
            work_order=missing_message_work_order,
            session_id=session_id,
            channel=channel,
        )
        final_text = _message_send_slot_gap_reply(
            missing_message_slot,
            contract=contract,
            work_order=missing_message_work_order,
        )
        closure = close_turn_waiting_user(
            turn_id=contract.turn_id,
            final_text=final_text,
            pending_decision={
                "decision_id": contract.decision_id,
                "work_order_id": missing_message_work_order.work_order_id,
                "task_type": contract.task_type,
                "missing_slot": missing_message_slot,
                "tool": missing_message_tool,
            },
        )
        _log_direct_execution(
            stage="waiting_user_message_slot",
            contract=contract,
            work_order=missing_message_work_order,
            tool_id=missing_message_tool,
            closure=closure,
            final_text=final_text,
            reason=f"missing_message_slot_before_confirmation:{missing_message_slot}",
        )
        return final_text
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
        question = _with_execution_trace_ui(
            question,
            status="waiting_user",
            contract=contract,
            work_order=work_order,
            tool_id=str(work_order.inputs.get("tool") or ""),
            ok=False,
            reason="DecisionContract requires confirmation",
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
    results = []
    upstream_observations: list[dict[str, Any]] = []
    for work_order in plan.work_orders:
        _inject_upstream_into_work_order(work_order, upstream_observations)
        tool_id = str(
            work_order.inputs.get("tool")
            or (work_order.tool_policy.allowed_tools[0] if work_order.tool_policy.allowed_tools else "")
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

        slot_gap = _message_send_slot_gap(work_order, tool_id)
        if slot_gap:
            save_pending_confirmation(
                contract=contract,
                work_order=work_order,
                session_id=session_id,
                channel=channel,
            )
            final_text = _message_send_slot_gap_reply(
                slot_gap,
                contract=contract,
                work_order=work_order,
            )
            closure = close_turn_waiting_user(
                turn_id=contract.turn_id,
                final_text=final_text,
                pending_decision={
                    "decision_id": contract.decision_id,
                    "work_order_id": work_order.work_order_id,
                    "task_type": contract.task_type,
                    "missing_slot": slot_gap,
                    "tool": tool_id,
                },
            )
            _log_direct_execution(
                stage="waiting_user_message_slot",
                contract=contract,
                work_order=work_order,
                tool_id=tool_id,
                closure=closure,
                final_text=final_text,
                reason=f"missing_message_slot:{slot_gap}",
            )
            return final_text

        _log_role_agent_prompt(
            contract=contract,
            work_order=work_order,
            tool_id=tool_id,
            allowed_skills=allowed_skills,
            available_tools=tools,
            stage="direct_mainline_dispatch",
        )
        result = await _execute_work_order(
            contract=contract,
            work_order=work_order,
            tool_id=tool_id,
            allowed_skills=allowed_skills,
            run_tool_func=run_tool_func,
        )
        results.append((work_order, tool_id, result))
        upstream_observations.append(
            {
                "work_order_id": work_order.work_order_id,
                "tool": tool_id,
                "role_agent": work_order.role_agent,
                "ok": bool(result.verification.ok),
                "observation": str(result.observation or ""),
            }
        )
        record_entity_correction_usage_from_work_order(
            work_order=work_order,
            turn_id=contract.turn_id,
            ok=bool(result.verification.ok),
            failure_reason=str(getattr(result.verification, "failure_reason", "") or ""),
        )
        if not bool(result.verification.ok):
            break
    if not results:
        return None
    final_work_order, final_tool_id, final_result = results[-1]
    reply_observation = _observation_with_verification_reason(final_result.observation, final_result.verification)
    all_ok = all(bool(item[2].verification.ok) for item in results)
    final_text = _planned_direct_reply(plan, all_ok, reply_observation)
    executed_work_orders = [item[0] for item in results]
    verification_reports = [item[2].verification for item in results]
    task_memory_requests = build_task_experience_memory_requests(
        contract=contract,
        work_orders=executed_work_orders,
        verification_reports=verification_reports,
        final_text=final_text,
    )
    closure = close_turn(
        turn_id=contract.turn_id,
        final_text=final_text,
        executed_work_orders=[item.work_order_id for item in executed_work_orders],
        verification_reports=verification_reports,
        aborted=not all_ok,
        memory_context_refs=contract.memory_context_refs,
        extra_memory_write_requests=task_memory_requests,
    )
    _log_direct_execution(
        stage="executed",
        contract=contract,
        work_order=final_work_order,
        tool_id=final_tool_id,
        dispatch_result=final_result,
        closure=closure,
        final_text=final_text,
    )
    await execute_turn_closure_memory_writes(closure)
    logger.info(
        "[CognitiveKernel] direct mainline executed turn=%s task=%s tool=%s ok=%s work_order=%s",
        contract.turn_id[:12],
        contract.task_type,
        final_tool_id,
        all_ok,
        final_work_order.work_order_id,
    )
    return _with_execution_trace_ui(
        final_text,
        status="done" if all_ok else "failed",
        contract=contract,
        work_order=final_work_order,
        tool_id=final_tool_id,
        ok=all_ok,
    )


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


def _observation_with_verification_reason(observation: str, verification: Any) -> str:
    text = str(observation or "")
    reason = str(getattr(verification, "failure_reason", "") or "").strip()
    if not reason or reason in text:
        return text
    return f"{text}\nverification_failure={reason}" if text.strip() else f"verification_failure={reason}"


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


def _with_execution_trace_ui(
    text: str,
    *,
    status: str,
    contract: DecisionContract | None,
    work_order: WorkOrder | None,
    tool_id: str = "",
    ok: bool = False,
    reason: str = "",
) -> str:
    steps: list[dict[str, Any]] = [
        {"label": "Goal Interpreter", "status": "done", "detail": "识别目标、约束和缺失槽位"},
        {"label": "ReviewBoard / Arbiter", "status": "done", "detail": "选择任务类型、风险等级和执行角色"},
        {
            "label": "WorkOrder",
            "status": "done" if work_order is not None else "pending",
            "detail": str(getattr(work_order, "work_order_id", "") or "生成结构化执行单"),
        },
        {
            "label": str(getattr(work_order, "role_agent", "") or "RoleExecutor"),
            "status": "pending" if status == "waiting_user" else ("done" if ok else status),
            "detail": str(tool_id or (getattr(work_order, "inputs", {}) or {}).get("tool") or ""),
        },
        {
            "label": "Verification",
            "status": "pending" if status == "waiting_user" else ("done" if ok else "failed"),
            "detail": "等待补槽" if status == "waiting_user" else ("已通过验证" if ok else "未通过验证"),
        },
    ]
    return attach_task_session_ui_protocol(
        text,
        status=status,
        contract=contract,
        work_order=work_order,
        current_step="等待用户补充信息" if status == "waiting_user" else ("执行完成" if ok else "执行未通过"),
        decision_basis=[
            f"task_type={getattr(contract, 'task_type', '')}" if contract is not None else "task_type=unknown",
            f"role_agent={getattr(work_order, 'role_agent', '')}" if work_order is not None else "role_agent=unknown",
            f"tool={tool_id or ((getattr(work_order, 'inputs', {}) or {}).get('tool') if work_order is not None else '')}",
            f"reason={reason}" if reason else ("verification=ok" if ok else "verification=pending"),
        ],
        steps=steps,
        evidence={
            "ok": ok,
            "reason": reason,
            "tool": tool_id,
            "work_order_id": getattr(work_order, "work_order_id", "") if work_order is not None else "",
        },
    )


async def _try_resume_pending_with_slot_reply(
    *,
    user_input: str,
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
    message_slot_patch = _resolve_pending_message_slot_reply(user_input, pending.work_order)
    if message_slot_patch:
        _fill_pending_message_slot(pending.work_order, patch=message_slot_patch, heard_as=user_input)
        save_pending_confirmation(
            contract=pending.contract,
            work_order=pending.work_order,
            session_id=pending.session_key,
            channel="",
        )
        append_pending_slot_event = {
            "session_key": pending.session_key,
            "decision_id": pending.contract.decision_id,
            "work_order_id": pending.work_order.work_order_id,
            "slots": sorted(message_slot_patch.keys()),
            "values": message_slot_patch,
            "heard_as": str(user_input or "")[:120],
        }
        try:
            from .ledger import append_event

            append_event("confirmation_message_slot_filled", pending.contract.turn_id, append_pending_slot_event)
        except Exception:
            pass
        mark_pending_as_confirmed(
            pending,
            confirmation_turn_id=plan.decision_contract.turn_id if plan is not None else "",
        )
        return await _execute_confirmed_pending(
            pending=pending,
            plan=plan,
            tools=tools,
            allowed_skills=allowed_skills,
            run_tool_func=run_tool_func,
        )
    app_name = _resolve_pending_app_slot_reply(user_input, pending.work_order)
    if not app_name:
        return None
    _fill_pending_app_slot(pending.work_order, app_name=app_name, heard_as=user_input)
    mark_pending_as_confirmed(
        pending,
        confirmation_turn_id=plan.decision_contract.turn_id if plan is not None else "",
    )
    append_pending_slot_event = {
        "session_key": pending.session_key,
        "decision_id": pending.contract.decision_id,
        "work_order_id": pending.work_order.work_order_id,
        "slot": "app",
        "value": app_name,
        "heard_as": str(user_input or "")[:120],
    }
    try:
        from .ledger import append_event

        append_event("confirmation_slot_filled", pending.contract.turn_id, append_pending_slot_event)
    except Exception:
        pass
    return await _execute_confirmed_pending(
        pending=pending,
        plan=plan,
        tools=tools,
        allowed_skills=allowed_skills,
        run_tool_func=run_tool_func,
    )


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
    return await _execute_confirmed_pending(
        pending=pending,
        plan=plan,
        tools=tools,
        allowed_skills=allowed_skills,
        run_tool_func=run_tool_func,
    )


async def _execute_confirmed_pending(
    *,
    pending: Any,
    plan: KernelPlanningResult | None,
    tools: list[dict[str, Any]],
    allowed_skills: Optional[list[str]],
    run_tool_func: RunToolFunc,
) -> str | None:
    contract = pending.contract
    work_order = pending.work_order
    tool_id = str(
        work_order.inputs.get("tool")
        or (contract.tool_policy.allowed_tools[0] if contract.tool_policy.allowed_tools else "")
    ).strip()
    if not _confirmed_direct_tool_allowed(contract, tool_id):
        final_text = "\u5df2\u6536\u5230\u786e\u8ba4\uff0c\u4f46\u8be5\u64cd\u4f5c\u6682\u672a\u63a5\u5165\u76f4\u63a5\u6267\u884c\u901a\u9053\uff0c\u8bf7\u91cd\u65b0\u53d1\u8d77\u4efb\u52a1\u3002"
        clear_pending_confirmation_by_key(pending.session_key)
        closure = close_turn(
            turn_id=contract.turn_id,
            final_text=final_text,
            executed_work_orders=[],
            verification_reports=[],
            aborted=True,
            memory_context_refs=contract.memory_context_refs,
        )
        await execute_turn_closure_memory_writes(closure)
        return _with_execution_trace_ui(
            final_text,
            status="failed",
            contract=contract,
            work_order=work_order,
            tool_id=tool_id,
            ok=False,
            reason="confirmed_direct_tool_not_allowed",
        )
    if not _planned_direct_tool_available(tool_id, tools):
        final_text = f"\u5df2\u6536\u5230\u786e\u8ba4\uff0c\u4f46\u5f53\u524d\u5de5\u5177\u4e0d\u53ef\u7528\uff1a{tool_id}\u3002"
        closure = close_turn(
            turn_id=contract.turn_id,
            final_text=final_text,
            executed_work_orders=[],
            verification_reports=[],
            aborted=True,
            memory_context_refs=contract.memory_context_refs,
        )
        await execute_turn_closure_memory_writes(closure)
        return _with_execution_trace_ui(
            final_text,
            status="failed",
            contract=contract,
            work_order=work_order,
            tool_id=tool_id,
            ok=False,
            reason="planned_direct_tool_unavailable",
        )

    slot_gap = _message_send_slot_gap(work_order, tool_id)
    if slot_gap:
        final_text = _message_send_slot_gap_reply(
            slot_gap,
            contract=contract,
            work_order=work_order,
        )
        closure = close_turn_waiting_user(
            turn_id=contract.turn_id,
            final_text=final_text,
            pending_decision={
                "decision_id": contract.decision_id,
                "work_order_id": work_order.work_order_id,
                "task_type": contract.task_type,
                "missing_slot": slot_gap,
                "tool": tool_id,
            },
        )
        _log_direct_execution(
            stage="waiting_user_message_slot",
            contract=contract,
            work_order=work_order,
            tool_id=tool_id,
            closure=closure,
            final_text=final_text,
            reason=f"missing_message_slot:{slot_gap}",
        )
        return _with_execution_trace_ui(
            final_text,
            status="waiting_user",
            contract=contract,
            work_order=work_order,
            tool_id=tool_id,
            ok=False,
            reason=f"missing_message_slot:{slot_gap}",
        )

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
    record_entity_correction_usage_from_work_order(
        work_order=work_order,
        turn_id=contract.turn_id,
        ok=bool(result.verification.ok),
        failure_reason=str(getattr(result.verification, "failure_reason", "") or ""),
    )
    if result.verification.ok:
        record_confirmed_entity_correction_from_work_order(work_order=work_order, turn_id=contract.turn_id)
        record_confirmed_entity_correction_from_input_context(work_order=work_order, turn_id=contract.turn_id)
        clear_pending_confirmation_by_key(pending.session_key)
    final_text = _direct_reply_from_contract(contract, work_order, bool(result.verification.ok), result.observation)
    closure = close_turn(
        turn_id=contract.turn_id,
        final_text=final_text,
        executed_work_orders=[work_order.work_order_id],
        verification_reports=[result.verification],
        aborted=not bool(result.verification.ok),
        memory_context_refs=contract.memory_context_refs,
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
    return _with_execution_trace_ui(
        final_text,
        status="done" if result.verification.ok else "failed",
        contract=contract,
        work_order=work_order,
        tool_id=tool_id,
        ok=bool(result.verification.ok),
    )


def _resolve_pending_app_slot_reply(text: str, work_order: WorkOrder) -> str:
    if not _pending_work_order_missing_app(work_order):
        return ""
    reply = str(text or "").strip()
    if not reply or len(reply) > 80:
        return ""
    low = reply.lower().strip(" .,!?:;\t\r\n")
    direct = {
        "lark": "Lark",
        "feishu": "Lark",
        "flybook": "Lark",
        "lock": "Lark",
        "log": "Lark",
        "look": "Lark",
        "loc": "Lark",
        "lok": "Lark",
        "\u98de\u4e66": "Lark",
        "wechat": "WeChat",
        "weixin": "WeChat",
        "we chat": "WeChat",
        "\u5fae\u4fe1": "WeChat",
        "chrome": "Chrome",
        "browser": "Browser",
        "\u6d4f\u89c8\u5668": "Browser",
        "calculator": "Calculator",
        "calc": "Calculator",
        "\u8ba1\u7b97\u5668": "Calculator",
    }
    if low in direct:
        return direct[low]
    corrected = correct_voice_entities(f"open {reply}").corrected_text.strip()
    if corrected.lower().startswith("open "):
        candidate = corrected[5:].strip()
        if candidate in {"Lark", "WeChat", "Chrome", "Browser", "Calculator", "VS Code", "Codex"}:
            return candidate
    return ""


def _pending_work_order_missing_app(work_order: WorkOrder) -> bool:
    tool = str(work_order.inputs.get("tool") or "").strip()
    if tool not in {"mcp:windows_open_app", "mcp:windows_close_app", "mcp:windows_switch_app"}:
        return False
    target = work_order.inputs.get("target")
    if isinstance(target, dict):
        if str(target.get("name") or target.get("app") or "").strip():
            return False
    raw_input = str(work_order.inputs.get("work_order_input") or "")
    if raw_input:
        try:
            payload = json.loads(raw_input)
        except Exception:
            payload = {}
        if isinstance(payload, dict) and str(payload.get("app") or payload.get("name") or "").strip():
            return False
    return True


def _fill_pending_app_slot(work_order: WorkOrder, *, app_name: str, heard_as: str) -> None:
    target = work_order.inputs.get("target")
    target = dict(target) if isinstance(target, dict) else {}
    target.update({"type": "app", "name": app_name, "source": "pending_slot_reply", "heard_as": str(heard_as or "")})
    work_order.inputs["target"] = target
    work_order.inputs["work_order_input"] = json.dumps({"app": app_name}, ensure_ascii=False)
    intent = str(work_order.inputs.get("intent") or "open_app").strip() or "open_app"
    work_order.task = f"{intent} {app_name}"
    _remember_pending_slot_entity_alias(kind="app", canonical=app_name, heard_as=heard_as)


def _resolve_pending_message_slot_reply(text: str, work_order: WorkOrder) -> dict[str, str]:
    tool_id = str(work_order.inputs.get("tool") or "").strip()
    if not any(token in tool_id.lower() for token in ("lark", "send", "message", "smtp")):
        return {}
    gap = _message_send_slot_gap(work_order, tool_id)
    if not gap:
        return {}
    reply = str(text or "").strip()
    if not reply or len(reply) > 300:
        return {}
    patch: dict[str, str] = {}
    if gap == "recipient":
        recipient = _resolve_builtin_message_recipient_choice(reply)
        if not recipient:
            recipient = _extract_direct_recipient_reply(reply)
        if recipient:
            patch["recipient"] = recipient
    if gap == "message":
        message = _extract_direct_message_reply(reply)
        if message:
            patch["message"] = message
    return patch


def _fill_pending_message_slot(work_order: WorkOrder, *, patch: dict[str, str], heard_as: str) -> None:
    payload = _work_order_payload_obj(work_order)
    if patch.get("recipient"):
        payload["recipients_json"] = json.dumps([patch["recipient"]], ensure_ascii=False)
        payload["recipient"] = patch["recipient"]
        _remember_pending_slot_entity_alias(kind="contact", canonical=patch["recipient"], heard_as=heard_as)
    if patch.get("message"):
        payload["message"] = patch["message"]
    payload["slot_reply_heard_as"] = str(heard_as or "")
    target = work_order.inputs.get("target")
    target = dict(target) if isinstance(target, dict) else {}
    if patch.get("recipient"):
        target["recipients"] = [patch["recipient"]]
        target["recipient_source"] = "builtin_contact_choice" if _is_builtin_message_recipient(patch["recipient"]) else "pending_slot_reply"
    if patch.get("message"):
        target["message"] = patch["message"]
        target["message_source"] = "pending_slot_reply"
    target["slot_reply_heard_as"] = str(heard_as or "")
    work_order.inputs["target"] = target
    work_order.inputs["work_order_input"] = json.dumps(
        {
            "recipients_json": payload.get("recipients_json", "[]"),
            "message": str(payload.get("message") or ""),
            "max_attempts": int(payload.get("max_attempts") or 2),
        },
        ensure_ascii=False,
    )
    work_order.task = "message_send"


def _remember_pending_slot_entity_alias(*, kind: str, canonical: str, heard_as: str) -> None:
    alias = str(heard_as or "").strip().strip(" .,!?:;\t\r\n")
    if not alias or not canonical:
        return
    if alias.lower() == str(canonical).lower():
        return
    if alias.lower() in {"1", "2", "3", "a", "b", "c", "yes", "no", "\u662f", "\u5426", "\u5bf9"}:
        return
    if len(alias) > 60:
        return
    if not re.search(r"[A-Za-z\u4e00-\u9fff]", alias):
        return
    try:
        teach_alias(kind, canonical, alias, source="pending_slot_confirmed")
    except Exception:
        logger.debug("pending slot alias memory skipped kind=%s canonical=%s alias=%s", kind, canonical, alias, exc_info=True)


def _builtin_message_recipient_options() -> list[tuple[str, str, str]]:
    try:
        from l3_node.message_contacts import message_contact_options

        return message_contact_options()
    except Exception:
        return [
            ("1", "A", "Neil"),
            ("2", "B", "Vivian"),
            ("3", "C", "测试备注冒烟草稿"),
        ]


def _resolve_builtin_message_recipient_choice(text: str) -> str:
    normalized = str(text or "").strip().lower().strip(" .,!?:;\t\r\n。！？、，；：")
    normalized = normalized.replace("选项", "").replace("第", "").replace("个", "").strip()
    cn_digits = {"一": "1", "二": "2", "三": "3"}
    normalized = cn_digits.get(normalized, normalized)
    for number, letter, name in _builtin_message_recipient_options():
        if normalized in {number.lower(), letter.lower(), name.lower()}:
            return name
    try:
        from l3_node.message_contacts import message_contact_alias_map

        direct_aliases = message_contact_alias_map()
    except Exception:
        direct_aliases = {
            "n": "Neil",
            "new": "Neil",
            "neil": "Neil",
            "v": "Vivian",
            "vivian": "Vivian",
            "测试群": "测试备注冒烟草稿",
            "群": "测试备注冒烟草稿",
            "群聊": "测试备注冒烟草稿",
            "测试备注": "测试备注冒烟草稿",
            "测试备注冒烟草稿": "测试备注冒烟草稿",
        }
    return direct_aliases.get(normalized, "")


def _is_builtin_message_recipient(name: str) -> bool:
    return any(name == option[2] for option in _builtin_message_recipient_options())


def _extract_direct_recipient_reply(text: str) -> str:
    reply = str(text or "").strip().strip(" .,!?:;\t\r\n。！？、，；：")
    match = re.search(r"(?:发给|发送给|给|找|联系人是|收件人是)\s*([A-Za-z][A-Za-z0-9_.-]{1,40}|[\u4e00-\u9fff]{2,20})", reply, re.I)
    if match:
        return match.group(1).strip(" .,!?:;\t\r\n。！？、，；：")
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{1,40}|[\u4e00-\u9fff]{2,20}", reply):
        return reply
    return ""


def _extract_direct_message_reply(text: str) -> str:
    reply = str(text or "").strip().strip(" \t\r\n")
    match = re.search(r"(?:内容|消息|正文)\s*(?:是|为|改成|改为|:|：)?\s*(.+)$", reply, re.I)
    if match:
        reply = match.group(1)
    reply = reply.strip(" .,!?:;\t\r\n。！？、，；：\"'“”‘’")
    if not reply or _resolve_builtin_message_recipient_choice(reply):
        return ""
    return reply


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
    if wanted in {"mcp:tavily_search", "mcp:fetch", "core:web_research_summarize"}:
        return True
    return bool(wanted) and any(str(t.get("id") or "").strip().lower() == wanted for t in tools if isinstance(t, dict))


def _planned_direct_tool_allowed(plan: KernelPlanningResult, tool_id: str) -> bool:
    contract = plan.decision_contract
    if not contract.execution_allowed or not plan.work_orders:
        return False
    if contract.risk_level.value == "critical":
        return False
    planned_tools = {str(wo.inputs.get("tool") or "").strip() for wo in plan.work_orders}
    if tool_id not in planned_tools:
        return False
    if contract.task_type == "app_control":
        return contract.risk_level.value == "low" and tool_id in {
            "mcp:windows_open_app",
            "mcp:windows_window_switch",
            "mcp:windows_window_close",
        }
    if contract.task_type == "calculator_calculate":
        return contract.risk_level.value == "low" and tool_id in {
            "mcp:windows_open_app",
            "mcp:windows_calculator_calculate",
        }
    if contract.task_type == "message_delivery":
        return tool_id in {
            "mcp:windows_open_app",
            "mcp:windows_lark_send_message",
            "util:lark_send_text",
            "mcp:lark_send_text",
        }
    if contract.task_type == "file_operation":
        return contract.risk_level.value in {"low", "medium", "high"} and tool_id in {
            "core:fs_read",
            "core:fs_write",
            "mcp:windows_file_open",
            "mcp:windows_file_reveal_in_explorer",
        }
    if contract.task_type == "web_research_delivery":
        return contract.risk_level.value in {"low", "medium", "high"} and tool_id in {
            "mcp:tavily_search",
            "mcp:fetch",
            "core:web_research_summarize",
            "mcp:windows_lark_send_message",
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
    if contract.task_type == "calculator_calculate":
        return contract.risk_level.value == "low" and tool_id == "mcp:windows_calculator_calculate"
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
    if contract.task_type == "web_research_delivery":
        return contract.risk_level.value in {"low", "medium", "high"} and tool_id in {
            "mcp:tavily_search",
            "mcp:fetch",
            "core:web_research_summarize",
            "mcp:windows_lark_send_message",
        }
    return False


def _inject_upstream_into_work_order(work_order: WorkOrder, upstream_observations: list[dict[str, Any]]) -> None:
    if not upstream_observations:
        return
    raw = str(work_order.inputs.get("work_order_input") or "").strip()
    try:
        payload = json.loads(raw) if raw else {}
    except Exception:
        payload = {"raw_input": raw}
    if not isinstance(payload, dict):
        payload = {"raw_input": raw}
    payload["upstream_observations"] = list(upstream_observations)
    tool = str(work_order.inputs.get("tool") or "").strip()
    if tool == "mcp:fetch" and not payload.get("url") and not payload.get("urls"):
        urls = _extract_urls_from_upstream(upstream_observations)
        if urls:
            payload["urls"] = urls[:3]
    if tool == "mcp:windows_lark_send_message":
        delivery_context_found = False
        summary_message = _extract_message_from_upstream(upstream_observations)
        if summary_message:
            payload["message"] = summary_message
            delivery_context_found = True
        summary_quality = _extract_summary_quality_from_upstream(upstream_observations)
        if summary_quality:
            payload["quality_report"] = summary_quality
            delivery_context_found = True
        summary_sources = _extract_summary_sources_from_upstream(upstream_observations)
        if summary_sources:
            payload["sources"] = summary_sources
            delivery_context_found = True
        if delivery_context_found or any(
            _observation_has_delivery_context(item) for item in upstream_observations or []
        ):
            payload.setdefault("delivery_mode", _extract_delivery_mode_from_upstream(upstream_observations, payload))
            payload.setdefault("dry_run", payload.get("delivery_mode") != "live_run")
            payload.setdefault("send_allowed", payload.get("delivery_mode") == "live_run")
    work_order.inputs["work_order_input"] = json.dumps(payload, ensure_ascii=False)


def _observation_has_delivery_context(item: dict[str, Any]) -> bool:
    text = str(item.get("observation") or "")
    try:
        obj = json.loads(text)
    except Exception:
        return False
    if not isinstance(obj, dict):
        return False
    return any(
        key in obj
        for key in (
            "delivery_mode",
            "dry_run",
            "send_allowed",
            "quality_report",
            "sources",
            "summary",
            "message",
        )
    )


def _extract_message_from_upstream(upstream_observations: list[dict[str, Any]]) -> str:
    for item in reversed(upstream_observations or []):
        text = str(item.get("observation") or "")
        try:
            obj = json.loads(text)
        except Exception:
            obj = None
        if isinstance(obj, dict):
            msg = str(obj.get("message") or obj.get("summary") or "").strip()
            if msg:
                return msg
    return ""


def _extract_summary_quality_from_upstream(upstream_observations: list[dict[str, Any]]) -> dict[str, Any]:
    for item in reversed(upstream_observations or []):
        text = str(item.get("observation") or "")
        try:
            obj = json.loads(text)
        except Exception:
            obj = None
        if isinstance(obj, dict) and isinstance(obj.get("quality_report"), dict):
            return dict(obj["quality_report"])
    return {}


def _extract_summary_sources_from_upstream(upstream_observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for item in reversed(upstream_observations or []):
        text = str(item.get("observation") or "")
        try:
            obj = json.loads(text)
        except Exception:
            obj = None
        if isinstance(obj, dict) and isinstance(obj.get("sources"), list):
            return [src for src in obj["sources"] if isinstance(src, dict)][:6]
    return []


def _extract_delivery_mode_from_upstream(upstream_observations: list[dict[str, Any]], payload: dict[str, Any]) -> str:
    explicit = str(payload.get("delivery_mode") or "").strip().lower()
    if explicit in {"live_run", "dry_run"}:
        return explicit
    if payload.get("send_allowed") is False or payload.get("dry_run") is True:
        return "dry_run"
    if payload.get("send_allowed") is True or payload.get("live_run") is True:
        return "live_run"
    for item in reversed(upstream_observations or []):
        text = str(item.get("observation") or "")
        try:
            obj = json.loads(text)
        except Exception:
            obj = None
        if isinstance(obj, dict):
            mode = str(obj.get("delivery_mode") or "").strip().lower()
            if mode in {"live_run", "dry_run"}:
                return mode
            if obj.get("dry_run") is True or obj.get("send_allowed") is False:
                return "dry_run"
            if obj.get("live_run") is True or obj.get("send_allowed") is True:
                return "live_run"
    return "dry_run"


def _extract_urls_from_upstream(upstream_observations: list[dict[str, Any]]) -> list[str]:
    urls: list[str] = []
    for item in upstream_observations or []:
        text = str(item.get("observation") or "")
        try:
            obj = json.loads(text)
        except Exception:
            obj = None
        if isinstance(obj, dict):
            for result in obj.get("results") or obj.get("items") or []:
                if isinstance(result, dict):
                    url = str(result.get("url") or result.get("link") or "").strip()
                    if url and url not in urls:
                        urls.append(url)
        for url in re.findall(r"https?://[^\s\"'<>]+", text):
            clean = url.rstrip(").,;，。")
            if clean and clean not in urls:
                urls.append(clean)
    return urls


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


def _message_send_slot_gap(work_order: WorkOrder, tool_id: str) -> str:
    low_tool = str(tool_id or work_order.inputs.get("tool") or "").lower()
    if not any(token in low_tool for token in ("lark", "send", "message", "smtp")):
        return ""
    payload = _work_order_payload_obj(work_order)
    if not _payload_recipients(payload):
        return "recipient"
    if not _payload_message(payload):
        return "message"
    return ""


def _first_message_send_slot_gap(
    work_orders: list[WorkOrder],
    *,
    contract: DecisionContract,
) -> tuple[WorkOrder | None, str, str]:
    for item in work_orders:
        tool_id = str(
            item.inputs.get("tool")
            or (item.tool_policy.allowed_tools[0] if item.tool_policy.allowed_tools else "")
            or (contract.tool_policy.allowed_tools[0] if contract.tool_policy.allowed_tools else "")
        ).strip()
        gap = _message_send_slot_gap(item, tool_id)
        if gap:
            return item, tool_id, gap
    return None, "", ""




def _message_send_slot_gap_reply(
    slot_gap: str,
    *,
    contract: DecisionContract | None = None,
    work_order: WorkOrder | None = None,
) -> str:
    if slot_gap == "recipient":
        options = "; ".join(
            f"{number}/{letter} = {name}" for number, letter, name in _builtin_message_recipient_options()
        )
        text = f"我还不知道这条消息要发给谁。请选择联系人，或直接回复编号/字母：{options}。"
        if contract is not None and work_order is not None:
            text = _with_pending_slot_choice_ui_protocol(
                text,
                contract=contract,
                work_order=work_order,
                slot="recipient",
            )
            text = _with_execution_trace_ui(
                text,
                status="waiting_user",
                contract=contract,
                work_order=work_order,
                tool_id=str(work_order.inputs.get("tool") or ""),
                ok=False,
                reason="missing_message_recipient",
            )
        return text
    if slot_gap == "message":
        return "我还不知道要发送什么内容。请补充消息正文，例如：内容是你好。"
    return "这条消息还缺少必要信息，请补充联系人和消息内容。"


def _with_pending_slot_choice_ui_protocol(
    text: str,
    *,
    contract: DecisionContract,
    work_order: WorkOrder,
    slot: str,
) -> str:
    choices = []
    for number, letter, name in _builtin_message_recipient_options():
        choices.append(
            {
                "id": f"{number}/{letter}",
                "label": name,
                "value": name,
                "send_text": name,
                "description": f"选择 {name} 作为收件人并继续执行这条消息任务",
            }
        )
    payload = {
        "type": "pending_confirmation",
        "interaction_kind": "slot_choice",
        "slot": slot,
        "decision_id": contract.decision_id,
        "work_order_id": work_order.work_order_id,
        "task_type": contract.task_type,
        "risk_level": contract.risk_level.value,
        "tool": work_order.inputs.get("tool"),
        "cancel_text": "取消",
        "choices": choices,
    }
    marker = f"<!-- jachin-ui:pending-confirmation {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))} -->"
    return f"{text.rstrip()}\n\n{marker}"


def _work_order_payload_obj(work_order: WorkOrder) -> dict[str, Any]:
    raw = work_order.inputs.get("work_order_input")
    if isinstance(raw, str) and raw.strip():
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                merged = dict(work_order.inputs or {})
                merged.update(obj)
                return merged
        except Exception:
            pass
    return dict(work_order.inputs or {})


def _payload_recipients(payload: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("recipient", "recipients", "recipients_json", "chat_id", "chat_ids", "to", "user", "users"):
        value = payload.get(key)
        if key == "recipients_json" and isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    out.extend(str(item).strip() for item in parsed if str(item).strip())
                elif str(parsed).strip():
                    out.append(str(parsed).strip())
            except Exception:
                if value.strip():
                    out.append(value.strip())
        elif isinstance(value, str) and value.strip():
            out.append(value.strip())
        elif isinstance(value, list):
            out.extend(str(item).strip() for item in value if str(item).strip())
    return [item for item in out if item]


def _payload_message(payload: dict[str, Any]) -> str:
    for key in ("message", "text", "content", "body", "summary"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _calculator_result_reply(target_obj: dict[str, Any], observation: str) -> str:
    expression = str(target_obj.get("expression") or "").strip()
    result = ""
    try:
        obj = json.loads(str(observation or ""))
    except Exception:
        obj = None
    if isinstance(obj, dict):
        evidence = obj.get("evidence") if isinstance(obj.get("evidence"), dict) else obj
        for key in ("clipboard_norm", "clipboard_raw", "expect", "result"):
            value = str(evidence.get(key) or "").strip() if isinstance(evidence, dict) else ""
            if value:
                result = value
                break
        visual = evidence.get("visual") if isinstance(evidence, dict) and isinstance(evidence.get("visual"), dict) else {}
        if not result:
            result = str(visual.get("result_norm") or visual.get("result") or "").strip()
    if expression and result:
        return f"已用计算器计算：{expression}={result}。"
    if expression:
        return f"已用计算器完成计算：{expression}。"
    if result:
        return f"已用计算器完成计算，结果是 {result}。"
    return "已用计算器完成计算。"


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
        if task_type == "web_research_delivery":
            recipients = target_obj.get("recipients") if isinstance(target_obj.get("recipients"), list) else []
            names = "\u3001".join(str(x) for x in recipients if str(x).strip()) or "\u76ee\u6807\u4f1a\u8bdd"
            query = str(target_obj.get("query") or target_obj.get("name") or "\u6700\u65b0\u4fe1\u606f").strip()
            if _observation_is_dry_run_preview(observation):
                return f"\u5df2\u81ea\u52a8\u8054\u7f51\u68c0\u7d22\u201c{query}\u201d\uff0c\u5e76\u751f\u6210\u53d1\u7ed9 {names} \u7684\u9884\u89c8\uff1b\u672c\u6b21\u662f dry-run\uff0c\u672a\u771f\u5b9e\u53d1\u9001\u3002"
            return f"\u5df2\u81ea\u52a8\u8054\u7f51\u68c0\u7d22\u201c{query}\u201d\uff0c\u5e76\u5c06\u6458\u8981\u53d1\u9001\u7ed9 {names}\u3002"
        if task_type == "calculator_calculate":
            return _calculator_result_reply(target_obj, observation)
        if task_type == "file_operation":
            return f"\u5df2\u5b8c\u6210\u6587\u4ef6\u64cd\u4f5c\uff1a{target}\u3002"
        return "\u5df2\u5b8c\u6210\u3002"
    preview = str(observation or "").strip()[:300]
    if preview:
        return f"{target} \u7684\u4efb\u52a1\u6ca1\u6709\u901a\u8fc7\u9a8c\u8bc1\u3002{preview}"
    return f"{target} \u7684\u4efb\u52a1\u6ca1\u6709\u901a\u8fc7\u9a8c\u8bc1\u3002"


def _observation_is_dry_run_preview(observation: str) -> bool:
    try:
        obj = json.loads(str(observation or ""))
    except Exception:
        return False
    return isinstance(obj, dict) and obj.get("dry_run") is True and obj.get("dry_run_preview_verified") is True
