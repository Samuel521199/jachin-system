"""意图网关专用模型名解析（DashScope / LiteLLM）。"""
from __future__ import annotations

import os

from l3_node.intent_gateway.config import get_intent_gateway_config


def _to_litellm_id(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        return raw
    try:
        from core.llm_provider import _normalize_model_for_litellm

        return _normalize_model_for_litellm(raw)
    except ImportError:
        ml = raw.lower()
        if ml.startswith("qwen") and not ml.startswith("qwen/") and not ml.startswith("dashscope/"):
            return f"dashscope/{raw}"
        return raw


def get_classification_model_litellm_id() -> str:
    """
    网关文本侧小模型（默认 qwen-turbo）。
    优先级：环境变量 INTENT_GATEWAY_CLASSIFICATION_MODEL → nexus intent_gateway.classification_model → 默认。
    """
    env_m = (os.environ.get("INTENT_GATEWAY_CLASSIFICATION_MODEL") or "").strip()
    if env_m:
        return _to_litellm_id(env_m)
    cfg = get_intent_gateway_config()
    m = str(cfg.get("classification_model") or "qwen-turbo").strip() or "qwen-turbo"
    return _to_litellm_id(m)


def get_multimodal_model_litellm_id() -> str:
    """
    网关多模态模型（默认 qwen-vl-max）。
    优先级：环境变量 INTENT_GATEWAY_MULTIMODAL_MODEL → nexus intent_gateway.multimodal_model → 默认。
    """
    env_m = (os.environ.get("INTENT_GATEWAY_MULTIMODAL_MODEL") or "").strip()
    if env_m:
        return _to_litellm_id(env_m)
    cfg = get_intent_gateway_config()
    m = str(cfg.get("multimodal_model") or "qwen-vl-max").strip() or "qwen-vl-max"
    return _to_litellm_id(m)
