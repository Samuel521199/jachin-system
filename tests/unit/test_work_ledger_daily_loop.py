from __future__ import annotations

import subprocess
from pathlib import Path


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def test_work_ledger_always_writes_baseline_lark_brief(tmp_path, monkeypatch):
    ledger_home = tmp_path / "work_ledger"
    kernel_home = tmp_path / "kernel"
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("# Work ledger daily loop\n", encoding="utf-8")
    _git(project, "init")

    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(ledger_home))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(kernel_home))
    monkeypatch.setenv("JACHIN_WORK_LEDGER_LLM_ENABLED", "0")

    from l3_node.work_ledger import add_manual_note, generate_work_outputs, read_output_text, start_session

    detail = start_session(
        title="2026-07-22 工作记录",
        project_path=str(project),
        user_goal="验证 Work Ledger 日常闭环",
        auto_collect=True,
    )
    sid = str(detail["session"]["session_id"])
    add_manual_note(sid, "今天完成了 Work Ledger 日常使用入口。")

    outputs = generate_work_outputs(sid)

    assert "daily_report" in outputs
    assert "codex_continuation_prompt" in outputs
    assert "lark_brief" in outputs
    payload = read_output_text(sid, "lark_brief", max_chars=1200)
    assert payload["output_key"] == "lark_brief"
    assert payload["text"].strip()
    assert "2026-07-22" in payload["text"]


def test_work_ledger_chat_daily_shortcuts(tmp_path, monkeypatch):
    ledger_home = tmp_path / "work_ledger"
    kernel_home = tmp_path / "kernel"
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("# Daily shortcut\n", encoding="utf-8")
    _git(project, "init")

    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(ledger_home))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(kernel_home))
    monkeypatch.setenv("JACHIN_WORK_LEDGER_LLM_ENABLED", "0")

    from l3_node.work_ledger import start_session
    from l3_node.work_ledger_chat import handle_work_ledger_chat_command, parse_work_ledger_command

    parsed = parse_work_ledger_command("开始今天工作")
    assert parsed
    assert parsed["kind"] == "start"
    assert "工作记录" in parsed["title"]

    detail = start_session(
        title="2026-07-22 工作记录",
        project_path=str(project),
        user_goal="验证聊天短版入口",
        auto_collect=True,
    )
    sid = str(detail["session"]["session_id"])

    reply = handle_work_ledger_chat_command("查看 Lark 短版")

    assert reply
    assert "Lark" in reply
    assert sid in reply or "文件：" in reply


def test_work_ledger_ai_trace_analysis_is_structured():
    from l3_node.work_ledger import analyze_ai_trace_text

    analysis = analyze_ai_trace_text(
        "\n".join(
            [
                "Goal: finish the Work Ledger daily loop.",
                "Changed: added clipboard import for Codex traces.",
                "Failed: Lark brief was missing when LLM was disabled.",
                "Decision: baseline lark_brief must always be generated.",
                "Next: run live smoke and check the evidence timeline.",
            ]
        )
    )

    buckets = analysis["buckets"]
    assert buckets["goals"]
    assert buckets["actions"]
    assert buckets["failures"]
    assert buckets["decisions"]
    assert buckets["next_steps"]
    assert analysis["signal_count"] >= 5
