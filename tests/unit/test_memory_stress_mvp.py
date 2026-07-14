import json
import time


def test_lifecycle_memory_duplicate_storm_and_expiry_pressure(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import MemoryWriteRequest
    from l3_node.cognitive_kernel.memory_lifecycle import (
        expire_lifecycle_memories,
        recall_lifecycle_memories,
        write_lifecycle_memory,
    )

    request = MemoryWriteRequest(
        turn_id="stress-duplicate-storm",
        source_event="stress_test",
        memory_type="tool_habit",
        content="Browser focus recovery prefers verified Memory Growth playbooks.",
        confidence=0.76,
        ttl="permanent",
        merge_policy="dedupe_and_merge",
        evidence=[{"type": "stress", "ok": True}],
    )
    first = write_lifecycle_memory(request)
    last = first
    for _ in range(149):
        last = write_lifecycle_memory(request)

    assert last.memory_id == first.memory_id
    assert last.hit_count == 150
    assert last.confidence >= 0.76

    hits = recall_lifecycle_memories("Browser focus recovery", memory_types=["tool_habit"], limit=10)
    assert len(hits) == 1
    assert hits[0].memory_id == first.memory_id

    for index in range(40):
        write_lifecycle_memory(
            MemoryWriteRequest(
                turn_id=f"stress-expiry-{index}",
                source_event="stress_test",
                memory_type="short_term_action",
                content=f"Temporary stress task state {index}",
                confidence=0.7,
                ttl="1ms",
                merge_policy="append_action_chain",
            )
        )
    time.sleep(0.01)
    expired = expire_lifecycle_memories()
    assert expired >= 40
    assert not recall_lifecycle_memories("Temporary stress task state", memory_types=["short_term_action"], limit=5)


def test_daily_review_survives_large_duplicate_and_corrupt_raw_file(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.daily_review import run_daily_review
    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir

    ensure_memory_growth_scaffold()
    raw_path = memory_growth_dir() / "raw" / "evidence" / "20260714.stress.jsonl"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for index in range(60):
        rows.append(
            {
                "schema_version": 1,
                "event_id": f"raw-stress-{index}",
                "category": "evidence",
                "source": "turn_closure_agent",
                "payload": {
                    "turn_id": f"stress-turn-{index}",
                        "closure": {
                            "turn_id": f"stress-turn-{index}",
                            "closure_type": "completed",
                            "final_user_message_intent": f"Stress output report {index}",
                            "verification_status": "passed",
                            "executed_work_orders": [f"work-stress-{index}"],
                        "memory_write_requests": [
                            {
                                "memory_type": "historical_task_summary",
                                "content": json.dumps(
                                    {
                                        "turn_id": f"stress-turn-{index}",
                                        "verification_status": "passed",
                                        "summary": "stress daily review candidate",
                                    },
                                    ensure_ascii=False,
                                ),
                                "confidence": 0.74,
                                "merge_policy": "append_action_chain",
                                "requires_user_confirmation": False,
                                "evidence": [{"type": "stress", "index": index}],
                            }
                        ],
                    },
                    "promotion_hints": {"has_executed_work_orders": True},
                },
                    "review": {
                        "review_candidate": True,
                        "priority": "normal",
                        "promotion_targets": ["concepts", "playbooks", "outputs"],
                    },
                "source_refs": [{"type": "stress", "index": index}],
            }
        )
    text_lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    text_lines.append(json.dumps(rows[0], ensure_ascii=False))
    text_lines.append("{ this is not valid json")
    raw_path.write_text("\n".join(text_lines) + "\n", encoding="utf-8")

    result = run_daily_review("2026-07-14")
    assert result.raw_event_count == 61
    assert result.task_count == 61
    assert result.passed_count == 60
    assert result.concept_candidate_count >= 60
    assert result.playbook_candidate_count >= 60
    assert result.output_candidate_count >= 60
    assert result.warnings == ["1 invalid raw JSONL lines need repair"]

    patch = json.loads(result.patch_path.read_text(encoding="utf-8"))
    assert patch["summary"]["invalid_raw_count"] == 1
    assert len({item["candidate_id"] for item in patch["concept_candidates"]}) == len(patch["concept_candidates"])


def test_recovery_planner_extreme_attempt_limit_and_failure_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import DecisionContract, RiskLevel, ToolPolicy, VerificationReport, WorkOrder
    from l3_node.cognitive_kernel.recovery_planner import RecoveryAttemptRecord, RecoveryPlanner

    contract = DecisionContract(
        decision_id="decision-stress-recovery",
        turn_id="stress-recovery",
        task_type="app_control",
        goal="open browser under repeated focus failure",
        selected_roles=["AppControlExecutorAgent"],
        risk_level=RiskLevel.LOW,
        tool_policy=ToolPolicy(allowed_tools=["mcp:windows_window_switch"], risk_level=RiskLevel.LOW),
        execution_allowed=True,
        memory_context_refs=[
            {
                "bucket": "failure_hints",
                "memory_id": "memory_growth:playbook:browser-focus-timeout",
                "source": "Memory Growth Playbooks",
                "preview": "timeout focus retry longer timeout foreground strategy_weight=1.2; governance_execution_mode=batch_ok",
            }
        ],
    )
    work = WorkOrder(
        work_order_id="work-stress-recovery",
        decision_id=contract.decision_id,
        role_agent="AppControlExecutorAgent",
        task="switch Browser",
        inputs={"tool": "mcp:windows_window_switch", "work_order_input": '{"window_title":"Chrome"}'},
        tool_policy=contract.tool_policy,
    )
    verification = VerificationReport(
        verification_id="verify-stress-recovery",
        work_order_id=work.work_order_id,
        ok=False,
        failure_reason="timeout waiting for foreground window",
    )
    planner = RecoveryPlanner(
        max_attempts=2,
        registry=type(
            "EmptyRegistry",
            (),
            {
                "max_attempts_for": lambda self, **kwargs: kwargs.get("default", 2),
                "select_next": lambda self, **kwargs: None,
                "candidate_snapshot": lambda self, **kwargs: [],
            },
        )(),
    )
    records = [
        RecoveryAttemptRecord(
            attempt_no=1,
            work_order_id="work-recover-1",
            role_agent="AppControlExecutorAgent",
            tool="mcp:windows_window_switch",
            strategy="memory_growth_longer_timeout",
            rationale="first recovery",
            ok=False,
            verification_id="verify-1",
            failure_reason="timeout waiting for foreground window",
        ),
        RecoveryAttemptRecord(
            attempt_no=2,
            work_order_id="work-recover-2",
            role_agent="AppControlExecutorAgent",
            tool="mcp:windows_window_switch",
            strategy="memory_growth_retry_same_path",
            rationale="second recovery",
            ok=False,
            verification_id="verify-2",
            failure_reason="window still not foreground",
        ),
    ]

    assert planner.next_attempt(
        contract=contract,
        failed_work_order=work,
        verification=verification,
        attempt_records=records,
    ) is None
    report = planner.final_failure_report(
        contract=contract,
        attempt_records=records,
        last_verification=verification,
    )
    assert report["max_attempts"] == 2
    assert report["attempt_count"] == 2
    assert report["failure_counts"]["timeout waiting for foreground window"] == 1
    assert report["failure_counts"]["window still not foreground"] == 1
    assert report["memory_context_refs"] == contract.memory_context_refs
    assert any("2" in item for item in report["recommended_next_steps"])
