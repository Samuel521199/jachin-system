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
    from l3_node.intent_gateway.realtime_knowledge_heuristic import (
        heuristic_requires_realtime_knowledge,
        user_input_should_skip_realtime_prefetch_for_vision,
    )

    cfg = get_intent_gateway_config()
    if not bool(cfg.get("realtime_knowledge_llm_enabled", True)):
        return False

    ui = (user_input or "").strip()
    ct = (classification_text or "").strip()
    if user_input_should_skip_realtime_prefetch_for_vision(ui):
        logger.info("[IntentGateway] realtime_knowledge 跳过：本轮为本地看图/OCR 意图")
        return False
    if len(ui) < 4 and len(ct) < 4:
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
        "若主要是闲聊、编程通用知识、本地文件/仓库操作、识别用户上传图片中的文字/描述截图、或无需联网即可回答，则为 false。"
        "勿因「会话摘要」里出现过旧新闻链接就判为 true，须看【本轮用户句】是否真的在问时效外部事实。"
    )
    user_block = (
        f"【本轮用户句】\n{ui[:3500]}\n\n"
        f"【会话分类面摘要（仅弱参考；旧任务里的「新闻」等勿单独作为联网依据）】\n{ct[:2000]}"
    )
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


def _normalize_domain_experts(raw: Any, *, max_n: int = 3) -> list[str]:
    out: list[str] = []
    if raw is None:
        return out
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return out
    for x in raw:
        s = str(x).strip()
        if not s or len(s) > 48:
            continue
        if s not in out:
            out.append(s)
        if len(out) >= max_n:
            break
    return out


async def infer_domain_experts_async(
    *,
    engine: Any,
    user_input: str,
    classification_text: str,
    timeout_sec: float = 3.0,
) -> list[str]:
    """
    小模型 JSON：推断 1–3 个最适合处理当前任务的资深专家身份；简单闲聊返回空列表。
    失败或超时返回 []（不阻断主 ReAct）。
    """
    from l3_node.intent_gateway.config import get_intent_gateway_config
    from l3_node.intent_gateway.model_resolve import get_classification_model_litellm_id

    cfg = get_intent_gateway_config()
    if not bool(cfg.get("domain_experts_llm_enabled", True)):
        return []

    ui = (user_input or "").strip()
    ct = (classification_text or "").strip()
    surf = ct if len(ct) >= 8 else ui
    if len(surf) < 4:
        return []

    try:
        to = float(cfg.get("domain_experts_llm_timeout_sec", timeout_sec))
    except (TypeError, ValueError):
        to = float(timeout_sec)
    to = max(0.5, min(to, 10.0))

    try:
        max_tok = int(cfg.get("domain_experts_llm_max_tokens", 220))
    except (TypeError, ValueError):
        max_tok = 220
    max_tok = max(64, min(max_tok, 512))

    sys_p = (
        "你是任务分析器。只输出一个 JSON 对象，不要其它文字。"
        '键 domain_experts：字符串数组，长度 0～3。根据用户任务推断最适合处理该任务的资深专家身份标签'
        "（如「资深系统架构师」「高级产品经理」「应用安全专家」），用于主对话模型扮演多视角智囊。"
        "若仅为寒暄、极短附和、无实质任务需求，则 domain_experts 必须为 []。"
        "不要输出解释性前言；标签宜简短（每个建议不超过 16 字）。"
    )
    user_block = f"【分类面/用户句】\n{surf[:3500]}"
    messages = [
        {"role": "system", "content": sys_p},
        {"role": "user", "content": user_block},
    ]
    model = get_classification_model_litellm_id()
    # 与主 ReAct 隔离：此处仅为「专家标签」补判，messages 全是纯文本；不消费 OpenAI 多模态块，
    # 也不修改 run_agent / WebSocket 传入的 attachments 与 _user_llm_content。
    logger.info(
        "[IntentGateway] domain_experts 补判使用 %s（纯文本两轮 JSON）；不影响主链路图片/文档结构",
        model,
    )

    async def _call() -> str:
        raw = await engine.generate_response(
            messages,
            tools=None,
            temperature=0.1,
            max_tokens=max_tok,
            l3_call_purpose="intent_gateway_domain_experts",
            l3_override_model=model,
        )
        if isinstance(raw, dict):
            return (raw.get("content") or "") or ""
        return str(raw or "")

    try:
        text = await asyncio.wait_for(_call(), timeout=to)
    except asyncio.TimeoutError:
        logger.info("[IntentGateway] domain_experts 分类超时 %.1fs，回退 []", to)
        return []
    except Exception as e:
        logger.info("[IntentGateway] domain_experts 分类失败: %s", str(e)[:200])
        return []

    data = _parse_json_loose(text)
    if not data:
        return []
    return _normalize_domain_experts(data.get("domain_experts"))
