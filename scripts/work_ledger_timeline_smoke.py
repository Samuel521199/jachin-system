from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    stamp = int(time.time())
    out_root = Path(args.output_dir or ROOT / "output" / "work_ledger_timeline_smoke").resolve()
    run_root = Path(tempfile.gettempdir()) / "jachin_work_ledger_timeline_smoke" / f"run_{stamp}"
    ledger_home = run_root / "ledger"
    project = run_root / "non_git_project"
    project.mkdir(parents=True, exist_ok=True)
    work_file = project / "daily_notes.txt"
    work_file.write_text("Task started.\n", encoding="utf-8")
    os.environ["JACHIN_WORK_LEDGER_HOME"] = str(ledger_home)
    os.environ["JACHIN_COGNITIVE_KERNEL_HOME"] = str(run_root / "kernel")
    os.environ["JACHIN_WORK_LEDGER_LLM_ENABLED"] = "0"

    from l3_node.work_ledger import (
        add_ai_work_trace,
        add_manual_note,
        build_work_timeline,
        collect_work_checkpoint,
        end_session,
        load_evidence,
        start_session,
    )

    detail = start_session(
        title="Non-git long task timeline",
        project_path=str(project),
        user_goal="Verify a long non-Git task remains observable without duplicate evidence.",
        created_from="script:work_ledger_timeline_smoke",
        auto_collect=False,
    )
    sid = str(detail["session"]["session_id"])
    first = collect_work_checkpoint(sid, trigger="smoke_initial")
    duplicate_results = [collect_work_checkpoint(sid, trigger=f"smoke_idle_{index}") for index in range(20)]
    changed_results: list[dict[str, Any]] = []
    for index in range(5):
        with work_file.open("a", encoding="utf-8") as handle:
            handle.write(f"Checkpoint change {index + 1}: completed a non-Git work step.\n")
        changed_results.append(collect_work_checkpoint(sid, trigger=f"smoke_change_{index + 1}"))
    add_manual_note(sid, "用户确认：非 Git 工作也必须进入今天的任务时间线。")
    add_ai_work_trace(
        sid,
        "Codex reviewed the notes, identified five completed steps, and proposed tomorrow's follow-up.",
        tool_name="Codex",
    )
    closed = end_session(sid, generate_outputs=True)
    timeline = build_work_timeline(sid, limit=300)
    evidence = load_evidence(sid, 1000)
    checkpoints = [row for row in evidence if row.get("source") == "work_checkpoint"]
    categories = {str(row.get("category") or "") for row in timeline.get("entries", [])}
    checks = {
        "first_checkpoint_filesystem": first.get("project_kind") == "filesystem",
        "idle_checkpoints_deduplicated": all(bool(row.get("deduplicated")) for row in duplicate_results),
        "changed_checkpoints_recorded": all(not bool(row.get("deduplicated")) for row in changed_results),
        "checkpoint_count_bounded": len(checkpoints) == 6,
        "timeline_unifies_sources": {"task", "checkpoint", "user_note", "ai_process", "output"}.issubset(categories),
        "daily_assets_generated": bool((closed.get("outputs") or {}).get("daily_report")) and bool((closed.get("outputs") or {}).get("context_pack")),
    }
    result = {
        "ok": all(checks.values()),
        "checks": checks,
        "session_id": sid,
        "checkpoint_count": len(checkpoints),
        "deduplicated_idle_count": sum(1 for row in duplicate_results if row.get("deduplicated")),
        "timeline_entry_count": timeline.get("entry_count"),
        "timeline_category_counts": timeline.get("category_counts"),
        "output_paths": (closed.get("session") or {}).get("output_paths"),
    }
    result_path = out_root / f"work_ledger_timeline_smoke_{stamp}.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["result_path"] = str(result_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Work Ledger timeline checkpoints for a long non-Git task.")
    parser.add_argument("--output-dir", default="", help="Directory for isolated smoke artifacts.")
    args = parser.parse_args()
    result = run_smoke(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
