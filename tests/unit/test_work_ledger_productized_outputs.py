from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def test_work_ledger_generates_productized_outputs_and_adoption(tmp_path, monkeypatch):
    ledger_home = tmp_path / "work_ledger"
    kernel_home = tmp_path / "kernel"
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("# Work Ledger\n", encoding="utf-8")
    (project / "feature.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")
    _git(project, "init")

    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(ledger_home))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(kernel_home))
    monkeypatch.setenv("JACHIN_WORK_LEDGER_LLM_ENABLED", "0")

    from l3_node.work_ledger import (
        add_ai_work_trace,
        add_manual_note,
        adopt_work_output,
        generate_instant_work_brief,
        generate_work_outputs,
        load_evidence,
        read_output_text,
        start_session,
    )

    detail = start_session(
        title="Work Ledger productized output",
        project_path=str(project),
        user_goal="Generate reusable daily and weekly work assets.",
        auto_collect=True,
    )
    sid = str(detail["session"]["session_id"])
    add_manual_note(sid, "User confirmed: daily report output should be reusable by the team.")
    add_ai_work_trace(
        sid,
        "\n".join(
            [
                "Goal: make Work Ledger useful every day.",
                "Changed: add productized report templates.",
                "Decision: adopted outputs should flow back to memory growth.",
                "Next: test with a live daily loop.",
            ]
        ),
        tool_name="Codex",
    )

    outputs = generate_work_outputs(sid)
    for key in [
        "work_review",
        "work_report_summary",
        "context_pack",
        "team_lark_brief",
        "weekly_report",
        "performance_entries",
        "methodology_candidates",
    ]:
        assert key in outputs
        assert Path(outputs[key]).is_file()
        payload = read_output_text(sid, key, max_chars=2000)
        assert payload["text"].strip()
    work_report = read_output_text(sid, "work_report_summary", max_chars=5000)["text"]
    assert "## 一、今日完成与推进" in work_report
    assert "## 二、涉及模块" in work_report
    assert "## 三、风险与未完成" in work_report
    assert "## 四、下一步计划" in work_report
    assert work_report.count("1. ") >= 4
    assert "项目核心功能与工程实现" in work_report
    assert "feature.py" not in work_report
    context = read_output_text(sid, "context_pack", max_chars=5000)["text"]
    assert "Task Context Pack" in context
    assert "可直接发给 Codex/Cursor 的下一轮任务书" in context
    assert "Changed: add productized report templates" in context

    brief = generate_instant_work_brief(1)
    assert brief["days"] == 1
    assert brief["window_mode"] == "calendar_days"
    assert brief["session_count"] == 1
    assert Path(brief["path"]).is_file()
    assert Path(brief["source_index_path"]).is_file()
    assert "今天 00:00 至当前" in brief["text"]
    assert "## 一、完成与推进" in brief["text"]
    assert "## 二、涉及项目与模块" in brief["text"]
    assert "## 三、风险与未完成" in brief["text"]
    assert "## 四、下一步计划" in brief["text"]
    assert brief["text"].count("1. ") >= 4
    assert "项目核心功能与工程实现" in brief["text"]
    assert "feature.py" not in brief["text"]

    adoption = adopt_work_output(sid, "methodology_candidates", note="accepted in unit test")
    assert adoption["source"] == "work_output_adoption"
    assert adoption["trust_level"] == "user_confirmed"
    evidence = load_evidence(sid, limit=100)
    assert any(ev.get("source") == "work_output_adoption" for ev in evidence)


def test_work_ledger_calendar_day_window_starts_at_local_midnight(monkeypatch):
    from l3_node import work_ledger

    fixed_now = datetime(2026, 7, 23, 15, 30, tzinfo=timezone.utc)
    before_midnight = int(
        datetime(2026, 7, 22, 23, 59, tzinfo=timezone.utc).timestamp() * 1000
    )
    after_midnight = int(
        datetime(2026, 7, 23, 0, 1, tzinfo=timezone.utc).timestamp() * 1000
    )
    monkeypatch.setattr(work_ledger, "_now_datetime", lambda: fixed_now)
    monkeypatch.setattr(
        work_ledger,
        "_load_index",
        lambda: [
            {"session_id": "today", "updated_at_ms": after_midnight},
            {"session_id": "yesterday", "updated_at_ms": before_midnight},
        ],
    )
    monkeypatch.setattr(
        work_ledger,
        "load_evidence",
        lambda session_id, limit=1000: [
            {
                "source": "manual_note",
                "summary": "昨天的记录",
                "collected_at_ms": before_midnight,
            },
            {
                "source": "manual_note",
                "summary": "今天的记录",
                "collected_at_ms": after_midnight,
            },
        ]
        if session_id == "today"
        else [],
    )

    rows = work_ledger.list_recent_sessions(1, calendar_window=True)
    index = work_ledger.build_work_ledger_recall_index(
        1,
        calendar_window=True,
    )

    assert [row["session_id"] for row in rows] == ["today"]
    assert [item["summary"] for item in index["recent_notes"]] == ["今天的记录"]


def test_instant_brief_does_not_treat_session_or_git_status_as_accomplishment():
    from l3_node.work_ledger import build_instant_work_brief

    text = build_instant_work_brief(
        {
            "window_days": 1,
            "generated_at": "2026-07-23T15:31:40+08:00",
            "sessions": [
                {
                    "session_id": "work_demo",
                    "title": "2026-07-23 工作记录",
                    "status": "active",
                    "project_name": "jachin-system-main",
                }
            ],
            "recent_changed_files": [
                {
                    "project_name": "jachin-system-main",
                    "path": "l3_node/work_ledger.py",
                    "status": "M",
                }
            ],
            "project_counts": {"jachin-system-main": 1},
            "recent_notes": [],
            "recent_ai_signals": [],
            "valued_outcomes": [],
            "verified_outcomes": [],
        }
    )

    assert "证据不足" in text
    assert "能力建设：AI 工作账本、工作记忆与复盘" in text
    assert "l3_node/work_ledger.py" not in text
    assert "正在推进：2026-07-23 工作记录" not in text
    assert "仍在进行：2026-07-23 工作记录" not in text
    assert "（M）" not in text


def test_multi_day_brief_backfills_git_commits_when_only_one_session_exists(
    tmp_path, monkeypatch
):
    ledger_home = tmp_path / "work_ledger"
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init")
    _git(project, "config", "user.email", "work-ledger@example.com")
    _git(project, "config", "user.name", "Work Ledger")
    (project / "feature.py").write_text("print('first')\n", encoding="utf-8")
    _git(project, "add", "feature.py")
    authored_at = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    monkeypatch.setenv("GIT_AUTHOR_DATE", authored_at)
    monkeypatch.setenv("GIT_COMMITTER_DATE", authored_at)
    _git(project, "commit", "-m", "Implement historical work evidence")
    monkeypatch.delenv("GIT_AUTHOR_DATE")
    monkeypatch.delenv("GIT_COMMITTER_DATE")
    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(ledger_home))
    monkeypatch.setenv("JACHIN_WORK_LEDGER_LLM_ENABLED", "0")

    from l3_node.work_ledger import generate_instant_work_brief, start_session

    start_session(
        title="Current task",
        project_path=str(project),
        user_goal="Continue current work.",
        auto_collect=False,
    )
    result = generate_instant_work_brief(7)

    assert result["session_count"] == 1
    assert result["git_commit_count"] == 1
    assert result["activity_day_count"] >= 1
    assert result["changed_file_count"] == 1
    assert "Implement historical work evidence" in result["text"]
    assert "项目核心功能与工程实现" in result["text"]
    assert "feature.py" not in result["text"]
    assert "Git 提交：1 个" in result["text"]


def test_multi_day_brief_exposes_daily_checkpoint_progress_without_commits():
    from l3_node.work_ledger import build_instant_work_brief

    text = build_instant_work_brief(
        {
            "window_days": 7,
            "generated_at": "2026-07-27T10:00:00+08:00",
            "activity_day_count": 2,
            "git_commit_count": 0,
            "sessions": [{"session_id": "one", "project_name": "Jachin"}],
            "project_counts": {"Jachin": 1},
            "session_evidence_digests": [
                {
                    "project_name": "Jachin",
                    "daily_checkpoints": [
                        {
                            "date": "2026-07-23",
                            "changed_file_count": 3,
                            "changed_files": [
                                {"path": "l3_node/work_ledger.py", "status": "M"},
                                {
                                    "path": "clients/desktop/src/console/WorkLedger.tsx",
                                    "status": "M",
                                },
                            ],
                        },
                        {
                            "date": "2026-07-24",
                            "changed_file_count": 2,
                            "changed_files": [
                                {
                                    "path": "tests/unit/test_work_ledger.py",
                                    "status": "A",
                                }
                            ],
                        },
                    ],
                }
            ],
            "recent_changed_files": [],
            "recent_project_files": [],
            "recent_notes": [],
            "recent_ai_signals": [],
            "valued_outcomes": [],
            "verified_outcomes": [],
            "git_activity": [],
        }
    )

    assert "推进工作能力建设（2026-07-23，Jachin）" in text
    assert "推进工作能力建设（2026-07-24，Jachin）" in text
    assert "AI 工作账本、工作记忆与复盘" in text
    assert "l3_node/work_ledger.py" not in text
    assert "活跃工作日：2 天" in text


def test_work_ledger_recall_finds_adopted_outputs_across_sessions(tmp_path, monkeypatch):
    ledger_home = tmp_path / "work_ledger"
    kernel_home = tmp_path / "kernel"
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("# Recall\n", encoding="utf-8")
    _git(project, "init")

    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(ledger_home))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(kernel_home))
    monkeypatch.setenv("JACHIN_WORK_LEDGER_LLM_ENABLED", "0")

    from l3_node.work_ledger import (
        add_manual_note,
        adopt_work_output,
        generate_work_outputs,
        recall_work_ledger,
        start_session,
        write_work_ledger_recall_index,
    )

    first = start_session(
        title="Voice owner gating",
        project_path=str(project),
        user_goal="Improve always-on voice owner gating and noise rejection.",
        auto_collect=True,
    )
    sid = str(first["session"]["session_id"])
    add_manual_note(sid, "User confirmed: owner voiceprint should suppress bystander noise.")
    generate_work_outputs(sid)
    adopt_work_output(sid, "methodology_candidates", note="voice gating methodology accepted")

    second = start_session(
        title="Work Ledger recall",
        project_path=str(project),
        user_goal="Make accepted outputs searchable later.",
        auto_collect=True,
    )
    add_manual_note(str(second["session"]["session_id"]), "Recall should search accepted outputs and manual notes.")

    index = write_work_ledger_recall_index(30)
    assert index["session_count"] >= 2
    assert index["adopted_outputs"]

    result = recall_work_ledger("owner voiceprint noise", days=30, limit=5)
    assert result["hit_count"] >= 1
    assert result["ranking"]["stages"] == ["keyword_recall", "rule_score", "normalized_vector_dot"]
    assert any(hit["kind"] in {"adopted_output", "methodology_candidate", "manual_note", "session"} for hit in result["hits"])
    assert all("score_parts" in hit for hit in result["hits"])
    assert any("voice" in str(hit.get("text", "")).lower() or "voice" in str(hit.get("title", "")).lower() for hit in result["hits"])


def test_work_ledger_chat_recall_and_weekly_commands(tmp_path, monkeypatch):
    ledger_home = tmp_path / "work_ledger"
    kernel_home = tmp_path / "kernel"
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("# Chat recall\n", encoding="utf-8")
    _git(project, "init")

    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(ledger_home))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(kernel_home))
    monkeypatch.setenv("JACHIN_WORK_LEDGER_LLM_ENABLED", "0")

    from l3_node.work_ledger import add_manual_note, adopt_work_output, generate_work_outputs, start_session
    from l3_node.work_ledger_chat import handle_work_ledger_chat_command, parse_work_ledger_command

    detail = start_session(
        title="Voice context session",
        project_path=str(project),
        user_goal="Make voice and text share Work Ledger recall.",
        auto_collect=True,
    )
    sid = str(detail["session"]["session_id"])
    add_manual_note(sid, "User confirmed: voice context should be recalled from Work Ledger.")
    generate_work_outputs(sid)
    adopt_work_output(sid, "team_lark_brief", note="accepted team brief")

    parsed = parse_work_ledger_command("上次语音上下文做到哪了")
    assert parsed and parsed["kind"] == "recall"
    recall_reply = handle_work_ledger_chat_command("上次语音上下文做到哪了")
    assert recall_reply and "召回" in recall_reply and "Voice context" in recall_reply

    weekly_reply = handle_work_ledger_chat_command("生成这周工作周报")
    assert weekly_reply and "工作周报" in weekly_reply and "文件：" in weekly_reply

    continue_reply = handle_work_ledger_chat_command("继续语音上下文任务")
    assert continue_reply and "Codex 续写任务书" in continue_reply
