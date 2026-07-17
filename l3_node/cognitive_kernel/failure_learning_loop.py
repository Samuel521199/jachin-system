"""Failure learning loop for recovery-aware execution."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any

from .contracts import DecisionContract, MemoryWriteRequest, VerificationReport, WorkOrder
from .ledger import append_event
from .memory_growth import append_raw_event


@dataclass(slots=True)
class FailureLearningRecord:
    failure_id: str
    task_type: str
    tool: str
    role_agent: str
    failure_reason: str
    failure_class: str
    attempt_count: int
    next_strategy: str
    memory_write: dict[str, Any]
    rationale: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_failure_reason(reason: str, evidence: list[dict[str, Any]] | None = None) -> str:
    text = " ".join([reason, *(str(item) for item in evidence or [])]).lower()
    if "tool_quality" in text or any(
        key in text
        for key in (
            "summary_placeholder_text",
            "summary_contains_web_noise",
            "summary_has_ellipsis_truncation",
            "summary_incomplete_sentence",
            "search_results_missing",
            "fetch_readable_content_missing",
        )
    ):
        return "tool_quality_failed"
    if any(key in text for key in ("not found", "missing target", "target_missing", "window_not_found", "recipient_not_found")):
        return "target_not_found"
    if any(key in text for key in ("permission", "denied", "unauthorized", "401", "403")):
        return "permission_required"
    if any(key in text for key in ("timeout", "timed out", "connection", "failed to fetch", "network")):
        return "timeout_or_connection"
    if any(key in text for key in ("focus", "foreground", "window", "app_focus_failed")):
        return "focus_or_window"
    if any(key in text for key in ("verification", "ocr", "evidence_missing", "post_send_verification_missing")):
        return "verification_missing"
    if any(key in text for key in ("invalid input", "missing slot", "requires recipient", "requires", "valueerror")):
        return "invalid_input"
    return "unknown"


def _strategy_for(failure_class: str, attempt_count: int) -> tuple[str, list[str]]:
    if attempt_count >= 5:
        return "final_report_with_recommendation", ["maximum recovery attempts reached"]
    if failure_class == "target_not_found":
        return "resolve_target_from_memory_or_ask_user", ["target resolution failed; use alias memory and capability candidates before retry"]
    if failure_class == "permission_required":
        return "ask_user_or_refresh_auth", ["permission failure should not be silently retried"]
    if failure_class == "timeout_or_connection":
        return "retry_with_longer_timeout_or_offline_path", ["transport failure can use backoff or an alternate local path"]
    if failure_class == "focus_or_window":
        return "switch_window_then_retry_with_visual_check", ["window focus failures need state repair before retry"]
    if failure_class == "verification_missing":
        return "collect_evidence_then_retry_or_mark_uncertain", ["action result is not trustworthy without verification evidence"]
    if failure_class == "tool_quality_failed":
        return "switch_to_higher_quality_path_or_regenerate_output", ["tool output failed quality gates; retry with better evidence, cleaner source, or stricter summarization"]
    if failure_class == "invalid_input":
        return "repair_slots_or_request_single_missing_field", ["input contract is incomplete or malformed"]
    return "inspect_evidence_then_retry_once", ["unknown failure needs evidence inspection before choosing a new path"]


def _tool_from_work_order(work_order: WorkOrder | dict[str, Any] | None) -> str:
    if not work_order:
        return ""
    data = work_order.to_dict() if isinstance(work_order, WorkOrder) else dict(work_order)
    inputs = dict(data.get("inputs") or {})
    return str(inputs.get("tool") or inputs.get("tool_id") or inputs.get("capability_id") or data.get("task") or "")


def _task_type(decision: DecisionContract | dict[str, Any] | None, work_order: WorkOrder | dict[str, Any] | None) -> str:
    if decision:
        data = decision.to_dict() if isinstance(decision, DecisionContract) else dict(decision)
        if data.get("task_type"):
            return str(data.get("task_type"))
    if work_order:
        data = work_order.to_dict() if isinstance(work_order, WorkOrder) else dict(work_order)
        return str(data.get("task") or "")
    return ""


def learn_from_failure(
    *,
    turn_id: str,
    decision: DecisionContract | dict[str, Any] | None = None,
    work_order: WorkOrder | dict[str, Any] | None = None,
    verification: VerificationReport | dict[str, Any] | None = None,
    attempt_count: int = 1,
) -> FailureLearningRecord:
    verification_data = verification.to_dict() if isinstance(verification, VerificationReport) else dict(verification or {})
    reason = str(verification_data.get("failure_reason") or verification_data.get("detail") or "unknown_failure")
    evidence = [dict(item) for item in verification_data.get("evidence") or [] if isinstance(item, dict)]
    failure_class = classify_failure_reason(reason, evidence)
    next_strategy, rationale = _strategy_for(failure_class, attempt_count)
    task_type = _task_type(decision, work_order)
    tool = _tool_from_work_order(work_order)
    role_agent = ""
    if work_order:
        work_data = work_order.to_dict() if isinstance(work_order, WorkOrder) else dict(work_order)
        role_agent = str(work_data.get("role_agent") or "")
    digest = hashlib.sha1(f"{turn_id}:{task_type}:{tool}:{reason}:{attempt_count}".encode("utf-8", errors="ignore")).hexdigest()[:12]
    memory_request = MemoryWriteRequest(
        turn_id=turn_id,
        source_event="failure_learning_loop",
        memory_type="failure_hint",
        content=f"{task_type or 'task'} failed via {tool or 'unknown_tool'}: {failure_class} / {reason}",
        evidence=[
            {
                "failure_class": failure_class,
                "failure_reason": reason,
                "tool": tool,
                "role_agent": role_agent,
                "attempt_count": attempt_count,
                "next_strategy": next_strategy,
            }
        ],
        confidence=0.66 if failure_class != "unknown" else 0.48,
        ttl="14d",
        requires_user_confirmation=False,
    )
    record = FailureLearningRecord(
        failure_id=f"failure_{digest}",
        task_type=task_type,
        tool=tool,
        role_agent=role_agent,
        failure_reason=reason,
        failure_class=failure_class,
        attempt_count=attempt_count,
        next_strategy=next_strategy,
        memory_write=memory_request.to_dict(),
        rationale=rationale,
    )
    append_event("failure_learning_recorded", turn_id, record.to_dict())
    _append_failure_learning_raw(turn_id=turn_id, record=record)
    return record


def _append_failure_learning_raw(*, turn_id: str, record: FailureLearningRecord) -> None:
    try:
        append_raw_event(
            category="evidence",
            source="failure_learning_loop",
            stream="failure_learning",
            payload={
                "turn_id": turn_id,
                "failure_learning": record.to_dict(),
            },
            source_refs=[
                {
                    "type": "cognitive_kernel_ledger",
                    "event_type": "failure_learning_recorded",
                    "turn_id": turn_id,
                },
                {
                    "type": "failure_learning",
                    "failure_id": record.failure_id,
                    "turn_id": turn_id,
                },
            ],
            review={
                "review_candidate": True,
                "promotion_targets": ["playbooks", "concepts"],
                "priority": "high",
                "reason": "failure_learning_to_experience_playbook",
            },
        )
    except Exception:
        append_event(
            "failure_learning_raw_append_failed",
            turn_id,
            {"failure_id": record.failure_id, "task_type": record.task_type, "tool": record.tool},
        )
