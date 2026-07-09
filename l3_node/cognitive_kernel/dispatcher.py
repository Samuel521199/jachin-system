"""WorkOrder dispatcher for role-agent mediated tool execution."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from .contracts import DecisionContract, RecoveryPlan, VerificationReport, WorkOrder
from .runtime import (
    blocked_confirmation_observation,
    build_decision_contract,
    build_recovery_plan,
    build_work_order,
    classify_tool_risk,
    mark_work_order_done,
    mark_work_order_running,
    verify_work_order,
)
from .role_executors import (
    RoleExecutionContext,
    RoleExecutorRegistry,
    get_default_role_executor_registry,
)
from .ledger import append_event
from .roles import RoleAgentRegistry, get_default_role_registry

ToolExecutor = Callable[[WorkOrder], Awaitable[str]]


@dataclass(slots=True)
class DispatchResult:
    observation: str
    contract: DecisionContract
    work_order: WorkOrder
    verification: VerificationReport
    recovery_plan: RecoveryPlan | None = None


async def dispatch_tool_work_order(
    *,
    turn_id: str,
    goal: str,
    tool: str,
    action_input: str,
    executor: ToolExecutor,
    registry: RoleAgentRegistry | None = None,
    executor_registry: RoleExecutorRegistry | None = None,
) -> DispatchResult:
    registry = registry or get_default_role_registry()
    executor_registry = executor_registry or get_default_role_executor_registry()
    risk = classify_tool_risk(tool, action_input)
    role = registry.select_for_tool(tool, action_input=action_input, risk=risk)
    provisional = build_decision_contract(
        turn_id=turn_id,
        goal=goal,
        tool=tool,
        action_input=action_input,
        role_agent=role.role_id,
    )
    provisional.selected_roles = [role.role_id, "VerificationAgent", "RecoveryAgent", "TurnClosureAgent"]
    allowed, reason = registry.is_allowed(role.role_id, tool, provisional.risk_level)
    if not allowed:
        provisional.execution_allowed = False
        provisional.clarification_question = reason
        provisional.tool_policy.requires_confirmation = True
        provisional.tool_policy.confirmation_reason = reason
        provisional.rationale.append(f"Role permission matrix blocked execution: {reason}")
    work_order = build_work_order(
        contract=provisional,
        tool=tool,
        action_input=action_input,
        role_agent=role.role_id,
    )
    started = time.perf_counter()
    if not provisional.execution_allowed:
        observation = blocked_confirmation_observation(provisional)
        mark_work_order_done(work_order, provisional.turn_id, ok=False)
        verification = verify_work_order(
            turn_id=provisional.turn_id,
            work_order=work_order,
            observation=observation,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
        )
        recovery = build_recovery_plan(
            turn_id=provisional.turn_id,
            work_order=work_order,
            verification=verification,
        )
        return DispatchResult(observation, provisional, work_order, verification, recovery)

    mark_work_order_running(work_order, provisional.turn_id)
    role_context = RoleExecutionContext(
        turn_id=provisional.turn_id,
        goal=goal,
        tool=tool,
        role_id=role.role_id,
        action_input=action_input,
        metadata={
            "decision_id": provisional.decision_id,
            "work_order_id": work_order.work_order_id,
            "selected_workflow": provisional.selected_workflow,
            "risk_level": provisional.risk_level.value,
        },
    )
    role_result = None
    try:
        role_result = await executor_registry.execute(role.role_id, work_order, executor, role_context)
        observation = role_result.observation
    except Exception as exc:
        observation = f"[tool execution failed] {type(exc).__name__}: {exc}"
        verification = verify_work_order(
            turn_id=provisional.turn_id,
            work_order=work_order,
            observation=observation,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
        )
        mark_work_order_done(work_order, provisional.turn_id, ok=False)
        recovery = build_recovery_plan(
            turn_id=provisional.turn_id,
            work_order=work_order,
            verification=verification,
        )
        return DispatchResult(observation, provisional, work_order, verification, recovery)

    verification = verify_work_order(
        turn_id=provisional.turn_id,
        work_order=work_order,
        observation=str(observation or ""),
        elapsed_ms=(time.perf_counter() - started) * 1000.0,
        extra_evidence=[
            {
                "type": "role_execution",
                "role_id": role.role_id,
                "adapter_kind": role_result.evidence.get("strategy") if role_result else "",
                "adapter_elapsed_ms": role_result.elapsed_ms if role_result else None,
                "adapter_ok": role_result.ok if role_result else None,
                "adapter_evidence": role_result.evidence if role_result else {},
            }
        ],
    )
    mark_work_order_done(work_order, provisional.turn_id, ok=bool(verification.ok))
    recovery = None
    if not verification.ok:
        recovery = build_recovery_plan(
            turn_id=provisional.turn_id,
            work_order=work_order,
            verification=verification,
        )
        if _should_auto_recover(recovery, role.role_id, provisional.risk_level.value):
            append_event(
                "recovery_execution_started",
                provisional.turn_id,
                {
                    "recovery_id": recovery.recovery_id,
                    "strategy": recovery.strategy,
                    "role_id": role.role_id,
                    "work_order_id": work_order.work_order_id,
                    "tool": tool,
                    "rationale": recovery.rationale,
                },
            )
            retry_started = time.perf_counter()
            mark_work_order_running(work_order, provisional.turn_id)
            try:
                retry_result = await executor_registry.execute(role.role_id, work_order, executor, role_context)
                retry_observation = retry_result.observation
            except Exception as exc:
                retry_observation = f"[recovery retry failed] {type(exc).__name__}: {exc}"
                retry_result = None
            retry_verification = verify_work_order(
                turn_id=provisional.turn_id,
                work_order=work_order,
                observation=str(retry_observation or ""),
                elapsed_ms=(time.perf_counter() - retry_started) * 1000.0,
                extra_evidence=[
                    {
                        "type": "role_execution",
                        "role_id": role.role_id,
                        "adapter_kind": retry_result.evidence.get("strategy") if retry_result else "",
                        "adapter_elapsed_ms": retry_result.elapsed_ms if retry_result else None,
                        "adapter_ok": retry_result.ok if retry_result else False,
                        "adapter_evidence": retry_result.evidence if retry_result else {},
                        "recovery_retry": True,
                    }
                ],
            )
            mark_work_order_done(work_order, provisional.turn_id, ok=bool(retry_verification.ok))
            append_event(
                "recovery_execution_finished",
                provisional.turn_id,
                {
                    "recovery_id": recovery.recovery_id,
                    "strategy": recovery.strategy,
                    "role_id": role.role_id,
                    "work_order_id": work_order.work_order_id,
                    "tool": tool,
                    "ok": bool(retry_verification.ok),
                    "verification_id": retry_verification.verification_id,
                    "observation_preview": str(retry_observation or "")[:800],
                },
            )
            observation = retry_observation
            verification = retry_verification
    return DispatchResult(str(observation or ""), provisional, work_order, verification, recovery)


async def dispatch_existing_work_order(
    *,
    contract: DecisionContract,
    work_order: WorkOrder,
    executor: ToolExecutor,
    registry: RoleAgentRegistry | None = None,
    executor_registry: RoleExecutorRegistry | None = None,
) -> DispatchResult:
    """Execute a WorkOrder already issued by the Arbiter.

    This is the mainline path: ReviewBoard/Arbiter create the contract and
    WorkOrder first; Dispatcher only enforces role permissions and executes.
    """

    registry = registry or get_default_role_registry()
    executor_registry = executor_registry or get_default_role_executor_registry()
    tool = str(work_order.inputs.get("tool") or (contract.tool_policy.allowed_tools[0] if contract.tool_policy.allowed_tools else ""))
    action_input = _action_input_from_work_order(work_order)
    allowed, reason = registry.is_allowed(work_order.role_agent, tool, contract.risk_level)
    if not contract.execution_allowed:
        reason = contract.clarification_question or "DecisionContract does not allow execution"
        allowed = False
    if not allowed:
        contract.execution_allowed = False
        contract.clarification_question = reason
        contract.tool_policy.requires_confirmation = True
        contract.tool_policy.confirmation_reason = reason
        observation = blocked_confirmation_observation(contract)
        mark_work_order_done(work_order, contract.turn_id, ok=False)
        verification = verify_work_order(
            turn_id=contract.turn_id,
            work_order=work_order,
            observation=observation,
            elapsed_ms=0.0,
        )
        recovery = build_recovery_plan(turn_id=contract.turn_id, work_order=work_order, verification=verification)
        return DispatchResult(observation, contract, work_order, verification, recovery)

    started = time.perf_counter()
    mark_work_order_running(work_order, contract.turn_id)
    role_context = RoleExecutionContext(
        turn_id=contract.turn_id,
        goal=contract.goal,
        tool=tool,
        role_id=work_order.role_agent,
        action_input=action_input,
        metadata={
            "decision_id": contract.decision_id,
            "work_order_id": work_order.work_order_id,
            "selected_workflow": contract.selected_workflow,
            "risk_level": contract.risk_level.value,
            "mainline": True,
        },
    )
    role_result = None
    try:
        role_result = await executor_registry.execute(work_order.role_agent, work_order, executor, role_context)
        observation = role_result.observation
    except Exception as exc:
        observation = f"[mainline work order failed] {type(exc).__name__}: {exc}"
    verification = verify_work_order(
        turn_id=contract.turn_id,
        work_order=work_order,
        observation=str(observation or ""),
        elapsed_ms=(time.perf_counter() - started) * 1000.0,
        extra_evidence=[
            {
                "type": "role_execution",
                "role_id": work_order.role_agent,
                "adapter_kind": role_result.evidence.get("strategy") if role_result else "",
                "adapter_elapsed_ms": role_result.elapsed_ms if role_result else None,
                "adapter_ok": role_result.ok if role_result else False,
                "adapter_evidence": role_result.evidence if role_result else {},
                "mainline": True,
            }
        ],
    )
    mark_work_order_done(work_order, contract.turn_id, ok=bool(verification.ok))
    recovery = None if verification.ok else build_recovery_plan(
        turn_id=contract.turn_id,
        work_order=work_order,
        verification=verification,
    )
    if not verification.ok and _should_auto_recover(recovery, work_order.role_agent, contract.risk_level.value):
        append_event(
            "recovery_execution_started",
            contract.turn_id,
            {
                "recovery_id": recovery.recovery_id if recovery else "",
                "strategy": recovery.strategy if recovery else "",
                "role_id": work_order.role_agent,
                "work_order_id": work_order.work_order_id,
                "tool": tool,
                "rationale": recovery.rationale if recovery else "",
                "mainline": True,
            },
        )
        retry_started = time.perf_counter()
        mark_work_order_running(work_order, contract.turn_id)
        retry_result = None
        try:
            retry_result = await executor_registry.execute(work_order.role_agent, work_order, executor, role_context)
            retry_observation = retry_result.observation
        except Exception as exc:
            retry_observation = f"[mainline recovery retry failed] {type(exc).__name__}: {exc}"
        retry_verification = verify_work_order(
            turn_id=contract.turn_id,
            work_order=work_order,
            observation=str(retry_observation or ""),
            elapsed_ms=(time.perf_counter() - retry_started) * 1000.0,
            extra_evidence=[
                {
                    "type": "role_execution",
                    "role_id": work_order.role_agent,
                    "adapter_kind": retry_result.evidence.get("strategy") if retry_result else "",
                    "adapter_elapsed_ms": retry_result.elapsed_ms if retry_result else None,
                    "adapter_ok": retry_result.ok if retry_result else False,
                    "adapter_evidence": retry_result.evidence if retry_result else {},
                    "recovery_retry": True,
                    "mainline": True,
                }
            ],
        )
        mark_work_order_done(work_order, contract.turn_id, ok=bool(retry_verification.ok))
        append_event(
            "recovery_execution_finished",
            contract.turn_id,
            {
                "recovery_id": recovery.recovery_id if recovery else "",
                "strategy": recovery.strategy if recovery else "",
                "role_id": work_order.role_agent,
                "work_order_id": work_order.work_order_id,
                "tool": tool,
                "ok": bool(retry_verification.ok),
                "verification_id": retry_verification.verification_id,
                "observation_preview": str(retry_observation or "")[:800],
                "mainline": True,
            },
        )
        observation = retry_observation
        verification = retry_verification
    return DispatchResult(str(observation or ""), contract, work_order, verification, recovery)


def _should_auto_recover(recovery: RecoveryPlan | None, role_id: str, risk_level: str) -> bool:
    if recovery is None or recovery.strategy != "retry":
        return False
    if risk_level == "critical":
        return False
    # MessageExecutorAgent already has recipient-aware retry logic. Retrying it here could duplicate sends.
    return role_id in {"AppControlExecutorAgent", "FileExecutorAgent", "ToolExecutionAgent"}


def _action_input_from_work_order(work_order: WorkOrder) -> str:
    existing = work_order.inputs.get("action_input")
    if isinstance(existing, str) and existing.strip():
        return existing
    tool = str(work_order.inputs.get("tool") or "")
    target = work_order.inputs.get("target")
    target_name = ""
    if isinstance(target, dict):
        target_name = str(target.get("name") or target.get("app_name") or target.get("title") or "").strip()
    if tool == "mcp:windows_open_app":
        return __import__("json").dumps({"app_name": target_name, "args_json": "[]"}, ensure_ascii=False)
    if tool == "mcp:windows_window_switch":
        return __import__("json").dumps({"keywords": target_name, "timeout": 5.0}, ensure_ascii=False)
    if tool == "mcp:windows_window_close":
        return __import__("json").dumps({"keywords": target_name, "timeout": 5.0}, ensure_ascii=False)
    return __import__("json").dumps(work_order.inputs, ensure_ascii=False, default=str)
