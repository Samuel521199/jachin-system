from __future__ import annotations


def test_process_inbox_merges_sources_and_persists_review(tmp_path, monkeypatch):
    ledger_home = tmp_path / "ledger"
    project = tmp_path / "project"
    project.mkdir()
    artifact = project / "work_ledger_sources.py"
    artifact.write_text("# WorkSourceAdapter\n", encoding="utf-8")

    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(ledger_home))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))
    monkeypatch.setenv("JACHIN_WORK_LEDGER_LLM_ENABLED", "0")

    from l3_node.work_ledger import build_work_timeline, collect_work_checkpoint, load_evidence, start_session
    from l3_node.work_ledger_sources import refresh_process_inbox, review_process_inbox_event

    detail = start_session(
        title="Build AI process inbox",
        project_path=str(project),
        user_goal="Implement WorkSourceAdapter and process inbox deduplication.",
        auto_collect=False,
    )
    sid = str(detail["session"]["session_id"])
    artifact.write_text("# WorkSourceAdapter\n# process inbox implemented\n", encoding="utf-8")
    collect_work_checkpoint(sid, trigger="test", force=True)

    sources = [
        {
            "source_type": "codex",
            "source_uri": "inline://codex/session-1",
            "text": "Implemented WorkSourceAdapter process inbox deduplication in work_ledger_sources.py and verified the result.",
        },
        {
            "source_type": "terminal",
            "source_uri": "inline://terminal/run-1",
            "text": "pytest verified WorkSourceAdapter process inbox deduplication in work_ledger_sources.py: 12 passed.",
        },
        {
            "source_type": "document",
            "source_uri": "inline://document/note-1",
            "text": "WorkSourceAdapter process inbox deduplication was completed in work_ledger_sources.py.",
        },
    ]
    inbox = refresh_process_inbox(sid, inline_sources=sources)
    merged = next(event for event in inbox["events"] if {"codex", "terminal", "document", "file_checkpoint"}.issubset(set(event["source_types"])))
    assert merged["source_count"] >= 4
    assert merged["status"] == "pending"
    assert "raw conversation" not in str(inbox).lower()

    reviewed = review_process_inbox_event(sid, merged["event_id"], "accepted", generate_outputs_after=False)
    assert reviewed["event"]["status"] == "accepted"
    assert reviewed["event"]["imported_evidence_id"]
    assert reviewed["event"]["project_fact_id"]
    assert reviewed["fact_result"]["ok"] is True

    refreshed = refresh_process_inbox(sid, inline_sources=sources)
    persisted = next(event for event in refreshed["events"] if event["event_id"] == merged["event_id"])
    assert persisted["status"] == "accepted"

    evidence = load_evidence(sid, 1000)
    assert any(row["source"] == "work_process_inbox_review" for row in evidence)
    timeline = build_work_timeline(sid)
    assert any(row["category"] == "candidate_feedback" for row in timeline["entries"])


def test_process_inbox_redacts_sensitive_material(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(tmp_path / "ledger"))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.work_ledger import start_session
    from l3_node.work_ledger_sources import refresh_process_inbox

    detail = start_session(title="Privacy test", project_path=str(tmp_path), auto_collect=False)
    sid = str(detail["session"]["session_id"])
    secret = "sk-test-secret-1234567890"
    inbox = refresh_process_inbox(
        sid,
        inline_sources=[
            {
                "source_type": "codex",
                "source_uri": "inline://codex/private",
                "text": f"Implemented the adapter. API key: {secret}",
            }
        ],
    )
    serialized = str(inbox)
    assert secret not in serialized
    assert inbox["events"]
