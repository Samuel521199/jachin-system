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
    assert attempt.strategy == "refetch_with_text_mode"
    assert attempt.work_order.inputs["tool"] == "mcp:fetch"
    scorecard = attempt.candidate_path["metadata"]["adaptive_scorecard"]
    assert "tool_quality" in scorecard["history_failure_classes"]
    assert any("repeated_tool_quality_source_refresh_bonus" in item for item in scorecard["rationale"])


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


def test_recovery_planner_uses_failure_history_to_avoid_repeating_failed_tool_path():
    planner = RecoveryPlanner(max_attempts=5, registry=CapabilityRecoveryRegistry())
    work = _work("mcp:fetch")
    verification = _verification(work, "tool_quality:fetch_access_or_bot_wall")
    records = [
        RecoveryAttemptRecord(
            attempt_no=1,
            work_order_id="work-recover-1",
            role_agent="BrowserExecutorAgent",
            tool="mcp:fetch",
            strategy="refetch_sources_for_summary",
            rationale="source fetch failed",
            ok=False,
            verification_id="verify-1",
            failure_reason="tool_quality:summary_missing_source_urls",
        ),
        RecoveryAttemptRecord(
            attempt_no=2,
            work_order_id="work-recover-2",
            role_agent="BrowserExecutorAgent",
            tool="mcp:fetch",
            strategy="retry_with_backoff_hint",
            rationale="transport retry failed",
            ok=False,
            verification_id="verify-2",
            failure_reason="tool_quality:fetch_access_or_bot_wall",
        ),
    ]

    attempt = planner.next_attempt(
        contract=_contract(),
        failed_work_order=work,
        verification=verification,
        attempt_records=records,
    )

    assert attempt is not None
    assert attempt.strategy == "mark_source_blocked_and_search_alternative"
    assert attempt.work_order.inputs["tool"] == "mcp:tavily_search"
    scorecard = attempt.candidate_path["metadata"]["adaptive_scorecard"]
    assert any("new_tool_after_repeated_failures_bonus" in item for item in scorecard["rationale"])
    assert any("history_failure_match_bonus" in item for item in scorecard["rationale"])


def test_candidate_snapshot_explains_rejected_paths_after_failures():
    planner = RecoveryPlanner(max_attempts=5, registry=CapabilityRecoveryRegistry())
    work = _work("mcp:fetch")
    verification = _verification(work, "tool_quality:fetch_access_or_bot_wall")
    records = [
        RecoveryAttemptRecord(
            attempt_no=1,
            work_order_id="work-recover-1",
            role_agent="BrowserExecutorAgent",
            tool="mcp:fetch",
            strategy="refetch_sources_for_summary",
            rationale="source fetch failed",
            ok=False,
            verification_id="verify-1",
            failure_reason="tool_quality:summary_missing_source_urls",
        ),
        RecoveryAttemptRecord(
            attempt_no=2,
            work_order_id="work-recover-2",
            role_agent="BrowserExecutorAgent",
            tool="mcp:fetch",
            strategy="retry_with_backoff_hint",
            rationale="transport retry failed",
            ok=False,
            verification_id="verify-2",
            failure_reason="tool_quality:fetch_access_or_bot_wall",
        ),
    ]

    candidates = planner.candidate_paths(
        contract=_contract(),
        failed_work_order=work,
        verification=verification,
        attempt_records=records,
    )

    assert candidates
    assert any(item.get("eligible") is False for item in candidates)
    assert any(item.get("reject_reason") == "same_tool_and_strategy_already_failed" for item in candidates)
    selected = next(item for item in candidates if item.get("eligible"))
    assert selected["strategy"] == "mark_source_blocked_and_search_alternative"
    assert selected["metadata"]["adaptive_scorecard"]["score"] == selected["rank_score"]


def _app_contract() -> DecisionContract:
    return DecisionContract(
        decision_id="decision-app-recovery",
        turn_id="turn-app-recovery",
        task_type="app_control",
        goal="open browser",
        selected_roles=["AppControlExecutorAgent"],
        risk_level=RiskLevel.LOW,
        tool_policy=ToolPolicy(allowed_tools=["mcp:windows_open_app", "mcp:windows_window_switch", "mcp:windows_window_close"]),
        execution_allowed=True,
    )


def _app_work(tool: str, app_name: str = "Browser") -> WorkOrder:
    return WorkOrder(
        work_order_id=f"work-app-{tool.replace(':', '-').replace('_', '-')}",
        decision_id="decision-app-recovery",
        role_agent="AppControlExecutorAgent",
        task="app_control",
        inputs={
            "tool": tool,
            "target": {"name": app_name, "app_name": app_name},
            "work_order_input": f'{{"app_name":"{app_name}","keywords":"{app_name}"}}',
        },
        tool_policy=ToolPolicy(allowed_tools=["mcp:windows_open_app", "mcp:windows_window_switch", "mcp:windows_window_close"]),
    )


def test_app_control_recovery_changes_path_after_window_failures():
    planner = RecoveryPlanner(max_attempts=5, registry=CapabilityRecoveryRegistry())
    work = _app_work("mcp:windows_window_switch", "Browser")
    verification = _verification(work, "window_not_found")
    records = [
        RecoveryAttemptRecord(
            attempt_no=1,
            work_order_id="work-app-1",
            role_agent="AppControlExecutorAgent",
            tool="mcp:windows_window_switch",
            strategy="retry_same_path",
            rationale="first retry failed",
            ok=False,
            verification_id="verify-app-1",
            failure_reason="window_not_found",
        ),
        RecoveryAttemptRecord(
            attempt_no=2,
            work_order_id="work-app-2",
            role_agent="AppControlExecutorAgent",
            tool="mcp:windows_window_switch",
            strategy="switch_existing_window",
            rationale="switch failed",
            ok=False,
            verification_id="verify-app-2",
            failure_reason="window_not_found",
        ),
    ]

    attempt = planner.next_attempt(
        contract=_app_contract(),
        failed_work_order=work,
        verification=verification,
        attempt_records=records,
    )

    assert attempt is not None
    assert attempt.strategy == "open_app_then_verify"
    assert attempt.work_order.inputs["tool"] == "mcp:windows_open_app"
    scorecard = attempt.candidate_path["metadata"]["adaptive_scorecard"]
    assert any("changed_path_after_last_failure_bonus" in item for item in scorecard["rationale"])


def _file_contract() -> DecisionContract:
    return DecisionContract(
        decision_id="decision-file-recovery",
        turn_id="turn-file-recovery",
        task_type="file_to_app",
        goal="open missing file",
        selected_roles=["FileExecutorAgent"],
        risk_level=RiskLevel.LOW,
        tool_policy=ToolPolicy(allowed_tools=["mcp:windows_file_open", "mcp:windows_file_reveal_in_explorer", "core:fs_read"]),
        execution_allowed=True,
    )


def _file_work(tool: str = "mcp:windows_file_open") -> WorkOrder:
    return WorkOrder(
        work_order_id=f"work-file-{tool.replace(':', '-').replace('_', '-')}",
        decision_id="decision-file-recovery",
        role_agent="FileExecutorAgent",
        task="file_to_app",
        inputs={
            "tool": tool,
            "work_order_input": '{"path":"D:\\\\missing\\\\report.docx"}',
        },
        tool_policy=ToolPolicy(allowed_tools=["mcp:windows_file_open", "mcp:windows_file_reveal_in_explorer", "core:fs_read"]),
    )


def test_file_recovery_normalizes_then_reveals_parent_after_repeated_path_failure():
    planner = RecoveryPlanner(max_attempts=5, registry=CapabilityRecoveryRegistry())
    work = _file_work()
    verification = _verification(work, "path_not_found")
    first = planner.next_attempt(
        contract=_file_contract(),
        failed_work_order=work,
        verification=verification,
        attempt_records=[],
    )
    assert first is not None
    assert first.strategy == "normalize_path_retry"

    second = planner.next_attempt(
        contract=_file_contract(),
        failed_work_order=work,
        verification=verification,
        attempt_records=[
            RecoveryAttemptRecord(
                attempt_no=1,
                work_order_id=first.work_order.work_order_id,
                role_agent="FileExecutorAgent",
                tool=first.work_order.inputs["tool"],
                strategy=first.strategy,
                rationale=first.rationale,
                ok=False,
                verification_id="verify-file-1",
                failure_reason="path_not_found",
            )
        ],
    )

    assert second is not None
    assert second.strategy == "reveal_parent_for_user_confirmation"
    assert second.work_order.inputs["tool"] == "mcp:windows_file_reveal_in_explorer"


def test_permission_failure_does_not_auto_recover_without_user_authorization():
    planner = RecoveryPlanner(max_attempts=5, registry=CapabilityRecoveryRegistry())
    work = _file_work("core:fs_read")
    verification = _verification(work, "permission_not_allowed")

    attempt = planner.next_attempt(
        contract=_file_contract(),
        failed_work_order=work,
        verification=verification,
        attempt_records=[],
    )

    assert attempt is None


def test_final_failure_report_contains_timeline_and_quality_score():
    planner = RecoveryPlanner(max_attempts=5, registry=CapabilityRecoveryRegistry())
    records = [
        RecoveryAttemptRecord(
            attempt_no=1,
            work_order_id="work-1",
            role_agent="BrowserExecutorAgent",
            tool="mcp:tavily_search",
            strategy="retry_search_with_clean_query",
            rationale="search retry",
            ok=False,
            verification_id="verify-1",
            failure_reason="search_results_missing",
        ),
        RecoveryAttemptRecord(
            attempt_no=2,
            work_order_id="work-2",
            role_agent="BrowserExecutorAgent",
            tool="mcp:fetch",
            strategy="refetch_sources_for_summary",
            rationale="fetch retry",
            ok=False,
            verification_id="verify-2",
            failure_reason="tool_quality:fetch_access_or_bot_wall",
        ),
        RecoveryAttemptRecord(
            attempt_no=3,
            work_order_id="work-3",
            role_agent="BrowserExecutorAgent",
            tool="core:web_research_summarize",
            strategy="regenerate_clean_summary",
            rationale="summary retry",
            ok=False,
            verification_id="verify-3",
            failure_reason="tool_quality:summary_contains_web_noise",
        ),
        RecoveryAttemptRecord(
            attempt_no=4,
            work_order_id="work-4",
            role_agent="BrowserExecutorAgent",
            tool="mcp:fetch",
            strategy="retry_with_backoff_hint",
            rationale="backoff retry",
            ok=False,
            verification_id="verify-4",
            failure_reason="connection_timeout",
        ),
        RecoveryAttemptRecord(
            attempt_no=5,
            work_order_id="work-5",
            role_agent="BrowserExecutorAgent",
            tool="core:web_research_summarize",
            strategy="regenerate_clean_summary_v2",
            rationale="final clean retry",
            ok=False,
            verification_id="verify-5",
            failure_reason="tool_quality:summary_incomplete_sentence",
        ),
    ]

    report = planner.final_failure_report(
        contract=_contract(),
        attempt_records=records,
        last_verification=_verification(_work(), "tool_quality:summary_incomplete_sentence"),
    )

    assert report["stopped_reason"] == "max_attempts_reached"
    assert report["recovery_quality_score"] >= 80
    assert len(report["failure_timeline"]) == 5
    assert "mcp:fetch" in report["exhausted_tools"]
    assert "regenerate_clean_summary" in report["exhausted_strategies"]
