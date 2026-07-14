"""Memory-first Cognitive Kernel boundary for L3.

The package contains architecture contracts and light orchestration helpers for
the Jarvis-style kernel described in
``docs/07_memory_first_main_agent_and_voice_app_agents.md``.
"""

from .contracts import (
    AgentInputEnvelope,
    DecisionContract,
    MemoryRecallRequest,
    MemoryWriteRequest,
    RelevantMemoryBundle,
    ReviewSummary,
    RoleAgentReview,
    RoleAgentReviewInput,
    RecoveryPlan,
    StateSnapshot,
    TaskLedgerEntry,
    TurnClosure,
    VerificationReport,
    WorkOrder,
)
from .arbiter import arbitrate_review_summary, build_work_order_from_decision
from .capability_recovery_registry import CapabilityRecoveryRegistry, RecoveryCandidate, load_recovery_manifests
from .dispatcher import DispatchResult, dispatch_existing_work_order, dispatch_tool_work_order
from .kernel_loop import KernelPlanningResult, plan_cognitive_turn
from .kernel_prompts import (
    build_cognitive_kernel_system_prompt,
    build_role_execution_system_prefix,
    build_user_facing_reply_agent_system_prompt,
)
from .memory_lifecycle import (
    LifecycleMemoryRecord,
    record_lifecycle_memory_feedback,
    recall_lifecycle_memories,
    write_lifecycle_memory,
)
from .memory_confidence import (
    MemoryFeedbackUpdate,
    apply_feedback,
    classify_memory_layer,
    extract_memory_scope,
    initial_confidence,
    recall_score,
)
from .growth_scheduler import GrowthPipelineResult, run_growth_pipeline
from .graph_connectors import GraphConnectorResult, sync_graph_engine_connectors
from .graph_sync_adapter import GraphSyncResult, sync_memory_growth_graph
from .memory_tools import recall_memory_search
from .pipeline import CognitiveTurnContext, build_cognitive_turn_context
from .prompt_policies import SQL_DATA_SOP_PROMPT
from .work_order_aliases import RECALL_MEMORY_TOOL_ID, WORK_ORDER_ALIAS_IDS
from .recovery_guards import (
    build_fake_mcp_error_recovery_prompt,
    build_fake_weather_error_recovery_prompt,
    is_hallucinated_final_mcp_error_json,
    is_hallucinated_weather_service_error_json,
)
from .recovery_planner import (
    RecoveryAttemptPlan,
    RecoveryAttemptRecord,
    RecoveryPlanner,
)
from .role_executors import (
    RoleExecutionContext,
    RoleExecutionResult,
    RoleExecutorRegistry,
    get_default_role_executor_registry,
)
from .roles import RoleAgentRegistry, RoleAgentSpec, get_default_role_registry
from .review_board import run_review_board
from .state_service import (
    StateFabricService,
    get_state_fabric_service,
    get_state_fabric_snapshot,
    get_state_fabric_status,
    start_state_fabric_service,
    stop_state_fabric_service,
)
from .task_dag import TaskDag, TaskDagNode, create_task_dag_from_work_orders, load_task_dag
from .task_decomposer import DecomposedTaskNode, TaskDecompositionPlan, decompose_task
from .task_guardian import TaskGuardian, get_task_guardian, scan_tasks_once, start_task_guardian, stop_task_guardian
from .weekly_review import WeeklyReviewResult, run_weekly_review

__all__ = [
    "AgentInputEnvelope",
    "CapabilityRecoveryRegistry",
    "CognitiveTurnContext",
    "DecisionContract",
    "DecomposedTaskNode",
    "DispatchResult",
    "KernelPlanningResult",
    "GrowthPipelineResult",
    "GraphSyncResult",
    "GraphConnectorResult",
    "LifecycleMemoryRecord",
    "MemoryFeedbackUpdate",
    "MemoryRecallRequest",
    "MemoryWriteRequest",
    "RelevantMemoryBundle",
    "RECALL_MEMORY_TOOL_ID",
    "WORK_ORDER_ALIAS_IDS",
    "RoleAgentRegistry",
    "RoleAgentReview",
    "RoleAgentReviewInput",
    "RoleExecutionContext",
    "RoleExecutionResult",
    "RoleExecutorRegistry",
    "SQL_DATA_SOP_PROMPT",
    "RoleAgentSpec",
    "RecoveryAttemptPlan",
    "RecoveryAttemptRecord",
    "RecoveryPlan",
    "RecoveryPlanner",
    "RecoveryCandidate",
    "ReviewSummary",
    "StateSnapshot",
    "StateFabricService",
    "TaskLedgerEntry",
    "TaskDag",
    "TaskDagNode",
    "TaskDecompositionPlan",
    "TaskGuardian",
    "TurnClosure",
    "VerificationReport",
    "WeeklyReviewResult",
    "WorkOrder",
    "arbitrate_review_summary",
    "build_cognitive_turn_context",
    "build_cognitive_kernel_system_prompt",
    "build_work_order_from_decision",
    "create_task_dag_from_work_orders",
    "build_fake_mcp_error_recovery_prompt",
    "build_fake_weather_error_recovery_prompt",
    "build_role_execution_system_prefix",
    "build_user_facing_reply_agent_system_prompt",
    "apply_feedback",
    "classify_memory_layer",
    "dispatch_tool_work_order",
    "dispatch_existing_work_order",
    "decompose_task",
    "extract_memory_scope",
    "get_default_role_executor_registry",
    "get_default_role_registry",
    "get_state_fabric_service",
    "get_state_fabric_snapshot",
    "get_state_fabric_status",
    "get_task_guardian",
    "is_hallucinated_final_mcp_error_json",
    "is_hallucinated_weather_service_error_json",
    "load_task_dag",
    "load_recovery_manifests",
    "initial_confidence",
    "plan_cognitive_turn",
    "recall_score",
    "recall_lifecycle_memories",
    "recall_memory_search",
    "record_lifecycle_memory_feedback",
    "run_review_board",
    "run_growth_pipeline",
    "run_weekly_review",
    "sync_memory_growth_graph",
    "sync_graph_engine_connectors",
    "scan_tasks_once",
    "start_state_fabric_service",
    "start_task_guardian",
    "stop_state_fabric_service",
    "stop_task_guardian",
    "write_lifecycle_memory",
]
