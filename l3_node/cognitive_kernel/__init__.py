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
    StateSnapshot,
    TaskLedgerEntry,
    TurnClosure,
    VerificationReport,
    WorkOrder,
)
from .arbiter import arbitrate_review_summary, build_work_order_from_decision
from .dispatcher import DispatchResult, dispatch_existing_work_order, dispatch_tool_work_order
from .kernel_loop import KernelPlanningResult, plan_cognitive_turn
from .memory_lifecycle import (
    LifecycleMemoryRecord,
    recall_lifecycle_memories,
    write_lifecycle_memory,
)
from .memory_tools import recall_memory_search
from .pipeline import CognitiveTurnContext, build_cognitive_turn_context
from .prompt_policies import SQL_DATA_SOP_PROMPT
from .pseudo_actions import REACT_PSEUDO_ACTION_IDS, RECALL_MEMORY_TOOL_ID
from .recovery_guards import (
    build_fake_mcp_error_recovery_prompt,
    build_fake_weather_error_recovery_prompt,
    is_hallucinated_final_mcp_error_json,
    is_hallucinated_weather_service_error_json,
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
from .task_guardian import TaskGuardian, get_task_guardian, scan_tasks_once, start_task_guardian, stop_task_guardian

__all__ = [
    "AgentInputEnvelope",
    "CognitiveTurnContext",
    "DecisionContract",
    "DispatchResult",
    "KernelPlanningResult",
    "LifecycleMemoryRecord",
    "MemoryRecallRequest",
    "MemoryWriteRequest",
    "RelevantMemoryBundle",
    "REACT_PSEUDO_ACTION_IDS",
    "RECALL_MEMORY_TOOL_ID",
    "RoleAgentRegistry",
    "RoleAgentReview",
    "RoleAgentReviewInput",
    "RoleExecutionContext",
    "RoleExecutionResult",
    "RoleExecutorRegistry",
    "SQL_DATA_SOP_PROMPT",
    "RoleAgentSpec",
    "ReviewSummary",
    "StateSnapshot",
    "StateFabricService",
    "TaskLedgerEntry",
    "TaskDag",
    "TaskDagNode",
    "TaskGuardian",
    "TurnClosure",
    "VerificationReport",
    "WorkOrder",
    "arbitrate_review_summary",
    "build_cognitive_turn_context",
    "build_work_order_from_decision",
    "create_task_dag_from_work_orders",
    "build_fake_mcp_error_recovery_prompt",
    "build_fake_weather_error_recovery_prompt",
    "dispatch_tool_work_order",
    "dispatch_existing_work_order",
    "get_default_role_executor_registry",
    "get_default_role_registry",
    "get_state_fabric_service",
    "get_state_fabric_snapshot",
    "get_state_fabric_status",
    "get_task_guardian",
    "is_hallucinated_final_mcp_error_json",
    "is_hallucinated_weather_service_error_json",
    "load_task_dag",
    "plan_cognitive_turn",
    "recall_lifecycle_memories",
    "recall_memory_search",
    "run_review_board",
    "scan_tasks_once",
    "start_state_fabric_service",
    "start_task_guardian",
    "stop_state_fabric_service",
    "stop_task_guardian",
    "write_lifecycle_memory",
]
