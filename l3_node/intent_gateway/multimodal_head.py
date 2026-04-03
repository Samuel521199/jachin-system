"""
§12.1 附件侧路：仅用 Feature Slots（无原始文件名进主串）调用多模态模型，产出轻量路由桶。
默认关闭（成本高）；失败不阻断。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def _parse_json_loose(raw: str) -> Optional[dict[str, Any]]:
    s = (raw or "").strip()
    if not s:
        return None
    m = _JSON_FENCE.search(s)
    if m:
        s = m.group(1).strip()
    try:
        o = json.loads(s)
        return o if isinstance(o, dict) else None
    except json.JSONDecodeError:
        return None


async def optional_multimodal_route_head(
    *,
    user_text: str,
    feature_slots: list[dict[str, Any]],
    engine: Any,
    timeout_sec: float,
) -> Optional[dict[str, Any]]:
    from l3_node.intent_gateway.config import get_intent_gateway_config
    from l3_node.intent_gateway.model_resolve import get_multimodal_model_litellm_id

    cfg = get_intent_gateway_config()
    if not bool(cfg.get("multimodal_routing_head_enabled", False)):
        return None
    if not feature_slots:
        return None

    sys_p = (
        "你只根据结构化附件槽位与用户短句做路由分类，输出 JSON 即可。"
        "键：intent_bucket 取值 chat|task|vision_analyze|unknown；confidence 0~1。"
        "不要复述文件名原文。"
    )
    payload = {
        "user_text": (user_text or "")[:500],
        "attachment_feature_slots": feature_slots[:12],
    }
    user_c = json.dumps(payload, ensure_ascii=False)
    messages = [
        {"role": "system", "content": sys_p},
        {"role": "user", "content": user_c},
    ]
    model = get_multimodal_model_litellm_id()
    max_tok = int(cfg.get("multimodal_routing_head_max_tokens", 128))

    async def _call() -> str:
        raw = await engine.generate_response(
            messages,
            tools=None,
            temperature=0.0,
            max_tokens=max_tok,
            l3_call_purpose="intent_gateway_mm_route_head",
            l3_override_model=model,
        )
        if isinstance(raw, dict):
            return (raw.get("content") or "") or ""
        return str(raw or "")

    try:
        text = await asyncio.wait_for(_call(), timeout=timeout_sec)
    except Exception as e:
        logger.debug("[IntentGateway] multimodal_head 跳过: %s", e)
        return None
    data = _parse_json_loose(text)
    if not data:
        return None
    return {
        "intent_bucket": str(data.get("intent_bucket") or "unknown"),
        "confidence": float(data.get("confidence") or 0.0),
    }
