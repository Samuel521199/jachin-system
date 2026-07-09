"""Role Agent registry and permission matrix for the Cognitive Kernel."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from fnmatch import fnmatchcase
from typing import Any

from .contracts import RiskLevel


@dataclass(slots=True)
class RoleAgentSpec:
    role_id: str
    description: str
    capabilities: list[str] = field(default_factory=list)
    tool_allow_patterns: list[str] = field(default_factory=list)
    max_risk: RiskLevel = RiskLevel.LOW
    can_execute_external_world: bool = False
    requires_work_order: bool = True
    priority: int = 100

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["max_risk"] = self.max_risk.value
        return data


class RoleAgentRegistry:
    def __init__(self, roles: list[RoleAgentSpec] | None = None) -> None:
        self._roles: dict[str, RoleAgentSpec] = {}
        for role in roles or default_role_agents():
            self.register(role)

    def register(self, role: RoleAgentSpec) -> None:
        self._roles[role.role_id] = role

    def get(self, role_id: str) -> RoleAgentSpec | None:
        return self._roles.get(role_id)

    def list_roles(self) -> list[RoleAgentSpec]:
        return sorted(self._roles.values(), key=lambda r: (r.priority, r.role_id))

    def select_for_tool(self, tool: str, *, action_input: str = "", risk: RiskLevel | None = None) -> RoleAgentSpec:
        risk = risk or RiskLevel.LOW
        for role in self.list_roles():
            if not role.can_execute_external_world and role.role_id not in {
                "ConversationAgent",
                "VerificationAgent",
                "RecoveryAgent",
                "MemoryWriteAgent",
            }:
                continue
            if _risk_rank(risk) > _risk_rank(role.max_risk):
                continue
            if _matches_any(tool, role.tool_allow_patterns):
                return role
        fallback = self.get("ToolExecutionAgent")
        if fallback is None:
            raise KeyError("ToolExecutionAgent is not registered")
        return fallback

    def is_allowed(self, role_id: str, tool: str, risk: RiskLevel) -> tuple[bool, str]:
        role = self.get(role_id)
        if role is None:
            return False, f"unknown role agent: {role_id}"
        if role.requires_work_order and not role.can_execute_external_world and tool:
            return False, f"role {role_id} is not an external-world executor"
        if _risk_rank(risk) > _risk_rank(role.max_risk):
            return False, f"risk {risk.value} exceeds role {role_id} max_risk {role.max_risk.value}"
        if tool and not _matches_any(tool, role.tool_allow_patterns):
            return False, f"tool {tool} is outside role {role_id} permission matrix"
        return True, ""


def _matches_any(tool: str, patterns: list[str]) -> bool:
    tid = str(tool or "")
    return any(fnmatchcase(tid, pattern) for pattern in patterns)


def _risk_rank(risk: RiskLevel) -> int:
    order = {
        RiskLevel.LOW: 1,
        RiskLevel.MEDIUM: 2,
        RiskLevel.HIGH: 3,
        RiskLevel.CRITICAL: 4,
    }
    return order.get(risk, 2)


def default_role_agents() -> list[RoleAgentSpec]:
    return [
        RoleAgentSpec(
            role_id="IntentAnalystAgent",
            description="Reviews user input and proposes candidate intents for the kernel.",
            capabilities=["intent_review", "intent_graph"],
            tool_allow_patterns=[],
            max_risk=RiskLevel.LOW,
            can_execute_external_world=False,
            requires_work_order=False,
            priority=1,
        ),
        RoleAgentSpec(
            role_id="AmbiguityResolverAgent",
            description="Resolves short references such as close, continue, it, and that one from state plus memory.",
            capabilities=["reference_resolution", "ambiguity_review"],
            tool_allow_patterns=[],
            max_risk=RiskLevel.LOW,
            can_execute_external_world=False,
            requires_work_order=False,
            priority=2,
        ),
        RoleAgentSpec(
            role_id="EntityResolverAgent",
            description="Extracts target apps, contacts, files, and other entities from input, state, and memory.",
            capabilities=["entity_resolution", "alias_resolution"],
            tool_allow_patterns=[],
            max_risk=RiskLevel.LOW,
            can_execute_external_world=False,
            requires_work_order=False,
            priority=3,
        ),
        RoleAgentSpec(
            role_id="VoiceEvidenceAgent",
            description="Reviews voice confidence and modality evidence before action.",
            capabilities=["voice_evidence_review", "stt_confidence"],
            tool_allow_patterns=[],
            max_risk=RiskLevel.LOW,
            can_execute_external_world=False,
            requires_work_order=False,
            priority=4,
        ),
        RoleAgentSpec(
            role_id="SafetyAgent",
            description="Reviews risk, confirmation, unsaved state, and external-world side effects.",
            capabilities=["risk_review", "safety_gate"],
            tool_allow_patterns=[],
            max_risk=RiskLevel.LOW,
            can_execute_external_world=False,
            requires_work_order=False,
            priority=5,
        ),
        RoleAgentSpec(
            role_id="AppControlPlannerAgent",
            description="Plans app open, close, switch, and window control before an executor receives a WorkOrder.",
            capabilities=["app_control_plan", "window_plan"],
            tool_allow_patterns=[],
            max_risk=RiskLevel.LOW,
            can_execute_external_world=False,
            requires_work_order=False,
            priority=6,
        ),
        RoleAgentSpec(
            role_id="ConversationAgent",
            description="Generates conversational and explanatory replies; cannot mutate external state.",
            capabilities=["chat", "explain", "summarize_reply"],
            tool_allow_patterns=[],
            max_risk=RiskLevel.LOW,
            can_execute_external_world=False,
            requires_work_order=False,
            priority=10,
        ),
        RoleAgentSpec(
            role_id="UserFacingReplyAgent",
            description="Turns TurnClosure and kernel decisions into concise user-facing replies.",
            capabilities=["final_reply", "closure_reply"],
            tool_allow_patterns=[],
            max_risk=RiskLevel.LOW,
            can_execute_external_world=False,
            requires_work_order=False,
            priority=11,
        ),
        RoleAgentSpec(
            role_id="AppControlExecutorAgent",
            description="Controls desktop applications and windows after a WorkOrder.",
            capabilities=["open_app", "close_app", "switch_app", "window_control"],
            tool_allow_patterns=[
                "core:windows_app_*",
                "core:windows_window_*",
                "mcp:windows_app_*",
                "mcp:windows_window_*",
                "mcp:windows_open_*",
                "mcp:windows_switch_*",
                "mcp:windows_calculator_*",
                "mcp:app_*",
                "mcp:uia_*",
            ],
            max_risk=RiskLevel.HIGH,
            can_execute_external_world=True,
            priority=20,
        ),
        RoleAgentSpec(
            role_id="FileExecutorAgent",
            description="Reads and modifies local files only inside WorkOrder scope.",
            capabilities=["file_read", "file_write", "file_move", "file_delete"],
            tool_allow_patterns=[
                "core:fs_*",
                "mcp:windows_file_*",
                "mcp:fs_*",
                "mcp:file*",
                "mcp:filesystem*",
                "mcp:read_file",
                "mcp:write_file",
                "mcp:create_file",
            ],
            max_risk=RiskLevel.CRITICAL,
            can_execute_external_world=True,
            priority=30,
        ),
        RoleAgentSpec(
            role_id="MessageExecutorAgent",
            description="Sends messages, emails, Lark notifications, and publish/upload operations.",
            capabilities=["send_message", "notify", "publish", "upload"],
            tool_allow_patterns=[
                "mcp:atom_lark_notifier",
                "mcp:lark_*",
                "mcp:windows_lark_*",
                "mcp:windows_codex_*lark*",
                "core:lark_*",
                "mcp:send*",
                "mcp:smtp*",
                "mcp:post*",
                "core:*publish*",
                "mcp:*publish*",
                "mcp:*upload*",
            ],
            max_risk=RiskLevel.HIGH,
            can_execute_external_world=True,
            priority=40,
        ),
        RoleAgentSpec(
            role_id="MemoryRecallAgent",
            description="Reads short-term, long-term, task, correction, and failure memories before kernel decisions.",
            capabilities=["memory_search", "reference_recall", "conflict_detection"],
            tool_allow_patterns=["recall_memory", "core:local_memory_search", "mcp:memory_search", "mcp:memory_read"],
            max_risk=RiskLevel.LOW,
            can_execute_external_world=True,
            priority=45,
        ),
        RoleAgentSpec(
            role_id="MemoryWriteAgent",
            description="Writes approved memories and action-chain records.",
            capabilities=["memory_append", "memory_dedupe", "memory_merge"],
            tool_allow_patterns=["core:local_memory_append", "core:memory_*", "mcp:memory_*"],
            max_risk=RiskLevel.MEDIUM,
            can_execute_external_world=True,
            priority=50,
        ),
        RoleAgentSpec(
            role_id="VerificationAgent",
            description="Verifies observable results and evidence.",
            capabilities=["verify", "audit", "ocr_check", "state_check"],
            tool_allow_patterns=["core:*verify*", "core:*status*", "mcp:*status*", "mcp:*screenshot*", "mcp:*ocr*"],
            max_risk=RiskLevel.LOW,
            can_execute_external_world=False,
            priority=60,
        ),
        RoleAgentSpec(
            role_id="RecoveryAgent",
            description="Plans retries, fallback tools, degradation, clarification, or abort.",
            capabilities=["retry_plan", "fallback_plan", "clarification"],
            tool_allow_patterns=[],
            max_risk=RiskLevel.LOW,
            can_execute_external_world=False,
            priority=70,
        ),
        RoleAgentSpec(
            role_id="ToolExecutionAgent",
            description="Generic tool transport for tools that do not yet have a specialized role.",
            capabilities=["generic_tool_transport"],
            tool_allow_patterns=["core:*", "mcp:*", "util:*", "delegate", "coordinate", "jpp:*"],
            max_risk=RiskLevel.CRITICAL,
            can_execute_external_world=True,
            priority=999,
        ),
    ]


DEFAULT_ROLE_REGISTRY = RoleAgentRegistry()


def get_default_role_registry() -> RoleAgentRegistry:
    return DEFAULT_ROLE_REGISTRY
