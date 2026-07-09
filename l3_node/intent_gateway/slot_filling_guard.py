"""
槽位追问守卫：最大追问次数与降级协议（Abort / 转兜底）。
由 Slot-filling Tracker 在发送每一轮槽位追问前/后调用；规格见 docs/07_memory_first_main_agent_and_voice_app_agents.md。
"""
from __future__ import annotations

import logging
from typing import Tuple

from l3_node.intent_gateway.bundle import GatewayContextBundle, SystemState

logger = logging.getLogger(__name__)

_ROUND_KEY = "slot_clarification_rounds"


def get_slot_clarification_rounds(bundle: GatewayContextBundle, skill_id: str = "") -> int:
    if skill_id:
        from l3_node.intent_gateway.slot_filling_session import get_rounds

        n = get_rounds(bundle, skill_id)
        bundle.extra[_ROUND_KEY] = n
        return n
    try:
        n = int(bundle.extra.get(_ROUND_KEY) or 0)
    except (TypeError, ValueError):
        n = 0
    return max(0, n)


def bump_slot_clarification_round(bundle: GatewayContextBundle, skill_id: str = "") -> int:
    """在网关**即将向用户发送**下一轮槽位追问时调用。"""
    if skill_id:
        from l3_node.intent_gateway.slot_filling_session import bump_rounds

        n = bump_rounds(bundle, skill_id)
        bundle.extra[_ROUND_KEY] = n
        return n
    n = get_slot_clarification_rounds(bundle) + 1
    bundle.extra[_ROUND_KEY] = n
    return n


def reset_slot_clarification_rounds(bundle: GatewayContextBundle, skill_id: str = "") -> None:
    if skill_id:
        try:
            from l3_node.intent_gateway.slot_filling_session import clear_skill

            clear_skill(bundle, skill_id)
        except Exception:
            pass
    bundle.extra.pop(_ROUND_KEY, None)
    bundle.extra.pop("slot_filling_active", None)
    bundle.extra.pop("pending_required_slots", None)


def try_slot_filling_degradation(bundle: GatewayContextBundle, skill_id: str = "") -> Tuple[bool, str]:
    """
    若已超过 max_clarification_retries：复位澄清态、清空槽位挂起，返回 (True, 用户可见文案)。
    调用时机：在决定再发一轮槽位追问**之前**检查；若 True 则不应再追问。
    Registry 槽位路径须传入 **skill_id** 以读取跨请求持久化的轮次。
    """
    if not (skill_id or "").strip():
        return False, ""
    try:
        from l3_node.intent_gateway.config import get_intent_gateway_config

        cfg = get_intent_gateway_config()
        max_r = int(cfg.get("slot_filling_max_clarification_retries", 3))
    except (TypeError, ValueError):
        max_r = 3
    max_r = max(1, min(max_r, 20))

    rounds = get_slot_clarification_rounds(bundle, skill_id)
    if rounds < max_r:
        return False, ""

    try:
        from l3_node.intent_gateway.config import get_intent_gateway_config

        custom = str(get_intent_gateway_config().get("slot_filling_abort_reply_zh") or "").strip()
    except Exception:
        custom = ""
    msg = custom or (
        "[Abort_Intent] 缺少关键参数，已为您取消当前挂起操作。"
        "您可以直接用一句完整指令重试，或换一个问题继续。"
    )

    bundle.system_state = SystemState.NORMAL
    bundle.clarification_handle = ""
    bundle.clarification_deadline_ts = 0.0
    reset_slot_clarification_rounds(bundle, skill_id)
    bundle.extra["slot_filling_abort_pending"] = True
    bundle.extra["slot_filling_degraded"] = {
        "rounds": rounds,
        "max_retries": max_r,
        "action": "abort_intent",
    }
    logger.warning(
        "[SlotFillingGuard] 槽位追问超限 rounds=%s max=%s correlation=%s",
        rounds,
        max_r,
        bundle.correlation_id[:12],
    )
    return True, msg


def clear_slot_filling_abort_pending(bundle: GatewayContextBundle) -> None:
    """执行面在返回 Abort 文案后调用，避免重复触发。"""
    bundle.extra.pop("slot_filling_abort_pending", None)
