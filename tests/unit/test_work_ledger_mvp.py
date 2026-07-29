from __future__ import annotations

import subprocess
from pathlib import Path


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def test_work_ledger_mvp_lifecycle(tmp_path, monkeypatch):
    ledger_home = tmp_path / "work_ledger"
    kernel_home = tmp_path / "kernel"
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("# Demo\n", encoding="utf-8")
    _git(project, "init")
    (project / "feature.py").write_text("print('hello')\n", encoding="utf-8")

    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(ledger_home))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(kernel_home))
    monkeypatch.setenv("JACHIN_WORK_LEDGER_LLM_ENABLED", "0")

    from l3_node.work_ledger import (
        add_manual_note,
        collect_snapshot,
        end_session,
        generate_work_outputs,
        get_active_session,
        load_evidence,
        start_session,
        status,
    )

    detail = start_session(
        title="验证工作账本 MVP",
        project_path=str(project),
        user_goal="验证开始、证据、输出和结束闭环",
        created_from="pytest",
    )
    session_id = detail["session"]["session_id"]
    assert get_active_session()["session_id"] == session_id

    add_manual_note(session_id, "记录一下：这是用户明确确认的关键过程。")
    (project / "feature.py").write_text(
        "# TODO: verify Work Ledger snippet collection\nprint('hello work ledger')\n",
        encoding="utf-8",
    )
    collect_snapshot(session_id, trigger="pytest_after_file_change")

    outputs = generate_work_outputs(session_id)
    review_path = Path(outputs["work_review"])
    work_report_path = Path(outputs["work_report_summary"])
    report_path = Path(outputs["daily_report"])
    prompt_path = Path(outputs["codex_continuation_prompt"])
    assert review_path.is_file()
    assert work_report_path.is_file()
    assert report_path.is_file()
    assert prompt_path.is_file()

    review = review_path.read_text(encoding="utf-8")
    work_report = work_report_path.read_text(encoding="utf-8")
    report = report_path.read_text(encoding="utf-8")
    prompt = prompt_path.read_text(encoding="utf-8")
    assert "## 一、今日完成与推进" in work_report
    assert "## 二、涉及模块" in work_report
    assert "## 三、风险与未完成" in work_report
    assert "## 四、下一步计划" in work_report
    assert work_report.count("1. ") >= 4
    assert "项目核心功能与工程实现" in work_report
    assert "feature.py" not in work_report
    assert "验证工作账本 MVP" in report
    assert "用户明确确认的关键过程" in report
    for heading in [
        "今天主要做了什么",
        "改了哪些模块",
        "哪些任务完成了",
        "哪些问题卡住了",
        "明天接着做什么",
        "这段内容怎么发日报",
        "这段内容怎么沉淀成方法论",
    ]:
        assert heading in review
    assert "feature.py" in review
    assert "TODO" in review
    assert "feature.py" in report
    assert "TODO" in report
    assert "请继续本地项目任务" in prompt
    assert "feature.py" in prompt

    closed = end_session(session_id)
    assert closed["session"]["status"] == "closed"
    assert status()["counts"]["active"] == 0

    sources = {row["source"] for row in load_evidence(session_id)}
    assert {
        "work_session",
        "git_snapshot",
        "file_scan",
        "file_content_snippets",
        "manual_note",
        "work_output",
    }.issubset(sources)
