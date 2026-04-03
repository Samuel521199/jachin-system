"""
战役二：复合意图拆分。
- 启发式：确定性分句 + 线性边 sub_1 depends_on sub_0。
- 可选 LLM：dependency_analysis + sub_intents + 参数绑定（dag_split_llm）；校验失败可回落启发式。
"""
from __future__ import annotations

import re
from typing import Any, List, Optional

from l3_node.intent_gateway.envelope import SubIntentNode


def split_intents_enabled() -> bool:
    try:
        from l3_node.intent_gateway.config import get_intent_gateway_config

        return bool(get_intent_gateway_config().get("dag_splitting_enabled", False))
    except Exception:
        return False


def dag_splitting_llm_enabled() -> bool:
    try:
        from l3_node.intent_gateway.config import get_intent_gateway_config

        return bool(get_intent_gateway_config().get("dag_splitting_llm_enabled", False))
    except Exception:
        return False


# 句号/问号后接衔接词，或空白接衔接词（仅切一刀，避免过度拆分）
_SPLIT_AFTER_PUNCT = re.compile(
    r"(?<=[。！？!?])\s*(?:然后|接着|接下来|还有|另外|同时|并且|再|顺便|以及)\s*",
    re.UNICODE,
)
_SPLIT_INLINE = re.compile(
    r"\s+(?:然后|接着|接下来|还有|另外|同时|并且|再|顺便|以及)\s+",
    re.UNICODE,
)


def propose_subintents_heuristic(user_text: str) -> List[SubIntentNode]:
    t = (user_text or "").strip()
    if len(t) < 10:
        return []

    parts = _SPLIT_AFTER_PUNCT.split(t, maxsplit=1)
    if len(parts) < 2:
        parts = _SPLIT_INLINE.split(t, maxsplit=1)
    if len(parts) < 2:
        return []

    a, b = parts[0].strip(), parts[1].strip()
    if len(a) < 2 or len(b) < 2:
        return []

    return [
        SubIntentNode(
            id="sub_0",
            text_span=a,
            rewritten_text=a,
            what="segment_0",
            locality="unspecified",
        ),
        SubIntentNode(
            id="sub_1",
            text_span=b,
            rewritten_text=b,
            what="segment_1",
            locality="unspecified",
            depends_on=["sub_0"],
        ),
    ]


def propose_subintents_from_user_text(user_text: str) -> List[SubIntentNode]:
    """同步路径：仅启发式（单测与无 engine 场景）。"""
    if not split_intents_enabled():
        return []
    return propose_subintents_heuristic(user_text)


async def propose_subintents_async(user_text: str, engine: Optional[Any] = None) -> List[SubIntentNode]:
    """
    主路径：dag_splitting_llm_enabled 且提供 engine 时优先 LLM；否则或失败时按配置回落启发式。
    """
    if not split_intents_enabled():
        return []
    try:
        from l3_node.intent_gateway.config import get_intent_gateway_config

        cfg = get_intent_gateway_config()
        fallback = bool(cfg.get("dag_splitting_fallback_heuristic", True))
    except Exception:
        fallback = True

    if dag_splitting_llm_enabled() and engine is not None:
        try:
            from l3_node.intent_gateway.dag_split_llm import propose_subintents_via_llm_async

            _da, nodes = await propose_subintents_via_llm_async(user_text=user_text, engine=engine)
            if len(nodes) >= 2:
                return nodes
        except Exception:
            pass
        if not fallback:
            return []

    return propose_subintents_heuristic(user_text)


async def propose_subintents_with_analysis_async(
    user_text: str, engine: Optional[Any] = None
) -> tuple[List[SubIntentNode], Optional[list[Any]]]:
    """
    与 propose_subintents_async 相同节点结果，额外返回 dependency_analysis（仅 LLM 成功时非 None）。
    """
    if not split_intents_enabled():
        return [], None
    try:
        from l3_node.intent_gateway.config import get_intent_gateway_config

        cfg = get_intent_gateway_config()
        fallback = bool(cfg.get("dag_splitting_fallback_heuristic", True))
    except Exception:
        fallback = True

    if dag_splitting_llm_enabled() and engine is not None:
        try:
            from l3_node.intent_gateway.dag_split_llm import propose_subintents_via_llm_async

            da, nodes = await propose_subintents_via_llm_async(user_text=user_text, engine=engine)
            if len(nodes) >= 2:
                return nodes, da
        except Exception:
            pass
        if not fallback:
            return [], None

    return propose_subintents_heuristic(user_text), None
