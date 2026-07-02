"""Mission intent schema for Jachin's OS-level assistant routing."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class MissionTaskType(str, Enum):
    UNKNOWN = "unknown"
    PROJECT_BRIEFING_DELIVERY = "project_briefing_delivery"
    CODEX_ASK_LARK_SEND = "codex_ask_lark_send"
    PROJECT_MEMORY_UPDATE = "project_memory_update"
    LARK_MESSAGE_SEND = "lark_message_send"
    CALCULATOR_CALCULATE = "calculator_calculate"
    FILE_TO_APP = "file_to_app"
    APP_CONTROL = "app_control"
    SYSTEM_STATUS_REPORT = "system_status_report"


class MissionRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class MissionSlots:
    project_name: str = ""
    project_path: str = ""
    directory_path: str = ""
    feature_query: str = ""
    bug_query: str = ""
    recipients: list[str] = field(default_factory=list)
    message: str = ""
    file_path: str = ""
    app_name: str = ""
    since_days: int = 3
    output_format: str = ""
    expression: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MissionIntent:
    task_type: MissionTaskType
    confidence: float
    slots: MissionSlots = field(default_factory=MissionSlots)
    missing_slots: list[str] = field(default_factory=list)
    risk_level: MissionRiskLevel = MissionRiskLevel.LOW
    reasoning: list[str] = field(default_factory=list)
    raw_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["task_type"] = self.task_type.value
        data["risk_level"] = self.risk_level.value
        return data


@dataclass
class CapabilityRoute:
    ok: bool
    tool_id: str = ""
    workflow_id: str = ""
    reason: str = ""
    evidence_policy: str = "write_router_and_tool_evidence"
    required_slots: list[str] = field(default_factory=list)
    missing_slots: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ClarificationDecision:
    should_ask: bool
    question: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
