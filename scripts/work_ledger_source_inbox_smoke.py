from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    stamp = int(time.time())
    runtime = Path(tempfile.gettempdir()) / f"jachin_work_source_smoke_{stamp}"
    project = runtime / "project"
    project.mkdir(parents=True, exist_ok=True)
    artifact = project / "work_ledger_sources.py"
    artifact.write_text("# source adapter\n", encoding="utf-8")
    os.environ["JACHIN_WORK_LEDGER_HOME"] = str(runtime / "ledger")
    os.environ["JACHIN_COGNITIVE_KERNEL_HOME"] = str(runtime / "kernel")
    os.environ["JACHIN_WORK_LEDGER_LLM_ENABLED"] = "0"

    from l3_node.work_ledger import build_work_timeline, collect_work_checkpoint, generate_work_outputs, start_session
    from l3_node.work_ledger_sources import refresh_process_inbox, review_process_inbox_event

    detail = start_session(
        title="Multi-source work inbox smoke",
        project_path=str(project),
        user_goal="Merge repeated Codex, terminal, document, and file evidence.",
        auto_collect=False,
    )
    sid = str(detail["session"]["session_id"])
    artifact.write_text("# source adapter\n# inbox deduplication implemented\n", encoding="utf-8")
    collect_work_checkpoint(sid, trigger="smoke", force=True)
    inline_sources = [
        {"source_type": "codex", "source_uri": "inline://codex/1", "text": "Implemented WorkSourceAdapter inbox deduplication in work_ledger_sources.py and verified it."},
        {"source_type": "terminal", "source_uri": "inline://terminal/1", "text": "pytest verified WorkSourceAdapter inbox deduplication in work_ledger_sources.py: all tests passed."},
        {"source_type": "document", "source_uri": "inline://document/1", "text": "WorkSourceAdapter inbox deduplication completed in work_ledger_sources.py."},
    ]
    inbox = refresh_process_inbox(sid, inline_sources=inline_sources)
    merged = max(inbox["events"], key=lambda row: int(row.get("source_count") or 0))
    reviewed = review_process_inbox_event(sid, merged["event_id"], "accepted", generate_outputs_after=False)
    outputs = generate_work_outputs(sid)
    refreshed = refresh_process_inbox(sid, inline_sources=inline_sources)
    persisted = next(row for row in refreshed["events"] if row["event_id"] == merged["event_id"])
    timeline = build_work_timeline(sid)

    checks = {
        "multi_source_merged": int(merged.get("source_count") or 0) >= 4,
        "codex_terminal_document_file_chain": {"codex", "terminal", "document", "file_checkpoint"}.issubset(set(merged.get("source_types") or [])),
        "review_persisted": persisted.get("status") == "accepted",
        "timeline_review_visible": any(row.get("category") == "candidate_feedback" for row in timeline.get("entries") or []),
        "daily_report_generated": bool(outputs.get("daily_report")),
        "continuation_prompt_generated": bool(outputs.get("codex_continuation_prompt")),
    }
    result = {
        "ok": all(checks.values()),
        "checks": checks,
        "session_id": sid,
        "candidate_count": inbox.get("candidate_count"),
        "event_count": inbox.get("event_count"),
        "merged_source_count": merged.get("source_count"),
        "merged_source_types": merged.get("source_types"),
        "review_evidence_id": (reviewed.get("feedback") or {}).get("evidence_id"),
        "timeline_entry_count": timeline.get("entry_count"),
    }
    output_dir = ROOT / "output" / "work_ledger_source_inbox_smoke"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"work_ledger_source_inbox_smoke_{stamp}.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**result, "result_path": str(output_path)}, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
