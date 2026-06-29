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
            MissionPlanStep("resolve_project", "解析项目名/路径并读取项目记忆", ["project_path", "project_memory"]),
            MissionPlanStep("open_codex", "打开或切换 Codex 并输入项目总结任务", ["window_title", "prompt"]),
            MissionPlanStep("wait_codex", "等待 Codex 输出并复制结果", ["clipboard_text", "validation"]),
            MissionPlanStep("open_lark", "打开或切换 Lark，搜索发送对象", ["recipient_visible", "screenshot"]),
            MissionPlanStep("send_lark", "粘贴并发送消息，做视觉/OCR校验", ["message_visible", "ocr_preview"]),
        ]
        summary = "Use Codex to summarize the project, then send the verified result to Lark."
    elif intent.task_type == MissionTaskType.LARK_MESSAGE_SEND:
        apps = ["Lark"]
        steps = [
            MissionPlanStep("open_lark", "打开或切换 Lark，搜索发送对象", ["recipient_visible", "screenshot"]),
            MissionPlanStep("send_lark", "粘贴并发送消息，做视觉/OCR校验", ["message_visible", "ocr_preview"]),
        ]
        summary = "Send a user-provided message through the verified Lark workflow."
    elif intent.task_type == MissionTaskType.APP_CONTROL:
        apps = [slots.app_name] if slots.app_name else []
        steps = [MissionPlanStep("open_app", "打开或切换目标 App 并验证前台窗口", ["active_window", "screenshot"])]
        summary = "Open or focus a Windows application."
    elif intent.task_type == MissionTaskType.FILE_TO_APP:
        apps = [slots.app_name] if slots.app_name else []
        files = [slots.file_path] if slots.file_path else []
        risk = MissionRiskLevel.MEDIUM.value
        steps = [
            MissionPlanStep("resolve_file", "解析并确认目标文件", ["file_path", "file_stat"]),
            MissionPlanStep("attach_file", "打开目标 App 并附加/上传文件", ["upload_target", "screenshot"]),
            MissionPlanStep("verify_file", "校验目标 App 中显示的文件名", ["filename_visible", "ocr_preview"]),
        ]
        summary = "Bridge a local file into a target application."
    elif intent.task_type == MissionTaskType.SYSTEM_STATUS_REPORT:
        steps = [
            MissionPlanStep("read_system", "读取磁盘、网络、进程和资源占用", ["system_status"]),
            MissionPlanStep("write_report", "生成系统状态 evidence", ["evidence_json"]),
        ]
        summary = "Read Windows system status and write an evidence report."
    elif intent.task_type == MissionTaskType.PROJECT_MEMORY_UPDATE:
        if slots.project_path:
            files.append(slots.project_path)
        steps = [MissionPlanStep("remember_project", "保存项目名到本机路径映射", ["memory_path", "project_path"])]
        summary = "Remember a project name and local path for future missions."
    else:
        steps = [MissionPlanStep("route_unknown", "无法构建明确执行计划", ["intent"])]
        summary = "Unknown mission."

    requires_confirmation = risk == MissionRiskLevel.HIGH.value
    return MissionPlanPreview(
        summary=summary,
        risk_level=risk,
        auto_execute=not requires_confirmation,
        requires_confirmation=requires_confirmation,
        confirmation_reason="high_risk_operation" if requires_confirmation else "",
        apps=apps,
        files=files,
        recipients=recipients,
        steps=steps,
    )


def classify_failure(intent: MissionIntent, route: CapabilityRoute, result: dict[str, Any]) -> str:
    if result.get("ok"):
        return "none"
    detail = str(result.get("detail") or "").lower()
    raw = str(result).lower()
    if "project_path_required" in detail or "project_path_required_first_time" in raw:
        return "missing_project_path"
    if "codex_open_failed" in detail or "codex" in detail and "open" in detail:
        return "codex_open_failed"
    if "lark_open_failed" in detail or "lark" in detail and "open" in detail:
        return "lark_open_failed"
    if "recipient" in raw and ("not" in raw or "false" in raw):
        return "recipient_not_verified"
    if "message_visible" in raw and "false" in raw:
        return "message_not_verified"
    if "validation" in raw and "false" in raw:
        return "output_validation_failed"
    if "unsupported_route" in detail or not route.ok:
        return "route_unavailable"
    return "workflow_failed"


def decide_retry(intent: MissionIntent, route: CapabilityRoute, result: dict[str, Any]) -> RetryDecision:
    failure = classify_failure(intent, route, result)
    if failure == "none":
        return RetryDecision(False, "success", 1, False)
    if failure in {"missing_project_path", "route_unavailable", "output_validation_failed"}:
        return RetryDecision(False, failure, 1, False)
    if intent.task_type in {MissionTaskType.APP_CONTROL, MissionTaskType.SYSTEM_STATUS_REPORT}:
        return RetryDecision(True, failure, 2, True)
    if failure in {"codex_open_failed", "lark_open_failed", "recipient_not_verified"}:
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
