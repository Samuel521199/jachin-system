"""Recall adapter for Memory Growth concepts and playbooks."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .contracts import MemoryEvidence, MemoryRecallRequest
from .memory_growth import memory_growth_dir
from .memory_growth_strategy import (
    apply_usage_to_score,
    apply_strategy_to_score,
    artifact_usage_metadata,
    artifact_strategy_metadata,
    load_governance_strategy_policy,
    parse_frontmatter,
    strategy_preview,
    usage_preview,
)


def recall_memory_growth(
    request: MemoryRecallRequest,
    *,
    limit: int = 5,
) -> tuple[list[MemoryEvidence], list[str]]:
    """Recall relevant Memory Growth concepts/playbooks for a turn."""

    gaps: list[str] = []
    root = memory_growth_dir()
    strategy_policy = load_governance_strategy_policy(root)
    query = _query_text(request)
    concepts = _recall_concepts(root, query=query, limit=limit, strategy_policy=strategy_policy)
    playbooks = _recall_playbooks(root, query=query, request=request, limit=limit, strategy_policy=strategy_policy)
    if not concepts and not playbooks:
        gaps.append("memory_growth_no_relevant_concepts_or_playbooks")
    return [*concepts, *playbooks], gaps


def _recall_concepts(root: Path, *, query: str, limit: int, strategy_policy: dict[str, Any]) -> list[MemoryEvidence]:
    rows = _index_rows(root / "indexes" / "concepts.json", key="concepts")
    if not rows:
        rows = [
            {
                "path": str(path.relative_to(root)),
                "type": path.parent.name,
                "slug": path.stem,
            }
            for path in sorted((root / "concepts").glob("*/*.md"))
            if path.name != "README.md"
        ]
    scored: list[tuple[float, Path, dict[str, Any], str, dict[str, Any], dict[str, Any]]] = []
    for row in rows:
        path = root / str(row.get("path") or "")
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        frontmatter = parse_frontmatter(text)
        score = _score_text(query, text, bonus_terms=[str(row.get("type") or ""), str(row.get("slug") or "")])
        strategy = artifact_strategy_metadata(root=root, path=path, frontmatter=frontmatter, policy=strategy_policy)
        score, _detail = apply_strategy_to_score(score, strategy)
        usage = artifact_usage_metadata(frontmatter)
        score, _usage_detail = apply_usage_to_score(score, usage)
        if score > 0:
            scored.append((score, path, row, text, strategy, usage))
    scored.sort(key=lambda item: item[0], reverse=True)
    items: list[MemoryEvidence] = []
    for score, path, row, text, strategy, usage in scored[:limit]:
        frontmatter = parse_frontmatter(text)
        concept_type = str(frontmatter.get("type") or row.get("type") or "concept")
        items.append(
            MemoryEvidence(
                memory_id=f"memory_growth:concept:{path.stem}",
                memory_type=_concept_memory_type(concept_type),
                content=_concept_content(path, frontmatter, text, strategy=strategy, usage=usage),
                source="Memory Growth Concepts",
                confidence=min(0.95, max(0.55, _float(frontmatter.get("confidence"), 0.65) + min(score, 5.0) * 0.03)),
                ttl="long_term",
                relevance_reason=f"Memory Growth concept matched current query; path={path}; {strategy_preview(strategy)}; {usage_preview(usage)}",
            )
        )
    return items


def _recall_playbooks(root: Path, *, query: str, request: MemoryRecallRequest, limit: int, strategy_policy: dict[str, Any]) -> list[MemoryEvidence]:
    rows = _index_rows(root / "indexes" / "playbooks.json", key="playbooks")
    if not rows:
        rows = [
            {
                "path": str(path.relative_to(root)),
                "slug": path.stem,
            }
            for path in sorted((root / "playbooks").glob("*.md"))
            if path.name != "README.md"
        ]
    bonus = [*request.candidate_intents, *request.candidate_task_domains]
    scored: list[tuple[float, Path, dict[str, Any], str, dict[str, Any], dict[str, Any]]] = []
    for row in rows:
        path = root / str(row.get("path") or "")
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        frontmatter = parse_frontmatter(text)
        score = _score_text(query, text, bonus_terms=[str(row.get("slug") or ""), *bonus])
        strategy = artifact_strategy_metadata(root=root, path=path, frontmatter=frontmatter, policy=strategy_policy)
        score, _detail = apply_strategy_to_score(score, strategy)
        usage = artifact_usage_metadata(frontmatter)
        score, _usage_detail = apply_usage_to_score(score, usage)
        if score > 0:
            scored.append((score, path, row, text, strategy, usage))
    scored.sort(key=lambda item: item[0], reverse=True)
    items: list[MemoryEvidence] = []
    for score, path, _row, text, strategy, usage in scored[:limit]:
        frontmatter = parse_frontmatter(text)
        memory_type = "failure_hint" if _looks_like_recovery_playbook(text) else "tool_habit"
        items.append(
            MemoryEvidence(
                memory_id=f"memory_growth:playbook:{path.stem}",
                memory_type=memory_type,
                content=_playbook_content(path, frontmatter, text, strategy=strategy, usage=usage),
                source="Memory Growth Playbooks",
                confidence=min(0.95, max(0.6, _float(frontmatter.get("confidence"), 0.68) + min(score, 5.0) * 0.03)),
                ttl="long_term",
                relevance_reason=f"Memory Growth playbook matched current query; path={path}; {strategy_preview(strategy)}; {usage_preview(usage)}",
            )
        )
    return items


def _index_rows(path: Path, *, key: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = data.get(key) if isinstance(data, dict) else None
    return rows if isinstance(rows, list) else []


def _query_text(request: MemoryRecallRequest) -> str:
    parts: list[str] = []
    parts.extend(str(x or "") for x in request.multi_queries.values())
    parts.extend(str(x or "") for x in request.candidate_intents)
    parts.extend(str(x or "") for x in request.candidate_task_domains)
    parts.extend(str(x or "") for x in request.candidate_entities)
    return "\n".join(parts)


def _score_text(query: str, text: str, *, bonus_terms: list[str]) -> float:
    query_terms = _terms(query)
    hay = text.lower()
    if not query_terms:
        return 0.0
    hits = sum(1 for term in query_terms if term in hay)
    bonus = sum(0.5 for term in bonus_terms if term and str(term).lower() in hay)
    return hits + bonus


def _terms(text: str) -> list[str]:
    raw_terms = re.findall(r"[A-Za-z0-9_\-]{2,}|[\u4e00-\u9fff]{2,}", text.lower())
    stop = {
        "the",
        "and",
        "or",
        "to",
        "of",
        "a",
        "an",
        "is",
        "in",
        "for",
        "with",
        "task",
        "query",
        "candidate",
    }
    out: list[str] = []
    seen: set[str] = set()
    for term in raw_terms:
        clean = term.strip("-_")
        if not clean or clean in stop or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
    return out[:80]


def _frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    out: dict[str, Any] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        raw = raw.strip()
        try:
            out[key.strip()] = json.loads(raw)
        except Exception:
            out[key.strip()] = raw.strip('"')
    return out


def _concept_memory_type(concept_type: str) -> str:
    low = concept_type.lower()
    if "preference" in low:
        return "user_preference"
    if "problem" in low or "failure" in low:
        return "failure_hint"
    if "project" in low:
        return "project_fact"
    if "tool" in low or "action" in low:
        return "tool_habit"
    return "historical_task_summary"


def _concept_content(path: Path, frontmatter: dict[str, Any], text: str, *, strategy: dict[str, Any], usage: dict[str, Any]) -> str:
    summary = str(frontmatter.get("summary") or _section(text, "Summary") or path.stem)
    facts = _section(text, "Stable Facts")
    return f"concept path={path}; summary={summary}; {strategy_preview(strategy)}; {usage_preview(usage)}; stable_facts={facts[:1200]}".strip()[:1800]


def _playbook_content(path: Path, frontmatter: dict[str, Any], text: str, *, strategy: dict[str, Any], usage: dict[str, Any]) -> str:
    summary = str(frontmatter.get("summary") or path.stem)
    trigger = _section(text, "Trigger Conditions")
    flow = _section(text, "Recommended Flow")
    verify = _section(text, "Verification Criteria")
    failure = _section(text, "Failure Paths")
    return (
        f"playbook path={path}; summary={summary}; {strategy_preview(strategy)}; {usage_preview(usage)}; "
        f"trigger={trigger[:500]}; flow={flow[:800]}; "
        f"verification={verify[:500]}; failure_paths={failure[:500]}"
    ).strip()[:2400]


def _section(text: str, heading: str) -> str:
    marker = f"## {heading}"
    start = text.find(marker)
    if start < 0:
        return ""
    start += len(marker)
    end = text.find("\n## ", start)
    if end < 0:
        end = len(text)
    return text[start:end].strip()


def _looks_like_recovery_playbook(text: str) -> bool:
    low = text.lower()
    title = low.split("\n\n", 1)[0]
    return any(
        term in low
        for term in (
            "failure recovery scenario",
            "recovery needed",
            "inspect_failure_reason",
            "select_next_capability_path",
        )
    ) or any(term in title for term in ("recovery", "failed", "failure"))


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default
