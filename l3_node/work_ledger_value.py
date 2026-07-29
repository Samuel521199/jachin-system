"""Outcome value-chain events for Work Ledger.

This layer never mutates project facts or verified outcomes. It records what
happened after completion: delivery, adoption, measurable impact, continuation,
methodology reuse, and explicit user value feedback.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any


_VALUE_LOCK = threading.RLock()
_EVENT_TYPES = {
    "delivered",
    "adopted",
    "impact_confirmed",
    "feedback_positive",
    "feedback_neutral",
    "feedback_negative",
    "continuation_available",
    "continuation_used",
    "methodology_reused",
    "methodology_reuse_failed",
}
_STAGE_ORDER = {
    "completed": 1,
    "delivered": 2,
    "adopted": 3,
    "impact": 4,
}
_STAGE_SCORE = {
    "completed": 20.0,
    "delivered": 45.0,
    "adopted": 72.0,
    "impact": 100.0,
}
_FEEDBACK_SCORE = {
    "positive": 15.0,
    "neutral": 0.0,
    "negative": -35.0,
}


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _stable_id(prefix: str, *parts: Any) -> str:
    payload = "\x1f".join(str(part or "").strip().lower() for part in parts)
    return f"{prefix}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:20]}"


def _ledger_path(project_key: str) -> Path:
    from l3_node.work_ledger import work_ledger_home

    return work_ledger_home() / "project_value" / f"{project_key}.json"


def _load_ledger(project_key: str) -> dict[str, Any]:
    path = _ledger_path(project_key)
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                data.setdefault("events", [])
                return data
    except (OSError, TypeError, ValueError):
        pass
    return {
        "schema_version": 1,
        "project_key": project_key,
        "events": [],
    }


def _save_ledger(ledger: dict[str, Any]) -> None:
    project_key = str(ledger.get("project_key") or "")
    if not project_key:
        raise ValueError("project_key is required")
    path = _ledger_path(project_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    ledger["updated_at"] = _now_iso()
    temp_path = path.with_suffix(
        f"{path.suffix}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        temp_path.write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        for attempt in range(6):
            try:
                os.replace(temp_path, path)
                return
            except PermissionError:
                if attempt >= 5:
                    raise
                time.sleep(0.02 * (attempt + 1))
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass


def _event_feedback(event_type: str) -> str:
    if event_type.startswith("feedback_"):
        return event_type.removeprefix("feedback_")
    return ""


def _stage_for_events(events: list[dict[str, Any]]) -> str:
    stage = "completed"
    for event in events:
        event_type = str(event.get("event_type") or "")
        candidate = {
            "delivered": "delivered",
            "adopted": "adopted",
            "impact_confirmed": "impact",
        }.get(event_type)
        if candidate and _STAGE_ORDER[candidate] > _STAGE_ORDER[stage]:
            stage = candidate
    return stage


def _latest_feedback(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    feedback = [
        event
        for event in events
        if _event_feedback(str(event.get("event_type") or ""))
    ]
    if not feedback:
        return None
    return sorted(feedback, key=lambda row: str(row.get("recorded_at") or ""))[-1]


def _aggregate(
    graph: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    value_rows: list[dict[str, Any]] = []
    for outcome in graph.get("outcomes") or []:
        if not isinstance(outcome, dict):
            continue
        outcome_id = str(outcome.get("outcome_id") or "")
        outcome_events = [
            event
            for event in events
            if outcome_id in (event.get("outcome_ids") or [])
        ]
        stage = _stage_for_events(outcome_events)
        latest_feedback = _latest_feedback(outcome_events)
        feedback = (
            _event_feedback(str(latest_feedback.get("event_type") or ""))
            if latest_feedback
            else ""
        )
        methodology_successes = sum(
            1
            for event in outcome_events
            if event.get("event_type") == "methodology_reused"
        )
        score = (
            _STAGE_SCORE[stage]
            + _FEEDBACK_SCORE.get(feedback, 0.0)
            + min(15.0, methodology_successes * 5.0)
        )
        if outcome.get("status") != "active":
            score -= 50.0
        value_rows.append(
            {
                **outcome,
                "value_stage": stage,
                "value_score": round(max(0.0, score), 1),
                "latest_feedback": feedback,
                "feedback_note": str(
                    (latest_feedback or {}).get("note") or ""
                ),
                "delivered_count": sum(
                    1
                    for event in outcome_events
                    if event.get("event_type") == "delivered"
                ),
                "adoption_count": sum(
                    1
                    for event in outcome_events
                    if event.get("event_type") == "adopted"
                ),
                "impact_count": sum(
                    1
                    for event in outcome_events
                    if event.get("event_type") == "impact_confirmed"
                ),
                "methodology_reuse_count": methodology_successes,
                "value_event_count": len(outcome_events),
                "latest_value_at": max(
                    (str(event.get("recorded_at") or "") for event in outcome_events),
                    default="",
                ),
            }
        )
    value_rows.sort(
        key=lambda row: (
            float(row.get("value_score") or 0),
            str(row.get("latest_value_at") or row.get("completed_at") or ""),
        ),
        reverse=True,
    )
    active_rows = [row for row in value_rows if row.get("status") == "active"]
    continuation_available = sum(
        1 for event in events if event.get("event_type") == "continuation_available"
    )
    continuation_used = sum(
        1 for event in events if event.get("event_type") == "continuation_used"
    )
    methodology_success = sum(
        1 for event in events if event.get("event_type") == "methodology_reused"
    )
    methodology_failed = sum(
        1
        for event in events
        if event.get("event_type") == "methodology_reuse_failed"
    )
    methodology_attempts = methodology_success + methodology_failed
    return {
        "outcome_values": value_rows,
        "summary": {
            "active_outcome_count": len(active_rows),
            "delivered_outcome_count": sum(
                1
                for row in active_rows
                if _STAGE_ORDER[str(row.get("value_stage") or "completed")] >= 2
            ),
            "adopted_outcome_count": sum(
                1
                for row in active_rows
                if _STAGE_ORDER[str(row.get("value_stage") or "completed")] >= 3
            ),
            "impact_outcome_count": sum(
                1
                for row in active_rows
                if row.get("value_stage") == "impact"
            ),
            "positive_feedback_count": sum(
                1 for row in active_rows if row.get("latest_feedback") == "positive"
            ),
            "negative_feedback_count": sum(
                1 for row in active_rows if row.get("latest_feedback") == "negative"
            ),
            "delivery_rate": round(
                sum(
                    1
                    for row in active_rows
                    if _STAGE_ORDER[str(row.get("value_stage") or "completed")] >= 2
                )
                / max(1, len(active_rows)),
                3,
            ),
            "adoption_rate": round(
                sum(
                    1
                    for row in active_rows
                    if _STAGE_ORDER[str(row.get("value_stage") or "completed")] >= 3
                )
                / max(1, len(active_rows)),
                3,
            ),
            "impact_rate": round(
                sum(1 for row in active_rows if row.get("value_stage") == "impact")
                / max(1, len(active_rows)),
                3,
            ),
            "continuation_available_count": continuation_available,
            "continuation_used_count": continuation_used,
            "continuation_use_rate": round(
                continuation_used / max(1, continuation_available),
                3,
            ),
            "methodology_reuse_attempt_count": methodology_attempts,
            "methodology_reuse_success_count": methodology_success,
            "methodology_reuse_success_rate": round(
                methodology_success / max(1, methodology_attempts),
                3,
            ),
        },
    }


def get_project_value_chain(project_path: str) -> dict[str, Any]:
    from l3_node.work_ledger_outcomes import get_project_outcome_graph

    with _VALUE_LOCK:
        graph = get_project_outcome_graph(project_path)
        project_key = str(graph.get("project_key") or "")
        ledger = _load_ledger(project_key)
        aggregate = _aggregate(graph, list(ledger.get("events") or []))
        return {
            **ledger,
            "project_path": str(graph.get("project_path") or project_path),
            "generated_at": _now_iso(),
            **aggregate,
        }


def get_session_value_context(session_id: str) -> dict[str, Any]:
    from l3_node.work_ledger import get_session_detail

    session = get_session_detail(session_id, evidence_limit=5)["session"]
    chain = get_project_value_chain(str(session.get("project_path") or ""))
    events = [
        event
        for event in chain.get("events") or []
        if event.get("session_id") == session_id
        or event.get("related_session_id") == session_id
    ]
    outcome_values = [
        row
        for row in chain.get("outcome_values") or []
        if row.get("completion_session_id") == session_id
        or any(
            str(row.get("outcome_id") or "") in (event.get("outcome_ids") or [])
            for event in events
        )
    ]
    return {
        "chain": chain,
        "events_this_session": events,
        "outcome_values_this_session": outcome_values,
        "summary": chain.get("summary") or {},
    }


def record_value_event(
    session_id: str,
    event_type: str,
    *,
    outcome_ids: list[str] | None = None,
    output_key: str = "",
    channel: str = "",
    note: str = "",
    impact_value: str = "",
    related_session_id: str = "",
    methodology_id: str = "",
    evidence_id: str = "",
    idempotency_key: str = "",
) -> dict[str, Any]:
    from l3_node.work_ledger import get_session_detail
    from l3_node.work_ledger_outcomes import get_project_outcome_graph

    clean_type = str(event_type or "").strip().lower()
    if clean_type not in _EVENT_TYPES:
        raise ValueError(f"unsupported value event type: {clean_type}")
    session = get_session_detail(session_id, evidence_limit=5)["session"]
    project_path = str(session.get("project_path") or "")
    graph = get_project_outcome_graph(project_path)
    project_key = str(graph.get("project_key") or "")
    valid_outcome_ids = {
        str(row.get("outcome_id") or "")
        for row in graph.get("outcomes") or []
        if isinstance(row, dict)
    }
    clean_outcome_ids = list(
        dict.fromkeys(
            str(value or "").strip()
            for value in (outcome_ids or [])
            if str(value or "").strip()
        )
    )
    unknown = [value for value in clean_outcome_ids if value not in valid_outcome_ids]
    if unknown:
        raise ValueError(f"unknown outcome ids: {', '.join(unknown)}")
    if clean_type in {
        "impact_confirmed",
        "feedback_positive",
        "feedback_neutral",
        "feedback_negative",
    } and not clean_outcome_ids:
        raise ValueError(f"{clean_type} requires at least one outcome_id")
    if clean_type.startswith("methodology_") and not str(methodology_id or "").strip():
        raise ValueError(f"{clean_type} requires methodology_id")
    clean_idempotency = str(idempotency_key or "").strip()
    event_id = (
        _stable_id("value", project_key, clean_idempotency)
        if clean_idempotency
        else f"value_{uuid.uuid4().hex}"
    )
    with _VALUE_LOCK:
        ledger = _load_ledger(project_key)
        existing = next(
            (
                event
                for event in ledger.get("events") or []
                if event.get("value_event_id") == event_id
            ),
            None,
        )
        if existing:
            chain = get_project_value_chain(project_path)
            return {"event": existing, "chain": chain, "deduplicated": True}
        event = {
            "value_event_id": event_id,
            "event_type": clean_type,
            "project_key": project_key,
            "session_id": session_id,
            "related_session_id": str(related_session_id or "").strip(),
            "outcome_ids": clean_outcome_ids,
            "output_key": str(output_key or "").strip(),
            "channel": str(channel or "").strip(),
            "note": str(note or "").strip()[:2000],
            "impact_value": str(impact_value or "").strip()[:2000],
            "methodology_id": str(methodology_id or "").strip(),
            "evidence_id": str(evidence_id or "").strip(),
            "idempotency_key": clean_idempotency,
            "recorded_at": _now_iso(),
        }
        ledger.setdefault("events", []).append(event)
        _save_ledger(ledger)
        chain = get_project_value_chain(project_path)
        return {"event": event, "chain": chain, "deduplicated": False}
