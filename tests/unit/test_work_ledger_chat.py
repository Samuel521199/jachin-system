from __future__ import annotations

import subprocess
from pathlib import Path


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def test_work_ledger_chat_commands_lifecycle(tmp_path, monkeypatch):
    ledger_home = tmp_path / "work_ledger"
    kernel_home = tmp_path / "kernel"
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("# Chat ledger\n", encoding="utf-8")
    _git(project, "init")

    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(ledger_home))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(kernel_home))
    monkeypatch.setenv("JACHIN_WORK_LEDGER_LLM_ENABLED", "0")

    from l3_node.work_ledger import get_active_session, list_sessions
    from l3_node.work_ledger_chat import handle_work_ledger_chat_command, parse_work_ledger_command

    parsed = parse_work_ledger_command(f"开始任务：优化常开语音\n项目路径：{project}")
    assert parsed
    assert parsed["kind"] == "start"
    assert parsed["title"] == "优化常开语音"
    assert parsed["project_path"] == str(project)

    reply = handle_work_ledger_chat_command(f"开始任务：优化常开语音\n项目路径：{project}")
    assert reply and "已开始记录这项工作" in reply
    assert "优化常开语音" in reply
    active = get_active_session()
    assert active
    assert active["title"] == "优化常开语音"

    note_reply = handle_work_ledger_chat_command("记录一下：旁边人噪声会干扰常开语音，需要强化声纹门控。")
    assert note_reply and "已写入工作记录" in note_reply

    trace_reply = handle_work_ledger_chat_command(
        "导入Codex记录：Codex 已定位到 voice gate 逻辑，并建议把噪声判断写入 Evidence。"
    )
    assert trace_reply and "已导入 AI 工具过程记录" in trace_reply
    assert "Codex" in trace_reply

    (project / "voice_fix.py").write_text("# FIXME: tune owner threshold\nprint('gate')\n", encoding="utf-8")
    collect_reply = handle_work_ledger_chat_command("采集证据")
    assert collect_reply and "已采集当前工作现场" in collect_reply

    generate_reply = handle_work_ledger_chat_command("生成日报")
    assert generate_reply and "日报：" in generate_reply and "Codex 续写任务书：" in generate_reply
    from l3_node.work_ledger import get_session_detail

    detail = get_session_detail(str(active["session_id"]))
    sources = {row["source"] for row in detail["evidence"]}
    assert "ai_work_trace" in sources
    assert "file_content_snippets" in sources

    end_reply = handle_work_ledger_chat_command("结束今天任务")
    assert end_reply and "已结束任务" in end_reply
    assert get_active_session() is None

    continue_reply = handle_work_ledger_chat_command("继续昨天任务")
    assert continue_reply and "请继续本地项目任务" in continue_reply

    sessions = list_sessions(limit=5)
    assert sessions and sessions[0]["title"] == "优化常开语音"


def test_work_ledger_chat_ignores_regular_chat(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(tmp_path / "work_ledger"))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))
    monkeypatch.setenv("JACHIN_WORK_LEDGER_LLM_ENABLED", "0")

    from l3_node.work_ledger_chat import handle_work_ledger_chat_command, parse_work_ledger_command

    assert parse_work_ledger_command("你好，今天怎么样") is None
    assert handle_work_ledger_chat_command("你好，今天怎么样") is None
