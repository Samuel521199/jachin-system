"""
§5 L2 语义路由 — 本地 Embedding Top-K（LiteLLM + DashScope/OpenAI），含 §12.4 稀疏边际 OOD。
失败时静默降级（无 Key / 网络），不阻断主路径。
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

from l3_node.intent_gateway.semantic_router import SKILL_PROTOTYPE_TEXTS

_PROTO_VEC_CACHE: dict[str, list[float]] = {}
_PROTO_CACHE_ORDER: list[str] = []
_PROTO_CACHE_MAX = 128


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _lru_proto_cache(key: str, vec: list[float]) -> None:
    global _PROTO_VEC_CACHE, _PROTO_CACHE_ORDER
    if key in _PROTO_VEC_CACHE:
        try:
            _PROTO_CACHE_ORDER.remove(key)
        except ValueError:
            pass
    elif len(_PROTO_CACHE_ORDER) >= _PROTO_CACHE_MAX:
        old = _PROTO_CACHE_ORDER.pop(0)
        _PROTO_VEC_CACHE.pop(old, None)
    _PROTO_VEC_CACHE[key] = vec
    _PROTO_CACHE_ORDER.append(key)


def _get_embedding_model() -> str:
    from l3_node.intent_gateway.config import get_intent_gateway_config

    m = str(get_intent_gateway_config().get("embedding_model") or "").strip()
    if m:
        return m
    return (os.environ.get("INTENT_GATEWAY_EMBEDDING_MODEL") or "dashscope/text-embedding-v1").strip()


async def _embed_one(text: str, shared_ctx: Any | None) -> Optional[list[float]]:
    t = (text or "").strip()
    if len(t) > 2000:
        t = t[:2000]
    if not t:
        return None
    try:
        import litellm
    except ImportError:
        return None

    from l3_node.llm_client import SecurityContext, _inject_env_keys_into_ctx, try_fetch_keys_from_l2

    ctx = shared_ctx if shared_ctx is not None else SecurityContext()
    if shared_ctx is None:
        _inject_env_keys_into_ctx(ctx)
        if not ctx.has_any_key():
            await try_fetch_keys_from_l2(ctx)
    if not ctx.has_any_key():
        return None

    model = _get_embedding_model()
    ml = model.lower()
    if "dashscope" in ml or ml.startswith("qwen"):
        ctx.inject_for_litellm("dashscope")
    else:
        ctx.inject_for_litellm("openai")

    try:
        resp = await litellm.aembedding(model=model, input=[t])
        data = getattr(resp, "data", None) or (resp.get("data") if isinstance(resp, dict) else None)
        if not data:
            return None
        row0 = data[0]
        emb = getattr(row0, "embedding", None) or (row0.get("embedding") if isinstance(row0, dict) else None)
        if isinstance(emb, list) and emb:
            return [float(x) for x in emb]
    except Exception as e:
        logger.info("[IntentGateway][Embed] aembedding 失败 model=%s: %s", model, str(e)[:200])
    return None


async def compute_embedding_route_hint(
    text: str,
    *,
    shared_ctx: Any | None = None,
) -> Optional[dict[str, Any]]:
    from l3_node.intent_gateway.config import get_intent_gateway_config

    cfg = get_intent_gateway_config()
    if not bool(cfg.get("embedding_router_enabled", False)):
        return None
    qvec = await _embed_one(text, shared_ctx)
    if not qvec:
        return None

    top_k = int(cfg.get("embedding_top_k", 5))
    min_top1 = float(cfg.get("embedding_min_top1_similarity", 0.22))
    margin_min = float(cfg.get("embedding_sparse_margin_min", 0.035))

    scored: list[tuple[str, float]] = []
    for skill_id, proto in SKILL_PROTOTYPE_TEXTS:
        h = hashlib.sha256(f"{skill_id}\n{proto}".encode("utf-8", errors="replace")).hexdigest()
        pvec = _PROTO_VEC_CACHE.get(h)
        if pvec is None:
            pvec = await _embed_one(proto, shared_ctx)
            if pvec:
                _lru_proto_cache(h, pvec)
        if not pvec or len(pvec) != len(qvec):
            continue
        scored.append((skill_id, _cosine(qvec, pvec)))

    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[: max(1, top_k)]
    if not top:
        return {
            "kind": "embedding_topk",
            "top_hits": [],
            "top1": 0.0,
            "margin": 0.0,
            "ood_sparse": True,
        }
    top1 = top[0][1]
    top2 = top[1][1] if len(top) > 1 else 0.0
    margin = top1 - top2
    ood_sparse = (margin < margin_min) or (top1 < min_top1)
    return {
        "kind": "embedding_topk",
        "top_hits": [{"skill_id": sid, "score": round(s, 5)} for sid, s in top],
        "top1": round(top1, 5),
        "margin": round(margin, 5),
        "ood_sparse": ood_sparse,
    }
