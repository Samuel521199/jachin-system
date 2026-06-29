"""User-visible mission preview objects.

The preview is the contract between intent recognition and execution.  It can
be shown in chat, written to evidence, or rendered in the console without
depending on any concrete Windows automation implementation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from l3_node.mission_intent_schema import CapabilityRoute, ClarificationDecision, MissionIntent
from l3_node.mission_runtime import MissionPlanPreview
from l3_node.mission_template_library import MissionTemplate


@dataclass
class MissionPreview:
    title: str
    task_type: str
    confidence: float
    template_id: str = ""
    workflow_id: str = ""
    tool_id: str = ""
    summary: str = ""
    auto_execute: bool = True
    requires_confirmation: bool = False
    confirmation_reason: str = ""
    clarification_question: str = ""
    slots: dict[str, Any] = field(default_factory=dict)
    memory: dict[str, Any] = field(default_factory=dict)
    apps: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    recipients: list[str] = field(default_factory=list)
    evidence_expected: list[str] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    execution_policy: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_mission_preview(
    *,
    intent: MissionIntent,
    route: CapabilityRoute,
    plan: MissionPlanPreview,
    template: MissionTemplate | None,
    clarification: ClarificationDecision,
    memory_evidence: dict[str, Any] | None = None,
) -> MissionPreview:
    template_id = template.id if template else ""
    title = template.title if template else intent.task_type.value
    evidence_expected = list(template.evidence) if template else []
    workflow_id = route.workflow_id or (template.workflow_id if template else "")
    tool_id = route.tool_id or (template.tool_id if template else "")
    execution_policy = {
        "route_ok": route.ok,
        "auto_execute": plan.auto_execute,
        "requires_confirmation": plan.requires_confirmation,
        "clarification_required": clarification.should_ask,
        "risk_level": plan.risk_level,
        "reason": route.reason,
    }
    return MissionPreview(
        title=title,
        task_type=intent.task_type.value,
        confidence=round(float(intent.confidence), 3),
        template_id=template_id,
        workflow_id=workflow_id,
        tool_id=tool_id,
        summary=plan.summary,
        auto_execute=plan.auto_execute and not clarification.should_ask,
        requires_confirmation=plan.requires_confirmation,
        confirmation_reason=plan.confirmation_reason,
        clarification_question=clarification.question if clarification.should_ask else "",
        slots=intent.slots.to_dict(),
        memory=memory_evidence or {},
        apps=list(plan.apps),
        files=list(plan.files),
        recipients=list(plan.recipients),
        evidence_expected=evidence_expected,
        steps=[step.to_dict() for step in plan.steps],
        execution_policy=execution_policy,
    )


def format_preview_for_chat(preview: MissionPreview, *, executed: bool = False) -> list[str]:
    lines = [
        f"Task Preview: {preview.title}",
        f"- task_type: {preview.task_type}",
        f"- workflow: {preview.workflow_id or preview.tool_id}",
    ]
    if preview.recipients:
        lines.append(f"- recipients: {', '.join(preview.recipients)}")
    project_path = str(preview.slots.get("project_path") or "").strip()
    project_name = str(preview.slots.get("project_name") or "").strip()
    if project_name or project_path:
        lines.append(f"- project: {project_name or project_path}")
    if preview.requires_confirmation:
        lines.append(f"- confirmation: required ({preview.confirmation_reason or 'risk'})")
    elif preview.clarification_question:
        lines.append("- status: needs clarification")
    else:
        lines.append(f"- status: {'executed' if executed else 'ready'}")
    return lines
