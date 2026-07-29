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
    runtime = Path(tempfile.gettempdir()) / f"jachin_work_source_cursor_smoke_{stamp}"
    project = runtime / "project"
    exports = runtime / "exports"
    project.mkdir(parents=True, exist_ok=True)
    exports.mkdir(parents=True, exist_ok=True)
    history = exports / "codex-history.jsonl"
    first_lines = [json.dumps({"message": f"implemented work source event {index:03d}"}) for index in range(100)]
    history.write_text("\n".join(first_lines) + "\n", encoding="utf-8")

    os.environ["JACHIN_WORK_LEDGER_HOME"] = str(runtime / "ledger")
    os.environ["JACHIN_COGNITIVE_KERNEL_HOME"] = str(runtime / "kernel")
    os.environ["JACHIN_WORK_LEDGER_LLM_ENABLED"] = "0"

    from l3_node.work_ledger import start_session
    from l3_node.work_ledger_sources import (
        configure_work_source_roots,
        control_work_source,
        get_work_source_status,
        refresh_process_inbox,
    )

    detail = start_session(
        title="Incremental AI source cursor smoke",
        project_path=str(project),
        user_goal="Only process newly appended Codex history.",
        auto_collect=False,
    )
    sid = str(detail["session"]["session_id"])
    configure_work_source_roots(sid, [str(exports)])
    first = refresh_process_inbox(sid)

    appended = [json.dumps({"message": f"implemented work source event {index:03d}"}) for index in range(100, 105)]
    with history.open("a", encoding="utf-8") as stream:
        stream.write("\n".join(appended) + "\n")
    second = refresh_process_inbox(sid)
    third = refresh_process_inbox(sid)

    status = get_work_source_status(sid)
    source = next(row for row in status["sources"] if row["source_uri"] == str(history.resolve()))
    control_work_source(sid, "pause", source_key=source["source_key"])
    with history.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"message": "implemented paused event 105"}) + "\n")
    paused = refresh_process_inbox(sid)
    control_work_source(sid, "resume", source_key=source["source_key"])
    resumed = refresh_process_inbox(sid)

    checks = {
        "first_sync_100_lines": first["last_refresh"]["new_line_count"] == 100,
        "second_sync_only_5_lines": second["last_refresh"]["new_line_count"] == 5,
        "unchanged_sync_zero_lines": third["last_refresh"]["new_line_count"] == 0,
        "pause_blocks_new_content": paused["last_refresh"]["new_line_count"] == 0,
        "resume_reads_pending_line": resumed["last_refresh"]["new_line_count"] == 1,
        "configured_root_persisted": status["configured_roots"] == [str(exports.resolve())],
        "cursor_totals_105_before_pause": int(source.get("total_line_count") or 0) == 105,
    }
    result = {
        "ok": all(checks.values()),
        "checks": checks,
        "session_id": sid,
        "first_refresh": first.get("last_refresh"),
        "second_refresh": second.get("last_refresh"),
        "third_refresh": third.get("last_refresh"),
        "paused_refresh": paused.get("last_refresh"),
        "resumed_refresh": resumed.get("last_refresh"),
        "source_status": source,
    }
    output_dir = ROOT / "output" / "work_ledger_source_cursor_smoke"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"work_ledger_source_cursor_smoke_{stamp}.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**result, "result_path": str(output_path)}, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
