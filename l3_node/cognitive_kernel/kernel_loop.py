"""Finite cognitive turn planner for the Memory-first Kernel mainline."""

from __future__ import annotations

from dataclasses import dataclass, field

from .arbiter import arbitrate_review_summary, build_work_order_from_decision
from .contracts import ClosureType, DecisionContract, ReviewSummary, TurnClosure, WorkOrder
from .ledger import append_event, record_turn_closure
from .pipeline import CognitiveTurnContext
from .review_board import run_review_board
from .task_dag import TaskDag, create_task_dag_from_work_orders


@dataclass(slots=True)
class KernelPlanningResult:
    review_summary: ReviewSummary
    decision_contract: DecisionContract
    work_orders: list[WorkOrder] = field(default_factory=list)
    closure: TurnClosure | None = None
    task_dag: TaskDag | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "review_summary": self.review_summary.to_dict(),
            "decision_contract": self.decision_contract.to_dict(),
            "work_orders": [x.to_dict() for x in self.work_orders],
            "closure": self.closure.to_dict() if self.closure else None,
            "task_dag": self.task_dag.to_dict() if self.task_dag else None,
        }


def plan_cognitive_turn(ctx: CognitiveTurnContext, *, emit_non_execution_closure: bool = True) -> KernelPlanningResult:
    """Run the non-mutating mainline: ReviewBoard -> Arbiter -> WorkOrder plan.

    This does not call external-world tools. Execution remains the Dispatcher's
    job after a WorkOrder is authorized.
    """

    review_summary = run_review_board(
        envelope=ctx.envelope,
        state_snapshot=ctx.state_snapshot,
        memory_bundle=ctx.memory_bundle,
    )
    contract = arbitrate_review_summary(review_summary, goal=ctx.envelope.normalized_text or ctx.envelope.raw_text)
    work_order = build_work_order_from_decision(contract, review_summary)
    work_orders = [work_order] if work_order else []
    task_dag = _maybe_create_task_dag(ctx, contract, work_orders)
    closure = _maybe_close_without_execution(contract, review_summary) if emit_non_execution_closure else None
    result = KernelPlanningResult(
        review_summary=review_summary,
        decision_contract=contract,
        work_orders=work_orders,
        closure=closure,
        task_dag=task_dag,
    )
    append_event(
        "kernel_planning_finished",
        ctx.envelope.turn_id,
        {
            "review_session_id": review_summary.review_session_id,
            "decision_id": contract.decision_id,
            "work_order_ids": [x.work_order_id for x in work_orders],
            "closure_type": closure.closure_type.value if closure else "",
            "task_dag_id": task_dag.dag_id if task_dag else "",
        },
    )
    return result


def _maybe_create_task_dag(
    ctx: CognitiveTurnContext,
    contract: DecisionContract,
    work_orders: list[WorkOrder],
) -> TaskDag | None:
    text = (ctx.envelope.normalized_text or ctx.envelope.raw_text or "").lower()
    complex_markers = (
        "然后",
        "并且",
        "同时",
        "整理",
        "总结",
        "发送",
        "发给",
        "workflow",
        "报告",
        "多步骤",
        "batch",
    )
    should_create = len(work_orders) > 1 or any(marker in text for marker in complex_markers)
    if not should_create:
        return None
    return create_task_dag_from_work_orders(
        turn_id=contract.turn_id,
        goal=contract.goal,
        contract=contract,
        work_orders=work_orders,
        background=contract.task_type not in {"conversation", "app_control"},
    )


def _maybe_close_without_execution(contract: DecisionContract, review_summary: ReviewSummary) -> TurnClosure | None:
    if contract.execution_allowed:
        return None
    if review_summary.task_type == "conversation":
        closure = TurnClosure(
            turn_id=contract.turn_id,
            closure_type=ClosureType.ANSWERED,
            final_user_message_intent="conversation_reply",
            verification_status="not_required",
            next_turn_hints=["UserFacingReplyAgent should answer without external-world action."],
        )
        record_turn_closure(closure)
        return closure
    if contract.clarification_question:
        closure = TurnClosure(
            turn_id=contract.turn_id,
            closure_type=ClosureType.WAITING_USER,
            final_user_message_intent="ask_clarification",
            verification_status="not_required",
            pending_decision=contract.to_dict(),
            next_turn_hints=[contract.clarification_question],
        )
        record_turn_closure(closure)
        return closure
    return None
