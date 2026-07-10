"""
§4 插件化意图注册表：L1 级 preflight 短路（优先级排序）。
支持 required_slots：命中意图但缺槽时拦截 RoleExecutionAgent，返回追问文案并进入 AWAITING_CLARIFICATION。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from l3_node.intent_gateway.bundle import GatewayContextBundle

logger = logging.getLogger(__name__)

MatchFn = Callable[["GatewayContextBundle", dict[str, Any]], bool]
HandleFn = Callable[["GatewayContextBundle", dict[str, Any]], Awaitable[Optional[str]]]


@dataclass(order=True)
class PreflightEntry:
    priority: int
    skill_id: str = field(compare=False)
    match: MatchFn = field(compare=False)
    handle: HandleFn = field(compare=False)
    """first_party | third_party | experimental — 第三方默认需 l1_sandbox_allow_third_party。"""
    l1_tier: str = field(compare=False, default="first_party")
    required_slots: list[dict[str, Any]] = field(compare=False, default_factory=list)
    """
    每项：name（必填）、pattern（正则，推荐）、prompt_template（追问句）、hint/description（可选）。
    """
    defer_to_role_execution_on_success: bool = field(compare=False, default=False)
    """match 且槽位已齐、handle 返回 None 时，终止整条 preflight 链，交给 RoleExecutionAgent（不继续尝试后续条目）。"""


class IntentRegistry:
    def __init__(self) -> None:
        self._preflights: List[PreflightEntry] = []

    def register_preflight(
        self,
        skill_id: str,
        *,
        priority: int,
        match: MatchFn,
        handle: HandleFn,
        l1_tier: str = "first_party",
        required_slots: list[dict[str, Any]] | None = None,
        defer_to_role_execution_on_success: bool = False,
    ) -> None:
        try:
            from l3_node.intent_gateway.config import get_intent_gateway_config

            if bool(get_intent_gateway_config().get("l1_enforce_skill_id_shape", False)):
                if (l1_tier or "first_party") == "first_party":
                    from l3_node.intent_gateway.l1_eligibility import assert_preflight_skill_id_eligible

                    assert_preflight_skill_id_eligible(skill_id)
        except ValueError:
            raise
        except Exception:
            pass
        rs = list(required_slots) if required_slots else []
        self._preflights.append(
            PreflightEntry(
                priority,
                skill_id,
                match,
                handle,
                l1_tier,
                rs,
                defer_to_role_execution_on_success,
            )
        )
        self._preflights.sort()

    async def run_preflights(
        self,
        bundle: "GatewayContextBundle",
        ctx: dict[str, Any],
    ) -> Optional[str]:
        try:
            from l3_node.intent_gateway.config import get_intent_gateway_config

            _cfg = get_intent_gateway_config()
            _sandbox_tp = bool(_cfg.get("l1_sandbox_allow_third_party", False))
            _slot_gate = bool(_cfg.get("slot_gating_enabled", True))
        except Exception:
            _cfg = {}
            _sandbox_tp = False
            _slot_gate = True

        _lc = str(ctx.get("lark_cid") or "").strip()
        if _lc and not (bundle.session_id or "").strip():
            bundle.session_id = _lc

        for ent in self._preflights:
            try:
                if ent.l1_tier == "third_party" and not _sandbox_tp:
                    continue
                if not ent.match(bundle, ctx):
                    continue

                if _slot_gate and ent.required_slots:
                    from l3_node.intent_gateway.slot_filling_guard import (
                        bump_slot_clarification_round,
                        reset_slot_clarification_rounds,
                        try_slot_filling_degradation,
                    )
                    from l3_node.intent_gateway.slot_specs import get_missing_for_entry
                    from l3_node.intent_gateway.bundle import SystemState

                    missing = get_missing_for_entry(ent, bundle)
                    if missing:
                        bundle.extra["slot_filling_active"] = True
                        bundle.extra["pending_required_slots"] = [str(m.get("name") or "") for m in missing if isinstance(m, dict)]
                        hit_abort, abort_msg = try_slot_filling_degradation(bundle, ent.skill_id)
                        if hit_abort:
                            return abort_msg

                        bump_slot_clarification_round(bundle, ent.skill_id)
                        bundle.system_state = SystemState.AWAITING_CLARIFICATION
                        bundle.clarification_handle = f"slots:{ent.skill_id}"
                        try:
                            ttl = float(_cfg.get("slot_clarification_ttl_seconds", 600.0))
                        except (TypeError, ValueError):
                            ttl = 600.0
                        bundle.clarification_deadline_ts = time.time() + max(30.0, ttl)

                        q = await _render_slot_clarification_question(ent, missing, bundle, ctx)
                        bundle.extra["slot_clarification_last_reply"] = q
                        logger.info(
                            "[IntentRegistry] 槽位未齐 skill=%s missing=%s round=%s",
                            ent.skill_id,
                            bundle.extra.get("pending_required_slots"),
                            bundle.extra.get("slot_clarification_rounds"),
                        )
                        return q

                    reset_slot_clarification_rounds(bundle, ent.skill_id)

                out = await ent.handle(bundle, ctx)
                if out is not None:
                    logger.info("[IntentRegistry] preflight hit skill=%s", ent.skill_id)
                    return out
                if ent.defer_to_role_execution_on_success:
                    logger.info("[IntentRegistry] preflight defer RoleExecutionAgent skill=%s", ent.skill_id)
                    return None
            except Exception as e:
                logger.warning("[IntentRegistry] preflight %s 异常: %s", ent.skill_id, e)
        return None


_GLOBAL = IntentRegistry()


async def _render_slot_clarification_question(
    ent: PreflightEntry,
    missing: list[dict[str, Any]],
    bundle: "GatewayContextBundle",
    ctx: dict[str, Any],
) -> str:
    from l3_node.intent_gateway.slot_clarification_llm import (
        generate_slot_clarification_async,
        template_clarification_reply,
    )
    from l3_node.intent_gateway.slot_specs import combined_slot_probe_text

    engine = ctx.get("engine")
    llm_q = await generate_slot_clarification_async(
        skill_id=ent.skill_id,
        user_input=bundle.user_input or "",
        probe_text=combined_slot_probe_text(bundle),
        missing=missing,
        engine=engine,
    )
    if llm_q:
        return llm_q
    return template_clarification_reply(ent.skill_id, missing)


def get_intent_registry() -> IntentRegistry:
    return _GLOBAL


async def run_registered_preflights(
    bundle: "GatewayContextBundle",
    ctx: dict[str, Any],
) -> Optional[str]:
    return await _GLOBAL.run_preflights(bundle, ctx)
