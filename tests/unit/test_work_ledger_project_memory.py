from __future__ import annotations

import subprocess
from pathlib import Path


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def test_work_ledger_remembers_project_alias_and_reuses_path(tmp_path, monkeypatch):
    ledger_home = tmp_path / "work_ledger"
    kernel_home = tmp_path / "kernel"
    project = tmp_path / "jachin-system-main"
    project.mkdir()
    (project / "README.md").write_text("# Jachin\n", encoding="utf-8")
    _git(project, "init")

    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(ledger_home))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(kernel_home))
    monkeypatch.setenv("JACHIN_WORK_LEDGER_LLM_ENABLED", "0")

    from l3_node.work_ledger import end_session, get_active_session, start_session, status
    from l3_node.work_ledger_chat import parse_work_ledger_command
    from l3_node.work_ledger_project_memory import resolve_project_reference

    first = start_session(
        title="Jachin 工作记录",
        project_path=str(project),
        user_goal="开始记录今天 Jachin 的开发工作",
        auto_collect=False,
    )
    sid = str(first["session"]["session_id"])
    end_session(sid, generate_outputs=False)

    resolved = resolve_project_reference("继续 Jachin 这个项目")
    assert resolved
    assert resolved["project_path"] == str(project)
    assert resolved["session_id"] == sid

    parsed = parse_work_ledger_command("开始记录今天 Jachin 的复盘工作")
    assert parsed
    assert parsed["kind"] == "start"
    assert parsed["project_path"] == str(project)
    assert parsed["project_memory"]["reason"].startswith("matched_project_alias")

    overview = status()
    assert overview["project_memory"]["project_count"] >= 1
    assert overview["project_memory"]["recent"]["session_id"] == sid
    assert get_active_session() is None


def test_work_ledger_continue_uses_project_memory_session(tmp_path, monkeypatch):
    ledger_home = tmp_path / "work_ledger"
    kernel_home = tmp_path / "kernel"
    project = tmp_path / "jachin-system-main"
    project.mkdir()
    (project / "README.md").write_text("# Jachin continue\n", encoding="utf-8")
    _git(project, "init")

    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(ledger_home))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(kernel_home))
    monkeypatch.setenv("JACHIN_WORK_LEDGER_LLM_ENABLED", "0")

    from l3_node.work_ledger import add_manual_note, end_session, start_session
    from l3_node.work_ledger_chat import handle_work_ledger_chat_command, parse_work_ledger_command

    first = start_session(
        title="Jachin 语音任务",
        project_path=str(project),
        user_goal="优化 Jachin 语音常开入口",
        auto_collect=False,
    )
    sid = str(first["session"]["session_id"])
    add_manual_note(sid, "用户确认：Jachin 语音任务需要明天继续。")
    end_session(sid, generate_outputs=False)

    parsed = parse_work_ledger_command("明天让 Codex 接着 Jachin 做")
    assert parsed
    assert parsed["kind"] == "continue"
    assert parsed["project_memory"]["session_id"] == sid

    reply = handle_work_ledger_chat_command("明天让 Codex 接着 Jachin 做")
    assert reply
    assert sid in reply
    assert "codex_continuation_prompt.md" in reply
