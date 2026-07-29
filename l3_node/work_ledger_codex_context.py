"""Budgeted, redacted context packs for Work Ledger Codex collaboration."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from l3_node.work_ledger import redact_sensitive_material, scan_sensitive_material


DEFAULT_CONTEXT_MAX_CHARS = 16000
DEFAULT_FILE_EXCERPT_MAX_CHARS = 1400
BLOCKED_DIR_NAMES = {
    ".git",
    ".idea",
    ".venv",
    ".vscode",
    "__pycache__",
    "node_modules",
    "venv",
}
BLOCKED_FILE_SUFFIXES = {".key", ".pem", ".p12", ".pfx"}
BLOCKED_FILE_NAMES = {
    ".env",
    "credentials.json",
    "id_dsa",
    "id_ed25519",
    "id_rsa",
}


def _tokens(value: Any) -> set[str]:
    raw = str(value or "").casefold()
    tokens: set[str] = set()
    for token in re.findall(
        r"[a-z0-9_./-]{2,}|[\u4e00-\u9fff]{2,}",
        raw,
    ):
        clean = token.strip("._/-")
        if clean:
            tokens.add(clean)
        tokens.update(
            part
            for part in re.split(r"[._/-]+", clean)
            if len(part) >= 2
        )
    return tokens


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").replace("\x00", "").strip()
    if len(text) <= max(0, limit):
        return text
    if limit <= 80:
        return text[: max(0, limit)]
    head = max(40, int(limit * 0.72))
    tail = max(20, limit - head - 32)
    return (
        text[:head].rstrip()
        + "\n...[CONTEXT_TRUNCATED]...\n"
        + text[-tail:].lstrip()
    )[:limit]


def _redact(value: Any) -> tuple[str, dict[str, Any]]:
    raw = str(value or "").replace("\x00", "")
    before = scan_sensitive_material(raw)
    clean = redact_sensitive_material(raw)
    clean = re.sub(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        "[REDACTED_EMAIL]",
        clean,
    )
    clean = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[REDACTED_PHONE]", clean)
    clean = re.sub(
        r"(?i)\b(?:gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{20,}|AKIA[A-Z0-9]{16})\b",
        "[REDACTED_TOKEN]",
        clean,
    )
    clean = re.sub(
        r"(?i)\b(postgres(?:ql)?|mysql|mongodb(?:\+srv)?)://([^:/\s]+):([^@\s]+)@",
        r"\1://[REDACTED_USER]:[REDACTED_PASSWORD]@",
        clean,
    )
    after = scan_sensitive_material(clean)
    return clean, {
        "input_types": before.get("types") or [],
        "input_counts": before.get("counts") or {},
        "blocked_before_redaction": bool(before.get("blocked")),
        "safe_after_redaction": not bool(after.get("blocked")),
    }


def _relative_project_path(
    project_path: str,
    candidate_path: Any,
) -> tuple[str, str]:
    raw = str(candidate_path or "").strip().replace("\\", "/")
    if not raw:
        return "", "empty_path"
    lowered = raw.casefold()
    name = Path(raw).name.casefold()
    suffix = Path(raw).suffix.casefold()
    parts = {part.casefold() for part in Path(raw).parts}
    if (
        name in BLOCKED_FILE_NAMES
        or name.startswith(".env.")
        or any(word in name for word in ("credential", "private_key", "secret"))
        or suffix in BLOCKED_FILE_SUFFIXES
        or bool(parts & BLOCKED_DIR_NAMES)
    ):
        return "", "sensitive_or_generated_path"
    root = Path(project_path).expanduser().resolve()
    candidate = Path(raw)
    try:
        resolved = (
            candidate.expanduser().resolve()
            if candidate.is_absolute()
            else (root / candidate).resolve()
        )
        relative = resolved.relative_to(root)
    except (OSError, ValueError):
        return "", "outside_project_root"
    normalized = relative.as_posix()
    if not normalized or normalized == ".":
        return "", "project_root_not_file"
    return normalized, ""


def _relevance_score(path: str, text: str, query_tokens: set[str]) -> float:
    path_tokens = _tokens(path)
    text_tokens = _tokens(str(text or "")[:5000])
    if not query_tokens:
        return 0.5
    path_overlap = len(path_tokens & query_tokens) / max(1, len(query_tokens))
    text_overlap = len(text_tokens & query_tokens) / max(1, len(query_tokens))
    return round(min(1.0, path_overlap * 0.72 + text_overlap * 0.28), 4)


def _rank_changed_files(
    project_path: str,
    rows: list[dict[str, Any]],
    query_tokens: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    included: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        relative, reason = _relative_project_path(project_path, row.get("path"))
        if not relative:
            blocked.append({"path": str(row.get("path") or ""), "reason": reason})
            continue
        included.append(
            {
                "path": relative,
                "status": str(row.get("status") or "modified")[:40],
                "relevance": _relevance_score(relative, "", query_tokens),
                "_order": index,
            }
        )
    included.sort(key=lambda row: (-float(row["relevance"]), int(row["_order"])))
    for row in included:
        row.pop("_order", None)
    return included, blocked


def _diff_blocks(patch: str) -> list[tuple[str, str]]:
    text = str(patch or "").replace("\x00", "").strip()
    if not text:
        return []
    starts = [match.start() for match in re.finditer(r"(?m)^diff --git ", text)]
    if not starts:
        return [("", text)]
    blocks: list[tuple[str, str]] = []
    starts.append(len(text))
    for index in range(len(starts) - 1):
        block = text[starts[index] : starts[index + 1]].strip()
        header = block.splitlines()[0] if block else ""
        match = re.match(r"diff --git a/(.+?) b/(.+)$", header)
        path = match.group(2).strip() if match else ""
        blocks.append((path, block))
    return blocks


def _rank_diff(
    project_path: str,
    patch: str,
    query_tokens: set[str],
    *,
    budget: int,
) -> tuple[str, list[dict[str, Any]], int, dict[str, int]]:
    ranked: list[tuple[float, int, str, str]] = []
    blocked: list[dict[str, Any]] = []
    redaction_counts: dict[str, int] = {}
    for index, (path, block) in enumerate(_diff_blocks(patch)):
        relative = ""
        if path:
            relative, reason = _relative_project_path(project_path, path)
            if not relative:
                blocked.append({"path": path, "reason": reason})
                continue
        clean, report = _redact(block)
        for kind, count in report.get("input_counts", {}).items():
            redaction_counts[kind] = redaction_counts.get(kind, 0) + int(count)
        ranked.append(
            (
                _relevance_score(relative, clean, query_tokens),
                index,
                relative,
                clean,
            )
        )
    ranked.sort(key=lambda row: (-row[0], row[1]))
    selected: list[str] = []
    used = 0
    dropped = 0
    for _score, _index, _path, block in ranked:
        remaining = max(0, budget - used)
        if remaining < 120:
            dropped += 1
            continue
        clipped = _clip(block, min(remaining, 2600))
        selected.append(clipped)
        used += len(clipped) + 2
        if len(clipped) < len(block):
            dropped += 1
    return "\n\n".join(selected), blocked, dropped, redaction_counts


def _context_length(context: dict[str, Any]) -> int:
    return len(json.dumps(context, ensure_ascii=False, separators=(",", ":")))


def _fit_context(
    context: dict[str, Any],
    max_chars: int,
    stats: dict[str, Any],
) -> None:
    while _context_length(context) > max_chars:
        if context.get("file_snippets"):
            context["file_snippets"].pop()
            stats["dropped_snippets"] += 1
            continue
        if len(str(context.get("diff_excerpt") or "")) > 800:
            current = str(context["diff_excerpt"])
            context["diff_excerpt"] = _clip(current, max(800, int(len(current) * 0.75)))
            stats["truncated_sections"].append("diff_excerpt")
            continue
        if len(str(context.get("cached_diff_excerpt") or "")) > 500:
            current = str(context["cached_diff_excerpt"])
            context["cached_diff_excerpt"] = _clip(
                current,
                max(500, int(len(current) * 0.72)),
            )
            stats["truncated_sections"].append("cached_diff_excerpt")
            continue
        if len(context.get("changed_files") or []) > 12:
            context["changed_files"].pop()
            stats["dropped_changed_files"] += 1
            continue
        reduced = False
        for key in ("risks", "failures", "existing_decisions", "existing_next_steps"):
            if context.get(key):
                context[key].pop()
                stats["dropped_context_items"] += 1
                reduced = True
                break
        if reduced:
            continue
        context["user_goal"] = _clip(context.get("user_goal"), 600)
        context["task_title"] = _clip(context.get("task_title"), 240)
        break


def build_codex_context_pack(
    *,
    project_name: str,
    project_path: str,
    task_title: Any = "",
    user_goal: Any = "",
    purpose: Any = "",
    phase: Any = "",
    trigger_reason: Any = "",
    evidence_gaps: list[Any] | None = None,
    changed_files: list[dict[str, Any]] | None = None,
    diff_stat: Any = "",
    diff_patch: Any = "",
    cached_diff_patch: Any = "",
    file_snippets: list[dict[str, Any]] | None = None,
    failures: list[Any] | None = None,
    risks: list[Any] | None = None,
    existing_decisions: list[Any] | None = None,
    existing_next_steps: list[Any] | None = None,
    max_chars: int | None = None,
) -> dict[str, Any]:
    """Build a stable Context Pack that is safe to place in a Codex prompt."""

    budget = max(
        5000,
        min(
            int(
                max_chars
                or os.environ.get("JACHIN_CODEX_CONTEXT_MAX_CHARS")
                or DEFAULT_CONTEXT_MAX_CHARS
            ),
            48000,
        ),
    )
    per_file_budget = max(
        400,
        min(
            int(
                os.environ.get("JACHIN_CODEX_CONTEXT_FILE_MAX_CHARS")
                or DEFAULT_FILE_EXCERPT_MAX_CHARS
            ),
            4000,
        ),
    )
    query_text = " ".join(
        str(value or "")
        for value in (
            task_title,
            user_goal,
            purpose,
            trigger_reason,
            " ".join(str(item or "") for item in (evidence_gaps or [])),
        )
    )
    query_tokens = _tokens(query_text)
    ranked_files, blocked_paths = _rank_changed_files(
        project_path,
        list(changed_files or []),
        query_tokens,
    )
    diff_text, blocked_diff_paths, dropped_diff_blocks, diff_redactions = _rank_diff(
        project_path,
        str(diff_patch or ""),
        query_tokens,
        budget=min(6200, int(budget * 0.40)),
    )
    (
        cached_text,
        blocked_cached_paths,
        dropped_cached_blocks,
        cached_diff_redactions,
    ) = _rank_diff(
        project_path,
        str(cached_diff_patch or ""),
        query_tokens,
        budget=min(2600, int(budget * 0.18)),
    )
    snippets: list[dict[str, Any]] = []
    blocked_snippets: list[dict[str, Any]] = []
    redaction_reports: list[dict[str, Any]] = []
    for index, item in enumerate(file_snippets or []):
        if not isinstance(item, dict):
            continue
        relative, reason = _relative_project_path(project_path, item.get("path"))
        if not relative:
            blocked_snippets.append(
                {"path": str(item.get("path") or ""), "reason": reason}
            )
            continue
        clean, report = _redact(item.get("excerpt"))
        if report["input_types"]:
            redaction_reports.append(report)
        snippets.append(
            {
                "path": relative,
                "excerpt": _clip(clean, per_file_budget),
                "relevance": _relevance_score(relative, clean, query_tokens),
                "_order": index,
            }
        )
    snippets.sort(key=lambda row: (-float(row["relevance"]), int(row["_order"])))
    for row in snippets:
        row.pop("_order", None)

    def safe(value: Any, limit: int) -> str:
        clean, report = _redact(value)
        if report["input_types"]:
            redaction_reports.append(report)
        return _clip(clean, limit)

    def safe_list(values: list[Any] | None, limit: int, count: int) -> list[str]:
        return [
            clean
            for clean in (safe(value, limit) for value in (values or [])[:count])
            if clean
        ]

    context = {
        "schema_version": 1,
        "project_name": safe(project_name, 160),
        "project_root": str(Path(project_path).expanduser().resolve()),
        "task_title": safe(task_title, 400),
        "user_goal": safe(user_goal, 1200),
        "purpose": safe(purpose, 800),
        "phase": safe(phase, 80),
        "trigger_reason": safe(trigger_reason, 800),
        "evidence_gaps": safe_list(evidence_gaps, 240, 12),
        "changed_files": ranked_files[:60],
        "diff_stat": safe(diff_stat, 1600),
        "diff_excerpt": diff_text,
        "cached_diff_excerpt": cached_text,
        "file_snippets": snippets[:10],
        "failures": safe_list(failures, 600, 12),
        "risks": safe_list(risks, 600, 12),
        "existing_decisions": safe_list(existing_decisions, 500, 10),
        "existing_next_steps": safe_list(existing_next_steps, 500, 10),
    }
    redaction_type_counts = dict(diff_redactions)
    for kind, count in cached_diff_redactions.items():
        redaction_type_counts[kind] = redaction_type_counts.get(kind, 0) + int(
            count
        )
    stats = {
        "max_chars": budget,
        "input_changed_files": len(changed_files or []),
        "included_changed_files": len(context["changed_files"]),
        "dropped_changed_files": max(
            0,
            len(ranked_files) - len(context["changed_files"]),
        ),
        "input_snippets": len(file_snippets or []),
        "included_snippets": len(context["file_snippets"]),
        "dropped_snippets": max(0, len(snippets) - len(context["file_snippets"])),
        "dropped_diff_blocks": dropped_diff_blocks + dropped_cached_blocks,
        "dropped_context_items": 0,
        "blocked_paths": blocked_paths + blocked_diff_paths + blocked_cached_paths + blocked_snippets,
        "redaction_type_counts": redaction_type_counts,
        "truncated_sections": [],
    }
    for report in redaction_reports:
        for kind, count in report.get("input_counts", {}).items():
            stats["redaction_type_counts"][kind] = (
                stats["redaction_type_counts"].get(kind, 0) + int(count)
            )
    _fit_context(context, budget, stats)
    serialized = json.dumps(context, ensure_ascii=False, indent=2)
    stats["output_chars"] = len(serialized)
    stats["within_budget"] = len(serialized) <= budget
    stats["included_changed_files"] = len(context["changed_files"])
    stats["included_snippets"] = len(context["file_snippets"])
    stats["truncated_sections"] = list(dict.fromkeys(stats["truncated_sections"]))
    digest = hashlib.sha256(
        json.dumps(context, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "context": context,
        "serialized": serialized,
        "digest": digest,
        "stats": stats,
    }
