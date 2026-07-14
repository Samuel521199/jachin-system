"""Artifact curator for Memory Growth rewrite requests.

Artifact governance can downrank a weak concept/playbook and write a rewrite
request. The curator turns those requests into concrete drafts and confirmation
queue entries without overwriting the original artifact.
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
class ArtifactCuratorResult:
    curation_id: str
    processed_count: int
    skipped_count: int
    draft_paths: list[Path] = field(default_factory=list)
    confirmation_paths: list[Path] = field(default_factory=list)
    report_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "curation_id": self.curation_id,
            "processed_count": self.processed_count,
            "skipped_count": self.skipped_count,
            "draft_paths": [str(path) for path in self.draft_paths],
            "confirmation_paths": [str(path) for path in self.confirmation_paths],
            "report_path": str(self.report_path) if self.report_path else "",
        }


@dataclass(slots=True)
class ArtifactMergeResult:
    merge_id: str
    artifact_path: Path
    backup_path: Path
    draft_path: Path
    confirmation_path: Path | None
    side_effects: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "merge_id": self.merge_id,
            "artifact_path": str(self.artifact_path),
            "backup_path": str(self.backup_path),
            "draft_path": str(self.draft_path),
            "confirmation_path": str(self.confirmation_path) if self.confirmation_path else "",
            "side_effects": list(self.side_effects),
        }


def run_artifact_curator(*, max_items: int = 10) -> ArtifactCuratorResult:
    """Convert pending artifact rewrite requests into reviewable drafts."""

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    curation_id = f"artifact_curator_{_stamp()}_{uuid.uuid4().hex[:8]}"
    requests = _pending_rewrite_requests(root)[: max(1, min(max_items, 25))]
    draft_paths: list[Path] = []
    confirmation_paths: list[Path] = []
    skipped = 0
    decisions: list[dict[str, Any]] = []

    for request_path, request in requests:
        artifact_path = _safe_memory_growth_path(root, request.get("artifact_path"))
        if artifact_path is None or not artifact_path.exists():
            skipped += 1
            decisions.append(
                {
                    "request_path": str(request_path.relative_to(root)),
                    "action": "skip",
                    "reason": "artifact_missing",
                }
            )
            _mark_request(request_path, request, status="skipped", curation_id=curation_id, reason="artifact_missing")
            continue
        artifact_text = artifact_path.read_text(encoding="utf-8", errors="ignore")
        draft = _draft_for_artifact(
            root=root,
            artifact_path=artifact_path,
            artifact_text=artifact_text,
            request=request,
            curation_id=curation_id,
        )
        draft_path = _write_draft(root=root, draft=draft, curation_id=curation_id)
        confirmation_path = _write_confirmation(root=root, draft=draft, draft_path=draft_path, curation_id=curation_id)
        _mark_request(
            request_path,
            request,
            status="drafted",
            curation_id=curation_id,
            draft_path=str(draft_path.relative_to(root)),
            confirmation_path=str(confirmation_path.relative_to(root)),
        )
        draft_paths.append(draft_path)
        confirmation_paths.append(confirmation_path)
        decisions.append(
            {
                "request_path": str(request_path.relative_to(root)),
                "action": "draft",
                "artifact_path": str(artifact_path.relative_to(root)),
                "draft_path": str(draft_path.relative_to(root)),
                "confirmation_path": str(confirmation_path.relative_to(root)),
            }
        )

    report_path = _write_report(
        root=root,
        curation_id=curation_id,
        processed=len(draft_paths),
        skipped=skipped,
        decisions=decisions,
    )
    return ArtifactCuratorResult(
        curation_id=curation_id,
        processed_count=len(draft_paths),
        skipped_count=skipped,
        draft_paths=draft_paths,
        confirmation_paths=confirmation_paths,
        report_path=report_path,
    )


def merge_artifact_draft(
    *,
    draft_path: str | Path,
    confirmation_path: str | Path | None = None,
    governance_id: str | None = None,
) -> ArtifactMergeResult:
    """Merge an approved artifact rewrite draft back into the source artifact."""

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    draft_file = _safe_memory_growth_path(root, draft_path)
    if draft_file is None or not draft_file.exists():
        raise ValueError(f"artifact draft not found: {draft_path}")
    try:
        draft = json.loads(draft_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"invalid artifact draft json: {draft_file}") from exc
    artifact_file = _safe_memory_growth_path(root, draft.get("artifact_path"))
    if artifact_file is None or not artifact_file.exists():
        raise ValueError(f"source artifact not found: {draft.get('artifact_path')}")
    confirmation_file = _safe_memory_growth_path(root, confirmation_path) if confirmation_path else None
    merge_id = governance_id or f"artifact_merge_{_stamp()}_{uuid.uuid4().hex[:8]}"
    original = artifact_file.read_text(encoding="utf-8", errors="ignore")
    backup = _backup_artifact(root=root, artifact_path=artifact_file, text=original, merge_id=merge_id)
    merged = _merged_artifact_text(original=original, draft=draft, merge_id=merge_id)
    artifact_file.write_text(merged, encoding="utf-8")
    _mark_draft_merged(draft_file, draft, merge_id=merge_id, artifact_path=artifact_file.relative_to(root).as_posix(), backup_path=backup.relative_to(root).as_posix())
    if confirmation_file and confirmation_file.exists():
        _mark_confirmation_confirmed(confirmation_file, merge_id=merge_id)
    _refresh_usage_index(root)
    side_effects = [
        {"type": "artifact_rewrite_merged", "path": artifact_file.relative_to(root).as_posix()},
        {"type": "artifact_backup_written", "path": backup.relative_to(root).as_posix()},
        {"type": "artifact_draft_marked_merged", "path": draft_file.relative_to(root).as_posix()},
    ]
    if confirmation_file:
        side_effects.append({"type": "artifact_rewrite_confirmation_confirmed", "path": confirmation_file.relative_to(root).as_posix()})
    return ArtifactMergeResult(
        merge_id=merge_id,
        artifact_path=artifact_file,
        backup_path=backup,
        draft_path=draft_file,
        confirmation_path=confirmation_file,
        side_effects=side_effects,
    )


def _pending_rewrite_requests(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    request_dir = root / "reviews" / "artifact_rewrites"
    if not request_dir.exists():
        return []
    rows: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(request_dir.glob("*.json"), key=lambda item: item.stat().st_mtime if item.exists() else 0):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        if str(payload.get("curation_status") or "").lower() in {"drafted", "skipped", "confirmed"}:
            continue
        rows.append((path, payload))
    return rows


def _draft_for_artifact(
    *,
    root: Path,
    artifact_path: Path,
    artifact_text: str,
    request: dict[str, Any],
    curation_id: str,
) -> dict[str, Any]:
    frontmatter = _frontmatter(artifact_text)
    rel = artifact_path.relative_to(root).as_posix()
    artifact_type = str(frontmatter.get("type") or ("playbook" if "/playbooks/" in f"/{rel}" else "concept"))
    summary = str(frontmatter.get("summary") or _first_heading(artifact_text) or artifact_path.stem)
    failure_reason = _failure_reason(request=request, frontmatter=frontmatter)
    use_count = _int(frontmatter.get("memory_use_count"))
    success_rate = _float(frontmatter.get("memory_success_rate"))
    failure_count = _int(frontmatter.get("memory_failure_count"))
    return {
        "schema_version": 1,
        "draft_id": f"artifact_draft:{_safe_segment(rel)}:{uuid.uuid4().hex[:8]}",
        "curation_id": curation_id,
        "artifact_path": rel,
        "artifact_type": artifact_type,
        "summary": f"Rewrite proposal for {summary}",
        "failure_reason": failure_reason,
        "usage": {
            "memory_use_count": use_count,
            "memory_success_rate": success_rate,
            "memory_failure_count": failure_count,
        },
        "source_refs": [
            {"type": "artifact", "path": rel},
            {"type": "artifact_rewrite_request", "governance_id": request.get("governance_id"), "reason": request.get("reason")},
        ],
        "draft_markdown": _draft_markdown(
            artifact_type=artifact_type,
            title=summary,
            artifact_path=rel,
            failure_reason=failure_reason,
            use_count=use_count,
            success_rate=success_rate,
            failure_count=failure_count,
            artifact_text=artifact_text,
        ),
        "recommended_action": "confirm_pending_after_review",
        "created_at": _iso_now(),
    }


def _draft_markdown(
    *,
    artifact_type: str,
    title: str,
    artifact_path: str,
    failure_reason: str,
    use_count: int,
    success_rate: float,
    failure_count: int,
    artifact_text: str,
) -> str:
    if "playbook" in artifact_type.lower():
        body = (
            f"# Rewrite Draft: {title}\n\n"
            "## Why rewrite\n\n"
            f"- Source artifact: `{artifact_path}`\n"
            f"- Failure reason: `{failure_reason}`\n"
            f"- Usage: `{use_count}` uses, success rate `{success_rate:.3f}`, failures `{failure_count}`\n\n"
            "## Revised Applicable Scenario\n\n"
            "- Use only when the current task evidence matches the trigger conditions below.\n\n"
            "## Revised Recommended Flow\n\n"
            "1. Inspect the latest state/evidence before selecting this playbook.\n"
            "2. Execute the least risky path first.\n"
            "3. Verify the result with explicit evidence before reporting success.\n"
            "4. If the same failure repeats, stop automatic retry and request review.\n\n"
            "## Verification Criteria\n\n"
            "- The final state must match the user request.\n"
            "- Evidence must include the relevant window/file/message/result snapshot.\n"
            "- The previous failure reason must not recur.\n\n"
            "## Failure Paths\n\n"
            f"- If `{failure_reason}` appears again, do not reuse this artifact without a newer successful example.\n"
        )
    else:
        body = (
            f"# Rewrite Draft: {title}\n\n"
            "## Why rewrite\n\n"
            f"- Source artifact: `{artifact_path}`\n"
            f"- Failure reason: `{failure_reason}`\n"
            f"- Usage: `{use_count}` uses, success rate `{success_rate:.3f}`, failures `{failure_count}`\n\n"
            "## Revised Stable Fact\n\n"
            "- Keep this knowledge only if newer evidence confirms it still applies.\n\n"
            "## Verification Boundary\n\n"
            "- Mark as revalidated only after it is supported by a successful task or explicit user confirmation.\n"
        )
    preview = _body_preview(artifact_text)
    return body + "\n## Source Artifact Preview\n\n" + preview + "\n"


def _write_draft(*, root: Path, draft: dict[str, Any], curation_id: str) -> Path:
    draft_dir = root / "reviews" / "artifact_drafts"
    draft_dir.mkdir(parents=True, exist_ok=True)
    path = draft_dir / f"{curation_id}_{_safe_segment(str(draft.get('artifact_path') or 'artifact'))}.json"
    path.write_text(json.dumps(draft, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    md_path = path.with_suffix(".md")
    md_path.write_text(str(draft.get("draft_markdown") or ""), encoding="utf-8")
    return path


def _write_confirmation(*, root: Path, draft: dict[str, Any], draft_path: Path, curation_id: str) -> Path:
    conflict_dir = root / "conflicts" / "artifact_rewrites"
    conflict_dir.mkdir(parents=True, exist_ok=True)
    path = conflict_dir / f"{curation_id}_{_safe_segment(str(draft.get('artifact_path') or 'artifact'))}.json"
    payload = {
        "schema_version": 1,
        "reason": "artifact_rewrite_requires_user_confirmation",
        "date": _iso_now()[:10],
        "created_at": _iso_now(),
        "candidate": {
            "candidate_id": draft.get("draft_id"),
            "summary": draft.get("summary"),
            "target_artifact_path": draft.get("artifact_path"),
            "draft_path": str(draft_path.relative_to(root)),
            "requires_user_confirmation": True,
            "source_refs": draft.get("source_refs") or [],
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def _mark_request(path: Path, request: dict[str, Any], *, status: str, curation_id: str, **extra: Any) -> None:
    next_payload = dict(request)
    next_payload["curation_status"] = status
    next_payload["curation_id"] = curation_id
    next_payload["curated_at"] = _iso_now()
    next_payload.update(extra)
    path.write_text(json.dumps(next_payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _write_report(*, root: Path, curation_id: str, processed: int, skipped: int, decisions: list[dict[str, Any]]) -> Path:
    reports_dir = root / "reviews" / "artifact_curator"
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{curation_id}.json"
    payload = {
        "schema_version": 1,
        "curation_id": curation_id,
        "created_at": _iso_now(),
        "processed_count": processed,
        "skipped_count": skipped,
        "decisions": decisions,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def _backup_artifact(*, root: Path, artifact_path: Path, text: str, merge_id: str) -> Path:
    rel = artifact_path.relative_to(root)
    backup = root / "archive" / "artifact_versions" / rel.parent / f"{artifact_path.stem}.{_safe_segment(merge_id)}{artifact_path.suffix}"
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_text(text, encoding="utf-8")
    return backup


def _merged_artifact_text(*, original: str, draft: dict[str, Any], merge_id: str) -> str:
    draft_markdown = str(draft.get("draft_markdown") or "").strip()
    if not draft_markdown:
        raise ValueError("artifact draft has empty draft_markdown")
    now = _iso_now()
    frontmatter = _frontmatter(original)
    next_text = original
    for key, value in (
        ("summary", str(draft.get("summary") or frontmatter.get("summary") or "Rewritten artifact")),
        ("last_verified", now[:10]),
        ("artifact_review_status", "rewritten"),
        ("artifact_rewritten_at", now),
        ("artifact_rewrite_merge_id", merge_id),
        ("governance_strategy_action", "artifact_rewrite_merged"),
        ("governance_strategy_weight", "1.00"),
        ("governance_execution_mode", "normal"),
        ("governance_requires_more_evidence", "false"),
        ("governance_strategy_reason", "artifact_rewrite_confirmed"),
        ("governance_strategy_updated_at", now),
    ):
        next_text = _upsert_frontmatter_field(next_text, key, value)
    front = _frontmatter_block(next_text)
    return front + "\n\n" + draft_markdown + "\n\n## Merge Log\n\n" + f"- {now}: merged artifact rewrite draft `{draft.get('draft_id')}` by `{merge_id}`.\n"


def _mark_draft_merged(draft_path: Path, draft: dict[str, Any], *, merge_id: str, artifact_path: str, backup_path: str) -> None:
    next_payload = dict(draft)
    next_payload["merge_status"] = "merged"
    next_payload["merge_id"] = merge_id
    next_payload["merged_at"] = _iso_now()
    next_payload["merged_artifact_path"] = artifact_path
    next_payload["backup_path"] = backup_path
    draft_path.write_text(json.dumps(next_payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _mark_confirmation_confirmed(path: Path, *, merge_id: str) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    governance = payload.get("governance") if isinstance(payload.get("governance"), dict) else {}
    governance.update({"status": "confirmed", "governance_id": merge_id, "updated_at": _iso_now()})
    payload["governance"] = governance
    payload["artifact_merge_id"] = merge_id
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _refresh_usage_index(root: Path) -> None:
    try:
        from .memory_growth_strategy import refresh_artifact_usage_index

        refresh_artifact_usage_index(root)
    except Exception:
        pass


def _frontmatter_block(text: str) -> str:
    if not text.startswith("---"):
        return "---\n---"
    end = text.find("\n---", 3)
    if end < 0:
        return "---\n---"
    return text[: end + 5]


def _upsert_frontmatter_field(text: str, key: str, value: Any) -> str:
    line = f'{key}: "{_escape_frontmatter(str(value))}"'
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end >= 0:
            head = text[3:end].strip("\n")
            body = text[end:]
            rows = head.splitlines() if head else []
            replaced = False
            next_rows: list[str] = []
            for row in rows:
                if row.split(":", 1)[0].strip() == key:
                    next_rows.append(line)
                    replaced = True
                else:
                    next_rows.append(row)
            if not replaced:
                next_rows.append(line)
            return "---\n" + "\n".join(next_rows) + body
    return "---\n" + line + "\n---\n\n" + text


def _escape_frontmatter(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"')


def _failure_reason(*, request: dict[str, Any], frontmatter: dict[str, Any]) -> str:
    specific = str(frontmatter.get("memory_last_failure_reason") or "").strip()
    generic = str(request.get("reason") or "").strip()
    if specific and generic in {"", "low_success_or_repeated_failure", "low_success_rate"}:
        return specific
    return generic or specific or "artifact_needs_rewrite"


def _safe_memory_growth_path(root: Path, value: Any) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    raw = Path(text)
    path = raw if raw.is_absolute() else root / raw
    try:
        resolved_root = root.resolve()
        resolved = path.resolve()
        resolved.relative_to(resolved_root)
    except Exception:
        return None
    return resolved


def _frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    out: dict[str, Any] = {}
    for line in text[3:end].strip().splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        out[key.strip()] = raw.strip().strip('"').strip("'")
    return out


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return ""


def _body_preview(text: str, limit: int = 1400) -> str:
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end >= 0:
            body = text[end + 5 :]
    body = body.strip()
    return body[:limit] + ("\n\n..." if len(body) > limit else "")


def _safe_segment(value: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(value or "").lower())
    while "__" in clean:
        clean = clean.replace("__", "_")
    return clean.strip("_")[:120] or "artifact"


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except Exception:
        return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")
