from __future__ import annotations

import subprocess
from pathlib import Path


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def test_work_ledger_context_pack_output_and_chat_command(tmp_path, monkeypatch):
    ledger_home = tmp_path / "work_ledger"
    kernel_home = tmp_path / "kernel"
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("# Context pack\n", encoding="utf-8")
    (project / "handoff.py").write_text("# TODO: verify context handoff\n", encoding="utf-8")
    _git(project, "init")

    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(ledger_home))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(kernel_home))
    monkeypatch.setenv("JACHIN_WORK_LEDGER_LLM_ENABLED", "0")

    from l3_node.work_ledger import add_ai_work_trace, generate_work_outputs, read_output_text, start_session
    from l3_node.work_ledger_chat import handle_work_ledger_chat_command, parse_work_ledger_command

    detail = start_session(
        title="Context pack session",
        project_path=str(project),
        user_goal="Make Codex continuation handoff easy.",
        auto_collect=True,
    )
    sid = str(detail["session"]["session_id"])
    add_ai_work_trace(
        sid,
        "\n".join(
            [
                "Goal: improve the daily Work Ledger handoff.",
                "Changed: add a context pack output for Codex.",
                "Failed: previous handoff required users to piece together multiple files.",
                "Decision: one context pack should contain files, risks and next actions.",
                "Next: expose context pack in console and chat.",
            ]
        ),
        tool_name="Codex",
    )

    outputs = generate_work_outputs(sid)
    assert Path(outputs["context_pack"]).is_file()
    context = read_output_text(sid, "context_pack", max_chars=6000)["text"]
    assert "Task Context Pack" in context
    assert "可直接发给 Codex/Cursor 的下一轮任务书" in context
    assert "Changed: add a context pack output for Codex" in context
    assert "handoff.py" in context

    parsed = parse_work_ledger_command("生成上下文包")
    assert parsed and parsed["kind"] == "context_pack"
    reply = handle_work_ledger_chat_command("生成上下文包")
    assert reply and "Context Pack" in reply
    assert "Context pack session" in reply
    assert "Changed: add a context pack output for Codex" in reply


def test_work_ledger_import_process_filters_noisy_terminal_log(tmp_path, monkeypatch):
    ledger_home = tmp_path / "work_ledger"
    kernel_home = tmp_path / "kernel"
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("# Import process\n", encoding="utf-8")
    (project / "voice_gate.py").write_text("# FIXME: tune gate\n", encoding="utf-8")
    _git(project, "init")

    log_path = tmp_path / "terminal.log"
    log_path.write_text(
        "\n".join(
            [
                "random heartbeat line",
                "noise: user moved mouse",
                "Goal: improve always-on voice gate.",
                "python scripts/stress_voice_gate.py",
                "Changed: voice_gate.py now records owner threshold evidence.",
                "Failed: bystander speech interrupted WeChat open task.",
                "Decision: pending task should survive unrelated noise.",
                "Next: verify owner voiceprint threshold with live sample.",
                "another unimportant line",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(ledger_home))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(kernel_home))
    monkeypatch.setenv("JACHIN_WORK_LEDGER_LLM_ENABLED", "0")

    from l3_node.work_ledger import import_ai_work_process, read_output_text, start_session
    from l3_node.work_ledger_chat import handle_work_ledger_chat_command, parse_work_ledger_command

    detail = start_session(
        title="Noisy terminal import",
        project_path=str(project),
        user_goal="Make process import low friction.",
        auto_collect=True,
    )
    sid = str(detail["session"]["session_id"])

    result = import_ai_work_process(sid, file_path=str(log_path), tool_name="Terminal")
    assert result["import"]["raw_line_count"] == 9
    assert result["import"]["selected_line_count"] >= 5
    assert "context_pack" in result["outputs"]
    context = read_output_text(sid, "context_pack", max_chars=6000)["text"]
    assert "bystander speech interrupted WeChat open task" in context
    assert "voice_gate.py" in context

    parsed = parse_work_ledger_command(f"导入终端日志 {log_path}")
    assert parsed and parsed["kind"] == "process_import"
    reply = handle_work_ledger_chat_command(f"导入终端日志 {log_path}")
    assert reply and "已导入工作过程" in reply and "Context Pack" in reply
