"""WorkOrder dispatcher for role-agent mediated tool execution."""

from __future__ import annotations

import time
from dataclasses import dataclass
import os
from typing import Awaitable, Callable

from .contracts import DecisionContract, MemoryWriteRequest, RecoveryPlan, VerificationReport, WorkOrder
from .failure_learning_loop import learn_from_failure
from .capability_governance_policy import governance_policy_from_work_order
from .memory_lifecycle import write_lifecycle_memory
from .recovery_planner import RecoveryAttemptRecord, RecoveryPlanner
from .runtime import (
    blocked_confirmation_observation,
    build_decision_contract,
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
    attempts: list[dict] | None = None
    final_failure_report: dict | None = None


async def dispatch_tool_work_order(
    *,
    turn_id: str,
    goal: str,
    tool: str,
    work_order_input: str,
    executor: ToolExecutor,
    registry: RoleAgentRegistry | None = None,
    executor_registry: RoleExecutorRegistry | None = None,
) -> DispatchResult:
    registry = registry or get_default_role_registry()
    executor_registry = executor_registry or get_default_role_executor_registry()
    risk = classify_tool_risk(tool, work_order_input)
    role = registry.select_for_tool(tool, work_order_input=work_order_input, risk=risk)
    provisional = build_decision_contract(
        turn_id=turn_id,
        goal=goal,
        tool=tool,
        work_order_input=work_order_input,
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
        work_order_input=work_order_input,
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
        recovery = RecoveryPlanner(max_attempts=1).initial_plan(
            contract=provisional,
            failed_work_order=work_order,
            verification=verification,
            attempt_no=1,
        )
        _record_failure_learning(contract=provisional, work_order=work_order, verification=verification, attempt_no=1)
        return DispatchResult(observation, provisional, work_order, verification, recovery)

    observation, verification, recovery, attempts, final_failure = await _execute_with_recovery(
        contract=provisional,
        work_order=work_order,
        executor=executor,
        registry=registry,
        executor_registry=executor_registry,
        goal=goal,
        mainline=False,
    )
    return DispatchResult(str(observation or ""), provisional, work_order, verification, recovery, attempts, final_failure)


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
    work_order_input = _work_order_input_from_work_order(work_order)
    allowed, reason = registry.is_allowed(work_order.role_agent, tool, contract.risk_level)
    governance_policy = governance_policy_from_work_order(work_order)
    if governance_policy.execution_mode == "manual_review" or governance_policy.requires_confirmation:
        allowed = False
        reason = governance_policy.reason or "capability health requires manual review"
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
        recovery = RecoveryPlanner(max_attempts=1).initial_plan(
            contract=contract,
            failed_work_order=work_order,
            verification=verification,
            attempt_no=1,
        )
        _record_failure_learning(contract=contract, work_order=work_order, verification=verification, attempt_no=1)
        return DispatchResult(observation, contract, work_order, verification, recovery)

    observation, verification, recovery, attempts, final_failure = await _execute_with_recovery(
        contract=contract,
        work_order=work_order,
        executor=executor,
        registry=registry,
        executor_registry=executor_registry,
        goal=contract.goal,
        mainline=True,
    )
    return DispatchResult(str(observation or ""), contract, work_order, verification, recovery, attempts, final_failure)


async def _execute_verified_work_order(
    *,
    contract: DecisionContract,
    work_order: WorkOrder,
    executor: ToolExecutor,
    executor_registry: RoleExecutorRegistry,
    goal: str,
    mainline: bool,
    recovery_attempt: int = 1,
    recovery_strategy: str = "initial",
) -> tuple[str, VerificationReport, float]:
    started = time.perf_counter()
    mark_work_order_running(work_order, contract.turn_id)
    tool = str(work_order.inputs.get("tool") or (contract.tool_policy.allowed_tools[0] if contract.tool_policy.allowed_tools else ""))
    work_order_input = _work_order_input_from_work_order(work_order)
    execution_preference = work_order.inputs.get("execution_preference")
    if not isinstance(execution_preference, dict):
        execution_preference = {}
    candidate_tool_reliability = work_order.inputs.get("candidate_tool_reliability")
    if not isinstance(candidate_tool_reliability, list):
        candidate_tool_reliability = []
    role_context = RoleExecutionContext(
        turn_id=contract.turn_id,
        goal=goal,
        tool=tool,
        role_id=work_order.role_agent,
        work_order_input=work_order_input,
        metadata={
            "decision_id": contract.decision_id,
            "work_order_id": work_order.work_order_id,
            "selected_workflow": contract.selected_workflow,
            "risk_level": contract.risk_level.value,
            "mainline": mainline,
            "recovery_attempt": recovery_attempt,
            "recovery_strategy": recovery_strategy,
            "execution_preference": execution_preference,
            "preferred_execution_strategy": execution_preference.get("preferred_execution_strategy") or "",
            "preferred_work_order_chain": execution_preference.get("preferred_work_order_chain") or [],
            "candidate_tool_reliability": candidate_tool_reliability,
            "selected_tool_reliability": _selected_tool_reliability(tool, candidate_tool_reliability),
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
                "mainline": mainline,
                "recovery_attempt": recovery_attempt,
                "recovery_strategy": recovery_strategy,
                "execution_preference": execution_preference,
                "candidate_tool_reliability": candidate_tool_reliability,
                "selected_tool_reliability": _selected_tool_reliability(tool, candidate_tool_reliability),
            }
        ],
    )
    mark_work_order_done(work_order, contract.turn_id, ok=bool(verification.ok))
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return str(observation or ""), verification, elapsed_ms


async def _execute_with_recovery(
    *,
    contract: DecisionContract,
    work_order: WorkOrder,
    executor: ToolExecutor,
    registry: RoleAgentRegistry,
    executor_registry: RoleExecutorRegistry,
    goal: str,
    mainline: bool,
) -> tuple[str, VerificationReport, RecoveryPlan | None, list[dict], dict | None]:
    planner = RecoveryPlanner(max_attempts=_recovery_budget())
    observation, verification, elapsed_ms = await _execute_verified_work_order(
        contract=contract,
        work_order=work_order,
        executor=executor,
        executor_registry=executor_registry,
        goal=goal,
        mainline=mainline,
        recovery_attempt=1,
        recovery_strategy="initial",
    )
    attempt_records = [
        _attempt_record(
            attempt_no=1,
            work_order=work_order,
            strategy="initial",
            rationale="initial execution path",
            verification=verification,
            observation=observation,
            elapsed_ms=elapsed_ms,
        )
    ]
    if not verification.ok:
        _record_failure_learning(contract=contract, work_order=work_order, verification=verification, attempt_no=1)
    recovery = None if verification.ok else planner.initial_plan(
        contract=contract,
        failed_work_order=work_order,
        verification=verification,
        attempt_no=1,
    )
    current_work_order = work_order
    while not verification.ok:
        next_plan = planner.next_attempt(
            contract=contract,
            failed_work_order=current_work_order,
            verification=verification,
            attempt_records=attempt_records,
        )
        if next_plan is None:
            break
        next_tool = str(next_plan.work_order.inputs.get("tool") or "")
        allowed, reason = registry.is_allowed(next_plan.work_order.role_agent, next_tool, contract.risk_level)
        append_event(
            "recovery_attempt_planned",
            contract.turn_id,
            {
                "attempt_no": next_plan.attempt_no,
                "strategy": next_plan.strategy,
                "role_id": next_plan.work_order.role_agent,
                "work_order_id": next_plan.work_order.work_order_id,
                "tool": next_tool,
                "allowed": bool(allowed),
                "blocked_reason": reason,
                "candidate_path": next_plan.candidate_path,
                "mainline": mainline,
            },
        )
        if not allowed:
            observation = f"[recovery blocked] {reason}"
            verification = verify_work_order(
                turn_id=contract.turn_id,
                work_order=next_plan.work_order,
                observation=observation,
                elapsed_ms=0.0,
            )
            mark_work_order_done(next_plan.work_order, contract.turn_id, ok=False)
            attempt_records.append(
                _attempt_record(
                    attempt_no=next_plan.attempt_no,
                    work_order=next_plan.work_order,
                    strategy=next_plan.strategy,
                    rationale=next_plan.rationale,
                    verification=verification,
                    observation=observation,
                    elapsed_ms=0.0,
                )
            )
            _record_failure_learning(
                contract=contract,
                work_order=next_plan.work_order,
                verification=verification,
                attempt_no=next_plan.attempt_no,
            )
            break
        append_event(
            "recovery_execution_started",
            contract.turn_id,
            {
                "recovery_id": recovery.recovery_id if recovery else "",
                "attempt_no": next_plan.attempt_no,
                "strategy": next_plan.strategy,
                "role_id": next_plan.work_order.role_agent,
                "work_order_id": next_plan.work_order.work_order_id,
                "tool": next_tool,
                "rationale": next_plan.rationale,
                "mainline": mainline,
            },
        )
        observation, verification, elapsed_ms = await _execute_verified_work_order(
            contract=contract,
            work_order=next_plan.work_order,
            executor=executor,
            executor_registry=executor_registry,
            goal=goal,
            mainline=mainline,
            recovery_attempt=next_plan.attempt_no,
            recovery_strategy=next_plan.strategy,
        )
        current_work_order = next_plan.work_order
        attempt_records.append(
            _attempt_record(
                attempt_no=next_plan.attempt_no,
                work_order=next_plan.work_order,
                strategy=next_plan.strategy,
                rationale=next_plan.rationale,
                verification=verification,
                observation=observation,
                elapsed_ms=elapsed_ms,
            )
        )
        append_event(
            "recovery_execution_finished",
            contract.turn_id,
            {
                "recovery_id": recovery.recovery_id if recovery else "",
                "attempt_no": next_plan.attempt_no,
                "strategy": next_plan.strategy,
                "role_id": next_plan.work_order.role_agent,
                "work_order_id": next_plan.work_order.work_order_id,
                "tool": next_tool,
                "ok": bool(verification.ok),
                "verification_id": verification.verification_id,
                "observation_preview": str(observation or "")[:800],
                "mainline": mainline,
            },
        )
        if verification.ok:
            break
        _record_failure_learning(
            contract=contract,
            work_order=next_plan.work_order,
            verification=verification,
            attempt_no=next_plan.attempt_no,
        )

    final_failure = None
    if not verification.ok:
        final_failure = planner.final_failure_report(
            contract=contract,
            attempt_records=attempt_records,
            last_verification=verification,
        )
        if recovery is not None:
            recovery.final_failure_report = final_failure
        append_event("final_failure_report", contract.turn_id, final_failure)
    if recovery is not None:
        recovery.max_attempts = planner.max_attempts
    return observation, verification, recovery, [x.to_dict() for x in attempt_records], final_failure


def _attempt_record(
    *,
    attempt_no: int,
    work_order: WorkOrder,
    strategy: str,
    rationale: str,
    verification: VerificationReport,
    observation: str,
    elapsed_ms: float | None,
) -> RecoveryAttemptRecord:
    return RecoveryAttemptRecord(
        attempt_no=attempt_no,
        work_order_id=work_order.work_order_id,
        role_agent=work_order.role_agent,
        tool=str(work_order.inputs.get("tool") or ""),
        strategy=strategy,
        rationale=rationale,
        ok=bool(verification.ok),
        verification_id=verification.verification_id,
        failure_reason=verification.failure_reason,
        observation_preview=str(observation or "")[:800],
        elapsed_ms=elapsed_ms,
    )


def _record_failure_learning(
    *,
    contract: DecisionContract,
    work_order: WorkOrder,
    verification: VerificationReport,
    attempt_no: int,
) -> None:
    try:
        record = learn_from_failure(
            turn_id=contract.turn_id,
            decision=contract,
            work_order=work_order,
            verification=verification,
            attempt_count=attempt_no,
        )
        write_lifecycle_memory(MemoryWriteRequest(**record.memory_write))
    except Exception as exc:
        append_event(
            "failure_learning_write_failed",
            contract.turn_id,
            {
                "work_order_id": work_order.work_order_id,
                "verification_id": verification.verification_id,
                "attempt_no": attempt_no,
                "error": f"{type(exc).__name__}: {exc}",
            },
        )


def _selected_tool_reliability(tool: str, rows: list[dict]) -> dict:
    for row in rows or []:
        if isinstance(row, dict) and str(row.get("tool") or "") == tool:
            return dict(row)
    return {}


def _recovery_budget() -> int:
    raw = os.getenv("JACHIN_RECOVERY_MAX_ATTEMPTS", "").strip()
    try:
        return max(1, min(8, int(raw))) if raw else 5
    except Exception:
        return 5


def _work_order_input_from_work_order(work_order: WorkOrder) -> str:
    existing = work_order.inputs.get("work_order_input")
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
