from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _observed_at(day: int) -> str:
    zone = timezone(timedelta(hours=8))
    value = datetime(2026, 6, 1, 9, 0, tzinfo=zone) + timedelta(days=day - 1)
    return value.strftime("%Y-%m-%dT%H:%M:%S%z")


def _event(
    day: int,
    event_id: str,
    summary: str,
    *,
    fact_id: str = "",
    target_state: str = "",
    source_type: str = "codex",
    decision: str = "",
    failure_reason: str = "",
    next_action: str = "",
    supersedes_fact_id: str = "",
) -> dict:
    return {
        "event_id": event_id,
        "summary": summary,
        "excerpt": summary,
        "observed_at": _observed_at(day),
        "project_fact_id": fact_id,
        "target_state": target_state,
        "decision": decision,
        "failure_reason": failure_reason,
        "next_action": next_action,
        "supersedes_fact_id": supersedes_fact_id,
        "source_types": [source_type],
        "source_chain": [
            {
                "source_type": source_type,
                "source_uri": f"inline://{source_type}/day-{day}/{event_id}",
            }
        ],
    }


def main() -> int:
    stamp = int(time.time())
    runtime = Path(tempfile.gettempdir()) / f"jachin_fact_lifecycle_smoke_{stamp}"
    project = runtime / "project"
    project.mkdir(parents=True, exist_ok=True)
    os.environ["JACHIN_WORK_LEDGER_HOME"] = str(runtime / "ledger")
    os.environ["JACHIN_COGNITIVE_KERNEL_HOME"] = str(runtime / "kernel")
    os.environ["JACHIN_WORK_LEDGER_LLM_ENABLED"] = "0"

    from l3_node.work_ledger import end_session, generate_work_outputs, start_session
    from l3_node.work_ledger_facts import (
        get_project_fact_index,
        get_session_fact_context,
        record_confirmed_work_event,
        review_fact_match,
    )

    main_fact_id = ""
    replacement_fact_id = ""
    session_ids: list[str] = []
    separated_pair: tuple[str, str] | None = None
    last_session_id = ""
    final_outputs: dict[str, str] = {}

    for day in range(1, 31):
        detail = start_session(
            title=f"Lifecycle day {day:02d}",
            project_path=str(project),
            user_goal="Build reliable project fact lifecycle and decision history.",
            auto_collect=False,
        )
        session_id = str(detail["session"]["session_id"])
        session_ids.append(session_id)
        last_session_id = session_id

        if day == 1:
            result = record_confirmed_work_event(
                session_id,
                _event(
                    day,
                    "main-open",
                    "Project fact lifecycle is blocked by missing transition persistence",
                    target_state="open",
                    failure_reason="Transition persistence is missing.",
                    next_action="Implement append-only lifecycle transitions.",
                ),
            )
            main_fact_id = str(result["fact"]["fact_id"])
        elif day == 2:
            record_confirmed_work_event(
                session_id,
                _event(
                    day,
                    "main-progress",
                    "Project fact lifecycle transition persistence is in progress",
                    fact_id=main_fact_id,
                    target_state="in_progress",
                ),
            )
        elif day == 3:
            record_confirmed_work_event(
                session_id,
                _event(
                    day,
                    "main-decision",
                    "Decision: keep lifecycle transitions append-only",
                    fact_id=main_fact_id,
                    decision="Keep lifecycle transitions append-only for auditability.",
                ),
            )
        elif day == 10:
            record_confirmed_work_event(
                session_id,
                _event(
                    day,
                    "main-complete",
                    "Project fact lifecycle transition persistence verified and completed",
                    fact_id=main_fact_id,
                    target_state="completed",
                    source_type="terminal",
                ),
            )
        elif day == 18:
            record_confirmed_work_event(
                session_id,
                _event(
                    day,
                    "main-regression",
                    "Project fact lifecycle regression failed during concurrent update",
                    fact_id=main_fact_id,
                    source_type="terminal",
                    failure_reason="Concurrent update reopened the completed lifecycle fact.",
                    next_action="Add atomic-write concurrency regression coverage.",
                ),
            )
        elif day == 19:
            record_confirmed_work_event(
                session_id,
                _event(
                    day,
                    "main-recovery",
                    "Project fact lifecycle concurrency recovery is in progress",
                    fact_id=main_fact_id,
                    target_state="in_progress",
                ),
            )
        elif day == 25:
            record_confirmed_work_event(
                session_id,
                _event(
                    day,
                    "main-recomplete",
                    "Project fact lifecycle concurrency recovery tests passed",
                    fact_id=main_fact_id,
                    target_state="completed",
                    source_type="terminal",
                ),
            )
        elif day == 28:
            replacement = record_confirmed_work_event(
                session_id,
                _event(
                    day,
                    "replacement",
                    "Added versioned project knowledge graph lifecycle engine",
                    target_state="completed",
                    supersedes_fact_id=main_fact_id,
                ),
            )
            replacement_fact_id = str(replacement["fact"]["fact_id"])
        elif day > 28:
            record_confirmed_work_event(
                session_id,
                _event(
                    day,
                    f"replacement-observation-{day}",
                    "Versioned project knowledge graph lifecycle engine remains verified",
                    fact_id=replacement_fact_id,
                    source_type="git",
                ),
            )
        else:
            record_confirmed_work_event(
                session_id,
                _event(
                    day,
                    f"main-observation-{day}",
                    "Project fact lifecycle transition persistence remains under verification",
                    fact_id=main_fact_id,
                    source_type="document" if day % 2 else "git",
                ),
            )

        if day == 5:
            left = record_confirmed_work_event(
                session_id,
                _event(
                    day,
                    "boundary-left",
                    "Implemented release package source registry synchronization for private catalogs",
                ),
            )
            right = record_confirmed_work_event(
                session_id,
                _event(
                    day,
                    "boundary-right",
                    "Implemented release package model registry validation for public catalogs",
                    source_type="document",
                ),
            )
            separated_pair = (
                str(left["fact"]["fact_id"]),
                str(right["fact"]["fact_id"]),
            )
            index = get_project_fact_index(str(project))
            candidate = index["review_queue"][0]
            review_fact_match(
                str(project),
                str(candidate["candidate_id"]),
                "separate",
            )
        elif day == 20 and separated_pair:
            record_confirmed_work_event(
                session_id,
                _event(
                    day,
                    "boundary-right",
                    "Implemented release package model registry validation for public catalogs",
                    source_type="terminal",
                ),
            )

        if day == 30:
            final_outputs = generate_work_outputs(session_id)
        else:
            end_session(session_id, generate_outputs=False)

    index = get_project_fact_index(str(project))
    main_fact = next(row for row in index["facts"] if row["fact_id"] == main_fact_id)
    replacement_fact = next(
        row for row in index["facts"] if row["fact_id"] == replacement_fact_id
    )
    main_states = [row["to_state"] for row in main_fact["lifecycle"]]
    final_context = get_session_fact_context(last_session_id)
    report = Path(final_outputs["daily_report"]).read_text(encoding="utf-8")
    prompt = Path(final_outputs["codex_continuation_prompt"]).read_text(
        encoding="utf-8"
    )
    checks = {
        "thirty_sessions_recorded": len(session_ids) == 30,
        "lifecycle_sequence_is_complete": main_states == [
            "open",
            "in_progress",
            "completed",
            "reopened",
            "in_progress",
            "completed",
            "superseded",
        ],
        "failure_history_is_preserved": len(main_fact["failure_attempts"]) == 2,
        "decision_history_is_preserved": (
            main_fact["decisions"][-1]["text"]
            == "Keep lifecycle transitions append-only for auditability."
        ),
        "next_action_history_is_preserved": (
            main_fact["next_actions"][-1]["text"]
            == "Add atomic-write concurrency regression coverage."
        ),
        "replacement_relation_is_preserved": (
            main_fact["state"] == "superseded"
            and main_fact["superseded_by_fact_id"] == replacement_fact_id
            and replacement_fact["state"] == "completed"
        ),
        "separation_rule_is_persistent": (
            len(
                [
                    row
                    for row in index.get("separation_rules") or []
                    if row.get("status") == "active"
                ]
            )
            == 1
            and index["summary"]["review_pending"] == 0
        ),
        "final_session_continues_replacement": any(
            row["fact_id"] == replacement_fact_id
            for row in final_context["continued_facts"]
        ),
        "report_does_not_claim_repeated_fact_as_new": (
            "[持续事实]" in report
            and "[新增完成] Versioned project knowledge graph lifecycle engine"
            not in report
        ),
        "continuation_prompt_contains_decision_chain": (
            "当前方案替代的历史事实与决策" in prompt
            and "Keep lifecycle transitions append-only for auditability." in prompt
            and "Concurrent update reopened the completed lifecycle fact." in prompt
            and "Add atomic-write concurrency regression coverage." in prompt
        ),
    }
    result = {
        "ok": all(checks.values()),
        "checks": checks,
        "project_path": str(project),
        "session_count": len(session_ids),
        "main_fact_id": main_fact_id,
        "main_lifecycle": main_fact["lifecycle"],
        "replacement_fact_id": replacement_fact_id,
        "fact_summary": index["summary"],
        "separation_rules": index.get("separation_rules") or [],
        "daily_report": final_outputs["daily_report"],
        "continuation_prompt": final_outputs["codex_continuation_prompt"],
    }
    output_dir = ROOT / "output" / "work_ledger_fact_lifecycle_smoke"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"work_ledger_fact_lifecycle_smoke_{stamp}.json"
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {**result, "result_path": str(output_path)},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
