"""
L0 全局逃生舱：无论 Planning / 槽位澄清 / 消解态，优先匹配逃生词即复位会话网关状态。
规格见 docs/07_memory_first_main_agent_and_voice_app_agents.md。
"""
from __future__ import annotations

import logging
from typing import Any

from l3_node.intent_gateway.bundle import GatewayContextBundle, SystemState

logger = logging.getLogger(__name__)

_DEFAULT_ESCAPE_KW = (
    "取消",
    "重置",
    "reset",
    "abort",
    "算了",
    "全停下",
    "别干了",
    "停",
)

# 放行执行链前要从 bundle.extra 剥掉的规划/澄清挂起键（磁盘 task_plan.md 须由执行面或用户另行处理）
_EXTRA_KEYS_CLEAR_ON_ESCAPE: tuple[str, ...] = (
    "gateway_planning_mandatory",
    "gateway_dag_cycle_detected",
    "gateway_dag_cycle_detail",
    "dag_dependency_analysis",
    "validated_subintents",
    "validated_subintent_ids",
    "slot_clarification_rounds",
    "slot_filling_active",
    "pending_required_slots",
    "slot_filling_abort_pending",
    "clarification_gate",
    "entity_resolution_candidates",
)


def global_escape_triggered(user_input: str, keywords: list[str]) -> bool:
    """
    短句优先：整句较短且包含任一关键词则视为逃生；较长句仅当以关键词开头时触发，避免「长文里出现取消」误触。
    """
    s = (user_input or "").strip()
    if not s:
        return False
    low = s.casefold()
    short_max = 56
    for k in keywords:
        kk = (k or "").strip()
        if not kk:
            continue
        kl = kk.casefold()
        if kl not in low:
            continue
        if len(s) <= short_max:
            return True
        if low.startswith(kl):
            return True
    return False


def apply_global_escape_hatch(
    bundle: GatewayContextBundle,
    user_input: str,
) -> dict[str, Any]:
    """
    若命中逃生词：system_state→NORMAL，清空 clarification_*，清理 extra 中规划/槽位挂起键。
    返回 {"escaped": bool, "keyword": str|None, ...} 供观测。
    """
    out: dict[str, Any] = {"escaped": False, "keyword": None}
    try:
        from l3_node.intent_gateway.config import get_intent_gateway_config

        cfg = get_intent_gateway_config()
        if not bool(cfg.get("global_escape_hatch_enabled", True)):
            return out
        raw = cfg.get("global_escape_keywords")
        if isinstance(raw, list) and raw:
            kws = [str(x).strip() for x in raw if str(x).strip()]
        else:
            kws = list(_DEFAULT_ESCAPE_KW)
    except Exception:
        kws = list(_DEFAULT_ESCAPE_KW)

    if not global_escape_triggered(user_input, kws):
        return out

    hit = None
    low = (user_input or "").strip().casefold()
    for k in kws:
        if k.casefold() in low:
            hit = k
            break

    bundle.system_state = SystemState.NORMAL
    bundle.clarification_handle = ""
    bundle.clarification_deadline_ts = 0.0
    try:
        from l3_node.intent_gateway.slot_filling_session import clear_all_for_bundle

        clear_all_for_bundle(bundle)
    except Exception:
        pass
    for ek in _EXTRA_KEYS_CLEAR_ON_ESCAPE:
        bundle.extra.pop(ek, None)
    bundle.extra["global_escape_hatch"] = {"action": "reset_to_normal", "matched": hit}
    out["escaped"] = True
    out["keyword"] = hit
    logger.info("[GlobalEscape] L0 复位 correlation=%s matched=%r", bundle.correlation_id[:12], hit)
    return out
