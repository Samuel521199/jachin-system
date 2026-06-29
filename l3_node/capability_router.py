"""Capability-aware routing for mission intents."""
from __future__ import annotations

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


def choose_capability_route(intent: MissionIntent, tools: list[dict], allowed: list[str] | None = None) -> CapabilityRoute:
    ids = _tool_ids(tools)

    def available(tool_id: str) -> bool:
        return tool_id in ids and _allowed_ok(tool_id, allowed)

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
        return CapabilityRoute(
            ok=available(tool_id),
            tool_id=tool_id,
            workflow_id="windows_lark_message_send",
            reason="Lark message should use Windows UI verified send workflow",
            required_slots=["recipients", "message"],
            missing_slots=list(intent.missing_slots),
        )

    if intent.task_type == MissionTaskType.FILE_TO_APP:
        tool_id = "mcp:windows_file_bridge_to_app"
        return CapabilityRoute(
            ok=available(tool_id),
            tool_id=tool_id,
            workflow_id="windows_file_to_app_bridge",
            reason="file-to-app tasks should use the OS file bridge workflow",
            required_slots=["file_path", "app_name"],
            missing_slots=list(intent.missing_slots),
        )

    if intent.task_type == MissionTaskType.APP_CONTROL:
        tool_id = "mcp:windows_open_app"
        return CapabilityRoute(
            ok=available(tool_id),
            tool_id=tool_id,
            workflow_id="windows_app_control",
            reason="app launch/switch should use the generic Windows app tool",
            required_slots=["app_name"],
            missing_slots=list(intent.missing_slots),
        )

    if intent.task_type == MissionTaskType.SYSTEM_STATUS_REPORT:
        preferred = "mcp:windows_system_status"
        fallback = "mcp:windows_workspace_report"
        tool_id = preferred if available(preferred) else fallback
        return CapabilityRoute(
            ok=available(tool_id),
            tool_id=tool_id,
            workflow_id="windows_system_status_report",
            reason="system status should use Windows OS sensing tools",
            required_slots=[],
            missing_slots=list(intent.missing_slots),
        )

    return CapabilityRoute(ok=False, reason="unknown mission intent")
