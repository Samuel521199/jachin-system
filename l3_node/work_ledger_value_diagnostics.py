"""Developer diagnostics and persistent logs for Work Ledger Value Chain."""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any


_LOG_LOCK = threading.RLock()


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def diagnostic_log_paths() -> dict[str, str]:
    from l3_node.work_ledger import work_ledger_home

    root = work_ledger_home() / "logs"
    return {
        "jsonl": str(root / "work_ledger_value_chain.jsonl"),
        "markdown": str(root / "work_ledger_value_chain_test_log.md"),
    }


def _safe_payload(value: Any, *, max_chars: int = 5000) -> Any:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(value)
    if len(text) <= max_chars:
        try:
            return json.loads(text)
        except (TypeError, ValueError):
            return text
    return {
        "truncated": True,
        "preview": text[:max_chars],
        "original_chars": len(text),
    }


def append_value_diagnostic_log(
    event: str,
    *,
    status: str,
    session_id: str = "",
    summary: str = "",
    details: Any = None,
) -> dict[str, Any]:
    paths = diagnostic_log_paths()
    jsonl_path = Path(paths["jsonl"])
    markdown_path = Path(paths["markdown"])
    record = {
        "log_id": f"value-log-{uuid.uuid4().hex[:12]}",
        "recorded_at": _now_iso(),
        "event": str(event or "unknown").strip(),
        "status": str(status or "info").strip().lower(),
        "session_id": str(session_id or "").strip(),
        "summary": str(summary or "").strip()[:1000],
        "details": _safe_payload(details),
    }
    with _LOG_LOCK:
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with jsonl_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        if not markdown_path.is_file():
            markdown_path.write_text(
                "# Work Ledger Value Chain Test Log\n\n"
                "该文档由 Jachin 自动追加，用于开发测试和错误排查。"
                "原始结构化日志位于同目录 JSONL 文件。\n\n",
                encoding="utf-8",
            )
        details_text = json.dumps(
            record["details"],
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        with markdown_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(
                f"## {record['recorded_at']} · {record['event']} · "
                f"{record['status'].upper()}\n\n"
                f"- Log ID：`{record['log_id']}`\n"
                f"- Session：`{record['session_id'] or '-'}`\n"
                f"- 摘要：{record['summary'] or '-'}\n\n"
                "<details><summary>诊断详情</summary>\n\n"
                "```json\n"
                f"{details_text}\n"
                "```\n\n"
                "</details>\n\n"
            )
    return {**record, "paths": paths}


def read_value_diagnostic_logs(limit: int = 100) -> dict[str, Any]:
    paths = diagnostic_log_paths()
    path = Path(paths["jsonl"])
    rows: list[dict[str, Any]] = []
    if path.is_file():
        try:
            lines = path.read_text(
                encoding="utf-8-sig",
                errors="replace",
            ).splitlines()
            for line in lines[-max(1, min(int(limit or 100), 500)) :]:
                try:
                    row = json.loads(line)
                    if isinstance(row, dict):
                        rows.append(row)
                except (TypeError, ValueError):
                    continue
        except OSError:
            rows = []
    rows.reverse()
    return {
        "entries": rows,
        "count": len(rows),
        "paths": paths,
    }


def _check(
    checks: list[dict[str, Any]],
    name: str,
    ok: bool,
    *,
    severity: str = "error",
    detail: str = "",
    evidence: Any = None,
) -> None:
    checks.append(
        {
            "name": name,
            "ok": bool(ok),
            "severity": "pass" if ok else severity,
            "detail": detail,
            "evidence": _safe_payload(evidence, max_chars=2500),
        }
    )


def run_value_chain_diagnostics(session_id: str) -> dict[str, Any]:
    from l3_node.work_ledger import get_session_detail
    from l3_node.work_ledger_outcomes import get_session_outcome_context
    from l3_node.work_ledger_value import get_session_value_context

    clean_session_id = str(session_id or "").strip()
    if not clean_session_id:
        raise ValueError("session_id is required")
    started = time.perf_counter()
    try:
        session = get_session_detail(clean_session_id, evidence_limit=300)["session"]
        outcomes = get_session_outcome_context(clean_session_id)
        value_context = get_session_value_context(clean_session_id)
        chain = value_context.get("chain") or {}
        events = [
            event
            for event in chain.get("events") or []
            if isinstance(event, dict)
        ]
        outcome_rows = [
            row
            for row in chain.get("outcome_values") or []
            if isinstance(row, dict)
        ]
        summary = chain.get("summary") or {}
        checks: list[dict[str, Any]] = []
        project_path = str(session.get("project_path") or "").strip()
        _check(
            checks,
            "project_bound",
            bool(project_path),
            detail="Session must bind a project before value accounting.",
            evidence={"project_path": project_path},
        )

        event_ids = [
            str(event.get("value_event_id") or "")
            for event in events
            if str(event.get("value_event_id") or "")
        ]
        _check(
            checks,
            "value_event_ids_unique",
            len(event_ids) == len(set(event_ids)),
            detail="Duplicate value event IDs would double-count adoption or impact.",
            evidence={"event_count": len(event_ids), "unique_count": len(set(event_ids))},
        )

        graph_outcome_ids = {
            str(row.get("outcome_id") or "")
            for row in (outcomes.get("graph") or {}).get("outcomes") or []
            if isinstance(row, dict) and str(row.get("outcome_id") or "")
        }
        broken_refs = sorted(
            {
                str(outcome_id or "")
                for event in events
                for outcome_id in (event.get("outcome_ids") or [])
                if str(outcome_id or "")
                and str(outcome_id or "") not in graph_outcome_ids
            }
        )
        _check(
            checks,
            "outcome_references_valid",
            not broken_refs,
            detail="Every value event outcome reference must exist in Outcome Graph.",
            evidence={"broken_outcome_ids": broken_refs},
        )

        missing_required_outcomes = [
            str(event.get("value_event_id") or "")
            for event in events
            if event.get("event_type")
            in {
                "impact_confirmed",
                "feedback_positive",
                "feedback_neutral",
                "feedback_negative",
            }
            and not event.get("outcome_ids")
        ]
        _check(
            checks,
            "impact_and_feedback_linked",
            not missing_required_outcomes,
            detail="Impact and feedback events must identify the verified outcome.",
            evidence={"event_ids": missing_required_outcomes},
        )

        missing_evidence = [
            str(event.get("value_event_id") or "")
            for event in events
            if not str(event.get("evidence_id") or "")
        ]
        _check(
            checks,
            "value_events_have_evidence",
            not missing_evidence,
            severity="warning",
            detail="Direct developer events may omit Evidence, but production events should not.",
            evidence={"event_ids": missing_evidence[:50]},
        )

        active_rows = [
            row for row in outcome_rows if row.get("status") == "active"
        ]
        expected_summary = {
            "active_outcome_count": len(active_rows),
            "delivered_outcome_count": sum(
                1
                for row in active_rows
                if row.get("value_stage") in {"delivered", "adopted", "impact"}
            ),
            "adopted_outcome_count": sum(
                1
                for row in active_rows
                if row.get("value_stage") in {"adopted", "impact"}
            ),
            "impact_outcome_count": sum(
                1 for row in active_rows if row.get("value_stage") == "impact"
            ),
        }
        summary_mismatches = {
            key: {
                "expected": expected,
                "actual": int(summary.get(key) or 0),
            }
            for key, expected in expected_summary.items()
            if int(summary.get(key) or 0) != expected
        }
        _check(
            checks,
            "value_summary_consistent",
            not summary_mismatches,
            detail="Value summary must equal the active outcome aggregation.",
            evidence=summary_mismatches,
        )

        available = int(summary.get("continuation_available_count") or 0)
        used = int(summary.get("continuation_used_count") or 0)
        _check(
            checks,
            "continuation_usage_bounded",
            used <= available,
            detail="Actual continuation use cannot exceed recorded opportunities.",
            evidence={"available": available, "used": used},
        )

        methodology_attempts = int(
            summary.get("methodology_reuse_attempt_count") or 0
        )
        methodology_success = int(
            summary.get("methodology_reuse_success_count") or 0
        )
        _check(
            checks,
            "methodology_reuse_bounded",
            methodology_success <= methodology_attempts,
            detail="Methodology successes cannot exceed attempts.",
            evidence={
                "attempts": methodology_attempts,
                "successes": methodology_success,
            },
        )

        errors = [
            row
            for row in checks
            if not row.get("ok") and row.get("severity") == "error"
        ]
        warnings = [
            row
            for row in checks
            if not row.get("ok") and row.get("severity") == "warning"
        ]
        status = "failed" if errors else "warning" if warnings else "passed"
        result = {
            "schema_version": 1,
            "session_id": clean_session_id,
            "project_path": project_path,
            "status": status,
            "ok": not errors,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "checks": checks,
            "counts": {
                "checks": len(checks),
                "passed": sum(1 for row in checks if row.get("ok")),
                "warnings": len(warnings),
                "errors": len(errors),
                "events": len(events),
                "outcomes": len(outcome_rows),
            },
            "value_summary": summary,
            "log_paths": diagnostic_log_paths(),
        }
        log = append_value_diagnostic_log(
            "diagnostic_run",
            status=status,
            session_id=clean_session_id,
            summary=(
                f"{result['counts']['passed']}/{result['counts']['checks']} checks passed; "
                f"{len(warnings)} warnings; {len(errors)} errors."
            ),
            details=result,
        )
        result["log_id"] = log["log_id"]
        return result
    except Exception as exc:
        append_value_diagnostic_log(
            "diagnostic_run",
            status="error",
            session_id=clean_session_id,
            summary=f"{type(exc).__name__}: {exc}",
            details={"exception_type": type(exc).__name__, "error": str(exc)},
        )
        raise
