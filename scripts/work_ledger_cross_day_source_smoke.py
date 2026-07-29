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
    runtime = Path(tempfile.gettempdir()) / f"jachin_cross_day_source_smoke_{stamp}"
    project_a = runtime / "project-a"
    project_b = runtime / "project-b"
    exports_a = runtime / "codex-exports-a"
    for path in (project_a, project_b, exports_a):
        path.mkdir(parents=True, exist_ok=True)
    history = exports_a / "codex-history.log"
    history.write_text("implemented project A day-one work asset\n", encoding="utf-8")

    os.environ["JACHIN_WORK_LEDGER_HOME"] = str(runtime / "ledger")
    os.environ["JACHIN_COGNITIVE_KERNEL_HOME"] = str(runtime / "kernel")
    os.environ["JACHIN_WORK_LEDGER_LLM_ENABLED"] = "0"

    from l3_node.work_ledger import end_session, start_session
    from l3_node.work_ledger_sources import (
        configure_work_source_roots,
        get_work_source_status,
        refresh_process_inbox,
        revoke_project_source_authorization,
    )

    day_one = start_session(title="Project A day one", project_path=str(project_a), auto_collect=False)
    day_one_id = str(day_one["session"]["session_id"])
    configure_work_source_roots(day_one_id, [str(exports_a)])
    day_one_inbox = refresh_process_inbox(day_one_id)
    end_session(day_one_id, generate_outputs=False)

    with history.open("a", encoding="utf-8") as stream:
        stream.write("implemented project A day-two continuation only\n")
    day_two = start_session(title="Project A day two", project_path=str(project_a), auto_collect=False)
    day_two_id = str(day_two["session"]["session_id"])
    inherited = get_work_source_status(day_two_id)
    day_two_inbox = refresh_process_inbox(day_two_id)
    end_session(day_two_id, generate_outputs=False)

    project_b_session = start_session(title="Project B", project_path=str(project_b), auto_collect=False)
    project_b_id = str(project_b_session["session"]["session_id"])
    isolated = get_work_source_status(project_b_id)
    end_session(project_b_id, generate_outputs=False)

    moved_exports = runtime / "codex-exports-a-moved"
    exports_a.rename(moved_exports)
    missing_session = start_session(title="Project A missing source", project_path=str(project_a), auto_collect=False)
    missing_id = str(missing_session["session"]["session_id"])
    unavailable = get_work_source_status(missing_id)
    unavailable_refresh = refresh_process_inbox(missing_id)
    revoked = revoke_project_source_authorization(missing_id)
    end_session(missing_id, generate_outputs=False)

    after_revoke = start_session(title="Project A after revoke", project_path=str(project_a), auto_collect=False)
    after_revoke_id = str(after_revoke["session"]["session_id"])
    clean_status = get_work_source_status(after_revoke_id)

    checks = {
        "day_one_reads_one_line": day_one_inbox["last_refresh"]["new_line_count"] == 1,
        "same_project_inherits_authorization": inherited["configured_roots"] == [str(exports_a.resolve())],
        "same_project_inherits_cursor": inherited.get("source_profile", {}).get("inherited") is True,
        "day_two_reads_only_new_line": day_two_inbox["last_refresh"]["new_line_count"] == 1,
        "different_project_is_isolated": isolated["configured_roots"] == [] and isolated["authorizations"] == [],
        "missing_source_is_visible": bool(unavailable["authorizations"]) and unavailable["authorizations"][0]["exists"] is False,
        "missing_source_does_not_fallback": unavailable_refresh["last_refresh"]["sources_read"] == 0,
        "revoke_clears_current_authorization": revoked["configured_roots"] == [],
        "revoke_prevents_future_inheritance": clean_status["configured_roots"] == [],
    }
    result = {
        "ok": all(checks.values()),
        "checks": checks,
        "day_one_session_id": day_one_id,
        "day_two_session_id": day_two_id,
        "inherited_status": inherited,
        "isolated_status": isolated,
        "unavailable_status": unavailable,
        "clean_status": clean_status,
    }
    output_dir = ROOT / "output" / "work_ledger_cross_day_source_smoke"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"work_ledger_cross_day_source_smoke_{stamp}.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**result, "result_path": str(output_path)}, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
