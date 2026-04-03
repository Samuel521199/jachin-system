"""planning_gate_phase、entity_resolver、execution_tier 轻量单测。"""
from __future__ import annotations

from l3_node.intent_gateway.entity_resolver import try_resolve_entity_candidates_sync
from l3_node.intent_gateway.execution_tier import compute_execution_tier
from l3_node.intent_gateway.planning_gate_phase import extract_needs_info


def test_extract_needs_info() -> None:
    assert extract_needs_info("x [Needs_Info: 缺少仓库名] y") == "缺少仓库名"
    assert extract_needs_info("none") is None


def test_execution_tier_mandatory() -> None:
    t, sig = compute_execution_tier(
        user_input="hi",
        classification_text="hi",
        bundle_extra={"gateway_planning_mandatory": True},
    )
    assert t == "composite" and sig.get("reason") == "gateway_planning_mandatory"


def test_entity_resolver_resolved() -> None:
    r = try_resolve_entity_candidates_sync(
        [
            {"id": "a1", "label": "北京", "score": 0.7},
            {"id": "a2", "label": "上海", "score": 0.65},
        ],
        "北京",
        min_margin=0.08,
    )
    assert r.get("resolved") is True
    assert r.get("choice_id") == "a1"


def test_entity_resolver_ambiguous_margin() -> None:
    r = try_resolve_entity_candidates_sync(
        [
            {"id": "a1", "label": "北京朝阳", "score": 0.51},
            {"id": "a2", "label": "北京海淀", "score": 0.50},
        ],
        "北京",
        min_margin=0.15,
    )
    assert r.get("ambiguous") is True
