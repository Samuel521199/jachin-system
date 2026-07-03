"""Capability-aware routing for mission intents."""
from __future__ import annotations

import re

from l3_node.mission_intent_schema import CapabilityRoute, MissionIntent, MissionTaskType


def _tool_ids(tools: list[dict]) -> set[str]:
    out: set[str] = set()
    for item in tools or []:
        if not isinstance(item, dict):
            continue
        raw = str(item.get("id") or item.get("name") or "").strip()
        if raw:
            out.add(raw)
            if raw.startswith("mcp:"):
                out.add(raw[4:])
            else:
                out.add(f"mcp:{raw}")
    return out


def _allowed_ok(tool_id: str, allowed: list[str] | None) -> bool:
    if allowed is None:
        return True
    allowed_set = {str(x).strip() for x in allowed if str(x).strip()}
    return tool_id in allowed_set or tool_id.removeprefix("mcp:") in allowed_set


def _lark_send_sanity_issue(intent: MissionIntent) -> str:
    recipients = [str(x).strip() for x in intent.slots.recipients if str(x).strip()]
    if not recipients:
        return "lark_send_missing_recipient"
    if not str(intent.slots.message or "").strip():
        return "lark_send_missing_message"

    # Rules are no longer allowed to reject a valid semantic Lark-send intent
    # just because the utterance contains polite prefixes such as "open Lark".
    # They only guard against slots that are clearly not people/groups.
    suspicious_terms = (
        r"\u6253\u5f00|\u542f\u52a8|\u8fd0\u884c|\u5207\u6362|\u8ba1\u7b97|"
        r"\u6d4f\u89c8\u5668|\u8ba1\u7b97\u5668|\u539f\u751f|\u672c\u5730|"
        r"windows|calculator|browser|codex|open|launch|run|calculate|"
        r"\u6253\u5f00|\u542f\u52a8|\u8fd0\u884c|\u8ba1\u7b97|\u6d4f\u89c8\u5668|\u8ba1\u7b97\u5668"
    )
    if any(re.search(suspicious_terms, r, re.I) for r in recipients):
        return "recipient_looks_like_local_task"
    return ""

_LOCAL_OS_WORKFLOW_TOOL_IDS = {
    "mcp:windows_lark_send_message",
    "mcp:windows_codex_ask_lark_send",
    "mcp:windows_calculator_calculate",
    "mcp:windows_file_bridge_to_app",
    "mcp:windows_open_app",
    "mcp:windows_system_status",
    "mcp:windows_workspace_report",
}


def choose_capability_route(intent: MissionIntent, tools: list[dict], allowed: list[str] | None = None) -> CapabilityRoute:
    ids = _tool_ids(tools)

    def available(tool_id: str) -> bool:
        return tool_id in ids and _allowed_ok(tool_id, allowed)

    def available_or_local(tool_id: str) -> bool:
        return available(tool_id) or (tool_id in _LOCAL_OS_WORKFLOW_TOOL_IDS and _allowed_ok(tool_id, allowed))

    if intent.task_type == MissionTaskType.PROJECT_BRIEFING_DELIVERY:
        tool_id = "mcp:windows_codex_lark_workflow_template"
        if available(tool_id):
            return CapabilityRoute(
                ok=True,
                tool_id=tool_id,
                workflow_id="codex_project_briefing_to_lark",
                reason="project briefing delivery must use Codex -> Lark OS workflow",
                required_slots=["project", "recipients"],
                missing_slots=list(intent.missing_slots),
            )
        return CapabilityRoute(
            ok=False,
            tool_id=tool_id,
            workflow_id="codex_project_briefing_to_lark",
            reason="required Codex -> Lark workflow tool is not available",
            required_slots=["project", "recipients"],
            missing_slots=list(intent.missing_slots),
        )

    if intent.task_type == MissionTaskType.CODEX_ASK_LARK_SEND:
        tool_id = "mcp:windows_codex_ask_lark_send"
        return CapabilityRoute(
            ok=available_or_local(tool_id),
            tool_id=tool_id,
            workflow_id="codex_ask_lark_send",
            reason="multi-step Codex question then Lark delivery should use the composed OS workflow",
            required_slots=["feature_query", "recipients"],
            missing_slots=list(intent.missing_slots),
        )

    if intent.task_type == MissionTaskType.PROJECT_MEMORY_UPDATE:
        tool_id = "mcp:windows_project_remember" if available("mcp:windows_project_remember") else "local:project_memory"
        return CapabilityRoute(
            ok=True,
            tool_id=tool_id,
            workflow_id="windows_project_memory_update",
            reason="project path memory should use the OS project memory skill or local memory fallback",
            required_slots=["project_name", "project_path"],
            missing_slots=list(intent.missing_slots),
        )

    if intent.task_type == MissionTaskType.LARK_MESSAGE_SEND:
        tool_id = "mcp:windows_lark_send_message"
        sanity_issue = _lark_send_sanity_issue(intent)
        if sanity_issue:
            return CapabilityRoute(
                ok=False,
                tool_id=tool_id,
                workflow_id="windows_lark_message_send",
                reason=sanity_issue,
                required_slots=["recipients", "message"],
                missing_slots=list(intent.missing_slots),
            )
        return CapabilityRoute(
            ok=available_or_local(tool_id),
            tool_id=tool_id,
            workflow_id="windows_lark_message_send",
            reason="Lark message should use Windows UI verified send workflow",
            required_slots=["recipients", "message"],
            missing_slots=list(intent.missing_slots),
        )

    if intent.task_type == MissionTaskType.CALCULATOR_CALCULATE:
        tool_id = "mcp:windows_calculator_calculate"
        return CapabilityRoute(
            ok=available_or_local(tool_id),
            tool_id=tool_id,
            workflow_id="windows_calculator_calculate",
            reason="calculator arithmetic should use the verified Windows Calculator workflow",
            required_slots=["expression"],
            missing_slots=list(intent.missing_slots),
        )

    if intent.task_type == MissionTaskType.FILE_TO_APP:
        tool_id = "mcp:windows_file_bridge_to_app"
        return CapabilityRoute(
            ok=available_or_local(tool_id),
            tool_id=tool_id,
            workflow_id="windows_file_to_app_bridge",
            reason="file-to-app tasks should use the OS file bridge workflow",
            required_slots=["file_path", "app_name"],
            missing_slots=list(intent.missing_slots),
        )

    if intent.task_type == MissionTaskType.APP_CONTROL:
        tool_id = "mcp:windows_open_app"
        return CapabilityRoute(
            ok=available_or_local(tool_id),
            tool_id=tool_id,
            workflow_id="windows_app_control",
            reason="app launch/switch should use the generic Windows app tool",
            required_slots=["app_name"],
            missing_slots=list(intent.missing_slots),
        )

    if intent.task_type == MissionTaskType.SYSTEM_STATUS_REPORT:
        preferred = "mcp:windows_system_status"
        fallback = "mcp:windows_workspace_report"
        tool_id = preferred if available_or_local(preferred) else fallback
        return CapabilityRoute(
            ok=available_or_local(tool_id),
            tool_id=tool_id,
            workflow_id="windows_system_status_report",
            reason="system status should use Windows OS sensing tools",
            required_slots=[],
            missing_slots=list(intent.missing_slots),
        )

    return CapabilityRoute(ok=False, reason="unknown mission intent")
