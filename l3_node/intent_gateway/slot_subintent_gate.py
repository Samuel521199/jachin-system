"""
DAG 子意图上的 slot_schema（与 Registry required_slots 同形）门控：非仅 preflight 表驱动，全量在入站合并检测。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

SUBINTENT_DAG_SKILL_ID = "subintent:dag"


async def maybe_subintent_slot_gate_async(
    bundle: Any,
    ctx: dict[str, Any],
) -> Optional[str]:
    """
    若 validated_subintents 合并后的 slot_schema 有缺，返回追问文案；否则 None。
    """
    if bundle is None:
        return None
    try:
        from l3_node.intent_gateway.config import get_intent_gateway_config

        if not bool(get_intent_gateway_config().get("subintent_slot_gate_enabled", True)):
            return None
        _cfg = get_intent_gateway_config()
        _slot_gate = bool(_cfg.get("slot_gating_enabled", True))
    except Exception:
        _cfg = {}
        _slot_gate = True

    if not _slot_gate:
        return None

    extra = getattr(bundle, "extra", None) or {}
    nodes = extra.get("validated_subintents")
    if not isinstance(nodes, list) or not nodes:
        return None

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for n in nodes:
        if not isinstance(n, dict):
            continue
        raw = n.get("slot_schema")
        if not isinstance(raw, list):
            continue
        for s in raw:
            if not isinstance(s, dict):
                continue
            nm = str(s.get("name") or "").strip()
            if not nm or nm in seen:
                continue
            seen.add(nm)
            merged.append(s)

    if not merged:
        return None

    from l3_node.intent_gateway.bundle import SystemState
    from l3_node.intent_gateway.intent_tracker import emit_intent_tracker_event
    from l3_node.intent_gateway.slot_filling_guard import (
        bump_slot_clarification_round,
        reset_slot_clarification_rounds,
        try_slot_filling_degradation,
    )
    from l3_node.intent_gateway.slot_specs import combined_slot_probe_text, missing_required_slots

    missing = missing_required_slots(merged, combined_slot_probe_text(bundle))
    if not missing:
        reset_slot_clarification_rounds(bundle, SUBINTENT_DAG_SKILL_ID)
        return None

    bundle.extra["slot_filling_active"] = True
    bundle.extra["pending_required_slots"] = [str(m.get("name") or "") for m in missing if isinstance(m, dict)]
    hit_abort, abort_msg = try_slot_filling_degradation(bundle, SUBINTENT_DAG_SKILL_ID)
    if hit_abort:
        emit_intent_tracker_event("subintent_slot_abort", {"missing": bundle.extra.get("pending_required_slots")})
        return abort_msg

    bump_slot_clarification_round(bundle, SUBINTENT_DAG_SKILL_ID)
    bundle.system_state = SystemState.AWAITING_CLARIFICATION
    bundle.clarification_handle = f"slots:{SUBINTENT_DAG_SKILL_ID}"
    try:
        ttl = float(_cfg.get("slot_clarification_ttl_seconds", 600.0))
    except (TypeError, ValueError):
        ttl = 600.0
    bundle.clarification_deadline_ts = time.time() + max(30.0, ttl)

    from l3_node.intent_gateway.slot_clarification_llm import (
        generate_slot_clarification_async,
        template_clarification_reply,
    )
    from l3_node.intent_gateway.slot_specs import combined_slot_probe_text

    _engine = ctx.get("engine")
    q = await generate_slot_clarification_async(
        skill_id=SUBINTENT_DAG_SKILL_ID,
        user_input=bundle.user_input or "",
        probe_text=combined_slot_probe_text(bundle),
        missing=missing,
        engine=_engine,
    )
    if not q:
        q = template_clarification_reply(SUBINTENT_DAG_SKILL_ID, missing)
    bundle.extra["slot_clarification_last_reply"] = q
    emit_intent_tracker_event("subintent_slot_clarify", {"missing": bundle.extra.get("pending_required_slots")})
    logger.info(
        "[SubIntentSlotGate] 槽位未齐 missing=%s",
        bundle.extra.get("pending_required_slots"),
    )
    return q
