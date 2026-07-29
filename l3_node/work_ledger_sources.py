"""Privacy-aware source adapters and the daily process inbox for Work Ledger."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


SUPPORTED_EXTENSIONS = {".jsonl", ".log", ".md", ".out", ".err", ".txt"}
SOURCE_HINTS = {
    "codex": ("codex",),
    "cursor": ("cursor",),
    "terminal": ("terminal", "powershell", "console", "pytest", "cargo", "npm"),
    "document": ("report", "daily", "weekly", "brief", "context", "notes", "document"),
}
STOP_TOKENS = {
    "the", "and", "for", "from", "with", "this", "that", "into", "then", "today",
    "codex", "cursor", "terminal", "powershell", "console", "work", "task", "file",
    "完成", "修改", "新增", "修复", "已经", "今天", "任务", "工作", "项目", "文件",
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        pass
    return default


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())[:120] or "unknown"


def _content_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _privacy_redact(text: str) -> str:
    """Apply source-layer redaction even when the general safety scanner misses a format."""

    clean = str(text or "")
    patterns = (
        (r"\bsk-[A-Za-z0-9_-]{12,}\b", "[REDACTED_API_KEY]"),
        (r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", "Bearer [REDACTED_TOKEN]"),
        (
            r"(?i)\b(api[_ -]?key|access[_ -]?token|secret|password)\b\s*[:=]\s*['\"]?[^\s,'\";]{8,}",
            r"\1: [REDACTED]",
        ),
    )
    for pattern, replacement in patterns:
        clean = re.sub(pattern, replacement, clean)
    return clean


def _tokens(text: str) -> set[str]:
    values = re.findall(r"[A-Za-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}", str(text or "").lower())
    normalized = {value.strip("._-") for value in values}
    return {value for value in normalized if value and value not in STOP_TOKENS and len(value) <= 80}


def _similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, len(left | right))


@dataclass(frozen=True)
class WorkSourceRecord:
    source_type: str
    source_id: str
    source_uri: str
    text: str
    mtime_ms: int
    metadata: dict[str, Any]


class WorkSourceAdapter(Protocol):
    """Contract implemented by every local AI-work source adapter."""

    source_type: str

    def supports(self, path: Path, preview: str) -> bool: ...

    def read(self, path: Path, *, start_offset: int = 0, max_chars: int = 30000) -> WorkSourceRecord: ...


class LocalTextSourceAdapter:
    def __init__(self, source_type: str, hints: tuple[str, ...]) -> None:
        self.source_type = source_type
        self.hints = hints

    def supports(self, path: Path, preview: str) -> bool:
        haystack = f"{path}\n{preview[:2000]}".lower()
        return any(hint in haystack for hint in self.hints)

    def read(self, path: Path, *, start_offset: int = 0, max_chars: int = 30000) -> WorkSourceRecord:
        raw_bytes = path.read_bytes()
        safe_offset = max(0, min(int(start_offset or 0), len(raw_bytes)))
        delta_bytes = raw_bytes[safe_offset:]
        raw = delta_bytes.decode("utf-8-sig" if safe_offset == 0 else "utf-8", errors="replace")
        text = raw[-max_chars:]
        stat = path.stat()
        return WorkSourceRecord(
            source_type=self.source_type,
            source_id=_content_hash(f"{path.resolve()}:{safe_offset}:{stat.st_size}:{stat.st_mtime_ns}"),
            source_uri=str(path.resolve()),
            text=text,
            mtime_ms=int(stat.st_mtime * 1000),
            metadata={
                "file_name": path.name,
                "size": stat.st_size,
                "start_offset": safe_offset,
                "end_offset": len(raw_bytes),
                "delta_bytes": len(delta_bytes),
                "delta_line_count": len(raw.splitlines()),
                "tail_clipped": len(raw) > max_chars,
            },
        )


class GenericDocumentAdapter(LocalTextSourceAdapter):
    def __init__(self) -> None:
        super().__init__("document", ())

    def supports(self, path: Path, preview: str) -> bool:
        return path.suffix.lower() in SUPPORTED_EXTENSIONS


ADAPTERS: tuple[WorkSourceAdapter, ...] = (
    LocalTextSourceAdapter("codex", SOURCE_HINTS["codex"]),
    LocalTextSourceAdapter("cursor", SOURCE_HINTS["cursor"]),
    LocalTextSourceAdapter("terminal", SOURCE_HINTS["terminal"]),
    LocalTextSourceAdapter("document", SOURCE_HINTS["document"]),
    GenericDocumentAdapter(),
)
_SOURCE_REFRESH_LOCKS: dict[str, threading.RLock] = {}
_SOURCE_REFRESH_LOCKS_GUARD = threading.Lock()


def _inbox_path(session_id: str) -> Path:
    from l3_node.work_ledger import work_ledger_home

    return work_ledger_home() / "inbox" / f"{_safe_id(session_id)}.json"


def _cursor_path(session_id: str) -> Path:
    from l3_node.work_ledger import work_ledger_home

    return work_ledger_home() / "source_cursors" / f"{_safe_id(session_id)}.json"


def _source_key(source_uri: str) -> str:
    return f"source_{_content_hash(str(source_uri or '').lower())[:20]}"


def _empty_cursor_state(session_id: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "session_id": session_id,
        "configured_roots": [],
        "source_profile_initialized": False,
        "source_profile": {},
        "sources": {},
        "health": {
            "sync_count": 0,
            "changed_sync_count": 0,
            "unchanged_sync_count": 0,
            "failed_source_count": 0,
            "total_duration_ms": 0,
            "total_bytes": 0,
            "total_chars": 0,
            "total_lines": 0,
            "total_candidates": 0,
            "total_events": 0,
        },
        "updated_at": "",
        "last_refresh": {},
    }


def _load_cursor_state(session_id: str) -> dict[str, Any]:
    state = _read_json(_cursor_path(session_id), {})
    if not isinstance(state, dict) or state.get("session_id") != session_id:
        state = _empty_cursor_state(session_id)
    if not isinstance(state.get("sources"), dict):
        state["sources"] = {}
    if not isinstance(state.get("configured_roots"), list):
        state["configured_roots"] = []
    if not isinstance(state.get("health"), dict):
        state["health"] = _empty_cursor_state(session_id)["health"]
    if not state.get("source_profile_initialized"):
        _inherit_project_source_profile(session_id, state)
    return state


def _save_cursor_state(session_id: str, state: dict[str, Any]) -> None:
    state["schema_version"] = 1
    state["session_id"] = session_id
    state["updated_at"] = _now_iso()
    _write_json(_cursor_path(session_id), state)
    _persist_project_source_cursors(session_id, state)


def _inherit_project_source_profile(session_id: str, state: dict[str, Any]) -> None:
    state["source_profile_initialized"] = True
    try:
        from l3_node.work_ledger_project_memory import get_project_source_profile

        session = _load_session(session_id)
        project_path = str(session.get("project_path") or "").strip()
        profile = get_project_source_profile(project_path)
        if not profile:
            return
        roots = [str(item) for item in profile.get("roots") or [] if str(item).strip()]
        state["configured_roots"] = roots
        state["source_profile"] = {
            "project_key": profile.get("project_key"),
            "project_path": profile.get("project_path"),
            "inherited": True,
            "inherited_from_session_id": profile.get("last_session_id") or "",
            "profile_updated_at_ms": int(profile.get("updated_at_ms") or 0),
        }
        for source_key, raw in (profile.get("source_cursors") or {}).items():
            if not isinstance(raw, dict):
                continue
            state["sources"][str(source_key)] = {
                **raw,
                "paused": False,
                "status": "inherited",
                "consecutive_errors": 0,
                "backoff_seconds": 0,
                "backoff_until_ms": 0,
                "last_error": "",
            }
    except Exception:
        return


def _persist_project_source_cursors(session_id: str, state: dict[str, Any]) -> None:
    if not state.get("configured_roots"):
        return
    try:
        from l3_node.work_ledger_project_memory import update_project_source_profile_cursors

        session = _load_session(session_id)
        update_project_source_profile_cursors(
            str(session.get("project_path") or ""),
            state.get("sources") if isinstance(state.get("sources"), dict) else {},
            session_id=session_id,
        )
    except Exception:
        return


def _path_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _source_backoff_seconds(consecutive_errors: int) -> int:
    """Return a bounded exponential delay for a repeatedly failing source."""

    errors = max(1, int(consecutive_errors or 1))
    return min(3600, 30 * (2 ** min(errors - 1, 7)))


def _source_is_backing_off(cursor: dict[str, Any], now_ms: int) -> bool:
    return int(cursor.get("backoff_until_ms") or 0) > now_ms


def _mark_source_healthy(cursor: dict[str, Any]) -> None:
    cursor["consecutive_errors"] = 0
    cursor["backoff_until_ms"] = 0
    cursor["backoff_seconds"] = 0
    cursor["last_error"] = ""


def _mark_source_failed(cursor: dict[str, Any], error: Exception, now_ms: int) -> None:
    errors = int(cursor.get("consecutive_errors") or 0) + 1
    delay_seconds = _source_backoff_seconds(errors)
    cursor.update(
        {
            "status": "error",
            "last_error": str(error)[:500],
            "last_sync_at": _now_iso(),
            "consecutive_errors": errors,
            "backoff_seconds": delay_seconds,
            "backoff_until_ms": now_ms + delay_seconds * 1000,
            "total_error_count": int(cursor.get("total_error_count") or 0) + 1,
        }
    )


def _update_source_health(state: dict[str, Any], stats: dict[str, Any]) -> dict[str, Any]:
    defaults = _empty_cursor_state(str(state.get("session_id") or ""))["health"]
    health = state.get("health") if isinstance(state.get("health"), dict) else {}
    health = {**defaults, **health}
    changed = int(stats.get("sources_read") or 0) > 0 or int(stats.get("new_event_count") or 0) > 0
    health["sync_count"] = int(health.get("sync_count") or 0) + 1
    health["changed_sync_count"] = int(health.get("changed_sync_count") or 0) + int(changed)
    health["unchanged_sync_count"] = int(health.get("unchanged_sync_count") or 0) + int(not changed)
    health["failed_source_count"] = int(health.get("failed_source_count") or 0) + int(stats.get("sources_failed") or 0)
    health["total_duration_ms"] = int(health.get("total_duration_ms") or 0) + int(stats.get("duration_ms") or 0)
    health["total_bytes"] = int(health.get("total_bytes") or 0) + int(stats.get("new_byte_count") or 0)
    health["total_chars"] = int(health.get("total_chars") or 0) + int(stats.get("new_char_count") or 0)
    health["total_lines"] = int(health.get("total_lines") or 0) + int(stats.get("new_line_count") or 0)
    health["total_candidates"] = int(health.get("total_candidates") or 0) + int(stats.get("new_candidate_count") or 0)
    health["total_events"] = int(health.get("total_events") or 0) + int(stats.get("new_event_count") or 0)
    health["last_sync_at"] = stats.get("completed_at") or _now_iso()
    health["average_duration_ms"] = round(
        int(health.get("total_duration_ms") or 0) / max(1, int(health.get("sync_count") or 0)),
        2,
    )
    attempted_sources = (
        int(health.get("total_candidates") or 0)
        + int(health.get("failed_source_count") or 0)
        + int(health.get("unchanged_sync_count") or 0)
    )
    health["error_rate"] = round(
        int(health.get("failed_source_count") or 0) / max(1, attempted_sources),
        4,
    )
    state["health"] = health
    return health


def _load_session(session_id: str) -> dict[str, Any]:
    from l3_node.work_ledger import get_session_detail

    return get_session_detail(session_id, evidence_limit=10)["session"]


def _allowed_roots(session: dict[str, Any], requested_roots: list[str] | None) -> list[Path]:
    """Resolve only roots explicitly supplied by the user/session/environment."""

    from l3_node.work_ledger import work_ledger_home

    roots: list[Path] = []
    raw_values: list[str] = []
    if requested_roots:
        raw_values.extend(str(item) for item in requested_roots if str(item).strip())
    else:
        project_path = str(session.get("project_path") or "").strip()
        if project_path:
            raw_values.append(project_path)
        raw_values.append(str(work_ledger_home() / "imports"))
        raw_values.extend(
            item for item in (os.environ.get("JACHIN_WORK_SOURCE_ALLOWLIST") or "").split(os.pathsep) if item.strip()
        )
    seen: set[str] = set()
    for raw in raw_values:
        try:
            path = Path(raw).expanduser().resolve()
        except Exception:
            continue
        key = str(path).lower()
        if key in seen or not path.exists():
            continue
        seen.add(key)
        roots.append(path)
    return roots


def _iter_source_files(roots: list[Path], *, max_files: int = 240) -> list[Path]:
    from l3_node.work_ledger import EXCLUDED_DIRS

    rows: list[Path] = []
    for root in roots:
        if root.is_file():
            if root.suffix.lower() in SUPPORTED_EXTENSIONS:
                rows.append(root)
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name not in EXCLUDED_DIRS and not name.startswith(".cache")]
            for name in filenames:
                path = Path(dirpath) / name
                if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    continue
                rows.append(path)
                if len(rows) >= max_files:
                    return rows
    return rows


def _choose_adapter(path: Path, preview: str) -> WorkSourceAdapter:
    file_name = path.name.lower()
    explicit_order = ("terminal", "cursor", "codex", "document")
    for source_type in explicit_order:
        hints = SOURCE_HINTS[source_type]
        if any(hint in file_name for hint in hints):
            return next(adapter for adapter in ADAPTERS if adapter.source_type == source_type)
    for adapter in ADAPTERS:
        if adapter.supports(path, preview):
            return adapter
    return ADAPTERS[-1]


def _task_association_score(session: dict[str, Any], text: str, source_uri: str) -> float:
    task_terms = _tokens(
        " ".join(
            str(session.get(key) or "")
            for key in ("title", "user_goal", "project_name", "project_path")
        )
    )
    source_terms = _tokens(f"{source_uri} {text[:6000]}")
    if not task_terms:
        return 0.5
    return round(min(1.0, len(task_terms & source_terms) / max(1, min(5, len(task_terms)))), 3)


def _classify_terminal_text(text: str) -> dict[str, Any]:
    """Separate useful terminal outcomes from progress noise and heartbeat logs."""

    clean = str(text or "")
    lower = clean.lower()
    lines = [line.strip() for line in clean.splitlines() if line.strip()]
    failure_signals = sum(
        lower.count(token)
        for token in ("traceback", "exception", "error:", "failed", "exit code: 1", "exit code 1", "panic")
    )
    success_signals = sum(
        lower.count(token)
        for token in ("passed", "success", "completed", "finished", "exit code: 0", "exit code 0", "build succeeded")
    )
    noise_lines = sum(
        1
        for line in lines
        if re.search(r"\b(heartbeat|progress|polling|waiting|still running|health check)\b", line, re.I)
        or re.fullmatch(r"[.\-=#\s\[\]0-9%/]+", line)
    )
    noise_ratio = noise_lines / max(1, len(lines))
    if failure_signals and success_signals:
        outcome = "mixed"
    elif failure_signals:
        outcome = "failure"
    elif success_signals:
        outcome = "success"
    elif len(lines) >= 3 and noise_ratio >= 0.7:
        outcome = "noise"
    else:
        outcome = "unknown"
    return {
        "outcome": outcome,
        "failure_signal_count": failure_signals,
        "success_signal_count": success_signals,
        "noise_line_count": noise_lines,
        "line_count": len(lines),
        "noise_ratio": round(noise_ratio, 3),
    }


def _prepare_candidate(session: dict[str, Any], record: WorkSourceRecord) -> dict[str, Any]:
    from l3_node.work_ledger import (
        analyze_ai_trace_text,
        prepare_work_process_import,
        redact_sensitive_material,
        scan_sensitive_material,
    )

    safety = scan_sensitive_material(record.text)
    sanitized = _privacy_redact(redact_sensitive_material(record.text))
    prepared = prepare_work_process_import(
        sanitized,
        source_meta={"type": record.source_type, "file_path": record.source_uri},
    )
    trace_text = str(prepared.get("trace_text") or "")
    analysis = analyze_ai_trace_text(trace_text)
    association = _task_association_score(session, trace_text, record.source_uri)
    signal_count = int(analysis.get("signal_count") or 0)
    score = 35.0 + min(25.0, signal_count * 3.0) + association * 30.0
    content_class = {"outcome": "work_process"}
    if record.source_type == "terminal":
        content_class = _classify_terminal_text(trace_text or sanitized)
        if content_class.get("outcome") == "failure":
            score += 12.0
        elif content_class.get("outcome") == "success":
            score += 8.0
        elif content_class.get("outcome") == "noise":
            score = min(score, 8.0)
    if safety.get("blocked"):
        score = 0.0
    elif not trace_text.strip():
        score = 5.0
    summary = str(analysis.get("one_line") or prepared.get("one_line") or Path(record.source_uri).name).strip()[:240]
    event_tokens = sorted(_tokens(f"{summary} {trace_text[:8000]}"))
    return {
        "candidate_id": f"src_{record.source_id[:20]}",
        "source_type": record.source_type,
        "source_id": record.source_id,
        "source_uri": record.source_uri,
        "mtime_ms": record.mtime_ms,
        "summary": summary,
        "excerpt": trace_text[:1200],
        "content_hash": _content_hash(sanitized),
        "event_tokens": event_tokens[:100],
        "task_association": association,
        "quality_score": round(min(100.0, score), 2),
        "signal_count": signal_count,
        "content_class": content_class,
        "safety": safety,
        "metadata": record.metadata,
    }


def _checkpoint_candidate(session_id: str) -> dict[str, Any] | None:
    from l3_node.work_ledger import load_evidence

    checkpoint = next((row for row in reversed(load_evidence(session_id, 1000)) if row.get("source") == "work_checkpoint"), None)
    if not checkpoint:
        return None
    payload = checkpoint.get("payload") if isinstance(checkpoint.get("payload"), dict) else {}
    paths = [str(item.get("path") or "") for item in (payload.get("changed_files") or []) if isinstance(item, dict)]
    paths.extend(str(item.get("path") or "") for item in (payload.get("recent_files") or []) if isinstance(item, dict))
    paths = list(dict.fromkeys(path for path in paths if path))[:40]
    summary = f"文件检查点记录了 {len(paths)} 个任务期变化文件"
    return {
        "candidate_id": f"checkpoint_{str(checkpoint.get('evidence_id') or '')}",
        "source_type": "file_checkpoint",
        "source_id": str(checkpoint.get("evidence_id") or ""),
        "source_uri": str(payload.get("project_path") or ""),
        "mtime_ms": int(checkpoint.get("collected_at_ms") or 0),
        "summary": summary,
        "excerpt": "\n".join(paths),
        "content_hash": str(payload.get("fingerprint") or _content_hash("\n".join(paths))),
        "event_tokens": sorted(_tokens(" ".join(paths))),
        "task_association": 1.0,
        "quality_score": 70.0,
        "signal_count": len(paths),
        "safety": {"ok": True, "blocked": False, "types": [], "counts": {}},
        "metadata": {"evidence_id": checkpoint.get("evidence_id"), "file_count": len(paths)},
    }


def _merge_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda row: (float(row.get("quality_score") or 0), int(row.get("mtime_ms") or 0)), reverse=True):
        tokens = set(candidate.get("event_tokens") or [])
        match: dict[str, Any] | None = None
        best = 0.0
        for group in groups:
            group_tokens = set(group.get("event_tokens") or [])
            similarity = _similarity(tokens, group_tokens)
            shared_artifact = any("." in token or "_" in token for token in (tokens & group_tokens))
            group_source_types = {str(row.get("source_type") or "") for row in group.get("source_chain") or []}
            checkpoint_pair = candidate.get("source_type") == "file_checkpoint" or "file_checkpoint" in group_source_types
            threshold = 0.06 if shared_artifact and checkpoint_pair else 0.12 if shared_artifact else 0.28
            if similarity > best and similarity >= threshold:
                match = group
                best = similarity
        source_ref = {
            "candidate_id": candidate.get("candidate_id"),
            "source_type": candidate.get("source_type"),
            "source_id": candidate.get("source_id"),
            "source_uri": candidate.get("source_uri"),
            "quality_score": candidate.get("quality_score"),
            "mtime_ms": candidate.get("mtime_ms"),
        }
        if match is None:
            event_seed = "|".join(sorted(tokens)[:30]) or str(candidate.get("content_hash") or candidate.get("candidate_id"))
            groups.append(
                {
                    "event_id": f"event_{_content_hash(event_seed)[:20]}",
                    "summary": candidate.get("summary"),
                    "excerpt": candidate.get("excerpt"),
                    "quality_score": candidate.get("quality_score"),
                    "task_association": candidate.get("task_association"),
                    "event_tokens": sorted(tokens),
                    "source_chain": [source_ref],
                    "status": (
                        "blocked"
                        if (candidate.get("safety") or {}).get("blocked")
                        else "ignored"
                        if (candidate.get("content_class") or {}).get("outcome") == "noise"
                        else "pending"
                    ),
                    "safety": candidate.get("safety"),
                    "content_class": candidate.get("content_class"),
                }
            )
            continue
        match["source_chain"].append(source_ref)
        match["event_tokens"] = sorted(set(match.get("event_tokens") or []) | tokens)
        match["quality_score"] = round(max(float(match.get("quality_score") or 0), float(candidate.get("quality_score") or 0)) + min(12.0, len(match["source_chain"]) * 2.0), 2)
        match["task_association"] = max(float(match.get("task_association") or 0), float(candidate.get("task_association") or 0))
    for group in groups:
        group["source_count"] = len(group.get("source_chain") or [])
        group["source_types"] = sorted({str(row.get("source_type") or "unknown") for row in group.get("source_chain") or []})
        group["dedupe_tokens"] = group.pop("event_tokens", [])
    groups.sort(key=lambda row: (str(row.get("status")) == "pending", float(row.get("quality_score") or 0)), reverse=True)
    return groups


def _merge_inbox_events(previous_events: list[dict[str, Any]], new_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = [dict(row) for row in previous_events if isinstance(row, dict)]
    for event in new_events:
        tokens = set(event.get("dedupe_tokens") or [])
        match: dict[str, Any] | None = next(
            (row for row in merged if row.get("event_id") == event.get("event_id")),
            None,
        )
        if match is None:
            best = 0.0
            for row in merged:
                row_tokens = set(row.get("dedupe_tokens") or [])
                similarity = _similarity(tokens, row_tokens)
                shared_artifact = any("." in token or "_" in token for token in (tokens & row_tokens))
                threshold = 0.1 if shared_artifact else 0.32
                if similarity >= threshold and similarity > best:
                    match = row
                    best = similarity
        if match is None:
            merged.append(event)
            continue
        existing_refs = {
            str(ref.get("source_id") or ref.get("candidate_id") or "")
            for ref in match.get("source_chain") or []
            if isinstance(ref, dict)
        }
        for ref in event.get("source_chain") or []:
            ref_id = str(ref.get("source_id") or ref.get("candidate_id") or "") if isinstance(ref, dict) else ""
            if isinstance(ref, dict) and ref_id not in existing_refs:
                match.setdefault("source_chain", []).append(ref)
                existing_refs.add(ref_id)
        match["source_count"] = len(match.get("source_chain") or [])
        match["source_types"] = sorted(
            {str(ref.get("source_type") or "unknown") for ref in match.get("source_chain") or [] if isinstance(ref, dict)}
        )
        match["dedupe_tokens"] = sorted(set(match.get("dedupe_tokens") or []) | tokens)
        if float(event.get("quality_score") or 0) > float(match.get("quality_score") or 0):
            match["summary"] = event.get("summary")
            match["excerpt"] = event.get("excerpt")
            match["quality_score"] = event.get("quality_score")
            match["content_class"] = event.get("content_class")
        match["task_association"] = max(
            float(match.get("task_association") or 0),
            float(event.get("task_association") or 0),
        )
    return sorted(
        merged[-200:],
        key=lambda row: (str(row.get("status")) == "pending", float(row.get("quality_score") or 0)),
        reverse=True,
    )


def _refresh_process_inbox_unlocked(
    session_id: str,
    *,
    roots: list[str] | None = None,
    inline_sources: list[dict[str, Any]] | None = None,
    max_files: int = 240,
) -> dict[str, Any]:
    """Refresh the inbox from explicit local sources without storing raw conversations."""

    started_perf = time.perf_counter()
    refresh_now_ms = _now_ms()
    session = _load_session(session_id)
    cursor_state = _load_cursor_state(session_id)
    configured_roots = [str(item) for item in cursor_state.get("configured_roots") or [] if str(item).strip()]
    requested_roots = roots if roots is not None else configured_roots or None
    allowed_roots = _allowed_roots(session, requested_roots)
    if roots is not None:
        cursor_state["configured_roots"] = [str(path) for path in allowed_roots]
    cutoff_ms = max(0, int(session.get("created_at_ms") or 0) - 6 * 60 * 60 * 1000)
    candidates: list[dict[str, Any]] = []
    refresh_stats = {
        "started_at": _now_iso(),
        "files_considered": 0,
        "sources_read": 0,
        "sources_skipped_unchanged": 0,
        "sources_paused": 0,
        "sources_backoff": 0,
        "sources_failed": 0,
        "new_byte_count": 0,
        "new_char_count": 0,
        "new_line_count": 0,
        "new_candidate_count": 0,
    }
    for path in _iter_source_files(allowed_roots, max_files=max_files):
        refresh_stats["files_considered"] += 1
        source_uri = str(path.resolve())
        source_key = _source_key(source_uri)
        cursor = cursor_state["sources"].setdefault(
            source_key,
            {"source_key": source_key, "source_uri": source_uri, "position": 0, "paused": False},
        )
        if cursor.get("paused"):
            refresh_stats["sources_paused"] += 1
            continue
        if _source_is_backing_off(cursor, refresh_now_ms):
            refresh_stats["sources_backoff"] += 1
            cursor["status"] = "backoff"
            continue
        try:
            stat = path.stat()
            if cutoff_ms and int(stat.st_mtime * 1000) < cutoff_ms:
                continue
            old_position = max(0, int(cursor.get("position") or 0))
            if stat.st_size < old_position:
                old_position = 0
                cursor["rotation_detected"] = True
            if stat.st_size == old_position:
                refresh_stats["sources_skipped_unchanged"] += 1
                cursor["status"] = "unchanged"
                cursor["last_sync_at"] = _now_iso()
                _mark_source_healthy(cursor)
                continue
            preview = path.read_text(encoding="utf-8-sig", errors="replace")[-3000:]
            adapter = _choose_adapter(path, preview)
            record = adapter.read(path, start_offset=old_position)
            cursor.update(
                {
                    "source_type": record.source_type,
                    "source_uri": source_uri,
                    "position": int(record.metadata.get("end_offset") or stat.st_size),
                    "size": stat.st_size,
                    "mtime_ms": record.mtime_ms,
                    "status": "ok",
                    "last_sync_at": _now_iso(),
                    "total_read_count": int(cursor.get("total_read_count") or 0) + 1,
                    "total_line_count": int(cursor.get("total_line_count") or 0) + int(record.metadata.get("delta_line_count") or 0),
                }
            )
            _mark_source_healthy(cursor)
            if record.text.strip():
                candidates.append(_prepare_candidate(session, record))
                refresh_stats["sources_read"] += 1
                refresh_stats["new_byte_count"] += int(record.metadata.get("delta_bytes") or 0)
                refresh_stats["new_char_count"] += len(record.text)
                refresh_stats["new_line_count"] += int(record.metadata.get("delta_line_count") or 0)
        except (OSError, UnicodeError) as exc:
            _mark_source_failed(cursor, exc, refresh_now_ms)
            refresh_stats["sources_failed"] += 1
            continue
    for index, raw in enumerate(inline_sources or []):
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        source_type = str(raw.get("source_type") or "document").strip().lower()
        source_uri = str(raw.get("source_uri") or f"inline://{source_type}/{index}")
        source_key = _source_key(source_uri)
        cursor = cursor_state["sources"].setdefault(
            source_key,
            {"source_key": source_key, "source_uri": source_uri, "position_chars": 0, "paused": False},
        )
        if cursor.get("paused"):
            refresh_stats["sources_paused"] += 1
            continue
        if _source_is_backing_off(cursor, refresh_now_ms):
            refresh_stats["sources_backoff"] += 1
            cursor["status"] = "backoff"
            continue
        old_position = max(0, int(cursor.get("position_chars") or 0))
        incremental_only = bool(raw.get("incremental", False))
        if incremental_only:
            delta_text = text
            end_position = old_position + len(text)
        elif len(text) >= old_position:
            delta_text = text[old_position:]
            end_position = len(text)
        else:
            delta_text = text
            end_position = len(text)
            cursor["rotation_detected"] = True
        if not delta_text.strip():
            refresh_stats["sources_skipped_unchanged"] += 1
            cursor["status"] = "unchanged"
            cursor["last_sync_at"] = _now_iso()
            _mark_source_healthy(cursor)
            continue
        record = WorkSourceRecord(
            source_type=source_type,
            source_id=_content_hash(f"{source_uri}:{old_position}:{end_position}:{delta_text}"),
            source_uri=source_uri,
            text=delta_text,
            mtime_ms=int(raw.get("mtime_ms") or _now_ms()),
            metadata={
                "inline": True,
                "start_offset": old_position,
                "end_offset": end_position,
                "delta_line_count": len(delta_text.splitlines()),
            },
        )
        candidates.append(_prepare_candidate(session, record))
        cursor.update(
            {
                "source_type": source_type,
                "position_chars": end_position,
                "size": len(text),
                "mtime_ms": record.mtime_ms,
                "status": "ok",
                "last_sync_at": _now_iso(),
                "total_read_count": int(cursor.get("total_read_count") or 0) + 1,
                "total_line_count": int(cursor.get("total_line_count") or 0) + len(delta_text.splitlines()),
            }
        )
        _mark_source_healthy(cursor)
        refresh_stats["sources_read"] += 1
        refresh_stats["new_char_count"] += len(delta_text)
        refresh_stats["new_line_count"] += len(delta_text.splitlines())
    checkpoint = _checkpoint_candidate(session_id)
    if checkpoint:
        checkpoint_key = _source_key(f"checkpoint://{session_id}")
        checkpoint_cursor = cursor_state["sources"].setdefault(
            checkpoint_key,
            {
                "source_key": checkpoint_key,
                "source_uri": f"checkpoint://{session_id}",
                "source_type": "file_checkpoint",
                "paused": False,
            },
        )
        if checkpoint_cursor.get("paused"):
            refresh_stats["sources_paused"] += 1
        elif checkpoint_cursor.get("last_source_id") == checkpoint.get("source_id"):
            refresh_stats["sources_skipped_unchanged"] += 1
        else:
            candidates.append(checkpoint)
            checkpoint_cursor["last_source_id"] = checkpoint.get("source_id")
            checkpoint_cursor["last_sync_at"] = _now_iso()
            checkpoint_cursor["status"] = "ok"
            checkpoint_cursor["total_read_count"] = int(checkpoint_cursor.get("total_read_count") or 0) + 1
    path = _inbox_path(session_id)
    previous = _read_json(path, {})
    previous_event_ids = {
        str(row.get("event_id") or "")
        for row in previous.get("events") or []
        if isinstance(row, dict)
    }
    new_events = _merge_candidates(candidates)
    events = _merge_inbox_events(previous.get("events") or [], new_events)
    refresh_stats["new_candidate_count"] = len(candidates)
    refresh_stats["new_event_count"] = len(new_events)
    refresh_stats["high_quality_new_event_count"] = sum(
        1
        for event in new_events
        if event.get("status") == "pending"
        and float(event.get("quality_score") or 0) >= 70.0
        and str(event.get("event_id") or "") not in previous_event_ids
    )
    refresh_stats["completed_at"] = _now_iso()
    refresh_stats["duration_ms"] = max(0, round((time.perf_counter() - started_perf) * 1000))
    _update_source_health(cursor_state, refresh_stats)
    cursor_state["last_refresh"] = refresh_stats
    _save_cursor_state(session_id, cursor_state)
    inbox = {
        "schema_version": 1,
        "session_id": session_id,
        "generated_at": _now_iso(),
        "allowed_roots": [str(path) for path in allowed_roots],
        "candidate_count": len(candidates),
        "total_candidate_count": int(previous.get("total_candidate_count") or 0) + len(candidates),
        "event_count": len(events),
        "events": events,
        "last_refresh": refresh_stats,
        "summary": {
            status: sum(1 for event in events if event.get("status") == status)
            for status in ("pending", "accepted", "rejected", "ignored", "blocked")
        },
    }
    _write_json(path, inbox)
    return inbox


def refresh_process_inbox(
    session_id: str,
    *,
    roots: list[str] | None = None,
    inline_sources: list[dict[str, Any]] | None = None,
    max_files: int = 240,
) -> dict[str, Any]:
    """Serialize refreshes per task so manual and background scans cannot race."""

    with _SOURCE_REFRESH_LOCKS_GUARD:
        lock = _SOURCE_REFRESH_LOCKS.setdefault(str(session_id), threading.RLock())
    with lock:
        return _refresh_process_inbox_unlocked(
            session_id,
            roots=roots,
            inline_sources=inline_sources,
            max_files=max_files,
        )


def get_process_inbox(session_id: str) -> dict[str, Any]:
    inbox = _read_json(_inbox_path(session_id), {})
    if not isinstance(inbox, dict) or not inbox.get("session_id"):
        return {
            "schema_version": 1,
            "session_id": session_id,
            "generated_at": "",
            "allowed_roots": [],
            "candidate_count": 0,
            "event_count": 0,
            "events": [],
            "summary": {"pending": 0, "accepted": 0, "rejected": 0, "ignored": 0, "blocked": 0},
        }
    return inbox


def get_work_source_status(session_id: str) -> dict[str, Any]:
    state = _load_cursor_state(session_id)
    sources = sorted(
        [dict(row) for row in state.get("sources", {}).values() if isinstance(row, dict)],
        key=lambda row: (bool(row.get("paused")), str(row.get("source_type") or ""), str(row.get("source_uri") or "")),
    )
    now_ms = _now_ms()
    health = dict(state.get("health") or {})
    health["backoff_source_count"] = sum(1 for row in sources if _source_is_backing_off(row, now_ms))
    authorizations: list[dict[str, Any]] = []
    for root_value in state.get("configured_roots") or []:
        root = Path(str(root_value)).expanduser()
        try:
            resolved_root = root.resolve()
        except Exception:
            resolved_root = root
        related_sources = []
        for row in sources:
            source_uri = str(row.get("source_uri") or "")
            if not source_uri or "://" in source_uri:
                continue
            try:
                if _path_within(Path(source_uri), resolved_root):
                    related_sources.append(row)
            except Exception:
                continue
        exists = resolved_root.exists()
        authorizations.append(
            {
                "path": str(resolved_root),
                "exists": exists,
                "readable": bool(exists and os.access(resolved_root, os.R_OK)),
                "source_count": len(related_sources),
                "total_line_count": sum(int(row.get("total_line_count") or 0) for row in related_sources),
                "last_sync_at": max(
                    (str(row.get("last_sync_at") or "") for row in related_sources),
                    default="",
                ),
            }
        )
    return {
        "schema_version": 1,
        "session_id": session_id,
        "configured_roots": state.get("configured_roots") or [],
        "updated_at": state.get("updated_at") or "",
        "last_refresh": state.get("last_refresh") or {},
        "health": health,
        "source_profile": state.get("source_profile") or {},
        "authorizations": authorizations,
        "source_count": len(sources),
        "paused_count": sum(1 for row in sources if row.get("paused")),
        "error_count": sum(1 for row in sources if row.get("status") == "error"),
        "backoff_count": health["backoff_source_count"],
        "sources": sources,
    }


def configure_work_source_roots(session_id: str, roots: list[str]) -> dict[str, Any]:
    session = _load_session(session_id)
    resolved = _allowed_roots(session, roots)
    project_path = str(session.get("project_path") or "").strip()
    profile: dict[str, Any] = {}
    if project_path:
        from l3_node.work_ledger_project_memory import save_project_source_profile

        profile = save_project_source_profile(
            project_path,
            [str(path) for path in resolved],
            session_id=session_id,
        )
    state = _load_cursor_state(session_id)
    state["configured_roots"] = [str(path) for path in resolved]
    state["source_profile_initialized"] = True
    state["source_profile"] = {
        "project_key": profile.get("project_key"),
        "project_path": profile.get("project_path"),
        "inherited": False,
        "inherited_from_session_id": "",
        "profile_updated_at_ms": int(profile.get("updated_at_ms") or 0),
    }
    _save_cursor_state(session_id, state)
    return get_work_source_status(session_id)


def revoke_project_source_authorization(
    session_id: str,
    *,
    root: str = "",
) -> dict[str, Any]:
    session = _load_session(session_id)
    project_path = str(session.get("project_path") or "").strip()
    if not project_path:
        raise ValueError("session has no project_path")
    from l3_node.work_ledger_project_memory import revoke_project_source_profile

    clean_root = str(root or "").strip()
    revoke_project_source_profile(project_path, root=clean_root)
    state = _load_cursor_state(session_id)
    if clean_root:
        revoked_root = Path(clean_root).expanduser().resolve()
        state["configured_roots"] = [
            item
            for item in state.get("configured_roots") or []
            if Path(str(item)).expanduser().resolve() != revoked_root
        ]
        state["sources"] = {
            source_key: cursor
            for source_key, cursor in state.get("sources", {}).items()
            if (
                not str(cursor.get("source_uri") or "")
                or "://" in str(cursor.get("source_uri") or "")
                or not _path_within(Path(str(cursor.get("source_uri"))), revoked_root)
            )
        }
    else:
        state["configured_roots"] = []
        state["sources"] = {
            source_key: cursor
            for source_key, cursor in state.get("sources", {}).items()
            if "://" in str(cursor.get("source_uri") or "")
        }
    state["source_profile_initialized"] = True
    state["source_profile"] = {}
    _save_cursor_state(session_id, state)
    return get_work_source_status(session_id)


def control_work_source(session_id: str, action: str, *, source_key: str = "") -> dict[str, Any]:
    clean_action = str(action or "").strip().lower()
    if clean_action not in {"pause", "resume", "reset", "reset_all"}:
        raise ValueError("action must be pause, resume, reset, or reset_all")
    state = _load_cursor_state(session_id)
    sources = state.get("sources") if isinstance(state.get("sources"), dict) else {}
    targets: list[dict[str, Any]] = []
    if clean_action == "reset_all":
        targets = [row for row in sources.values() if isinstance(row, dict)]
    else:
        target = sources.get(str(source_key or "").strip())
        if not isinstance(target, dict):
            raise ValueError(f"source not found: {source_key}")
        targets = [target]
    for target in targets:
        if clean_action == "pause":
            target["paused"] = True
            target["status"] = "paused"
        elif clean_action == "resume":
            target["paused"] = False
            target["status"] = "ready"
            _mark_source_healthy(target)
        else:
            target["position"] = 0
            target["position_chars"] = 0
            target["last_source_id"] = ""
            target["rotation_detected"] = False
            target["status"] = "reset"
            _mark_source_healthy(target)
    _save_cursor_state(session_id, state)
    return get_work_source_status(session_id)


def review_process_inbox_event(
    session_id: str,
    event_id: str,
    action: str,
    *,
    note: str = "",
    generate_outputs_after: bool = True,
) -> dict[str, Any]:
    clean_action = str(action or "").strip().lower()
    if clean_action not in {"accepted", "rejected", "ignored"}:
        raise ValueError("action must be accepted, rejected, or ignored")
    inbox = get_process_inbox(session_id)
    event = next((row for row in inbox.get("events") or [] if row.get("event_id") == event_id), None)
    if not isinstance(event, dict):
        raise ValueError(f"inbox event not found: {event_id}")
    if event.get("status") == "blocked":
        raise ValueError("blocked inbox event cannot be accepted")

    from l3_node.work_ledger import add_ai_work_trace, append_evidence, generate_work_outputs

    imported_evidence_id = ""
    fact_result: dict[str, Any] = {}
    if clean_action == "accepted":
        source_types = ", ".join(event.get("source_types") or [])
        compact_trace = (
            f"工作事件：{event.get('summary') or ''}\n"
            f"来源：{source_types}\n"
            f"依据：{str(event.get('excerpt') or '')[:1200]}"
        )
        imported = add_ai_work_trace(
            session_id,
            compact_trace,
            tool_name="WorkSourceInbox",
            trace_kind="process_inbox_adoption",
        )
        imported_evidence_id = str(imported.get("evidence_id") or "")
        from l3_node.work_ledger_facts import record_confirmed_work_event

        fact_result = record_confirmed_work_event(
            session_id,
            event,
            verification_evidence_id=imported_evidence_id,
        )
        fact = fact_result.get("fact") if isinstance(fact_result, dict) else None
        if isinstance(fact, dict):
            event["project_fact_id"] = str(fact.get("fact_id") or "")
            event["fact_match_type"] = str(fact_result.get("match_type") or "")
            event["fact_review_pending"] = bool(fact_result.get("review_pending"))
            event["fact_state"] = str(fact.get("state") or "")
            event["fact_state_changed"] = bool(fact_result.get("state_changed"))
    feedback = append_evidence(
        session_id,
        source="work_process_inbox_review",
        summary=f"Process inbox event {clean_action}: {event.get('summary') or event_id}",
        payload={
            "event_id": event_id,
            "action": clean_action,
            "note": str(note or "")[:1000],
            "source_types": event.get("source_types") or [],
            "source_chain": event.get("source_chain") or [],
            "imported_evidence_id": imported_evidence_id,
            "project_fact_id": event.get("project_fact_id") or "",
            "fact_match_type": event.get("fact_match_type") or "",
            "fact_review_pending": bool(event.get("fact_review_pending")),
            "fact_state": event.get("fact_state") or "",
            "fact_state_changed": bool(event.get("fact_state_changed")),
            "fact_state_transition": fact_result.get("state_transition"),
        },
        trust_level="user_confirmed" if clean_action == "accepted" else "user_rejected",
        source_refs=[
            {"type": "work_source", **source}
            for source in (event.get("source_chain") or [])
            if isinstance(source, dict)
        ],
    )
    event["status"] = clean_action
    event["reviewed_at"] = _now_iso()
    event["review_note"] = str(note or "")[:1000]
    event["imported_evidence_id"] = imported_evidence_id
    inbox["generated_at"] = _now_iso()
    inbox["summary"] = {
        status: sum(1 for row in inbox.get("events") or [] if row.get("status") == status)
        for status in ("pending", "accepted", "rejected", "ignored", "blocked")
    }
    _write_json(_inbox_path(session_id), inbox)
    outputs = generate_work_outputs(session_id) if clean_action == "accepted" and generate_outputs_after else {}
    return {
        "event": event,
        "feedback": feedback,
        "fact_result": fact_result,
        "outputs": outputs,
        "inbox": inbox,
    }
