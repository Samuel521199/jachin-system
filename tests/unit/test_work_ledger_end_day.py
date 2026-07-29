from __future__ import annotations

import subprocess
from pathlib import Path


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def test_work_ledger_end_day_preview_and_finalize_guard_sensitive_material(tmp_path, monkeypatch):
    ledger_home = tmp_path / "work_ledger"
    kernel_home = tmp_path / "kernel"
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("# End day\n", encoding="utf-8")
    (project / "voice.py").write_text("# TODO: verify owner voice gate\n", encoding="utf-8")
    _git(project, "init")

    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(ledger_home))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(kernel_home))
    monkeypatch.setenv("JACHIN_WORK_LEDGER_LLM_ENABLED", "0")

    from l3_node.work_ledger import (
        build_end_day_preview,
        finalize_end_day_package,
        read_output_text,
        start_session,
    )
    from l3_node.work_ledger_chat import handle_work_ledger_chat_command, parse_work_ledger_command

    detail = start_session(
        title="End day package",
        project_path=str(project),
        user_goal="Make daily closing one click.",
        auto_collect=True,
    )
    sid = str(detail["session"]["session_id"])

    sensitive = "Decision: keep package preview.\nDASHSCOPE_API_KEY=sk-1234567890abcdef1234567890abcdef"
    preview = build_end_day_preview(sid, process_text=sensitive)
    assert preview["preview"]["safety"]["blocked"] is True
    assert "api_key" in preview["preview"]["safety"]["types"]

    safe_trace = "\n".join(
        [
            "Goal: make end-day package one click.",
            "Changed: add preview before finalizing daily report.",
            "Decision: user confirmation should generate context pack.",
            "Next: verify console button path.",
        ]
    )
    result = finalize_end_day_package(sid, process_text=safe_trace, close_session=True)
    outputs = result["outputs"]
    assert "context_pack" in outputs
    assert "daily_report" in outputs
    context = read_output_text(sid, "context_pack", max_chars=6000)["text"]
    assert "Changed: add preview before finalizing daily report" in context
    assert "Decision: user confirmation should generate context pack" in context
    assert result["closed"]["session"]["status"] == "closed"

    parsed = parse_work_ledger_command("收工预览")
    assert parsed and parsed["kind"] == "end_day_preview"
    reply = handle_work_ledger_chat_command("确认收工")
    assert reply and "当前没有活动工作任务" in reply


def test_work_ledger_end_day_preview_discovers_process_candidates_without_auto_import(tmp_path, monkeypatch):
    ledger_home = tmp_path / "work_ledger"
    kernel_home = tmp_path / "kernel"
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("# Candidate discovery\n", encoding="utf-8")
    _git(project, "init")

    trace_path = project / "codex_work_trace.log"
    trace_path.write_text(
        "\n".join(
            [
                "Goal: improve Work Ledger candidate discovery.",
                "Changed: detect recent Codex and Cursor process logs.",
                "Decision: preview should show candidates before importing.",
                "Next: let user select a candidate file.",
                "DASHSCOPE_API_KEY=sk-1234567890abcdef1234567890abcdef",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(ledger_home))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(kernel_home))
    monkeypatch.setenv("JACHIN_WORK_LEDGER_LLM_ENABLED", "0")

    from l3_node.work_ledger import build_end_day_preview, discover_work_process_candidates, load_evidence, start_session

    detail = start_session(
        title="Candidate discovery",
        project_path=str(project),
        user_goal="Make end-day preview suggest useful local process files.",
        auto_collect=True,
    )
    sid = str(detail["session"]["session_id"])

    discovered = discover_work_process_candidates(sid)
    assert discovered["candidate_count"] >= 1
    assert any(str(item.get("source", {}).get("file_path", "")).endswith("codex_work_trace.log") for item in discovered["candidates"])

    preview = build_end_day_preview(sid)
    candidates = preview["preview"]["candidates"]
    found = [item for item in candidates if item.get("kind") == "discovered_process_file"]
    assert found
    assert preview["preview"]["safety"]["blocked"] is False

    sources = [row["source"] for row in load_evidence(sid)]
    assert "end_day_preview" in sources
    assert "ai_work_trace" not in sources


def test_work_ledger_candidate_feedback_controls_recall_trust(tmp_path, monkeypatch):
    ledger_home = tmp_path / "work_ledger"
    kernel_home = tmp_path / "kernel"
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("# Candidate feedback\n", encoding="utf-8")
    _git(project, "init")

    accepted_trace = project / "codex_accept_trace.log"
    accepted_trace.write_text(
        "\n".join(
            [
                "Goal: preserve accepted candidate feedback.",
                "Changed: accepted material should enter recall.",
                "Decision: use user_confirmed trust for accepted candidate.",
            ]
        ),
        encoding="utf-8",
    )
    rejected_trace = project / "codex_reject_trace.log"
    rejected_trace.write_text(
        "\n".join(
            [
                "Goal: reject noisy candidate feedback.",
                "Changed: rejected material should not become a high trust recall hit.",
                "Decision: noisy candidate should stay out of recall.",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(ledger_home))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(kernel_home))
    monkeypatch.setenv("JACHIN_WORK_LEDGER_LLM_ENABLED", "0")

    from l3_node.work_ledger import (
        adopt_work_process_candidate,
        build_work_process_candidate_source_quality,
        build_work_ledger_recall_index,
        load_evidence,
        recall_work_ledger,
        record_work_process_candidate_feedback,
        start_session,
    )

    detail = start_session(
        title="Candidate feedback trust",
        project_path=str(project),
        user_goal="Verify candidate feedback trust layer.",
        auto_collect=True,
    )
    sid = str(detail["session"]["session_id"])

    adopted = adopt_work_process_candidate(sid, str(accepted_trace), adopted_by="test", generate_outputs_after=False)
    rejected = record_work_process_candidate_feedback(sid, str(rejected_trace), action="rejected", note="test rejection")

    sources = [row["source"] for row in load_evidence(sid)]
    assert "ai_work_trace" in sources
    assert "work_process_candidate_feedback" in sources
    assert adopted["feedback"]["trust_level"] == "user_confirmed"
    assert rejected["trust_level"] == "user_rejected"

    index = build_work_ledger_recall_index(days=7)
    assert len(index["adopted_process_candidates"]) == 1
    assert len(index["rejected_process_candidates"]) == 1

    quality = build_work_process_candidate_source_quality(days=7)
    assert quality["sources"]["codex_trace"]["total"] == 2
    assert quality["sources"]["codex_trace"]["accepted"] == 1
    assert quality["sources"]["codex_trace"]["rejected"] == 1
    assert quality["totals"]["total"] == 2
    assert quality["summary"]["source_count"] == 1
    assert quality["ranked_sources"][0]["quality_key"] == "codex_trace"

    accepted_hits = recall_work_ledger("accepted candidate feedback", days=7, limit=5)["hits"]
    assert any(hit["kind"] == "adopted_process_candidate" and hit["trust_level"] == "user_confirmed" for hit in accepted_hits)

    rejected_hits = recall_work_ledger("noisy candidate should stay out", days=7, limit=5)["hits"]
    assert not any(hit.get("trust_level") == "user_rejected" for hit in rejected_hits)


def test_candidate_feedback_changes_next_preview_ranking_and_is_written_to_evidence(tmp_path, monkeypatch):
    ledger_home = tmp_path / "work_ledger"
    kernel_home = tmp_path / "kernel"
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("# Candidate quality ranking\n", encoding="utf-8")
    _git(project, "init")

    codex_trace = project / "codex_quality_trace.log"
    cursor_trace = project / "cursor_quality_trace.log"
    content = "\n".join(
        [
            "Goal: verify candidate quality feedback.",
            "Changed: rank useful sources above rejected sources.",
            "Decision: feedback must affect the next preview.",
        ]
    )
    codex_trace.write_text(content, encoding="utf-8")
    cursor_trace.write_text(content, encoding="utf-8")

    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(ledger_home))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(kernel_home))
    monkeypatch.setenv("JACHIN_WORK_LEDGER_LLM_ENABLED", "0")

    from l3_node.work_ledger import (
        build_end_day_preview,
        build_work_process_candidate_source_quality,
        discover_work_process_candidates,
        load_evidence,
        record_work_process_candidate_feedback,
        start_session,
    )

    detail = start_session(
        title="Candidate quality ranking",
        project_path=str(project),
        user_goal="Verify source feedback changes ranking.",
        auto_collect=True,
    )
    sid = str(detail["session"]["session_id"])

    before = discover_work_process_candidates(sid, limit=20)["candidates"]
    before_scores = {
        Path(str(item.get("source", {}).get("file_path") or "")).name: float(item.get("score") or 0)
        for item in before
    }
    assert codex_trace.name in before_scores
    assert cursor_trace.name in before_scores

    for _ in range(2):
        record_work_process_candidate_feedback(sid, str(codex_trace), action="accepted", note="useful source")
        record_work_process_candidate_feedback(sid, str(cursor_trace), action="rejected", note="noisy source")

    after = discover_work_process_candidates(sid, limit=20)["candidates"]
    after_scores = {
        Path(str(item.get("source", {}).get("file_path") or "")).name: float(item.get("score") or 0)
        for item in after
    }
    assert after_scores[codex_trace.name] > before_scores[codex_trace.name]
    assert after_scores[cursor_trace.name] < before_scores[cursor_trace.name]
    assert after_scores[codex_trace.name] > after_scores[cursor_trace.name]

    quality = build_work_process_candidate_source_quality(days=7)
    assert quality["sources"]["codex_trace"]["score_adjustment"] > 0
    assert quality["sources"]["cursor_trace"]["score_adjustment"] < 0

    preview = build_end_day_preview(sid)
    preview_quality = preview["preview"]["candidate_quality"]
    assert preview_quality["totals"]["accepted"] == 2
    assert preview_quality["totals"]["rejected"] == 2
    preview_events = [row for row in load_evidence(sid) if row["source"] == "end_day_preview"]
    assert preview_events
    assert preview_events[-1]["payload"]["candidate_quality"]["summary"]["source_count"] >= 2
