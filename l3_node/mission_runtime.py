"""Mission runtime helpers for plan preview, retry policy, and run metrics.

The runtime stays above concrete Windows/Lark/Codex skills. It decides what a
mission is about, what evidence should be expected, and whether a failed
attempt is safe to retry.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from l3_node.mission_intent_schema import CapabilityRoute, MissionIntent, MissionRiskLevel, MissionTaskType


@dataclass
class MissionPlanStep:
    stage: str
    action: str
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MissionPlanPreview:
    summary: str
    risk_level: str
    auto_execute: bool
    requires_confirmation: bool
    confirmation_reason: str = ""
    apps: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    recipients: list[str] = field(default_factory=list)
    steps: list[MissionPlanStep] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["steps"] = [step.to_dict() for step in self.steps]
        return data


@dataclass
class RetryDecision:
    should_retry: bool
    reason: str
    max_attempts: int = 1
    safe_to_retry: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_plan_preview(intent: MissionIntent, route: CapabilityRoute) -> MissionPlanPreview:
    slots = intent.slots
    steps: list[MissionPlanStep] = []
    apps: list[str] = []
    files: list[str] = []
    recipients = list(slots.recipients)
    risk = intent.risk_level.value if isinstance(intent.risk_level, MissionRiskLevel) else str(intent.risk_level)

    if intent.task_type == MissionTaskType.PROJECT_BRIEFING_DELIVERY:
        apps = ["Codex", "Lark"]
        if slots.project_path:
            files.append(slots.project_path)
        steps = [
            MissionPlanStep("resolve_project", "?????/?????????", ["project_path", "project_memory"]),
            MissionPlanStep("open_codex", "????? Codex ?????????", ["window_title", "prompt"]),
            MissionPlanStep("wait_codex", "?? Codex ???????", ["clipboard_text", "validation"]),
            MissionPlanStep("open_lark", "????? Lark???????", ["recipient_visible", "screenshot"]),
            MissionPlanStep("send_lark", "???????????/OCR??", ["message_visible", "ocr_preview"]),
        ]
        summary = "Use Codex to summarize the project, then send the verified result to Lark."
    elif intent.task_type == MissionTaskType.LARK_MESSAGE_SEND:
        apps = ["Lark"]
        steps = [
            MissionPlanStep("open_lark", "????? Lark???????", ["recipient_visible", "screenshot"]),
            MissionPlanStep("send_lark", "???????????/OCR??", ["message_visible", "ocr_preview"]),
        ]
        summary = "Send a user-provided message through the verified Lark workflow."
    elif intent.task_type == MissionTaskType.CODEX_ASK_LARK_SEND:
        apps = ["Codex", "Lark"]
        steps = [
            MissionPlanStep("open_codex", "????? Codex", ["active_window", "screenshot"]),
            MissionPlanStep("ask_codex", "? Codex ???????", ["prompt", "codex_reply"]),
            MissionPlanStep("validate_reply", "?? Codex ?????????????", ["validation"]),
            MissionPlanStep("send_lark", "? Codex ????? Lark ???", ["recipient_visible", "message_visible", "ocr_preview"]),
        ]
        summary = "Ask Codex a question, then send its verified reply through Lark."
    elif intent.task_type == MissionTaskType.CALCULATOR_CALCULATE:
        apps = ["Calculator"]
        steps = [
            MissionPlanStep("open_calculator", "?? Windows ?????", ["active_window", "screenshot"]),
            MissionPlanStep("calculate", "??????????", ["expression", "result", "ocr_preview"]),
        ]
        summary = "Use Windows Calculator to compute and verify an arithmetic expression."
    elif intent.task_type == MissionTaskType.APP_CONTROL:
        apps = [slots.app_name] if slots.app_name else []
        steps = [MissionPlanStep("open_app", "??????? App ???????", ["active_window", "screenshot"])]
        summary = "Open or focus a Windows application."
    elif intent.task_type == MissionTaskType.FILE_TO_APP:
        apps = [slots.app_name] if slots.app_name else []
        files = [slots.file_path] if slots.file_path else []
        risk = MissionRiskLevel.MEDIUM.value
        steps = [
            MissionPlanStep("resolve_file", "?????????", ["file_path", "file_stat"]),
            MissionPlanStep("attach_file", "???? App ???/????", ["upload_target", "screenshot"]),
            MissionPlanStep("verify_file", "???? App ???????", ["filename_visible", "ocr_preview"]),
        ]
        summary = "Bridge a local file into a target application."
    elif intent.task_type == MissionTaskType.SYSTEM_STATUS_REPORT:
        steps = [
            MissionPlanStep("read_system", "???????????????", ["system_status"]),
            MissionPlanStep("write_report", "?????? evidence", ["evidence_json"]),
        ]
        summary = "Read Windows system status and write an evidence report."
    elif intent.task_type == MissionTaskType.PROJECT_MEMORY_UPDATE:
        if slots.project_path:
            files.append(slots.project_path)
        steps = [MissionPlanStep("remember_project", "????????????", ["memory_path", "project_path"])]
        summary = "Remember a project name and local path for future missions."
    else:
        steps = [MissionPlanStep("route_unknown", "??????????", ["intent"])]
        summary = "Unknown mission."

    requires_confirmation = risk == MissionRiskLevel.HIGH.value
    confirmation_reason = "high_risk_operation" if requires_confirmation else ""
    return MissionPlanPreview(
        summary=summary,
        risk_level=risk,
        auto_execute=not requires_confirmation,
        requires_confirmation=requires_confirmation,
        confirmation_reason=confirmation_reason,
        apps=apps,
        files=files,
        recipients=recipients,
        steps=steps,
    )


def _mission_result_deliveries(result: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
    deliveries = evidence.get("deliveries") or result.get("deliveries") or []
    return [d for d in deliveries if isinstance(d, dict)] if isinstance(deliveries, list) else []


def classify_failure(intent: MissionIntent, route: CapabilityRoute, result: dict[str, Any]) -> str:
    if result.get("ok"):
        return "none"
    detail = str(result.get("detail") or "").lower()
    raw = str(result).lower()
    deliveries = _mission_result_deliveries(result)
    if "nameerror" in raw and ("not defined" in raw or "_choose_codex_generic_reply" in raw):
        return "workflow_code_defect"
    if "mouse_failsafe_triggered" in detail or "failsafeexception" in raw or "fail-safe" in raw:
        return "mouse_failsafe_triggered"
    if "project_path_required" in detail or "project_path_required_first_time" in raw:
        return "missing_project_path"
    if "wrong_recipient" in detail or "wrong_recipient" in raw:
        return "wrong_recipient"
    if "wrong_foreground_app" in detail or "wrong_foreground_app" in raw:
        return "wrong_foreground_app"
    if "app_executable_not_found" in detail or "app_executable_not_found" in raw or "filenotfounderror" in raw:
        return "app_executable_not_found"
    if "app_launch_failed" in detail or "app_launch_failed" in raw:
        return "app_launch_failed"
    if "app_focus_failed" in detail or "app_focus_failed" in raw:
        return "app_focus_failed"
    if "codex_open_failed" in detail or ("codex" in detail and "open" in detail):
        return "codex_open_failed"
    if "lark_open_failed" in detail or ("lark" in detail and "open" in detail):
        return "lark_open_failed"
    if "draft_preview_verification_failed" in detail:
        return "message_preview_not_verified"
    if "sent_but_post_verification_failed" in detail:
        return "message_post_send_not_verified"
    for delivery in deliveries:
        failure_stage = str(delivery.get("failure_stage") or "").lower()
        if failure_stage == "message_preview_verification_failed":
            return "message_preview_not_verified"
        if failure_stage == "post_send_verification_failed":
            return "message_post_send_not_verified"
        attempts = delivery.get("attempts") if isinstance(delivery.get("attempts"), list) else []
        for attempt in attempts:
            if not isinstance(attempt, dict):
                continue
            if attempt.get("preview_recipient_visible") is True and attempt.get("preview_message_visible") is False:
                return "message_preview_not_verified"
        if delivery.get("preview_verified") is True and delivery.get("message_visible") is False:
            return "message_post_send_not_verified"
    for delivery in deliveries:
        failure_stage = str(delivery.get("failure_stage") or "").lower()
        if failure_stage == "recipient_preview_verification_failed" or delivery.get("recipient_visible") is False:
            return "recipient_not_verified"
    if "message_visible" in raw and "false" in raw:
        return "message_not_verified"
    if "recipient" in raw and ("not" in raw or "false" in raw):
        return "recipient_not_verified"
    if "validation" in raw and "false" in raw:
        return "output_validation_failed"
    if "unsupported_route" in detail or not route.ok:
        return "route_unavailable"
    return "workflow_failed"


def decide_retry(intent: MissionIntent, route: CapabilityRoute, result: dict[str, Any]) -> RetryDecision:
    failure = classify_failure(intent, route, result)
    if failure == "none":
        return RetryDecision(False, "success", 1, False)
    if failure == "mouse_failsafe_triggered":
        safe_tasks = {MissionTaskType.APP_CONTROL, MissionTaskType.SYSTEM_STATUS_REPORT, MissionTaskType.CALCULATOR_CALCULATE}
        if intent.task_type in safe_tasks:
            return RetryDecision(True, failure, 2, True)
        evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
        if evidence.get("side_effect_status") == "no_side_effect_started":
            return RetryDecision(True, failure, 2, True)
        return RetryDecision(False, failure, 1, False)
    if failure == "workflow_code_defect":
        return RetryDecision(False, failure, 1, False)
    if failure in {"missing_project_path", "route_unavailable", "output_validation_failed", "app_executable_not_found", "app_launch_failed"}:
        return RetryDecision(False, failure, 1, False)
    if intent.task_type in {MissionTaskType.APP_CONTROL, MissionTaskType.SYSTEM_STATUS_REPORT}:
        return RetryDecision(True, failure, 2, True)
    if failure in {"codex_open_failed", "lark_open_failed", "app_focus_failed", "wrong_foreground_app", "wrong_recipient", "recipient_not_verified", "message_preview_not_verified"}:
        return RetryDecision(True, failure, 2, True)
    return RetryDecision(False, failure, 1, False)


def execute_with_retry(
    *,
    intent: MissionIntent,
    route: CapabilityRoute,
    execute_once: Callable[[], str],
    parse_result: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    started = time.time()
    attempts: list[dict[str, Any]] = []
    attempt_no = 0
    max_attempts = 1
    final_text = ""
    final_data: dict[str, Any] = {}
    retry_decision = RetryDecision(False, "not_evaluated", 1, False)

    while attempt_no < max_attempts:
        attempt_no += 1
        attempt_started = time.time()
        text = execute_once()
        data = parse_result(text)
        final_text = text
        final_data = data
        retry_decision = decide_retry(intent, route, data)
        max_attempts = max(max_attempts, retry_decision.max_attempts)
        attempts.append(
            {
                "attempt": attempt_no,
                "ok": bool(data.get("ok")),
                "detail": str(data.get("detail") or data.get("task") or ""),
                "failure_class": classify_failure(intent, route, data),
                "duration_ms": int((time.time() - attempt_started) * 1000),
                "retry_decision": retry_decision.to_dict(),
            }
        )
        if data.get("ok") or not retry_decision.should_retry or attempt_no >= retry_decision.max_attempts:
            break
        time.sleep(0.6)

    duration_ms = int((time.time() - started) * 1000)
    return {
        "result_text": final_text,
        "result_data": final_data,
        "attempts": attempts,
        "retry": retry_decision.to_dict(),
        "metrics": {
            "duration_ms": duration_ms,
            "attempt_count": len(attempts),
            "final_ok": bool(final_data.get("ok")),
            "failure_class": classify_failure(intent, route, final_data),
            "workflow_id": route.workflow_id,
            "tool_id": route.tool_id,
            "task_type": intent.task_type.value,
        },
    }
