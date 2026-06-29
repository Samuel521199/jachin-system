"""Compose capability matches into explainable workflow plans."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from l3_node.capability_matcher import CapabilityMatchResult


@dataclass
class WorkflowStep:
    stage: str
    capability_id: str
    action: str
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WorkflowComposition:
    workflow_id: str
    mode: str
    selected_capability_id: str
    steps: list[WorkflowStep] = field(default_factory=list)
    evidence_expected: list[str] = field(default_factory=list)
    risk: str = "unknown"
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["steps"] = [step.to_dict() for step in self.steps]
        return data


def compose_workflow(match: CapabilityMatchResult) -> WorkflowComposition:
    cap = match.selected
    if not cap:
        return WorkflowComposition(
            workflow_id="unrouted",
            mode="none",
            selected_capability_id="",
            steps=[],
            evidence_expected=[],
            risk="unknown",
            reason=match.reason,
        )

    if cap.workflow_id == "codex_project_briefing_to_lark":
        steps = [
            WorkflowStep("resolve_project", "project_memory", "Resolve project alias/path", ["project_path", "project_memory"]),
            WorkflowStep("run_codex", cap.id, "Ask Codex to inspect the project and produce a briefing", ["prompt", "codex_output"]),
            WorkflowStep("validate_summary", cap.id, "Validate copied Codex output is non-empty and relevant", ["clipboard_text", "validation"]),
            WorkflowStep("send_lark", "mcp:windows_lark_send_message", "Send the summary to Lark recipients", ["recipient_visible", "message_visible"]),
            WorkflowStep("verify_delivery", "ocr_verify", "Verify sent result with screenshot/OCR", ["screenshot", "ocr_check"]),
        ]
        return WorkflowComposition(
            workflow_id=cap.workflow_id,
            mode="multi_step_template",
            selected_capability_id=cap.id,
            steps=steps,
            evidence_expected=cap.evidence,
            risk=cap.risk,
            reason="requires project understanding plus external delivery verification",
        )

    if cap.tool_chain:
        steps = [
            WorkflowStep(f"step_{idx}", tool_id, f"Run {tool_id}", cap.evidence)
            for idx, tool_id in enumerate(cap.tool_chain, start=1)
        ]
        return WorkflowComposition(
            workflow_id=cap.workflow_id,
            mode="tool_chain",
            selected_capability_id=cap.id,
            steps=steps,
            evidence_expected=cap.evidence,
            risk=cap.risk,
            reason="capability declares a tool chain",
        )

    return WorkflowComposition(
        workflow_id=cap.workflow_id or cap.id,
        mode="single_capability",
        selected_capability_id=cap.id,
        steps=[WorkflowStep("execute", cap.id, cap.description, cap.evidence)],
        evidence_expected=cap.evidence,
        risk=cap.risk,
        reason="single capability can satisfy the task",
    )
