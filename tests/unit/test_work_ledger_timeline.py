from __future__ import annotations


def test_non_git_checkpoint_deduplication_and_unified_timeline(tmp_path, monkeypatch):
    ledger_home = tmp_path / "work_ledger"
    project = tmp_path / "notes_project"
    project.mkdir()
    note_file = project / "meeting_notes.txt"
    note_file.write_text("first note\n", encoding="utf-8")

    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(ledger_home))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))
    monkeypatch.setenv("JACHIN_WORK_LEDGER_LLM_ENABLED", "0")

    from l3_node.work_ledger import (
        add_ai_work_trace,
        add_manual_note,
        build_work_timeline,
        collect_work_checkpoint,
        load_evidence,
        start_session,
    )

    detail = start_session(
        title="Non-git notes task",
        project_path=str(project),
        user_goal="Record useful work without a Git repository.",
        auto_collect=False,
    )
    sid = str(detail["session"]["session_id"])

    first = collect_work_checkpoint(sid, trigger="test", force=False)
    assert first["ok"] is True
    assert first["deduplicated"] is False
    assert first["project_kind"] == "filesystem"

    duplicate = collect_work_checkpoint(sid, trigger="test", force=False)
    assert duplicate["deduplicated"] is True

    note_file.write_text("first note\nsecond note with more detail\n", encoding="utf-8")
    changed = collect_work_checkpoint(sid, trigger="test", force=False)
    assert changed["deduplicated"] is False
    assert changed["project_kind"] == "filesystem"

    add_manual_note(sid, "用户确认：会议结论已经整理完成。")
    add_ai_work_trace(
        sid,
        "Codex summarized the meeting notes and proposed the next action.",
        tool_name="Codex",
    )
    checkpoint_rows = [row for row in load_evidence(sid) if row["source"] == "work_checkpoint"]
    assert len(checkpoint_rows) == 2

    timeline = build_work_timeline(sid)
    categories = {row["category"] for row in timeline["entries"]}
    assert {"task", "checkpoint", "user_note", "ai_process"}.issubset(categories)
    ai_entry = next(row for row in timeline["entries"] if row["category"] == "ai_process")
    assert ai_entry["actor"] == "ai_tool"
    note_entry = next(row for row in timeline["entries"] if row["category"] == "user_note")
    assert note_entry["actor"] == "user"
