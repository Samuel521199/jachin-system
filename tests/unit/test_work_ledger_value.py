from __future__ import annotations


def _completed_event(event_id: str, summary: str) -> dict:
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
        "target_state": "completed",
    }


def test_value_chain_separates_completion_delivery_adoption_and_impact(
    tmp_path,
    monkeypatch,
):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(tmp_path / "ledger"))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.work_ledger import start_session
    from l3_node.work_ledger_facts import record_confirmed_work_event
    from l3_node.work_ledger_outcomes import get_session_outcome_context
    from l3_node.work_ledger_value import (
        get_project_value_chain,
        record_value_event,
    )

    session = start_session(
        title="Value chain",
        project_path=str(project),
        auto_collect=False,
    )
    session_id = str(session["session"]["session_id"])
    record_confirmed_work_event(
        session_id,
        _completed_event("verified-result", "Completed verified value-chain work"),
    )
    outcome = get_session_outcome_context(session_id)["outcomes_this_session"][0]
    outcome_id = str(outcome["outcome_id"])

    initial = get_project_value_chain(str(project))
    assert initial["outcome_values"][0]["value_stage"] == "completed"

    record_value_event(
        session_id,
        "delivered",
        outcome_ids=[outcome_id],
        channel="lark",
        idempotency_key="delivery-1",
    )
    record_value_event(
        session_id,
        "adopted",
        outcome_ids=[outcome_id],
        output_key="daily_report",
        idempotency_key="adoption-1",
    )
    record_value_event(
        session_id,
        "impact_confirmed",
        outcome_ids=[outcome_id],
        impact_value="Saved the handoff owner 30 minutes.",
        idempotency_key="impact-1",
    )
    record_value_event(
        session_id,
        "feedback_positive",
        outcome_ids=[outcome_id],
        note="Useful in the real handoff.",
        idempotency_key="feedback-1",
    )

    chain = get_project_value_chain(str(project))
    valued = chain["outcome_values"][0]
    assert valued["value_stage"] == "impact"
    assert valued["delivered_count"] == 1
    assert valued["adoption_count"] == 1
    assert valued["impact_count"] == 1
    assert valued["latest_feedback"] == "positive"
    assert chain["summary"]["impact_rate"] == 1.0


def test_negative_value_feedback_does_not_mutate_verified_fact(
    tmp_path,
    monkeypatch,
):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(tmp_path / "ledger"))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.work_ledger import start_session
    from l3_node.work_ledger_facts import (
        get_project_fact_index,
        record_confirmed_work_event,
    )
    from l3_node.work_ledger_outcomes import get_session_outcome_context
    from l3_node.work_ledger_value import record_value_event

    session = start_session(
        title="Immutable fact",
        project_path=str(project),
        auto_collect=False,
    )
    session_id = str(session["session"]["session_id"])
    recorded = record_confirmed_work_event(
        session_id,
        _completed_event("immutable", "Completed immutable verified result"),
    )
    outcome_id = str(
        get_session_outcome_context(session_id)["outcomes_this_session"][0][
            "outcome_id"
        ]
    )
    result = record_value_event(
        session_id,
        "feedback_negative",
        outcome_ids=[outcome_id],
        note="Correct but not useful for this audience.",
        idempotency_key="negative-feedback",
    )
    duplicate = record_value_event(
        session_id,
        "feedback_negative",
        outcome_ids=[outcome_id],
        note="Must not duplicate.",
        idempotency_key="negative-feedback",
    )

    value = result["chain"]["outcome_values"][0]
    fact = next(
        row
        for row in get_project_fact_index(str(project))["facts"]
        if row["fact_id"] == recorded["fact"]["fact_id"]
    )
    assert value["latest_feedback"] == "negative"
    assert value["value_score"] == 0.0
    assert fact["state"] == "completed"
    assert duplicate["deduplicated"] is True
    assert len(duplicate["chain"]["events"]) == 1


def test_continuation_and_methodology_reuse_metrics_are_independent(
    tmp_path,
    monkeypatch,
):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(tmp_path / "ledger"))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.work_ledger import start_session
    from l3_node.work_ledger_value import record_value_event

    session = start_session(
        title="Continuation metrics",
        project_path=str(project),
        auto_collect=False,
    )
    session_id = str(session["session"]["session_id"])
    record_value_event(
        session_id,
        "continuation_available",
        related_session_id="previous-session",
        idempotency_key="continuation-available",
    )
    record_value_event(
        session_id,
        "continuation_used",
        related_session_id="previous-session",
        output_key="codex_continuation_prompt",
        idempotency_key="continuation-used",
    )
    record_value_event(
        session_id,
        "methodology_reused",
        methodology_id="method-1",
        idempotency_key="method-reuse-success",
    )
    failed = record_value_event(
        session_id,
        "methodology_reuse_failed",
        methodology_id="method-2",
        idempotency_key="method-reuse-failed",
    )

    summary = failed["chain"]["summary"]
    assert summary["continuation_use_rate"] == 1.0
    assert summary["methodology_reuse_attempt_count"] == 2
    assert summary["methodology_reuse_success_rate"] == 0.5


def test_output_adoption_and_cross_session_continuation_feed_value_chain(
    tmp_path,
    monkeypatch,
):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(tmp_path / "ledger"))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))
    monkeypatch.setenv("JACHIN_WORK_LEDGER_LLM_ENABLED", "0")

    from l3_node.work_ledger import (
        adopt_work_output,
        end_session,
        generate_work_outputs,
        start_session,
    )
    from l3_node.work_ledger_facts import record_confirmed_work_event
    from l3_node.work_ledger_value import get_project_value_chain

    first = start_session(
        title="Previous work",
        project_path=str(project),
        auto_collect=False,
    )
    first_id = str(first["session"]["session_id"])
    record_confirmed_work_event(
        first_id,
        _completed_event("first-result", "Completed first reusable result"),
    )
    end_session(first_id, generate_outputs=True)

    second = start_session(
        title="Continued work",
        project_path=str(project),
        auto_collect=False,
    )
    second_id = str(second["session"]["session_id"])
    assert second["session"]["continuation_context"]["hit"] is True
    record_confirmed_work_event(
        second_id,
        _completed_event("second-result", "Completed continued result"),
    )
    generate_work_outputs(second_id)
    adopted = adopt_work_output(second_id, "codex_continuation_prompt")

    chain = get_project_value_chain(str(project))
    second_value = next(
        row
        for row in chain["outcome_values"]
        if row["summary"] == "Completed continued result"
    )
    assert adopted["value_event"]["event_type"] == "adopted"
    assert second_value["value_stage"] == "adopted"
    assert chain["summary"]["continuation_available_count"] == 1
    assert chain["summary"]["continuation_used_count"] == 1
