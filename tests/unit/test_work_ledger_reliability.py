from __future__ import annotations

import subprocess
from pathlib import Path


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def test_work_ledger_reliability_tracks_assets_and_cross_session_continuation(tmp_path, monkeypatch):
    ledger_home = tmp_path / "work_ledger"
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("# Reliability\n", encoding="utf-8")
    _git(project, "init")

    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(ledger_home))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))
    monkeypatch.setenv("JACHIN_WORK_LEDGER_LLM_ENABLED", "0")

    from l3_node.work_ledger import (
        add_manual_note,
        adopt_work_output,
        build_work_ledger_reliability,
        end_session,
        load_evidence,
        start_session,
        write_work_ledger_reliability_report,
    )

    first = start_session(
        title="Day one",
        project_path=str(project),
        user_goal="Create a reusable context pack.",
        auto_collect=True,
    )
    first_sid = str(first["session"]["session_id"])
    add_manual_note(first_sid, "用户确认：第一天完成可靠性指标底座。")
    end_session(first_sid, generate_outputs=True)
    adopt_work_output(first_sid, "daily_report", note="accepted first daily report")

    second = start_session(
        title="Day two continuation",
        project_path=str(project),
        user_goal="Continue from yesterday's context pack.",
        auto_collect=True,
    )
    second_sid = str(second["session"]["session_id"])
    continuation = [row for row in load_evidence(second_sid) if row["source"] == "work_continuation_context"]
    assert continuation
    assert continuation[-1]["payload"]["previous_session_id"] == first_sid
    assert continuation[-1]["payload"]["hit"] is True

    add_manual_note(second_sid, "用户确认：第二天已续接，但故意不生成输出用于检测缺口。")
    end_session(second_sid, generate_outputs=False)

    reliability = build_work_ledger_reliability(7)
    assert reliability["metrics"]["session_count"] == 2
    assert reliability["metrics"]["completion_rate"] == 1.0
    assert reliability["metrics"]["asset_formation_rate"] == 0.5
    assert reliability["continuation"]["opportunities"] == 1
    assert reliability["continuation"]["hits"] == 1
    assert reliability["metrics"]["continuation_hit_rate"] == 1.0
    assert any(item["kind"] == "recorded_without_assets" and item["session_id"] == second_sid for item in reliability["reminders"])

    written = write_work_ledger_reliability_report(7)
    assert Path(written["path"]).is_file()
