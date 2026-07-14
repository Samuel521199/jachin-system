"""Task and capability experience memory for the Cognitive Kernel.

This module turns completed WorkOrder chains into durable memory write
requests. It keeps three related memory types together:

- historical_task_summary: what the user asked, what workflow ran, and whether
  it passed verification;
- tool_habit: which capability/tool worked for this task family;
- failure_hint: what failed and what recovery should consider next time.

The actual persistence still goes through TurnClosure -> MemoryWriteAgent ->
memory_lifecycle, so this is part of the unified memory architecture rather
than a side store.
"""

from __future__ import annotations

import json
from typing import Any

from .contracts import DecisionContract, MemoryWriteRequest, VerificationReport, WorkOrder
from .ledger import append_event


def build_task_experience_memory_requests(
    *,
    contract: DecisionContract,
    work_orders: list[WorkOrder],
    verification_reports: list[VerificationReport],
    final_text: str,
) -> list[MemoryWriteRequest]:
    if not work_orders:
        return []
    ok = bool(verification_reports) and all(report.ok for report in verification_reports)
    tools = [_work_order_tool(item) for item in work_orders]
    tools = [tool for tool in tools if tool]
    failure_reasons = [str(report.failure_reason or "").strip() for report in verification_reports if not report.ok]
    failure_reasons = [reason for reason in failure_reasons if reason]
    base_evidence = [
        {
            "type": "task_experience",
            "ok": ok,
            "task_type": contract.task_type,
            "workflow": contract.selected_workflow,
            "decision_id": contract.decision_id,
            "work_order_ids": [item.work_order_id for item in work_orders],
            "tools": tools,
            "failed_count": len(failure_reasons),
        }
    ]
    requests = [
        MemoryWriteRequest(
            turn_id=contract.turn_id,
            source_event="turn_closure_task_experience",
            memory_type="historical_task_summary",
            content=json.dumps(
                {
                    "type": "task_experience",
                    "task_type": contract.task_type,
                    "goal": contract.goal,
                    "workflow": contract.selected_workflow,
                    "roles": list(contract.selected_roles or []),
                    "tools": tools,
                    "work_order_ids": [item.work_order_id for item in work_orders],
                    "verification_status": "passed" if ok else "failed",
                    "failure_reasons": failure_reasons,
                    "final_user_message": str(final_text or "")[:300],
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            evidence=base_evidence,
            confidence=0.84 if ok else 0.56,
            ttl="30d",
            merge_policy="append_action_chain",
        )
    ]
    if ok:
        requests.extend(_tool_habit_requests(contract=contract, work_orders=work_orders, tools=tools))
    else:
        requests.append(_failure_hint_request(contract=contract, work_orders=work_orders, tools=tools, failure_reasons=failure_reasons))
    append_event(
        "task_experience_memory_requests_built",
        contract.turn_id,
        {
            "request_count": len(requests),
            "task_type": contract.task_type,
            "workflow": contract.selected_workflow,
            "tools": tools,
            "ok": ok,
        },
    )
    return requests


def _tool_habit_requests(*, contract: DecisionContract, work_orders: list[WorkOrder], tools: list[str]) -> list[MemoryWriteRequest]:
    out: list[MemoryWriteRequest] = []
    for tool in tools:
        out.append(
            MemoryWriteRequest(
                turn_id=contract.turn_id,
                source_event="turn_closure_capability_success",
                memory_type="tool_habit",
                content=json.dumps(
                    {
                        "type": "capability_usage",
                        "task_type": contract.task_type,
                        "workflow": contract.selected_workflow,
                        "tool": tool,
                        "role_agents": _roles_for_tool(work_orders, tool),
                        "recommendation": "prefer_when_same_task_type_and_slots_match",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                evidence=[
                    {
                        "type": "capability_success",
                        "ok": True,
                        "task_type": contract.task_type,
                        "tool": tool,
                        "workflow": contract.selected_workflow,
                    }
                ],
                confidence=0.78,
                ttl="permanent",
                merge_policy="dedupe_and_merge",
            )
        )
    return out


def _failure_hint_request(
    *,
    contract: DecisionContract,
    work_orders: list[WorkOrder],
    tools: list[str],
    failure_reasons: list[str],
) -> MemoryWriteRequest:
    failed_work_orders = [
        {
            "work_order_id": item.work_order_id,
            "role_agent": item.role_agent,
            "tool": _work_order_tool(item),
            "task": item.task,
        }
        for item in work_orders
        if _work_order_tool(item) in tools
    ]
    return MemoryWriteRequest(
        turn_id=contract.turn_id,
        source_event="turn_closure_failure_hint",
        memory_type="failure_hint",
        content=json.dumps(
            {
                "type": "task_failure_hint",
                "task_type": contract.task_type,
                "workflow": contract.selected_workflow,
                "tools": tools,
                "failed_work_orders": failed_work_orders,
                "failure_reasons": failure_reasons or ["verification_failed"],
                "next_time_policy": "consult_recovery_playbook_and_avoid_repeating_same_failed_path",
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        evidence=[
            {
                "type": "task_failure",
                "ok": False,
                "task_type": contract.task_type,
                "tools": tools,
                "failure_reasons": failure_reasons,
            }
        ],
        confidence=0.66,
        ttl="14d",
        merge_policy="dedupe_and_merge",
    )


def _work_order_tool(work_order: WorkOrder) -> str:
    tool = str((work_order.inputs or {}).get("tool") or "").strip()
    if tool:
        return tool
    if work_order.tool_policy.allowed_tools:
        return str(work_order.tool_policy.allowed_tools[0] or "").strip()
    return ""


def _roles_for_tool(work_orders: list[WorkOrder], tool: str) -> list[str]:
    roles: list[str] = []
    for item in work_orders:
        if _work_order_tool(item) == tool and item.role_agent not in roles:
            roles.append(item.role_agent)
    return roles
