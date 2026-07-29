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


def _event(
    event_id: str,
    summary: str,
    source_type: str,
    *,
    excerpt: str = "",
) -> dict:
    return {
        "event_id": event_id,
        "summary": summary,
        "excerpt": excerpt or summary,
        "source_types": [source_type],
        "source_chain": [
            {
                "source_type": source_type,
                "source_uri": f"inline://{source_type}/{event_id}",
            }
        ],
        "dedupe_tokens": [],
    }


def main() -> int:
    stamp = int(time.time())
    runtime = Path(tempfile.gettempdir()) / f"jachin_project_fact_smoke_{stamp}"
    project = runtime / "project"
    project.mkdir(parents=True, exist_ok=True)
    os.environ["JACHIN_WORK_LEDGER_HOME"] = str(runtime / "ledger")
    os.environ["JACHIN_COGNITIVE_KERNEL_HOME"] = str(runtime / "kernel")
    os.environ["JACHIN_WORK_LEDGER_LLM_ENABLED"] = "0"

    from l3_node.work_ledger import end_session, generate_work_outputs, start_session
    from l3_node.work_ledger_facts import (
        get_project_fact_index,
        record_confirmed_work_event,
    )

    source_types = ["codex", "terminal", "document", "cursor", "git", "codex", "terminal"]
    summaries = [
        "Implemented cross-day cursor persistence in l3_node/work_ledger_sources.py",
        "Cross-day cursor persistence implemented in l3_node/work_ledger_sources.py",
        "Verified cross-day cursor persistence in l3_node/work_ledger_sources.py",
        "Updated cross-day cursor persistence in l3_node/work_ledger_sources.py",
        "Cross-day cursor persistence completed in l3_node/work_ledger_sources.py",
        "Validated cross-day cursor persistence in l3_node/work_ledger_sources.py",
        "Cross-day cursor persistence verified in l3_node/work_ledger_sources.py",
    ]
    stable_fact_ids: list[str] = []
    day_session_ids: list[str] = []
    last_session_id = ""
    for index, (source_type, summary) in enumerate(zip(source_types, summaries), start=1):
        detail = start_session(
            title=f"Project fact day {index}",
            project_path=str(project),
            user_goal="Keep Work Ledger source cursor continuity reliable.",
            auto_collect=False,
        )
        session_id = str(detail["session"]["session_id"])
        day_session_ids.append(session_id)
        result = record_confirmed_work_event(
            session_id,
            _event(f"day-{index}", summary, source_type),
            verification_evidence_id=f"ev-day-{index}",
        )
        stable_fact_ids.append(str(result["fact"]["fact_id"]))
        last_session_id = session_id
        if index < len(source_types):
            end_session(session_id, generate_outputs=False)

    distinct = record_confirmed_work_event(
        last_session_id,
        _event(
            "distinct-fact",
            "Added source authorization revoke endpoint in l3_node/work_ledger_http.py",
            "codex",
        ),
        verification_evidence_id="ev-distinct",
    )

    first_uncertain = record_confirmed_work_event(
        last_session_id,
        _event(
            "uncertain-a",
            "Implemented release package source registry synchronization for private catalogs",
            "document",
            excerpt="Source registry synchronization preserves private catalog package origins.",
        ),
    )
    second_uncertain = record_confirmed_work_event(
        last_session_id,
        _event(
            "uncertain-b",
            "Implemented release package model registry validation for public catalogs",
            "terminal",
            excerpt="Model registry validation checks public catalog runtime dependencies.",
        ),
    )

    outputs = generate_work_outputs(last_session_id)
    report = Path(outputs["daily_report"]).read_text(encoding="utf-8")
    index = get_project_fact_index(str(project))
    stable_fact = next(
        (
            fact
            for fact in index["facts"]
            if fact.get("fact_id") == stable_fact_ids[0]
        ),
        {},
    )
    checks = {
        "seven_days_share_one_fact_id": len(set(stable_fact_ids)) == 1,
        "seven_occurrences_are_preserved": stable_fact.get("occurrence_count") == 7,
        "all_source_types_are_preserved": set(source_types).issubset(
            set(stable_fact.get("source_types") or [])
        ),
        "distinct_work_stays_distinct": (
            distinct["fact"]["fact_id"] != stable_fact_ids[0]
        ),
        "ambiguous_work_is_not_silently_merged": (
            first_uncertain["fact"]["fact_id"]
            != second_uncertain["fact"]["fact_id"]
        ),
        "ambiguous_work_enters_review": index["summary"]["review_pending"] >= 1,
        "daily_report_marks_repeated_fact_as_continued": (
            "[持续事实]" in report
            and "累计出现 7 次" in report
        ),
        "daily_report_keeps_distinct_new_fact": (
            "[新增完成] Added source authorization revoke endpoint" in report
        ),
    }
    result = {
        "ok": all(checks.values()),
        "checks": checks,
        "project_path": str(project),
        "session_ids": day_session_ids,
        "stable_fact_id": stable_fact_ids[0],
        "stable_fact": stable_fact,
        "distinct_fact_id": distinct["fact"]["fact_id"],
        "uncertain_fact_ids": [
            first_uncertain["fact"]["fact_id"],
            second_uncertain["fact"]["fact_id"],
        ],
        "fact_summary": index["summary"],
        "review_queue": index["review_queue"],
        "daily_report": outputs["daily_report"],
    }
    output_dir = ROOT / "output" / "work_ledger_project_fact_chain_smoke"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"work_ledger_project_fact_chain_smoke_{stamp}.json"
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
