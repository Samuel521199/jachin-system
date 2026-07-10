"""Arbiter for ReviewSummary -> DecisionContract -> WorkOrder planning."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from .contracts import DecisionContract, ReviewSummary, RiskLevel, ToolPolicy, WorkOrder
from .ledger import append_event, record_decision, record_work_order


def _new_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time())}_{uuid.uuid4().hex[:10]}"


def arbitrate_review_summary(summary: ReviewSummary, *, goal: str = "") -> DecisionContract:
    """Create the kernel's final DecisionContract from role reviews.

    Review roles only provide evidence. The Arbiter owns the final contract:
    workflow, role set, risk gate, allowed tools, clarification, and criteria.
    """

    task_type = summary.task_type or "conversation"
    tool = summary.candidate_tools[0] if summary.candidate_tools else ""
    requires_confirmation = _requires_confirmation(summary)
    execution_allowed = bool(tool and not requires_confirmation and not summary.needs_clarification)
    if task_type == "conversation":
        execution_allowed = False
    clarification = summary.clarification_question
    if requires_confirmation and not clarification:
        clarification = "这个操作风险较高，请确认后再执行。"

    contract = DecisionContract(
        decision_id=_new_id("decision"),
        turn_id=summary.turn_id,
        task_type=task_type,
        goal=(goal or _goal_from_summary(summary))[:1000],
        selected_workflow=_workflow_for(summary),
        selected_roles=list(summary.selected_roles),
        risk_level=summary.risk_level,
        tool_policy=ToolPolicy(
            allowed_tools=[tool] if tool else [],
            denied_tools=[],
            risk_level=summary.risk_level,
            requires_confirmation=requires_confirmation,
            confirmation_reason=clarification if requires_confirmation else "",
            verification_required=bool(tool),
        ),
        execution_allowed=execution_allowed,
        clarification_question=clarification,
        verification_criteria=_verification_criteria(summary),
        rationale=[
            "Arbiter accepted ReviewBoard evidence and produced the final DecisionContract.",
            *summary.rationale,
        ],
    )
    record_decision(contract)
    append_event(
        "arbiter_decision",
        summary.turn_id,
        {
            "review_session_id": summary.review_session_id,
            "decision_id": contract.decision_id,
            "top_intent": summary.top_intent,
            "task_type": contract.task_type,
            "execution_allowed": contract.execution_allowed,
            "selected_roles": contract.selected_roles,
            "target": summary.target,
            "candidate_tools": summary.candidate_tools,
            "risk_level": contract.risk_level.value,
            "requires_confirmation": contract.tool_policy.requires_confirmation,
        },
    )
    return contract


def build_work_order_from_decision(contract: DecisionContract, summary: ReviewSummary) -> WorkOrder | None:
    if not contract.tool_policy.allowed_tools:
        return None
    if not contract.execution_allowed and not contract.tool_policy.requires_confirmation:
        return None
    role_agent = _executor_role(contract.selected_roles)
    tool = contract.tool_policy.allowed_tools[0]
    work_order_input = _work_order_input_for(summary, tool)
    work_order = WorkOrder(
        work_order_id=_new_id("work"),
        decision_id=contract.decision_id,
        role_agent=role_agent,
        task=_task_text(summary),
        inputs={
            "tool": tool,
            "work_order_input": work_order_input,
            "intent": summary.top_intent,
            "target": summary.target,
            "review_session_id": summary.review_session_id,
        },
        tool_policy=contract.tool_policy,
        expected_outputs=["execution_report", "observable_evidence"],
        verification_criteria=contract.verification_criteria,
        status="pending",
    )
    record_work_order(work_order, contract.turn_id)
    append_event(
        "arbiter_work_order_created",
        contract.turn_id,
        {
            "review_session_id": summary.review_session_id,
            "decision_id": contract.decision_id,
            "work_order_id": work_order.work_order_id,
            "role_agent": work_order.role_agent,
            "tool": tool,
            "target": summary.target,
        },
    )
    return work_order


def _requires_confirmation(summary: ReviewSummary) -> bool:
    if summary.needs_clarification:
        return True
    if summary.risk_level in {RiskLevel.CRITICAL, RiskLevel.HIGH} and summary.top_intent in {"close_app", "file_operation"}:
        return True
    return False


def _goal_from_summary(summary: ReviewSummary) -> str:
    target = summary.target.get("name") if summary.target else ""
    if target:
        return f"{summary.top_intent}: {target}"
    return summary.top_intent or summary.task_type or "answer user"


def _workflow_for(summary: ReviewSummary) -> str:
    if summary.task_type == "app_control":
        return "reviewed_app_control_workflow"
    if summary.task_type == "message_delivery":
        return "reviewed_message_delivery_workflow"
    if summary.task_type == "file_operation":
        return "reviewed_file_operation_workflow"
    return "conversation_reply_workflow"


def _verification_criteria(summary: ReviewSummary) -> list[str]:
    target = summary.target.get("name") if summary.target else ""
    if summary.top_intent == "open_app":
        return [
            f"{target or 'target app'} appears in running apps or foreground window",
            "window/app state is refreshed after execution",
        ]
    if summary.top_intent == "close_app":
        return [
            f"{target or 'target app'} is no longer foreground or no longer running",
            "no unsaved-content prompt remains unhandled",
        ]
    if summary.top_intent == "switch_app":
        return [f"{target or 'target app'} becomes foreground window"]
    if summary.task_type == "message_delivery":
        return ["message target and content preview match", "send result has observable evidence"]
    if summary.task_type == "file_operation":
        return ["file operation result matches expected path and content evidence"]
    return ["no external-world action required"]


def _executor_role(roles: list[str]) -> str:
    for role in roles:
        if role.endswith("ExecutorAgent"):
            return role
    if "MemoryWriteAgent" in roles:
        return "MemoryWriteAgent"
    return "ToolExecutionAgent"


def _task_text(summary: ReviewSummary) -> str:
    target = summary.target.get("name") if summary.target else ""
    if target:
        return f"{summary.top_intent} {target}"
    return summary.top_intent or summary.task_type


def _work_order_input_for(summary: ReviewSummary, tool: str) -> str:
    target = summary.target or {}
    if tool == "mcp:windows_lark_send_message":
        recipients = target.get("recipients") if isinstance(target.get("recipients"), list) else []
        message = str(target.get("message") or "").strip()
        return json.dumps(
            {
                "recipients_json": json.dumps([str(x) for x in recipients if str(x).strip()], ensure_ascii=False),
                "message": message,
                "max_attempts": 2,
            },
            ensure_ascii=False,
        )
    if tool == "core:fs_read":
        path = str(target.get("path") or target.get("name") or "").strip()
        return json.dumps({"path": path}, ensure_ascii=False)
    if tool == "core:fs_write":
        path = str(target.get("path") or target.get("name") or "").strip()
        content = str(target.get("content") or "").strip()
        return json.dumps({"path": path, "content": content}, ensure_ascii=False)
    if tool == "mcp:windows_file_open":
        path = str(target.get("path") or target.get("name") or "").strip()
        return json.dumps({"path": path}, ensure_ascii=False)
    if tool == "mcp:windows_file_reveal_in_explorer":
        path = str(target.get("path") or target.get("name") or "").strip()
        return json.dumps({"path": path}, ensure_ascii=False)
    return ""
