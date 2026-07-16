from l3_node.cognitive_kernel.capability_recovery_registry import CapabilityRecoveryRegistry
from l3_node.cognitive_kernel.contracts import DecisionContract, RiskLevel, ToolPolicy, VerificationReport, WorkOrder
from l3_node.cognitive_kernel.recovery_planner import RecoveryAttemptRecord, RecoveryPlanner


def _contract() -> DecisionContract:
    return DecisionContract(
        decision_id="decision-adaptive-recovery",
        turn_id="turn-adaptive-recovery",
        task_type="web_research_delivery",
        goal="search latest AI model news and send Neil",
        selected_roles=["BrowserExecutorAgent", "MessageExecutorAgent"],
        risk_level=RiskLevel.LOW,
        tool_policy=ToolPolicy(allowed_tools=["mcp:tavily_search", "mcp:fetch", "core:web_research_summarize"]),
        execution_allowed=True,
    )


def _work(tool: str = "core:web_research_summarize") -> WorkOrder:
    return WorkOrder(
        work_order_id=f"work-adaptive-{tool.replace(':', '-').replace('_', '-')}",
        decision_id="decision-adaptive-recovery",
        role_agent="BrowserExecutorAgent",
        task="web_research_delivery",
        inputs={
            "tool": tool,
            "work_order_input": '{"query":"latest AI model news","recipients_json":"[\\"Neil\\"]"}',
        },
        tool_policy=ToolPolicy(allowed_tools=["mcp:tavily_search", "mcp:fetch", "core:web_research_summarize"]),
    )


def _verification(work: WorkOrder, reason: str) -> VerificationReport:
    return VerificationReport(
        verification_id=f"verify-{reason.replace(':', '-').replace('_', '-')[:40]}",
        work_order_id=work.work_order_id,
        ok=False,
        failure_reason=reason,
    )


def test_recovery_planner_refetches_when_summary_lacks_sources():
    planner = RecoveryPlanner(max_attempts=5, registry=CapabilityRecoveryRegistry())
    work = _work("core:web_research_summarize")
    verification = _verification(work, "tool_quality:summary_missing_source_urls")

    attempt = planner.next_attempt(
        contract=_contract(),
        failed_work_order=work,
        verification=verification,
        attempt_records=[],
    )

    assert attempt is not None
    assert attempt.strategy == "refetch_sources_for_summary"
    assert attempt.work_order.inputs["tool"] == "mcp:fetch"
    scorecard = attempt.candidate_path["metadata"]["adaptive_scorecard"]
    assert scorecard["current_failure_class"] == "tool_quality"
    assert any("missing_source_search_or_fetch_bonus" in item for item in scorecard["rationale"])


def test_recovery_planner_changes_path_after_refetch_also_fails():
    planner = RecoveryPlanner(max_attempts=5, registry=CapabilityRecoveryRegistry())
    work = _work("mcp:fetch")
    verification = _verification(work, "tool_quality:fetch_readable_content_missing")
    prior = RecoveryAttemptRecord(
        attempt_no=1,
        work_order_id="work-recover-1",
        role_agent="BrowserExecutorAgent",
        tool="mcp:fetch",
        strategy="refetch_sources_for_summary",
        rationale="refetch failed",
        ok=False,
        verification_id="verify-prior",
        failure_reason="tool_quality:summary_missing_source_urls",
    )

    attempt = planner.next_attempt(
        contract=_contract(),
        failed_work_order=work,
        verification=verification,
        attempt_records=[prior],
    )

    assert attempt is not None
    assert attempt.strategy == "regenerate_clean_summary"
    assert attempt.work_order.inputs["tool"] == "core:web_research_summarize"
    scorecard = attempt.candidate_path["metadata"]["adaptive_scorecard"]
    assert "tool_quality" in scorecard["history_failure_classes"]
    assert any("repeated_tool_quality_escalation_bonus" in item for item in scorecard["rationale"])


def test_recovery_planner_regenerates_bad_noisy_summary_directly():
    planner = RecoveryPlanner(max_attempts=5, registry=CapabilityRecoveryRegistry())
    work = _work("core:web_research_summarize")
    verification = _verification(work, "tool_quality:summary_contains_web_noise")

    attempt = planner.next_attempt(
        contract=_contract(),
        failed_work_order=work,
        verification=verification,
        attempt_records=[],
    )

    assert attempt is not None
    assert attempt.strategy == "regenerate_clean_summary"
    assert attempt.candidate_path["metadata"]["adaptive_scorecard"]["current_failure_class"] == "tool_quality"
