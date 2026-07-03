"""Reusable OS mission workflow templates.

The template library is the product-facing layer above concrete tools.  A
router selects a template from a normalized intent, then the capability router
maps that template to an available MCP/skill.  Keeping this layer explicit
prevents natural-language missions from becoming scattered one-off scripts.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from l3_node.mission_intent_schema import CapabilityRoute, MissionIntent, MissionTaskType


@dataclass(frozen=True)
class MissionTemplate:
    id: str
    title: str
    task_type: str
    workflow_id: str
    tool_id: str
    description: str
    required_slots: list[str] = field(default_factory=list)
    optional_slots: list[str] = field(default_factory=list)
    apps: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    risk_level: str = "low"
    examples: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_TEMPLATES: tuple[MissionTemplate, ...] = (
    MissionTemplate(
        id="codex_project_briefing_to_lark",
        title="Codex project briefing to Lark",
        task_type=MissionTaskType.PROJECT_BRIEFING_DELIVERY.value,
        workflow_id="codex_project_briefing_to_lark",
        tool_id="mcp:windows_codex_lark_workflow_template",
        description="Ask Codex to inspect a local project, produce a verified briefing, then send it through Lark.",
        required_slots=["project", "recipients"],
        optional_slots=["since_days", "feature_query", "output_format"],
        apps=["Codex", "Lark"],
        evidence=["router_evidence", "tool_evidence", "report_md", "lark_screenshot", "ocr_check"],
        examples=[
            "summarize Jachin recent progress and send to Neil",
            "ask Codex to analyze this project workflow and send it to the team group",
        ],
    ),
    MissionTemplate(
        id="codex_ask_lark_send",
        title="Codex answer to Lark",
        task_type=MissionTaskType.CODEX_ASK_LARK_SEND.value,
        workflow_id="codex_ask_lark_send",
        tool_id="mcp:windows_codex_ask_lark_send",
        description="Ask Codex a user-provided question, validate the reply, then send that reply through Lark.",
        required_slots=["feature_query", "recipients"],
        optional_slots=["since_days"],
        apps=["Codex", "Lark"],
        evidence=["codex_reply", "reply_validation", "lark_screenshot", "ocr_check"],
        examples=[
            "ask Codex what happened in AI this week and send the answer to Vivian",
            "让 Codex 回答这个问题，然后通过 Lark 发给 Vivian",
        ],
    ),
    MissionTemplate(
        id="lark_verified_message_send",
        title="Verified Lark message send",
        task_type=MissionTaskType.LARK_MESSAGE_SEND.value,
        workflow_id="windows_lark_message_send",
        tool_id="mcp:windows_lark_send_message",
        description="Open or focus Lark, find one or more recipients, send a message, and verify it visually.",
        required_slots=["recipients", "message"],
        optional_slots=[],
        apps=["Lark"],
        evidence=["recipient_visible", "message_visible", "screenshot", "ocr_check"],
    ),
    MissionTemplate(
        id="windows_calculator_calculate",
        title="Windows Calculator arithmetic",
        task_type=MissionTaskType.CALCULATOR_CALCULATE.value,
        workflow_id="windows_calculator_calculate",
        tool_id="mcp:windows_calculator_calculate",
        description="Open Windows Calculator, type an arithmetic expression, and verify the displayed result.",
        required_slots=["expression"],
        optional_slots=[],
        apps=["Calculator"],
        evidence=["active_window", "expression", "result", "ocr_check"],
    ),    MissionTemplate(
        id="windows_app_control",
        title="Windows app launch and focus",
        task_type=MissionTaskType.APP_CONTROL.value,
        workflow_id="windows_app_control",
        tool_id="mcp:windows_open_app",
        description="Open or switch to a Windows application and verify the active foreground window.",
        required_slots=["app_name"],
        optional_slots=["args"],
        apps=["Windows"],
        evidence=["active_window", "window_title", "screenshot"],
    ),
    MissionTemplate(
        id="windows_file_to_app_bridge",
        title="File to app bridge",
        task_type=MissionTaskType.FILE_TO_APP.value,
        workflow_id="windows_file_to_app_bridge",
        tool_id="mcp:windows_file_bridge_to_app",
        description="Resolve a local file, attach or upload it into a target app, then verify the filename.",
        required_slots=["file_path", "app_name"],
        optional_slots=["recipients", "target_field"],
        apps=["Windows"],
        evidence=["file_stat", "upload_target", "filename_visible", "ocr_check"],
        risk_level="medium",
    ),
    MissionTemplate(
        id="windows_system_status_report",
        title="Windows system status report",
        task_type=MissionTaskType.SYSTEM_STATUS_REPORT.value,
        workflow_id="windows_system_status_report",
        tool_id="mcp:windows_system_status",
        description="Read Windows status such as disk, network, battery, process, CPU, and memory usage.",
        required_slots=[],
        optional_slots=["since_days"],
        apps=["Windows"],
        evidence=["system_status", "process_snapshot", "evidence_json"],
    ),
    MissionTemplate(
        id="project_memory_update",
        title="Project path memory update",
        task_type=MissionTaskType.PROJECT_MEMORY_UPDATE.value,
        workflow_id="windows_project_memory_update",
        tool_id="mcp:windows_project_remember",
        description="Remember a project alias and local path so later missions can omit the path.",
        required_slots=["project_name", "project_path"],
        optional_slots=[],
        apps=["Jachin"],
        evidence=["memory_path", "project_path"],
    ),
)


def list_mission_templates() -> list[dict[str, Any]]:
    return [item.to_dict() for item in _TEMPLATES]


def get_mission_template(template_id: str) -> MissionTemplate | None:
    tid = str(template_id or "").strip()
    for item in _TEMPLATES:
        if item.id == tid:
            return item
    return None


def select_mission_template(intent: MissionIntent, route: CapabilityRoute | None = None) -> MissionTemplate | None:
    route_workflow = str(route.workflow_id or "") if route else ""
    route_tool = str(route.tool_id or "") if route else ""
    task_type = intent.task_type.value

    for item in _TEMPLATES:
        if route_workflow and item.workflow_id == route_workflow:
            return item
        if route_tool and item.tool_id == route_tool:
            return item
    for item in _TEMPLATES:
        if item.task_type == task_type:
            return item
    return None
