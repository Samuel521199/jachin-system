from __future__ import annotations


def test_file_source_cursor_reads_only_appended_content(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    terminal_log = project / "terminal.log"
    terminal_log.write_text("\n".join(f"implemented adapter item {index:03d}" for index in range(100)) + "\n", encoding="utf-8")

    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(tmp_path / "ledger"))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.work_ledger import start_session
    from l3_node.work_ledger_sources import (
        control_work_source,
        get_work_source_status,
        refresh_process_inbox,
    )

    detail = start_session(
        title="Incremental source test",
        project_path=str(project),
        user_goal="Read only new terminal work events.",
        auto_collect=False,
    )
    sid = str(detail["session"]["session_id"])

    first = refresh_process_inbox(sid, roots=[str(project)])
    assert first["last_refresh"]["new_line_count"] == 100
    status = get_work_source_status(sid)
    source = next(row for row in status["sources"] if row["source_uri"] == str(terminal_log.resolve()))
    assert source["total_line_count"] == 100

    with terminal_log.open("a", encoding="utf-8") as stream:
        stream.write("\n".join(f"implemented adapter item {index:03d}" for index in range(100, 105)) + "\n")
    second = refresh_process_inbox(sid)
    assert second["last_refresh"]["new_line_count"] == 5
    assert second["last_refresh"]["sources_read"] == 1

    unchanged = refresh_process_inbox(sid)
    assert unchanged["last_refresh"]["new_line_count"] == 0
    assert unchanged["last_refresh"]["sources_skipped_unchanged"] >= 1

    control_work_source(sid, "pause", source_key=source["source_key"])
    with terminal_log.open("a", encoding="utf-8") as stream:
        stream.write("implemented paused item 105\nimplemented paused item 106\n")
    paused = refresh_process_inbox(sid)
    assert paused["last_refresh"]["new_line_count"] == 0
    assert paused["last_refresh"]["sources_paused"] >= 1

    control_work_source(sid, "resume", source_key=source["source_key"])
    resumed = refresh_process_inbox(sid)
    assert resumed["last_refresh"]["new_line_count"] == 2


def test_terminal_noise_is_ignored_and_root_configuration_persists(tmp_path, monkeypatch):
    project = tmp_path / "project"
    exports = tmp_path / "codex_exports"
    project.mkdir()
    exports.mkdir()
    noise_log = exports / "terminal-heartbeat.log"
    noise_log.write_text("heartbeat waiting\nprogress 10%\nheartbeat waiting\nprogress 20%\n", encoding="utf-8")

    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(tmp_path / "ledger"))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.work_ledger import start_session
    from l3_node.work_ledger_sources import configure_work_source_roots, get_work_source_status, refresh_process_inbox

    detail = start_session(title="Source configuration", project_path=str(project), auto_collect=False)
    sid = str(detail["session"]["session_id"])
    configured = configure_work_source_roots(sid, [str(exports)])
    assert configured["configured_roots"] == [str(exports.resolve())]

    inbox = refresh_process_inbox(sid)
    noise_event = next(row for row in inbox["events"] if "terminal" in row["source_types"])
    assert noise_event["status"] == "ignored"
    assert noise_event["content_class"]["outcome"] == "noise"
    status = get_work_source_status(sid)
    assert status["configured_roots"] == [str(exports.resolve())]
