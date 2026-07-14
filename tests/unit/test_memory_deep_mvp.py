import json


def test_confirmed_entity_correction_feeds_review_board_and_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import (
        AgentInputEnvelope,
        InputSource,
        RelevantMemoryBundle,
        RiskLevel,
        StateSnapshot,
        ToolPolicy,
        WorkOrder,
    )
    from l3_node.cognitive_kernel.entity_corrections import (
        get_learned_app_correction,
        record_confirmed_entity_correction_from_work_order,
    )
    from l3_node.cognitive_kernel.memory_lifecycle import recall_lifecycle_memories
    from l3_node.cognitive_kernel.review_board import run_review_board

    confirmed_work = WorkOrder(
        work_order_id="work-correction-confirmed",
        decision_id="decision-correction-confirmed",
        role_agent="AppControlExecutorAgent",
        task="open Lark after user confirmation",
        inputs={
            "tool": "mcp:windows_open_app",
            "target": {
                "type": "app",
                "name": "Lark",
                "heard_as": "lock",
                "candidate_alias": "lark",
                "entity_score": 0.91,
                "requires_entity_confirmation": True,
            },
        },
        tool_policy=ToolPolicy(allowed_tools=["mcp:windows_open_app"], risk_level=RiskLevel.LOW),
    )
    assert record_confirmed_entity_correction_from_work_order(
        work_order=confirmed_work,
        turn_id="turn-correction-confirmed",
    )

    learned = get_learned_app_correction("lock")
    assert learned["name"] == "Lark"
    assert learned["source"] == "learned_entity_correction"
    assert learned["requires_confirmation"] is False

    correction_hits = recall_lifecycle_memories("lock Lark", memory_types=["correction"], limit=5)
    assert correction_hits
    assert "app_entity_correction" in correction_hits[0].content

    summary = run_review_board(
        envelope=AgentInputEnvelope(
            turn_id="turn-open-lock-after-learning",
            source=InputSource.TEXT,
            raw_text="打开 lock",
            normalized_text="打开 lock",
        ),
        state_snapshot=StateSnapshot(
            snapshot_id="state-open-lock",
            generated_at_ms=1,
            freshness_ms=1,
            risk_state={"unsaved_documents": "unknown"},
        ),
        memory_bundle=RelevantMemoryBundle(turn_id="turn-open-lock-after-learning"),
    )
    assert summary.top_intent == "open_app"
    assert summary.task_type == "app_control"
    assert summary.target["name"] == "Lark"
    assert summary.target["source"] == "learned_entity_correction"
    assert summary.needs_clarification is False
    assert "mcp:windows_open_app" in summary.candidate_tools


def test_memory_growth_playbook_recall_drives_recovery_next_attempt(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import (
        AgentInputEnvelope,
        InputSource,
        MemoryRecallRequest,
        RiskLevel,
        ToolPolicy,
        VerificationReport,
        WorkOrder,
    )
    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.cognitive_kernel.memory_growth_recall import recall_memory_growth
    from l3_node.cognitive_kernel.recovery_planner import RecoveryAttemptRecord, RecoveryPlanner
    from l3_node.cognitive_kernel.runtime import build_decision_contract

    ensure_memory_growth_scaffold()
    playbook_path = memory_growth_dir() / "playbooks" / "browser-open-focus.md"
    playbook_path.write_text(
        """---
id: "playbook:browser-open-focus"
type: "failure_playbook"
summary: "Recover browser focus timeout with longer timeout, then retry foreground switch"
confidence: 0.88
governance_strategy_weight: 1.25
governance_execution_mode: "batch_ok"
governance_requires_more_evidence: false
---

# Browser Focus Timeout Recovery

## Failure Paths

- If opening Browser fails with timeout or slow foreground detection, use longer timeout.
- If the longer timeout still fails, retry the same window switch path and verify foreground.
""",
        encoding="utf-8",
    )

    envelope = AgentInputEnvelope(
        turn_id="turn-browser-recovery",
        source=InputSource.TEXT,
        raw_text="打开浏览器",
        normalized_text="打开浏览器",
    )
    memories, gaps = recall_memory_growth(
        MemoryRecallRequest(
            turn_id=envelope.turn_id,
            input_envelope=envelope,
            candidate_intents=["open_app"],
            candidate_task_domains=["app_control"],
            multi_queries={"goal": "open browser focus timeout recovery playbook"},
            retrieval_channels=["memory_growth_playbook_memory"],
        )
    )
    assert "memory_growth_no_relevant_concepts_or_playbooks" not in gaps
    playbook_memory = next(item for item in memories if item.memory_id == "memory_growth:playbook:browser-open-focus")
    assert playbook_memory.memory_type == "failure_hint"

    contract = build_decision_contract(
        turn_id=envelope.turn_id,
        goal="open browser",
        tool="mcp:windows_window_switch",
        work_order_input='{"window_title":"Chrome"}',
    )
    contract.task_type = "app_control"
    contract.risk_level = RiskLevel.LOW
    contract.tool_policy = ToolPolicy(allowed_tools=["mcp:windows_window_switch"], risk_level=RiskLevel.LOW)
    contract.memory_context_refs = [
        {
            "bucket": "failure_hints",
            "memory_id": playbook_memory.memory_id,
            "source": playbook_memory.source,
            "confidence": playbook_memory.confidence,
            "preview": playbook_memory.content,
            "relevance_reason": playbook_memory.relevance_reason,
        }
    ]
    work = WorkOrder(
        work_order_id="work-browser-recovery",
        decision_id=contract.decision_id,
        role_agent="AppControlExecutorAgent",
        task="switch Browser",
        inputs={"tool": "mcp:windows_window_switch", "work_order_input": '{"window_title":"Chrome"}'},
        tool_policy=contract.tool_policy,
    )
    verification = VerificationReport(
        verification_id="verify-browser-recovery",
        work_order_id=work.work_order_id,
        ok=False,
        failure_reason="timeout waiting for foreground window",
    )
    planner = RecoveryPlanner(
        max_attempts=3,
        registry=type(
            "EmptyRegistry",
            (),
            {
                "max_attempts_for": lambda self, **kwargs: kwargs.get("default", 3),
                "select_next": lambda self, **kwargs: None,
                "candidate_snapshot": lambda self, **kwargs: [],
            },
        )(),
    )

    first = planner.next_attempt(
        contract=contract,
        failed_work_order=work,
        verification=verification,
        attempt_records=[],
    )
    assert first is not None
    assert first.strategy == "memory_growth_longer_timeout"
    first_input = json.loads(first.work_order.inputs["work_order_input"])
    assert first_input["timeout"] == 12.0
    assert first.candidate_path["metadata"]["source"] == "memory_growth"
    assert first.candidate_path["metadata"]["governance_execution_mode"] == "batch_ok"

    second = planner.next_attempt(
        contract=contract,
        failed_work_order=work,
        verification=verification,
        attempt_records=[
            RecoveryAttemptRecord(
                attempt_no=1,
                work_order_id=first.work_order.work_order_id,
                role_agent=first.work_order.role_agent,
                tool=first.work_order.inputs["tool"],
                strategy=first.strategy,
                rationale=first.rationale,
                ok=False,
                verification_id=verification.verification_id,
                failure_reason=verification.failure_reason,
            )
        ],
    )
    assert second is not None
    assert second.strategy == "memory_growth_retry_same_path"
    second_input = json.loads(second.work_order.inputs["work_order_input"])
    assert second_input["recovery_strategy"] == "memory_growth_retry_same_path"
