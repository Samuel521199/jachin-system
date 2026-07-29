from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _prepare_project(root: Path) -> Path:
    project = root / "sample_project"
    project.mkdir(parents=True, exist_ok=True)
    if not (project / ".git").is_dir():
        _git(project, "init")
    (project / "README.md").write_text("# Seven day Work Ledger replay\n", encoding="utf-8")
    (project / "reliability.py").write_text("STATE = 'seven-day-replay'\n", encoding="utf-8")
    return project


def _backdate_session(ledger_home: Path, session_id: str, days_ago: int) -> None:
    target = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)
    target_ms = int(target.timestamp() * 1000)
    target_iso = target.strftime("%Y-%m-%dT%H:%M:%S%z")
    session_path = ledger_home / "sessions" / session_id / "session.json"
    session = json.loads(session_path.read_text(encoding="utf-8"))
    session["created_at_ms"] = target_ms
    session["updated_at_ms"] = target_ms + 60_000
    session["start_time"] = target_iso
    if session.get("end_time"):
        session["end_time"] = (target + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S%z")
    session_path.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")

    evidence_path = ledger_home / "sessions" / session_id / "evidence.jsonl"
    evidence_rows: list[dict[str, Any]] = []
    for index, line in enumerate(evidence_path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        row = json.loads(line)
        row["collected_at_ms"] = target_ms + index * 1000
        row["collected_at"] = (target + timedelta(seconds=index)).strftime("%Y-%m-%dT%H:%M:%S%z")
        evidence_rows.append(row)
    evidence_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in evidence_rows) + "\n",
        encoding="utf-8",
    )

    index_path = ledger_home / "sessions.json"
    index_rows = json.loads(index_path.read_text(encoding="utf-8"))
    for row in index_rows:
        if row.get("session_id") == session_id:
            row.update(
                {
                    "created_at_ms": session["created_at_ms"],
                    "updated_at_ms": session["updated_at_ms"],
                    "start_time": session["start_time"],
                    "end_time": session.get("end_time"),
                }
            )
    index_rows.sort(key=lambda item: int(item.get("updated_at_ms") or 0), reverse=True)
    index_path.write_text(json.dumps(index_rows, ensure_ascii=False, indent=2), encoding="utf-8")


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    stamp = int(time.time())
    out_root = Path(args.output_dir or ROOT / "output" / "work_ledger_seven_day_replay").resolve()
    run_root = out_root / f"run_{stamp}"
    ledger_home = run_root / "ledger"
    run_root.mkdir(parents=True, exist_ok=True)
    os.environ["JACHIN_WORK_LEDGER_HOME"] = str(ledger_home)
    os.environ["JACHIN_COGNITIVE_KERNEL_HOME"] = str(run_root / "kernel")
    os.environ["JACHIN_WORK_LEDGER_LLM_ENABLED"] = "0"
    project = _prepare_project(run_root)
    candidate = project / "codex_seven_day_trace.log"
    candidate.write_text("Decision: seven day candidate feedback should remain reusable.\n", encoding="utf-8")

    from l3_node.work_ledger import (
        add_manual_note,
        adopt_work_output,
        build_work_ledger_reliability,
        end_session,
        recall_work_ledger,
        record_work_process_candidate_feedback,
        start_session,
        write_work_ledger_reliability_report,
    )

    sessions: list[str] = []
    for days_ago in reversed(range(7)):
        detail = start_session(
            title=f"Replay day {7 - days_ago}",
            project_path=str(project),
            user_goal="Keep seven consecutive days of reusable Jachin work context.",
            created_from="script:work_ledger_seven_day_replay",
            auto_collect=True,
        )
        sid = str(detail["session"]["session_id"])
        sessions.append(sid)
        note = (
            "用户确认：第一天记住续接暗号 ORBIT-LEDGER-731，后续必须能召回。"
            if days_ago == 6
            else f"用户确认：这是七天 replay 的第 {7 - days_ago} 天。"
        )
        add_manual_note(sid, note)
        if days_ago == 6:
            record_work_process_candidate_feedback(sid, str(candidate), action="accepted", note="trusted seven day source")
        elif days_ago == 5:
            record_work_process_candidate_feedback(sid, str(candidate), action="rejected", note="one noisy replay sample")
        generate_outputs = days_ago != 3
        end_session(sid, generate_outputs=generate_outputs)
        if generate_outputs and days_ago % 2 == 0:
            adopt_work_output(sid, "daily_report", note=f"adopted replay day {7 - days_ago}")
        _backdate_session(ledger_home, sid, days_ago)

    reliability = build_work_ledger_reliability(7)
    written = write_work_ledger_reliability_report(7)
    recall = recall_work_ledger("ORBIT-LEDGER-731", days=7, limit=5)
    metrics = reliability["metrics"]
    checks = {
        "seven_active_days": metrics["active_days"] == 7,
        "seven_day_streak": metrics["current_streak"] == 7,
        "all_sessions_closed": metrics["completion_rate"] == 1.0,
        "one_asset_gap_detected": any(item["kind"] == "recorded_without_assets" for item in reliability["reminders"]),
        "asset_formation_rate_expected": metrics["asset_formation_rate"] == round(6 / 7, 3),
        "continuation_evidence_recorded": reliability["continuation"]["opportunities"] == 6,
        "continuation_gap_detected": reliability["continuation"]["hits"] == 5,
        "first_day_fact_recalled": recall["hit_count"] > 0 and any("ORBIT-LEDGER-731" in str(hit.get("text") or "") for hit in recall["hits"]),
        "report_written": Path(str(written.get("path") or "")).is_file(),
    }
    result = {
        "ok": all(checks.values()),
        "checks": checks,
        "metrics": metrics,
        "continuation": reliability["continuation"],
        "reminders": reliability["reminders"],
        "daily": reliability["daily"],
        "recall_hits": recall["hits"],
        "sessions": sessions,
        "report_path": written.get("path"),
    }
    result_path = out_root / f"work_ledger_seven_day_replay_{stamp}.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["result_path"] = str(result_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay seven days of Work Ledger usage and verify cross-day continuity.")
    parser.add_argument("--output-dir", default="", help="Directory for isolated replay artifacts.")
    args = parser.parse_args()
    result = run_smoke(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
