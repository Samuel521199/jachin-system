"""Weekly lifecycle governance for Memory Growth.

Daily review grows knowledge. Weekly review keeps it sane: it scans concepts,
playbooks, outputs, conflicts, and indexes, then writes a lifecycle report with
dedupe candidates, stale facts, conflict pressure, and output quality issues.

This module is conservative by design. It does not delete or rewrite knowledge
pages automatically; it creates review evidence that a later curator or user can
accept. That keeps the knowledge system self-growing without becoming
self-corrupting.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import date as Date
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
from .memory_governance_auto_index import append_auto_governance_mode_history
from .memory_growth_strategy import persist_strategy_policy_to_artifacts


@dataclass(slots=True)
class WeeklyReviewResult:
    review_id: str
    week_id: str
    concept_count: int
    playbook_count: int
    output_count: int
    conflict_count: int
    duplicate_cluster_count: int
    stale_concept_count: int
    weak_output_count: int
    governance_action_count: int
    governance_batch_count: int
    governance_failed_count: int
    governance_effectiveness_score: int
    markdown_path: Path
    report_path: Path
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "week_id": self.week_id,
            "concept_count": self.concept_count,
            "playbook_count": self.playbook_count,
            "output_count": self.output_count,
            "conflict_count": self.conflict_count,
            "duplicate_cluster_count": self.duplicate_cluster_count,
            "stale_concept_count": self.stale_concept_count,
            "weak_output_count": self.weak_output_count,
            "governance_action_count": self.governance_action_count,
            "governance_batch_count": self.governance_batch_count,
            "governance_failed_count": self.governance_failed_count,
            "governance_effectiveness_score": self.governance_effectiveness_score,
            "markdown_path": str(self.markdown_path),
            "report_path": str(self.report_path),
            "warnings": list(self.warnings),
        }


def run_weekly_review(
    *,
    week_start: str | None = None,
    stale_after_days: int = 30,
) -> WeeklyReviewResult:
    """Run a conservative weekly lifecycle review over Memory Growth wiki."""

    ensure_memory_growth_scaffold()
    start = _normalize_week_start(week_start)
    week_id = _week_id(start)
    review_id = f"weekly_{week_id}_{uuid.uuid4().hex[:8]}"
    root = memory_growth_dir()

    concepts = _load_pages(root / "concepts", "*/*.md", page_type="concept")
    playbooks = _load_pages(root / "playbooks", "*.md", page_type="playbook")
    outputs = _load_pages(root / "outputs", "*/*.md", page_type="output")
    conflicts = _load_conflicts(root / "conflicts")
    governance_actions = _load_governance_actions(root / "reviews" / "governance")
    warnings = _index_warnings(root, concepts=concepts, playbooks=playbooks, outputs=outputs)

    duplicate_clusters = _duplicate_concept_clusters(concepts)
    stale_concepts = _stale_concepts(concepts, now=start, stale_after_days=stale_after_days)
    weak_outputs = _weak_outputs(outputs)
    failure_patterns = _failure_patterns(conflicts=conflicts, outputs=outputs, playbooks=playbooks)
    trust_governance_review = _trust_governance_status(root)
    memory_governance_auto = _memory_governance_auto_status(root)
    governance_effectiveness = _governance_effectiveness(
        governance_actions=governance_actions,
        conflicts=conflicts,
        failure_patterns=failure_patterns,
        trust_governance_review=trust_governance_review,
    )
    artifact_usage = _artifact_usage_analysis(root)

    report = {
        "schema_version": 1,
        "review_id": review_id,
        "week_id": week_id,
        "week_start": start.isoformat(),
        "generated_at": _iso_now(),
        "summary": {
            "concept_count": len(concepts),
            "playbook_count": len(playbooks),
            "output_count": len(outputs),
            "conflict_count": len(conflicts),
            "duplicate_cluster_count": len(duplicate_clusters),
            "stale_concept_count": len(stale_concepts),
            "weak_output_count": len(weak_outputs),
            "failure_pattern_count": len(failure_patterns),
            "governance_action_count": len(governance_actions),
            "governance_batch_count": sum(1 for item in governance_actions if item.get("is_batch")),
            "governance_failed_count": sum(int(item.get("failed_count") or 0) for item in governance_actions),
            "governance_effectiveness_score": governance_effectiveness["score"],
            "trust_governance_conversion_rate": trust_governance_review["summary"]["conversion_rate"],
            "trust_governance_pending_count": trust_governance_review["summary"]["pending_count"],
            "trust_governance_failed_count": trust_governance_review["summary"]["failed_count"],
            "trust_governance_follow_up_count": trust_governance_review["summary"].get("follow_up_count", 0),
            "trust_governance_next_action_count": trust_governance_review["summary"].get("next_action_count", 0),
            "memory_governance_auto_current_mode": memory_governance_auto.get("recommendation", {}).get("current_mode", ""),
            "memory_governance_auto_recommended_mode": memory_governance_auto.get("recommendation", {}).get("recommended_mode", ""),
            "memory_governance_auto_should_change": bool(memory_governance_auto.get("recommendation", {}).get("should_change")),
            "artifact_usage_count": artifact_usage["summary"]["artifact_count"],
            "artifact_total_use_count": artifact_usage["summary"]["total_use_count"],
            "artifact_success_rate": artifact_usage["summary"]["success_rate"],
            "artifact_low_success_count": len(artifact_usage["low_success_assets"]),
            "artifact_stale_unused_count": len(artifact_usage["stale_unused_assets"]),
            "warning_count": len(warnings),
        },
        "duplicate_concept_clusters": duplicate_clusters,
        "stale_concepts": stale_concepts,
        "weak_outputs": weak_outputs,
        "failure_patterns": failure_patterns,
        "governance_actions": governance_actions[:100],
        "governance_effectiveness": governance_effectiveness,
        "trust_governance_review": trust_governance_review,
        "memory_governance_next_actions": (trust_governance_review.get("next_actions") or [])[:8],
        "memory_governance_auto": memory_governance_auto,
        "artifact_usage": artifact_usage,
        "conflicts": conflicts[:100],
        "warnings": warnings,
        "recommendations": _recommendations(
            duplicate_clusters=duplicate_clusters,
            stale_concepts=stale_concepts,
            weak_outputs=weak_outputs,
            conflicts=conflicts,
            governance_actions=governance_actions,
            governance_effectiveness=governance_effectiveness,
            trust_governance_review=trust_governance_review,
            memory_governance_auto=memory_governance_auto,
            artifact_usage=artifact_usage,
            warnings=warnings,
        ),
    }

    reviews_dir = root / "reviews" / "weekly"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    report_path = reviews_dir / f"{week_id}.weekly_lifecycle.json"
    markdown_path = reviews_dir / f"{week_id}.md"
    auto_history = append_auto_governance_mode_history(
        source="weekly_review",
        date=start.isoformat(),
        recommendation=memory_governance_auto.get("recommendation") if isinstance(memory_governance_auto.get("recommendation"), dict) else {},
        auto_policy=memory_governance_auto.get("policy") if isinstance(memory_governance_auto.get("policy"), dict) else {},
        auto_result=memory_governance_auto.get("latest") if isinstance(memory_governance_auto.get("latest"), dict) else {},
        report_path=str(report_path),
    )
    report["memory_governance_auto_history"] = auto_history
    report["summary"]["memory_governance_auto_history_risk"] = (auto_history.get("summary") or {}).get("risk_direction", "")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    markdown_path.write_text(_render_markdown(report), encoding="utf-8")
    _write_lifecycle_index(report, report_path=report_path, markdown_path=markdown_path)
    _write_governance_effectiveness_index(report, report_path=report_path, markdown_path=markdown_path)
    _write_artifact_usage_trend_index(report, report_path=report_path, markdown_path=markdown_path)

    summary = report["summary"]
    return WeeklyReviewResult(
        review_id=review_id,
        week_id=week_id,
        concept_count=summary["concept_count"],
        playbook_count=summary["playbook_count"],
        output_count=summary["output_count"],
        conflict_count=summary["conflict_count"],
        duplicate_cluster_count=summary["duplicate_cluster_count"],
        stale_concept_count=summary["stale_concept_count"],
        weak_output_count=summary["weak_output_count"],
        governance_action_count=summary["governance_action_count"],
        governance_batch_count=summary["governance_batch_count"],
        governance_failed_count=summary["governance_failed_count"],
        governance_effectiveness_score=summary["governance_effectiveness_score"],
        markdown_path=markdown_path,
        report_path=report_path,
        warnings=warnings,
    )


def _load_pages(root: Path, pattern: str, *, page_type: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not root.exists():
        return rows
    memory_root = memory_growth_dir()
    for path in sorted(root.glob(pattern)):
        if path.name == "README.md":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        frontmatter = _frontmatter(text)
        body = _body(text)
        rows.append(
            {
                "type": page_type,
                "path": str(path.relative_to(memory_root)),
                "slug": path.stem,
                "summary": str(frontmatter.get("summary") or _first_heading(text) or path.stem).strip(),
                "frontmatter": frontmatter,
                "body_preview": body[:800],
                "source_ref_count": _source_ref_count(frontmatter, body),
                "confidence": _float(frontmatter.get("confidence")),
                "last_verified": str(frontmatter.get("last_verified") or frontmatter.get("created_at") or ""),
                "verification_status": str(frontmatter.get("verification_status") or ""),
            }
        )
    return rows


def _load_conflicts(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not root.exists():
        return rows
    memory_root = memory_growth_dir()
    for path in sorted(root.glob("**/*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            rows.append(
                {
                    "path": str(path.relative_to(memory_root)),
                    "reason": "invalid_conflict_json",
                    "error": exc.__class__.__name__,
                }
            )
            continue
        rows.append(
            {
                "path": str(path.relative_to(memory_root)),
                "reason": str(payload.get("reason") or ""),
                "candidate_id": str((payload.get("candidate") or {}).get("candidate_id") or ""),
                "date": str(payload.get("date") or ""),
            }
        )
    return rows


def _load_governance_actions(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not root.exists():
        return rows
    memory_root = memory_growth_dir()
    for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            rows.append(
                {
                    "path": str(path.relative_to(memory_root)),
                    "action": "invalid_governance_report",
                    "error": exc.__class__.__name__,
                    "failed_count": 1,
                    "is_batch": False,
                }
            )
            continue
        is_batch = "batch_id" in payload
        rows.append(
            {
                "path": str(path.relative_to(memory_root)),
                "id": str(payload.get("batch_id") or payload.get("governance_id") or path.stem),
                "action": str(payload.get("action") or ("batch_governance" if is_batch else "unknown")),
                "created_at": str(payload.get("created_at") or ""),
                "note": str(payload.get("note") or ""),
                "is_batch": is_batch,
                "executed_count": int(payload.get("executed_count") or (1 if payload.get("governance_id") else 0)),
                "failed_count": int(payload.get("failed_count") or 0),
                "side_effect_count": len(payload.get("side_effects") or []),
                "success_count": int(payload.get("executed_count") or (1 if payload.get("side_effects") else 0)),
            }
        )
    return rows


def _governance_effectiveness(
    *,
    governance_actions: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    failure_patterns: list[dict[str, Any]],
    trust_governance_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    action_count = len(governance_actions)
    success_count = sum(int(item.get("success_count") or 0) for item in governance_actions)
    failed_count = sum(int(item.get("failed_count") or 0) for item in governance_actions)
    conflict_pressure = len(conflicts)
    failure_pressure = sum(int(item.get("count") or 0) for item in failure_patterns if item.get("pattern") != "recovery_playbooks_exist")
    success_rate = round(success_count / max(1, success_count + failed_count), 3)
    trust_summary = trust_governance_review.get("summary") if isinstance(trust_governance_review, dict) else {}
    trust_conversion_rate = _float_value(trust_summary.get("conversion_rate"), 0.0) if isinstance(trust_summary, dict) else 0.0
    trust_converted_count = int(trust_summary.get("converted_count") or 0) if isinstance(trust_summary, dict) else 0
    trust_pending_count = int(trust_summary.get("pending_count") or 0) if isinstance(trust_summary, dict) else 0
    trust_failed_count = int(trust_summary.get("failed_count") or 0) if isinstance(trust_summary, dict) else 0
    score = 0
    if action_count:
        score = 35 + round(success_rate * 35) + min(20, success_count * 3)
        if trust_converted_count:
            score += min(10, round(trust_conversion_rate * 10))
        score -= min(20, failed_count * 5)
        score -= min(12, trust_pending_count * 2 + trust_failed_count * 4)
        score -= min(15, max(0, conflict_pressure + failure_pressure - success_count) // 2)
        score = max(0, min(100, score))
    grade = "no_data" if not action_count else "healthy" if score >= 80 else "watch" if score >= 60 else "weak"
    recommendations: list[str] = []
    if not action_count:
        recommendations.append("Run governance actions before the next review so effectiveness can be measured.")
    if failed_count:
        recommendations.append("Retry failed governance actions with a safer path or explicit user confirmation.")
    if trust_pending_count:
        recommendations.append("Trust-governance recommendations are pending; execute or dismiss them so they can become durable artifacts.")
    if trust_failed_count:
        recommendations.append("Trust-governance conversion failed; inspect side effects and retry with safer inputs.")
    if trust_converted_count and trust_conversion_rate >= 0.7:
        recommendations.append("Trust-governance conversion is healthy; keep comparing future conversion rates against this baseline.")
    if conflict_pressure > success_count:
        recommendations.append("Governance output is still lower than conflict pressure; prioritize confirmations and recovery playbooks.")
    if not recommendations:
        recommendations.append("Governance effectiveness is healthy; keep measuring conflict and failure pressure over time.")
    return {
        "score": score,
        "grade": grade,
        "action_count": action_count,
        "success_count": success_count,
        "failure_count": failed_count,
        "success_rate": success_rate,
        "trust_conversion_rate": trust_conversion_rate,
        "trust_converted_count": trust_converted_count,
        "trust_pending_count": trust_pending_count,
        "trust_failed_count": trust_failed_count,
        "conflict_pressure": conflict_pressure,
        "failure_pressure": failure_pressure,
        "recommendations": recommendations,
    }


def _trust_governance_status(root: Path) -> dict[str, Any]:
    """Read normalized trust-governance conversion status for weekly scoring."""

    fallback = {
        "summary": {
            "recommended_count": 0,
            "executed_count": 0,
            "converted_count": 0,
            "pending_count": 0,
            "failed_count": 0,
            "follow_up_count": 0,
            "next_action_count": 0,
            "conversion_rate": 0.0,
        },
        "pending": [],
        "converted": [],
        "failed": [],
        "follow_up_queue": [],
        "next_actions": [],
        "recent": [],
    }
    try:
        from l3_node.memory_growth_http import memory_growth_status

        monitoring = memory_growth_status().get("monitoring")
        review = monitoring.get("trust_governance_review") if isinstance(monitoring, dict) else None
        if isinstance(review, dict) and isinstance(review.get("summary"), dict):
            return review
    except Exception:
        pass
    return fallback


def _memory_governance_auto_status(root: Path) -> dict[str, Any]:
    fallback = {
        "policy": {},
        "latest": {},
        "trends": {"days_7": [], "days_14": [], "days_30": []},
        "recommendation": {
            "current_mode": "",
            "recommended_mode": "",
            "should_change": False,
            "reasons": [],
            "metrics": {},
        },
    }
    try:
        from l3_node.memory_growth_http import memory_growth_status

        monitoring = memory_growth_status().get("monitoring")
        if not isinstance(monitoring, dict):
            return fallback
        return {
            "policy": monitoring.get("memory_governance_auto_policy") if isinstance(monitoring.get("memory_governance_auto_policy"), dict) else {},
            "latest": monitoring.get("memory_governance_auto_latest") if isinstance(monitoring.get("memory_governance_auto_latest"), dict) else {},
            "trends": monitoring.get("memory_governance_auto_trends") if isinstance(monitoring.get("memory_governance_auto_trends"), dict) else fallback["trends"],
            "recommendation": monitoring.get("memory_governance_auto_recommendation") if isinstance(monitoring.get("memory_governance_auto_recommendation"), dict) else fallback["recommendation"],
        }
    except Exception:
        return fallback


def _duplicate_concept_clusters(concepts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for concept in concepts:
        fingerprint = _fingerprint(str(concept.get("summary") or ""))
        if not fingerprint:
            continue
        buckets.setdefault(fingerprint, []).append(concept)
    clusters: list[dict[str, Any]] = []
    for fingerprint, items in sorted(buckets.items()):
        if len(items) <= 1:
            continue
        clusters.append(
            {
                "fingerprint": fingerprint,
                "count": len(items),
                "paths": [item["path"] for item in items],
                "summaries": [item["summary"] for item in items],
                "recommendation": "merge_or_link_duplicate_concepts",
            }
        )
    return clusters


def _stale_concepts(concepts: list[dict[str, Any]], *, now: Date, stale_after_days: int) -> list[dict[str, Any]]:
    stale: list[dict[str, Any]] = []
    threshold = now - timedelta(days=max(1, stale_after_days))
    for concept in concepts:
        verified = _parse_date(str(concept.get("last_verified") or ""))
        valid_until = _parse_date(str((concept.get("frontmatter") or {}).get("valid_until") or ""))
        if valid_until and valid_until < now:
            stale.append({**_thin_page(concept), "reason": "valid_until_expired", "valid_until": valid_until.isoformat()})
            continue
        if verified and verified < threshold:
            stale.append({**_thin_page(concept), "reason": "last_verified_stale", "last_verified": verified.isoformat()})
    return stale


def _weak_outputs(outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    weak: list[dict[str, Any]] = []
    for output in outputs:
        reasons: list[str] = []
        if output.get("source_ref_count", 0) <= 0:
            reasons.append("missing_source_refs")
        if str(output.get("verification_status") or "").lower() == "failed":
            reasons.append("failed_output")
        if len(str(output.get("body_preview") or "").strip()) < 40:
            reasons.append("too_short")
        if output.get("confidence", 0.0) < 0.3:
            reasons.append("low_confidence")
        if reasons:
            weak.append({**_thin_page(output), "reasons": reasons})
    return weak


def _failure_patterns(
    *,
    conflicts: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
    playbooks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    counter: dict[str, int] = {}
    for item in conflicts:
        reason = str(item.get("reason") or "unknown_conflict")
        counter[reason] = counter.get(reason, 0) + 1
    failed_outputs = [item for item in outputs if str(item.get("verification_status") or "").lower() == "failed"]
    if failed_outputs:
        counter["failed_user_facing_outputs"] = counter.get("failed_user_facing_outputs", 0) + len(failed_outputs)
    recovery_playbooks = [item for item in playbooks if "recovery" in str(item.get("summary") or "").lower()]
    if recovery_playbooks:
        counter["recovery_playbooks_exist"] = counter.get("recovery_playbooks_exist", 0) + len(recovery_playbooks)
    return [
        {"pattern": key, "count": count, "recommendation": _failure_recommendation(key)}
        for key, count in sorted(counter.items(), key=lambda x: (-x[1], x[0]))
    ]


def _index_warnings(
    root: Path,
    *,
    concepts: list[dict[str, Any]],
    playbooks: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
) -> list[str]:
    warnings: list[str] = []
    checks = [
        ("indexes/concepts.json", len(concepts), "concepts"),
        ("indexes/playbooks.json", len(playbooks), "playbooks"),
        ("indexes/outputs.json", len(outputs), "outputs"),
    ]
    for rel, count, key in checks:
        path = root / rel
        if count and not path.exists():
            warnings.append(f"missing_index:{rel}")
            continue
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            warnings.append(f"invalid_index_json:{rel}")
            continue
        rows = payload.get(key)
        if not isinstance(rows, list):
            warnings.append(f"invalid_index_rows:{rel}")
        elif len(rows) != count:
            warnings.append(f"index_count_mismatch:{rel}:{len(rows)}!={count}")
    return warnings


def _recommendations(
    *,
    duplicate_clusters: list[dict[str, Any]],
    stale_concepts: list[dict[str, Any]],
    weak_outputs: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    governance_actions: list[dict[str, Any]],
    governance_effectiveness: dict[str, Any],
    trust_governance_review: dict[str, Any],
    memory_governance_auto: dict[str, Any],
    artifact_usage: dict[str, Any],
    warnings: list[str],
) -> list[str]:
    out: list[str] = []
    if duplicate_clusters:
        out.append("Merge or cross-link duplicate concept pages before increasing recall weight.")
    if stale_concepts:
        out.append("Re-verify stale concepts or lower their recall priority.")
    if weak_outputs:
        out.append("Review weak outputs before promoting them into concepts or playbooks.")
    if conflicts:
        out.append("Triage conflict queue; repeated conflict reasons should become explicit failure playbooks.")
    if governance_actions:
        failed = sum(int(item.get("failed_count") or 0) for item in governance_actions)
        if failed:
            out.append("Review failed governance actions; batch governance should surface safe retry paths before the next weekly review.")
        else:
            out.append("Governance actions are being recorded; compare future conflict pressure to confirm they reduce repeated failures.")
    else:
        out.append("No governance actions were recorded this week; use the Memory Growth console to confirm, archive, or convert failures into playbooks.")
    if warnings:
        out.append("Repair index warnings before relying on Memory Growth recall in production.")
    trust_summary = trust_governance_review.get("summary") if isinstance(trust_governance_review, dict) else {}
    trust_pending = int(trust_summary.get("pending_count") or 0) if isinstance(trust_summary, dict) else 0
    trust_failed = int(trust_summary.get("failed_count") or 0) if isinstance(trust_summary, dict) else 0
    trust_converted = int(trust_summary.get("converted_count") or 0) if isinstance(trust_summary, dict) else 0
    trust_next_actions = int(trust_summary.get("next_action_count") or 0) if isinstance(trust_summary, dict) else 0
    trust_conversion_rate = _float_value(trust_summary.get("conversion_rate"), 0.0) if isinstance(trust_summary, dict) else 0.0
    if trust_pending:
        out.append("Execute or dismiss pending trust-governance recommendations; unresolved trust work should not drift between reviews.")
    if trust_failed:
        out.append("Retry failed trust-governance actions and check whether the expected durable artifact was created.")
    if trust_next_actions:
        out.append("Run the memory governance next-action list before the next review; it contains executable follow-ups for pending or failed trust work.")
    if trust_converted and trust_conversion_rate >= 0.7:
        out.append("Trust-governance conversion is healthy; use it as a baseline for future memory-quality work.")
    auto_rec = memory_governance_auto.get("recommendation") if isinstance(memory_governance_auto.get("recommendation"), dict) else {}
    if auto_rec.get("should_change"):
        out.append(
            "Memory governance auto mode should be reviewed: "
            f"{auto_rec.get('current_mode') or '-'} -> {auto_rec.get('recommended_mode') or '-'} "
            f"because {', '.join(str(item) for item in (auto_rec.get('reasons') or [])[:2])}."
        )
    elif auto_rec.get("recommended_mode"):
        out.append(f"Memory governance auto mode recommendation is stable: keep `{auto_rec.get('recommended_mode')}`.")
    score = int(governance_effectiveness.get("score") or 0)
    if governance_actions and score < 60:
        out.append("Governance effectiveness is weak; connect more failed patterns to recovery playbooks and retry failed governance items.")
    elif governance_actions and score >= 80:
        out.append("Governance effectiveness is healthy; use this score as a baseline for the next weekly review.")
    artifact_summary = artifact_usage.get("summary") if isinstance(artifact_usage.get("summary"), dict) else {}
    if int(artifact_summary.get("artifact_count") or 0) <= 0:
        out.append("No artifact usage was recorded this week; make sure DecisionContract memory_context_refs reach TurnClosure.")
    if artifact_usage.get("low_success_assets"):
        out.append("Rewrite or downrank low-success knowledge assets before they keep influencing execution.")
    if artifact_usage.get("stale_unused_assets"):
        out.append("Archive or revalidate stale unused knowledge assets to keep recall focused.")
    if artifact_usage.get("top_successful_assets"):
        out.append("Promote top successful playbooks as preferred recovery or execution guidance.")
    if not out:
        out.append("No major lifecycle issues found; keep weekly review running.")
    return out


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _write_lifecycle_index(report: dict[str, Any], *, report_path: Path, markdown_path: Path) -> None:
    root = memory_growth_dir()
    path = root / "indexes" / "weekly_lifecycle.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "latest_review": {
            "review_id": report["review_id"],
            "week_id": report["week_id"],
            "report_path": str(report_path.relative_to(root)),
            "markdown_path": str(markdown_path.relative_to(root)),
            "summary": report["summary"],
        },
        "recommendations": report["recommendations"],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _write_governance_effectiveness_index(report: dict[str, Any], *, report_path: Path, markdown_path: Path) -> None:
    root = memory_growth_dir()
    path = root / "indexes" / "governance_effectiveness.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            existing = loaded if isinstance(loaded, dict) else {}
        except Exception:
            existing = {}
    history = existing.get("history") if isinstance(existing.get("history"), list) else []
    effectiveness = report.get("governance_effectiveness") if isinstance(report.get("governance_effectiveness"), dict) else {}
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    item = {
        "week_id": str(report.get("week_id") or ""),
        "week_start": str(report.get("week_start") or ""),
        "generated_at": str(report.get("generated_at") or ""),
        "score": int(effectiveness.get("score") or summary.get("governance_effectiveness_score") or 0),
        "grade": str(effectiveness.get("grade") or "no_data"),
        "action_count": int(effectiveness.get("action_count") or summary.get("governance_action_count") or 0),
        "success_count": int(effectiveness.get("success_count") or 0),
        "failure_count": int(effectiveness.get("failure_count") or summary.get("governance_failed_count") or 0),
        "success_rate": float(effectiveness.get("success_rate") or 0.0),
        "trust_conversion_rate": float(effectiveness.get("trust_conversion_rate") or summary.get("trust_governance_conversion_rate") or 0.0),
        "trust_converted_count": int(effectiveness.get("trust_converted_count") or 0),
        "trust_pending_count": int(effectiveness.get("trust_pending_count") or summary.get("trust_governance_pending_count") or 0),
        "trust_failed_count": int(effectiveness.get("trust_failed_count") or summary.get("trust_governance_failed_count") or 0),
        "conflict_pressure": int(effectiveness.get("conflict_pressure") or summary.get("conflict_count") or 0),
        "failure_pressure": int(effectiveness.get("failure_pressure") or summary.get("failure_pattern_count") or 0),
        "report_path": str(report_path.relative_to(root)),
        "markdown_path": str(markdown_path.relative_to(root)),
    }
    next_history = [row for row in history if not isinstance(row, dict) or row.get("week_id") != item["week_id"]]
    next_history.append(item)
    next_history = sorted(next_history, key=lambda row: str(row.get("week_start") or row.get("generated_at") or ""), reverse=True)[:104]
    attribution = _governance_effectiveness_attribution(report)
    strategy_policy = _governance_strategy_policy(attribution=attribution, latest=item, history=next_history)
    payload = {
        "schema_version": 1,
        "updated_at": _iso_now(),
        "latest": item,
        "history": next_history,
        "attribution": attribution,
        "strategy_policy": strategy_policy,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    persist_strategy_policy_to_artifacts(root, strategy_policy)


def _governance_effectiveness_attribution(report: dict[str, Any]) -> dict[str, Any]:
    actions = report.get("governance_actions") if isinstance(report.get("governance_actions"), list) else []
    by_action: dict[str, dict[str, Any]] = {}
    repeated_failures: list[dict[str, Any]] = []
    for item in actions:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "unknown")
        bucket = by_action.setdefault(action, {"action": action, "count": 0, "success_count": 0, "failure_count": 0, "paths": []})
        executed = int(item.get("executed_count") or 0)
        failed = int(item.get("failed_count") or 0)
        bucket["count"] += max(1, executed + failed)
        bucket["success_count"] += executed
        bucket["failure_count"] += failed
        if item.get("path"):
            bucket["paths"].append(item.get("path"))
        if failed:
            repeated_failures.append({"action": action, "path": item.get("path"), "failed_count": failed})
    effective = [row for row in by_action.values() if int(row.get("success_count") or 0) > 0 and int(row.get("failure_count") or 0) == 0]
    ineffective = [row for row in by_action.values() if int(row.get("failure_count") or 0) > 0]
    effective.sort(key=lambda row: (-int(row.get("success_count") or 0), str(row.get("action"))))
    ineffective.sort(key=lambda row: (-int(row.get("failure_count") or 0), str(row.get("action"))))
    return {
        "effective_actions": effective[:10],
        "ineffective_actions": ineffective[:10],
        "repeated_failures": repeated_failures[:10],
    }


def _artifact_usage_analysis(root: Path) -> dict[str, Any]:
    rows = _artifact_usage_rows(root)
    total_use = sum(int(row.get("memory_use_count") or 0) for row in rows)
    success_count = sum(int(row.get("memory_success_count") or 0) for row in rows)
    failure_count = sum(int(row.get("memory_failure_count") or 0) for row in rows)
    success_rate = round(success_count / max(1, success_count + failure_count), 3)
    low_success = [
        {
            **_thin_artifact(row),
            "reason": "low_success_rate",
            "recommendation": "rewrite_or_downrank",
        }
        for row in rows
        if int(row.get("memory_use_count") or 0) >= 2 and float(row.get("memory_success_rate") or 0.0) < 0.5
    ]
    high_failure = [
        {
            **_thin_artifact(row),
            "reason": str(row.get("memory_last_failure_reason") or "repeated_failure"),
            "recommendation": "convert_failure_into_recovery_playbook_or_request_review",
        }
        for row in rows
        if int(row.get("memory_failure_count") or 0) >= 2
    ]
    stale_unused = [
        {
            **_thin_artifact(row),
            "reason": "not_used_recently",
            "recommendation": "archive_or_revalidate",
        }
        for row in rows
        if int(row.get("memory_use_count") or 0) == 0 or _is_stale_used(row.get("memory_last_used_at"))
    ]
    top_successful = [
        {
            **_thin_artifact(row),
            "recommendation": "promote_as_preferred_guidance",
        }
        for row in rows
        if int(row.get("memory_use_count") or 0) > 0 and float(row.get("memory_success_rate") or 0.0) >= 0.75
    ]
    low_success.sort(key=lambda row: (float(row.get("memory_success_rate") or 0.0), -int(row.get("memory_use_count") or 0), str(row.get("path") or "")))
    high_failure.sort(key=lambda row: (-int(row.get("memory_failure_count") or 0), str(row.get("path") or "")))
    stale_unused.sort(key=lambda row: (str(row.get("memory_last_used_at") or ""), str(row.get("path") or "")))
    top_successful.sort(key=lambda row: (-int(row.get("memory_success_count") or 0), -int(row.get("memory_use_count") or 0), str(row.get("path") or "")))
    return {
        "summary": {
            "artifact_count": len(rows),
            "active_artifact_count": sum(1 for row in rows if int(row.get("memory_use_count") or 0) > 0),
            "total_use_count": total_use,
            "success_count": success_count,
            "failure_count": failure_count,
            "success_rate": success_rate,
        },
        "top_successful_assets": top_successful[:10],
        "low_success_assets": low_success[:10],
        "high_failure_assets": high_failure[:10],
        "stale_unused_assets": stale_unused[:10],
    }


def _artifact_usage_rows(root: Path) -> list[dict[str, Any]]:
    path = root / "indexes" / "artifact_usage.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = payload.get("artifacts") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "path": str(row.get("path") or ""),
                "id": str(row.get("id") or ""),
                "type": str(row.get("type") or ""),
                "summary": str(row.get("summary") or ""),
                "memory_use_count": int(_float(row.get("memory_use_count"))),
                "memory_success_count": int(_float(row.get("memory_success_count"))),
                "memory_failure_count": int(_float(row.get("memory_failure_count"))),
                "memory_success_rate": _float(row.get("memory_success_rate")),
                "memory_last_used_at": str(row.get("memory_last_used_at") or ""),
                "memory_last_failure_reason": str(row.get("memory_last_failure_reason") or ""),
            }
        )
    out.sort(key=lambda row: (-int(row.get("memory_use_count") or 0), -float(row.get("memory_success_rate") or 0.0), str(row.get("path") or "")))
    return out


def _thin_artifact(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": row.get("path"),
        "id": row.get("id"),
        "type": row.get("type"),
        "summary": row.get("summary"),
        "memory_use_count": int(row.get("memory_use_count") or 0),
        "memory_success_count": int(row.get("memory_success_count") or 0),
        "memory_failure_count": int(row.get("memory_failure_count") or 0),
        "memory_success_rate": float(row.get("memory_success_rate") or 0.0),
        "memory_last_used_at": row.get("memory_last_used_at") or "",
        "memory_last_failure_reason": row.get("memory_last_failure_reason") or "",
    }


def _is_stale_used(value: Any, *, stale_after_days: int = 30) -> bool:
    parsed = _parse_date(str(value or ""))
    if not parsed:
        return False
    return parsed < (datetime.now().date() - timedelta(days=stale_after_days))


def _write_artifact_usage_trend_index(report: dict[str, Any], *, report_path: Path, markdown_path: Path) -> None:
    root = memory_growth_dir()
    path = root / "indexes" / "artifact_usage_trends.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            existing = loaded if isinstance(loaded, dict) else {}
        except Exception:
            existing = {}
    history = existing.get("history") if isinstance(existing.get("history"), list) else []
    artifact_usage = report.get("artifact_usage") if isinstance(report.get("artifact_usage"), dict) else {}
    summary = artifact_usage.get("summary") if isinstance(artifact_usage.get("summary"), dict) else {}
    item = {
        "week_id": str(report.get("week_id") or ""),
        "week_start": str(report.get("week_start") or ""),
        "generated_at": str(report.get("generated_at") or ""),
        "artifact_count": int(summary.get("artifact_count") or 0),
        "active_artifact_count": int(summary.get("active_artifact_count") or 0),
        "total_use_count": int(summary.get("total_use_count") or 0),
        "success_count": int(summary.get("success_count") or 0),
        "failure_count": int(summary.get("failure_count") or 0),
        "success_rate": float(summary.get("success_rate") or 0.0),
        "low_success_count": len(artifact_usage.get("low_success_assets") or []),
        "high_failure_count": len(artifact_usage.get("high_failure_assets") or []),
        "stale_unused_count": len(artifact_usage.get("stale_unused_assets") or []),
        "report_path": str(report_path.relative_to(root)),
        "markdown_path": str(markdown_path.relative_to(root)),
    }
    next_history = [row for row in history if not isinstance(row, dict) or row.get("week_id") != item["week_id"]]
    next_history.append(item)
    next_history = sorted(next_history, key=lambda row: str(row.get("week_start") or row.get("generated_at") or ""), reverse=True)[:104]
    attribution = _artifact_usage_attribution(artifact_usage)
    payload = {
        "schema_version": 1,
        "updated_at": _iso_now(),
        "latest": item,
        "history": next_history,
        "attribution": attribution,
        "recommendations": _artifact_usage_recommendations(artifact_usage),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _artifact_usage_attribution(artifact_usage: dict[str, Any]) -> dict[str, Any]:
    return {
        "best_playbooks": [
            row for row in artifact_usage.get("top_successful_assets") or []
            if isinstance(row, dict) and "playbook" in str(row.get("type") or row.get("path") or "").lower()
        ][:10],
        "top_successful_assets": list(artifact_usage.get("top_successful_assets") or [])[:10],
        "low_success_assets": list(artifact_usage.get("low_success_assets") or [])[:10],
        "high_failure_assets": list(artifact_usage.get("high_failure_assets") or [])[:10],
        "stale_unused_assets": list(artifact_usage.get("stale_unused_assets") or [])[:10],
    }


def _artifact_usage_recommendations(artifact_usage: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in artifact_usage.get("low_success_assets") or []:
        if isinstance(item, dict):
            rows.append({"action": "rewrite_or_downrank", "target": item.get("path"), "reason": item.get("reason"), "priority": "high"})
    for item in artifact_usage.get("high_failure_assets") or []:
        if isinstance(item, dict):
            rows.append({"action": "create_or_update_recovery_playbook", "target": item.get("path"), "reason": item.get("reason"), "priority": "high"})
    for item in artifact_usage.get("stale_unused_assets") or []:
        if isinstance(item, dict):
            rows.append({"action": "archive_or_revalidate", "target": item.get("path"), "reason": item.get("reason"), "priority": "medium"})
    for item in artifact_usage.get("top_successful_assets") or []:
        if isinstance(item, dict):
            rows.append({"action": "promote_preferred_guidance", "target": item.get("path"), "reason": "high_success_asset", "priority": "low"})
    return rows[:20]


def _governance_strategy_policy(*, attribution: dict[str, Any], latest: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    action_policy: dict[str, dict[str, Any]] = {}
    for row in attribution.get("effective_actions") or []:
        if not isinstance(row, dict):
            continue
        action = str(row.get("action") or "")
        if not action:
            continue
        success = int(row.get("success_count") or 0)
        action_policy[action] = {
            "weight": min(1.6, 1.0 + success * 0.12),
            "execution_mode": "batch_ok",
            "requires_more_evidence": False,
            "reason": "recent_governance_effective",
        }
    for row in attribution.get("ineffective_actions") or []:
        if not isinstance(row, dict):
            continue
        action = str(row.get("action") or "")
        if not action:
            continue
        failed = int(row.get("failure_count") or row.get("failed_count") or 1)
        previous = action_policy.get(action, {})
        action_policy[action] = {
            "weight": max(0.45, float(previous.get("weight") or 1.0) - failed * 0.18),
            "execution_mode": "manual_review",
            "requires_more_evidence": True,
            "reason": "recent_governance_failed",
        }
    trend_delta = _effectiveness_score_delta(history)
    latest_score = int(latest.get("score") or 0)
    global_mode = "normal"
    if latest_score and latest_score < 60:
        global_mode = "cautious"
    elif trend_delta > 8:
        global_mode = "accelerate"
    elif trend_delta < -8:
        global_mode = "cautious"
    return {
        "schema_version": 1,
        "latest_score": latest_score,
        "trend_delta": trend_delta,
        "global_mode": global_mode,
        "action_policy": action_policy,
    }


def _effectiveness_score_delta(history: list[dict[str, Any]]) -> int:
    rows = [row for row in history if isinstance(row, dict)]
    rows.sort(key=lambda row: str(row.get("week_start") or row.get("generated_at") or ""))
    if len(rows) < 2:
        return 0
    return int(rows[-1].get("score") or 0) - int(rows[-2].get("score") or 0)


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        f"# Weekly Memory Growth Review {report['week_id']}",
        "",
        "## Summary",
        "",
        f"- Concepts: {summary['concept_count']}",
        f"- Playbooks: {summary['playbook_count']}",
        f"- Outputs: {summary['output_count']}",
        f"- Conflicts: {summary['conflict_count']}",
        f"- Duplicate concept clusters: {summary['duplicate_cluster_count']}",
        f"- Stale concepts: {summary['stale_concept_count']}",
        f"- Weak outputs: {summary['weak_output_count']}",
        f"- Governance actions: {summary['governance_action_count']}",
        f"- Governance batches: {summary['governance_batch_count']}",
        f"- Governance failed items: {summary['governance_failed_count']}",
        f"- Governance effectiveness score: {summary['governance_effectiveness_score']}",
        f"- Memory governance auto mode: {summary.get('memory_governance_auto_current_mode') or '-'}",
        f"- Memory governance auto recommended mode: {summary.get('memory_governance_auto_recommended_mode') or '-'}",
        f"- Memory governance auto should change: {bool(summary.get('memory_governance_auto_should_change'))}",
        f"- Memory governance auto history risk: {summary.get('memory_governance_auto_history_risk') or '-'}",
        f"- Artifact usage count: {summary.get('artifact_usage_count', 0)}",
        f"- Artifact total uses: {summary.get('artifact_total_use_count', 0)}",
        f"- Artifact success rate: {summary.get('artifact_success_rate', 0)}",
        f"- Low-success artifacts: {summary.get('artifact_low_success_count', 0)}",
        f"- Stale unused artifacts: {summary.get('artifact_stale_unused_count', 0)}",
        "",
        "## Recommendations",
        "",
    ]
    lines.extend(f"- {item}" for item in report["recommendations"])
    lines.extend(["", "## Duplicate Concepts", ""])
    lines.extend(_render_list(report["duplicate_concept_clusters"], empty="- None."))
    lines.extend(["", "## Stale Concepts", ""])
    lines.extend(_render_list(report["stale_concepts"], empty="- None."))
    lines.extend(["", "## Weak Outputs", ""])
    lines.extend(_render_list(report["weak_outputs"], empty="- None."))
    lines.extend(["", "## Failure Patterns", ""])
    lines.extend(_render_list(report["failure_patterns"], empty="- None."))
    lines.extend(["", "## Governance Actions", ""])
    lines.extend(_render_list(report["governance_actions"], empty="- None."))
    lines.extend(["", "## Governance Effectiveness", ""])
    lines.extend(_render_list([report["governance_effectiveness"]], empty="- None."))
    lines.extend(["", "## Memory Governance Auto Recommendation", ""])
    auto = report.get("memory_governance_auto") if isinstance(report.get("memory_governance_auto"), dict) else {}
    lines.extend(_render_list([auto.get("recommendation") or {}], empty="- None."))
    lines.extend(["", "## Memory Governance Auto History", ""])
    auto_history = report.get("memory_governance_auto_history") if isinstance(report.get("memory_governance_auto_history"), dict) else {}
    lines.extend(_render_list([auto_history.get("summary") or {}], empty="- None."))
    lines.extend(["", "## Artifact Usage", ""])
    artifact_usage = report.get("artifact_usage") if isinstance(report.get("artifact_usage"), dict) else {}
    lines.extend(_render_list([artifact_usage.get("summary") or {}], empty="- None."))
    lines.extend(["", "### Top Successful Assets", ""])
    lines.extend(_render_list(list(artifact_usage.get("top_successful_assets") or []), empty="- None."))
    lines.extend(["", "### Low Success Assets", ""])
    lines.extend(_render_list(list(artifact_usage.get("low_success_assets") or []), empty="- None."))
    lines.extend(["", "### Stale Unused Assets", ""])
    lines.extend(_render_list(list(artifact_usage.get("stale_unused_assets") or []), empty="- None."))
    lines.extend([""])
    return "\n".join(lines)


def _render_list(items: list[dict[str, Any]], *, empty: str) -> list[str]:
    if not items:
        return [empty]
    return [f"- `{json.dumps(item, ensure_ascii=False, default=str)}`" for item in items[:30]]


def _thin_page(page: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": page.get("path"),
        "summary": page.get("summary"),
        "confidence": page.get("confidence"),
    }


def _frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}
    body = text[4:end]
    out: dict[str, Any] = {}
    for line in body.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        raw = value.strip()
        try:
            out[key.strip()] = json.loads(raw)
        except Exception:
            out[key.strip()] = raw.strip().strip('"')
    return out


def _body(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end == -1:
        return text
    return text[end + 5 :]


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _source_ref_count(frontmatter: dict[str, Any], body: str) -> int:
    refs = frontmatter.get("source_refs")
    if isinstance(refs, list):
        return len(refs)
    return body.count('"type"') + body.count("raw_event")


def _fingerprint(text: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9_\u4e00-\u9fff]+", text.lower())
    stop = {"the", "a", "an", "and", "or", "to", "of", "in", "is", "are", "task"}
    key = [token for token in tokens if token not in stop][:10]
    return "-".join(key)


def _failure_recommendation(pattern: str) -> str:
    if "low_confidence" in pattern:
        return "Collect more source evidence before promotion."
    if "requires_user_confirmation" in pattern:
        return "Ask user or create confirmation workflow before promotion."
    if "failed" in pattern:
        return "Create or update recovery playbook with verified fallback path."
    return "Review repeated pattern and decide whether it should become a playbook."


def _normalize_week_start(value: str | None) -> Date:
    if value:
        return datetime.strptime(value, "%Y-%m-%d").date()
    today = datetime.now().date()
    return today - timedelta(days=today.weekday())


def _week_id(start: Date) -> str:
    iso = start.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _parse_date(value: str) -> Date | None:
    if not value:
        return None
    text = value[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except Exception:
        return None


def _float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")
