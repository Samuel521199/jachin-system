from __future__ import annotations


def _event(
    event_id: str,
    summary: str,
    *,
    source_type: str = "codex",
    excerpt: str = "",
) -> dict:
    return {
        "event_id": event_id,
        "summary": summary,
        "excerpt": excerpt or summary,
        "source_types": [source_type],
        "source_chain": [
            {
                "source_type": source_type,
                "source_uri": f"inline://{source_type}/{event_id}",
            }
        ],
        "dedupe_tokens": [],
    }


def test_project_fact_is_stable_across_sessions_and_sources(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(tmp_path / "ledger"))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))
    monkeypatch.setenv("JACHIN_WORK_LEDGER_LLM_ENABLED", "0")

    from l3_node.work_ledger import end_session, generate_work_outputs, start_session
    from l3_node.work_ledger_facts import (
        get_project_fact_index,
        get_session_fact_context,
        record_confirmed_work_event,
    )

    first = start_session(
        title="Day one",
        project_path=str(project),
        auto_collect=False,
    )
    first_id = str(first["session"]["session_id"])
    first_result = record_confirmed_work_event(
        first_id,
        _event(
            "codex-1",
            "Implemented cross-day source cursor persistence in work_ledger_sources.py",
            source_type="codex",
        ),
        verification_evidence_id="ev-codex-1",
    )
    assert first_result["ok"] is True
    assert first_result["match_type"] == "new"
    end_session(first_id, generate_outputs=False)

    second = start_session(
        title="Day two",
        project_path=str(project),
        auto_collect=False,
    )
    second_id = str(second["session"]["session_id"])
    second_result = record_confirmed_work_event(
        second_id,
        _event(
            "terminal-1",
            "Cross-day source cursor persistence implemented in work_ledger_sources.py",
            source_type="terminal",
        ),
        verification_evidence_id="ev-terminal-1",
    )
    assert second_result["ok"] is True
    assert second_result["match_type"] in {"exact_identity", "strong_identity"}
    assert second_result["fact"]["fact_id"] == first_result["fact"]["fact_id"]

    index = get_project_fact_index(str(project))
    assert index["summary"]["fact_count"] == 1
    assert index["facts"][0]["occurrence_count"] == 2
    assert set(index["facts"][0]["source_types"]) == {"codex", "terminal"}
    context = get_session_fact_context(second_id)
    assert context["new_facts"] == []
    assert len(context["continued_facts"]) == 1

    outputs = generate_work_outputs(second_id)
    report = open(outputs["daily_report"], encoding="utf-8").read()
    assert "本次暂无新确认的项目事实" in report
    assert "[持续事实]" in report
    assert "累计出现 2 次" in report


def test_similar_facts_require_review_and_can_be_merged(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(tmp_path / "ledger"))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.work_ledger import start_session
    from l3_node.work_ledger_facts import (
        get_project_fact_index,
        record_confirmed_work_event,
        review_fact_match,
    )

    session = start_session(
        title="Fact review",
        project_path=str(project),
        auto_collect=False,
    )
    session_id = str(session["session"]["session_id"])
    first = record_confirmed_work_event(
        session_id,
        _event(
            "fact-a",
            "Implemented project source profile persistence and cursor recovery",
            excerpt="Project source profile persistence stores cursor recovery data.",
        ),
    )
    second = record_confirmed_work_event(
        session_id,
        _event(
            "fact-b",
            "Implemented project fact persistence and review recovery",
            excerpt="Project fact persistence stores review recovery data.",
            source_type="document",
        ),
    )
    assert first["fact"]["fact_id"] != second["fact"]["fact_id"]
    assert second["match_type"] == "new_with_review_candidate"

    index = get_project_fact_index(str(project))
    assert index["summary"]["fact_count"] == 2
    assert index["summary"]["review_pending"] == 1
    candidate_id = index["review_queue"][0]["candidate_id"]

    resolved = review_fact_match(str(project), candidate_id, "merge")
    assert resolved["candidate"]["resolution"] == "merge"
    assert resolved["index"]["summary"]["fact_count"] == 1
    assert resolved["index"]["summary"]["review_pending"] == 0
    assert resolved["index"]["facts"][0]["occurrence_count"] == 2


def test_fact_lifecycle_tracks_progress_completion_reopen_and_decision_chain(
    tmp_path,
    monkeypatch,
):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(tmp_path / "ledger"))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))
    monkeypatch.setenv("JACHIN_WORK_LEDGER_LLM_ENABLED", "0")

    from l3_node.work_ledger import end_session, start_session
    from l3_node.work_ledger_facts import (
        get_project_fact_index,
        get_session_fact_context,
        record_confirmed_work_event,
        update_project_fact,
    )

    day_one = start_session(
        title="Open issue",
        project_path=str(project),
        auto_collect=False,
    )
    day_one_id = str(day_one["session"]["session_id"])
    opened = record_confirmed_work_event(
        day_one_id,
        {
            **_event(
                "issue-open",
                "Source cursor recovery is blocked by a rotated history file",
            ),
            "target_state": "open",
            "failure_reason": "History file rotation invalidated the old cursor.",
            "next_action": "Rebase the cursor using the stable source identity.",
        },
    )
    fact_id = opened["fact"]["fact_id"]
    assert opened["fact"]["state"] == "open"
    end_session(day_one_id, generate_outputs=False)

    day_two = start_session(
        title="Resolve issue",
        project_path=str(project),
        auto_collect=False,
    )
    day_two_id = str(day_two["session"]["session_id"])
    in_progress = update_project_fact(
        day_two_id,
        fact_id,
        target_state="in_progress",
        reason="Cursor recovery implementation is in progress.",
        decision="Use stable source identity instead of file name.",
    )
    assert in_progress["fact"]["state"] == "in_progress"
    completed = update_project_fact(
        day_two_id,
        fact_id,
        target_state="completed",
        reason="Cursor recovery tests passed.",
    )
    assert completed["fact"]["state"] == "completed"
    end_session(day_two_id, generate_outputs=False)

    day_three = start_session(
        title="Regression",
        project_path=str(project),
        auto_collect=False,
    )
    day_three_id = str(day_three["session"]["session_id"])
    reopened = record_confirmed_work_event(
        day_three_id,
        {
            **_event(
                "issue-regression",
                "Source cursor recovery regression failed after another file rotation",
                source_type="terminal",
            ),
            "project_fact_id": fact_id,
            "failure_reason": "A second rotation reopened the cursor recovery issue.",
            "next_action": "Add a second-rotation regression test.",
        },
    )
    assert reopened["fact"]["state"] == "reopened"
    assert reopened["state_changed"] is True
    context = get_session_fact_context(day_three_id)
    assert [fact["fact_id"] for fact in context["reopened_this_session"]] == [fact_id]

    final = update_project_fact(
        day_three_id,
        fact_id,
        target_state="completed",
        reason="Second-rotation regression test passed.",
    )
    assert final["fact"]["state"] == "completed"

    index = get_project_fact_index(str(project))
    fact = next(row for row in index["facts"] if row["fact_id"] == fact_id)
    states = [row["to_state"] for row in fact["lifecycle"]]
    assert states == ["open", "in_progress", "completed", "reopened", "completed"]
    assert len(fact["failure_attempts"]) == 2
    assert fact["decisions"][-1]["text"] == "Use stable source identity instead of file name."
    assert fact["next_actions"][-1]["text"] == "Add a second-rotation regression test."


def test_separate_review_persists_anti_merge_rule_and_supersede_transition(
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
        get_session_fact_context,
        record_confirmed_work_event,
        review_fact_match,
    )

    session = start_session(
        title="Fact boundaries",
        project_path=str(project),
        auto_collect=False,
    )
    session_id = str(session["session"]["session_id"])
    first = record_confirmed_work_event(
        session_id,
        _event(
            "boundary-a",
            "Implemented release package source registry synchronization for private catalogs",
        ),
    )
    second = record_confirmed_work_event(
        session_id,
        _event(
            "boundary-b",
            "Implemented release package model registry validation for public catalogs",
            source_type="document",
        ),
    )
    index = get_project_fact_index(str(project))
    candidate_id = index["review_queue"][0]["candidate_id"]
    separated = review_fact_match(str(project), candidate_id, "separate")
    assert separated["index"]["summary"]["review_pending"] == 0
    assert separated["index"]["separation_rules"][0]["status"] == "active"

    replacement = record_confirmed_work_event(
        session_id,
        {
            **_event(
                "replacement",
                "Added catalog contract validator with dependency graph enforcement",
                source_type="codex",
            ),
            "supersedes_fact_id": first["fact"]["fact_id"],
        },
    )
    after = get_project_fact_index(str(project))
    old = next(row for row in after["facts"] if row["fact_id"] == first["fact"]["fact_id"])
    assert old["state"] == "superseded"
    assert old["superseded_by_fact_id"] == replacement["fact"]["fact_id"]
    context = get_session_fact_context(session_id)
    assert any(
        row["fact_id"] == old["fact_id"]
        for row in context["superseded_this_session"]
    )
    assert replacement["fact"]["supersedes_fact_ids"] == [old["fact_id"]]
    assert [row["fact_id"] for row in context["predecessor_facts"]] == [
        old["fact_id"]
    ]
    assert first["fact"]["fact_id"] != second["fact"]["fact_id"]
