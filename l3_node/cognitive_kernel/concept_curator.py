"""Concept curator for Memory Growth review patches.

Daily review produces candidate facts. The curator is the first promotion gate:
it writes stable, source-backed Markdown concept pages and quarantines weak or
conflicting candidates instead of polluting long-term memory.
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

MIN_STABLE_CONFIDENCE = 0.72


@dataclass(slots=True)
class ConceptCuratorResult:
    merge_id: str
    patch_path: Path
    promoted_count: int
    quarantined_count: int
    skipped_count: int
    concept_paths: list[Path] = field(default_factory=list)
    conflict_paths: list[Path] = field(default_factory=list)
    report_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "merge_id": self.merge_id,
            "patch_path": str(self.patch_path),
            "promoted_count": self.promoted_count,
            "quarantined_count": self.quarantined_count,
            "skipped_count": self.skipped_count,
            "concept_paths": [str(path) for path in self.concept_paths],
            "conflict_paths": [str(path) for path in self.conflict_paths],
            "report_path": str(self.report_path) if self.report_path else "",
        }


def apply_concept_patch(patch_path: str | Path, *, min_confidence: float = MIN_STABLE_CONFIDENCE) -> ConceptCuratorResult:
    """Merge concept candidates from a daily review patch."""

    ensure_memory_growth_scaffold()
    patch_file = Path(patch_path)
    patch = json.loads(patch_file.read_text(encoding="utf-8"))
    merge_id = f"concept_merge_{_now_stamp()}_{uuid.uuid4().hex[:8]}"
    concept_paths: list[Path] = []
    conflict_paths: list[Path] = []
    promoted = quarantined = skipped = 0
    decisions: list[dict[str, Any]] = []

    for candidate in patch.get("concept_candidates") or []:
        if not isinstance(candidate, dict):
            skipped += 1
            continue
        decision = _classify_candidate(candidate, min_confidence=min_confidence)
        if decision["action"] == "promote":
            path = _write_concept(candidate, patch=patch, merge_id=merge_id)
            concept_paths.append(path)
            promoted += 1
            decision["path"] = str(path)
        elif decision["action"] == "quarantine":
            path = _write_conflict(candidate, patch=patch, merge_id=merge_id, reason=decision["reason"])
            conflict_paths.append(path)
            quarantined += 1
            decision["path"] = str(path)
        else:
            skipped += 1
        decisions.append(decision)

    report_path = _write_merge_report(
        merge_id=merge_id,
        patch=patch,
        patch_path=patch_file,
        promoted=promoted,
        quarantined=quarantined,
        skipped=skipped,
        decisions=decisions,
    )
    _write_concept_index()
    return ConceptCuratorResult(
        merge_id=merge_id,
        patch_path=patch_file,
        promoted_count=promoted,
        quarantined_count=quarantined,
        skipped_count=skipped,
        concept_paths=concept_paths,
        conflict_paths=conflict_paths,
        report_path=report_path,
    )


def _classify_candidate(candidate: dict[str, Any], *, min_confidence: float) -> dict[str, Any]:
    summary = str(candidate.get("summary") or "").strip()
    confidence = float(candidate.get("confidence") or 0.0)
    if not summary:
        return {"candidate_id": candidate.get("candidate_id"), "action": "skip", "reason": "empty_summary"}
    if candidate.get("requires_user_confirmation"):
        return {
            "candidate_id": candidate.get("candidate_id"),
            "action": "quarantine",
            "reason": "requires_user_confirmation",
        }
    if confidence < min_confidence:
        return {
            "candidate_id": candidate.get("candidate_id"),
            "action": "quarantine",
            "reason": f"low_confidence:{confidence:.2f}",
        }
    existing = _existing_concept_path(candidate)
    if existing and _has_conflicting_summary(existing, summary):
        return {
            "candidate_id": candidate.get("candidate_id"),
            "action": "quarantine",
            "reason": "summary_conflicts_with_existing_concept",
        }
    return {"candidate_id": candidate.get("candidate_id"), "action": "promote", "reason": "stable_candidate"}


def _write_concept(candidate: dict[str, Any], *, patch: dict[str, Any], merge_id: str) -> Path:
    path = _concept_path(candidate)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = _iso_now()
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        updated = _append_concept_update(existing, candidate=candidate, patch=patch, merge_id=merge_id, now=now)
        path.write_text(updated, encoding="utf-8")
    else:
        path.write_text(_render_new_concept(candidate, patch=patch, merge_id=merge_id, now=now), encoding="utf-8")
    return path


def _write_conflict(candidate: dict[str, Any], *, patch: dict[str, Any], merge_id: str, reason: str) -> Path:
    conflicts_dir = memory_growth_dir() / "conflicts" / _safe_segment(str(candidate.get("target_type") or "unknown"))
    conflicts_dir.mkdir(parents=True, exist_ok=True)
    path = conflicts_dir / f"{_safe_segment(str(candidate.get('candidate_id') or uuid.uuid4().hex))}.json"
    payload = {
        "schema_version": 1,
        "merge_id": merge_id,
        "review_id": patch.get("review_id"),
        "date": patch.get("date"),
        "reason": reason,
        "candidate": candidate,
        "created_at": _iso_now(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def _render_new_concept(candidate: dict[str, Any], *, patch: dict[str, Any], merge_id: str, now: str) -> str:
    source_refs = candidate.get("source_refs") or []
    summary = str(candidate.get("summary") or "").strip()
    target_type = str(candidate.get("target_type") or "fact")
    concept_id = _concept_id(candidate)
    title = _title_for_candidate(candidate)
    frontmatter = {
        "id": concept_id,
        "type": target_type,
        "summary": summary,
        "source_refs": source_refs,
        "confidence": float(candidate.get("confidence") or 0.0),
        "last_verified": now,
        "valid_from": patch.get("date") or now[:10],
        "valid_until": "",
        "conflicts": [],
    }
    return (
        "---\n"
        + _yaml_like(frontmatter)
        + "---\n\n"
        + f"# {title}\n\n"
        + "## Summary\n\n"
        + f"{summary}\n\n"
        + "## Stable Facts\n\n"
        + f"- {summary}\n\n"
        + "## Source Evidence\n\n"
        + _render_refs(source_refs)
        + "\n"
        + "## Related Entities\n\n"
        + "- TBD\n\n"
        + "## Open Questions\n\n"
        + "- None recorded.\n\n"
        + "## Update Log\n\n"
        + f"- {now}: promoted from review `{patch.get('review_id')}` by `{merge_id}`.\n"
    )


def _append_concept_update(
    existing: str,
    *,
    candidate: dict[str, Any],
    patch: dict[str, Any],
    merge_id: str,
    now: str,
) -> str:
    summary = str(candidate.get("summary") or "").strip()
    block = (
        "\n"
        + f"- {now}: merged candidate `{candidate.get('candidate_id')}` from review `{patch.get('review_id')}` "
        + f"by `{merge_id}`. Confidence `{float(candidate.get('confidence') or 0.0):.2f}`.\n"
    )
    fact = f"- {summary}\n"
    updated = existing
    if summary and summary not in updated:
        updated = _append_under_heading(updated, "## Stable Facts", fact)
    updated = _append_under_heading(updated, "## Source Evidence", _render_refs(candidate.get("source_refs") or []))
    updated = _append_under_heading(updated, "## Update Log", block)
    updated = _replace_frontmatter_field(updated, "last_verified", now)
    updated = _replace_frontmatter_field(updated, "confidence", f"{float(candidate.get('confidence') or 0.0):.2f}")
    return updated


def _write_merge_report(
    *,
    merge_id: str,
    patch: dict[str, Any],
    patch_path: Path,
    promoted: int,
    quarantined: int,
    skipped: int,
    decisions: list[dict[str, Any]],
) -> Path:
    reports_dir = memory_growth_dir() / "reviews" / "concept_merges"
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{merge_id}.json"
    payload = {
        "schema_version": 1,
        "merge_id": merge_id,
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


def _write_concept_index() -> None:
    root = memory_growth_dir()
    concepts_root = root / "concepts"
    index_path = root / "indexes" / "concepts.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for path in sorted(concepts_root.glob("*/*.md")):
        rows.append(
            {
                "path": str(path.relative_to(root)),
                "type": path.parent.name,
                "slug": path.stem,
                "updated_at": _iso_now(),
            }
        )
    index_path.write_text(json.dumps({"schema_version": 1, "concepts": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _concept_path(candidate: dict[str, Any]) -> Path:
    target_type = _safe_segment(str(candidate.get("target_type") or "fact"))
    slug = _concept_slug(candidate)
    return memory_growth_dir() / "concepts" / target_type / f"{slug}.md"


def _existing_concept_path(candidate: dict[str, Any]) -> Path | None:
    path = _concept_path(candidate)
    return path if path.exists() else None


def _concept_id(candidate: dict[str, Any]) -> str:
    return f"{candidate.get('target_type') or 'fact'}:{_concept_slug(candidate)}"


def _concept_slug(candidate: dict[str, Any]) -> str:
    candidate_id = str(candidate.get("candidate_id") or "")
    if candidate_id:
        parts = candidate_id.split(":")
        if len(parts) >= 3:
            return _safe_segment(":".join(parts[1:]))
    return _safe_segment(str(candidate.get("summary") or "concept"))[:80]


def _title_for_candidate(candidate: dict[str, Any]) -> str:
    summary = str(candidate.get("summary") or "").strip()
    if len(summary) <= 72:
        return summary or "Untitled Concept"
    return summary[:72].rstrip() + "..."


def _has_conflicting_summary(path: Path, summary: str) -> bool:
    text = path.read_text(encoding="utf-8")
    if summary in text:
        return False
    old = _extract_frontmatter_value(text, "summary")
    if not old:
        return False
    return _token_overlap(old, summary) < 0.2


def _token_overlap(a: str, b: str) -> float:
    token_re = re.compile(r"[A-Za-z0-9_\u4e00-\u9fff]+")
    left = {x.lower() for x in token_re.findall(a)}
    right = {x.lower() for x in token_re.findall(b)}
    if not left or not right:
        return 0.0
    return len(left & right) / max(len(left | right), 1)


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


def _extract_frontmatter_value(text: str, key: str) -> str:
    if not text.startswith("---\n"):
        return ""
    end = text.find("\n---\n", 4)
    if end == -1:
        return ""
    for line in text[:end].splitlines():
        if line.startswith(f"{key}:"):
            value = line.split(":", 1)[1].strip()
            return value.strip('"')
    return ""


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


def _render_refs(refs: list[dict[str, Any]]) -> str:
    if not refs:
        return "- No source refs recorded.\n"
    return "".join(f"- `{json.dumps(ref, ensure_ascii=False, sort_keys=True, default=str)}`\n" for ref in refs)


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
