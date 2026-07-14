"""Daily digestion for the AI self-growing knowledge system.

The daily review layer is intentionally conservative. It reads append-only raw
events and produces review patches, but it does not mutate concepts/playbooks
directly. Later curator agents can accept, merge, reject, or ask for human
confirmation.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .memory_growth import ensure_memory_growth_scaffold, memory_growth_dir


@dataclass(slots=True)
class DailyReviewResult:
    review_id: str
    date: str
    raw_event_count: int
    task_count: int
    passed_count: int
    failed_count: int
    waiting_user_count: int
    concept_candidate_count: int
    playbook_candidate_count: int
    output_candidate_count: int
    review_path: Path
    patch_path: Path
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "date": self.date,
            "raw_event_count": self.raw_event_count,
            "task_count": self.task_count,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "waiting_user_count": self.waiting_user_count,
            "concept_candidate_count": self.concept_candidate_count,
            "playbook_candidate_count": self.playbook_candidate_count,
            "output_candidate_count": self.output_candidate_count,
            "review_path": str(self.review_path),
            "patch_path": str(self.patch_path),
            "warnings": list(self.warnings),
        }


def run_daily_review(date: str | None = None) -> DailyReviewResult:
    """Generate a daily review and patch proposal from raw evidence."""

    ensure_memory_growth_scaffold()
    date_iso = _normalize_date(date)
    raw_events = _load_raw_events_for_day(date_iso)
    grouped = _group_by_turn(raw_events)
    patch = _build_patch(date_iso=date_iso, raw_events=raw_events, grouped=grouped)

    reviews_dir = memory_growth_dir() / "reviews"
    patches_dir = reviews_dir / "patches"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    patches_dir.mkdir(parents=True, exist_ok=True)

    review_path = reviews_dir / f"{date_iso}.md"
    patch_path = patches_dir / f"{date_iso}.daily_review.patch.json"
    review_path.write_text(_render_review_markdown(patch), encoding="utf-8")
    patch_path.write_text(json.dumps(patch, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    summary = patch["summary"]
    return DailyReviewResult(
        review_id=patch["review_id"],
        date=date_iso,
        raw_event_count=summary["raw_event_count"],
        task_count=summary["task_count"],
        passed_count=summary["passed_count"],
        failed_count=summary["failed_count"],
        waiting_user_count=summary["waiting_user_count"],
        concept_candidate_count=len(patch["concept_candidates"]),
        playbook_candidate_count=len(patch["playbook_candidates"]),
        output_candidate_count=len(patch["output_candidates"]),
        review_path=review_path,
        patch_path=patch_path,
        warnings=patch["warnings"],
    )


def _load_raw_events_for_day(date_iso: str) -> list[dict[str, Any]]:
    yyyymmdd = date_iso.replace("-", "")
    raw_root = memory_growth_dir() / "raw"
    events: list[dict[str, Any]] = []
    if not raw_root.exists():
        return events
    for path in sorted(raw_root.glob(f"*/*{yyyymmdd}*.jsonl")):
        events.extend(_read_jsonl(path))
    for path in sorted(raw_root.glob(f"*/{yyyymmdd}.*.jsonl")):
        events.extend(_read_jsonl(path))
    return _dedupe_events(events)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            rows.append(
                {
                    "event_id": f"invalid:{path.name}:{line_no}",
                    "category": "invalid",
                    "source": "jsonl_reader",
                    "payload": {"path": str(path), "line_no": line_no, "raw": line[:500]},
                    "review": {"review_candidate": True, "priority": "high"},
                    "source_refs": [{"type": "raw_jsonl", "path": str(path), "line_no": line_no}],
                    "_invalid": True,
                }
            )
            continue
        row["_raw_path"] = str(path)
        row["_raw_line_no"] = line_no
        rows.append(row)
    return rows


def _dedupe_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for event in events:
        event_id = str(event.get("event_id") or "")
        key = event_id or f"{event.get('_raw_path')}:{event.get('_raw_line_no')}"
        if key in seen:
            continue
        seen.add(key)
        out.append(event)
    return out


def _group_by_turn(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        turn_id = _event_turn_id(event)
        grouped.setdefault(turn_id or "unattributed", []).append(event)
    return grouped


def _build_patch(
    *,
    date_iso: str,
    raw_events: list[dict[str, Any]],
    grouped: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    concept_candidates: list[dict[str, Any]] = []
    playbook_candidates: list[dict[str, Any]] = []
    output_candidates: list[dict[str, Any]] = []
    warnings: list[str] = []

    passed = failed = waiting = 0
    task_summaries: list[dict[str, Any]] = []

    for turn_id, events in sorted(grouped.items()):
        closure_events = [event for event in events if _closure(event)]
        verification_statuses = [_closure(event).get("verification_status", "") for event in closure_events]
        closure_types = [_closure(event).get("closure_type", "") for event in closure_events]
        if any(status == "failed" for status in verification_statuses):
            failed += 1
        elif any(kind == "waiting_user" for kind in closure_types):
            waiting += 1
        elif any(status == "passed" for status in verification_statuses):
            passed += 1

        task_summary = _summarize_task(turn_id, events)
        task_summaries.append(task_summary)
        concept_candidates.extend(_concept_candidates_from_task(task_summary, events))
        playbook_candidates.extend(_playbook_candidates_from_task(task_summary, events))
        output_candidates.extend(_output_candidates_from_task(task_summary, events))

    invalid_count = sum(1 for event in raw_events if event.get("_invalid"))
    if invalid_count:
        warnings.append(f"{invalid_count} invalid raw JSONL lines need repair")

    concept_candidates = _dedupe_candidates(concept_candidates)
    playbook_candidates = _dedupe_candidates(playbook_candidates)
    output_candidates = _dedupe_candidates(output_candidates)

    return {
        "schema_version": 1,
        "review_id": f"daily_{date_iso}_{uuid.uuid4().hex[:8]}",
        "date": date_iso,
        "generated_at_ms": int(time.time() * 1000),
        "source": "daily_review_agent",
        "summary": {
            "raw_event_count": len(raw_events),
            "task_count": len(grouped),
            "passed_count": passed,
            "failed_count": failed,
            "waiting_user_count": waiting,
            "invalid_raw_count": invalid_count,
        },
        "task_summaries": task_summaries,
        "concept_candidates": concept_candidates,
        "playbook_candidates": playbook_candidates,
        "output_candidates": output_candidates,
        "warnings": warnings,
    }


def _summarize_task(turn_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    closures = [_closure(event) for event in events if _closure(event)]
    work_order_ids: list[str] = []
    memory_types: list[str] = []
    verification_status = ""
    closure_type = ""
    pending_user = False
    for closure in closures:
        verification_status = verification_status or str(closure.get("verification_status") or "")
        closure_type = closure_type or str(closure.get("closure_type") or "")
        pending_user = pending_user or bool(closure.get("pending_decision"))
        work_order_ids.extend(str(x) for x in closure.get("executed_work_orders") or [])
        for request in closure.get("memory_write_requests") or []:
            memory_type = str(request.get("memory_type") or "")
            if memory_type:
                memory_types.append(memory_type)
    return {
        "turn_id": turn_id,
        "event_count": len(events),
        "sources": sorted({str(event.get("source") or "unknown") for event in events}),
        "categories": sorted({str(event.get("category") or "unknown") for event in events}),
        "verification_status": verification_status,
        "closure_type": closure_type,
        "pending_user": pending_user,
        "work_order_ids": sorted(set(work_order_ids)),
        "memory_types": sorted(set(memory_types)),
        "source_refs": _collect_source_refs(events),
    }


def _concept_candidates_from_task(task: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for event in events:
        closure = _closure(event)
        for request in closure.get("memory_write_requests") or []:
            content = str(request.get("content") or "").strip()
            if not content:
                continue
            memory_type = str(request.get("memory_type") or "fact").strip() or "fact"
            candidates.append(
                {
                    "candidate_id": f"concept:{task['turn_id']}:{memory_type}:{_stable_suffix(content)}",
                    "target_type": memory_type,
                    "summary": content[:500],
                    "confidence": float(request.get("confidence") or 0.0),
                    "merge_policy": request.get("merge_policy") or "dedupe_and_merge",
                    "requires_user_confirmation": bool(request.get("requires_user_confirmation")),
                    "source_refs": _candidate_refs(task, event, request.get("evidence") or []),
                }
            )
    if task["verification_status"] == "failed":
        candidates.append(
            {
                "candidate_id": f"concept:{task['turn_id']}:problem:{_stable_suffix(task['turn_id'])}",
                "target_type": "problems",
                "summary": f"Task {task['turn_id']} failed and needs failure-pattern review.",
                "confidence": 0.5,
                "merge_policy": "append_conflict_or_problem",
                "requires_user_confirmation": False,
                "source_refs": task["source_refs"],
            }
        )
    return candidates


def _playbook_candidates_from_task(task: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if task["verification_status"] == "passed" and task["work_order_ids"]:
        candidates.append(
            {
                "candidate_id": f"playbook:{task['turn_id']}:success:{_stable_suffix(','.join(task['work_order_ids']))}",
                "title": f"Repeatable flow from {task['turn_id']}",
                "trigger": {
                    "sources": task["sources"],
                    "categories": task["categories"],
                    "memory_types": task["memory_types"],
                },
                "recommended_flow": [
                    {
                        "step": "reuse_work_order_chain",
                        "work_order_ids": task["work_order_ids"],
                        "verification_status": task["verification_status"],
                    }
                ],
                "evidence_required": ["work_order", "verification_report", "turn_closure"],
                "source_refs": task["source_refs"],
                "confidence": 0.55,
            }
        )
    if task["verification_status"] == "failed":
        candidates.append(
            {
                "candidate_id": f"playbook:{task['turn_id']}:recovery:{_stable_suffix(task['turn_id'])}",
                "title": f"Recovery needed for {task['turn_id']}",
                "trigger": {"failure": True, "categories": task["categories"]},
                "recommended_flow": [
                    {"step": "inspect_failure_reason"},
                    {"step": "select_next_capability_path"},
                    {"step": "verify_or_ask_user"},
                ],
                "evidence_required": ["failure_reason", "alternative_path", "final_report"],
                "source_refs": task["source_refs"],
                "confidence": 0.5,
            }
        )
    return candidates


def _output_candidates_from_task(task: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for event in events:
        review = event.get("review") or {}
        targets = review.get("promotion_targets") or []
        if "outputs" not in targets:
            continue
        closure = _closure(event)
        final_text = str(closure.get("final_user_message_intent") or "").strip()
        verification_status = task.get("verification_status") or ""
        candidates.append(
            {
                "candidate_id": f"output:{task['turn_id']}:{event.get('event_id')}",
                "target_type": _output_category_for_task(task, closure),
                "summary": f"Task {task['turn_id']} has user-facing output evidence.",
                "content": final_text,
                "verification_status": verification_status,
                "closure_type": task.get("closure_type") or "",
                "source_refs": _candidate_refs(task, event, []),
                "confidence": 0.45 if task["verification_status"] != "failed" else 0.25,
            }
        )
    return candidates


def _output_category_for_task(task: dict[str, Any], closure: dict[str, Any]) -> str:
    text = " ".join(
        [
            str(task.get("turn_id") or ""),
            str(task.get("closure_type") or ""),
            str(task.get("verification_status") or ""),
            str(closure.get("final_user_message_intent") or ""),
        ]
    ).lower()
    if "lark" in text or "message" in text or "发送" in text:
        return "lark_messages"
    if "report" in text or "briefing" in text or "简报" in text or "报告" in text:
        return "reports"
    if task.get("verification_status") == "failed":
        return "debug_summaries"
    return "work_records"


def _render_review_markdown(patch: dict[str, Any]) -> str:
    summary = patch["summary"]
    lines = [
        f"# Daily Review {patch['date']}",
        "",
        "## Summary",
        "",
        f"- Raw events: {summary['raw_event_count']}",
        f"- Tasks: {summary['task_count']}",
        f"- Passed: {summary['passed_count']}",
        f"- Failed: {summary['failed_count']}",
        f"- Waiting user: {summary['waiting_user_count']}",
        f"- Concept candidates: {len(patch['concept_candidates'])}",
        f"- Playbook candidates: {len(patch['playbook_candidates'])}",
        f"- Output candidates: {len(patch['output_candidates'])}",
        "",
        "## Tasks",
        "",
    ]
    for task in patch["task_summaries"]:
        lines.extend(
            [
                f"### {task['turn_id']}",
                "",
                f"- events: {task['event_count']}",
                f"- verification: {task['verification_status'] or 'unknown'}",
                f"- closure: {task['closure_type'] or 'unknown'}",
                f"- work_orders: {', '.join(task['work_order_ids']) if task['work_order_ids'] else '-'}",
                "",
            ]
        )
    if patch["warnings"]:
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {warning}" for warning in patch["warnings"])
        lines.append("")
    lines.extend(
        [
            "## Patch File",
            "",
            f"`reviews/patches/{patch['date']}.daily_review.patch.json`",
            "",
        ]
    )
    return "\n".join(lines)


def _closure(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") or {}
    closure = payload.get("closure") or {}
    return closure if isinstance(closure, dict) else {}


def _event_turn_id(event: dict[str, Any]) -> str:
    payload = event.get("payload") or {}
    closure = payload.get("closure") or {}
    if isinstance(payload, dict) and payload.get("turn_id"):
        return str(payload.get("turn_id"))
    if isinstance(closure, dict) and closure.get("turn_id"):
        return str(closure.get("turn_id"))
    for ref in event.get("source_refs") or []:
        if isinstance(ref, dict) and ref.get("turn_id"):
            return str(ref.get("turn_id"))
    return ""


def _collect_source_refs(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for event in events:
        refs.append(
            {
                "type": "raw_event",
                "event_id": event.get("event_id"),
                "path": event.get("_raw_path"),
                "line_no": event.get("_raw_line_no"),
            }
        )
        refs.extend(ref for ref in event.get("source_refs") or [] if isinstance(ref, dict))
    return _dedupe_refs(refs)


def _candidate_refs(task: dict[str, Any], event: dict[str, Any], extra_refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs = [
        {
            "type": "raw_event",
            "event_id": event.get("event_id"),
            "turn_id": task["turn_id"],
            "path": event.get("_raw_path"),
            "line_no": event.get("_raw_line_no"),
        }
    ]
    refs.extend(ref for ref in extra_refs if isinstance(ref, dict))
    return _dedupe_refs(refs)


def _dedupe_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for ref in refs:
        key = json.dumps(ref, sort_keys=True, ensure_ascii=False, default=str)
        if key in seen:
            continue
        seen.add(key)
        out.append(ref)
    return out


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for candidate in candidates:
        key = str(candidate.get("candidate_id") or json.dumps(candidate, sort_keys=True, ensure_ascii=False))
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out


def _stable_suffix(text: str) -> str:
    import hashlib

    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:10]


def _normalize_date(date: str | None) -> str:
    if not date:
        return time.strftime("%Y-%m-%d")
    clean = str(date).strip()
    if len(clean) == 8 and clean.isdigit():
        return f"{clean[:4]}-{clean[4:6]}-{clean[6:]}"
    return clean
