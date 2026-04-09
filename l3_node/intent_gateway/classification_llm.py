"""
§6.1 小模型 JSON 路由扩写（可选）：在严格超时内产出 routing_utterance 候选，失败则保持原文。
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


async def optional_rewrite_routing_utterance(
    *,
    user_input: str,
    short_memory: str,
    prior_messages: list[dict[str, Any]],
    engine: Any,
    timeout_sec: float,
) -> Optional[str]:
    from l3_node.intent_gateway.config import get_intent_gateway_config
    from l3_node.intent_gateway.model_resolve import get_classification_model_litellm_id

    cfg = get_intent_gateway_config()
    if not bool(cfg.get("classification_llm_rewrite_enabled", False)):
        return None
    ui = (user_input or "").strip()
    if len(ui) > 1200:
        return None

    last_a = ""
    for m in reversed(prior_messages or []):
        if (m.get("role") or "").strip().lower() == "assistant":
            c = m.get("content")
            last_a = (c if isinstance(c, str) else str(c or "")).strip()[:400]
            break

    mem = (short_memory or "").strip()[:600]
    sys_p = (
        "你是意图路由预处理。只输出一个 JSON 对象，不要其它文字。"
        '键：routing_utterance（字符串，将用户当前句扩写为可独立分类的一句中文意图，可含必要上下文）、'
        "confidence（0~1 小数）。信息不足时 routing_utterance 尽量接近原句，confidence 放低。"
    )
    user_block = f"【短记忆】\n{mem}\n\n【最近助理片段】\n{last_a}\n\n【当前用户句】\n{ui}"
    messages = [
        {"role": "system", "content": sys_p},
        {"role": "user", "content": user_block},
    ]
    model = get_classification_model_litellm_id()
    max_tok = int(cfg.get("classification_llm_max_tokens", 256))

    async def _call() -> str:
        raw = await engine.generate_response(
            messages,
            tools=None,
            temperature=0.1,
            max_tokens=max_tok,
            l3_call_purpose="intent_gateway_routing_rewrite",
            l3_override_model=model,
        )
        if isinstance(raw, dict):
            return (raw.get("content") or "") or ""
        return str(raw or "")

    try:
        text = await asyncio.wait_for(_call(), timeout=timeout_sec)
    except asyncio.TimeoutError:
        logger.info("[IntentGateway] classification_llm 超时 %.1fs", timeout_sec)
        return None
    except Exception as e:
        logger.info("[IntentGateway] classification_llm 失败: %s", str(e)[:200])
        return None

    data = _parse_json_loose(text)
    if not data:
        return None
    ru = str(data.get("routing_utterance") or "").strip()
    if len(ru) < 2 or len(ru) > 800:
        return None
    return ru


async def infer_requires_realtime_knowledge_async(
    *,
    engine: Any,
    user_input: str,
    classification_text: str,
    timeout_sec: float = 2.5,
) -> bool:
    """
    小模型 JSON 判定：本轮用户意图是否需要「实时外部知识」预取（ Tavily 注入）。
    启发式优先命中则跳过小模型；小模型超时则回退启发式（避免始终 False）。
    """
    from l3_node.intent_gateway.config import get_intent_gateway_config
    from l3_node.intent_gateway.model_resolve import get_classification_model_litellm_id
    from l3_node.intent_gateway.realtime_knowledge_heuristic import heuristic_requires_realtime_knowledge

    cfg = get_intent_gateway_config()
    if not bool(cfg.get("realtime_knowledge_llm_enabled", True)):
        return False

    ui = (user_input or "").strip()
    ct = (classification_text or "").strip()
    surf = ct if len(ct) >= 8 else ui
    if len(surf) < 4:
        return False

    if heuristic_requires_realtime_knowledge(ui, ct):
        logger.info("[IntentGateway] realtime_knowledge 命中启发式，跳过小模型")
        return True

    try:
        from l3_node.intent_gateway.ood_signals import user_input_looks_like_mixed_poison

        if user_input_looks_like_mixed_poison(ui):
            return False
    except Exception:
        pass

    try:
        to = float(cfg.get("realtime_knowledge_llm_timeout_sec", timeout_sec))
    except (TypeError, ValueError):
        to = float(timeout_sec)
    to = max(0.5, min(to, 8.0))

    try:
        max_tok = int(cfg.get("realtime_knowledge_llm_max_tokens", 96))
    except (TypeError, ValueError):
        max_tok = 96
    max_tok = max(32, min(max_tok, 256))

    sys_p = (
        "你是意图分类器。只输出一个 JSON 对象，不要其它文字。"
        '键 requires_realtime_knowledge：布尔值。含义：当用户问题依赖「当前互联网上较新或较细」的外部事实时为 true，'
        "例如：最新时事/政策/发布、股票或行情、某产品/API 最新文档版本、天气实况、赛程比分、具体外部实体近况等。"
        "若主要是闲聊、编程通用知识、本地文件/仓库操作、或无需联网即可回答，则为 false。"
    )
    user_block = f"【分类面/用户句】\n{surf[:3500]}"
    messages = [
        {"role": "system", "content": sys_p},
        {"role": "user", "content": user_block},
    ]
    model = get_classification_model_litellm_id()

    async def _call() -> str:
        raw = await engine.generate_response(
            messages,
            tools=None,
            temperature=0.0,
            max_tokens=max_tok,
            l3_call_purpose="intent_gateway_realtime_knowledge",
            l3_override_model=model,
        )
        if isinstance(raw, dict):
            return (raw.get("content") or "") or ""
        return str(raw or "")

    try:
        text = await asyncio.wait_for(_call(), timeout=to)
    except asyncio.TimeoutError:
        _fb = heuristic_requires_realtime_knowledge(ui, ct)
        logger.info(
            "[IntentGateway] realtime_knowledge 分类超时 %.1fs，回退启发式=%s",
            to,
            _fb,
        )
        return _fb
    except Exception as e:
        logger.info("[IntentGateway] realtime_knowledge 分类失败: %s", str(e)[:200])
        return heuristic_requires_realtime_knowledge(ui, ct)

    data = _parse_json_loose(text)
    if not data:
        return heuristic_requires_realtime_knowledge(ui, ct)
    v = data.get("requires_realtime_knowledge")
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "是")
    return False
