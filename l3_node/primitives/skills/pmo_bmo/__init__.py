"""PMO/BMO — L3 Skill（知识库同步流水线 + 预留效能监督扩展）

避免在包导入时加载 main_skill，消除 python -m l3_node.primitives.skills.pmo_bmo.main_skill 的 runpy RuntimeWarning。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = [
    "run_pmo_knowledge_sync",
    "is_pmo_bmo_intent",
    "run_pmo_export_scheduled_tables_only",
    "get_pmo_big_requirement_alignment_task_spec",
    "build_pmo_big_requirement_alignment_context",
    "run_pmo_big_requirement_alignment_task",
    "get_pmo_person_task_stats_task_spec",
    "build_pmo_person_task_stats_context",
    "run_pmo_person_task_stats_task",
    "get_pmo_dashboard_three_cards_task_spec",
    "run_pmo_full_business_pipeline",
    "run_pmo_output_docs_from_raw",
    "run_pmo_resource_monitoring",
]

if TYPE_CHECKING:
    from l3_node.primitives.skills.pmo_bmo.main_skill import (
        is_pmo_bmo_intent as is_pmo_bmo_intent,
        run_pmo_export_scheduled_tables_only as run_pmo_export_scheduled_tables_only,
        run_pmo_knowledge_sync as run_pmo_knowledge_sync,
    )
    from l3_node.primitives.skills.pmo_bmo.main_skill import (
        build_pmo_big_requirement_alignment_context as build_pmo_big_requirement_alignment_context,
        get_pmo_big_requirement_alignment_task_spec as get_pmo_big_requirement_alignment_task_spec,
        run_pmo_big_requirement_alignment_task as run_pmo_big_requirement_alignment_task,
    )
    from l3_node.primitives.skills.pmo_bmo.main_skill import (
        build_pmo_person_task_stats_context as build_pmo_person_task_stats_context,
        get_pmo_person_task_stats_task_spec as get_pmo_person_task_stats_task_spec,
        run_pmo_person_task_stats_task as run_pmo_person_task_stats_task,
    )
    from l3_node.primitives.skills.pmo_bmo.main_skill import run_pmo_full_business_pipeline as run_pmo_full_business_pipeline


def __getattr__(name: str) -> Any:
    if name == "run_pmo_knowledge_sync":
        from l3_node.primitives.skills.pmo_bmo.main_skill import run_pmo_knowledge_sync

        return run_pmo_knowledge_sync
    if name == "is_pmo_bmo_intent":
        from l3_node.primitives.skills.pmo_bmo.main_skill import is_pmo_bmo_intent

        return is_pmo_bmo_intent
    if name == "run_pmo_export_scheduled_tables_only":
        from l3_node.primitives.skills.pmo_bmo.main_skill import run_pmo_export_scheduled_tables_only

        return run_pmo_export_scheduled_tables_only
    if name == "get_pmo_big_requirement_alignment_task_spec":
        from l3_node.primitives.skills.pmo_bmo.main_skill import get_pmo_big_requirement_alignment_task_spec

        return get_pmo_big_requirement_alignment_task_spec
    if name == "build_pmo_big_requirement_alignment_context":
        from l3_node.primitives.skills.pmo_bmo.main_skill import build_pmo_big_requirement_alignment_context

        return build_pmo_big_requirement_alignment_context
    if name == "run_pmo_big_requirement_alignment_task":
        from l3_node.primitives.skills.pmo_bmo.main_skill import run_pmo_big_requirement_alignment_task

        return run_pmo_big_requirement_alignment_task
    if name == "get_pmo_person_task_stats_task_spec":
        from l3_node.primitives.skills.pmo_bmo.main_skill import get_pmo_person_task_stats_task_spec

        return get_pmo_person_task_stats_task_spec
    if name == "build_pmo_person_task_stats_context":
        from l3_node.primitives.skills.pmo_bmo.main_skill import build_pmo_person_task_stats_context

        return build_pmo_person_task_stats_context
    if name == "run_pmo_person_task_stats_task":
        from l3_node.primitives.skills.pmo_bmo.main_skill import run_pmo_person_task_stats_task

        return run_pmo_person_task_stats_task
    if name == "get_pmo_dashboard_three_cards_task_spec":
        from l3_node.primitives.skills.pmo_bmo.main_skill import get_pmo_dashboard_three_cards_task_spec

        return get_pmo_dashboard_three_cards_task_spec
    if name == "run_pmo_full_business_pipeline":
        from l3_node.primitives.skills.pmo_bmo.main_skill import run_pmo_full_business_pipeline

        return run_pmo_full_business_pipeline
    if name == "run_pmo_output_docs_from_raw":
        from l3_node.primitives.skills.pmo_bmo.main_skill import run_pmo_output_docs_from_raw

        return run_pmo_output_docs_from_raw
    if name == "run_pmo_resource_monitoring":
        from l3_node.primitives.skills.pmo_bmo.monitoring_skill import run_pmo_resource_monitoring

        return run_pmo_resource_monitoring
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
