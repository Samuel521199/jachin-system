"""
execution_tier：simple | composite | executing（executing 由下游 planning_composite_released 表示）。
全局模糊度启发式 + gateway_planning_mandatory / 多步话术等信号。
"""
from __future__ import annotations

import re
from typing import Any

_VAGUE = re.compile(
    r"分析|报告|瓶颈|方案|调研|架构|重构|路线图|roadmap|评估|设计.{0,6}系统|根因|优化建议|全面|梳理",
    re.IGNORECASE,
)


def compute_execution_tier(
    *,
    user_input: str,
    classification_text: str,
    bundle_extra: dict[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    """
    返回 (tier, signals)。tier ∈ {simple, composite}。
    """
    extra = bundle_extra if isinstance(bundle_extra, dict) else {}
    signals: dict[str, Any] = {}

    if extra.get("gateway_planning_mandatory"):
        signals["reason"] = "gateway_planning_mandatory"
        return "composite", signals

    ids = extra.get("validated_subintent_ids")
    if isinstance(ids, list) and len(ids) > 1:
        signals["reason"] = "dag_multi_subintent"
        return "composite", signals

    try:
        from l3_node.task_plan_policy import user_message_suggests_multi_step_task

        if user_message_suggests_multi_step_task(user_input or ""):
            signals["reason"] = "multi_step_user_heuristic"
            return "composite", signals
    except Exception:
        pass

    u = (user_input or "").strip()
    c = (classification_text or "").strip()
    if _VAGUE.search(u) or _VAGUE.search(c):
        signals["vague_surface"] = True

    try:
        from l3_node.intent_gateway.config import get_intent_gateway_config

        cfg = get_intent_gateway_config()
        if bool(cfg.get("force_planning_phase_first", False)):
            if len(u) >= int(cfg.get("force_planning_min_user_chars", 400) or 400):
                signals["reason"] = "force_planning_phase_first_long_input"
                return "composite", signals
            if signals.get("vague_surface") and bool(cfg.get("vague_task_treat_as_composite", False)):
                signals["reason"] = "vague_task_treat_as_composite"
                return "composite", signals
        elif signals.get("vague_surface") and bool(cfg.get("vague_task_treat_as_composite", False)):
            signals["reason"] = "vague_task_treat_as_composite"
            return "composite", signals
    except Exception:
        pass
    return "simple", signals
