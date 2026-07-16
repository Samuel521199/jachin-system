from l3_node.capability_semantic_registry import CapabilityDescriptor
from l3_node.cognitive_kernel import (
    AgentInputEnvelope,
    DecisionContract,
    InputSource,
    MemoryEvidence,
    RelevantMemoryBundle,
    ReviewSummary,
    StateSnapshot,
    TaskLedgerEntry,
    VerificationReport,
    WorkOrder,
    arbitrate_review_summary,
    build_capability_intelligence,
    build_capability_intelligence_index,
    build_world_state_model,
    classify_failure_reason,
    decide_memory_promotion,
    decompose_task,
    interpret_goal,
    learn_from_failure,
    plan_cognitive_turn,
)
from l3_node.cognitive_kernel.capability_recovery_registry import CapabilityRecoveryRegistry
from l3_node.cognitive_kernel.contracts import RiskLevel, ToolPolicy
from l3_node.cognitive_kernel.pipeline import CognitiveTurnContext
from l3_node.cognitive_kernel.recovery_planner import RecoveryAttemptRecord, RecoveryPlanner


def test_goal_interpreter_extracts_message_delivery_slots():
    envelope = AgentInputEnvelope(
        turn_id="t-goal-1",
        source=InputSource.TEXT,
        raw_text="send hello to Neil",
        normalized_text="send hello to Neil",
    )

    goal = interpret_goal(envelope, capability_candidates=[{"id": "mcp:windows_lark_send_message", "task_type": "message_delivery", "score": 0.9}])

    assert goal.task_type == "message_delivery"
    assert goal.entities["recipients"] == ["Neil"]
    assert goal.entities["message"] == "hello"
    assert "external_message" in goal.risk_factors
    assert goal.missing_information == []
    assert goal.required_capabilities == ["mcp:windows_lark_send_message"]


def test_goal_interpreter_prioritizes_calculation_over_plain_open_app():
    envelope = AgentInputEnvelope(
        turn_id="t-goal-2",
        source=InputSource.TEXT,
        raw_text="open calculator and calculate 99+100",
        normalized_text="open calculator and calculate 99+100",
    )

    goal = interpret_goal(envelope)

    assert goal.task_type == "calculator_calculate"
    assert goal.entities["expression"] == "99+100"


def test_capability_intelligence_profiles_manifest_quality_and_dependencies():
    descriptor = CapabilityDescriptor(
        id="skill:test.message",
        domain="communication.test",
        actions=["send_message"],
        objects=["contact", "message"],
        inputs=["recipients", "message"],
        risk="external_effect",
        description="Send a test message.",
        examples=["send a test message"],
        task_type="message_delivery",
        evidence=["api_receipt"],
        metadata={
            "required_mcps": ["mcp:test.message"],
            "required_models": ["model:test"],
            "recovery_playbook": {"targets": [{"on": "timeout", "strategy": "retry_with_backoff"}]},
        },
    )

    profile = build_capability_intelligence(descriptor)
    index = build_capability_intelligence_index([descriptor], turn_id="t-capability")

    assert profile.capability_id == "skill:test.message"
    assert {"kind": "slot", "name": "recipient", "required": True} in profile.preconditions
    assert profile.required_mcps == ["mcp:test.message"]
    assert profile.required_models == ["model:test"]
    assert profile.recovery_paths[0]["strategy"] == "retry_with_backoff"
    assert profile.quality_score > 0.85
    assert index["skill:test.message"].task_type == "message_delivery"


def test_memory_promotion_engine_promotes_stable_memory_and_downranks_bad_memory():
    stable = {
        "memory_id": "mem-good",
        "memory_type": "user_preference",
        "confidence": 0.91,
        "success_count": 2,
        "failure_count": 0,
        "hit_count": 4,
        "status": "active",
    }
    bad = {
        "memory_id": "mem-bad",
        "memory_type": "tool_habit",
        "confidence": 0.62,
        "success_count": 0,
        "failure_count": 3,
        "hit_count": 1,
        "status": "active",
    }

    stable_decision = decide_memory_promotion(stable)
    bad_decision = decide_memory_promotion(bad)

    assert stable_decision.action == "promote_to_long_term"
    assert stable_decision.target_layer == "long_term"
    assert bad_decision.action == "downrank"


def test_world_state_model_distills_active_and_recent_apps():
    snapshot = StateSnapshot(
        snapshot_id="snap-1",
        generated_at_ms=1000,
        freshness_ms=200,
        active_window={"app_name": "Chrome", "title": "New Tab"},
        running_apps=[{"name": "Chrome"}, {"name": "Lark"}],
        recent_app_events=[
            {"app": "Calculator", "event": "opened"},
            {"app": "Lark", "event": "focused"},
        ],
        risk_state={"network": "ok"},
    )

    model = build_world_state_model(snapshot, turn_id="t-world")

    assert model.active_app == "Chrome"
    assert model.active_window_title == "New Tab"
    assert model.last_opened_app == "Lark"
    assert model.last_user_facing_app == "Chrome"
    assert model.gaps == []


def test_failure_learning_loop_classifies_and_proposes_next_strategy():
    work_order = WorkOrder(
        work_order_id="wo-1",
        decision_id="dec-1",
        role_agent="MessageExecutorAgent",
        task="message_delivery",
        inputs={"capability_id": "mcp:windows_lark_send_message"},
    )
    verification = VerificationReport(
        verification_id="ver-1",
        work_order_id="wo-1",
        ok=False,
        failure_reason="message send requires recipient/chat_id/to",
    )

    record = learn_from_failure(turn_id="t-fail", work_order=work_order, verification=verification, attempt_count=1)

    assert classify_failure_reason("window_not_found") == "target_not_found"
    assert record.failure_class == "invalid_input"
    assert record.next_strategy == "repair_slots_or_request_single_missing_field"
    assert record.memory_write["memory_type"] == "failure_hint"
    assert "recipient" in record.memory_write["content"]


def test_kernel_planning_result_carries_intelligence_foundation_context():
    text = "send hello to Neil"
    envelope = AgentInputEnvelope(
        turn_id="t-kernel-intel",
        source=InputSource.TEXT,
        raw_text=text,
        normalized_text=text,
    )
    state = StateSnapshot(
        snapshot_id="state-kernel-intel",
        generated_at_ms=1,
        freshness_ms=1,
        active_window={"app_name": "Jachin"},
        running_apps=[{"name": "Jachin"}, {"name": "Lark"}],
    )
    memory = RelevantMemoryBundle(
        turn_id="t-kernel-intel",
        recent_actions=[
            MemoryEvidence(
                memory_id="mem-recent-lark",
                memory_type="short_term_action",
                content="Opened Lark recently.",
                source="unit_test",
                confidence=0.9,
            )
        ],
        confidence=0.8,
    )
    ctx = CognitiveTurnContext(
        envelope=envelope,
        state_snapshot=state,
        memory_bundle=memory,
        ledger_entry=TaskLedgerEntry(
            turn_id=envelope.turn_id,
            input_envelope=envelope,
            state_snapshot=state,
            memory_bundle=memory,
        ),
    )

    plan = plan_cognitive_turn(ctx, emit_non_execution_closure=False)

    assert plan.goal_interpretation is not None
    assert plan.goal_interpretation.task_type == "message_delivery"
    assert plan.world_state_model is not None
    assert plan.world_state_model.active_app == "Jachin"
    assert plan.capability_profiles
    assert plan.to_dict()["goal_interpretation"]["task_type"] == "message_delivery"


def test_task_decomposer_uses_capability_profile_for_manifest_dag():
    descriptor = {
        "id": "skill:test.file.workflow",
        "domain": "file.test",
        "actions": ["read", "verify"],
        "objects": ["file"],
        "inputs": ["path"],
        "risk": "low",
        "description": "Manifest-driven file workflow.",
        "task_type": "file_operation",
        "metadata": {
            "decomposition": {
                "nodes": [
                    {
                        "id": "prepare",
                        "goal": "Prepare $target.path",
                        "role_agent": "FileExecutorAgent",
                        "tool": "mcp:test_prepare",
                        "inputs": {"path": "$target.path"},
                        "verification_criteria": ["path prepared"],
                    },
                    {
                        "id": "read",
                        "goal": "Read $target.path",
                        "role_agent": "FileExecutorAgent",
                        "tool": "mcp:test_read",
                        "depends_on": ["prepare"],
                        "inputs": {"path": "$target.path"},
                        "verification_criteria": ["content returned"],
                    },
                ]
            },
            "recovery_playbook": {
                "targets": [
                    {
                        "role_agent": "FileExecutorAgent",
                        "tools": ["mcp:test_read"],
                        "max_attempts": 4,
                        "steps": [
                            {
                                "strategy": "retry_with_normalized_path",
                                "tool": "$same",
                                "when": {"failure_any": ["path"]},
                                "action_patch": {"path": "$path"},
                            }
                        ],
                    }
                ]
            },
        },
    }
    summary = ReviewSummary(
        review_session_id="review-manifest",
        turn_id="t-manifest-dag",
        top_intent="file_read",
        task_type="file_operation",
        target={"path": "README.md", "name": "README.md"},
        selected_roles=["FileExecutorAgent"],
        candidate_tools=["mcp:test_read"],
        capability_candidates=[{"descriptor": descriptor}],
        risk_level=RiskLevel.LOW,
        confidence=0.9,
    )
    contract = DecisionContract(
        decision_id="decision-manifest",
        turn_id="t-manifest-dag",
        task_type="file_operation",
        goal="Read README.md",
        selected_roles=["FileExecutorAgent"],
        tool_policy=ToolPolicy(allowed_tools=["mcp:test_read"]),
        execution_allowed=True,
        verification_criteria=["content returned"],
    )

    plan = decompose_task(contract=contract, summary=summary)

    assert len(plan.nodes) == 2
    assert plan.nodes[1].depends_on == [plan.nodes[0].node_id]
    assert plan.nodes[0].inputs["capability_profile"]["capability_id"] == "skill:test.file.workflow"
    assert plan.nodes[1].recovery_policy["capability_recovery_paths"][0]["max_attempts"] == 4
    assert "skill:test.file.workflow" in plan.available_capabilities


def test_recovery_planner_uses_inline_capability_profile_paths():
    contract = DecisionContract(
        decision_id="decision-recovery",
        turn_id="t-recovery-inline",
        task_type="file_operation",
        goal="Read README.md",
        selected_roles=["FileExecutorAgent"],
        risk_level=RiskLevel.LOW,
        tool_policy=ToolPolicy(allowed_tools=["mcp:test_read"]),
        execution_allowed=True,
    )
    work_order = WorkOrder(
        work_order_id="work-recovery",
        decision_id=contract.decision_id,
        role_agent="FileExecutorAgent",
        task="file_operation",
        inputs={
            "tool": "mcp:test_read",
            "work_order_input": '{"path":"README.md"}',
            "capability_profile": {
                "capability_id": "skill:test.file.workflow",
                "recovery_paths": [
                    {
                        "role_agent": "FileExecutorAgent",
                        "tools": ["mcp:test_read"],
                        "max_attempts": 4,
                        "steps": [
                            {
                                "strategy": "retry_with_normalized_path",
                                "tool": "$same",
                                "when": {"failure_any": ["path"]},
                                "action_patch": {"path": "$path"},
                                "priority": 5,
                            }
                        ],
                    }
                ],
            },
        },
    )
    verification = VerificationReport(
        verification_id="ver-recovery",
        work_order_id=work_order.work_order_id,
        ok=False,
        failure_reason="path not found",
    )
    planner = RecoveryPlanner(max_attempts=2, registry=CapabilityRecoveryRegistry(manifests=[]))
    initial = planner.initial_plan(
        contract=contract,
        failed_work_order=work_order,
        verification=verification,
        attempt_no=1,
    )
    attempt = RecoveryAttemptRecord(
        attempt_no=1,
        work_order_id=work_order.work_order_id,
        role_agent=work_order.role_agent,
        tool="mcp:test_read",
        strategy="initial",
        rationale="initial",
        ok=False,
        verification_id=verification.verification_id,
        failure_reason=verification.failure_reason,
    )

    next_attempt = planner.next_attempt(
        contract=contract,
        failed_work_order=work_order,
        verification=verification,
        attempt_records=[attempt],
    )

    assert initial is not None
    assert initial.max_attempts == 4
    assert next_attempt is not None
    assert next_attempt.strategy == "retry_with_normalized_path"
    assert next_attempt.candidate_path["capability_id"] == "skill:test.file.workflow"


def test_capability_governance_health_can_force_confirmation(monkeypatch, tmp_path):
    governance_path = tmp_path / "os_evidence_governance_index.json"
    governance_path.write_text(
        """
{
  "index_path": "unit-test",
  "health": [
    {
      "capability": "skill:test.risky",
      "days": 7,
      "score": 42,
      "level": "critical",
      "evidence_count": 8,
      "block_rate": 0.63,
      "recovery_density": 0.1,
      "learning_density": 0.1,
      "top_issue": "message_post_send_verification_missing (5)",
      "suggestions": [{"severity":"critical","category":"quality","message":"bad","action":"fix"}]
    }
  ]
}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("JACHIN_OS_EVIDENCE_GOVERNANCE_INDEX", str(governance_path))
    summary = ReviewSummary(
        review_session_id="review-governance",
        turn_id="t-governance-confirm",
        top_intent="message_send",
        task_type="message_delivery",
        target={"recipients": ["Neil"], "message": "hello"},
        selected_roles=["MessageExecutorAgent"],
        candidate_tools=["mcp:test_send"],
        capability_candidates=[{"descriptor": {"id": "skill:test.risky", "task_type": "message_delivery"}}],
        risk_level=RiskLevel.LOW,
        confidence=0.9,
    )

    contract = arbitrate_review_summary(summary, goal="send hello")

    assert contract.tool_policy.requires_confirmation is True
    assert contract.execution_allowed is False
    assert "capability_health_critical_requires_confirmation" in contract.clarification_question


def test_task_decomposer_attaches_governance_policy_to_nodes(monkeypatch, tmp_path):
    governance_path = tmp_path / "os_evidence_governance_index.json"
    governance_path.write_text(
        '{"health":[{"capability":"skill:test.file.workflow","days":7,"score":65,"level":"degraded","evidence_count":4,"block_rate":0.3,"recovery_density":0.5,"learning_density":0.5,"top_issue":"","suggestions":[]}]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("JACHIN_OS_EVIDENCE_GOVERNANCE_INDEX", str(governance_path))
    descriptor = {
        "id": "skill:test.file.workflow",
        "domain": "file.test",
        "actions": ["read"],
        "objects": ["file"],
        "inputs": ["path"],
        "risk": "low",
        "task_type": "file_operation",
        "metadata": {
            "decomposition": {
                "nodes": [
                    {
                        "id": "read",
                        "goal": "Read $target.path",
                        "role_agent": "FileExecutorAgent",
                        "tool": "mcp:test_read",
                        "inputs": {"path": "$target.path"},
                        "verification_criteria": ["content returned"],
                    }
                ]
            }
        },
    }
    summary = ReviewSummary(
        review_session_id="review-governance-decomp",
        turn_id="t-governance-decomp",
        top_intent="file_read",
        task_type="file_operation",
        target={"path": "README.md", "name": "README.md"},
        selected_roles=["FileExecutorAgent"],
        candidate_tools=["mcp:test_read"],
        capability_candidates=[{"descriptor": descriptor}],
        risk_level=RiskLevel.LOW,
        confidence=0.9,
    )
    contract = DecisionContract(
        decision_id="decision-governance-decomp",
        turn_id="t-governance-decomp",
        task_type="file_operation",
        goal="Read README.md",
        selected_roles=["FileExecutorAgent"],
        tool_policy=ToolPolicy(allowed_tools=["mcp:test_read"]),
        execution_allowed=True,
        verification_criteria=["content returned"],
    )

    plan = decompose_task(contract=contract, summary=summary)

    policy = plan.nodes[0].inputs["governance_policy"]
    assert policy["score"] == 65
    assert policy["execution_mode"] == "degraded_auto"
    assert plan.nodes[0].recovery_policy["governance_policy"]["level"] == "degraded"


def test_recovery_planner_uses_governance_health_to_prefer_alternate_path():
    contract = DecisionContract(
        decision_id="decision-governance-recovery",
        turn_id="t-governance-recovery",
        task_type="file_operation",
        goal="Read README.md",
        selected_roles=["FileExecutorAgent"],
        risk_level=RiskLevel.LOW,
        tool_policy=ToolPolicy(allowed_tools=["mcp:test_read"]),
        execution_allowed=True,
    )
    work_order = WorkOrder(
        work_order_id="work-governance-recovery",
        decision_id=contract.decision_id,
        role_agent="FileExecutorAgent",
        task="file_operation",
        inputs={
            "tool": "mcp:test_read",
            "work_order_input": '{"path":"README.md"}',
            "governance_policy": {
                "capability": "skill:test.file.workflow",
                "score": 61,
                "level": "degraded",
                "execution_mode": "degraded_auto",
            },
            "capability_profile": {
                "capability_id": "skill:test.file.workflow",
                "recovery_paths": [
                    {
                        "role_agent": "FileExecutorAgent",
                        "tools": ["mcp:test_read"],
                        "max_attempts": 3,
                        "steps": [
                            {
                                "strategy": "retry_same_path",
                                "tool": "$same",
                                "when": {"failure_any": ["timeout"]},
                                "priority": 1,
                            },
                            {
                                "strategy": "switch_to_normalized_path",
                                "tool": "$same",
                                "when": {"failure_any": ["timeout"]},
                                "action_patch": {"path": "$path"},
                                "priority": 30,
                            },
                        ],
                    }
                ],
            },
        },
    )
    verification = VerificationReport(
        verification_id="ver-governance-recovery",
        work_order_id=work_order.work_order_id,
        ok=False,
        failure_reason="timeout while reading file",
    )
    planner = RecoveryPlanner(max_attempts=3, registry=CapabilityRecoveryRegistry(manifests=[]))

    attempt = planner.next_attempt(
        contract=contract,
        failed_work_order=work_order,
        verification=verification,
        attempt_records=[],
    )

    assert attempt is not None
    assert attempt.strategy == "switch_to_normalized_path"
    rationale = attempt.candidate_path["metadata"]["adaptive_scorecard"]["rationale"]
    assert "governance_low_health_alternate_path_bonus=35" in rationale
