from __future__ import annotations


def test_same_project_inherits_authorization_and_cursor_without_replaying_history(tmp_path, monkeypatch):
    project = tmp_path / "project-a"
    exports = tmp_path / "exports-a"
    project.mkdir()
    exports.mkdir()
    history = exports / "codex-history.log"
    history.write_text("implemented day one source profile\n", encoding="utf-8")

    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(tmp_path / "ledger"))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.work_ledger import end_session, start_session
    from l3_node.work_ledger_sources import (
        configure_work_source_roots,
        get_work_source_status,
        refresh_process_inbox,
    )

    day_one = start_session(title="Project A day one", project_path=str(project), auto_collect=False)
    day_one_id = str(day_one["session"]["session_id"])
    configure_work_source_roots(day_one_id, [str(exports)])
    first = refresh_process_inbox(day_one_id)
    assert first["last_refresh"]["new_line_count"] == 1
    end_session(day_one_id, generate_outputs=False)

    with history.open("a", encoding="utf-8") as stream:
        stream.write("implemented day two source profile continuation\n")

    day_two = start_session(title="Project A day two", project_path=str(project), auto_collect=False)
    day_two_id = str(day_two["session"]["session_id"])
    inherited = get_work_source_status(day_two_id)
    assert inherited["configured_roots"] == [str(exports.resolve())]
    assert inherited["source_profile"]["inherited"] is True
    assert inherited["source_profile"]["inherited_from_session_id"] == day_one_id
    second = refresh_process_inbox(day_two_id)
    assert second["last_refresh"]["new_line_count"] == 1
    assert second["last_refresh"]["sources_read"] == 1


def test_project_source_profiles_are_isolated_and_missing_paths_are_visible(tmp_path, monkeypatch):
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    exports_a = tmp_path / "exports-a"
    project_a.mkdir()
    project_b.mkdir()
    exports_a.mkdir()
    history = exports_a / "cursor-history.log"
    history.write_text("implemented project a cursor work\n", encoding="utf-8")

    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(tmp_path / "ledger"))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.work_ledger import end_session, start_session
    from l3_node.work_ledger_sources import (
        configure_work_source_roots,
        get_work_source_status,
        refresh_process_inbox,
    )

    first = start_session(title="Project A", project_path=str(project_a), auto_collect=False)
    first_id = str(first["session"]["session_id"])
    configure_work_source_roots(first_id, [str(exports_a)])
    refresh_process_inbox(first_id)
    end_session(first_id, generate_outputs=False)

    second = start_session(title="Project B", project_path=str(project_b), auto_collect=False)
    second_id = str(second["session"]["session_id"])
    isolated = get_work_source_status(second_id)
    assert isolated["configured_roots"] == []
    assert isolated["authorizations"] == []
    assert all(str(exports_a.resolve()) not in row["source_uri"] for row in isolated["sources"])
    end_session(second_id, generate_outputs=False)

    missing_path = tmp_path / "exports-a-moved"
    exports_a.rename(missing_path)
    third = start_session(title="Project A resumed", project_path=str(project_a), auto_collect=False)
    third_id = str(third["session"]["session_id"])
    unavailable = get_work_source_status(third_id)
    assert unavailable["configured_roots"] == [str(exports_a.resolve())]
    assert unavailable["authorizations"][0]["exists"] is False
    assert unavailable["authorizations"][0]["readable"] is False
    refresh = refresh_process_inbox(third_id)
    assert refresh["last_refresh"]["sources_read"] == 0


def test_revoked_project_source_is_not_inherited_again(tmp_path, monkeypatch):
    project = tmp_path / "project"
    exports = tmp_path / "exports"
    project.mkdir()
    exports.mkdir()
    (exports / "terminal.log").write_text("tests passed\n", encoding="utf-8")

    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(tmp_path / "ledger"))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.work_ledger import end_session, start_session
    from l3_node.work_ledger_sources import (
        configure_work_source_roots,
        get_work_source_status,
        revoke_project_source_authorization,
    )

    first = start_session(title="Authorize", project_path=str(project), auto_collect=False)
    first_id = str(first["session"]["session_id"])
    configure_work_source_roots(first_id, [str(exports)])
    revoked = revoke_project_source_authorization(first_id, root=str(exports))
    assert revoked["configured_roots"] == []
    assert revoked["authorizations"] == []
    end_session(first_id, generate_outputs=False)

    second = start_session(title="After revoke", project_path=str(project), auto_collect=False)
    second_id = str(second["session"]["session_id"])
    status = get_work_source_status(second_id)
    assert status["configured_roots"] == []
    assert status["authorizations"] == []
