"""Playbook builder for Memory Growth review patches.

Playbooks are reusable task methods distilled from repeated successes and
failures. This module promotes review patch candidates into Markdown playbook
pages while keeping weak candidates quarantined for later review.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .memory_growth import ensure_memory_growth_scaffold, memory_growth_dir

MIN_PLAYBOOK_CONFIDENCE = 0.5


@dataclass(slots=True)
class PlaybookBuilderResult:
    build_id: str
    patch_path: Path
    promoted_count: int
    quarantined_count: int
    skipped_count: int
    playbook_paths: list[Path] = field(default_factory=list)
    quarantine_paths: list[Path] = field(default_factory=list)
    report_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "build_id": self.build_id,
            "patch_path": str(self.patch_path),
            "promoted_count": self.promoted_count,
            "quarantined_count": self.quarantined_count,
            "skipped_count": self.skipped_count,
            "playbook_paths": [str(path) for path in self.playbook_paths],
            "quarantine_paths": [str(path) for path in self.quarantine_paths],
            "report_path": str(self.report_path) if self.report_path else "",
        }


def apply_playbook_patch(patch_path: str | Path, *, min_confidence: float = MIN_PLAYBOOK_CONFIDENCE) -> PlaybookBuilderResult:
    """Merge playbook candidates from a review patch into Markdown playbooks."""

    ensure_memory_growth_scaffold()
    patch_file = Path(patch_path)
    patch = json.loads(patch_file.read_text(encoding="utf-8"))
    build_id = f"playbook_build_{_now_stamp()}_{uuid.uuid4().hex[:8]}"
    playbook_paths: list[Path] = []
    quarantine_paths: list[Path] = []
    promoted = quarantined = skipped = 0
    decisions: list[dict[str, Any]] = []

    for candidate in patch.get("playbook_candidates") or []:
        if not isinstance(candidate, dict):
            skipped += 1
            continue
        decision = _classify_candidate(candidate, min_confidence=min_confidence)
        if decision["action"] == "promote":
            path = _write_playbook(candidate, patch=patch, build_id=build_id)
            playbook_paths.append(path)
            promoted += 1
            decision["path"] = str(path)
        elif decision["action"] == "quarantine":
            path = _write_quarantine(candidate, patch=patch, build_id=build_id, reason=decision["reason"])
            quarantine_paths.append(path)
            quarantined += 1
            decision["path"] = str(path)
        else:
            skipped += 1
        decisions.append(decision)

    report_path = _write_build_report(
        build_id=build_id,
        patch=patch,
        patch_path=patch_file,
        promoted=promoted,
        quarantined=quarantined,
        skipped=skipped,
        decisions=decisions,
    )
    _write_playbook_index()
    return PlaybookBuilderResult(
        build_id=build_id,
        patch_path=patch_file,
        promoted_count=promoted,
        quarantined_count=quarantined,
        skipped_count=skipped,
        playbook_paths=playbook_paths,
        quarantine_paths=quarantine_paths,
        report_path=report_path,
    )


def _classify_candidate(candidate: dict[str, Any], *, min_confidence: float) -> dict[str, Any]:
    title = str(candidate.get("title") or "").strip()
    flow = candidate.get("recommended_flow") or []
    confidence = float(candidate.get("confidence") or 0.0)
    if not title:
        return {"candidate_id": candidate.get("candidate_id"), "action": "skip", "reason": "empty_title"}
    if not isinstance(flow, list) or not flow:
        return {"candidate_id": candidate.get("candidate_id"), "action": "quarantine", "reason": "missing_recommended_flow"}
    if confidence < min_confidence:
        return {
            "candidate_id": candidate.get("candidate_id"),
            "action": "quarantine",
            "reason": f"low_confidence:{confidence:.2f}",
        }
    return {"candidate_id": candidate.get("candidate_id"), "action": "promote", "reason": "usable_playbook_candidate"}


def _write_playbook(candidate: dict[str, Any], *, patch: dict[str, Any], build_id: str) -> Path:
    path = _playbook_path(candidate)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = _iso_now()
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        path.write_text(_append_playbook_update(existing, candidate=candidate, patch=patch, build_id=build_id, now=now), encoding="utf-8")
    else:
        path.write_text(_render_new_playbook(candidate, patch=patch, build_id=build_id, now=now), encoding="utf-8")
    return path


def _write_quarantine(candidate: dict[str, Any], *, patch: dict[str, Any], build_id: str, reason: str) -> Path:
    path = memory_growth_dir() / "conflicts" / "playbooks" / f"{_safe_segment(str(candidate.get('candidate_id') or uuid.uuid4().hex))}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "build_id": build_id,
        "review_id": patch.get("review_id"),
        "date": patch.get("date"),
        "reason": reason,
        "candidate": candidate,
        "created_at": _iso_now(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def _render_new_playbook(candidate: dict[str, Any], *, patch: dict[str, Any], build_id: str, now: str) -> str:
    source_refs = candidate.get("source_refs") or []
    title = str(candidate.get("title") or "Untitled Playbook").strip()
    frontmatter = {
        "id": _playbook_id(candidate),
        "type": "playbook",
        "summary": title,
        "source_refs": source_refs,
        "confidence": float(candidate.get("confidence") or 0.0),
        "last_verified": now,
    }
    return (
        "---\n"
        + _yaml_like(frontmatter)
        + "---\n\n"
        + f"# {title}\n\n"
        + "## Applicable Scenario\n\n"
        + _render_trigger(candidate.get("trigger") or {})
        + "\n"
        + "## Trigger Conditions\n\n"
        + _render_json_bullets(candidate.get("trigger") or {})
        + "\n"
        + "## Required Context\n\n"
        + "- Source evidence must be available before reuse.\n\n"
        + "## Recommended Flow\n\n"
        + _render_flow(candidate.get("recommended_flow") or [])
        + "\n"
        + "## WorkOrder Breakdown\n\n"
        + _render_work_orders(candidate)
        + "\n"
        + "## Available Skill / MCP\n\n"
        + "- Derived from source WorkOrder chain. Exact capability selection remains Arbiter-controlled.\n\n"
        + "## Verification Criteria\n\n"
        + _render_list(candidate.get("evidence_required") or ["verification_report", "turn_closure"])
        + "\n"
        + "## Failure Paths\n\n"
        + _render_failure_paths(candidate)
        + "\n"
        + "## User Confirmation Boundary\n\n"
        + "- Ask the user before high-risk writes, destructive operations, or external message delivery.\n\n"
        + "## Evidence Requirements\n\n"
        + _render_refs(source_refs)
        + "\n"
        + "## Historical Effective Cases\n\n"
        + f"- {now}: promoted from review `{patch.get('review_id')}` by `{build_id}`.\n\n"
        + "## Forbidden Actions\n\n"
        + "- Do not execute destructive steps without an explicit confirmation policy.\n"
    )


def _append_playbook_update(
    existing: str,
    *,
    candidate: dict[str, Any],
    patch: dict[str, Any],
    build_id: str,
    now: str,
) -> str:
    updated = existing
    updated = _append_under_heading(updated, "## Recommended Flow", _render_flow(candidate.get("recommended_flow") or []))
    updated = _append_under_heading(updated, "## Evidence Requirements", _render_refs(candidate.get("source_refs") or []))
    updated = _append_under_heading(
        updated,
        "## Historical Effective Cases",
        f"- {now}: merged `{candidate.get('candidate_id')}` from review `{patch.get('review_id')}` by `{build_id}`.\n",
    )
    updated = _replace_frontmatter_field(updated, "last_verified", now)
    updated = _replace_frontmatter_field(updated, "confidence", f"{float(candidate.get('confidence') or 0.0):.2f}")
    return updated


def _write_build_report(
    *,
    build_id: str,
    patch: dict[str, Any],
    patch_path: Path,
    promoted: int,
    quarantined: int,
    skipped: int,
    decisions: list[dict[str, Any]],
) -> Path:
    path = memory_growth_dir() / "reviews" / "playbook_builds" / f"{build_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "build_id": build_id,
        "patch_path": str(patch_path),
        "review_id": patch.get("review_id"),
        "date": patch.get("date"),
        "promoted_count": promoted,
        "quarantined_count": quarantined,
        "skipped_count": skipped,
        "decisions": decisions,
        "created_at": _iso_now(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def _write_playbook_index() -> None:
    root = memory_growth_dir()
    index_path = root / "indexes" / "playbooks.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "path": str(path.relative_to(root)),
            "slug": path.stem,
            "updated_at": _iso_now(),
        }
        for path in sorted((root / "playbooks").glob("*.md"))
        if path.name != "README.md"
    ]
    index_path.write_text(json.dumps({"schema_version": 1, "playbooks": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _playbook_path(candidate: dict[str, Any]) -> Path:
    return memory_growth_dir() / "playbooks" / f"{_playbook_slug(candidate)}.md"


def _playbook_id(candidate: dict[str, Any]) -> str:
    return f"playbook:{_playbook_slug(candidate)}"


def _playbook_slug(candidate: dict[str, Any]) -> str:
    candidate_id = str(candidate.get("candidate_id") or "")
    if candidate_id:
        parts = candidate_id.split(":")
        if len(parts) >= 3:
            return _safe_segment(":".join(parts[1:]))[:100]
    return _safe_segment(str(candidate.get("title") or "playbook"))[:100]


def _render_trigger(trigger: dict[str, Any]) -> str:
    if not trigger:
        return "- General repeated workflow detected from review evidence.\n"
    sources = ", ".join(str(x) for x in trigger.get("sources") or [])
    categories = ", ".join(str(x) for x in trigger.get("categories") or [])
    memory_types = ", ".join(str(x) for x in trigger.get("memory_types") or [])
    lines = []
    if sources:
        lines.append(f"- Sources: {sources}")
    if categories:
        lines.append(f"- Categories: {categories}")
    if memory_types:
        lines.append(f"- Memory types: {memory_types}")
    if trigger.get("failure"):
        lines.append("- Failure recovery scenario")
    return "\n".join(lines or ["- General repeated workflow detected from review evidence."]) + "\n"


def _render_json_bullets(value: dict[str, Any]) -> str:
    if not value:
        return "- No explicit trigger metadata recorded.\n"
    return "".join(f"- `{key}`: `{json.dumps(item, ensure_ascii=False, default=str)}`\n" for key, item in value.items())


def _render_flow(flow: list[dict[str, Any]]) -> str:
    if not flow:
        return "- No flow recorded.\n"
    lines: list[str] = []
    for idx, step in enumerate(flow, start=1):
        if isinstance(step, dict):
            label = step.get("step") or f"step_{idx}"
            detail = {k: v for k, v in step.items() if k != "step"}
            suffix = f" `{json.dumps(detail, ensure_ascii=False, default=str)}`" if detail else ""
            lines.append(f"{idx}. {label}{suffix}")
        else:
            lines.append(f"{idx}. {step}")
    return "\n".join(lines) + "\n"


def _render_work_orders(candidate: dict[str, Any]) -> str:
    work_order_ids: list[str] = []
    for step in candidate.get("recommended_flow") or []:
        if isinstance(step, dict):
            work_order_ids.extend(str(x) for x in step.get("work_order_ids") or [])
    if not work_order_ids:
        return "- WorkOrder chain should be derived at runtime by Arbiter.\n"
    return "".join(f"- `{work_order_id}`\n" for work_order_id in sorted(set(work_order_ids)))


def _render_failure_paths(candidate: dict[str, Any]) -> str:
    trigger = candidate.get("trigger") or {}
    if trigger.get("failure"):
        return (
            "1. Inspect failure reason from VerificationReport.\n"
            "2. Select the next capability path based on accumulated attempts.\n"
            "3. Retry or ask the user when risk or ambiguity is high.\n"
        )
    return (
        "1. If verification fails, inspect the VerificationReport failure reason.\n"
        "2. Ask RecoveryPlanner for capability-declared alternatives.\n"
        "3. Stop with a clear final report after max attempts.\n"
    )


def _render_list(items: list[Any]) -> str:
    if not items:
        return "- None recorded.\n"
    return "".join(f"- {item}\n" for item in items)


def _render_refs(refs: list[dict[str, Any]]) -> str:
    if not refs:
        return "- No source refs recorded.\n"
    return "".join(f"- `{json.dumps(ref, ensure_ascii=False, sort_keys=True, default=str)}`\n" for ref in refs)


def _append_under_heading(text: str, heading: str, addition: str) -> str:
    if not addition.strip():
        return text
    if heading not in text:
        return text.rstrip() + f"\n\n{heading}\n\n{addition}"
    idx = text.index(heading) + len(heading)
    insert_at = text.find("\n## ", idx)
    if insert_at == -1:
        insert_at = len(text)
    before = text[:insert_at].rstrip()
    after = text[insert_at:]
    if addition.strip() in before:
        return text
    return before + "\n\n" + addition.rstrip() + "\n" + after


def _replace_frontmatter_field(text: str, key: str, value: str) -> str:
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end == -1:
        return text
    frontmatter = text[:end]
    pattern = re.compile(rf"^{re.escape(key)}:.*$", re.MULTILINE)
    replacement = f"{key}: {json.dumps(value, ensure_ascii=False) if not _looks_number(value) else value}"
    if pattern.search(frontmatter):
        frontmatter = pattern.sub(replacement, frontmatter)
    else:
        frontmatter += "\n" + replacement
    return frontmatter + text[end:]


def _yaml_like(data: dict[str, Any]) -> str:
    lines: list[str] = []
    for key, value in data.items():
        if isinstance(value, (list, dict)):
            lines.append(f"{key}: {json.dumps(value, ensure_ascii=False, default=str)}")
        elif isinstance(value, (int, float)):
            lines.append(f"{key}: {value}")
        else:
            lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    return "\n".join(lines) + "\n"


def _safe_segment(value: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in value.strip().lower())
    clean = re.sub(r"-+", "-", clean).strip("-_.")
    return clean or "unknown"


def _looks_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _now_stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S", time.gmtime())
