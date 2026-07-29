from __future__ import annotations


def test_source_backoff_is_exponential_and_bounded():
    from l3_node.work_ledger_sources import _source_backoff_seconds

    assert [_source_backoff_seconds(index) for index in range(1, 6)] == [30, 60, 120, 240, 480]
    assert _source_backoff_seconds(20) == 3600


def test_source_health_distinguishes_changed_and_idle_syncs(tmp_path, monkeypatch):
    project = tmp_path / "project"
    exports = tmp_path / "exports"
    project.mkdir()
    exports.mkdir()
    history = exports / "codex-history.log"
    history.write_text("", encoding="utf-8")

    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(tmp_path / "ledger"))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.work_ledger import load_evidence, start_session
    from l3_node.work_ledger_sources import (
        configure_work_source_roots,
        get_process_inbox,
        get_work_source_status,
        refresh_process_inbox,
    )

    detail = start_session(
        title="Source health test",
        project_path=str(project),
        user_goal="Track sparse Codex changes without busy work.",
        auto_collect=False,
    )
    session_id = str(detail["session"]["session_id"])
    configure_work_source_roots(session_id, [str(exports)])
    evidence_before = len(load_evidence(session_id, 1000))

    refresh_process_inbox(session_id)
    with history.open("a", encoding="utf-8") as stream:
        stream.write("implemented source health metrics and tests\n")
    changed = refresh_process_inbox(session_id)
    for _ in range(8):
        refresh_process_inbox(session_id)

    status = get_work_source_status(session_id)
    health = status["health"]
    inbox = get_process_inbox(session_id)
    assert changed["last_refresh"]["new_line_count"] == 1
    assert health["sync_count"] == 10
    assert health["changed_sync_count"] == 1
    assert health["unchanged_sync_count"] == 9
    assert health["total_lines"] == 1
    assert health["failed_source_count"] == 0
    assert inbox["summary"]["accepted"] == 0
    assert len(load_evidence(session_id, 1000)) == evidence_before


def test_failed_source_enters_backoff_and_resume_clears_it(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    history = project / "codex-history.log"
    history.write_text("implemented a recoverable source sync change\n", encoding="utf-8")

    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(tmp_path / "ledger"))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.work_ledger import start_session
    from l3_node import work_ledger_sources as sources

    detail = start_session(
        title="Source backoff test",
        project_path=str(project),
        user_goal="Back off a broken source and recover safely.",
        auto_collect=False,
    )
    session_id = str(detail["session"]["session_id"])
    original_choose_adapter = sources._choose_adapter

    class BrokenAdapter:
        def read(self, path, *, start_offset=0, max_chars=12000):
            raise OSError("simulated locked source")

    monkeypatch.setattr(sources, "_choose_adapter", lambda path, preview: BrokenAdapter())
    failed = sources.refresh_process_inbox(session_id, roots=[str(project)])
    assert failed["last_refresh"]["sources_failed"] == 1
    status = sources.get_work_source_status(session_id)
    source = next(row for row in status["sources"] if row["source_uri"] == str(history.resolve()))
    assert source["consecutive_errors"] == 1
    assert source["backoff_seconds"] == 30
    assert status["backoff_count"] == 1

    backed_off = sources.refresh_process_inbox(session_id)
    assert backed_off["last_refresh"]["sources_backoff"] == 1
    assert backed_off["last_refresh"]["sources_failed"] == 0

    monkeypatch.setattr(sources, "_choose_adapter", original_choose_adapter)
    sources.control_work_source(session_id, "resume", source_key=source["source_key"])
    recovered = sources.refresh_process_inbox(session_id)
    assert recovered["last_refresh"]["new_line_count"] == 1
    recovered_status = sources.get_work_source_status(session_id)
    recovered_source = next(
        row for row in recovered_status["sources"] if row["source_uri"] == str(history.resolve())
    )
    assert recovered_source["consecutive_errors"] == 0
    assert recovered_status["backoff_count"] == 0
