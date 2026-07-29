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
    runtime = Path(tempfile.gettempdir()) / f"jachin_work_source_health_smoke_{stamp}"
    project = runtime / "project"
    exports = runtime / "exports"
    project.mkdir(parents=True, exist_ok=True)
    exports.mkdir(parents=True, exist_ok=True)
    history = exports / "codex-history.log"
    history.write_text("", encoding="utf-8")

    os.environ["JACHIN_WORK_LEDGER_HOME"] = str(runtime / "ledger")
    os.environ["JACHIN_COGNITIVE_KERNEL_HOME"] = str(runtime / "kernel")
    os.environ["JACHIN_WORK_LEDGER_LLM_ENABLED"] = "0"

    from l3_node.work_ledger import load_evidence, start_session
    from l3_node.work_ledger_sources import (
        configure_work_source_roots,
        get_process_inbox,
        get_work_source_status,
        refresh_process_inbox,
    )

    detail = start_session(
        title="Sparse automatic source sync smoke",
        project_path=str(project),
        user_goal="Process only six real changes across sixty polling rounds.",
        auto_collect=False,
    )
    session_id = str(detail["session"]["session_id"])
    configure_work_source_roots(session_id, [str(exports)])
    evidence_before = len(load_evidence(session_id, 1000))
    changed_rounds = {5, 15, 25, 35, 45, 55}
    observed_changed_rounds: list[int] = []
    total_duration_ms = 0

    for round_index in range(60):
        if round_index in changed_rounds:
            with history.open("a", encoding="utf-8") as stream:
                stream.write(
                    f"implemented verified work ledger source health milestone round {round_index}\n"
                )
        inbox = refresh_process_inbox(session_id)
        stats = inbox["last_refresh"]
        total_duration_ms += int(stats.get("duration_ms") or 0)
        if int(stats.get("sources_read") or 0) > 0:
            observed_changed_rounds.append(round_index)

    status = get_work_source_status(session_id)
    inbox = get_process_inbox(session_id)
    evidence_after = len(load_evidence(session_id, 1000))
    health = status["health"]
    checks = {
        "sixty_sync_rounds_recorded": health.get("sync_count") == 60,
        "only_six_rounds_processed": observed_changed_rounds == sorted(changed_rounds),
        "changed_health_count_is_six": health.get("changed_sync_count") == 6,
        "idle_health_count_is_fifty_four": health.get("unchanged_sync_count") == 54,
        "only_six_lines_consumed": health.get("total_lines") == 6,
        "no_source_failures": health.get("failed_source_count") == 0,
        "no_automatic_adoption": inbox.get("summary", {}).get("accepted") == 0,
        "no_evidence_written_by_sync": evidence_after == evidence_before,
        "average_sync_under_100ms": float(health.get("average_duration_ms") or 0) < 100,
    }
    result = {
        "ok": all(checks.values()),
        "checks": checks,
        "session_id": session_id,
        "changed_rounds": observed_changed_rounds,
        "health": health,
        "last_refresh": status.get("last_refresh"),
        "inbox_summary": inbox.get("summary"),
        "evidence_before": evidence_before,
        "evidence_after": evidence_after,
        "wall_clock_duration_ms": total_duration_ms,
    }
    output_dir = ROOT / "output" / "work_ledger_source_health_smoke"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"work_ledger_source_health_smoke_{stamp}.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**result, "result_path": str(output_path)}, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
