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


def _event(
    day: int,
    event_id: str,
    summary: str,
    *,
    source_type: str = "codex",
    **extra,
):
    observed_at = (
        datetime(2026, 4, 1, 9, 0, tzinfo=timezone(timedelta(hours=8)))
        + timedelta(days=day - 1)
    ).strftime("%Y-%m-%dT%H:%M:%S%z")
    return {
        "event_id": event_id,
        "summary": summary,
        "excerpt": summary,
        "observed_at": observed_at,
        "source_types": [source_type],
        "source_chain": [
            {
                "source_type": source_type,
                "source_uri": f"inline://{source_type}/{event_id}",
            }
        ],
        "verification_evidence_id": f"ev-{event_id}",
        **extra,
    }


def main() -> int:
    stamp = int(time.time())
    sandbox = Path(tempfile.gettempdir()) / f"jachin_outcome_graph_smoke_{stamp}"
    project = sandbox / "project"
    ledger = sandbox / "ledger"
    kernel = sandbox / "kernel"
    project.mkdir(parents=True, exist_ok=True)
    os.environ["JACHIN_WORK_LEDGER_HOME"] = str(ledger)
    os.environ["JACHIN_COGNITIVE_KERNEL_HOME"] = str(kernel)
    os.environ["JACHIN_WORK_LEDGER_LLM_ENABLED"] = "0"

    from l3_node.work_ledger import (
        end_session,
        generate_multi_day_weekly_report,
        start_session,
    )
    from l3_node.work_ledger_facts import record_confirmed_work_event
    from l3_node.work_ledger_outcomes import (
        get_project_outcome_graph,
        review_methodology_candidate,
    )

    recovery_fact_id = ""
    weekly_fact_id = ""
    pending_fact_id = ""
    legacy_fact_id = ""
    replacement_fact_id = ""
    session_ids: list[str] = []

    for day in range(1, 91):
        session = start_session(
            title=f"Outcome graph replay day {day}",
            project_path=str(project),
            user_goal="Keep project achievements traceable without duplicate claims.",
            auto_collect=False,
        )
        session_id = str(session["session"]["session_id"])
        session_ids.append(session_id)

        if day == 1:
            recorded = record_confirmed_work_event(
                session_id,
                _event(
                    day,
                    "recovery-open",
                    "Outcome graph atomic persistence failed in outcome_store.py",
                    target_state="open",
                    failure_reason="Concurrent replacement truncated outcome_store.py.",
                    next_action="Add project-scoped atomic replacement.",
                ),
            )
            recovery_fact_id = str(recorded["fact"]["fact_id"])
        elif day == 8:
            record_confirmed_work_event(
                session_id,
                _event(
                    day,
                    "recovery-progress",
                    "Outcome graph atomic persistence implementation is in progress",
                    project_fact_id=recovery_fact_id,
                    target_state="in_progress",
                    decision="Use project-scoped locks and atomic file replacement.",
                ),
            )
        elif day == 15:
            record_confirmed_work_event(
                session_id,
                _event(
                    day,
                    "recovery-complete-one",
                    "Outcome graph atomic persistence tests passed",
                    project_fact_id=recovery_fact_id,
                    target_state="completed",
                ),
            )
        elif day == 31:
            record_confirmed_work_event(
                session_id,
                _event(
                    day,
                    "recovery-regression",
                    "Outcome graph atomic persistence concurrency regression failed",
                    project_fact_id=recovery_fact_id,
                    target_state="reopened",
                    failure_reason="A stale temp file survived process interruption.",
                    next_action="Clean stale temp files before atomic replacement.",
                ),
            )
        elif day == 38:
            record_confirmed_work_event(
                session_id,
                _event(
                    day,
                    "recovery-second-progress",
                    "Outcome graph interruption recovery is in progress",
                    project_fact_id=recovery_fact_id,
                    target_state="in_progress",
                    decision="Validate and remove stale temp files under the same project lock.",
                ),
            )
        elif day == 45:
            record_confirmed_work_event(
                session_id,
                _event(
                    day,
                    "recovery-complete-two",
                    "Outcome graph interruption recovery suite passed twice",
                    project_fact_id=recovery_fact_id,
                    target_state="completed",
                ),
            )
        elif day == 5:
            recorded = record_confirmed_work_event(
                session_id,
                _event(
                    day,
                    "weekly-complete",
                    "Completed verified achievement accounting in weekly_policy.py",
                    target_state="completed",
                    source_type="git",
                ),
            )
            weekly_fact_id = str(recorded["fact"]["fact_id"])
        elif day in {20, 40, 60, 80}:
            record_confirmed_work_event(
                session_id,
                _event(
                    day,
                    f"weekly-repeat-{day}",
                    "Verified achievement accounting remains stable in weekly_policy.py",
                    project_fact_id=weekly_fact_id,
                    source_type="terminal",
                ),
            )
        elif day == 50:
            recorded = record_confirmed_work_event(
                session_id,
                _event(
                    day,
                    "pending-open",
                    "Methodology review migration remains blocked in review_queue.py",
                    target_state="open",
                    failure_reason="Legacy review rows still need migration.",
                    next_action="Write the review row migration.",
                ),
            )
            pending_fact_id = str(recorded["fact"]["fact_id"])
        elif day == 55:
            recorded = record_confirmed_work_event(
                session_id,
                _event(
                    day,
                    "legacy-complete",
                    "Completed legacy outcome renderer in legacy_renderer.py",
                    target_state="completed",
                ),
            )
            legacy_fact_id = str(recorded["fact"]["fact_id"])
        elif day == 70:
            recorded = record_confirmed_work_event(
                session_id,
                _event(
                    day,
                    "replacement-complete",
                    "Completed relationship-first outcome renderer in graph_renderer.py",
                    target_state="completed",
                    supersedes_fact_id=legacy_fact_id,
                ),
            )
            replacement_fact_id = str(recorded["fact"]["fact_id"])

        end_session(session_id, generate_outputs=False)

    graph = get_project_outcome_graph(str(project))
    pending_candidates = [
        row
        for row in graph.get("methodology_candidates") or []
        if row.get("status") == "pending_review"
    ]
    if pending_candidates:
        review_methodology_candidate(
            str(project),
            str(pending_candidates[0]["candidate_id"]),
            "approve",
            note="Approved after the 90-session replay.",
        )
    graph = get_project_outcome_graph(str(project))
    weekly = generate_multi_day_weekly_report(90)
    weekly_text = Path(weekly["path"]).read_text(encoding="utf-8")
    active_outcomes = [
        row for row in graph.get("outcomes") or [] if row.get("status") == "active"
    ]
    active_fact_ids = {str(row.get("fact_id") or "") for row in active_outcomes}
    approved_methods = [
        row
        for row in graph.get("methodology_candidates") or []
        if row.get("status") == "approved"
    ]

    recovery_summary = "Outcome graph atomic persistence failed in outcome_store.py"
    weekly_summary = "Completed verified achievement accounting in weekly_policy.py"
    pending_summary = "Methodology review migration remains blocked in review_queue.py"
    legacy_summary = "Completed legacy outcome renderer in legacy_renderer.py"
    replacement_summary = "Completed relationship-first outcome renderer in graph_renderer.py"
    checks = {
        "ninety_sessions_recorded": len(session_ids) == 90,
        "active_outcomes_are_not_duplicated": (
            len(active_outcomes) == 3
            and active_fact_ids
            == {recovery_fact_id, weekly_fact_id, replacement_fact_id}
        ),
        "repeated_fact_has_one_active_outcome": (
            sum(
                1
                for row in active_outcomes
                if row.get("fact_id") == weekly_fact_id
            )
            == 1
        ),
        "open_fact_is_not_an_outcome": pending_fact_id not in active_fact_ids,
        "superseded_fact_is_not_an_active_outcome": (
            legacy_fact_id not in active_fact_ids
            and replacement_fact_id in active_fact_ids
        ),
        "methodology_requires_and_preserves_user_approval": (
            len(approved_methods) == 1
            and approved_methods[0]["fact_id"] == recovery_fact_id
            and bool(approved_methods[0]["evidence_ids"])
        ),
        "weekly_contains_verified_outcomes_once": (
            weekly_text.count(f"- {recovery_summary} （") == 1
            and weekly_text.count(f"- {weekly_summary} （") == 1
            and weekly_text.count(f"- {replacement_summary} （") == 1
        ),
        "weekly_excludes_open_and_superseded_claims": (
            pending_summary not in weekly_text and legacy_summary not in weekly_text
        ),
        "weekly_contains_only_approved_methodology": (
            "## 6. 已批准方法论" in weekly_text
            and approved_methods[0]["title"] in weekly_text
            and "待审查方法论候选" not in weekly_text
        ),
        "relationship_graph_is_traceable": {
            edge.get("relation") for edge in graph.get("edges") or []
        }
        >= {
            "has_failure",
            "informed_decision",
            "drives_action",
            "produced_outcome",
            "verified_as",
            "suggests_methodology",
            "superseded_by",
        },
    }
    result = {
        "ok": all(checks.values()),
        "checks": checks,
        "project_path": str(project),
        "session_count": len(session_ids),
        "active_outcomes": active_outcomes,
        "approved_methodologies": approved_methods,
        "graph_summary": graph.get("summary") or {},
        "weekly_report": weekly["path"],
    }
    output_dir = ROOT / "output" / "work_ledger_outcome_graph_smoke"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"work_ledger_outcome_graph_smoke_{stamp}.json"
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
