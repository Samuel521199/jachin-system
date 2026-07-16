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
    role_group: str = "general"
    permission_scope: str = "review_only"
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

    def select_for_tool(self, tool: str, *, work_order_input: str = "", risk: RiskLevel | None = None) -> RoleAgentSpec:
        risk = risk or RiskLevel.LOW
        if not str(tool or "").strip():
            fallback = self.get("ToolExecutionAgent")
            if fallback is None:
                raise KeyError("ToolExecutionAgent is not registered")
            return fallback
        for role in self.list_roles():
            if tool and not _matches_any(tool, role.tool_allow_patterns):
                continue
            if _risk_rank(risk) > _risk_rank(role.max_risk):
                continue
            return role
        fallback = self.get("ToolExecutionAgent")
        if fallback is None:
            raise KeyError("ToolExecutionAgent is not registered")
        return fallback

    def is_allowed(self, role_id: str, tool: str, risk: RiskLevel) -> tuple[bool, str]:
        role = self.get(role_id)
        if role is None:
            return False, f"unknown role agent: {role_id}"
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
    def review_role(role_id: str, description: str, capabilities: list[str], *, priority: int) -> RoleAgentSpec:
        return RoleAgentSpec(
            role_id=role_id,
            description=description,
            role_group="review_expert",
            permission_scope="review_only",
            capabilities=capabilities,
            tool_allow_patterns=[],
            max_risk=RiskLevel.LOW,
            can_execute_external_world=False,
            requires_work_order=False,
            priority=priority,
        )

    def readonly_role(
        role_id: str,
        description: str,
        capabilities: list[str],
        patterns: list[str],
        *,
        group: str,
        priority: int,
    ) -> RoleAgentSpec:
        return RoleAgentSpec(
            role_id=role_id,
            description=description,
            role_group=group,
            permission_scope="read_or_plan_only",
            capabilities=capabilities,
            tool_allow_patterns=patterns,
            max_risk=RiskLevel.LOW,
            can_execute_external_world=False,
            requires_work_order=True,
            priority=priority,
        )

    def worker_role(
        role_id: str,
        description: str,
        capabilities: list[str],
        patterns: list[str],
        *,
        priority: int,
        max_risk: RiskLevel = RiskLevel.MEDIUM,
    ) -> RoleAgentSpec:
        return RoleAgentSpec(
            role_id=role_id,
            description=description,
            role_group="domain_worker",
            permission_scope="plan_or_execute_when_authorized",
            capabilities=capabilities,
            tool_allow_patterns=patterns,
            max_risk=max_risk,
            can_execute_external_world=bool(patterns),
            requires_work_order=True,
            priority=priority,
        )

    def executor_role(
        role_id: str,
        description: str,
        capabilities: list[str],
        patterns: list[str],
        *,
        priority: int,
        max_risk: RiskLevel,
    ) -> RoleAgentSpec:
        return RoleAgentSpec(
            role_id=role_id,
            description=description,
            role_group="executor",
            permission_scope="execute_only_with_work_order",
            capabilities=capabilities,
            tool_allow_patterns=patterns,
            max_risk=max_risk,
            can_execute_external_world=True,
            requires_work_order=True,
            priority=priority,
        )

    return [
        review_role(
            "IntentAnalystAgent",
            "Reviews user input and proposes candidate intents for the kernel.",
            ["intent_review", "intent_graph"],
            priority=1,
        ),
        review_role(
            "AmbiguityResolverAgent",
            "Resolves short references such as close, continue, it, and that one from state plus memory.",
            ["reference_resolution", "ambiguity_review"],
            priority=2,
        ),
        review_role(
            "EntityResolverAgent",
            "Extracts target apps, contacts, files, and other entities from input, state, and memory.",
            ["entity_resolution", "alias_resolution"],
            priority=3,
        ),
        review_role(
            "VoiceEvidenceAgent",
            "Reviews voice confidence and modality evidence before action.",
            ["voice_evidence_review", "stt_confidence"],
            priority=4,
        ),
        RoleAgentSpec(
            role_id="SafetyAgent",
            description="Reviews risk, confirmation, unsaved state, and external-world side effects.",
            role_group="safety_permission",
            permission_scope="veto_and_confirmation_only",
            capabilities=["risk_review", "safety_gate"],
            tool_allow_patterns=[],
            max_risk=RiskLevel.LOW,
            can_execute_external_world=False,
            requires_work_order=False,
            priority=5,
        ),
        RoleAgentSpec(
            role_id="PermissionAgent",
            description="Checks whether the user, channel, and current context permit the requested action.",
            role_group="safety_permission",
            permission_scope="permission_review_only",
            capabilities=["permission_review", "policy_check"],
            tool_allow_patterns=[],
            max_risk=RiskLevel.LOW,
            can_execute_external_world=False,
            requires_work_order=False,
            priority=6,
        ),
        RoleAgentSpec(
            role_id="PrivacyAgent",
            description="Reviews privacy exposure, sensitive content, contacts, files, and outbound sharing risks.",
            role_group="safety_permission",
            permission_scope="privacy_review_only",
            capabilities=["privacy_review", "sensitive_data_check"],
            tool_allow_patterns=[],
            max_risk=RiskLevel.LOW,
            can_execute_external_world=False,
            requires_work_order=False,
            priority=7,
        ),
        RoleAgentSpec(
            role_id="ConfirmationAgent",
            description="Produces concise confirmation questions for risky or ambiguous actions.",
            role_group="safety_permission",
            permission_scope="confirmation_only",
            capabilities=["confirmation_prompt", "pending_decision"],
            tool_allow_patterns=[],
            max_risk=RiskLevel.LOW,
            can_execute_external_world=False,
            requires_work_order=False,
            priority=8,
        ),
        RoleAgentSpec(
            role_id="AppAliasResolverAgent",
            description="Resolves app aliases such as 飞书, browser, Chrome, Calculator, and project-specific app names.",
            role_group="review_expert",
            permission_scope="review_only",
            capabilities=["app_alias_resolution", "entity_resolution"],
            tool_allow_patterns=[],
            max_risk=RiskLevel.LOW,
            can_execute_external_world=False,
            requires_work_order=False,
            priority=11,
        ),
        RoleAgentSpec(
            role_id="AppControlPlannerAgent",
            description="Plans app open, close, switch, and window control before an executor receives a WorkOrder.",
            role_group="domain_worker",
            permission_scope="planning_only",
            capabilities=["app_control_plan", "window_plan"],
            tool_allow_patterns=[],
            max_risk=RiskLevel.LOW,
            can_execute_external_world=False,
            requires_work_order=False,
            priority=12,
        ),
        RoleAgentSpec(
            role_id="AppLaunchPlannerAgent",
            description="Plans low-risk app launch actions and candidate tools before execution authorization.",
            role_group="domain_worker",
            permission_scope="planning_only",
            capabilities=["app_launch_plan", "tool_selection"],
            tool_allow_patterns=[],
            max_risk=RiskLevel.LOW,
            can_execute_external_world=False,
            requires_work_order=False,
            priority=13,
        ),
        RoleAgentSpec(
            role_id="AppClosePlannerAgent",
            description="Plans app/window close actions and flags unsaved-content risk before execution authorization.",
            role_group="domain_worker",
            permission_scope="planning_only",
            capabilities=["app_close_plan", "unsaved_risk_review"],
            tool_allow_patterns=[],
            max_risk=RiskLevel.LOW,
            can_execute_external_world=False,
            requires_work_order=False,
            priority=14,
        ),
        RoleAgentSpec(
            role_id="CommunicationPlannerAgent",
            description="Plans message, contact, and communication workflows before MessageExecutorAgent receives a WorkOrder.",
            role_group="domain_worker",
            permission_scope="planning_only",
            capabilities=["message_plan", "contact_resolution", "send_safety_plan"],
            tool_allow_patterns=[],
            max_risk=RiskLevel.LOW,
            can_execute_external_world=False,
            requires_work_order=False,
            priority=15,
        ),
        RoleAgentSpec(
            role_id="ConversationAgent",
            description="Generates conversational and explanatory replies; cannot mutate external state.",
            role_group="domain_worker",
            permission_scope="reply_draft_only",
            capabilities=["chat", "explain", "summarize_reply"],
            tool_allow_patterns=[],
            max_risk=RiskLevel.LOW,
            can_execute_external_world=False,
            requires_work_order=False,
            priority=20,
        ),
        RoleAgentSpec(
            role_id="UserFacingReplyAgent",
            description="Turns TurnClosure and kernel decisions into concise user-facing replies.",
            role_group="domain_worker",
            permission_scope="final_reply_only",
            capabilities=["final_reply", "closure_reply"],
            tool_allow_patterns=[],
            max_risk=RiskLevel.LOW,
            can_execute_external_world=False,
            requires_work_order=False,
            priority=21,
        ),
        worker_role(
            "AppControlWorker",
            "Handles app-control domain planning and may execute only through delegated WorkOrders.",
            ["app_control_plan", "window_plan", "workflow_step"],
            [],
            priority=24,
        ),
        worker_role(
            "BrowserWorker",
            "Handles browser-domain planning and navigation workflows before browser execution.",
            ["browser_plan", "tab_context", "web_workflow"],
            [],
            priority=25,
        ),
        worker_role(
            "FileWorker",
            "Handles file-domain planning, context interpretation, and safe file workflow steps.",
            ["file_plan", "path_context", "file_workflow"],
            [],
            priority=26,
        ),
        worker_role(
            "CommunicationWorker",
            "Handles communication-domain planning, contact context, and message workflow steps.",
            ["communication_plan", "contact_context", "message_workflow"],
            [],
            priority=27,
        ),
        executor_role(
            "AppControlExecutorAgent",
            "Controls desktop applications and windows after a WorkOrder.",
            ["open_app", "close_app", "switch_app", "window_control"],
            [
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
            priority=20,
            max_risk=RiskLevel.HIGH,
        ),
        executor_role(
            "BrowserExecutorAgent",
            "Controls browser tabs, navigation, extraction, and browser automation only inside WorkOrder scope.",
            ["browser_open", "browser_click", "browser_extract", "browser_tab_control"],
            [
                "mcp:browser_*",
                "mcp:chrome_*",
                "mcp:playwright_*",
                "core:browser_*",
                "mcp:fetch",
                "fetch",
                "mcp:tavily_*",
                "mcp:tavily*",
                "core:web_research_*",
            ],
            priority=28,
            max_risk=RiskLevel.HIGH,
        ),
        executor_role(
            "FileExecutorAgent",
            "Reads and modifies local files only inside WorkOrder scope.",
            ["file_read", "file_write", "file_move", "file_delete"],
            [
                "core:fs_*",
                "mcp:windows_file_*",
                "mcp:fs_*",
                "mcp:file*",
                "mcp:filesystem*",
                "mcp:read_file",
                "mcp:write_file",
                "mcp:create_file",
            ],
            priority=30,
            max_risk=RiskLevel.CRITICAL,
        ),
        executor_role(
            "MessageExecutorAgent",
            "Sends messages, emails, Lark notifications, and publish/upload operations.",
            ["send_message", "notify", "publish", "upload"],
            [
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
            priority=40,
            max_risk=RiskLevel.HIGH,
        ),
        executor_role(
            "OsAutomationExecutorAgent",
            "Runs OS automation, UI automation, and system-level tasks only inside WorkOrder scope.",
            ["os_automation", "uia_control", "system_task"],
            [
                "mcp:os_*",
                "mcp:uia_*",
                "mcp:windows_uia_*",
                "mcp:windows_os_*",
                "core:os_*",
            ],
            priority=42,
            max_risk=RiskLevel.CRITICAL,
        ),
        RoleAgentSpec(
            role_id="MemoryRecallAgent",
            description="Reads short-term, long-term, task, correction, and failure memories before kernel decisions.",
            role_group="memory_learning",
            permission_scope="read_memory_only",
            capabilities=["memory_search", "reference_recall", "conflict_detection"],
            tool_allow_patterns=["recall_memory", "core:local_memory_search", "mcp:memory_search", "mcp:memory_read"],
            max_risk=RiskLevel.LOW,
            can_execute_external_world=False,
            priority=45,
        ),
        RoleAgentSpec(
            role_id="PreferenceAgent",
            description="Interprets explicit and stable user preferences without writing long-term memory by itself.",
            role_group="memory_learning",
            permission_scope="preference_review_only",
            capabilities=["preference_review", "preference_conflict_detection"],
            tool_allow_patterns=[],
            max_risk=RiskLevel.LOW,
            can_execute_external_world=False,
            requires_work_order=False,
            priority=46,
        ),
        RoleAgentSpec(
            role_id="CorrectionLearningAgent",
            description="Turns user corrections into candidate alias, preference, and strategy memory write requests.",
            role_group="memory_learning",
            permission_scope="learning_plan_only",
            capabilities=["correction_learning", "alias_update_plan", "memory_write_request_plan"],
            tool_allow_patterns=[],
            max_risk=RiskLevel.LOW,
            can_execute_external_world=False,
            requires_work_order=False,
            priority=47,
        ),
        RoleAgentSpec(
            role_id="MemoryWriteAgent",
            description="Writes approved memories and action-chain records.",
            role_group="memory_learning",
            permission_scope="write_memory_only_with_authorization",
            capabilities=["memory_append", "memory_dedupe", "memory_merge"],
            tool_allow_patterns=["core:local_memory_append", "core:memory_*", "mcp:memory_*"],
            max_risk=RiskLevel.MEDIUM,
            can_execute_external_world=True,
            priority=50,
        ),
        readonly_role(
            "DesktopStateReadAgent",
            "Reads the current StateSnapshot and explains desktop foreground, running apps, and risk state.",
            ["desktop_state_read", "foreground_context", "running_apps_context"],
            ["core:state_snapshot", "core:*status*", "mcp:*status*", "mcp:windows_*status*"],
            group="environment_context",
            priority=52,
        ),
        readonly_role(
            "WindowContextAgent",
            "Explains active windows, target windows, and unsaved/window-control context from state snapshots.",
            ["window_context", "active_window_analysis"],
            ["core:state_snapshot", "mcp:windows_window_status", "mcp:windows_screenshot*", "mcp:*screenshot*"],
            group="environment_context",
            priority=53,
        ),
        readonly_role(
            "AppStateAgent",
            "Explains running app state, recent app events, and app availability from state snapshots.",
            ["app_state_context", "recent_app_events"],
            ["core:state_snapshot", "mcp:windows_app_status", "mcp:*status*"],
            group="environment_context",
            priority=54,
        ),
        readonly_role(
            "FileContextAgent",
            "Explains file paths, file existence, and file-risk context without mutating files.",
            ["file_context", "path_resolution", "file_risk_context"],
            ["core:fs_read", "core:fs_stat", "mcp:fs_stat", "mcp:read_file", "mcp:filesystem*"],
            group="environment_context",
            priority=55,
        ),
        RoleAgentSpec(
            role_id="VerificationAgent",
            description="Verifies observable results and evidence.",
            role_group="verification_audit",
            permission_scope="verify_only",
            capabilities=["verify", "audit", "ocr_check", "state_check"],
            tool_allow_patterns=["core:*verify*", "core:*status*", "mcp:*status*", "mcp:*screenshot*", "mcp:*ocr*"],
            max_risk=RiskLevel.LOW,
            can_execute_external_world=False,
            priority=60,
        ),
        RoleAgentSpec(
            role_id="AuditAgent",
            description="Records audit evidence and checks that execution followed DecisionContract and WorkOrder boundaries.",
            role_group="verification_audit",
            permission_scope="audit_only",
            capabilities=["audit_trail", "contract_consistency_check"],
            tool_allow_patterns=["core:*audit*", "core:*ledger*", "mcp:*status*"],
            max_risk=RiskLevel.LOW,
            can_execute_external_world=False,
            priority=61,
        ),
        RoleAgentSpec(
            role_id="ConsistencyCheckAgent",
            description="Checks consistency among input, memory, state, ReviewSummary, DecisionContract, and results.",
            role_group="verification_audit",
            permission_scope="consistency_check_only",
            capabilities=["consistency_check", "contradiction_review"],
            tool_allow_patterns=[],
            max_risk=RiskLevel.LOW,
            can_execute_external_world=False,
            requires_work_order=False,
            priority=62,
        ),
        RoleAgentSpec(
            role_id="RecoveryAgent",
            description="Plans retries, fallback tools, degradation, clarification, or abort.",
            role_group="recovery_background",
            permission_scope="recovery_plan_only",
            capabilities=["retry_plan", "fallback_plan", "clarification"],
            tool_allow_patterns=[],
            max_risk=RiskLevel.LOW,
            can_execute_external_world=False,
            requires_work_order=False,
            priority=70,
        ),
        RoleAgentSpec(
            role_id="RetryPlannerAgent",
            description="Produces bounded retry, switch-tool, degrade, ask-user, or abort recovery plans.",
            role_group="recovery_background",
            permission_scope="retry_plan_only",
            capabilities=["retry_plan", "switch_tool_plan", "degrade_plan"],
            tool_allow_patterns=[],
            max_risk=RiskLevel.LOW,
            can_execute_external_world=False,
            requires_work_order=False,
            priority=71,
        ),
        RoleAgentSpec(
            role_id="BackgroundTaskAgent",
            description="Maintains and resumes background tasks without bypassing kernel authorization.",
            role_group="recovery_background",
            permission_scope="background_task_guardian",
            capabilities=["background_task", "task_resume", "task_guard"],
            tool_allow_patterns=["core:background_task_*", "mcp:background_task_*"],
            max_risk=RiskLevel.MEDIUM,
            can_execute_external_world=False,
            priority=72,
        ),
        RoleAgentSpec(
            role_id="WatcherAgent",
            description="Consumes watcher events and proposes kernel turns for state changes; it does not directly act.",
            role_group="recovery_background",
            permission_scope="watcher_event_review_only",
            capabilities=["watcher_event", "state_change_trigger"],
            tool_allow_patterns=["core:state_snapshot", "core:watcher_*"],
            max_risk=RiskLevel.LOW,
            can_execute_external_world=False,
            priority=73,
        ),
        RoleAgentSpec(
            role_id="ToolExecutionAgent",
            description="Generic tool transport for tools that do not yet have a specialized role.",
            role_group="executor",
            permission_scope="fallback_execute_only_with_work_order",
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
