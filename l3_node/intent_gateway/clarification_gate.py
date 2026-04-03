"""
§10.3 / §11.2：澄清态 TTL、打断词退出、轻量语义漂移（与助理问域重叠过低则终止澄清挂起）。

与「参谋长」软拦截话术对齐：面向用户的追问正文由槽位门闸（slot_subintent_gate / slot_clarification_llm）
与 task_plan / planning_composite 系统消息统一为【情报汇整】+【行动预案】范式（见 pushback_copy）。
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from l3_node.intent_gateway.bundle import GatewayContextBundle, SystemState

logger = logging.getLogger(__name__)

_DEFAULT_INTERRUPT = (
    "换个话题",
    "算了",
    "不问了",
    "不管了",
    "等等再说",
    "先别说这个",
    "别问了",
    "不说了",
    "取消澄清",
)

# 助理侧「选项/澄清」弱信号
_CLARIFY_HINT_RE = re.compile(r"[?？]|还是|或者|请选择|哪(?:个|种|项)|是否|要不要", re.UNICODE)


def _char_bigrams(s: str) -> set[str]:
    s = re.sub(r"\s+", "", s, flags=re.UNICODE)
    if len(s) < 2:
        return set()
    return {s[i : i + 2] for i in range(len(s) - 1)}


def _overlap_ratio(a: str, b: str) -> float:
    ga, gb = _char_bigrams(a), _char_bigrams(b)
    if not ga or not gb:
        return 0.0
    inter = len(ga & gb)
    return inter / max(len(ga), 1)


def apply_clarification_gate(
    bundle: GatewayContextBundle,
    user_input: str,
    prior_messages: list[dict[str, Any]],
    *,
    now_ts: float | None = None,
) -> dict[str, Any]:
    """
    就地可能修改 bundle.system_state / clarification_* / extra。
    返回摘要 dict 供可观测日志使用。
    """
    out: dict[str, Any] = {"state_in": str(bundle.system_state)}
    if bundle.system_state != SystemState.AWAITING_CLARIFICATION:
        out["action"] = "not_clarifying"
        return out

    now = float(now_ts if now_ts is not None else time.time())
    ui = (user_input or "").strip()

    try:
        from l3_node.intent_gateway.config import get_intent_gateway_config

        cfg = get_intent_gateway_config()
        interrupt_kw = cfg.get("clarification_interrupt_keywords")
        if not isinstance(interrupt_kw, list) or not interrupt_kw:
            interrupt_kw = list(_DEFAULT_INTERRUPT)
        drift_thr = float(cfg.get("clarification_drift_overlap_min", 0.06))
    except Exception:
        interrupt_kw = list(_DEFAULT_INTERRUPT)
        drift_thr = 0.06

    # TTL：deadline 已过则结束澄清态
    if bundle.clarification_deadline_ts > 0.0 and now > bundle.clarification_deadline_ts:
        bundle.system_state = SystemState.NORMAL
        bundle.clarification_handle = ""
        bundle.extra["clarification_gate"] = {"action": "ttl_expired"}
        out.update({"action": "ttl_expired", "state_out": str(bundle.system_state)})
        logger.info("[ClarificationGate] TTL 过期，恢复 NORMAL correlation=%s", bundle.correlation_id[:12])
        return out

    # 打断词
    for kw in interrupt_kw:
        if isinstance(kw, str) and kw and kw in ui:
            bundle.system_state = SystemState.NORMAL
            bundle.clarification_handle = ""
            bundle.extra["clarification_gate"] = {"action": "interrupt_keyword", "keyword": kw[:32]}
            out.update({"action": "interrupt_keyword", "state_out": str(bundle.system_state)})
            logger.info("[ClarificationGate] 打断词退出 keyword=%s", kw[:32])
            return out

    # §7.3：实体消解优先于漂移（候选由上游写入 bundle.extra["entity_resolution_candidates"]）
    _cand = bundle.extra.get("entity_resolution_candidates")
    if ui and isinstance(_cand, list) and _cand:
        try:
            from l3_node.intent_gateway.config import get_intent_gateway_config

            margin = float(get_intent_gateway_config().get("entity_resolver_min_top1_top2_margin", 0.08))
        except (TypeError, ValueError):
            margin = 0.08
        try:
            from l3_node.intent_gateway.entity_resolver import try_resolve_entity_candidates_sync
            from l3_node.intent_gateway.intent_tracker import emit_intent_tracker_event

            er = try_resolve_entity_candidates_sync(_cand, ui, min_margin=margin)
            if er.get("resolved"):
                bundle.system_state = SystemState.NORMAL
                bundle.clarification_handle = ""
                bundle.extra["entity_resolution_result"] = er
                bundle.extra.pop("entity_resolution_candidates", None)
                bundle.extra["clarification_gate"] = {
                    "action": "entity_resolved",
                    "choice_id": er.get("choice_id"),
                    "label": er.get("label"),
                }
                emit_intent_tracker_event("entity_resolver_resolved", {"choice_id": er.get("choice_id")})
                out.update({"action": "entity_resolved", "state_out": str(bundle.system_state)})
                logger.info("[ClarificationGate] 实体消解命中 id=%s", er.get("choice_id"))
                return out
            if er.get("ambiguous"):
                bundle.extra["clarification_gate"] = {"action": "entity_ambiguous", "detail": er}
                emit_intent_tracker_event("entity_resolver_ambiguous", {"reason": er.get("reason")})
                out["entity_gate"] = "ambiguous"
        except Exception as e:
            logger.debug("[ClarificationGate] entity_resolution 跳过: %s", e)

    # 漂移：短答且与最近助理文本重叠极低，而助理像澄清问句
    last_a = ""
    for m in reversed(prior_messages or []):
        if (m.get("role") or "").strip().lower() == "assistant":
            c = m.get("content")
            last_a = (c if isinstance(c, str) else str(c or "")).strip()[:600]
            break

    if (
        ui
        and len(ui) <= 64
        and last_a
        and _CLARIFY_HINT_RE.search(last_a)
        and _overlap_ratio(ui, last_a) < drift_thr
    ):
        bundle.system_state = SystemState.NORMAL
        bundle.clarification_handle = ""
        ov = round(_overlap_ratio(ui, last_a), 4)
        bundle.extra["clarification_gate"] = {
            "action": "drift_abort",
            "overlap": ov,
        }
        out.update({"action": "drift_abort", "overlap": ov, "state_out": str(bundle.system_state)})
        logger.info("[ClarificationGate] 漂移检测结束澄清 overlap=%s", ov)
        return out

    out["action"] = "passthrough_clarification"
    out["state_out"] = str(bundle.system_state)
    return out
