"""Grow reusable playbooks from repeated execution experience.

This module is the bridge between append-only raw evidence and executable
long-term know-how. It turns structured failure learning records into Markdown
playbooks plus indexes that the Memory Growth recall layer can retrieve later.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from .memory_growth import ensure_memory_growth_scaffold, memory_growth_dir

INDEX_NAME = "learned_playbooks.json"
SUCCESS_INDEX_NAME = "learned_success_playbooks.json"


def build_experience_playbooks(
    *,
    date_iso: str,
    raw_events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build/update learned recovery playbooks from raw events.

    The operation is idempotent per raw event id. Rerunning the same daily
    review will not inflate support counts.
    """

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    failure_growth = _build_failure_playbooks(root=root, date_iso=date_iso, raw_events=raw_events)
    success_growth = _build_success_playbooks(root=root, date_iso=date_iso, raw_events=raw_events)
    return {
        **failure_growth,
        "success_created_count": success_growth["created_count"],
        "success_updated_count": success_growth["updated_count"],
        "success_changed_count": success_growth["changed_count"],
        "success_index_path": success_growth["index_path"],
        "success_playbooks": success_growth["playbooks"],
    }


def _build_failure_playbooks(
    *,
    root: Path,
    date_iso: str,
    raw_events: list[dict[str, Any]],
) -> dict[str, Any]:
    learned_dir = root / "playbooks" / "learned"
    learned_dir.mkdir(parents=True, exist_ok=True)

    existing = _load_index(root / "indexes" / INDEX_NAME)
    entries_by_slug = {str(row.get("slug") or ""): dict(row) for row in existing.get("playbooks", []) if row.get("slug")}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in raw_events:
        record = _failure_learning_record(event)
        if not record:
            continue
        slug = _playbook_slug(record)
        grouped.setdefault(slug, []).append({"event": event, "record": record})

    created = 0
    updated = 0
    changed_entries: list[dict[str, Any]] = []

    for slug, rows in sorted(grouped.items()):
        current = entries_by_slug.get(slug) or {}
        known_ids = set(str(item) for item in current.get("source_event_ids") or [])
        new_rows = [row for row in rows if _raw_event_id(row["event"]) not in known_ids]
        if not new_rows and current:
            continue

        all_ids = sorted({*known_ids, *(_raw_event_id(row["event"]) for row in rows)})
        representative = rows[-1]["record"]
        source_refs = _source_refs(rows)
        now_ms = int(time.time() * 1000)
        first_seen = int(current.get("first_seen_at_ms") or now_ms)
        confidence = min(0.9, 0.58 + min(len(all_ids), 6) * 0.05)
        rel_path = f"playbooks/learned/{slug}.md"
        path = root / rel_path
        entry = {
            "slug": slug,
            "path": rel_path,
            "id": f"playbook:learned:{slug}",
            "type": "failure_playbook",
            "task_type": str(representative.get("task_type") or ""),
            "tool": str(representative.get("tool") or ""),
            "role_agent": str(representative.get("role_agent") or ""),
            "failure_class": str(representative.get("failure_class") or "unknown"),
            "next_strategy": str(representative.get("next_strategy") or ""),
            "source_event_count": len(all_ids),
            "source_event_ids": all_ids,
            "first_seen_at_ms": first_seen,
            "last_seen_at_ms": now_ms,
            "last_review_date": date_iso,
            "confidence": round(confidence, 3),
        }
        path.write_text(_render_playbook(entry, representative, source_refs), encoding="utf-8")
        if current:
            updated += 1
        else:
            created += 1
        entries_by_slug[slug] = entry
        changed_entries.append(entry)

    merged_entries = sorted(entries_by_slug.values(), key=lambda row: (-int(row.get("source_event_count") or 0), str(row.get("slug") or "")))
    learned_index = {
        "schema_version": 1,
        "updated_at_ms": int(time.time() * 1000),
        "date": date_iso,
        "playbooks": merged_entries,
    }
    indexes = root / "indexes"
    indexes.mkdir(parents=True, exist_ok=True)
    (indexes / INDEX_NAME).write_text(json.dumps(learned_index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _merge_playbook_index(root, merged_entries, learned_folder="playbooks/learned/", learned_type="failure_playbook")
    return {
        "schema_version": 1,
        "date": date_iso,
        "created_count": created,
        "updated_count": updated,
        "changed_count": len(changed_entries),
        "index_path": str(indexes / INDEX_NAME),
        "playbooks": [
            {
                "slug": row["slug"],
                "path": row["path"],
                "task_type": row.get("task_type") or "",
                "tool": row.get("tool") or "",
                "failure_class": row.get("failure_class") or "",
                "next_strategy": row.get("next_strategy") or "",
                "source_event_count": row.get("source_event_count") or 0,
                "confidence": row.get("confidence") or 0.0,
            }
            for row in changed_entries
        ],
    }


def _build_success_playbooks(
    *,
    root: Path,
    date_iso: str,
    raw_events: list[dict[str, Any]],
) -> dict[str, Any]:
    learned_dir = root / "playbooks" / "learned_success"
    learned_dir.mkdir(parents=True, exist_ok=True)

    existing = _load_index(root / "indexes" / SUCCESS_INDEX_NAME)
    entries_by_slug = {str(row.get("slug") or ""): dict(row) for row in existing.get("playbooks", []) if row.get("slug")}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in raw_events:
        record = _success_execution_record(event)
        if not record:
            continue
        slug = _success_playbook_slug(record)
        grouped.setdefault(slug, []).append({"event": event, "record": record})

    created = 0
    updated = 0
    changed_entries: list[dict[str, Any]] = []

    for slug, rows in sorted(grouped.items()):
        current = entries_by_slug.get(slug) or {}
        known_ids = set(str(item) for item in current.get("source_event_ids") or [])
        new_rows = [row for row in rows if _raw_event_id(row["event"]) not in known_ids]
        if not new_rows and current:
            continue

        all_ids = sorted({*known_ids, *(_raw_event_id(row["event"]) for row in rows)})
        representative = rows[-1]["record"]
        source_refs = _source_refs(rows)
        now_ms = int(time.time() * 1000)
        first_seen = int(current.get("first_seen_at_ms") or now_ms)
        confidence = min(0.93, 0.6 + min(len(all_ids), 8) * 0.04)
        rel_path = f"playbooks/learned_success/{slug}.md"
        path = root / rel_path
        entry = {
            "slug": slug,
            "path": rel_path,
            "id": f"playbook:success:{slug}",
            "type": "success_playbook",
            "task_type": str(representative.get("task_type") or ""),
            "tool": str(representative.get("primary_tool") or ""),
            "role_agent": str(representative.get("role_agent") or ""),
            "success_strategy": str(representative.get("success_strategy") or ""),
            "work_order_chain": list(representative.get("work_order_chain") or []),
            "source_event_count": len(all_ids),
            "source_event_ids": all_ids,
            "first_seen_at_ms": first_seen,
            "last_seen_at_ms": now_ms,
            "last_review_date": date_iso,
            "confidence": round(confidence, 3),
        }
        path.write_text(_render_success_playbook(entry, representative, source_refs), encoding="utf-8")
        if current:
            updated += 1
        else:
            created += 1
        entries_by_slug[slug] = entry
        changed_entries.append(entry)

    merged_entries = sorted(entries_by_slug.values(), key=lambda row: (-int(row.get("source_event_count") or 0), str(row.get("slug") or "")))
    learned_index = {
        "schema_version": 1,
        "updated_at_ms": int(time.time() * 1000),
        "date": date_iso,
        "playbooks": merged_entries,
    }
    indexes = root / "indexes"
    indexes.mkdir(parents=True, exist_ok=True)
    (indexes / SUCCESS_INDEX_NAME).write_text(json.dumps(learned_index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _merge_playbook_index(root, merged_entries, learned_folder="playbooks/learned_success/", learned_type="success_playbook")
    return {
        "schema_version": 1,
        "date": date_iso,
        "created_count": created,
        "updated_count": updated,
        "changed_count": len(changed_entries),
        "index_path": str(indexes / SUCCESS_INDEX_NAME),
        "playbooks": [
            {
                "slug": row["slug"],
                "path": row["path"],
                "task_type": row.get("task_type") or "",
                "tool": row.get("tool") or "",
                "success_strategy": row.get("success_strategy") or "",
                "source_event_count": row.get("source_event_count") or 0,
                "confidence": row.get("confidence") or 0.0,
            }
            for row in changed_entries
        ],
    }


def _failure_learning_record(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    candidates = [
        payload.get("failure_learning"),
        payload.get("record"),
        payload.get("failure"),
        payload,
    ]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        failure_class = str(candidate.get("failure_class") or "")
        next_strategy = str(candidate.get("next_strategy") or "")
        if failure_class or next_strategy or candidate.get("failure_reason"):
            return {
                "failure_id": str(candidate.get("failure_id") or _raw_event_id(event)),
                "task_type": str(candidate.get("task_type") or payload.get("task_type") or ""),
                "tool": str(candidate.get("tool") or payload.get("tool") or ""),
                "role_agent": str(candidate.get("role_agent") or payload.get("role_agent") or ""),
                "failure_reason": str(candidate.get("failure_reason") or "unknown_failure"),
                "failure_class": failure_class or "unknown",
                "next_strategy": next_strategy or "inspect_evidence_then_retry_once",
                "attempt_count": int(_safe_int(candidate.get("attempt_count"), 1)),
                "rationale": candidate.get("rationale") if isinstance(candidate.get("rationale"), list) else [],
            }
    return {}


def _success_execution_record(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    closure = payload.get("closure") if isinstance(payload.get("closure"), dict) else {}
    if not closure:
        candidate = payload.get("turn_closure") if isinstance(payload.get("turn_closure"), dict) else {}
        closure = candidate
    if str(closure.get("verification_status") or "").lower() != "passed":
        return {}
    work_orders = [str(item).strip() for item in closure.get("executed_work_orders") or [] if str(item).strip()]
    if not work_orders:
        return {}
    final_intent = str(closure.get("final_user_message_intent") or payload.get("final_user_message_intent") or "")
    task_type = _infer_success_task_type(final_intent, work_orders)
    primary_tool = _infer_success_tool(final_intent, work_orders)
    role_agent = _infer_success_role(task_type, primary_tool)
    return {
        "turn_id": str(closure.get("turn_id") or payload.get("turn_id") or _raw_event_id(event)),
        "task_type": task_type,
        "primary_tool": primary_tool,
        "role_agent": role_agent,
        "final_user_message_intent": final_intent[:500],
        "work_order_chain": work_orders,
        "success_strategy": _success_strategy(task_type=task_type, primary_tool=primary_tool, work_orders=work_orders),
        "verification_status": "passed",
    }


def _playbook_slug(record: dict[str, Any]) -> str:
    parts = [
        "failure",
        record.get("task_type") or "task",
        record.get("tool") or "tool",
        record.get("failure_class") or "unknown",
    ]
    raw = "-".join(str(part) for part in parts)
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-").lower()
    digest = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"{clean[:72]}-{digest}"


def _success_playbook_slug(record: dict[str, Any]) -> str:
    parts = [
        "success",
        record.get("task_type") or "task",
        record.get("primary_tool") or "tool",
        "-".join(record.get("work_order_chain") or [])[:80],
    ]
    raw = "-".join(str(part) for part in parts)
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-").lower()
    digest = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"{clean[:72]}-{digest}"


def _render_playbook(entry: dict[str, Any], record: dict[str, Any], source_refs: list[dict[str, Any]]) -> str:
    title = f"Learned Recovery Playbook: {entry['failure_class']}"
    source_ref_json = json.dumps(source_refs[:8], ensure_ascii=False)
    rationale = record.get("rationale") if isinstance(record.get("rationale"), list) else []
    rationale_lines = "\n".join(f"- {item}" for item in rationale) if rationale else "- No rationale recorded."
    return (
        "---\n"
        f"id: {json.dumps(entry['id'], ensure_ascii=False)}\n"
        'type: "failure_playbook"\n'
        f"summary: {json.dumps(title, ensure_ascii=False)}\n"
        f"task_type: {json.dumps(entry['task_type'], ensure_ascii=False)}\n"
        f"tool: {json.dumps(entry['tool'], ensure_ascii=False)}\n"
        f"role_agent: {json.dumps(entry['role_agent'], ensure_ascii=False)}\n"
        f"failure_class: {json.dumps(entry['failure_class'], ensure_ascii=False)}\n"
        f"next_strategy: {json.dumps(entry['next_strategy'], ensure_ascii=False)}\n"
        f"source_event_count: {entry['source_event_count']}\n"
        f"confidence: {entry['confidence']}\n"
        f"last_review_date: {json.dumps(entry['last_review_date'], ensure_ascii=False)}\n"
        f"source_refs: {json.dumps(source_refs[:8], ensure_ascii=False)}\n"
        "---\n\n"
        f"# {title}\n\n"
        "## Trigger\n\n"
        f"- Task type: `{entry['task_type'] or 'unknown'}`\n"
        f"- Tool/capability: `{entry['tool'] or 'unknown'}`\n"
        f"- Role agent: `{entry['role_agent'] or 'unknown'}`\n"
        f"- Failure class: `{entry['failure_class']}`\n"
        f"- Last failure reason: {record.get('failure_reason') or 'unknown'}\n\n"
        "## Learned Strategy\n\n"
        f"Use `{entry['next_strategy']}` before repeating the same path. The next attempt should absorb the previous failure reason and either repair inputs, switch the target/tool path, collect missing evidence, or ask the user a single blocking question.\n\n"
        "## Verification Required\n\n"
        "- The retry must produce a VerificationReport with concrete evidence.\n"
        "- If verification is still missing, do not claim success.\n"
        "- After repeated failure, return a concise final report with attempted paths and the next recommended human action.\n\n"
        "## Rationale\n\n"
        f"{rationale_lines}\n\n"
        "## Evidence\n\n"
        f"```json\n{source_ref_json}\n```\n"
    )


def _render_success_playbook(entry: dict[str, Any], record: dict[str, Any], source_refs: list[dict[str, Any]]) -> str:
    title = f"Learned Success Playbook: {entry['task_type'] or 'task'}"
    source_ref_json = json.dumps(source_refs[:8], ensure_ascii=False)
    work_order_json = json.dumps(entry.get("work_order_chain") or [], ensure_ascii=False)
    return (
        "---\n"
        f"id: {json.dumps(entry['id'], ensure_ascii=False)}\n"
        'type: "success_playbook"\n'
        f"summary: {json.dumps(title, ensure_ascii=False)}\n"
        f"task_type: {json.dumps(entry['task_type'], ensure_ascii=False)}\n"
        f"tool: {json.dumps(entry['tool'], ensure_ascii=False)}\n"
        f"role_agent: {json.dumps(entry['role_agent'], ensure_ascii=False)}\n"
        f"success_strategy: {json.dumps(entry['success_strategy'], ensure_ascii=False)}\n"
        f"work_order_chain: {json.dumps(entry['work_order_chain'], ensure_ascii=False)}\n"
        f"source_event_count: {entry['source_event_count']}\n"
        f"confidence: {entry['confidence']}\n"
        f"last_review_date: {json.dumps(entry['last_review_date'], ensure_ascii=False)}\n"
        f"source_refs: {json.dumps(source_refs[:8], ensure_ascii=False)}\n"
        "---\n\n"
        f"# {title}\n\n"
        "## Trigger\n\n"
        f"- Task type: `{entry['task_type'] or 'unknown'}`\n"
        f"- Primary tool/capability: `{entry['tool'] or 'unknown'}`\n"
        f"- Role agent: `{entry['role_agent'] or 'unknown'}`\n"
        f"- User-facing intent: {record.get('final_user_message_intent') or 'unknown'}\n\n"
        "## Recommended Flow\n\n"
        f"Prefer `{entry['success_strategy']}` when the current goal, target, and verification criteria match this pattern.\n\n"
        "## WorkOrder Chain\n\n"
        f"```json\n{work_order_json}\n```\n\n"
        "## Verification Criteria\n\n"
        "- Only reuse this path when the previous run had `verification_status=passed`.\n"
        "- Do not claim success unless the current run produces fresh VerificationReport evidence.\n"
        "- If the same path fails, hand the failure reason to RecoveryPlanner instead of repeating silently.\n\n"
        "## Evidence\n\n"
        f"```json\n{source_ref_json}\n```\n"
    )


def _merge_playbook_index(root: Path, learned_entries: list[dict[str, Any]], *, learned_folder: str, learned_type: str) -> None:
    path = root / "indexes" / "playbooks.json"
    existing = _load_index(path)
    rows = existing.get("playbooks") if isinstance(existing.get("playbooks"), list) else []
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        rel = str(row.get("path") or "")
        if rel.startswith(learned_folder):
            continue
        key = rel or str(row.get("slug") or row.get("id") or "")
        if key:
            merged[key] = dict(row)
    for row in learned_entries:
        rel = str(row.get("path") or "")
        merged[rel] = {
            "path": rel,
            "slug": row.get("slug") or Path(rel).stem,
            "type": learned_type,
            "task_type": row.get("task_type") or "",
            "tool": row.get("tool") or "",
            "role_agent": row.get("role_agent") or "",
            "failure_class": row.get("failure_class") or "",
            "next_strategy": row.get("next_strategy") or "",
            "success_strategy": row.get("success_strategy") or "",
            "work_order_chain": row.get("work_order_chain") or [],
            "confidence": row.get("confidence") or 0.0,
        }
    out = {
        "schema_version": 1,
        "updated_at_ms": int(time.time() * 1000),
        "playbooks": sorted(merged.values(), key=lambda row: str(row.get("path") or "")),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _infer_success_task_type(final_intent: str, work_orders: list[str]) -> str:
    text = f"{final_intent} {' '.join(work_orders)}".lower()
    if any(token in text for token in ("lark", "message", "send", "neil", "vivian", "samuel", "飞书", "发送")):
        return "message_delivery"
    if any(token in text for token in ("calculator", "calculate", "计算器", "计算")):
        return "calculator_calculate"
    if any(token in text for token in ("file", "open", "reveal", "文件", "打开", "所在位置")):
        return "file_operation"
    if any(token in text for token in ("app", "window", "browser", "wechat", "lark", "应用", "窗口")):
        return "app_control"
    return "general_task"


def _infer_success_tool(final_intent: str, work_orders: list[str]) -> str:
    text = f"{final_intent} {' '.join(work_orders)}".lower()
    if "lark" in text or "飞书" in text:
        return "mcp:windows_lark_send_message"
    if "calculator" in text or "计算器" in text:
        return "mcp:windows_calculator_calculate"
    if "reveal" in text or "所在位置" in text:
        return "mcp:windows_file_reveal_in_explorer"
    if "file" in text or "文件" in text:
        return "core:fs_read"
    if "open" in text or "打开" in text:
        return "mcp:windows_open_app"
    return ""


def _infer_success_role(task_type: str, primary_tool: str) -> str:
    if task_type == "message_delivery" or "lark" in primary_tool:
        return "MessageExecutorAgent"
    if task_type in {"app_control", "calculator_calculate"} or "windows_open_app" in primary_tool:
        return "AppControlExecutorAgent"
    if task_type == "file_operation" or "file" in primary_tool or "fs_" in primary_tool:
        return "FileExecutorAgent"
    return "ToolExecutionAgent"


def _success_strategy(*, task_type: str, primary_tool: str, work_orders: list[str]) -> str:
    if task_type == "message_delivery":
        return "reuse_verified_message_delivery_chain"
    if task_type == "calculator_calculate":
        return "reuse_verified_calculator_visual_chain"
    if task_type == "file_operation":
        return "reuse_verified_file_operation_chain"
    if task_type == "app_control":
        return "reuse_verified_app_control_chain"
    if primary_tool:
        return "reuse_verified_capability_chain"
    if len(work_orders) > 1:
        return "reuse_verified_work_order_dag"
    return "reuse_verified_single_step"


def _load_index(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _source_refs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        event = row["event"]
        raw_ref = {
            "type": "raw_event",
            "event_id": _raw_event_id(event),
            "path": event.get("_raw_path"),
            "line_no": event.get("_raw_line_no"),
        }
        for ref in [raw_ref, *(event.get("source_refs") or [])]:
            if not isinstance(ref, dict):
                continue
            key = json.dumps(ref, ensure_ascii=False, sort_keys=True, default=str)
            if key in seen:
                continue
            seen.add(key)
            refs.append(ref)
    return refs


def _raw_event_id(event: dict[str, Any]) -> str:
    event_id = str(event.get("event_id") or "")
    if event_id:
        return event_id
    return f"{event.get('_raw_path') or 'raw'}:{event.get('_raw_line_no') or 0}"


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default
