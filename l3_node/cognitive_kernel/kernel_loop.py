"""Finite cognitive turn planner for the Memory-first Kernel mainline."""

from __future__ import annotations

from dataclasses import dataclass, field

from .arbiter import arbitrate_review_summary, build_work_orders_from_decision
from .contracts import ClosureType, DecisionContract, ReviewSummary, TurnClosure, WorkOrder
from .capability_intelligence import CapabilityIntelligenceProfile, build_capability_intelligence
from .goal_interpreter import GoalInterpretation, interpret_goal
from .ledger import append_event, record_turn_closure
from .pipeline import CognitiveTurnContext
from .review_board import run_review_board
from .task_dag import TaskDag, create_task_dag_from_work_orders
from .world_state_model import WorldStateModel, build_world_state_model


@dataclass(slots=True)
class KernelPlanningResult:
    review_summary: ReviewSummary
    decision_contract: DecisionContract
    work_orders: list[WorkOrder] = field(default_factory=list)
    closure: TurnClosure | None = None
    task_dag: TaskDag | None = None
    goal_interpretation: GoalInterpretation | None = None
    capability_profiles: list[CapabilityIntelligenceProfile] = field(default_factory=list)
    world_state_model: WorldStateModel | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "review_summary": self.review_summary.to_dict(),
            "decision_contract": self.decision_contract.to_dict(),
            "work_orders": [x.to_dict() for x in self.work_orders],
            "closure": self.closure.to_dict() if self.closure else None,
            "task_dag": self.task_dag.to_dict() if self.task_dag else None,
            "goal_interpretation": self.goal_interpretation.to_dict() if self.goal_interpretation else None,
            "capability_profiles": [x.to_dict() for x in self.capability_profiles],
            "world_state_model": self.world_state_model.to_dict() if self.world_state_model else None,
        }


def plan_cognitive_turn(ctx: CognitiveTurnContext, *, emit_non_execution_closure: bool = True) -> KernelPlanningResult:
    """Run the non-mutating mainline: ReviewBoard -> Arbiter -> WorkOrder plan.

    This does not call external-world tools. Execution remains the Dispatcher's
    job after a WorkOrder is authorized.
    """

    world_state_model = build_world_state_model(ctx.state_snapshot, turn_id=ctx.envelope.turn_id)
    review_summary = run_review_board(
        envelope=ctx.envelope,
        state_snapshot=ctx.state_snapshot,
        memory_bundle=ctx.memory_bundle,
    )
    capability_profiles = _capability_profiles_from_review(review_summary)
    goal_interpretation = interpret_goal(
        ctx.envelope,
        state_snapshot=ctx.state_snapshot,
        memory_bundle=ctx.memory_bundle,
        capability_candidates=review_summary.capability_candidates,
    )
    _attach_intelligence_to_review_summary(
        review_summary=review_summary,
        goal_interpretation=goal_interpretation,
        capability_profiles=capability_profiles,
        world_state_model=world_state_model,
    )
    contract = arbitrate_review_summary(review_summary, goal=ctx.envelope.normalized_text or ctx.envelope.raw_text)
    work_orders = build_work_orders_from_decision(contract, review_summary)
    task_dag = _maybe_create_task_dag(ctx, contract, work_orders)
    closure = _maybe_close_without_execution(contract, review_summary) if emit_non_execution_closure else None
    result = KernelPlanningResult(
        review_summary=review_summary,
        decision_contract=contract,
        work_orders=work_orders,
        closure=closure,
        task_dag=task_dag,
        goal_interpretation=goal_interpretation,
        capability_profiles=capability_profiles,
        world_state_model=world_state_model,
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
            "goal_id": goal_interpretation.goal_id,
            "goal_task_type": goal_interpretation.task_type,
            "capability_profile_count": len(capability_profiles),
            "world_state_model_id": world_state_model.model_id,
        },
    )
    return result


def _capability_profiles_from_review(review_summary: ReviewSummary) -> list[CapabilityIntelligenceProfile]:
    profiles: list[CapabilityIntelligenceProfile] = []
    seen: set[str] = set()
    for candidate in review_summary.capability_candidates or []:
        descriptor = candidate.get("descriptor") if isinstance(candidate, dict) else None
        payload = descriptor if isinstance(descriptor, dict) else candidate
        if not isinstance(payload, dict):
            continue
        cap_id = str(payload.get("id") or payload.get("capability_id") or "")
        if cap_id and cap_id in seen:
            continue
        profile = build_capability_intelligence(payload)
        if profile.capability_id:
            seen.add(profile.capability_id)
            profiles.append(profile)
    if not profiles and review_summary.candidate_tools:
        for tool in review_summary.candidate_tools:
            if not tool or tool in seen:
                continue
            profile = build_capability_intelligence(
                {
                    "id": tool,
                    "domain": review_summary.task_type,
                    "actions": [review_summary.top_intent],
                    "objects": [str((review_summary.target or {}).get("name") or "")],
                    "inputs": list((review_summary.target or {}).keys()),
                    "risk": review_summary.risk_level.value,
                    "description": f"ReviewBoard selected {tool} for {review_summary.task_type}.",
                    "task_type": review_summary.task_type,
                    "evidence": review_summary.rationale,
                    "source": "review_board",
                    "metadata": {},
                }
            )
            seen.add(tool)
            profiles.append(profile)
    return profiles


def _attach_intelligence_to_review_summary(
    *,
    review_summary: ReviewSummary,
    goal_interpretation: GoalInterpretation,
    capability_profiles: list[CapabilityIntelligenceProfile],
    world_state_model: WorldStateModel,
) -> None:
    review_summary.rationale.extend(
        [
            f"GoalInterpreter task_type={goal_interpretation.task_type} confidence={goal_interpretation.confidence:.2f}.",
            f"WorldStateModel active_app={world_state_model.active_app or 'unknown'} confidence={world_state_model.confidence:.2f}.",
        ]
    )
    if capability_profiles:
        review_summary.rationale.append(
            "CapabilityIntelligence profiles="
            + ", ".join(f"{p.capability_id}:{p.quality_score:.2f}" for p in capability_profiles[:5])
        )
    if goal_interpretation.missing_information:
        review_summary.rationale.append(
            "GoalInterpreter observed missing slots but did not override ReviewBoard: "
            + ", ".join(goal_interpretation.missing_information)
        )
    append_event(
        "kernel_intelligence_context",
        review_summary.turn_id,
        {
            "goal_interpretation": goal_interpretation.to_dict(),
            "capability_profiles": [profile.to_dict() for profile in capability_profiles],
            "world_state_model": world_state_model.to_dict(),
        },
    )


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
