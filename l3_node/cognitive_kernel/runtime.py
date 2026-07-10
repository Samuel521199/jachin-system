"""Decision, WorkOrder, verification, recovery, and closure helpers."""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any

from .contracts import (
    ClosureType,
    DecisionContract,
    MemoryWriteRequest,
    RecoveryPlan,
    RiskLevel,
    ToolPolicy,
    TurnClosure,
    VerificationReport,
    WorkOrder,
)
from .ledger import (
    record_decision,
    record_recovery,
    record_turn_closure,
    record_verification,
    record_work_order,
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time())}_{uuid.uuid4().hex[:10]}"


_CRITICAL_PATTERNS = (
    "delete",
    "remove",
    "rm ",
    "rmdir",
    "format",
    "drop table",
    "truncate table",
    "wipe",
    "kill",
    "terminate_process",
)
_HIGH_PATTERNS = (
    "send",
    "submit",
    "publish",
    "upload",
    "move",
    "rename",
    "overwrite",
    "apply_patch",
    "fs_write",
    "write_file",
    "edit_file",
    "create_file",
    "shell_exec",
)
_LOW_PATTERNS = (
    "read",
    "list",
    "search",
    "find",
    "status",
    "health",
    "query",
    "get_",
    "inspect",
)


def classify_tool_risk(tool: str, work_order_input: str = "") -> RiskLevel:
    hay = f"{tool or ''}\n{work_order_input or ''}".lower()
    if any(p in hay for p in _CRITICAL_PATTERNS):
        return RiskLevel.CRITICAL
    if any(p in hay for p in _HIGH_PATTERNS):
        return RiskLevel.HIGH
    if any(p in hay for p in _LOW_PATTERNS):
        return RiskLevel.LOW
    return RiskLevel.MEDIUM


def confirmation_required(risk: RiskLevel) -> bool:
    if str(risk.value) == RiskLevel.CRITICAL.value:
        val = str(__import__("os").environ.get("JACHIN_ALLOW_CRITICAL_WITHOUT_CONFIRM", "")).strip().lower()
        return val not in {"1", "true", "yes", "on"}
    return False


def build_decision_contract(
    *,
    turn_id: str,
    goal: str,
    tool: str,
    work_order_input: str,
    role_agent: str = "ToolExecutionAgent",
) -> DecisionContract:
    risk = classify_tool_risk(tool, work_order_input)
    requires_confirmation = confirmation_required(risk)
    policy = ToolPolicy(
        allowed_tools=[tool] if tool else [],
        denied_tools=[],
        risk_level=risk,
        requires_confirmation=requires_confirmation,
        confirmation_reason="critical external-world operation" if requires_confirmation else "",
        verification_required=True,
    )
    contract = DecisionContract(
        decision_id=_new_id("decision"),
        turn_id=turn_id,
        task_type="tool_execution",
        goal=(goal or "").strip()[:1000],
        selected_workflow="work_order_role_dispatcher",
        selected_roles=[role_agent, "VerificationAgent", "RecoveryAgent", "TurnClosureAgent"],
        risk_level=risk,
        tool_policy=policy,
        execution_allowed=not requires_confirmation,
        clarification_question="Please confirm this critical operation before execution." if requires_confirmation else "",
        verification_criteria=[
            "tool returned without process exception",
            "observation does not contain a clear failure marker",
            "side-effect tools must provide observable evidence when available",
        ],
        rationale=[
            "Parsed tool intent was converted into a Cognitive Kernel DecisionContract.",
            f"Risk classified as {risk.value} from tool id and tool input.",
        ],
    )
    record_decision(contract)
    return contract


def build_work_order(
    *,
    contract: DecisionContract,
    tool: str,
    work_order_input: str,
    role_agent: str = "ToolExecutionAgent",
) -> WorkOrder:
    wo = WorkOrder(
        work_order_id=_new_id("work"),
        decision_id=contract.decision_id,
        role_agent=role_agent,
        task=f"Execute tool {tool}",
        inputs={"tool": tool, "work_order_input": work_order_input},
        tool_policy=contract.tool_policy,
        expected_outputs=["observation", "evidence"],
        verification_criteria=contract.verification_criteria,
        status="pending",
    )
    record_work_order(wo, contract.turn_id)
    return wo


def mark_work_order_running(work_order: WorkOrder, turn_id: str) -> WorkOrder:
    work_order.status = "running"
    record_work_order(work_order, turn_id)
    return work_order


def mark_work_order_done(work_order: WorkOrder, turn_id: str, ok: bool) -> WorkOrder:
    work_order.status = "done" if ok else "failed"
    record_work_order(work_order, turn_id)
    return work_order


def _looks_failed(observation: str) -> tuple[bool, str]:
    text = str(observation or "")
    low = text.lower()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            if obj.get("ok") is True or obj.get("success") is True:
                return False, ""
            if obj.get("ok") is False or obj.get("success") is False:
                return True, str(obj.get("error") or obj.get("reason") or "json_false")
            if obj.get("status") in {"failed", "error"}:
                return True, str(obj.get("error") or obj.get("status"))
    except Exception:
        pass
    markers = [
        "traceback",
        "exception",
        "error",
        "failed",
        "timeout",
        "not allowed",
        "permission denied",
        "connection refused",
        "unknown tool",
        "未知工具",
        "未知 wasm 技能",
        "未找到技能",
        "无法",
        "失败",
        "错误",
        "超时",
        "拒绝",
    ]
    for marker in markers:
        if marker in low or marker in text:
            return True, marker
    return False, ""


def _looks_failed_from_evidence(extra_evidence: list[dict[str, Any]]) -> tuple[bool, str]:
    for item in extra_evidence or []:
        if not isinstance(item, dict) or item.get("type") != "role_execution":
            continue
        adapter_evidence = item.get("adapter_evidence") if isinstance(item.get("adapter_evidence"), dict) else {}
        adapter_reason = ""
        if isinstance(adapter_evidence, dict):
            result = adapter_evidence.get("app_control_result")
            if isinstance(result, dict):
                adapter_reason = str(result.get("reason") or "")
        if item.get("adapter_ok") is False and adapter_reason:
            return True, adapter_reason
        if isinstance(adapter_evidence, dict):
            if adapter_evidence.get("strategy") == "app_control" and adapter_evidence.get("foreground_verified") is False:
                return True, "app_control_foreground_unverified"
        if item.get("adapter_ok") is False:
            role_id = str(item.get("role_id") or "RoleExecutionAgent")
            return True, f"{role_id}_adapter_failed"
    return False, ""


def verify_work_order(
    *,
    turn_id: str,
    work_order: WorkOrder,
    observation: str,
    elapsed_ms: float | None = None,
    extra_evidence: list[dict[str, Any]] | None = None,
) -> VerificationReport:
    failed, reason = _looks_failed(observation)
    evidence_items = list(extra_evidence or [])
    evidence_failed, evidence_reason = _looks_failed_from_evidence(evidence_items)
    if evidence_failed:
        failed = True
        reason = evidence_reason or reason
    ok = not failed
    report = VerificationReport(
        verification_id=_new_id("verify"),
        work_order_id=work_order.work_order_id,
        ok=ok,
        evidence=[
            {
                "type": "tool_observation",
                "elapsed_ms": elapsed_ms,
                "preview": str(observation or "")[:1200],
                "length": len(str(observation or "")),
            }
        ]
        + evidence_items,
        confidence=0.82 if ok else 0.45,
        failure_reason=reason,
    )
    record_verification(report, turn_id)
    return report


def build_recovery_plan(
    *,
    turn_id: str,
    work_order: WorkOrder,
    verification: VerificationReport,
) -> RecoveryPlan | None:
    if verification.ok:
        return None
    reason = verification.failure_reason.lower()
    strategy = "degrade"
    retry_markers = (
        "timeout",
        "connection",
        "not ready",
        "not found",
        "window_not_found",
        "window_close_unverified",
        "window_switch_unverified",
        "unverified",
        "temporarily",
        "busy",
        "focus",
        "超时",
    )
    if any(marker in reason for marker in retry_markers):
        strategy = "retry"
    elif "not allowed" in reason or "permission" in reason or "拒绝" in reason:
        strategy = "ask_user"
    plan = RecoveryPlan(
        recovery_id=_new_id("recovery"),
        turn_id=turn_id,
        failed_work_order_id=work_order.work_order_id,
        strategy=strategy,  # type: ignore[arg-type]
        rationale=verification.failure_reason or "tool observation failed kernel verification",
    )
    record_recovery(plan)
    return plan


def close_turn(
    *,
    turn_id: str,
    final_text: str,
    executed_work_orders: list[str] | None = None,
    verification_reports: list[VerificationReport] | None = None,
    aborted: bool = False,
) -> TurnClosure:
    reports = verification_reports or []
    failed = [r for r in reports if not r.ok]
    closure_type = ClosureType.ANSWERED
    if aborted:
        closure_type = ClosureType.FAILED_RECOVERABLE
    elif failed:
        closure_type = ClosureType.FAILED_RECOVERABLE
    elif executed_work_orders:
        closure_type = ClosureType.COMPLETED
    status = "not_required"
    if reports:
        status = "failed" if failed else "passed"
    memory_write_requests: list[MemoryWriteRequest] = []
    if executed_work_orders:
        memory_write_requests.append(
            MemoryWriteRequest(
                turn_id=turn_id,
                source_event="turn_closure",
                memory_type="short_term_action",
                content=json.dumps(
                    {
                        "executed_work_orders": list(executed_work_orders or []),
                        "verification_status": status,
                        "final_user_message_intent": str(final_text or "")[:300],
                    },
                    ensure_ascii=False,
                ),
                evidence=[
                    {
                        "type": "verification_summary",
                        "status": status,
                        "failed_count": len(failed),
                        "report_count": len(reports),
                    }
                ],
                confidence=0.86 if not failed else 0.55,
                ttl="short_term",
                merge_policy="append_action_chain",
            )
        )
    closure = TurnClosure(
        turn_id=turn_id,
        closure_type=closure_type,
        final_user_message_intent=str(final_text or "")[:800],
        executed_work_orders=list(executed_work_orders or []),
        verification_status=status,
        memory_write_requests=memory_write_requests,
        next_turn_hints=[] if not failed else ["inspect recovery_plan events in the Cognitive Kernel ledger"],
    )
    record_turn_closure(closure)
    return closure


def close_turn_waiting_user(
    *,
    turn_id: str,
    final_text: str,
    pending_decision: dict[str, Any] | None = None,
    next_turn_hints: list[str] | None = None,
) -> TurnClosure:
    closure = TurnClosure(
        turn_id=turn_id,
        closure_type=ClosureType.WAITING_USER,
        final_user_message_intent=str(final_text or "")[:800],
        executed_work_orders=[],
        verification_status="not_required",
        pending_decision=pending_decision or None,
        memory_write_requests=[],
        next_turn_hints=list(next_turn_hints or ["reply confirm to resume pending DecisionContract"]),
    )
    record_turn_closure(closure)
    return closure


def blocked_confirmation_observation(contract: DecisionContract) -> str:
    return json.dumps(
        {
            "ok": False,
            "blocked_by": "cognitive_kernel_decision_contract",
            "requires_confirmation": True,
            "risk_level": contract.risk_level.value,
            "question": contract.clarification_question,
        },
        ensure_ascii=False,
    )




