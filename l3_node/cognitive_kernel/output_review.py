"""Output review for Memory Growth review patches.

The output layer stores final user-facing artifacts as Markdown so they can be
reviewed again later. It is deliberately separate from concepts/playbooks:
outputs are evidence and examples first, not automatically stable knowledge.
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

MIN_OUTPUT_CONFIDENCE = 0.2


@dataclass(slots=True)
class OutputReviewResult:
    review_id: str
    patch_path: Path
    promoted_count: int
    quarantined_count: int
    skipped_count: int
    output_paths: list[Path] = field(default_factory=list)
    quarantine_paths: list[Path] = field(default_factory=list)
    report_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "patch_path": str(self.patch_path),
            "promoted_count": self.promoted_count,
            "quarantined_count": self.quarantined_count,
            "skipped_count": self.skipped_count,
            "output_paths": [str(path) for path in self.output_paths],
            "quarantine_paths": [str(path) for path in self.quarantine_paths],
            "report_path": str(self.report_path) if self.report_path else "",
        }


def apply_output_patch(patch_path: str | Path, *, min_confidence: float = MIN_OUTPUT_CONFIDENCE) -> OutputReviewResult:
    """Promote output candidates from a daily review patch into output pages."""

    ensure_memory_growth_scaffold()
    patch_file = Path(patch_path)
    patch = json.loads(patch_file.read_text(encoding="utf-8"))
    review_id = f"output_review_{_now_stamp()}_{uuid.uuid4().hex[:8]}"
    output_paths: list[Path] = []
    quarantine_paths: list[Path] = []
    promoted = quarantined = skipped = 0
    decisions: list[dict[str, Any]] = []

    for candidate in patch.get("output_candidates") or []:
        if not isinstance(candidate, dict):
            skipped += 1
            continue
        decision = _classify_candidate(candidate, min_confidence=min_confidence)
        if decision["action"] == "promote":
            path = _write_output(candidate, patch=patch, review_id=review_id)
            output_paths.append(path)
            promoted += 1
            decision["path"] = str(path)
        elif decision["action"] == "quarantine":
            path = _write_quarantine(candidate, patch=patch, review_id=review_id, reason=decision["reason"])
            quarantine_paths.append(path)
            quarantined += 1
            decision["path"] = str(path)
        else:
            skipped += 1
        decisions.append(decision)

    report_path = _write_report(
        review_id=review_id,
        patch=patch,
        patch_path=patch_file,
        promoted=promoted,
        quarantined=quarantined,
        skipped=skipped,
        decisions=decisions,
    )
    _write_output_index()
    return OutputReviewResult(
        review_id=review_id,
        patch_path=patch_file,
        promoted_count=promoted,
        quarantined_count=quarantined,
        skipped_count=skipped,
        output_paths=output_paths,
        quarantine_paths=quarantine_paths,
        report_path=report_path,
    )


def _classify_candidate(candidate: dict[str, Any], *, min_confidence: float) -> dict[str, Any]:
    summary = str(candidate.get("summary") or "").strip()
    content = str(candidate.get("content") or "").strip()
    confidence = float(candidate.get("confidence") or 0.0)
    if not summary and not content:
        return {"candidate_id": candidate.get("candidate_id"), "action": "skip", "reason": "empty_output"}
    if confidence < min_confidence:
        return {
            "candidate_id": candidate.get("candidate_id"),
            "action": "quarantine",
            "reason": f"low_confidence:{confidence:.2f}",
        }
    return {"candidate_id": candidate.get("candidate_id"), "action": "promote", "reason": "output_evidence_candidate"}


def _write_output(candidate: dict[str, Any], *, patch: dict[str, Any], review_id: str) -> Path:
    path = _output_path(candidate)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = _iso_now()
    text = _render_output(candidate, patch=patch, review_id=review_id, now=now)
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        path.write_text(_append_output_update(existing, candidate=candidate, patch=patch, review_id=review_id, now=now), encoding="utf-8")
    else:
        path.write_text(text, encoding="utf-8")
    return path


def _write_quarantine(candidate: dict[str, Any], *, patch: dict[str, Any], review_id: str, reason: str) -> Path:
    path = memory_growth_dir() / "conflicts" / "outputs" / f"{_safe_segment(str(candidate.get('candidate_id') or uuid.uuid4().hex))}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "review_id": review_id,
        "daily_review_id": patch.get("review_id"),
        "date": patch.get("date"),
        "reason": reason,
        "candidate": candidate,
        "created_at": _iso_now(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def _render_output(candidate: dict[str, Any], *, patch: dict[str, Any], review_id: str, now: str) -> str:
    category = _output_category(candidate)
    title = _title(candidate)
    content = str(candidate.get("content") or "").strip() or str(candidate.get("summary") or "").strip()
    source_refs = candidate.get("source_refs") or []
    frontmatter = {
        "id": _output_id(candidate),
        "type": "output",
        "category": category,
        "summary": str(candidate.get("summary") or "").strip(),
        "source_refs": source_refs,
        "confidence": float(candidate.get("confidence") or 0.0),
        "verification_status": candidate.get("verification_status") or "",
        "created_at": now,
    }
    return (
        "---\n"
        + _yaml_like(frontmatter)
        + "---\n\n"
        + f"# {title}\n\n"
        + "## Output\n\n"
        + f"{content}\n\n"
        + "## Review Notes\n\n"
        + f"- Daily review: `{patch.get('review_id')}`\n"
        + f"- Output review: `{review_id}`\n"
        + f"- Verification status: `{candidate.get('verification_status') or 'unknown'}`\n"
        + f"- Closure type: `{candidate.get('closure_type') or 'unknown'}`\n\n"
        + "## Source Evidence\n\n"
        + _render_refs(source_refs)
        + "\n"
        + "## Reflow Back To Knowledge\n\n"
        + "- This output should be considered by later Daily/Weekly Review before becoming a concept or playbook.\n"
    )


def _append_output_update(existing: str, *, candidate: dict[str, Any], patch: dict[str, Any], review_id: str, now: str) -> str:
    block = (
        "\n"
        + f"- {now}: linked candidate `{candidate.get('candidate_id')}` from daily review `{patch.get('review_id')}` "
        + f"through `{review_id}`. Verification `{candidate.get('verification_status') or 'unknown'}`.\n"
    )
    updated = _append_under_heading(existing, "## Review Notes", block)
    updated = _append_under_heading(updated, "## Source Evidence", _render_refs(candidate.get("source_refs") or []))
    return _replace_frontmatter_field(updated, "confidence", f"{float(candidate.get('confidence') or 0.0):.2f}")


def _write_report(
    *,
    review_id: str,
    patch: dict[str, Any],
    patch_path: Path,
    promoted: int,
    quarantined: int,
    skipped: int,
    decisions: list[dict[str, Any]],
) -> Path:
    path = memory_growth_dir() / "reviews" / "output_reviews" / f"{review_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "review_id": review_id,
        "patch_path": str(patch_path),
        "daily_review_id": patch.get("review_id"),
        "date": patch.get("date"),
        "promoted_count": promoted,
        "quarantined_count": quarantined,
        "skipped_count": skipped,
        "decisions": decisions,
        "created_at": _iso_now(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def _write_output_index() -> None:
    root = memory_growth_dir()
    index_path = root / "indexes" / "outputs.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "path": str(path.relative_to(root)),
            "category": path.parent.name,
            "slug": path.stem,
            "updated_at": _iso_now(),
        }
        for path in sorted((root / "outputs").glob("*/*.md"))
        if path.name != "README.md"
    ]
    index_path.write_text(json.dumps({"schema_version": 1, "outputs": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _output_path(candidate: dict[str, Any]) -> Path:
    category = _output_category(candidate)
    return memory_growth_dir() / "outputs" / category / f"{_output_slug(candidate)}.md"


def _output_category(candidate: dict[str, Any]) -> str:
    raw = str(candidate.get("target_type") or candidate.get("output_category") or "work_records").strip().lower()
    allowed = {"reports", "lark_messages", "work_records", "pmo_reports", "debug_summaries", "user_docs"}
    return raw if raw in allowed else "work_records"


def _output_id(candidate: dict[str, Any]) -> str:
    return f"output:{_output_category(candidate)}:{_output_slug(candidate)}"


def _output_slug(candidate: dict[str, Any]) -> str:
    candidate_id = str(candidate.get("candidate_id") or "")
    if candidate_id:
        parts = candidate_id.split(":")
        if len(parts) >= 2:
            return _safe_segment(":".join(parts[1:]))[:100]
    return _safe_segment(str(candidate.get("summary") or "output"))[:100]


def _title(candidate: dict[str, Any]) -> str:
    summary = str(candidate.get("summary") or "").strip()
    if len(summary) <= 72:
        return summary or "Untitled Output"
    return summary[:72].rstrip() + "..."


def _render_refs(refs: list[dict[str, Any]]) -> str:
    if not refs:
        return "- No source refs recorded.\n"
    return "".join(f"- `{json.dumps(ref, ensure_ascii=False, default=str)}`\n" for ref in refs)


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
    replacement = f"{key}: {value if _looks_number(value) else json.dumps(value, ensure_ascii=False)}"
    if pattern.search(frontmatter):
        frontmatter = pattern.sub(replacement, frontmatter)
    else:
        frontmatter += "\n" + replacement
    return frontmatter + text[end:]


def _yaml_like(value: dict[str, Any]) -> str:
    lines: list[str] = []
    for key, item in value.items():
        if isinstance(item, (list, dict)):
            lines.append(f"{key}: {json.dumps(item, ensure_ascii=False, default=str)}")
        elif isinstance(item, (int, float)):
            lines.append(f"{key}: {item}")
        else:
            lines.append(f"{key}: {json.dumps(str(item), ensure_ascii=False)}")
    return "\n".join(lines) + "\n"


def _safe_segment(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_\-\u4e00-\u9fff]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "output"


def _looks_number(value: str) -> bool:
    try:
        float(value)
        return True
    except Exception:
        return False


def _now_stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")
