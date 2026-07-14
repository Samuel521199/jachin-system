"""Memory Growth pipeline scheduler.

This module coordinates the lightweight self-growing knowledge loop:

DailyReview -> ConceptCurator -> PlaybookBuilder -> OutputReview.

It is intentionally callable from scripts, UI buttons, or future background
schedulers without tying the growth pipeline to the live chat critical path.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .concept_curator import ConceptCuratorResult, apply_concept_patch
from .daily_review import DailyReviewResult, run_daily_review
from .graph_connectors import GraphConnectorResult, sync_graph_engine_connectors
from .graph_sync_adapter import GraphSyncResult, sync_memory_growth_graph
from .memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
from .output_review import OutputReviewResult, apply_output_patch
from .playbook_builder import PlaybookBuilderResult, apply_playbook_patch
from .weekly_review import WeeklyReviewResult, run_weekly_review


@dataclass(slots=True)
class GrowthPipelineResult:
    run_id: str
    date: str
    daily_review: DailyReviewResult
    concept_result: ConceptCuratorResult | None
    playbook_result: PlaybookBuilderResult | None
    output_result: OutputReviewResult | None
    weekly_result: WeeklyReviewResult | None
    graph_sync_result: GraphSyncResult | None
    graph_connector_results: list[GraphConnectorResult]
    report_path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "date": self.date,
            "daily_review": self.daily_review.to_dict(),
            "concept_result": self.concept_result.to_dict() if self.concept_result else None,
            "playbook_result": self.playbook_result.to_dict() if self.playbook_result else None,
            "output_result": self.output_result.to_dict() if self.output_result else None,
            "weekly_result": self.weekly_result.to_dict() if self.weekly_result else None,
            "graph_sync_result": self.graph_sync_result.to_dict() if self.graph_sync_result else None,
            "graph_connector_results": [item.to_dict() for item in self.graph_connector_results],
            "report_path": str(self.report_path),
        }


def run_growth_pipeline(
    date: str | None = None,
    *,
    promote_concepts: bool = True,
    build_playbooks: bool = True,
    review_outputs: bool = True,
    weekly_lifecycle_review: bool = False,
    sync_graph: bool = False,
    graph_connector_ids: list[str] | None = None,
) -> GrowthPipelineResult:
    """Run one Memory Growth digestion pipeline and write a run report."""

    ensure_memory_growth_scaffold()
    run_id = f"growth_pipeline_{_now_stamp()}_{uuid.uuid4().hex[:8]}"
    daily = run_daily_review(date=date)
    concept_result = apply_concept_patch(daily.patch_path) if promote_concepts else None
    playbook_result = apply_playbook_patch(daily.patch_path) if build_playbooks else None
    output_result = apply_output_patch(daily.patch_path) if review_outputs else None
    weekly_result = run_weekly_review() if weekly_lifecycle_review else None
    graph_sync_result = sync_memory_growth_graph() if sync_graph else None
    graph_connector_results = sync_graph_engine_connectors(graph_connector_ids) if graph_sync_result else []
    report_path = _write_pipeline_report(
        run_id=run_id,
        daily=daily,
        concept_result=concept_result,
        playbook_result=playbook_result,
        output_result=output_result,
        weekly_result=weekly_result,
        graph_sync_result=graph_sync_result,
        graph_connector_results=graph_connector_results,
    )
    return GrowthPipelineResult(
        run_id=run_id,
        date=daily.date,
        daily_review=daily,
        concept_result=concept_result,
        playbook_result=playbook_result,
        output_result=output_result,
        weekly_result=weekly_result,
        graph_sync_result=graph_sync_result,
        graph_connector_results=graph_connector_results,
        report_path=report_path,
    )


def _write_pipeline_report(
    *,
    run_id: str,
    daily: DailyReviewResult,
    concept_result: ConceptCuratorResult | None,
    playbook_result: PlaybookBuilderResult | None,
    output_result: OutputReviewResult | None,
    weekly_result: WeeklyReviewResult | None,
    graph_sync_result: GraphSyncResult | None,
    graph_connector_results: list[GraphConnectorResult],
) -> Path:
    path = memory_growth_dir() / "reviews" / "pipeline_runs" / f"{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "run_id": run_id,
        "date": daily.date,
        "created_at": _iso_now(),
        "stages": {
            "daily_review": daily.to_dict(),
            "concept_curator": concept_result.to_dict() if concept_result else None,
            "playbook_builder": playbook_result.to_dict() if playbook_result else None,
            "output_review": output_result.to_dict() if output_result else None,
            "weekly_review": weekly_result.to_dict() if weekly_result else None,
            "graph_sync": graph_sync_result.to_dict() if graph_sync_result else None,
            "graph_connectors": [item.to_dict() for item in graph_connector_results],
        },
        "summary": {
            "raw_event_count": daily.raw_event_count,
            "concept_promoted": concept_result.promoted_count if concept_result else 0,
            "playbook_promoted": playbook_result.promoted_count if playbook_result else 0,
            "output_promoted": output_result.promoted_count if output_result else 0,
            "weekly_lifecycle_issues": (
                weekly_result.duplicate_cluster_count + weekly_result.stale_concept_count + weekly_result.weak_output_count + weekly_result.conflict_count
                if weekly_result
                else 0
            ),
            "graph_nodes": graph_sync_result.node_count if graph_sync_result else 0,
            "graph_edges": graph_sync_result.edge_count if graph_sync_result else 0,
            "graph_connectors_ok": sum(1 for item in graph_connector_results if item.ok),
            "graph_connectors_total": len(graph_connector_results),
            "warnings": list(daily.warnings),
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def _now_stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")
