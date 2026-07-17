"""Core contracts for the Memory-first Cognitive Kernel.

These dataclasses are intentionally plain and serializable. They define the
boundary between input channels, memory recall, state snapshots, kernel
decisions, role-agent execution, verification, recovery, and memory write-back.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Literal


class InputSource(str, Enum):
    TEXT = "text"
    VOICE = "voice"
    IM = "im"
    HOTKEY = "hotkey"
    WATCHER = "watcher"
    API = "api"
    SYSTEM = "system"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ClosureType(str, Enum):
    COMPLETED = "completed"
    ANSWERED = "answered"
    WAITING_USER = "waiting_user"
    BACKGROUNDED = "backgrounded"
    BLOCKED = "blocked"
    FAILED_RECOVERABLE = "failed_recoverable"
    FAILED_FINAL = "failed_final"


def _json_ready(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): _json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_ready(v) for v in value]
    return value


@dataclass(slots=True)
class AgentInputEnvelope:
    turn_id: str
    source: InputSource
    raw_text: str
    normalized_text: str
    session_id: str = ""
    channel: str = ""
    language: str = ""
    confidence: float | None = None
    attachments: list[dict[str, Any]] = field(default_factory=list)
    modality_evidence: dict[str, Any] = field(default_factory=dict)
    implicit_attribution: dict[str, Any] = field(default_factory=dict)
    created_at_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(slots=True)
class StateSnapshot:
    snapshot_id: str
    generated_at_ms: int
    freshness_ms: int
    active_window: dict[str, Any] = field(default_factory=dict)
    running_apps: list[dict[str, Any]] = field(default_factory=list)
    recent_app_events: list[dict[str, Any]] = field(default_factory=list)
    task_state: dict[str, Any] = field(default_factory=dict)
    voice_state: dict[str, Any] = field(default_factory=dict)
    resource_state: dict[str, Any] = field(default_factory=dict)
    risk_state: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(slots=True)
class MemoryRecallRequest:
    turn_id: str
    input_envelope: AgentInputEnvelope
    candidate_intents: list[str] = field(default_factory=list)
    candidate_task_domains: list[str] = field(default_factory=list)
    candidate_entities: list[str] = field(default_factory=list)
    multi_queries: dict[str, str] = field(default_factory=dict)
    retrieval_channels: list[str] = field(default_factory=list)
    state_snapshot_summary: dict[str, Any] = field(default_factory=dict)
    active_task_stack_summary: dict[str, Any] = field(default_factory=dict)
    retrieval_purpose: list[str] = field(default_factory=list)
    max_results_per_channel: int = 5
    freshness_requirement: str = "recent_first"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["input_envelope"] = self.input_envelope.to_dict()
        return _json_ready(data)


@dataclass(slots=True)
class MemoryEvidence:
    memory_id: str
    memory_type: str
    content: str
    source: str
    created_at: str = ""
    updated_at: str = ""
    confidence: float = 0.0
    confirmed_by_user: bool = False
    ttl: str = ""
    relevance_reason: str = ""
    trust_state: str = "floating"
    trust_reason: str = ""
    user_attitude: str = "floating"
    recall_allowed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(slots=True)
class RelevantMemoryBundle:
    turn_id: str
    retrieval_summary: str = ""
    recall_request: dict[str, Any] = field(default_factory=dict)
    candidate_intents: list[str] = field(default_factory=list)
    candidate_task_domains: list[str] = field(default_factory=list)
    multi_queries: dict[str, str] = field(default_factory=dict)
    resolved_references: list[dict[str, Any]] = field(default_factory=list)
    recent_actions: list[MemoryEvidence] = field(default_factory=list)
    active_tasks: list[MemoryEvidence] = field(default_factory=list)
    user_preferences: list[MemoryEvidence] = field(default_factory=list)
    safety_preferences: list[MemoryEvidence] = field(default_factory=list)
    aliases: list[MemoryEvidence] = field(default_factory=list)
    corrections: list[MemoryEvidence] = field(default_factory=list)
    entity_matches: list[MemoryEvidence] = field(default_factory=list)
    contact_matches: list[MemoryEvidence] = field(default_factory=list)
    project_facts: list[MemoryEvidence] = field(default_factory=list)
    tool_habits: list[MemoryEvidence] = field(default_factory=list)
    failure_hints: list[MemoryEvidence] = field(default_factory=list)
    historical_task_summaries: list[MemoryEvidence] = field(default_factory=list)
    ranking_evidence: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    memory_gaps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(slots=True)
class RoleAgentReviewInput:
    review_session_id: str
    turn_id: str
    role_id: str
    input_envelope: AgentInputEnvelope
    state_snapshot: StateSnapshot
    memory_bundle: RelevantMemoryBundle
    candidate_intents: list[str] = field(default_factory=list)
    candidate_entities: list[dict[str, Any]] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_session_id": self.review_session_id,
            "turn_id": self.turn_id,
            "role_id": self.role_id,
            "input_envelope": self.input_envelope.to_dict(),
            "state_snapshot": self.state_snapshot.to_dict(),
            "memory_bundle": self.memory_bundle.to_dict(),
            "candidate_intents": list(self.candidate_intents),
            "candidate_entities": _json_ready(self.candidate_entities),
            "constraints": _json_ready(self.constraints),
        }


@dataclass(slots=True)
class RoleAgentReview:
    review_id: str
    review_session_id: str
    turn_id: str
    role_id: str
    candidate_intents: list[str] = field(default_factory=list)
    candidate_entities: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    risk_level: RiskLevel = RiskLevel.LOW
    recommended_roles: list[str] = field(default_factory=list)
    proposed_task_type: str = ""
    proposed_tool: str = ""
    rationale: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    needs_clarification: bool = False
    clarification_question: str = ""
    can_execute: bool = False

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(slots=True)
class ReviewSummary:
    review_session_id: str
    turn_id: str
    reviews: list[RoleAgentReview] = field(default_factory=list)
    top_intent: str = ""
    task_type: str = ""
    target: dict[str, Any] = field(default_factory=dict)
    selected_roles: list[str] = field(default_factory=list)
    candidate_tools: list[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    confidence: float = 0.0
    needs_clarification: bool = False
    clarification_question: str = ""
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    rationale: list[str] = field(default_factory=list)
    semantic_candidates: list[dict[str, Any]] = field(default_factory=list)
    capability_candidates: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(slots=True)
class ToolPolicy:
    allowed_tools: list[str] = field(default_factory=list)
    denied_tools: list[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    requires_confirmation: bool = False
    confirmation_reason: str = ""
    verification_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(slots=True)
class DecisionContract:
    decision_id: str
    turn_id: str
    task_type: str
    goal: str
    selected_workflow: str = ""
    selected_roles: list[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    tool_policy: ToolPolicy = field(default_factory=ToolPolicy)
    execution_allowed: bool = False
    clarification_question: str = ""
    verification_criteria: list[str] = field(default_factory=list)
    rationale: list[str] = field(default_factory=list)
    memory_context_refs: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(slots=True)
class WorkOrder:
    work_order_id: str
    decision_id: str
    role_agent: str
    task: str
    inputs: dict[str, Any] = field(default_factory=dict)
    tool_policy: ToolPolicy = field(default_factory=ToolPolicy)
    expected_outputs: list[str] = field(default_factory=list)
    verification_criteria: list[str] = field(default_factory=list)
    status: Literal["pending", "running", "done", "failed", "cancelled"] = "pending"

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(slots=True)
class VerificationReport:
    verification_id: str
    work_order_id: str
    ok: bool
    evidence: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    failure_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(slots=True)
class RecoveryPlan:
    recovery_id: str
    turn_id: str
    failed_work_order_id: str
    strategy: Literal["retry", "switch_tool", "degrade", "ask_user", "abort"]
    rationale: str
    next_work_order: WorkOrder | None = None
    max_attempts: int = 1
    attempt_no: int = 1
    alternative_paths: list[dict[str, Any]] = field(default_factory=list)
    final_failure_report: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.next_work_order:
            data["next_work_order"] = self.next_work_order.to_dict()
        return _json_ready(data)


@dataclass(slots=True)
class MemoryWriteRequest:
    turn_id: str
    source_event: str
    memory_type: str
    content: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    ttl: str = ""
    requires_user_confirmation: bool = False
    merge_policy: str = "dedupe_and_merge"
    trust_state: str = ""
    trust_reason: str = ""
    user_attitude: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(slots=True)
class TurnClosure:
    turn_id: str
    closure_type: ClosureType
    final_user_message_intent: str = ""
    executed_work_orders: list[str] = field(default_factory=list)
    verification_status: str = ""
    pending_decision: dict[str, Any] | None = None
    background_task_id: str = ""
    memory_write_requests: list[MemoryWriteRequest] = field(default_factory=list)
    next_turn_hints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(slots=True)
class TaskLedgerEntry:
    turn_id: str
    input_envelope: AgentInputEnvelope
    state_snapshot: StateSnapshot
    memory_bundle: RelevantMemoryBundle
    decision_contract: DecisionContract | None = None
    work_orders: list[WorkOrder] = field(default_factory=list)
    verification_reports: list[VerificationReport] = field(default_factory=list)
    recovery_plans: list[RecoveryPlan] = field(default_factory=list)
    closure: TurnClosure | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "input_envelope": self.input_envelope.to_dict(),
            "state_snapshot": self.state_snapshot.to_dict(),
            "memory_bundle": self.memory_bundle.to_dict(),
            "decision_contract": self.decision_contract.to_dict() if self.decision_contract else None,
            "work_orders": [x.to_dict() for x in self.work_orders],
            "verification_reports": [x.to_dict() for x in self.verification_reports],
            "recovery_plans": [x.to_dict() for x in self.recovery_plans],
            "closure": self.closure.to_dict() if self.closure else None,
        }
