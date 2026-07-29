from __future__ import annotations


def _event(event_id: str, summary: str, **extra):
    return {
        "event_id": event_id,
        "summary": summary,
        "excerpt": summary,
        "source_types": ["codex"],
        "source_chain": [
            {
                "source_type": "codex",
                "source_uri": f"inline://codex/{event_id}",
            }
        ],
        "verification_evidence_id": f"ev-{event_id}",
        **extra,
    }


def test_outcome_graph_requires_verified_completed_fact_and_preserves_chain(
    tmp_path,
    monkeypatch,
):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(tmp_path / "ledger"))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.work_ledger import end_session, start_session
    from l3_node.work_ledger_facts import (
        record_confirmed_work_event,
        update_project_fact,
    )
    from l3_node.work_ledger_outcomes import (
        get_project_outcome_graph,
        get_session_outcome_context,
        review_methodology_candidate,
    )

    first_session = start_session(
        title="Initial delivery",
        project_path=str(project),
        auto_collect=False,
    )
    first_session_id = str(first_session["session"]["session_id"])
    created = record_confirmed_work_event(
        first_session_id,
        _event(
            "initial-completion",
            "Completed atomic project outcome graph persistence",
            target_state="completed",
            failure_reason="Concurrent writes previously truncated the graph.",
            decision="Use atomic replace with stable graph identifiers.",
            next_action="Run the cross-session concurrency regression suite.",
        ),
    )
    fact_id = str(created["fact"]["fact_id"])
    first_graph = get_project_outcome_graph(str(project))
    assert first_graph["summary"]["active_outcome_count"] == 1
    assert first_graph["summary"]["methodology_pending_count"] == 0
    end_session(first_session_id, generate_outputs=False)

    recovery_session = start_session(
        title="Regression recovery",
        project_path=str(project),
        auto_collect=False,
    )
    recovery_session_id = str(recovery_session["session"]["session_id"])
    update_project_fact(
        recovery_session_id,
        fact_id,
        target_state="reopened",
        reason="Concurrent regression reproduced.",
        failure_reason="A second writer exposed a stale temporary file.",
        next_action="Serialize graph replacement by project key.",
    )
    update_project_fact(
        recovery_session_id,
        fact_id,
        target_state="in_progress",
        reason="Recovery implementation is in progress.",
        decision="Guard graph replacement with a project-scoped lock.",
    )
    update_project_fact(
        recovery_session_id,
        fact_id,
        target_state="completed",
        reason="Concurrency regression suite passed twice.",
    )

    graph = get_project_outcome_graph(str(project))
    assert graph["summary"]["active_outcome_count"] == 1
    assert graph["summary"]["historical_outcome_count"] == 1
    assert graph["summary"]["methodology_pending_count"] == 1
    assert {
        edge["relation"] for edge in graph["edges"]
    } >= {
        "has_failure",
        "informed_decision",
        "drives_action",
        "produced_outcome",
        "verified_as",
        "suggests_methodology",
    }

    context = get_session_outcome_context(recovery_session_id)
    assert len(context["outcomes_this_session"]) == 1
    candidate = context["methodology_pending"][0]
    assert candidate["trigger"] == "A second writer exposed a stale temporary file."
    assert candidate["decision"] == "Guard graph replacement with a project-scoped lock."
    assert candidate["action"] == "Serialize graph replacement by project key."

    reviewed = review_methodology_candidate(
        str(project),
        candidate["candidate_id"],
        "approve",
        note="Validated by the 90-day replay.",
    )
    assert reviewed["candidate"]["status"] == "approved"
    approved_context = get_session_outcome_context(recovery_session_id)
    assert approved_context["methodology_pending"] == []
    assert [
        row["candidate_id"] for row in approved_context["methodology_approved"]
    ] == [candidate["candidate_id"]]


def test_reopened_fact_is_removed_from_active_outcomes_and_reports(
    tmp_path,
    monkeypatch,
):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(tmp_path / "ledger"))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.work_ledger import (
        build_performance_entries,
        build_weekly_report,
        get_session_detail,
        start_session,
    )
    from l3_node.work_ledger_facts import (
        record_confirmed_work_event,
        update_project_fact,
    )
    from l3_node.work_ledger_outcomes import get_project_outcome_graph

    session = start_session(
        title="Reopened result",
        project_path=str(project),
        auto_collect=False,
    )
    session_id = str(session["session"]["session_id"])
    created = record_confirmed_work_event(
        session_id,
        _event(
            "complete",
            "Completed verified weekly outcome accounting",
            target_state="completed",
        ),
    )
    update_project_fact(
        session_id,
        str(created["fact"]["fact_id"]),
        target_state="reopened",
        reason="Acceptance regression failed.",
        failure_reason="The completed result no longer passes acceptance.",
    )

    graph = get_project_outcome_graph(str(project))
    assert graph["summary"]["active_outcome_count"] == 0
    detail = get_session_detail(session_id, evidence_limit=100)
    performance = build_performance_entries(detail["session"], detail["evidence"])
    weekly = build_weekly_report(detail["session"], detail["evidence"])
    assert "暂无符合绩效成果口径" in performance
    assert "没有可计入成果的已验证完成事实" in weekly
    assert "Completed verified weekly outcome accounting。" not in performance
