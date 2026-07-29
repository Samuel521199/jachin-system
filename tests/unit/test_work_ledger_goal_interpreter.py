from __future__ import annotations

import subprocess
from pathlib import Path


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def test_work_ledger_goal_interpreter_classifies_natural_daily_work_language(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(tmp_path / "work_ledger"))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))
    monkeypatch.setenv("JACHIN_WORK_LEDGER_LLM_ENABLED", "0")

    from l3_node.work_ledger_chat import parse_work_ledger_command

    cases = [
        ("开始记录今天 Jachin 的开发工作", "start"),
        ("今天下班了，给我生成一份日报", "end"),
        ("帮我整理这周干了什么", "weekly"),
        ("把最近 Jachin 的工作写成周报", "weekly"),
        ("明天让 Codex 接着这个项目做", "continue"),
        ("看看之前语音常开做到哪了", "recall"),
        ("帮我生成今天工作的 Lark 简报", "lark_brief"),
    ]
    for text, expected in cases:
        parsed = parse_work_ledger_command(text)
        assert parsed is not None, text
        assert parsed["kind"] == expected
        assert parsed.get("confidence", 1.0) >= 0.62
        assert parsed.get("reason")

    assert parse_work_ledger_command("你好，今天怎么样") is None


def test_work_ledger_goal_interpreter_executes_start_and_work_review(tmp_path, monkeypatch):
    ledger_home = tmp_path / "work_ledger"
    kernel_home = tmp_path / "kernel"
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("# Natural work ledger\n", encoding="utf-8")
    _git(project, "init")

    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(ledger_home))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(kernel_home))
    monkeypatch.setenv("JACHIN_WORK_LEDGER_LLM_ENABLED", "0")

    from l3_node.work_ledger import get_active_session, get_session_detail
    from l3_node.work_ledger_chat import handle_work_ledger_chat_command

    reply = handle_work_ledger_chat_command(f"开始记录今天 Jachin 的开发工作，项目路径：{project}")
    assert reply
    active = get_active_session()
    assert active
    assert active["project_path"] == str(project)

    (project / "feature.py").write_text("# TODO: natural interpreter smoke\n", encoding="utf-8")
    handle_work_ledger_chat_command("采集证据")
    end_reply = handle_work_ledger_chat_command("今天下班了，给我生成一份日报")
    assert end_reply and "工作复盘七问" in end_reply

    detail = get_session_detail(str(active["session_id"]))
    outputs = detail["session"]["output_paths"]
    assert "work_review" in outputs
    review = Path(outputs["work_review"])
    assert review.is_file()
    text = review.read_text(encoding="utf-8")
    assert "今天主要做了什么" in text
    assert "明天接着做什么" in text
