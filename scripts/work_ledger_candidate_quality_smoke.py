from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _prepare_project(root: Path) -> tuple[Path, Path, Path]:
    project = root / "sample_project"
    project.mkdir(parents=True, exist_ok=True)
    if not (project / ".git").is_dir():
        _git(project, "init")
    (project / "README.md").write_text("# Candidate quality smoke\n", encoding="utf-8")
    trace = "\n".join(
        [
            "Goal: make candidate ranking learn from user feedback.",
            "Changed: record accepted and rejected source quality.",
            "Decision: the next preview must explain its ranking.",
            "Next: verify candidate quality in Evidence.",
        ]
    )
    codex_trace = project / "codex_candidate_quality_trace.log"
    cursor_trace = project / "cursor_candidate_quality_trace.log"
    codex_trace.write_text(trace, encoding="utf-8")
    cursor_trace.write_text(trace, encoding="utf-8")
    return project, codex_trace, cursor_trace


def _score_by_name(candidates: list[dict[str, Any]]) -> dict[str, float]:
    return {
        Path(str((item.get("source") or {}).get("file_path") or "")).name: float(item.get("score") or 0.0)
        for item in candidates
        if isinstance(item.get("source"), dict)
    }


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    stamp = int(time.time())
    out_root = Path(args.output_dir or ROOT / "output" / "work_ledger_candidate_quality_smoke").resolve()
    run_root = out_root / f"run_{stamp}"
    run_root.mkdir(parents=True, exist_ok=True)
    os.environ["JACHIN_WORK_LEDGER_HOME"] = str(run_root / "ledger")
    os.environ["JACHIN_COGNITIVE_KERNEL_HOME"] = str(run_root / "kernel")
    os.environ["JACHIN_WORK_LEDGER_LLM_ENABLED"] = "0"

    project, codex_trace, cursor_trace = _prepare_project(run_root)
    from l3_node.work_ledger import (
        build_end_day_preview,
        build_work_process_candidate_source_quality,
        discover_work_process_candidates,
        load_evidence,
        record_work_process_candidate_feedback,
        start_session,
    )

    detail = start_session(
        title="Candidate quality live smoke",
        project_path=str(project),
        user_goal="Prove that accepted and rejected sources change the next preview ranking.",
        created_from="script:work_ledger_candidate_quality_smoke",
        auto_collect=True,
    )
    sid = str(detail["session"]["session_id"])
    before = discover_work_process_candidates(sid, limit=20)
    before_scores = _score_by_name(before.get("candidates", []))

    for _ in range(2):
        record_work_process_candidate_feedback(sid, str(codex_trace), action="accepted", note="smoke accepted source")
        record_work_process_candidate_feedback(sid, str(cursor_trace), action="rejected", note="smoke rejected source")

    after = discover_work_process_candidates(sid, limit=20)
    after_scores = _score_by_name(after.get("candidates", []))
    quality = build_work_process_candidate_source_quality(days=7)
    preview = build_end_day_preview(sid)
    evidence = load_evidence(sid, 1000)
    preview_events = [row for row in evidence if row.get("source") == "end_day_preview"]

    checks = {
        "both_candidates_discovered": codex_trace.name in before_scores and cursor_trace.name in before_scores,
        "accepted_source_score_increased": after_scores.get(codex_trace.name, -999) > before_scores.get(codex_trace.name, 999),
        "rejected_source_score_decreased": after_scores.get(cursor_trace.name, 999) < before_scores.get(cursor_trace.name, -999),
        "accepted_source_ranks_higher": after_scores.get(codex_trace.name, -999) > after_scores.get(cursor_trace.name, 999),
        "preview_contains_quality": bool((preview.get("preview") or {}).get("candidate_quality")),
        "evidence_contains_quality": bool(
            preview_events
            and isinstance(preview_events[-1].get("payload"), dict)
            and (preview_events[-1].get("payload") or {}).get("candidate_quality")
        ),
    }
    result = {
        "ok": all(checks.values()),
        "session_id": sid,
        "checks": checks,
        "before_scores": before_scores,
        "after_scores": after_scores,
        "quality": quality,
        "preview_candidate_order": [
            Path(str((item.get("source") or {}).get("file_path") or "")).name
            for item in (preview.get("preview") or {}).get("candidates", [])
            if isinstance(item.get("source"), dict) and (item.get("source") or {}).get("file_path")
        ],
        "evidence_sources": sorted({str(row.get("source") or "") for row in evidence}),
    }
    result_path = out_root / f"work_ledger_candidate_quality_smoke_{stamp}.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["result_path"] = str(result_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Work Ledger candidate source quality learning and Evidence output.")
    parser.add_argument("--output-dir", default="", help="Directory for isolated smoke artifacts.")
    args = parser.parse_args()
    result = run_smoke(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
