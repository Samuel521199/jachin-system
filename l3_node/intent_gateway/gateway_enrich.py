"""
网关异步增强：Embedding 路由、可选小模型 routing 扩写、可选多模态侧路头。
在 `await apply_gateway_ingress_pipeline` 之后、最终 classification_text 定稿前调用。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def enrich_gateway_async(
    bundle: Any,
    engine: Any,
    user_input: str,
    prior_messages: list[dict[str, Any]],
) -> None:
    from l3_node.intent_gateway.config import get_intent_gateway_config
    from l3_node.intent_gateway.classification_llm import optional_rewrite_routing_utterance
    from l3_node.intent_gateway.embedding_router import compute_embedding_route_hint
    from l3_node.intent_gateway.multimodal_head import optional_multimodal_route_head

    cfg = get_intent_gateway_config()
    ctx = getattr(engine, "ctx", None)

    ct = (bundle.classification_text or bundle.user_input or "").strip()

    _poison_ui = False
    try:
        from l3_node.intent_gateway.ood_signals import user_input_looks_like_mixed_poison

        _poison_ui = user_input_looks_like_mixed_poison(user_input or "")
        if _poison_ui:
            bundle.extra["embedding_router_skipped_ood_input"] = True
    except Exception:
        pass

    if bool(cfg.get("embedding_router_enabled", False)) and not _poison_ui:
        try:
            emb = await compute_embedding_route_hint(ct, shared_ctx=ctx)
            if emb:
                bundle.extra["embedding_route"] = emb
                bundle.extra["embedding_ood_sparse"] = bool(emb.get("ood_sparse"))
        except Exception as e:
            logger.debug("[IntentGateway] embedding_router 跳过: %s", e)

    if bool(cfg.get("multimodal_routing_head_enabled", False)) and bundle.extra.get("attachment_has_image"):
        slots = bundle.extra.get("attachment_feature_slots") or []
        if isinstance(slots, list) and slots:
            try:
                to = float(cfg.get("multimodal_routing_head_timeout_sec", 6.0))
            except (TypeError, ValueError):
                to = 6.0
            mm = await optional_multimodal_route_head(
                user_text=user_input or "",
                feature_slots=slots,
                engine=engine,
                timeout_sec=to,
            )
            if mm:
                bundle.extra["multimodal_route_head"] = mm

    if bool(cfg.get("classification_llm_rewrite_enabled", False)):
        _skip_rewrite = False
        try:
            from l3_node.intent_gateway.ood_signals import user_input_looks_like_mixed_poison

            if user_input_looks_like_mixed_poison(user_input or ""):
                bundle.extra["classification_llm_rewrite_skipped_ood"] = True
                _skip_rewrite = True
        except Exception:
            pass
        if not _skip_rewrite:
            try:
                to = float(cfg.get("classification_llm_timeout_sec", 4.0))
            except (TypeError, ValueError):
                to = 4.0
            rewritten = await optional_rewrite_routing_utterance(
                user_input=user_input or "",
                # Memory SSOT: Gateway rewrite must not consume a separate
                # short-memory summary; memory enters via MemoryRecallAgent.
                short_memory="",
                prior_messages=prior_messages,
                engine=engine,
                timeout_sec=to,
            )
            if rewritten:
                bundle.routing_utterance = rewritten
                bundle.extra["classification_llm_routing_rewrite"] = True
