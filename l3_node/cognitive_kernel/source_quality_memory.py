"""Source-quality memory for web research workflows.

This module keeps a small, local reputation index for web sources.  It is not a
replacement for the main Memory Growth review pipeline; it is a fast runtime
signal that helps search/fetch DAG nodes avoid repeatedly bad domains before a
human-readable playbook is grown from the raw evidence.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .memory_growth import append_raw_event, ensure_memory_growth_scaffold, memory_growth_dir


def record_web_research_source_quality(
    *,
    query: str,
    quality_report: dict[str, Any],
    sources: list[dict[str, Any]],
    turn_id: str = "",
    source: str = "web_research_quality_gate",
) -> Path | None:
    """Update domain reputation and append raw Memory Growth evidence."""

    ensure_memory_growth_scaffold()
    normalized_sources = _normalize_sources(sources)
    report = dict(quality_report or {})
    if not normalized_sources and int(report.get("source_count") or 0) <= 0:
        return None

    root = memory_growth_dir()
    index_path = _source_quality_index_path(root)
    index = _load_index(index_path)
    now_ms = int(time.time() * 1000)
    send_ready = report.get("send_ready") is True
    score = _float(report.get("score"), 0.0)
    issues = [str(item) for item in (report.get("issues") if isinstance(report.get("issues"), list) else []) if str(item).strip()]
    primary_issue = str(report.get("primary_issue") or (issues[0] if issues else "")).strip()

    for item in normalized_sources:
        domain = str(item.get("domain") or "")
        if not domain:
            continue
        row = index.setdefault("domains", {}).setdefault(
            domain,
            {
                "domain": domain,
                "use_count": 0,
                "success_count": 0,
                "failure_count": 0,
                "quality_score_sum": 0.0,
                "issue_counts": {},
                "last_seen_ms": 0,
                "last_query": "",
                "last_url": "",
                "last_title": "",
                "last_primary_issue": "",
            },
        )
        row["use_count"] = int(row.get("use_count") or 0) + 1
        row["success_count"] = int(row.get("success_count") or 0) + (1 if send_ready else 0)
        row["failure_count"] = int(row.get("failure_count") or 0) + (0 if send_ready else 1)
        row["quality_score_sum"] = round(_float(row.get("quality_score_sum"), 0.0) + score, 6)
        row["last_seen_ms"] = now_ms
        row["last_query"] = str(query or "")[:240]
        row["last_url"] = str(item.get("url") or "")[:500]
        row["last_title"] = str(item.get("title") or "")[:240]
        row["last_primary_issue"] = primary_issue[:120]
        issue_counts = row.setdefault("issue_counts", {})
        if isinstance(issue_counts, dict):
            for issue in issues:
                issue_counts[issue] = int(issue_counts.get(issue) or 0) + 1
        row.update(_domain_health(row))

    index["updated_at_ms"] = now_ms
    _write_index(index_path, index)

    try:
        return append_raw_event(
            category="evidence",
            source=source,
            stream="source_quality",
            payload={
                "turn_id": turn_id,
                "query": query,
                "quality_report": report,
                "sources": normalized_sources,
                "domain_reputation": [source_reputation_for_url(item.get("url", "")) for item in normalized_sources],
            },
            source_refs=[
                {
                    "type": "web_research_quality_report",
                    "turn_id": turn_id,
                    "query": query,
                }
            ],
            review={
                "review_candidate": True,
                "promotion_targets": ["playbooks", "concepts"],
                "priority": "high" if not send_ready else "normal",
                "reason": "web_research_source_quality_feedback",
            },
        )
    except Exception:
        return None


def source_reputation_for_url(url: str) -> dict[str, Any]:
    domain = domain_from_url(url)
    if not domain:
        return {
            "domain": "",
            "url": str(url or ""),
            "score": 0.5,
            "health": "unknown",
            "use_count": 0,
            "success_rate": 0.0,
            "average_quality_score": 0.0,
            "last_primary_issue": "",
        }
    row = _load_index(_source_quality_index_path(memory_growth_dir())).get("domains", {}).get(domain) or {}
    if not isinstance(row, dict) or not row:
        return {
            "domain": domain,
            "url": str(url or ""),
            "score": 0.5,
            "health": "unknown",
            "use_count": 0,
            "success_rate": 0.0,
            "average_quality_score": 0.0,
            "last_primary_issue": "",
        }
    health = _domain_health(row)
    return {
        "domain": domain,
        "url": str(url or ""),
        "score": health["reputation_score"],
        "health": health["health"],
        "use_count": int(row.get("use_count") or 0),
        "success_rate": health["success_rate"],
        "average_quality_score": health["average_quality_score"],
        "last_primary_issue": str(row.get("last_primary_issue") or ""),
    }


def rank_urls_by_source_quality(urls: list[str]) -> list[str]:
    indexed = list(enumerate([str(url).strip() for url in urls if str(url).strip()]))
    indexed.sort(key=lambda item: (_url_rank_score(item[1]), -item[0]), reverse=True)
    return list(dict.fromkeys(url for _idx, url in indexed))


def rank_findings_by_source_quality(findings: list[dict[str, object]]) -> list[dict[str, object]]:
    indexed = [(idx, item) for idx, item in enumerate(findings or []) if isinstance(item, dict)]
    indexed.sort(key=lambda item: (_url_rank_score(str(item[1].get("url") or "")), -item[0]), reverse=True)
    return [item for _idx, item in indexed]


def domain_from_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    except Exception:
        return ""
    host = (parsed.hostname or "").lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def _url_rank_score(url: str) -> float:
    rep = source_reputation_for_url(url)
    health = str(rep.get("health") or "")
    score = _float(rep.get("score"), 0.5)
    if health == "degraded":
        score -= 0.35
    if health == "reliable":
        score += 0.15
    return score


def _source_quality_index_path(root: Path) -> Path:
    path = root / "indexes" / "source_quality.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_index(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("schema_version", 1)
    data.setdefault("domains", {})
    return data


def _write_index(path: Path, index: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _normalize_sources(sources: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in sources or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        domain = domain_from_url(url)
        if not domain:
            continue
        out.append(
            {
                "domain": domain,
                "url": url,
                "title": str(item.get("title") or "")[:240],
                "source": str(item.get("source") or "")[:80],
            }
        )
    return out


def _domain_health(row: dict[str, Any]) -> dict[str, Any]:
    use_count = max(0, int(row.get("use_count") or 0))
    success_count = max(0, int(row.get("success_count") or 0))
    failure_count = max(0, int(row.get("failure_count") or 0))
    success_rate = round(success_count / use_count, 3) if use_count else 0.0
    avg_quality = round(_float(row.get("quality_score_sum"), 0.0) / use_count, 3) if use_count else 0.0
    issue_counts = row.get("issue_counts") if isinstance(row.get("issue_counts"), dict) else {}
    severe_issues = sum(
        int(issue_counts.get(issue) or 0)
        for issue in (
            "source_count_zero",
            "source_url_missing",
            "brief_contains_web_residue",
            "brief_contains_markdown_artifact",
            "brief_contains_ellipsis",
            "fetch_access_or_bot_wall",
        )
    )
    reputation = 0.45 * success_rate + 0.45 * avg_quality + 0.1 * min(1.0, use_count / 5.0)
    reputation -= min(0.35, severe_issues * 0.07)
    reputation = round(max(0.0, min(1.0, reputation)), 3)
    if use_count >= 2 and (success_rate < 0.45 or reputation < 0.42 or failure_count >= 3):
        health = "degraded"
    elif use_count >= 2 and success_rate >= 0.75 and reputation >= 0.68:
        health = "reliable"
    else:
        health = "unproven"
    return {
        "success_rate": success_rate,
        "average_quality_score": avg_quality,
        "reputation_score": reputation,
        "health": health,
    }


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
