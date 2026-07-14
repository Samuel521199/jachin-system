"""HTTP API for the Memory Growth control surface.

These routes expose the self-growing knowledge pipeline to the desktop console
without requiring the UI to import Python internals.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter, defaultdict
from datetime import date as Date
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _json_response(data: Any, status: int = 200):
    import aiohttp.web

    return aiohttp.web.json_response(data, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


async def _json_body(request: Any) -> dict[str, Any]:
    try:
        if getattr(request, "body_exists", False):
            body = await request.json()
            return body if isinstance(body, dict) else {}
    except Exception:
        return {}
    return {}


async def handle_memory_growth_status(request: Any) -> Any:
    """GET /api/v1/memory-growth/status"""

    try:
        return _json_response({"ok": True, **memory_growth_status()})
    except Exception as e:
        logger.warning("[MemoryGrowth HTTP] status failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def handle_memory_growth_pipeline(request: Any) -> Any:
    """POST /api/v1/memory-growth/pipeline"""

    body = await _json_body(request)
    try:
        from l3_node.cognitive_kernel.growth_scheduler import run_growth_pipeline

        result = run_growth_pipeline(
            date=_optional_str(body.get("date")),
            promote_concepts=_optional_bool(body.get("promote_concepts"), True),
            build_playbooks=_optional_bool(body.get("build_playbooks"), True),
            review_outputs=_optional_bool(body.get("review_outputs"), True),
            weekly_lifecycle_review=_optional_bool(body.get("weekly_lifecycle_review"), False),
            sync_graph=_optional_bool(body.get("sync_graph"), False),
            graph_connector_ids=_string_list(body.get("graph_connector_ids")),
        )
        return _json_response({"ok": True, "result": result.to_dict(), "status": memory_growth_status()})
    except Exception as e:
        logger.exception("[MemoryGrowth HTTP] pipeline failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def handle_memory_growth_weekly_review(request: Any) -> Any:
    """POST /api/v1/memory-growth/weekly-review"""

    body = await _json_body(request)
    try:
        from l3_node.cognitive_kernel.weekly_review import run_weekly_review

        result = run_weekly_review(
            week_start=_optional_str(body.get("week_start")),
            stale_after_days=int(body.get("stale_after_days") or 30),
        )
        return _json_response({"ok": True, "result": result.to_dict(), "status": memory_growth_status()})
    except Exception as e:
        logger.exception("[MemoryGrowth HTTP] weekly review failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def handle_memory_growth_graph_sync(request: Any) -> Any:
    """POST /api/v1/memory-growth/graph-sync"""

    try:
        from l3_node.cognitive_kernel.graph_sync_adapter import sync_memory_growth_graph

        result = sync_memory_growth_graph()
        return _json_response({"ok": True, "result": result.to_dict(), "status": memory_growth_status()})
    except Exception as e:
        logger.exception("[MemoryGrowth HTTP] graph sync failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def handle_memory_growth_connector_sync(request: Any) -> Any:
    """POST /api/v1/memory-growth/connector-sync"""

    body = await _json_body(request)
    try:
        from l3_node.cognitive_kernel.graph_connectors import sync_graph_engine_connectors

        results = sync_graph_engine_connectors(_string_list(body.get("graph_connector_ids")))
        return _json_response(
            {
                "ok": True,
                "results": [item.to_dict() for item in results],
                "status": memory_growth_status(),
            }
        )
    except Exception as e:
        logger.exception("[MemoryGrowth HTTP] connector sync failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def handle_memory_growth_governance(request: Any) -> Any:
    """POST /api/v1/memory-growth/governance"""

    body = await _json_body(request)
    try:
        result = apply_memory_growth_governance(
            action=str(body.get("action") or "").strip(),
            item=body.get("item") if isinstance(body.get("item"), dict) else {},
            note=str(body.get("note") or "").strip(),
            days=int(body.get("days") or 14),
        )
        return _json_response({"ok": True, "result": result, "status": memory_growth_status()})
    except Exception as e:
        logger.exception("[MemoryGrowth HTTP] governance action failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_memory_growth_batch_governance(request: Any) -> Any:
    """POST /api/v1/memory-growth/batch-governance"""

    body = await _json_body(request)
    try:
        result = apply_memory_growth_batch_governance(
            operations=body.get("operations"),
            action=_optional_str(body.get("action")),
            items=body.get("items"),
            note=str(body.get("note") or ""),
            days=int(body.get("days") or 14),
            max_items=int(body.get("max_items") or 10),
        )
        return _json_response({"ok": True, "result": result, "status": memory_growth_status()})
    except Exception as e:
        logger.exception("[MemoryGrowth HTTP] batch governance failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_memory_growth_artifact_curator(request: Any) -> Any:
    """POST /api/v1/memory-growth/artifact-curator"""

    body = await _json_body(request)
    try:
        from l3_node.cognitive_kernel.artifact_curator import run_artifact_curator

        result = run_artifact_curator(max_items=int(body.get("max_items") or 10))
        return _json_response({"ok": True, "result": result.to_dict(), "status": memory_growth_status()})
    except Exception as e:
        logger.exception("[MemoryGrowth HTTP] artifact curator failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


def memory_growth_status() -> dict[str, Any]:
    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir

    root = ensure_memory_growth_scaffold()
    latest_pipeline = _latest(root / "reviews" / "pipeline_runs", "*.json")
    latest_weekly = _latest(root / "reviews" / "weekly", "*.weekly_lifecycle.json")
    latest_graph_event = _latest(root / "graph" / "events", "*.graph_sync.jsonl")
    latest_connector_index = root / "indexes" / "graph_connectors.json"
    latest_artifact_curator = _latest(root / "reviews" / "artifact_curator", "*.json")
    counts = {
        "raw_events": _count_jsonl(root / "raw"),
        "concepts": _count_files(root / "concepts", "*.md", exclude_readme=True),
        "playbooks": _count_files(root / "playbooks", "*.md", exclude_readme=True),
        "outputs": _count_files(root / "outputs", "*.md", exclude_readme=True),
        "conflicts": _count_files(root / "conflicts", "*.json"),
        "graph_nodes": _index_count(root / "indexes" / "graph_nodes.json", "nodes"),
        "graph_edges": _index_count(root / "indexes" / "graph_edges.json", "edges"),
    }
    return {
        "root": str(root),
        "counts": counts,
        "monitoring": _memory_growth_monitoring(root, counts),
        "latest": {
            "pipeline_report": str(latest_pipeline) if latest_pipeline else "",
            "weekly_report": str(latest_weekly) if latest_weekly else "",
            "graph_event": str(latest_graph_event) if latest_graph_event else "",
            "connector_index": str(latest_connector_index) if latest_connector_index.exists() else "",
            "artifact_curator_report": str(latest_artifact_curator) if latest_artifact_curator else "",
        },
        "available_actions": [
            "pipeline",
            "weekly-review",
            "graph-sync",
            "connector-sync",
        "governance",
            "batch-governance",
            "artifact-governance",
            "artifact-curator",
        ],
    }


def apply_memory_growth_governance(
    *,
    action: str,
    item: dict[str, Any],
    note: str = "",
    days: int = 14,
) -> dict[str, Any]:
    from l3_node.cognitive_kernel.memory_growth import append_raw_event, ensure_memory_growth_scaffold, memory_growth_dir

    allowed = {
        "confirm_pending",
        "reject_pending",
        "defer_pending",
        "revalidate_stale",
        "archive_stale",
        "generate_failure_playbook",
        "rewrite_or_downrank",
        "create_or_update_recovery_playbook",
        "archive_or_revalidate",
        "promote_preferred_guidance",
        "revalidate_artifact",
        "merge_artifact_draft",
    }
    if action not in allowed:
        raise ValueError(f"unsupported governance action: {action}")
    root = ensure_memory_growth_scaffold()
    governance_id = f"governance_{int(time.time() * 1000)}_{_safe_segment(action)[:24]}"
    item_path = _safe_memory_growth_path(root, item.get("path") or item.get("target"))
    result: dict[str, Any] = {
        "schema_version": 1,
        "governance_id": governance_id,
        "action": action,
        "item": item,
        "note": note,
        "created_at": _iso_now(),
        "side_effects": [],
    }

    if action in ("confirm_pending", "reject_pending", "defer_pending"):
        if not item_path:
            raise ValueError("pending governance requires item.path")
        _apply_pending_governance(
            action=action,
            path=item_path,
            root=root,
            result=result,
            governance_id=governance_id,
            days=max(1, days),
        )
    elif action == "revalidate_stale":
        if not item_path:
            raise ValueError("revalidate_stale requires item.path")
        _apply_revalidate_stale(path=item_path, root=root, result=result, governance_id=governance_id)
    elif action == "archive_stale":
        if not item_path:
            raise ValueError("archive_stale requires item.path")
        _apply_archive_stale(path=item_path, root=root, result=result, governance_id=governance_id)
    elif action == "generate_failure_playbook":
        _apply_failure_playbook(item=item, root=root, result=result, governance_id=governance_id)
    elif action == "rewrite_or_downrank":
        if not item_path:
            raise ValueError("rewrite_or_downrank requires item.path or item.target")
        _apply_artifact_downrank(path=item_path, root=root, result=result, governance_id=governance_id, note=note)
    elif action == "create_or_update_recovery_playbook":
        _apply_artifact_recovery_playbook(item=item, root=root, result=result, governance_id=governance_id)
    elif action == "archive_or_revalidate":
        if not item_path:
            raise ValueError("archive_or_revalidate requires item.path or item.target")
        _apply_artifact_archive_or_revalidate(path=item_path, root=root, result=result, governance_id=governance_id)
    elif action == "promote_preferred_guidance":
        if not item_path:
            raise ValueError("promote_preferred_guidance requires item.path or item.target")
        _apply_artifact_promote(path=item_path, root=root, result=result, governance_id=governance_id)
    elif action == "revalidate_artifact":
        if not item_path:
            raise ValueError("revalidate_artifact requires item.path or item.target")
        _apply_revalidate_stale(path=item_path, root=root, result=result, governance_id=governance_id)
    elif action == "merge_artifact_draft":
        _apply_artifact_draft_merge(item=item, root=root, result=result, governance_id=governance_id)
    _refresh_artifact_usage_index(root)

    report_path = _write_governance_report(root, result)
    raw_path = append_raw_event(
        category="evidence",
        source="memory_growth_governance_agent",
        stream="governance",
        payload={
            "governance_id": governance_id,
            "action": action,
            "item": item,
            "note": note,
            "result": result,
        },
        source_refs=[{"type": "memory_growth_governance", "governance_id": governance_id}],
        review={
            "review_candidate": True,
            "promotion_targets": ["concepts", "playbooks", "outputs"],
            "priority": "high" if action in ("confirm_pending", "generate_failure_playbook") else "normal",
            "reason": "memory_growth_governance_action",
        },
    )
    result["report_path"] = str(report_path)
    result["raw_event_path"] = str(raw_path)
    return result


def apply_memory_growth_batch_governance(
    *,
    operations: Any = None,
    action: str | None = None,
    items: Any = None,
    note: str = "",
    days: int = 14,
    max_items: int = 10,
) -> dict[str, Any]:
    from l3_node.cognitive_kernel.memory_growth import append_raw_event, ensure_memory_growth_scaffold

    root = ensure_memory_growth_scaffold()
    batch_id = f"governance_batch_{int(time.time() * 1000)}"
    normalized = _normalize_batch_operations(operations=operations, action=action, items=items, note=note)
    if not normalized:
        raise ValueError("batch governance requires operations or action+items")
    limit = max(1, min(max_items, 25))
    selected = normalized[:limit]
    result: dict[str, Any] = {
        "schema_version": 1,
        "batch_id": batch_id,
        "created_at": _iso_now(),
        "note": note,
        "requested_count": len(normalized),
        "executed_count": 0,
        "failed_count": 0,
        "results": [],
    }
    for op in selected:
        op_action = str(op.get("action") or "").strip()
        op_item = op.get("item") if isinstance(op.get("item"), dict) else {}
        op_note = str(op.get("note") or note or f"batch {op_action}")
        try:
            child = apply_memory_growth_governance(action=op_action, item=op_item, note=op_note, days=days)
            result["executed_count"] += 1
            result["results"].append(
                {
                    "ok": True,
                    "action": op_action,
                    "item": op_item,
                    "governance_id": child.get("governance_id"),
                    "report_path": child.get("report_path"),
                    "side_effects": child.get("side_effects", []),
                }
            )
        except Exception as exc:
            result["failed_count"] += 1
            result["results"].append({"ok": False, "action": op_action, "item": op_item, "error": str(exc)})
    report_path = _write_batch_governance_report(root, result)
    raw_path = append_raw_event(
        category="evidence",
        source="memory_growth_governance_agent",
        stream="batch_governance",
        payload={"batch_id": batch_id, "note": note, "result": result},
        source_refs=[{"type": "memory_growth_batch_governance", "batch_id": batch_id}],
        review={
            "review_candidate": True,
            "promotion_targets": ["playbooks", "outputs"],
            "priority": "high" if result["failed_count"] else "normal",
            "reason": "memory_growth_batch_governance_action",
        },
    )
    result["report_path"] = str(report_path)
    result["raw_event_path"] = str(raw_path)
    return result


def register_memory_growth_routes(app: Any) -> None:
    app.router.add_get("/api/v1/memory-growth/status", handle_memory_growth_status)
    app.router.add_post("/api/v1/memory-growth/pipeline", handle_memory_growth_pipeline)
    app.router.add_post("/api/v1/memory-growth/weekly-review", handle_memory_growth_weekly_review)
    app.router.add_post("/api/v1/memory-growth/graph-sync", handle_memory_growth_graph_sync)
    app.router.add_post("/api/v1/memory-growth/connector-sync", handle_memory_growth_connector_sync)
    app.router.add_post("/api/v1/memory-growth/governance", handle_memory_growth_governance)
    app.router.add_post("/api/v1/memory-growth/batch-governance", handle_memory_growth_batch_governance)
    app.router.add_post("/api/v1/memory-growth/artifact-curator", handle_memory_growth_artifact_curator)
    logger.info("[MemoryGrowth HTTP] routes registered")


def _optional_str(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _string_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        out = [str(item).strip() for item in value if str(item).strip()]
        return out or None
    text = str(value).strip()
    if not text:
        return None
    return [item.strip() for item in text.split(",") if item.strip()] or None


def _latest(root: Path, pattern: str) -> Path | None:
    if not root.exists():
        return None
    items = sorted(root.glob(pattern), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    return items[0] if items else None


def _count_jsonl(root: Path) -> int:
    if not root.exists():
        return 0
    total = 0
    for path in root.glob("**/*.jsonl"):
        try:
            total += sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        except OSError:
            continue
    return total


def _count_files(root: Path, pattern: str, *, exclude_readme: bool = False) -> int:
    if not root.exists():
        return 0
    return sum(1 for path in root.glob(f"**/{pattern}") if not (exclude_readme and path.name == "README.md"))


def _index_count(path: Path, key: str) -> int:
    if not path.exists():
        return 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    rows = payload.get(key)
    return len(rows) if isinstance(rows, list) else 0


def _memory_growth_monitoring(root: Path, counts: dict[str, int]) -> dict[str, Any]:
    raw_by_date = _raw_event_counts_by_date(root / "raw")
    file_counts = {
        "concepts": _file_counts_by_date(root / "concepts", "*.md", exclude_readme=True),
        "playbooks": _file_counts_by_date(root / "playbooks", "*.md", exclude_readme=True),
        "outputs": _file_counts_by_date(root / "outputs", "*.md", exclude_readme=True),
        "conflicts": _file_counts_by_date(root / "conflicts", "*.json"),
    }
    stale_concepts = _stale_concepts(root / "concepts", stale_after_days=30)
    conflict_types = _conflict_type_rows(root / "conflicts")
    failure_patterns = _failure_pattern_rows(root)
    pending_queue = _pending_confirmation_queue(root)
    governance_history = _governance_history(root)
    artifact_usage = _artifact_usage_rows(root)
    artifact_usage_trend_index = _artifact_usage_trend_index(root)
    governance_effectiveness_index = _governance_effectiveness_index(root)
    governance_strategy_policy = _governance_strategy_policy(governance_effectiveness_index)
    governance_effectiveness = _governance_effectiveness(
        root=root,
        governance_history=governance_history,
        conflict_types=conflict_types,
        failure_patterns=failure_patterns,
    )
    governance_recommendations = _governance_recommendations(
        root=root,
        pending_queue=pending_queue,
        stale_concepts=stale_concepts,
        failure_patterns=failure_patterns,
        conflict_types=conflict_types,
        governance_history=governance_history,
        strategy_policy=governance_strategy_policy,
    )
    durable = counts.get("concepts", 0) + counts.get("playbooks", 0) + counts.get("outputs", 0)
    risk = counts.get("conflicts", 0) + len(stale_concepts) + len(pending_queue) + sum(row["count"] for row in failure_patterns)
    quality_score = 100 if durable <= 0 and risk <= 0 else max(0, min(100, round((max(1, durable) / max(1, durable + risk)) * 100)))
    risk_level = "high" if quality_score < 55 else "medium" if quality_score < 80 else "low"
    return {
        "trends": {
            "days_7": _trend_rows(7, raw_by_date, file_counts),
            "days_14": _trend_rows(14, raw_by_date, file_counts),
            "days_30": _trend_rows(30, raw_by_date, file_counts),
        },
        "conflict_types": conflict_types,
        "stale_concepts": stale_concepts[:30],
        "failure_patterns": failure_patterns[:30],
        "pending_confirmation_queue": pending_queue[:30],
        "governance_history": governance_history[:30],
        "artifact_usage": artifact_usage[:30],
        "artifact_usage_trends": _artifact_usage_trends(artifact_usage_trend_index),
        "artifact_usage_attribution": _artifact_usage_attribution(artifact_usage_trend_index),
        "artifact_usage_recommendations": _artifact_usage_recommendations(artifact_usage_trend_index),
        "governance_recommendations": governance_recommendations[:8],
        "governance_effectiveness": governance_effectiveness,
        "governance_effectiveness_trends": _governance_effectiveness_trends(governance_effectiveness_index),
        "governance_effectiveness_attribution": _governance_effectiveness_attribution(governance_effectiveness_index),
        "governance_strategy_policy": governance_strategy_policy,
        "health": {
            "quality_score": quality_score,
            "risk_level": risk_level,
            "stale_concept_count": len(stale_concepts),
            "pending_confirmation_count": len(pending_queue),
            "failure_pattern_count": len(failure_patterns),
            "governance_history_count": len(governance_history),
            "artifact_usage_count": len(artifact_usage),
            "artifact_low_success_count": len((artifact_usage_trend_index.get("attribution") or {}).get("low_success_assets") or []) if isinstance(artifact_usage_trend_index.get("attribution"), dict) else 0,
            "artifact_stale_unused_count": len((artifact_usage_trend_index.get("attribution") or {}).get("stale_unused_assets") or []) if isinstance(artifact_usage_trend_index.get("attribution"), dict) else 0,
            "recommendation_count": len(governance_recommendations),
            "governance_effectiveness_score": governance_effectiveness["score"],
        },
}


def _artifact_usage_rows(root: Path) -> list[dict[str, Any]]:
    path = root / "indexes" / "artifact_usage.json"
    if not path.exists():
        return []
    payload = _read_json(path)
    rows = payload.get("artifacts") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    clean: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        clean.append(
            {
                "path": str(row.get("path") or ""),
                "id": str(row.get("id") or ""),
                "type": str(row.get("type") or ""),
                "summary": str(row.get("summary") or ""),
                "memory_use_count": int(row.get("memory_use_count") or 0),
                "memory_success_count": int(row.get("memory_success_count") or 0),
                "memory_failure_count": int(row.get("memory_failure_count") or 0),
                "memory_success_rate": float(row.get("memory_success_rate") or 0.0),
                "memory_last_used_at": str(row.get("memory_last_used_at") or ""),
                "memory_last_failure_reason": str(row.get("memory_last_failure_reason") or ""),
            }
        )
    clean.sort(key=lambda row: (-int(row.get("memory_use_count") or 0), -float(row.get("memory_success_rate") or 0.0), str(row.get("path") or "")))
    return clean


def _artifact_usage_trend_index(root: Path) -> dict[str, Any]:
    path = root / "indexes" / "artifact_usage_trends.json"
    if not path.exists():
        return {}
    payload = _read_json(path)
    return payload if payload else {}


def _artifact_usage_trends(index: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    history = index.get("history") if isinstance(index.get("history"), list) else []
    rows = [row for row in history if isinstance(row, dict)]
    rows.sort(key=lambda row: str(row.get("week_start") or row.get("generated_at") or ""))
    return {
        "days_7": _artifact_usage_trend_rows(rows, days=7),
        "days_14": _artifact_usage_trend_rows(rows, days=14),
        "days_30": _artifact_usage_trend_rows(rows, days=30),
    }


def _artifact_usage_trend_rows(rows: list[dict[str, Any]], *, days: int) -> list[dict[str, Any]]:
    threshold = Date.today() - timedelta(days=max(1, days))
    out: list[dict[str, Any]] = []
    for row in rows:
        row_date = _parse_date(str(row.get("week_start") or row.get("generated_at") or ""))
        if row_date and row_date < threshold:
            continue
        out.append(
            {
                "date": str(row.get("week_start") or row.get("generated_at") or ""),
                "week_id": str(row.get("week_id") or ""),
                "artifact_count": int(row.get("artifact_count") or 0),
                "active_artifact_count": int(row.get("active_artifact_count") or 0),
                "total_use_count": int(row.get("total_use_count") or 0),
                "success_count": int(row.get("success_count") or 0),
                "failure_count": int(row.get("failure_count") or 0),
                "success_rate": float(row.get("success_rate") or 0.0),
                "low_success_count": int(row.get("low_success_count") or 0),
                "high_failure_count": int(row.get("high_failure_count") or 0),
                "stale_unused_count": int(row.get("stale_unused_count") or 0),
            }
        )
    return out[-12:]


def _artifact_usage_attribution(index: dict[str, Any]) -> dict[str, Any]:
    attribution = index.get("attribution") if isinstance(index.get("attribution"), dict) else {}
    return {
        "best_playbooks": list(attribution.get("best_playbooks") or [])[:10],
        "top_successful_assets": list(attribution.get("top_successful_assets") or [])[:10],
        "low_success_assets": list(attribution.get("low_success_assets") or [])[:10],
        "high_failure_assets": list(attribution.get("high_failure_assets") or [])[:10],
        "stale_unused_assets": list(attribution.get("stale_unused_assets") or [])[:10],
        "latest": index.get("latest") if isinstance(index.get("latest"), dict) else {},
    }


def _artifact_usage_recommendations(index: dict[str, Any]) -> list[dict[str, Any]]:
    rows = index.get("recommendations") if isinstance(index.get("recommendations"), list) else []
    return [row for row in rows if isinstance(row, dict)][:20]


def _governance_history(root: Path) -> list[dict[str, Any]]:
    reports_root = root / "reviews" / "governance"
    if not reports_root.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(reports_root.glob("*.json"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
        payload = _read_json(path)
        if not payload:
            continue
        item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
        is_batch = "batch_id" in payload
        rows.append(
            {
                "governance_id": str(payload.get("governance_id") or payload.get("batch_id") or path.stem),
                "action": str(payload.get("action") or ("batch_governance" if is_batch else "unknown")),
                "created_at": str(payload.get("created_at") or _iso_from_mtime(path)),
                "note": str(payload.get("note") or ""),
                "summary": _governance_item_summary(item) if not is_batch else f"Batch governance: {payload.get('executed_count', 0)} executed, {payload.get('failed_count', 0)} failed",
                "item_path": str(item.get("path") or ""),
                "item_pattern": str(item.get("pattern") or ""),
                "side_effect_count": len(payload.get("side_effects") or []),
                "executed_count": int(payload.get("executed_count") or 0),
                "failed_count": int(payload.get("failed_count") or 0),
                "report_path": str(path.relative_to(root)),
            }
        )
    return rows


def _governance_recommendations(
    *,
    root: Path,
    pending_queue: list[dict[str, Any]],
    stale_concepts: list[dict[str, Any]],
    failure_patterns: list[dict[str, Any]],
    conflict_types: list[dict[str, Any]],
    governance_history: list[dict[str, Any]],
    strategy_policy: dict[str, Any],
) -> list[dict[str, Any]]:
    recent = _recent_governance_signatures(governance_history)
    rows: list[dict[str, Any]] = []

    for item in pending_queue[:3]:
        signature = ("confirm_pending", str(item.get("path") or ""))
        if signature in recent:
            continue
        rows.append(
            _strategy_enriched_recommendation(
                {
                    "id": f"pending:{item.get('path') or item.get('summary')}",
                    "priority": "high",
                    "title": "确认一条待沉淀知识",
                    "reason": str(item.get("reason") or "pending_confirmation"),
                    "action": "confirm_pending",
                    "item": item,
                    "source": "pending_confirmation_queue",
                    "base_priority_score": 90,
                },
                strategy_policy,
            )
        )

    for item in failure_patterns[:3]:
        count = int(item.get("count") or 0)
        if count <= 0:
            continue
        pattern = str(item.get("pattern") or "")
        signature = ("generate_failure_playbook", pattern)
        if signature in recent:
            continue
        rows.append(
            _strategy_enriched_recommendation(
                {
                    "id": f"failure:{pattern}",
                    "priority": "high" if count >= 3 else "medium",
                    "title": "把高频失败模式沉淀成恢复方法论",
                    "reason": f"{pattern} 出现 {count} 次",
                    "action": "generate_failure_playbook",
                    "item": item,
                    "source": "failure_patterns",
                    "base_priority_score": 88 if count >= 3 else 68,
                },
                strategy_policy,
            )
        )

    for item in stale_concepts[:3]:
        signature = ("revalidate_stale", str(item.get("path") or ""))
        if signature in recent:
            continue
        rows.append(
            _strategy_enriched_recommendation(
                {
                    "id": f"stale:{item.get('path') or item.get('summary')}",
                    "priority": "medium",
                    "title": "重新验证一条陈旧概念",
                    "reason": str(item.get("reason") or "stale_concept"),
                    "action": "revalidate_stale",
                    "item": item,
                    "source": "stale_concepts",
                    "base_priority_score": 62,
                },
                strategy_policy,
            )
        )

    for item in conflict_types[:2]:
        reason = str(item.get("reason") or "")
        if reason in ("requires_user_confirmation", "low_confidence"):
            continue
        rows.append(
            _strategy_enriched_recommendation(
                {
                    "id": f"conflict:{reason}",
                    "priority": "medium",
                    "title": "复盘一类知识冲突",
                    "reason": f"{reason} 出现 {int(item.get('count') or 0)} 次",
                    "action": "generate_failure_playbook",
                    "item": {"pattern": f"conflict:{reason}", "count": item.get("count"), "examples": [item.get("latest_path")]},
                    "source": "conflict_types",
                    "base_priority_score": 60 + min(20, int(item.get("count") or 0) * 2),
                },
                strategy_policy,
            )
        )

    rows.sort(key=lambda row: (-float(row.get("priority_score") or 0), str(row.get("source")), str(row.get("id"))))
    return rows


def _governance_strategy_policy(index: dict[str, Any]) -> dict[str, Any]:
    attribution = index.get("attribution") if isinstance(index.get("attribution"), dict) else {}
    latest = index.get("latest") if isinstance(index.get("latest"), dict) else {}
    history = index.get("history") if isinstance(index.get("history"), list) else []
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

    latest_score = int(latest.get("score") or 0)
    trend_delta = _effectiveness_score_delta(history)
    global_mode = "normal"
    if latest_score and latest_score < 60:
        global_mode = "cautious"
    elif trend_delta > 8:
        global_mode = "accelerate"
    elif trend_delta < -8:
        global_mode = "cautious"
    return {
        "schema_version": 1,
        "updated_at": str(index.get("updated_at") or ""),
        "latest_score": latest_score,
        "trend_delta": trend_delta,
        "global_mode": global_mode,
        "action_policy": action_policy,
    }


def _strategy_enriched_recommendation(row: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    action = str(row.get("action") or "")
    action_policy = policy.get("action_policy") if isinstance(policy.get("action_policy"), dict) else {}
    strategy = action_policy.get(action) if isinstance(action_policy.get(action), dict) else {}
    weight = float(strategy.get("weight") or 1.0)
    global_mode = str(policy.get("global_mode") or "normal")
    if global_mode == "accelerate" and strategy.get("execution_mode") != "manual_review":
        weight += 0.08
    if global_mode == "cautious" and strategy.get("execution_mode") != "batch_ok":
        weight -= 0.08
    base_score = float(row.get("base_priority_score") or 50)
    priority_score = max(0.0, min(100.0, round(base_score * max(0.25, weight), 2)))
    if priority_score >= 80:
        priority = "high"
    elif priority_score >= 55:
        priority = "medium"
    else:
        priority = "low"
    enriched = dict(row)
    enriched["priority"] = priority
    enriched["priority_score"] = priority_score
    enriched["strategy"] = {
        "weight": round(weight, 3),
        "execution_mode": str(strategy.get("execution_mode") or "normal"),
        "requires_more_evidence": bool(strategy.get("requires_more_evidence") or False),
        "reason": str(strategy.get("reason") or "default_strategy"),
        "global_mode": global_mode,
        "trend_delta": policy.get("trend_delta", 0),
    }
    return enriched


def _effectiveness_score_delta(history: Any) -> int:
    rows = [row for row in history if isinstance(row, dict)] if isinstance(history, list) else []
    rows.sort(key=lambda row: str(row.get("week_start") or row.get("generated_at") or ""))
    if len(rows) < 2:
        return 0
    return int(rows[-1].get("score") or 0) - int(rows[-2].get("score") or 0)


def _governance_effectiveness(
    *,
    root: Path,
    governance_history: list[dict[str, Any]],
    conflict_types: list[dict[str, Any]],
    failure_patterns: list[dict[str, Any]],
) -> dict[str, Any]:
    reports = _governance_report_payloads(root)
    action_count = 0
    success_count = 0
    failure_count = 0
    confirmed_count = 0
    playbook_count = 0
    revalidated_count = 0
    archived_count = 0
    post_governance_failure_count = 0
    signals: list[str] = []

    for report in reports:
        is_batch = "batch_id" in report
        if is_batch:
            results = report.get("results") if isinstance(report.get("results"), list) else []
            action_count += len(results)
            success_count += sum(1 for item in results if isinstance(item, dict) and item.get("ok") is True)
            failure_count += sum(1 for item in results if isinstance(item, dict) and item.get("ok") is not True)
            for child in results:
                if not isinstance(child, dict) or child.get("ok") is not True:
                    continue
                action = str(child.get("action") or "")
                side_effects = child.get("side_effects") if isinstance(child.get("side_effects"), list) else []
                confirmed_count += _side_effect_count(side_effects, "confirmed_concept_written")
                playbook_count += _side_effect_count(side_effects, "failure_playbook_written")
                if action == "revalidate_stale":
                    revalidated_count += 1
                if action == "archive_stale":
                    archived_count += 1
                post_governance_failure_count += _post_governance_failure_count(root, child.get("item"), report.get("created_at"))
            continue

        action_count += 1
        side_effects = report.get("side_effects") if isinstance(report.get("side_effects"), list) else []
        failed = bool(report.get("error")) or not side_effects
        if failed:
            failure_count += 1
        else:
            success_count += 1
        action = str(report.get("action") or "")
        confirmed_count += _side_effect_count(side_effects, "confirmed_concept_written")
        playbook_count += _side_effect_count(side_effects, "failure_playbook_written")
        if action == "revalidate_stale" and not failed:
            revalidated_count += 1
        if action == "archive_stale" and not failed:
            archived_count += 1
        post_governance_failure_count += _post_governance_failure_count(root, report.get("item"), report.get("created_at"))

    open_conflict_pressure = sum(int(row.get("count") or 0) for row in conflict_types)
    open_failure_pressure = sum(int(row.get("count") or 0) for row in failure_patterns)
    success_rate = round(success_count / max(1, action_count), 3)
    production_gain = confirmed_count + playbook_count + revalidated_count + archived_count
    score = 0
    if action_count:
        score = 35 + round(success_rate * 35) + min(20, production_gain * 4)
        score -= min(20, failure_count * 5 + post_governance_failure_count * 3)
        score -= min(15, max(0, open_conflict_pressure + open_failure_pressure - production_gain) // 2)
        score = max(0, min(100, score))

    if not action_count:
        grade = "no_data"
        signals.append("no_governance_actions")
    elif score >= 80:
        grade = "healthy"
        signals.append("governance_actions_are_working")
    elif score >= 60:
        grade = "watch"
        signals.append("governance_actions_need_followup")
    else:
        grade = "weak"
        signals.append("governance_actions_not_yet_effective")
    if failure_count:
        signals.append("governance_failures_need_retry")
    if post_governance_failure_count:
        signals.append("same_failure_after_governance")
    if open_conflict_pressure:
        signals.append("open_conflict_pressure")

    recommendations: list[str] = []
    if not action_count:
        recommendations.append("Run at least one governance action so weekly review can measure impact.")
    if failure_count:
        recommendations.append("Review failed governance reports and retry with a safer path or user confirmation.")
    if post_governance_failure_count:
        recommendations.append("Failures still appeared after playbook governance; update the playbook with a stronger recovery path.")
    if open_conflict_pressure > production_gain:
        recommendations.append("Conflict pressure is still higher than governance output; prioritize confirmations and conflict playbooks.")
    if not recommendations:
        recommendations.append("Keep weekly review enabled and compare future conflict/failure pressure against this baseline.")

    return {
        "score": score,
        "grade": grade,
        "action_count": action_count,
        "success_count": success_count,
        "failure_count": failure_count,
        "success_rate": success_rate,
        "confirmed_concept_count": confirmed_count,
        "generated_playbook_count": playbook_count,
        "revalidated_count": revalidated_count,
        "archived_count": archived_count,
        "post_governance_failure_count": post_governance_failure_count,
        "open_conflict_pressure": open_conflict_pressure,
        "open_failure_pressure": open_failure_pressure,
        "history_visible_count": len(governance_history),
        "signals": signals,
        "recommendations": recommendations,
    }


def _governance_effectiveness_index(root: Path) -> dict[str, Any]:
    path = root / "indexes" / "governance_effectiveness.json"
    if not path.exists():
        return {}
    payload = _read_json(path)
    return payload if payload else {}


def _governance_effectiveness_trends(index: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    history = index.get("history") if isinstance(index.get("history"), list) else []
    rows = [row for row in history if isinstance(row, dict)]
    rows.sort(key=lambda row: str(row.get("week_start") or row.get("generated_at") or ""))
    return {
        "days_7": _effectiveness_trend_rows(rows, days=7),
        "days_14": _effectiveness_trend_rows(rows, days=14),
        "days_30": _effectiveness_trend_rows(rows, days=30),
    }


def _effectiveness_trend_rows(rows: list[dict[str, Any]], *, days: int) -> list[dict[str, Any]]:
    threshold = Date.today() - timedelta(days=max(1, days))
    out: list[dict[str, Any]] = []
    for row in rows:
        row_date = _parse_date(str(row.get("week_start") or row.get("generated_at") or ""))
        if row_date and row_date < threshold:
            continue
        out.append(
            {
                "date": str(row.get("week_start") or row.get("generated_at") or ""),
                "week_id": str(row.get("week_id") or ""),
                "score": int(row.get("score") or 0),
                "action_count": int(row.get("action_count") or 0),
                "success_count": int(row.get("success_count") or 0),
                "failure_count": int(row.get("failure_count") or 0),
                "conflict_pressure": int(row.get("conflict_pressure") or 0),
                "failure_pressure": int(row.get("failure_pressure") or 0),
            }
        )
    return out[-12:]


def _governance_effectiveness_attribution(index: dict[str, Any]) -> dict[str, Any]:
    attribution = index.get("attribution") if isinstance(index.get("attribution"), dict) else {}
    return {
        "effective_actions": list(attribution.get("effective_actions") or [])[:10],
        "ineffective_actions": list(attribution.get("ineffective_actions") or [])[:10],
        "repeated_failures": list(attribution.get("repeated_failures") or [])[:10],
        "latest": index.get("latest") if isinstance(index.get("latest"), dict) else {},
    }


def _governance_report_payloads(root: Path) -> list[dict[str, Any]]:
    reports_root = root / "reviews" / "governance"
    if not reports_root.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(reports_root.glob("*.json"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
        payload = _read_json(path)
        if payload:
            rows.append(payload)
    return rows


def _side_effect_count(side_effects: list[Any], expected_type: str) -> int:
    return sum(1 for item in side_effects if isinstance(item, dict) and item.get("type") == expected_type)


def _post_governance_failure_count(root: Path, item: Any, created_at: Any) -> int:
    if not isinstance(item, dict):
        return 0
    pattern = str(item.get("pattern") or "").strip()
    if not pattern.startswith("failed_turn:"):
        return 0
    needle = pattern.split(":", 1)[1].strip()
    if not needle:
        return 0
    created = _parse_datetime(str(created_at or ""))
    count = 0
    raw_root = root / "raw"
    if not raw_root.exists():
        return 0
    for path in raw_root.glob("**/*.jsonl"):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            event_time = _event_datetime(payload, path)
            if created and event_time and event_time <= created:
                continue
            event_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
            closure = event_payload.get("closure") if isinstance(event_payload.get("closure"), dict) else {}
            if str(closure.get("verification_status") or "").lower() != "failed":
                continue
            haystack = f"{closure.get('failure_reason') or ''} {closure.get('final_text') or ''}"
            if needle in haystack:
                count += 1
    return count


def _event_datetime(payload: dict[str, Any], path: Path) -> datetime | None:
    ts_ms = payload.get("ts_ms")
    if ts_ms is not None:
        try:
            return datetime.fromtimestamp(float(ts_ms) / 1000.0)
        except Exception:
            pass
    for key in ("ts", "created_at", "date"):
        parsed = _parse_datetime(str(payload.get(key) or ""))
        if parsed:
            return parsed
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return None


def _recent_governance_signatures(history: list[dict[str, Any]]) -> set[tuple[str, str]]:
    signatures: set[tuple[str, str]] = set()
    cutoff = Date.today() - timedelta(days=7)
    for row in history:
        created = _parse_date(str(row.get("created_at") or ""))
        if created and created < cutoff:
            continue
        action = str(row.get("action") or "")
        if action in ("confirm_pending", "reject_pending", "defer_pending", "revalidate_stale", "archive_stale"):
            target = str(row.get("item_path") or "")
        elif action == "generate_failure_playbook":
            target = str(row.get("item_pattern") or "")
        else:
            target = ""
        if action and target:
            signatures.add((action, target))
    return signatures


def _governance_item_summary(item: dict[str, Any]) -> str:
    for key in ("summary", "reason", "pattern", "path", "candidate_id"):
        value = str(item.get(key) or "").strip()
        if value:
            return value[:160]
    return "governance item"


def _normalize_batch_operations(*, operations: Any, action: str | None, items: Any, note: str) -> list[dict[str, Any]]:
    if isinstance(operations, list):
        out: list[dict[str, Any]] = []
        for raw in operations:
            if not isinstance(raw, dict):
                continue
            op_action = str(raw.get("action") or action or "").strip()
            op_item = raw.get("item") if isinstance(raw.get("item"), dict) else {}
            if op_action and op_item:
                out.append({"action": op_action, "item": op_item, "note": str(raw.get("note") or note or "")})
        return out
    if action and isinstance(items, list):
        return [{"action": action, "item": item, "note": note} for item in items if isinstance(item, dict)]
    return []


def _apply_pending_governance(
    *,
    action: str,
    path: Path,
    root: Path,
    result: dict[str, Any],
    governance_id: str,
    days: int,
) -> None:
    payload = _read_json(path)
    if not payload:
        raise ValueError(f"invalid pending item json: {path}")
    candidate = payload.get("candidate") if isinstance(payload.get("candidate"), dict) else {}
    status = "confirmed" if action == "confirm_pending" else "rejected" if action == "reject_pending" else "deferred"
    governance = {
        "status": status,
        "governance_id": governance_id,
        "updated_at": _iso_now(),
    }
    if action == "defer_pending":
        governance["defer_until"] = (Date.today() + timedelta(days=days)).isoformat()
    payload["governance"] = governance
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    result["side_effects"].append({"type": "conflict_governance_status", "path": str(path.relative_to(root)), "status": status})
    if action == "confirm_pending" and candidate and candidate.get("draft_path"):
        _apply_artifact_draft_merge(
            item={
                "draft_path": candidate.get("draft_path"),
                "confirmation_path": str(path.relative_to(root)),
                "target": candidate.get("target_artifact_path"),
            },
            root=root,
            result=result,
            governance_id=governance_id,
        )
        result["side_effects"].append({"type": "artifact_rewrite_confirmed_via_pending", "path": str(path.relative_to(root))})
    elif action == "confirm_pending" and candidate:
        concept_path = _write_confirmed_concept(candidate, root=root, governance_id=governance_id)
        result["side_effects"].append({"type": "confirmed_concept_written", "path": str(concept_path.relative_to(root))})


def _apply_revalidate_stale(*, path: Path, root: Path, result: dict[str, Any], governance_id: str) -> None:
    text = _read_text(path)
    if not text:
        raise ValueError(f"empty or missing stale concept: {path}")
    now = Date.today().isoformat()
    updated = _upsert_frontmatter_field(text, "last_verified", now)
    updated = _upsert_frontmatter_field(updated, "verification_status", "revalidated_by_governance")
    updated = _append_update_log(updated, f"{_iso_now()}: revalidated by `{governance_id}`.")
    path.write_text(updated, encoding="utf-8")
    result["side_effects"].append({"type": "concept_revalidated", "path": str(path.relative_to(root)), "last_verified": now})


def _apply_archive_stale(*, path: Path, root: Path, result: dict[str, Any], governance_id: str) -> None:
    if not path.exists():
        raise ValueError(f"stale concept not found: {path}")
    rel = path.relative_to(root)
    archive = root / "archive" / rel
    archive.parent.mkdir(parents=True, exist_ok=True)
    text = _read_text(path)
    archive.write_text(_append_update_log(text, f"{_iso_now()}: archived by `{governance_id}`."), encoding="utf-8")
    path.unlink()
    result["side_effects"].append({"type": "concept_archived", "from": str(rel), "to": str(archive.relative_to(root))})


def _apply_failure_playbook(*, item: dict[str, Any], root: Path, result: dict[str, Any], governance_id: str) -> None:
    pattern = str(item.get("pattern") or item.get("reason") or "unknown_failure").strip()
    playbook_dir = root / "playbooks" / "recovery"
    playbook_dir.mkdir(parents=True, exist_ok=True)
    slug = _safe_segment(pattern)[:80] or "unknown_failure"
    path = playbook_dir / f"{slug}.md"
    now = _iso_now()
    examples = item.get("examples") if isinstance(item.get("examples"), list) else []
    body = (
        "---\n"
        f"id: \"playbook:{slug}\"\n"
        "type: \"recovery_playbook\"\n"
        f"summary: \"Recovery guidance for {pattern}\"\n"
        f"created_at: \"{now}\"\n"
        f"last_verified: \"{now[:10]}\"\n"
        f"governance_id: \"{governance_id}\"\n"
        "---\n\n"
        f"# Recovery Playbook: {pattern}\n\n"
        "## Trigger\n\n"
        f"- Pattern: `{pattern}`\n"
        f"- Count: `{item.get('count', 1)}`\n\n"
        "## Recommended Recovery\n\n"
        "- Re-check the latest evidence before retrying.\n"
        "- Switch to an alternate capability path if the same error repeats.\n"
        "- Ask the user for confirmation when the recovery would mutate files, messages, or external state.\n\n"
        "## Evidence Examples\n\n"
        + "".join(f"- `{example}`\n" for example in examples[:8])
        + "\n## Update Log\n\n"
        f"- {now}: generated from governance action `{governance_id}`.\n"
    )
    path.write_text(body, encoding="utf-8")
    result["side_effects"].append({"type": "failure_playbook_written", "path": str(path.relative_to(root))})


def _apply_artifact_downrank(*, path: Path, root: Path, result: dict[str, Any], governance_id: str, note: str) -> None:
    text = _read_text(path)
    if not text:
        raise ValueError(f"artifact not found or empty: {path}")
    now = _iso_now()
    next_text = text
    next_text = _upsert_frontmatter_field(next_text, "governance_strategy_action", "rewrite_or_downrank")
    next_text = _upsert_frontmatter_field(next_text, "governance_strategy_weight", "0.45")
    next_text = _upsert_frontmatter_field(next_text, "governance_execution_mode", "manual_review")
    next_text = _upsert_frontmatter_field(next_text, "governance_requires_more_evidence", "true")
    next_text = _upsert_frontmatter_field(next_text, "governance_strategy_reason", "artifact_low_success_or_repeated_failure")
    next_text = _upsert_frontmatter_field(next_text, "governance_strategy_updated_at", now)
    next_text = _upsert_frontmatter_field(next_text, "artifact_review_status", "needs_rewrite")
    next_text = _append_update_log(next_text, f"{now}: downranked by artifact governance `{governance_id}`. {note}".strip())
    path.write_text(next_text, encoding="utf-8")
    request_path = _write_artifact_rewrite_request(path=path, root=root, governance_id=governance_id, note=note, reason="low_success_or_repeated_failure")
    result["side_effects"].append({"type": "artifact_downranked", "path": str(path.relative_to(root)), "weight": 0.45})
    result["side_effects"].append({"type": "artifact_rewrite_request_written", "path": str(request_path.relative_to(root))})


def _apply_artifact_recovery_playbook(*, item: dict[str, Any], root: Path, result: dict[str, Any], governance_id: str) -> None:
    source_path = _safe_memory_growth_path(root, item.get("path") or item.get("target"))
    source_text = _read_text(source_path) if source_path else ""
    source_fm = _frontmatter(source_text)
    source_summary = str(item.get("summary") or source_fm.get("summary") or _first_heading(source_text) or (source_path.stem if source_path else "artifact"))
    reason = str(item.get("reason") or item.get("memory_last_failure_reason") or source_fm.get("memory_last_failure_reason") or "artifact_repeated_failure")
    slug = _safe_segment(f"artifact_{source_path.stem if source_path else source_summary}_{reason}")[:90]
    playbook_dir = root / "playbooks" / "recovery"
    playbook_dir.mkdir(parents=True, exist_ok=True)
    path = playbook_dir / f"{slug}.md"
    now = _iso_now()
    body = (
        "---\n"
        f"id: \"playbook:{slug}\"\n"
        "type: \"recovery_playbook\"\n"
        f"summary: \"Recovery guidance for artifact: {_escape_frontmatter(source_summary)}\"\n"
        f"created_at: \"{now}\"\n"
        f"last_verified: \"{now[:10]}\"\n"
        f"governance_id: \"{governance_id}\"\n"
        "governance_strategy_action: \"generate_failure_playbook\"\n"
        "governance_strategy_weight: \"1.10\"\n"
        "governance_execution_mode: \"normal\"\n"
        "governance_requires_more_evidence: \"false\"\n"
        "---\n\n"
        f"# Recovery Playbook: {source_summary}\n\n"
        "## Trigger\n\n"
        f"- Source artifact: `{str(source_path.relative_to(root)) if source_path else ''}`\n"
        f"- Failure reason: `{reason}`\n\n"
        "## Recommended Recovery\n\n"
        "- Re-check the latest execution evidence before using this artifact again.\n"
        "- Prefer a verified alternate path when the same failure repeats.\n"
        "- Ask the user for confirmation if recovery would mutate files, messages, or external systems.\n\n"
        "## Source Artifact Snapshot\n\n"
        f"- Uses: `{item.get('memory_use_count', source_fm.get('memory_use_count', 0))}`\n"
        f"- Success rate: `{item.get('memory_success_rate', source_fm.get('memory_success_rate', 0))}`\n"
        f"- Failures: `{item.get('memory_failure_count', source_fm.get('memory_failure_count', 0))}`\n\n"
        "## Update Log\n\n"
        f"- {now}: generated from artifact governance action `{governance_id}`.\n"
    )
    path.write_text(body, encoding="utf-8")
    result["side_effects"].append({"type": "artifact_recovery_playbook_written", "path": str(path.relative_to(root)), "source_path": str(source_path.relative_to(root)) if source_path else ""})


def _apply_artifact_archive_or_revalidate(*, path: Path, root: Path, result: dict[str, Any], governance_id: str) -> None:
    text = _read_text(path)
    if not text:
        raise ValueError(f"artifact not found or empty: {path}")
    fm = _frontmatter(text)
    use_count = _int(fm.get("memory_use_count"))
    success_rate = _float_value(fm.get("memory_success_rate"))
    failure_count = _int(fm.get("memory_failure_count"))
    if use_count <= 0 or (failure_count >= 2 and success_rate < 0.5):
        rel = path.relative_to(root)
        archive = root / "archive" / "artifacts" / rel
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_text(_append_update_log(text, f"{_iso_now()}: archived by artifact governance `{governance_id}`."), encoding="utf-8")
        path.unlink()
        result["side_effects"].append({"type": "artifact_archived", "from": str(rel), "to": str(archive.relative_to(root))})
        return
    _apply_revalidate_stale(path=path, root=root, result=result, governance_id=governance_id)
    result["side_effects"].append({"type": "artifact_revalidated_instead_of_archived", "path": str(path.relative_to(root)), "success_rate": success_rate})


def _apply_artifact_promote(*, path: Path, root: Path, result: dict[str, Any], governance_id: str) -> None:
    text = _read_text(path)
    if not text:
        raise ValueError(f"artifact not found or empty: {path}")
    now = _iso_now()
    next_text = text
    next_text = _upsert_frontmatter_field(next_text, "preferred_guidance", "true")
    next_text = _upsert_frontmatter_field(next_text, "governance_strategy_action", "promote_preferred_guidance")
    next_text = _upsert_frontmatter_field(next_text, "governance_strategy_weight", "1.50")
    next_text = _upsert_frontmatter_field(next_text, "governance_execution_mode", "batch_ok")
    next_text = _upsert_frontmatter_field(next_text, "governance_requires_more_evidence", "false")
    next_text = _upsert_frontmatter_field(next_text, "governance_strategy_reason", "artifact_high_success")
    next_text = _upsert_frontmatter_field(next_text, "governance_strategy_updated_at", now)
    next_text = _upsert_frontmatter_field(next_text, "artifact_review_status", "preferred")
    next_text = _append_update_log(next_text, f"{now}: promoted as preferred guidance by artifact governance `{governance_id}`.")
    path.write_text(next_text, encoding="utf-8")
    result["side_effects"].append({"type": "artifact_promoted_preferred_guidance", "path": str(path.relative_to(root)), "weight": 1.5})


def _apply_artifact_draft_merge(*, item: dict[str, Any], root: Path, result: dict[str, Any], governance_id: str) -> None:
    draft_path = item.get("draft_path") or item.get("path") or item.get("target")
    if not draft_path:
        raise ValueError("merge_artifact_draft requires item.draft_path")
    try:
        from l3_node.cognitive_kernel.artifact_curator import merge_artifact_draft

        merged = merge_artifact_draft(
            draft_path=draft_path,
            confirmation_path=item.get("confirmation_path"),
            governance_id=governance_id,
        )
    except Exception as exc:
        raise ValueError(f"artifact draft merge failed: {exc}") from exc
    result["side_effects"].extend(merged.to_dict().get("side_effects") or [])
    result["side_effects"].append(
        {
            "type": "artifact_draft_merge_report",
            "artifact_path": str(merged.artifact_path.relative_to(root)),
            "backup_path": str(merged.backup_path.relative_to(root)),
            "draft_path": str(merged.draft_path.relative_to(root)),
        }
    )


def _write_artifact_rewrite_request(*, path: Path, root: Path, governance_id: str, note: str, reason: str) -> Path:
    reports_dir = root / "reviews" / "artifact_rewrites"
    reports_dir.mkdir(parents=True, exist_ok=True)
    rel = path.relative_to(root)
    payload = {
        "schema_version": 1,
        "governance_id": governance_id,
        "created_at": _iso_now(),
        "artifact_path": str(rel),
        "reason": reason,
        "note": note,
        "recommended_action": "rewrite artifact content, add stronger evidence, or merge into a better playbook",
    }
    out = reports_dir / f"{governance_id}_{_safe_segment(path.stem)}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return out


def _refresh_artifact_usage_index(root: Path) -> None:
    try:
        from l3_node.cognitive_kernel.memory_growth_strategy import refresh_artifact_usage_index

        refresh_artifact_usage_index(root)
    except Exception:
        pass


def _write_confirmed_concept(candidate: dict[str, Any], *, root: Path, governance_id: str) -> Path:
    concept_dir = root / "concepts" / "confirmed"
    concept_dir.mkdir(parents=True, exist_ok=True)
    summary = str(candidate.get("summary") or candidate.get("candidate_id") or "Confirmed knowledge").strip()
    slug = _safe_segment(str(candidate.get("candidate_id") or summary))[:80] or "confirmed_knowledge"
    path = concept_dir / f"{slug}.md"
    now = _iso_now()
    source_refs = candidate.get("source_refs") if isinstance(candidate.get("source_refs"), list) else []
    text = (
        "---\n"
        f"id: \"concept:{slug}\"\n"
        "type: \"confirmed\"\n"
        f"summary: \"{_escape_frontmatter(summary)}\"\n"
        f"confidence: {float(candidate.get('confidence') or 0.85):.2f}\n"
        f"last_verified: \"{now[:10]}\"\n"
        f"governance_id: \"{governance_id}\"\n"
        "---\n\n"
        f"# {summary}\n\n"
        "## Summary\n\n"
        f"{summary}\n\n"
        "## Source Evidence\n\n"
        + (json.dumps(source_refs, ensure_ascii=False, indent=2) if source_refs else "- Confirmed from governance queue.\n")
        + "\n\n## Update Log\n\n"
        f"- {now}: confirmed by governance action `{governance_id}`.\n"
    )
    path.write_text(text, encoding="utf-8")
    return path


def _write_governance_report(root: Path, result: dict[str, Any]) -> Path:
    reports_dir = root / "reviews" / "governance"
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{result['governance_id']}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def _write_batch_governance_report(root: Path, result: dict[str, Any]) -> Path:
    reports_dir = root / "reviews" / "governance"
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{result['batch_id']}.batch.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def _trend_rows(days: int, raw_by_date: Counter[str], file_counts: dict[str, Counter[str]]) -> list[dict[str, Any]]:
    today = Date.today()
    rows: list[dict[str, Any]] = []
    for offset in range(days - 1, -1, -1):
        key = (today - timedelta(days=offset)).isoformat()
        rows.append(
            {
                "date": key,
                "raw_events": raw_by_date.get(key, 0),
                "concepts": file_counts["concepts"].get(key, 0),
                "playbooks": file_counts["playbooks"].get(key, 0),
                "outputs": file_counts["outputs"].get(key, 0),
                "conflicts": file_counts["conflicts"].get(key, 0),
            }
        )
    return rows


def _raw_event_counts_by_date(root: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    if not root.exists():
        return counts
    for path in root.glob("**/*.jsonl"):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except Exception:
                counts[_date_from_path_or_mtime(path)] += 1
                continue
            key = _normalize_date(payload.get("date")) or _date_from_ts_ms(payload.get("ts_ms")) or _date_from_path_or_mtime(path)
            counts[key] += 1
    return counts


def _file_counts_by_date(root: Path, pattern: str, *, exclude_readme: bool = False) -> Counter[str]:
    counts: Counter[str] = Counter()
    if not root.exists():
        return counts
    for path in root.glob(f"**/{pattern}"):
        if exclude_readme and path.name == "README.md":
            continue
        key = _date_from_file(path)
        counts[key] += 1
    return counts


def _conflict_type_rows(root: Path) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    if not root.exists():
        return []
    memory_root = root.parent
    for path in root.glob("**/*.json"):
        payload = _read_json(path)
        reason = str(payload.get("reason") or "unknown_conflict").strip() if payload else "invalid_conflict_json"
        row = buckets.setdefault(reason, {"reason": reason, "count": 0, "latest_path": "", "latest_date": ""})
        row["count"] += 1
        row["latest_path"] = str(path.relative_to(memory_root))
        row["latest_date"] = _date_from_file(path)
    return sorted(buckets.values(), key=lambda item: (-int(item["count"]), str(item["reason"])))


def _pending_confirmation_queue(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rows.extend(_pending_from_conflicts(root / "conflicts", root))
    rows.extend(_pending_from_raw_events(root / "raw", root))
    rows.extend(_pending_from_lifecycle_reviews(root.parent / "memory" / "memory_lifecycle.jsonl", root.parent))
    rows.sort(key=lambda item: str(item.get("date") or ""), reverse=True)
    return rows


def _pending_from_lifecycle_reviews(store_path: Path, memory_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not store_path.exists():
        return rows
    try:
        lines = store_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return rows
    for line in lines:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if not isinstance(payload, dict) or payload.get("status", "active") != "active":
            continue
        if payload.get("review_required") is not True:
            continue
        updated_at = int(payload.get("updated_at_ms") or payload.get("created_at_ms") or 0)
        rows.append(
            {
                "kind": "memory_lifecycle_review",
                "source": "memory_lifecycle",
                "reason": str(payload.get("review_reason") or "memory_review_required"),
                "summary": _memory_review_summary(payload),
                "path": str(store_path.relative_to(memory_root)) if _is_relative_to(store_path, memory_root) else str(store_path),
                "date": _date_from_ms(updated_at) or _date_from_file(store_path),
                "memory_id": str(payload.get("memory_id") or ""),
                "memory_type": str(payload.get("memory_type") or ""),
                "success_count": int(payload.get("success_count") or 0),
                "failure_count": int(payload.get("failure_count") or 0),
                "confidence": float(payload.get("confidence") or 0.0),
            }
        )
    return rows


def _memory_review_summary(payload: dict[str, Any]) -> str:
    content = str(payload.get("content") or "").strip()
    try:
        obj = json.loads(content)
        if isinstance(obj, dict) and obj.get("type") == "app_entity_correction":
            return f"Entity correction: {obj.get('surface_norm')} -> {obj.get('target_app')}"
    except Exception:
        pass
    return content[:160] or str(payload.get("memory_id") or "memory review required")


def _date_from_ms(value: int) -> str:
    if value <= 0:
        return ""
    try:
        return datetime.fromtimestamp(value / 1000).date().isoformat()
    except Exception:
        return ""


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _pending_from_conflicts(conflict_root: Path, memory_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not conflict_root.exists():
        return rows
    for path in conflict_root.glob("**/*.json"):
        payload = _read_json(path)
        if not payload:
            continue
        governance = payload.get("governance") if isinstance(payload.get("governance"), dict) else {}
        status = str(governance.get("status") or "")
        if status in ("confirmed", "rejected"):
            continue
        defer_until = _parse_date(str(governance.get("defer_until") or ""))
        if status == "deferred" and defer_until and defer_until >= Date.today():
            continue
        reason = str(payload.get("reason") or "")
        candidate = payload.get("candidate") if isinstance(payload.get("candidate"), dict) else {}
        if reason == "requires_user_confirmation" or candidate.get("requires_user_confirmation") is True:
            rows.append(
                {
                    "kind": "concept_confirmation",
                    "source": "conflict",
                    "reason": reason or "requires_user_confirmation",
                    "summary": str(candidate.get("summary") or candidate.get("candidate_id") or path.stem),
                    "path": str(path.relative_to(memory_root)),
                    "date": str(payload.get("date") or _date_from_file(path)),
                }
            )
    return rows


def _pending_from_raw_events(raw_root: Path, memory_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not raw_root.exists():
        return rows
    for path in raw_root.glob("**/*.jsonl"):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            event_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
            closure = event_payload.get("closure") if isinstance(event_payload.get("closure"), dict) else {}
            pending = closure.get("pending_decision") if isinstance(closure.get("pending_decision"), dict) else None
            if not pending:
                continue
            rows.append(
                {
                    "kind": "pending_decision",
                    "source": str(payload.get("source") or "raw_event"),
                    "reason": "pending_user_decision",
                    "summary": str(pending.get("summary") or pending.get("title") or pending.get("decision_id") or "待用户确认"),
                    "path": str(path.relative_to(memory_root)),
                    "date": _normalize_date(payload.get("date")) or _date_from_file(path),
                }
            )
    return rows


def _stale_concepts(root: Path, *, stale_after_days: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not root.exists():
        return rows
    memory_root = root.parent
    threshold = Date.today() - timedelta(days=max(1, stale_after_days))
    for path in root.glob("**/*.md"):
        if path.name == "README.md":
            continue
        text = _read_text(path)
        frontmatter = _frontmatter(text)
        summary = str(frontmatter.get("summary") or _first_heading(text) or path.stem).strip()
        last_verified = _parse_date(str(frontmatter.get("last_verified") or frontmatter.get("created_at") or ""))
        valid_until = _parse_date(str(frontmatter.get("valid_until") or ""))
        reason = ""
        ref_date: Date | None = None
        if valid_until and valid_until < Date.today():
            reason = "valid_until_expired"
            ref_date = valid_until
        elif last_verified and last_verified < threshold:
            reason = "last_verified_stale"
            ref_date = last_verified
        elif not last_verified and _mtime_date(path) < threshold:
            reason = "mtime_stale_without_verification"
            ref_date = _mtime_date(path)
        if reason:
            rows.append(
                {
                    "summary": summary,
                    "reason": reason,
                    "date": ref_date.isoformat() if ref_date else "",
                    "path": str(path.relative_to(memory_root)),
                }
            )
    rows.sort(key=lambda item: str(item.get("date") or ""))
    return rows


def _failure_pattern_rows(root: Path) -> list[dict[str, Any]]:
    counter: dict[str, dict[str, Any]] = defaultdict(lambda: {"pattern": "", "count": 0, "examples": []})
    for row in _conflict_type_rows(root / "conflicts"):
        reason = str(row.get("reason") or "unknown_conflict")
        if reason in ("requires_user_confirmation", "low_confidence"):
            continue
        item = counter[f"conflict:{reason}"]
        item["pattern"] = f"conflict:{reason}"
        item["count"] += int(row.get("count") or 0)
        item["examples"].append(row.get("latest_path"))
    _collect_failed_closures(root / "raw", root, counter)
    rows = list(counter.values())
    for row in rows:
        row["examples"] = [item for item in row["examples"] if item][:5]
    return sorted(rows, key=lambda item: (-int(item["count"]), str(item["pattern"])))


def _collect_failed_closures(raw_root: Path, memory_root: Path, counter: dict[str, dict[str, Any]]) -> None:
    if not raw_root.exists():
        return
    for path in raw_root.glob("**/*.jsonl"):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            event_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
            closure = event_payload.get("closure") if isinstance(event_payload.get("closure"), dict) else {}
            if str(closure.get("verification_status") or "").lower() != "failed":
                continue
            reason = str(closure.get("failure_reason") or closure.get("final_text") or "verification_failed")[:80]
            key = f"failed_turn:{reason}"
            row = counter[key]
            row["pattern"] = key
            row["count"] += 1
            row["examples"].append(str(path.relative_to(memory_root)))


def _frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    raw = text[3:end].strip()
    out: dict[str, Any] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _safe_memory_growth_path(root: Path, value: Any) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    raw = Path(text)
    path = raw if raw.is_absolute() else root / raw
    try:
        resolved_root = root.resolve()
        resolved = path.resolve()
    except OSError:
        return None
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"path outside memory_growth root: {text}") from exc
    return resolved


def _safe_segment(value: str) -> str:
    raw = str(value or "").strip().lower()
    clean = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in raw)
    while "__" in clean:
        clean = clean.replace("__", "_")
    return clean.strip("_") or "item"


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _escape_frontmatter(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"')


def _iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _upsert_frontmatter_field(text: str, key: str, value: str) -> str:
    line = f'{key}: "{_escape_frontmatter(value)}"'
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


def _append_update_log(text: str, line: str) -> str:
    entry = f"- {line.strip()}\n"
    marker = "## Update Log"
    if marker in text:
        idx = text.find(marker)
        after = text.find("\n", idx)
        if after >= 0:
            return text[: after + 1] + entry + text[after + 1 :]
    suffix = "" if text.endswith("\n") else "\n"
    return text + suffix + "\n## Update Log\n\n" + entry


def _normalize_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = _parse_date(text)
    return parsed.isoformat() if parsed else None


def _parse_date(text: str) -> Date | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    for chunk in (raw[:10], raw):
        for fmt in ("%Y-%m-%d", "%Y%m%d"):
            try:
                return datetime.strptime(chunk, fmt).date()
            except ValueError:
                pass
    return None


def _parse_datetime(text: str) -> datetime | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        pass
    parsed_date = _parse_date(raw)
    return datetime.combine(parsed_date, datetime.min.time()) if parsed_date else None


def _date_from_ts_ms(value: Any) -> str | None:
    try:
        ts = float(value) / 1000.0
        return datetime.fromtimestamp(ts).date().isoformat()
    except Exception:
        return None


def _date_from_file(path: Path) -> str:
    text = _read_text(path) if path.suffix.lower() == ".md" else ""
    frontmatter = _frontmatter(text) if text else {}
    for key in ("date", "created_at", "generated_at", "last_verified"):
        normalized = _normalize_date(frontmatter.get(key))
        if normalized:
            return normalized
    return _date_from_path_or_mtime(path)


def _date_from_path_or_mtime(path: Path) -> str:
    match = None
    for part in [path.name, *path.parts[-4:]]:
        if len(part) >= 8:
            import re

            match = re.search(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})", part)
            if match:
                return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return _mtime_date(path).isoformat()


def _iso_from_mtime(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        return _iso_now()


def _mtime_date(path: Path) -> Date:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).date()
    except OSError:
        return Date.today()
