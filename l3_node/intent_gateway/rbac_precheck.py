"""
§6.4 L2 派发前 RBAC 预检桩：可接 IAM / tenant 策略。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from l3_node.intent_gateway.bundle import GatewayContextBundle

logger = logging.getLogger(__name__)


def precheck_l2_subintent_allowed(
    bundle: "GatewayContextBundle",
    *,
    locality: str,
    rbac_scope_hint: str = "",
    extra: Optional[dict[str, Any]] = None,
) -> tuple[bool, str]:
    """
    返回 (allowed, reason_code)。
    nexus intent_gateway.rbac_l2_precheck_enabled=False 时恒 True。
    """
    try:
        from l3_node.intent_gateway.config import get_intent_gateway_config

        if not bool(get_intent_gateway_config().get("rbac_l2_precheck_enabled", False)):
            return True, "precheck_disabled"
    except Exception:
        return True, "precheck_skip"
    _ = rbac_scope_hint
    tid = (bundle.tenant_id or "").strip()
    if locality in ("require_l2_task_manager", "prefer_l2"):
        if not tid:
            logger.warning("[RBACPrecheck] 拒绝 L2：缺 tenant_id locality=%s", locality)
            bundle.extra["rbac_precheck"] = {"allowed": False, "reason": "tenant_required_for_l2"}
            return False, "tenant_required_for_l2"
        logger.debug("[RBACPrecheck] L2 locality=%s tenant=%s ok", locality, tid[:16])
        bundle.extra["rbac_precheck"] = {"allowed": True, "reason": "ok", "tenant_prefix": tid[:16]}
    if extra and isinstance(extra, dict) and extra.get("force_deny_l2"):
        return False, "policy_force_deny_l2"
    return True, "ok"
