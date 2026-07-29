from __future__ import annotations

import json
from pathlib import Path

import pytest


def _completed_event() -> dict:
    return {
        "event_id": "diagnostic-completed",
        "summary": "Completed diagnostic value result",
        "excerpt": "Completed diagnostic value result",
        "source_types": ["codex"],
        "source_chain": [
            {
                "source_type": "codex",
                "source_uri": "inline://codex/diagnostic",
            }
        ],
        "verification_evidence_id": "verification-diagnostic",
        "target_state": "completed",
    }


def test_value_chain_diagnostic_writes_jsonl_and_markdown_log(
    tmp_path,
    monkeypatch,
):
    project = tmp_path / "project"
    project.mkdir()
    ledger_home = tmp_path / "ledger"
    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(ledger_home))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.work_ledger import start_session
    from l3_node.work_ledger_facts import record_confirmed_work_event
    from l3_node.work_ledger_outcomes import get_session_outcome_context
    from l3_node.work_ledger_value import record_value_event
    from l3_node.work_ledger_value_diagnostics import (
        read_value_diagnostic_logs,
        run_value_chain_diagnostics,
    )

    session = start_session(
        title="Diagnostic session",
        project_path=str(project),
        auto_collect=False,
    )
    session_id = str(session["session"]["session_id"])
    record_confirmed_work_event(session_id, _completed_event())
    outcome_id = str(
        get_session_outcome_context(session_id)["outcomes_this_session"][0][
            "outcome_id"
        ]
    )
    record_value_event(
        session_id,
        "delivered",
        outcome_ids=[outcome_id],
        evidence_id="delivery-evidence",
        idempotency_key="diagnostic-delivery",
    )

    result = run_value_chain_diagnostics(session_id)
    logs = read_value_diagnostic_logs()
    jsonl_path = Path(logs["paths"]["jsonl"])
    markdown_path = Path(logs["paths"]["markdown"])

    assert result["status"] == "passed"
    assert result["ok"] is True
    assert result["counts"]["errors"] == 0
    assert logs["count"] == 1
    assert json.loads(jsonl_path.read_text(encoding="utf-8").splitlines()[0])[
        "event"
    ] == "diagnostic_run"
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "Work Ledger Value Chain Test Log" in markdown
    assert session_id in markdown
    assert result["log_id"] in markdown


def test_value_chain_diagnostic_failure_is_persisted(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(tmp_path / "ledger"))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.work_ledger_value_diagnostics import (
        read_value_diagnostic_logs,
        run_value_chain_diagnostics,
    )

    with pytest.raises(Exception):
        run_value_chain_diagnostics("missing-session")

    logs = read_value_diagnostic_logs()
    assert logs["count"] == 1
    assert logs["entries"][0]["status"] == "error"
    assert logs["entries"][0]["session_id"] == "missing-session"
