"""Thirty-day replay for Work Ledger outcome value accounting."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _completed_event(day: int, summary: str) -> dict:
    return {
        "event_id": f"day-{day}-completed",
        "summary": summary,
        "excerpt": summary,
        "source_types": ["codex"],
        "source_chain": [
            {
                "source_type": "codex",
                "source_uri": f"inline://codex/day-{day}",
            }
        ],
        "verification_evidence_id": f"verify-day-{day}",
        "target_state": "completed",
    }


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="jachin-value-chain-"))
    project = root / "project"
    project.mkdir()
    os.environ["JACHIN_WORK_LEDGER_HOME"] = str(root / "ledger")
    os.environ["JACHIN_COGNITIVE_KERNEL_HOME"] = str(root / "kernel")
    os.environ["JACHIN_WORK_LEDGER_LLM_ENABLED"] = "0"

    from l3_node.work_ledger import (
        build_multi_day_weekly_report,
        build_work_ledger_recall_index,
        start_session,
    )
    from l3_node.work_ledger_facts import (
        get_project_fact_index,
        record_confirmed_work_event,
    )
    from l3_node.work_ledger_outcomes import get_session_outcome_context
    from l3_node.work_ledger_value import (
        get_project_value_chain,
        record_value_event,
    )

    staged_outcomes: list[tuple[str, str, str]] = []
    previous_session_id = ""
    fact_ids: list[str] = []
    stages = {
        0: ("impact_confirmed", "Impact result"),
        6: ("adopted", "Adopted result"),
        12: ("delivered", "Delivered result"),
        18: ("completed", "Completion-only result"),
        24: ("feedback_negative", "Low-value completed result"),
    }

    for day in range(30):
        session = start_session(
            title=f"Replay day {day + 1}",
            project_path=str(project),
            user_goal=f"Continue verified work on replay day {day + 1}",
            auto_collect=False,
        )
        session_id = str(session["session"]["session_id"])
        if previous_session_id:
            record_value_event(
                session_id,
                "continuation_available",
                related_session_id=previous_session_id,
                idempotency_key=f"day-{day}-continuation-available",
            )
            record_value_event(
                session_id,
                "continuation_used",
                related_session_id=previous_session_id,
                output_key="codex_continuation_prompt",
                idempotency_key=f"day-{day}-continuation-used",
            )
        if day in stages:
            event_type, summary = stages[day]
            recorded = record_confirmed_work_event(
                session_id,
                _completed_event(day, summary),
            )
            fact_ids.append(str(recorded["fact"]["fact_id"]))
            outcome_id = str(
                get_session_outcome_context(session_id)["outcomes_this_session"][0][
                    "outcome_id"
                ]
            )
            staged_outcomes.append((event_type, outcome_id, summary))
            if event_type != "completed":
                record_value_event(
                    session_id,
                    event_type,
                    outcome_ids=[outcome_id],
                    impact_value=(
                        "Reduced context reconstruction by 30 minutes."
                        if event_type == "impact_confirmed"
                        else ""
                    ),
                    note=(
                        "Correct result, but it was not useful in the real workflow."
                        if event_type == "feedback_negative"
                        else ""
                    ),
                    idempotency_key=f"day-{day}-{event_type}",
                )
            if event_type == "impact_confirmed":
                record_value_event(
                    session_id,
                    "feedback_positive",
                    outcome_ids=[outcome_id],
                    note="Used successfully in the next work session.",
                    idempotency_key="impact-positive-feedback",
                )
        if day in {8, 16}:
            record_value_event(
                session_id,
                "methodology_reused",
                methodology_id="method-replay",
                idempotency_key=f"method-success-{day}",
            )
        if day == 22:
            record_value_event(
                session_id,
                "methodology_reuse_failed",
                methodology_id="method-replay",
                idempotency_key="method-failure-22",
            )
        previous_session_id = session_id

    chain = get_project_value_chain(str(project))
    index = build_work_ledger_recall_index(30)
    report = build_multi_day_weekly_report(index, title="30 Day Value Replay")
    rows = {
        str(row.get("summary") or ""): row
        for row in chain.get("outcome_values") or []
    }
    expected_order = [
        "Impact result",
        "Adopted result",
        "Delivered result",
        "Completion-only result",
        "Low-value completed result",
    ]
    actual_order = [
        str(row.get("summary") or "")
        for row in chain.get("outcome_values") or []
        if row.get("status") == "active"
    ]
    facts = get_project_fact_index(str(project))["facts"]
    assertions = {
        "five_verified_outcomes": len(actual_order) == 5,
        "value_order": actual_order == expected_order,
        "impact_stage": rows["Impact result"]["value_stage"] == "impact",
        "negative_feedback_does_not_reopen": all(
            fact.get("state") == "completed"
            for fact in facts
            if fact.get("fact_id") in fact_ids
        ),
        "continuation_use_rate": chain["summary"]["continuation_use_rate"] == 1.0,
        "methodology_reuse_rate": (
            chain["summary"]["methodology_reuse_success_rate"] == 0.667
        ),
        "weekly_value_labels": all(
            marker in report
            for marker in (
                "[已产生影响] Impact result",
                "[已采用] Adopted result",
                "[已交付] Delivered result",
                "[已完成] Completion-only result",
            )
        ),
        "weekly_value_order": (
            report.index("Impact result")
            < report.index("Adopted result")
            < report.index("Delivered result")
            < report.index("Completion-only result")
        ),
        "index_value_summary": (
            index["value_summary"]["impact_outcome_count"] == 1
            and index["value_summary"]["continuation_used_count"] == 29
        ),
    }
    result = {
        "ok": all(assertions.values()),
        "generated_at": time.time(),
        "workspace": str(root),
        "assertions": assertions,
        "summary": chain.get("summary"),
        "value_order": actual_order,
        "weekly_report": report,
    }
    out_dir = Path("output") / "work_ledger_value_chain_smoke"
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / f"work_ledger_value_chain_smoke_{int(time.time())}.json"
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({**result, "output_path": str(output_path)}, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
